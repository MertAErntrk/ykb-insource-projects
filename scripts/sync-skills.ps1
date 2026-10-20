<#
.SYNOPSIS
    Yerel Claude Code skill'lerini bu repodaki .claude/skills/ klasörüne kopyalar,
    commit'ler ve push'lar. Böylece bu repoyla açılan bulut oturumları da aynı
    skill'leri kullanır.

.DESCRIPTION
    Kaynaklar:
      - ~/.claude/skills/<skill>/SKILL.md            (kişisel skill'ler)
      - ~/.claude/plugins/installed_plugins.json      (plugin'lerin içindeki skill'ler)

    Kopyalarken node_modules, .git, .venv, __pycache__ gibi klasörleri ve .env
    dosyalarını atlar. Commit'ten önce dosyalarda API anahtarı / token arar;
    bulursa commit'lemez ve listeyi gösterir.

    Plugin ve marketplace listesi (dosya yolları olmadan) .claude/skills-sync/
    inventory.json dosyasına yazılır.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\sync-skills.ps1

.EXAMPLE
    # Sadece kopyala, commit/push yapma
    powershell -ExecutionPolicy Bypass -File .\scripts\sync-skills.ps1 -NoCommit
#>
[CmdletBinding()]
param(
    # Yerel Claude klasörü (varsayılan: C:\Users\<kullanıcı>\.claude)
    [string]$ClaudeHome = (Join-Path $HOME '.claude'),
    # Repo kök klasörü (varsayılan: bu script'in bir üst klasörü)
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    # Plugin'lerin içindeki skill'leri alma
    [switch]$SkipPluginSkills,
    # Kopyala ama commit/push yapma
    [switch]$NoCommit,
    # Commit'le ama push'lama
    [switch]$NoPush,
    # Gizli bilgi taramasında bulgu olsa da commit'le (bulguları kontrol ettiysen)
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$ExcludeDirs  = @('node_modules', '.git', '.venv', 'venv', '__pycache__', '.pytest_cache',
                  '.mypy_cache', '.ruff_cache', '.cache', '.idea', '.vscode')
$ExcludeFiles = @('.DS_Store', 'Thumbs.db', 'desktop.ini')
$ExcludeGlobs = @('*.pyc', '.env', '.env.*', '*.pem', '*.key', '*.p12', '*.pfx', 'credentials*.json')
$MaxDepth     = 25
$MaxFileBytes = 50MB

$SecretPatterns = [ordered]@{
    'Anthropic API key'   = 'sk-ant-[A-Za-z0-9_\-]{20,}'
    'OpenAI API key'      = 'sk-(proj-)?[A-Za-z0-9_\-]{32,}'
    'GitHub token'        = '(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}'
    'AWS access key'      = 'AKIA[0-9A-Z]{16}'
    'Google API key'      = 'AIza[0-9A-Za-z_\-]{35}'
    'Slack token'         = 'xox[abprs]-[A-Za-z0-9\-]{10,}'
    'Private key'         = '-----BEGIN [A-Z ]*PRIVATE KEY-----'
    'Hardcoded secret'    = '(?i)(api[_-]?key|secret|token|password|passwd)["'']?\s*[:=]\s*["''][A-Za-z0-9_\-\.\/\+=]{20,}["'']'
}
$TextExtensions = @('.md', '.txt', '.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.json', '.yaml',
                    '.yml', '.toml', '.sh', '.ps1', '.bat', '.cmd', '.html', '.css', '.xml', '.ini',
                    '.cfg', '.conf', '.env', '.rb', '.go', '.rs', '.java', '.cs', '.sql', '')

function Write-Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Warn([string]$Text) { Write-Host "  ! $Text" -ForegroundColor Yellow }
function Write-Ok([string]$Text)   { Write-Host "  + $Text" -ForegroundColor Green }

function Test-Excluded([System.IO.FileSystemInfo]$Item) {
    if ($Item.PSIsContainer) { return $ExcludeDirs -contains $Item.Name }
    if ($ExcludeFiles -contains $Item.Name) { return $true }
    foreach ($glob in $ExcludeGlobs) {
        if ($Item.Name -like $glob -and $Item.Name -ne '.env.example') { return $true }
    }
    return $false
}

# Klasörü özyinelemeli kopyalar. Sembolik bağlantılı skill klasörlerinin
# (ör. npx skills ile kurulanlar) içeriğini de kopyalar.
function Copy-Tree([string]$Source, [string]$Destination, [int]$Depth, [System.Collections.Generic.List[string]]$Skipped) {
    if ($Depth -gt $MaxDepth) { $Skipped.Add("$Source (çok derin)"); return }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        if (Test-Excluded $item) { $Skipped.Add($item.FullName); continue }
        $target = Join-Path $Destination $item.Name
        if ($item.PSIsContainer) {
            Copy-Tree $item.FullName $target ($Depth + 1) $Skipped
        } elseif ($item.Length -gt $MaxFileBytes) {
            $Skipped.Add("$($item.FullName) ($([math]::Round($item.Length / 1MB)) MB, çok büyük)")
        } else {
            Copy-Item -LiteralPath $item.FullName -Destination $target -Force
        }
    }
}

function Get-SkillName([string]$SkillMd, [string]$Fallback) {
    $lines = @(Get-Content -LiteralPath $SkillMd -Encoding UTF8 -TotalCount 40)
    if ($lines.Count -gt 0 -and $lines[0].Trim() -eq '---') {
        foreach ($line in $lines | Select-Object -Skip 1) {
            if ($line.Trim() -eq '---') { break }
            if ($line -match '^name:\s*["'']?([^"'']+?)["'']?\s*$') { return $Matches[1].Trim() }
        }
    }
    return $Fallback
}

function Get-JsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Write-Warn "$Path okunamadı: $($_.Exception.Message)"; return $null }
}

