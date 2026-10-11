param([switch]$Real, [string]$Python)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if ($Python) {
        $pythonCommand = $Python
        $pythonArgs = @()
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $pythonCommand = 'py'
        $pythonArgs = @('-3.9')
    } else {
        $pythonCommand = 'python'
        $pythonArgs = @()
    }
    # Avoid nested quotes: Windows PowerShell 5.1 strips them for native commands.
    & $pythonCommand @pythonArgs -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,9) and sys.maxsize > 2**32 else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.9 x64 gerekli. Gerekirse -Python ile python.exe yolunu belirtin.' }
    & $pythonCommand @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.9 ortami olusturulamadi.' }
}
& '.\.venv\Scripts\python.exe' -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,9) and sys.maxsize > 2**32 else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Mevcut .venv Python 3.9 x64 degil. Eski ortami baska bir adla saklayip yeniden kurun.' }
& '.\.venv\Scripts\python.exe' -m pip install --require-hashes -r requirements-build.lock
if ($LASTEXITCODE -ne 0) { throw 'Kurulum araclari yuklenemedi.' }
$requirements = if ($Real) { 'requirements-real.lock' } else { 'requirements-demo.lock' }
& '.\.venv\Scripts\python.exe' -m pip install --require-hashes -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Bağımlılık kurulumu başarısız.' }
& '.\.venv\Scripts\python.exe' -m pip install --no-build-isolation --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Proje kurulumu başarısız.' }
Write-Host 'Kurulum tamamlandı. Banka bağlantısı yapılmadı.'
