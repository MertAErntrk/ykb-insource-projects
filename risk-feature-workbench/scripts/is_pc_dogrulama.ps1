# Is bilgisayari dogrulama betigi (Windows PowerShell 5.1).
#
# Kullanim (proje kokunden):
#   powershell -ExecutionPolicy Bypass -File scripts\is_pc_dogrulama.ps1 -Config config\real.json
#   powershell -ExecutionPolicy Bypass -File scripts\is_pc_dogrulama.ps1 -Config config\real.json `
#       -Excel C:\yol\etiketli.xlsx -LabelColumn "Beklenen kolon" -Rows 100 -GroupSize 25
#
# Adimlar: 1) probe (embedding gecikmesi, parmak izi, verify)  2) -Excel verildiyse havuz/kapi taramasi + ucdan uca kosu.
# Cikti: runtime\dogrulama\<zaman damgasi>\ (rapor.md, rapor.json, havuz.json).
# Parola dosyaya yazilmaz. Oracle/embedding bilgileri config'teki *_env ortam degiskenlerinden okunur;
# tanimsiz olanlar icin yalniz bu surec icin guvenli istem gosterilir (diske yazilmaz).
param(
    [string]$Config = "config\real.json",
    [string]$Excel = "",
    [string]$LabelColumn = "Beklenen kolon",
    [int]$Rows = 0,
    [int]$GroupSize = 25,
    [string]$StateDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Stop-Hata([string]$mesaj) {
    Write-Host ""
    Write-Host ("HATA: " + $mesaj) -ForegroundColor Red
    exit 1
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Stop-Hata "Sanal ortam bulunamadi (.venv\Scripts\python.exe). Once scripts\setup.ps1 ile kurulum yapin." }
if (-not (Test-Path $Config)) { Stop-Hata "Yapilandirma dosyasi bulunamadi: $Config. config\real.example.json dosyasini kopyalayip doldurun." }
if ($Excel -ne "" -and -not (Test-Path $Excel)) { Stop-Hata "Etiketli Excel bulunamadi: $Excel" }

# Gerekli ortam degiskeni adlarini config'ten oku (degerleri degil).
try {
    $cfg = Get-Content -Raw -Encoding UTF8 $Config | ConvertFrom-Json
} catch {
    Stop-Hata "Yapilandirma JSON olarak okunamadi: $Config"
}
$gerekli = @()
if ($cfg.oracle) {
    foreach ($alan in @("user_env", "password_env", "dsn_env")) {
        $ad = $cfg.oracle.$alan
        if ($ad) { $gerekli += $ad }
    }
}
foreach ($ad in $gerekli) {
    if (-not [Environment]::GetEnvironmentVariable($ad)) {
        $gizli = ($ad -match "PASS|SECRET|KEY")
        if ($gizli) {
            $guvenli = Read-Host -Prompt ("$ad degeri (ekranda gorunmez, diske yazilmaz)") -AsSecureString
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($guvenli)
            try { $deger = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        } else {
            $deger = Read-Host -Prompt "$ad degeri (yalniz bu surec icin)"
        }
        if (-not $deger) { Stop-Hata "$ad tanimli degil. Uygulamanin kullandigi ortam degiskenini ayarlayin." }
        [Environment]::SetEnvironmentVariable($ad, $deger, "Process")
    }
}

if ($cfg.embedding -and $cfg.embedding.api_key_env -and -not [Environment]::GetEnvironmentVariable($cfg.embedding.api_key_env)) {
    Write-Host ("Not: " + $cfg.embedding.api_key_env + " tanimli degil; embedding servisi anahtar istiyorsa probe 401 ile duser.")
}

$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $root ("runtime\dogrulama\" + $stamp)
New-Item -ItemType Directory -Force -Path $out | Out-Null

$arguman = @("scripts\evaluate.py", "--config", $Config, "--out", $out)
if ($StateDir -ne "") { $arguman += @("--state-dir", $StateDir) }

if ($Excel -eq "") {
    Write-Host "1/1 Probe (embedding gecikmesi, parmak izi, verify)..." -ForegroundColor Cyan
    $arguman += @("--probe")
} else {
    Write-Host "1/2 Probe + 2/2 havuz/kapi taramasi ve uctan uca kosu (uzun surebilir)..." -ForegroundColor Cyan
    $arguman += @("--probe", "--excel", $Excel, "--label-column", $LabelColumn, "--group-size", $GroupSize, "--e2e", "--consistency")
    if ($Rows -gt 0) { $arguman += @("--rows", $Rows) }
}

& $python @arguman
$kod = $LASTEXITCODE
if ($kod -ne 0) {
    Stop-Hata ("Olcum araci hata kodu $kod ile bitti. Yukaridaki mesaji kontrol edin (ag/servis erisimi, ortam degiskenleri, yapilandirma).")
}

$rapor = Join-Path $out "rapor.md"
if (-not (Test-Path $rapor)) { Stop-Hata "Rapor olusmadi: $rapor" }
Write-Host ""
Write-Host "Tamamlandi. Rapor: $rapor" -ForegroundColor Green
