param(
    [string]$ContainerName = 'llm-server',
    [string]$BaseUrl = 'http://127.0.0.1:8000',
    [switch]$Kubernetes,
    [string]$ClusterProfile = 'llm-lab',
    [string]$Namespace = 'llm-lab'
)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$checks = @()
$sensitiveValues = New-Object 'System.Collections.Generic.List[string]'
$temporaryFiles = New-Object 'System.Collections.Generic.List[string]'

try {
    $testCases = @('normal', 'stream', 'invalid')
    $authArguments = @()
    if ($Kubernetes) {
        $encodedToken = & minikube --profile=$ClusterProfile kubectl -- get secret api-credentials `
            -n $Namespace -o 'jsonpath={.data.token}'
        if ($LASTEXITCODE -ne 0) { throw 'Could not read the API Secret.' }
        $apiToken = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(($encodedToken -join '')))
        if ([string]::IsNullOrWhiteSpace($apiToken)) { throw 'API token is empty.' }
        $sensitiveValues.Add($apiToken)
        # Keep the token out of curl command-line arguments.
        $authFile = [System.IO.Path]::GetTempFileName()
        $temporaryFiles.Add($authFile)
        [System.IO.File]::WriteAllText($authFile, "Authorization: Bearer $apiToken`n", $utf8)
        $authArguments = @('-H', "@$authFile")
        $testCases += @('unauthorized', 'wrong-token')
    }
    foreach ($testCase in $testCases) {
        $marker = 'PRIVACY_' + [guid]::NewGuid().ToString('N')
        $sensitiveValues.Add($marker)
        $payload = @{
            model = 'qwen'
            messages = @(@{ role = 'user'; content = "Reply with exactly this text: $marker" })
            max_tokens = 64
            temperature = 0
            stream = ($testCase -eq 'stream')
        }
        if ($testCase -eq 'invalid') { $payload.max_tokens = $marker }

        $requestFile = [System.IO.Path]::GetTempFileName()
        $responseFile = [System.IO.Path]::GetTempFileName()
        $headerFile = [System.IO.Path]::GetTempFileName()
        $temporaryFiles.Add($requestFile)
        $temporaryFiles.Add($responseFile)
        $temporaryFiles.Add($headerFile)
        [System.IO.File]::WriteAllText($requestFile, ($payload | ConvertTo-Json -Depth 6), $utf8)

        Write-Host "Sending $testCase request..."
        $requestAuth = $authArguments
        if ($testCase -eq 'unauthorized') { $requestAuth = @() }
        if ($testCase -eq 'wrong-token') {
            $wrongToken = 'INVALID_' + [guid]::NewGuid().ToString('N')
            $sensitiveValues.Add($wrongToken)
            $requestAuth = @('-H', "Authorization: Bearer $wrongToken")
        }
        $httpCode = & curl.exe --silent --show-error --noproxy '*' `
            --http1.1 --max-time 120 -H 'Content-Type: application/json' -H 'Expect:' @requestAuth `
            --data-binary "@$requestFile" -D $headerFile -o $responseFile `
            -w '%{http_code}' "$BaseUrl/v1/chat/completions"
        if ($LASTEXITCODE -ne 0) { throw "curl failed for $testCase; check the server before repeating." }
        $status = [int]($httpCode -join '')
        if ($testCase -eq 'invalid') {
            if ($status -notin @(400, 422)) { throw "Expected validation error, received HTTP $status." }
        } elseif ($testCase -in @('unauthorized', 'wrong-token')) {
            if ($status -ne 401) { throw "Expected HTTP 401 for $testCase, received $status." }
        } elseif ($status -ne 200) { throw "Expected HTTP 200 for $testCase, received $status." }

        $headers = [System.IO.File]::ReadAllText($headerFile)
        $idMatch = [regex]::Match($headers, '(?im)^x-request-id:\s*([a-f0-9]{32})\s*$')
        if (-not $idMatch.Success) { throw "Missing X-Request-ID for $testCase." }
        $responseText = [System.IO.File]::ReadAllText($responseFile)
        $generatedText = ''
        if ($testCase -eq 'normal') {
            $responseObject = $responseText | ConvertFrom-Json
            $generatedText = [string]$responseObject.choices[0].message.content
        } elseif ($testCase -eq 'stream') {
            $done = $false
            foreach ($line in ($responseText -split '\r?\n')) {
                if ($line -eq 'data: [DONE]') { $done = $true; continue }
                if ($line.StartsWith('data: ')) {
                    $chunk = $line.Substring(6) | ConvertFrom-Json
                    if ($chunk.choices.Count -gt 0) {
                        $fragment = [string]$chunk.choices[0].delta.content
                        $generatedText += $fragment
                        if ($fragment.Length -ge 12) { $sensitiveValues.Add($fragment) }
                    }
                }
            }
            if (-not $done) { throw 'Streaming response did not finish with [DONE].' }
        }
        if ($testCase -in @('normal', 'stream')) {
            if ([string]::IsNullOrWhiteSpace($generatedText)) { throw "Empty model output for $testCase." }
            $sensitiveValues.Add($generatedText)
        }
        $checks += [pscustomobject]@{
            test = $testCase; request_id = $idMatch.Groups[1].Value; status = $status
        }
    }

    # The middleware logs immediately after the last ASGI response message.
    Start-Sleep -Milliseconds 500
    $ErrorActionPreference = 'Continue'
    if ($Kubernetes) {
        $logLines = & minikube --profile=$ClusterProfile kubectl -- logs deployment/vllm `
            -c vllm -n $Namespace 2>&1
    } else {
        $logLines = & docker logs $ContainerName 2>&1
    }
    $logExitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($logExitCode -ne 0) { throw 'Could not read the container logs.' }
    $logs = ($logLines | ForEach-Object { $_.ToString() }) -join "`n"
    foreach ($value in $sensitiveValues) {
        $escapedValue = ConvertTo-Json -InputObject $value -Compress
        if ($logs.Contains($value) -or $logs.Contains($escapedValue)) {
            throw 'FAIL: request marker or generated output found in container logs. Do not deploy yet.'
        }
    }

    $records = @()
    foreach ($line in ($logs -split '\r?\n')) {
        $offset = $line.IndexOf('{"event":"http_request"')
        if ($offset -ge 0) { $records += $line.Substring($offset) | ConvertFrom-Json }
    }
    foreach ($check in $checks) {
        $matching = @($records | Where-Object { $_.request_id -eq $check.request_id })
        if ($matching.Count -ne 1 -or $matching[0].status -ne $check.status `
            -or $matching[0].method -ne 'POST' -or $matching[0].path -ne '/v1/chat/completions' `
            -or -not $matching[0].completed -or $matching[0].failed) {
            throw "FAIL: missing or incorrect metadata for $($check.test)."
        }
        Write-Host "PASS: $($check.test), HTTP $($check.status), metadata present, no content leakage."
    }
    Write-Host 'PASS: normal, streaming, and validation-error checks completed.'
    if ($Kubernetes) { Write-Host 'PASS: missing and incorrect API tokens are rejected (HTTP 401).' }
    Write-Host 'This checks the tested request paths; it does not prove safety for every possible failure.'
} finally {
    foreach ($temporaryFile in $temporaryFiles) {
        Remove-Item -LiteralPath $temporaryFile -Force -ErrorAction SilentlyContinue
    }
}
