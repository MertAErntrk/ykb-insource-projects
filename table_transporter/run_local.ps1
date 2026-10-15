# Yerel çalıştırma (Windows PowerShell 5.1). Kök dizinden: .\run_local.ps1 [-Port 8000] [-SkipBuild]
param(
    [int]$Port = 8000,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'

if (-not (Test-Path (Join-Path $backend '.env'))) {
    Write-Host "backend\.env yok. Önce kopyalayın ve gizli alanları doldurun:"
    Write-Host "  Copy-Item backend\.env.example backend\.env"
    exit 1
}

$python = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host "backend\.venv yok. Önce sanal ortamı kurun (docs\DEPLOY.md)."
    exit 1
}

if (-not $SkipBuild) {
    Push-Location $frontend
    try {
        if (-not (Test-Path 'node_modules')) {
            & pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { Write-Host "pnpm install başarısız ($LASTEXITCODE)"; exit $LASTEXITCODE }
        }
        & pnpm build
        if ($LASTEXITCODE -ne 0) { Write-Host "pnpm build başarısız ($LASTEXITCODE)"; exit $LASTEXITCODE }
    }
    finally {
        Pop-Location
    }
}

$env:TT_FRONTEND_DIST = Join-Path $frontend 'dist'
if (-not (Test-Path Env:TT_COOKIE_SECURE)) {
    $env:TT_COOKIE_SECURE = 'false'   # http://localhost: Secure çerez gönderilmez
}

Push-Location $backend
try {
    Write-Host "VeriKöprü: http://verikopru.localhost:$Port  (yedek: http://127.0.0.1:$Port)"
    & $python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $Port --workers 1
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($code -ne 0) { Write-Host "uvicorn çıkış kodu $code" }
exit $code
