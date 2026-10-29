param([string]$ClusterProfile = 'llm-lab')
$ErrorActionPreference = 'Stop'

function Apply-Resource($Resource) {
    $Resource | ConvertTo-Json -Depth 12 -Compress | `
        & minikube --profile=$ClusterProfile kubectl -- apply -f -
    if ($LASTEXITCODE -ne 0) { throw 'Could not apply Kubernetes resource.' }
}

function Resource-Exists([string]$Kind, [string]$Name) {
    $result = & minikube --profile=$ClusterProfile kubectl -- `
        get $Kind $Name -n llm-lab --ignore-not-found -o name
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Kubernetes resources.' }
    return -not [string]::IsNullOrWhiteSpace(($result -join ''))
}

Apply-Resource @{ apiVersion = 'v1'; kind = 'Namespace'; metadata = @{ name = 'llm-lab' } }

if (-not (Resource-Exists 'configmap' 'model-download-config')) {
    $modelId = 'Qwen/Qwen2.5-1.5B-Instruct'
    $modelInfo = Invoke-RestMethod -Uri "https://huggingface.co/api/models/$modelId" -TimeoutSec 30
    $revision = [string]$modelInfo.sha
    if ($revision -notmatch '^[a-fA-F0-9]{40}$') { throw 'Hugging Face did not return a full revision SHA.' }
    Apply-Resource @{
        apiVersion = 'v1'; kind = 'ConfigMap'
        metadata = @{ name = 'model-download-config'; namespace = 'llm-lab' }
        data = @{ MODEL_ID = $modelId; MODEL_REVISION = $revision; MODEL_DIR = '/models/qwen' }
    }
    Write-Host "Pinned model revision: $revision"
} else {
    Write-Host 'Keeping the existing model revision.'
}

if (-not (Resource-Exists 'secret' 'hf-credentials')) {
    $secureToken = Read-Host 'HF read token (Enter for public model without a token)' -AsSecureString
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try {
        $hfToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
        $encodedToken = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($hfToken))
        Apply-Resource @{
            apiVersion = 'v1'; kind = 'Secret'; type = 'Opaque'
            metadata = @{ name = 'hf-credentials'; namespace = 'llm-lab' }
            data = @{ token = $encodedToken }
        }
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
        $hfToken = $null
        $encodedToken = $null
        $secureToken.Dispose()
    }
}

if (-not (Resource-Exists 'secret' 'api-credentials')) {
    $randomBytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($randomBytes) } finally { $generator.Dispose() }
    $apiToken = [Convert]::ToBase64String($randomBytes)
    Apply-Resource @{
        apiVersion = 'v1'; kind = 'Secret'; type = 'Opaque'
        metadata = @{ name = 'api-credentials'; namespace = 'llm-lab' }
        data = @{ token = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($apiToken)) }
    }
    $apiToken = $null
}
Write-Host 'Configuration and Secrets are ready. Tokens were not written to project files.'
