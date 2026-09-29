# whisper_kesif.ps1 — Whisper STT chart kesfi: values + sablonlari tek dosyada toplar.
# Calistirma:  cd <helm-klasoru> ; .\whisper_kesif.ps1
# (Yasak derse bir kerelik:  Set-ExecutionPolicy -Scope Process Bypass)

$ErrorActionPreference = "Continue"
$chart   = "whisper-stt-large-v3-turbo-turkish-v1-model"
$surum   = "1.0.0+2"
$cikti   = "whisper_kesif_cikti.txt"

Set-Location $PSScriptRoot
if (-not (Test-Path .\helm.exe)) { Write-Host "helm.exe bu klasorde yok; scripti qwen-test klasorune koyup oradan calistir."; exit 1 }

"===== HELM REPO UPDATE =====" | Out-File $cikti -Encoding utf8
.\helm.exe repo update 2>&1 | Out-File $cikti -Append -Encoding utf8

"`n===== SEARCH =====" | Out-File $cikti -Append -Encoding utf8
.\helm.exe search repo $chart --versions 2>&1 | Out-File $cikti -Append -Encoding utf8

"`n===== DEFAULT VALUES =====" | Out-File $cikti -Append -Encoding utf8
.\helm.exe show values "internal-helm/$chart" --version $surum 2>&1 | Out-File $cikti -Append -Encoding utf8

if (Test-Path $chart) { Remove-Item $chart -Recurse -Force }
.\helm.exe pull "internal-helm/$chart" --version $surum --untar 2>&1 | Out-File $cikti -Append -Encoding utf8

"`n===== CHART.YAML =====" | Out-File $cikti -Append -Encoding utf8
Get-Content "$chart\Chart.yaml" -Raw -ErrorAction SilentlyContinue | Out-File $cikti -Append -Encoding utf8

"`n===== TEMPLATES (dir) =====" | Out-File $cikti -Append -Encoding utf8
Get-ChildItem "$chart\templates" -ErrorAction SilentlyContinue | Select-Object Name, Length | Format-Table -AutoSize | Out-String | Out-File $cikti -Append -Encoding utf8

Get-ChildItem "$chart\templates" -File -ErrorAction SilentlyContinue | ForEach-Object {
    "`n===== templates\$($_.Name) =====" | Out-File $cikti -Append -Encoding utf8
    Get-Content $_.FullName -Raw | Out-File $cikti -Append -Encoding utf8
}

"`n===== README (varsa) =====" | Out-File $cikti -Append -Encoding utf8
Get-Content "$chart\README.md" -Raw -ErrorAction SilentlyContinue | Out-File $cikti -Append -Encoding utf8

Write-Host "Bitti -> $((Get-Item $cikti).FullName)  ($([math]::Round((Get-Item $cikti).Length/1KB)) KB)"
notepad $cikti
