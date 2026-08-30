$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$sidecarRoot = Join-Path $repoRoot 'integrations\pi_agent_core'
$env:NPM_CONFIG_CACHE = Join-Path $repoRoot '.cache\npm'

New-Item -ItemType Directory -Force -Path $env:NPM_CONFIG_CACHE | Out-Null
npm ci --ignore-scripts --prefix $sidecarRoot
if ($LASTEXITCODE -ne 0) {
    throw "Pi sidecar dependency installation failed with exit code $LASTEXITCODE"
}

Write-Host "Pi sidecar installed under: $sidecarRoot"
Write-Host "npm cache: $env:NPM_CONFIG_CACHE"
