param(
    [string]$ModelPath = 'D:\Models\Qwen2.5-1.5B-Instruct'
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath (Join-Path $ModelPath 'config.json'))) {
    throw "Model config.json not found in $ModelPath"
}
docker run --rm --name llm-server `
    --cpus=4 `
    --memory=10g `
    --shm-size=2g `
    -p 127.0.0.1:8000:8000 `
    --mount "type=bind,source=$ModelPath,target=/models/qwen,readonly" `
    llm-lab:v0.2
if ($LASTEXITCODE -ne 0) { throw 'The server container exited with an error.' }
