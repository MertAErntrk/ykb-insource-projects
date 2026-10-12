# Is bilgisayari dogrulama betigi (Windows PowerShell 5.1).
#
# Varsayilan mod web-esdeger: web uygulamasiyla AYNI baglanti ve model ayarlari (config dosyasi gerekmez).
#   Oracle: sabit kurum DSN'i (EDWLIVE, LIVE), container ve erisilebilir semalar baglanti testinden okunur.
#   Embedding/LLM: web'deki gibi EMBEDDING_BASE_URL / LLM_BASE_URL uzerinden kesfedilir; arama modu web varsayilani.
#
# Kullanim (proje kokunden):
#   powershell -ExecutionPolicy Bypass -File scripts\is_pc_dogrulama.ps1 -Excel C:\yol\etiketleme.xlsx   (DM_CAD, DM, EDW; once tam indeks)
#   ... -Excel C:\yol\etiketleme.xlsx -Rows 100 -GroupSize 25 -Llm
#   ... -Excel C:\yol\etiketleme.xlsx -NoEmbedding     (yalniz ad/metin aramasi)
#   ... -Schemas DM_CAD -Excel C:\yol\etiketleme.xlsx  (bilincli daraltma)
#   ... -Llm -LlmModel <kimlik> / -EmbeddingModel <kimlik>  (servis birden cok model sunuyorsa web'de secilen kimlik)
#   ... -Config config\real.json -Excel C:\yol\etiketli.xlsx             (eski yol: config dosyasi + *_env)
#
# -ResourcesDir (varsayilan runtime\web): web uygulamasinin katalog/vektor onbellegi yeniden kullanilir;
# dizin yoksa gecici onbellek kullanilir. Ayni onbellegi kullandigi icin web uygulamasi kapali olmali.
#
# Cikti: runtime\dogrulama\<zaman damgasi>\ (rapor.md, rapor.json, iskalar.md, satirlar.json,
# katalog_ozet.md, katalog_ozet.json, havuz.json). Hepsi yalniz metadata icerir.
# Kullanici adi, parola ve anahtarlar yalniz bu surecin ortamina konur; diske ve rapora yazilmaz.
param(
    [string]$Schemas = "",
    [string]$Excel = "",
    [string]$LabelColumn = "Beklenen kolon",
    [string]$ResourcesDir = "runtime\web",
    [switch]$BuildIndex,
    [int]$IndexTimeout = 3600,
    [switch]$Llm,
    [switch]$NoEmbedding,
    [string]$EmbeddingModel = "",
    [string]$LlmModel = "",
    [int]$Rows = 0,
    [int]$GroupSize = 25,
    [string]$StateDir = "",
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"
# Goreli -Excel / -Config cagrildigi klasore gore cozulur (proje kokune gecmeden once).
if ($Excel -ne "" -and (Test-Path $Excel)) { $Excel = (Resolve-Path $Excel).Path }
if ($Config -ne "" -and (Test-Path $Config)) { $Config = (Resolve-Path $Config).Path }
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Stop-Hata([string]$mesaj) {
    Write-Host ""
    Write-Host ("HATA: " + $mesaj) -ForegroundColor Red
    exit 1
}

function Read-Gizli([string]$istem) {
    $guvenli = Read-Host -Prompt $istem -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($guvenli)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Set-SurecDegiskeni([string]$ad, [string]$istem, [bool]$gizli, [bool]$bosOlabilir) {
    if ([Environment]::GetEnvironmentVariable($ad)) { return }
    if ($gizli) { $deger = Read-Gizli $istem } else { $deger = Read-Host -Prompt $istem }
    if (-not $deger -and -not $bosOlabilir) { Stop-Hata "$ad bos birakilamaz." }
    # Yalniz bu surec: kullanici/makine ortamina ve diske yazilmaz.
    [Environment]::SetEnvironmentVariable($ad, $deger, "Process")
}

Write-Host "Not: Once web uygulamasini kapatin (ayni katalog/vektor onbellegini kullanir)." -ForegroundColor Yellow

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Stop-Hata "Sanal ortam bulunamadi (.venv\Scripts\python.exe). Once scripts\setup.ps1 ile kurulum yapin." }
if ($Excel -ne "" -and -not (Test-Path $Excel)) { Stop-Hata "Etiketli Excel bulunamadi: $Excel" }
if ($Llm -and $NoEmbedding) { Stop-Hata "-Llm ve -NoEmbedding birlikte kullanilamaz (LLM yorumu embedding baglantisi ister)." }

$webModu = ($Config -eq "")
if ($webModu) {
    if ($Excel -eq "" -and $NoEmbedding) { Stop-Hata "-Excel verilmeden yalniz embedding probe calisir; -NoEmbedding ile anlamsiz." }
    Set-SurecDegiskeni "RISK_ORACLE_USER" "EDWLIVE kullanici adi (yalniz bu surec icin)" $false $false
    Set-SurecDegiskeni "RISK_ORACLE_PASSWORD" "EDWLIVE parolasi (ekranda gorunmez, diske yazilmaz)" $true $false
    if (-not $NoEmbedding) {
        Set-SurecDegiskeni "RISK_EMBEDDING_API_KEY" "Embedding API anahtari (anahtar gerekmiyorsa Enter)" $true $true
    }
    if ($Llm) {
        Set-SurecDegiskeni "RISK_LLM_API_KEY" "LLM API anahtari (anahtar gerekmiyorsa Enter)" $true $true
    }
} else {
    if (-not (Test-Path $Config)) { Stop-Hata "Yapilandirma dosyasi bulunamadi: $Config" }
    if ($Llm -or $NoEmbedding) { Stop-Hata "-Llm ve -NoEmbedding yalniz web-esdeger modda (-Config olmadan)." }
    try {
        $cfg = Get-Content -Raw -Encoding UTF8 $Config | ConvertFrom-Json
    } catch {
        Stop-Hata "Yapilandirma JSON olarak okunamadi: $Config"
    }
    # Gerekli ortam degiskeni adlarini config'ten oku (degerleri degil).
    if ($cfg.oracle) {
        foreach ($alan in @("user_env", "password_env", "dsn_env")) {
            $ad = $cfg.oracle.$alan
            if ($ad) { Set-SurecDegiskeni $ad "$ad degeri (yalniz bu surec icin, diske yazilmaz)" ($ad -match "PASS|SECRET|KEY") $false }
        }
    }
    if ($cfg.embedding -and $cfg.embedding.api_key_env -and -not [Environment]::GetEnvironmentVariable($cfg.embedding.api_key_env)) {
        Write-Host ("Not: " + $cfg.embedding.api_key_env + " tanimli degil; embedding servisi anahtar istiyorsa probe 401 ile duser.")
    }
}

$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $root ("runtime\dogrulama\" + $stamp)
New-Item -ItemType Directory -Force -Path $out | Out-Null

$arguman = @("scripts\evaluate.py", "--out", $out)
if ($webModu) {
    # Bos birakilirsa web ile ayni sabit semalar (DM_CAD, DM, EDW) taranir.
    if ($Schemas -ne "") { $arguman += @("--schemas", $Schemas) }
    if ($Llm) { $arguman += @("--llm") }
    if ($NoEmbedding) { $arguman += @("--no-embedding") }
    if ($EmbeddingModel -ne "") { $arguman += @("--embedding-model", $EmbeddingModel) }
    if ($LlmModel -ne "") { $arguman += @("--llm-model", $LlmModel) }
} else {
    $arguman += @("--config", $Config)
    if ($Schemas -ne "") { $arguman += @("--schemas", $Schemas) }
}
if ($StateDir -ne "") { $arguman += @("--state-dir", $StateDir) }
if ($ResourcesDir -ne "" -and (Test-Path $ResourcesDir)) {
    $arguman += @("--resources-dir", (Resolve-Path $ResourcesDir).Path)
} elseif ($ResourcesDir -ne "") {
    Write-Host "Not: $ResourcesDir bulunamadi; gecici onbellek kullanilacak (katalog yeniden taranir)."
}
if ($BuildIndex) { $arguman += @("--build-index", "--index-timeout", $IndexTimeout) }
if (-not $NoEmbedding) { $arguman += @("--probe") }

if ($Excel -eq "") {
    Write-Host "1/1 Probe (embedding gecikmesi, parmak izi, verify)..." -ForegroundColor Cyan
} else {
    Write-Host "Havuz/kapi taramasi ve uctan uca kosu (uzun surebilir)..." -ForegroundColor Cyan
    $arguman += @("--excel", $Excel, "--label-column", $LabelColumn, "--group-size", $GroupSize, "--e2e", "--consistency")
    if ($Rows -gt 0) { $arguman += @("--rows", $Rows) }
}

& $python @arguman
$kod = $LASTEXITCODE
if ($kod -ne 0) {
    Stop-Hata ("Olcum araci hata kodu $kod ile bitti. Yukaridaki mesaji kontrol edin (kullanici/parola, sema adi, ag/servis erisimi).")
}

$rapor = Join-Path $out "rapor.md"
if (-not (Test-Path $rapor)) { Stop-Hata "Rapor olusmadi: $rapor" }
Write-Host ""
Write-Host "Tamamlandi. Cikti klasoru: $out" -ForegroundColor Green
Write-Host "Paylasilacak dosyalar (yalniz metadata; parola/anahtar/adres icermez):"
foreach ($ad in @("rapor.md", "iskalar.md", "katalog_ozet.md")) {
    $yol = Join-Path $out $ad
    if (Test-Path $yol) { Write-Host ("  " + $yol) }
}