# --- Ön kontroller -----------------------------------------------------------

Write-Step 'Kontroller'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git bulunamadı. Git for Windows kurulu olmalı.' }
$RepoPath = [System.IO.Path]::GetFullPath($RepoPath)
# Windows PowerShell 5.1'de Stop modunda git'in stderr çıktısı hata sayılır; git çağrıları Continue ile yapılır.
$ErrorActionPreference = 'Continue'
$top = & git -C $RepoPath rev-parse --show-toplevel
$ErrorActionPreference = 'Stop'
if ($LASTEXITCODE -ne 0 -or -not $top) { throw "$RepoPath bir git reposu değil. -RepoPath ile repo klasörünü ver." }
$branch = (& git -C $RepoPath rev-parse --abbrev-ref HEAD).Trim()
$SkillsSrc = Join-Path $ClaudeHome 'skills'
if (-not (Test-Path -LiteralPath $SkillsSrc)) { Write-Warn "$SkillsSrc bulunamadı" }
Write-Ok "Repo: $RepoPath (branch: $branch)"
Write-Ok "Kaynak: $ClaudeHome"

$SkillsDst = [System.IO.Path]::GetFullPath((Join-Path $RepoPath '.claude/skills'))
$SyncDir   = [System.IO.Path]::GetFullPath((Join-Path $RepoPath '.claude/skills-sync'))
New-Item -ItemType Directory -Force -Path $SkillsDst, $SyncDir | Out-Null

# --- Skill'leri topla --------------------------------------------------------

Write-Step 'Skill''ler bulunuyor'
$candidates = New-Object System.Collections.Generic.List[object]

if (Test-Path -LiteralPath $SkillsSrc) {
    foreach ($dir in Get-ChildItem -LiteralPath $SkillsSrc -Directory -Force) {
        $skillMd = Join-Path $dir.FullName 'SKILL.md'
        if (Test-Path -LiteralPath $skillMd) {
            $candidates.Add([pscustomobject]@{ Folder = $dir.Name; Path = $dir.FullName; Origin = 'user' })
        } else {
            Write-Warn "$($dir.Name): SKILL.md yok, atlandı"
        }
    }
}

