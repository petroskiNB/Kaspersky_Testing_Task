param(
    [ValidateSet(1024, 2048)]
    [int]$MaxModelLen = 1024,
    [ValidateSet(1, 2)]
    [int]$MaxNumSeqs,
    [string]$ClusterProfile = 'llm-lab'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$configFile = Join-Path $projectRoot 'k8s\app-config.yaml'

function Get-ServerPod {
    $json = & minikube --profile=$ClusterProfile kubectl -- get pods -n llm-lab -l app=vllm -o json
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect server Pod.' }
    $pods = ($json -join "`n") | ConvertFrom-Json
    $active = @($pods.items | Where-Object { -not $_.metadata.deletionTimestamp })
    if ($active.Count -ne 1) { throw 'Expected exactly one active server Pod.' }
    return $active[0]
}

$before = Get-ServerPod
$beforeStatus = @($before.status.containerStatuses | Where-Object { $_.name -eq 'vllm' })
if ($beforeStatus.Count -ne 1 -or -not $beforeStatus[0].ready) { throw 'Server must be Ready before the test.' }
$beforeImage = $beforeStatus[0].imageID

$text = [IO.File]::ReadAllText($configFile)
$pattern = '(?m)^(    max-model-len: )\d+'
if ([regex]::Matches($text, $pattern).Count -ne 1) { throw 'Expected one max-model-len setting.' }
$text = [regex]::Replace($text, $pattern, [Text.RegularExpressions.MatchEvaluator]{
    param($match)
    return $match.Groups[1].Value + $MaxModelLen
})
if ($PSBoundParameters.ContainsKey('MaxNumSeqs')) {
    $sequencePattern = '(?m)^(    max-num-seqs: )\d+'
    if ([regex]::Matches($text, $sequencePattern).Count -ne 1) { throw 'Expected one max-num-seqs setting.' }
    $text = [regex]::Replace($text, $sequencePattern, [Text.RegularExpressions.MatchEvaluator]{
        param($match)
        return $match.Groups[1].Value + $MaxNumSeqs
    })
}
[IO.File]::WriteAllText($configFile, $text, (New-Object Text.UTF8Encoding($false)))

& minikube --profile=$ClusterProfile kubectl -- apply -f $configFile
if ($LASTEXITCODE -ne 0) { throw 'Could not apply ConfigMaps.' }
& minikube --profile=$ClusterProfile kubectl -- rollout restart deployment/vllm -n llm-lab
if ($LASTEXITCODE -ne 0) { throw 'Could not restart Deployment.' }
Write-Host 'Waiting for the replacement Pod to become Ready...'
& minikube --profile=$ClusterProfile kubectl -- rollout status deployment/vllm -n llm-lab --timeout=15m
if ($LASTEXITCODE -ne 0) { throw 'Rollout did not complete. Inspect Pod status and logs.' }

$after = Get-ServerPod
$afterStatus = @($after.status.containerStatuses | Where-Object { $_.name -eq 'vllm' })
if ($after.metadata.uid -eq $before.metadata.uid) { throw 'The Pod was not replaced.' }
if ($afterStatus[0].imageID -ne $beforeImage) { throw 'Image digest changed; the test is not isolated to ConfigMap.' }

# Check the running API inside the Pod, avoiding stale Windows port-forward.
# The API token stays inside the container environment and is not printed.
$pythonCode = "import json,os,urllib.request; req=urllib.request.Request('http://127.0.0.1:8000/v1/models',headers={'Authorization':'Bearer '+os.environ['VLLM_API_KEY']}); data=json.load(urllib.request.urlopen(req,timeout=15)); print(data['data'][0]['max_model_len'])"
$actual = & minikube --profile=$ClusterProfile kubectl -- exec $after.metadata.name `
    -n llm-lab -c vllm -- python -c $pythonCode
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the running model configuration.' }
if (($actual -join '').Trim() -ne [string]$MaxModelLen) { throw 'The running API reports a different context length.' }

if ($PSBoundParameters.ContainsKey('MaxNumSeqs')) {
    $sequenceCode = "import yaml; print(yaml.safe_load(open('/etc/llm/server.yaml'))['max-num-seqs'])"
    $actualSequences = & minikube --profile=$ClusterProfile kubectl -- exec $after.metadata.name `
        -n llm-lab -c vllm -- python -c $sequenceCode
    if ($LASTEXITCODE -ne 0 -or ($actualSequences -join '').Trim() -ne [string]$MaxNumSeqs) {
        throw 'The mounted server configuration has a different max-num-seqs value.'
    }
    Write-Host "PASS: mounted configuration has max-num-seqs=$MaxNumSeqs."
}

$initLogs = & minikube --profile=$ClusterProfile kubectl -- logs $after.metadata.name -n llm-lab -c download-model
if ($LASTEXITCODE -ne 0) { throw 'Could not read initializer logs.' }
if (($initLogs -join "`n") -notmatch '"event"\s*:\s*"model_cache_hit"') {
    throw 'Expected model_cache_hit on the replacement Pod.'
}
Write-Host "PASS: API reports max_model_len=$MaxModelLen."
Write-Host 'PASS: replacement Pod uses the same server image digest.'
Write-Host 'PASS: model reused from PVC (model_cache_hit).'
Write-Host 'The setting remains in app-config.yaml. Run with -MaxModelLen 2048 to restore the baseline.'
if ($PSBoundParameters.ContainsKey('MaxNumSeqs')) {
    Write-Host 'To restore single-sequence scheduling, also specify -MaxNumSeqs 1.'
}
Write-Host 'Restart port-forward after this test: its previous Pod was replaced.'
