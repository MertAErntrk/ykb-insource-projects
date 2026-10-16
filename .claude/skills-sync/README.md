# Claude skill'leri

`.claude/skills/` klasöründeki skill'ler, bu repoyla açılan her Claude Code
oturumunda (bulut ya da yerel) otomatik yüklenir. Kaynakları kişisel
bilgisayardaki `C:\Users\<kullanıcı>\.claude\skills` klasörüdür.

## Yerel skill'leri repoya göndermek

Repo klasöründe, PowerShell'de:

```powershell
git pull
powershell -ExecutionPolicy Bypass -File .\scripts\sync-skills.ps1
```

Script şunları yapar:

1. `~/.claude/skills/` altındaki her skill'i ve kurulu plugin'lerin içindeki
   skill'leri `.claude/skills/` altına kopyalar. `node_modules`, `.git`,
   `.venv`, `__pycache__`, `.env` ve anahtar dosyalarını atlar.
2. Plugin ve marketplace listesini `.claude/skills-sync/inventory.json`
   dosyasına yazar (dosya yolları olmadan).
3. Kopyalarda API anahtarı ve token arar. Bulursa commit'lemez, dosya ve
   satırı gösterir. Bulgular örnek/yer tutucu ise `-Force` ile tekrar çalıştır.
4. Commit'ler ve bulunduğun dala push'lar.

Seçenekler: `-NoCommit` (sadece kopyala), `-NoPush` (commit'le ama gönderme),
`-SkipPluginSkills` (plugin skill'lerini alma).

Script yerelde silinen skill'leri repodan silmez; gerekirse klasörü elle sil.
Tekrar çalıştırıldığında repodaki kopyanın üzerine yazar.

## Bulut oturumları

Yeni bulut oturumları repoyu varsayılan daldan (`main`) klonlar. Skill'lerin
gelmesi için değişikliğin `main`'e girmiş olması gerekir.