$plugins = @()
$marketplaces = @()
$pluginsDir = Join-Path $ClaudeHome 'plugins'
$installed = Get-JsonFile (Join-Path $pluginsDir 'installed_plugins.json')
if ($installed -and $installed.plugins) {
    foreach ($prop in $installed.plugins.PSObject.Properties) {
        # Sürüm 1: { "ad@market": {...} }, sürüm 2: { "ad@market": [ {...}, ... ] }
        foreach ($entry in @($prop.Value)) {
            $plugins += [pscustomobject]@{ id = $prop.Name; version = $entry.version; scope = $entry.scope }
            if ($SkipPluginSkills -or -not $entry.installPath) { continue }
            $pluginSkills = Join-Path $entry.installPath 'skills'
            if (-not (Test-Path -LiteralPath $pluginSkills)) { continue }
            foreach ($dir in Get-ChildItem -LiteralPath $pluginSkills -Directory -Force) {
                if (Test-Path -LiteralPath (Join-Path $dir.FullName 'SKILL.md')) {
                    $candidates.Add([pscustomobject]@{ Folder = $dir.Name; Path = $dir.FullName; Origin = "plugin:$($prop.Name)" })
                }
            }
        }
    }
}
$known = Get-JsonFile (Join-Path $pluginsDir 'known_marketplaces.json')
if ($known) {
    foreach ($prop in $known.PSObject.Properties) {
        $marketplaces += [pscustomobject]@{ name = $prop.Name; source = $prop.Value.source }
    }
}
$settings = Get-JsonFile (Join-Path $ClaudeHome 'settings.json')
$enabledPlugins = if ($settings -and $settings.enabledPlugins) { $settings.enabledPlugins } else { $null }

if ($candidates.Count -eq 0) { throw 'Hiç skill bulunamadı.' }

# --- Kopyala -----------------------------------------------------------------

Write-Step "Kopyalanıyor: $($candidates.Count) skill"
$skipped = New-Object System.Collections.Generic.List[string]
$copied = @()
$seen = @{}
foreach ($c in $candidates) {
    # Aynı adda iki skill varsa kişisel olan kazanır (Claude Code'daki öncelikle aynı)
    if ($seen.ContainsKey($c.Folder)) { Write-Warn "$($c.Folder) ($($c.Origin)) atlandı: aynı adda skill zaten var ($($seen[$c.Folder]))"; continue }
    $seen[$c.Folder] = $c.Origin
    $dst = Join-Path $SkillsDst $c.Folder
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
    Copy-Tree $c.Path $dst 0 $skipped
    $files = @(Get-ChildItem -LiteralPath $dst -Recurse -File -Force)
    $bytes = ($files | Measure-Object -Property Length -Sum).Sum
    $copied += [pscustomobject]@{
        folder = $c.Folder
        name   = Get-SkillName (Join-Path $dst 'SKILL.md') $c.Folder
        origin = $c.Origin
        files  = $files.Count
        bytes  = [long]$bytes
    }
    Write-Ok ("{0,-40} {1,4} dosya  {2}" -f $c.Folder, $files.Count, $c.Origin)
}
if ($skipped.Count -gt 0) {
    Write-Host "  Atlananlar ($($skipped.Count)): node_modules, .git, .env vb." -ForegroundColor DarkGray
    $skipped | Select-Object -First 15 | ForEach-Object { Write-Host "    - $($_.Replace($ClaudeHome, '~/.claude'))" -ForegroundColor DarkGray }
}

# --- Envanter ----------------------------------------------------------------

$inventory = [ordered]@{
    syncedFrom      = 'local Claude Code (~/.claude)'
    skills          = $copied
    plugins         = $plugins
    marketplaces    = $marketplaces
    enabledPlugins  = $enabledPlugins
}
$inventoryPath = Join-Path $SyncDir 'inventory.json'
[System.IO.File]::WriteAllText($inventoryPath, ($inventory | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding $false))
Write-Ok "Envanter: .claude/skills-sync/inventory.json"

