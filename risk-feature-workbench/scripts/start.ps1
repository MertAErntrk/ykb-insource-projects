$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = '1'
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Önce scripts\setup.ps1 ile kurulumu tamamlayın.'
}
& '.\.venv\Scripts\python.exe' -m riskmatch serve
