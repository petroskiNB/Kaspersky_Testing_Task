$ErrorActionPreference = 'Stop'
$cpuImage = 'vllm/vllm-openai-cpu:v0.27.0-x86_64@sha256:ad2d073b2ff1b755e0d0b9e33a581bfcbadde22c13a073af1dfff1d553119d38'
$projectRoot = Split-Path -Parent $PSScriptRoot

docker pull $cpuImage
if ($LASTEXITCODE -ne 0) { throw 'Could not pull the vLLM CPU image.' }

# Use package metadata instead of pip freeze: upstream images may install
# vLLM from a local wheel, which freeze would record as a file:// reference.
$pythonCode = "import importlib.metadata as m; print(chr(10).join(sorted({d.metadata['Name'] + '==' + d.version for d in m.distributions()})))"
$packages = docker run --rm --entrypoint python $cpuImage -c $pythonCode
if ($LASTEXITCODE -ne 0) { throw 'Could not export installed package versions.' }

$requirementsPath = Join-Path $projectRoot 'requirements.txt'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($requirementsPath, [string[]]$packages, $utf8)
Write-Host "Created $requirementsPath"