# --- Gizli bilgi taraması ----------------------------------------------------

Write-Step 'Gizli bilgi (API anahtarı, token) taranıyor'
$findings = @()
foreach ($skill in $copied) {
    foreach ($file in Get-ChildItem -LiteralPath (Join-Path $SkillsDst $skill.folder) -Recurse -File -Force) {
        if ($TextExtensions -notcontains $file.Extension.ToLowerInvariant()) { continue }
        $content = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction SilentlyContinue
        if (-not $content) { continue }
        foreach ($kind in $SecretPatterns.Keys) {
            foreach ($m in [regex]::Matches($content, $SecretPatterns[$kind])) {
                $line = ($content.Substring(0, $m.Index) -split "`n").Count
                $preview = $m.Value.Substring(0, [Math]::Min(12, $m.Value.Length)) + '...'
                $rel = $file.FullName.Substring($SkillsDst.Length + 1)
                $key = "${rel}:$line"
                $existing = $findings | Where-Object { $_.Location -eq $key } | Select-Object -First 1
                if ($existing) {
                    if ($existing.Kinds -notcontains $kind) { $existing.Kinds += $kind }
                } else {
                    $findings += [pscustomobject]@{ Location = $key; Kinds = @($kind); Preview = $preview }
                }
            }
        }
    }
}
if ($findings.Count -gt 0) {
    Write-Warn "$($findings.Count) olası gizli bilgi bulundu:"
    foreach ($f in $findings) {
        Write-Host ("    - {0}  [{1}]  {2}" -f $f.Location, ($f.Kinds -join ', '), $f.Preview) -ForegroundColor Yellow
    }
    if (-not $Force) {
        Write-Host 'Commit yapılmadı. Bu dosyaları kontrol et: gerçek bir anahtar varsa .claude/skills altındaki kopyadan sil.' -ForegroundColor Yellow
        Write-Host 'Hepsi örnek/yer tutucu ise script''i -Force ile tekrar çalıştır.' -ForegroundColor Yellow
        exit 2
    }
    Write-Warn '-Force verildi, devam ediliyor'
} else {
    Write-Ok 'Bulgu yok'
}

# --- Git'in yok saydığı dosyalar ---------------------------------------------

$ignored = @(& git -C $RepoPath ls-files --others --ignored --exclude-standard -- .claude/skills)
if ($ignored.Count -gt 0) {
    Write-Warn ".gitignore yüzünden commit'e girmeyecek $($ignored.Count) dosya var:"
    $ignored | Select-Object -First 15 | ForEach-Object { Write-Host "    - $_" -ForegroundColor DarkGray }
}

# --- Commit / push -----------------------------------------------------------

if ($NoCommit) { Write-Step 'Bitti (-NoCommit: commit yapılmadı)'; exit 0 }

Write-Step 'Commit'
$ErrorActionPreference = 'Continue'
& git -C $RepoPath add -- .claude/skills .claude/skills-sync
& git -C $RepoPath diff --cached --quiet -- .claude/skills .claude/skills-sync
if ($LASTEXITCODE -eq 0) { Write-Ok 'Değişiklik yok, commit gerekmedi'; exit 0 }
& git -C $RepoPath commit -m "Skills: yerel Claude Code skill'leri eklendi ($($copied.Count) skill)" -- .claude/skills .claude/skills-sync
if ($LASTEXITCODE -ne 0) { throw 'git commit başarısız oldu' }

if ($NoPush) { Write-Step 'Bitti (-NoPush: push yapılmadı)'; exit 0 }

Write-Step "Push: origin/$branch"
& git -C $RepoPath push -u origin $branch
if ($LASTEXITCODE -ne 0) { throw 'git push başarısız oldu' }
Write-Step "Bitti: $($copied.Count) skill origin/$branch dalına gönderildi"
