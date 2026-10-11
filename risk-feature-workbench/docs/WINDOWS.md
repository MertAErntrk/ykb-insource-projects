# Windows teknik kurulum notları

Güncel akış: [iş bilgisayarı rehberi](IS_BILGISAYARINDA_BASLANGIC.md). Flask + Waitress yalnız 127.0.0.1 üzerinde çalışır. Python 3.9.7 x64 gerekir. Web paketleri requirements-real.lock içindedir; paketler SHA256 ile sabitlenmiştir. Tk ve Node.js gerekmez.

Script çalıştırılamıyorsa kurum politikasını değiştirmeden:

```powershell
$env:PIP_INDEX_URL = 'https://KURUM-ARTIFACTORY-ADRESI/api/pypi/REPO/simple'
py -3.9 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-build.lock
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-real.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m riskmatch serve
```

.venv bilgisayarlar arasında taşınmaz. Offline kurulumda kurumun onayladığı wheel dizinini --no-index --find-links ile kullanın; sabit sürüm/hash'leri koruyun. Kurum aynası aynı paketleri sağlamalıdır.

## CLI kullananlar

Web JSON dosyası zorunluluğunu kaldırır. Otomasyon için CLI gerçek modla kullanılabilir. config/real.example.json bir **CLI** şablonudur, web profilinden farklıdır. CLI şifreleri *_env ortam değişkenlerinden okur; web parolaları bellekte adapter'a aktarır. CLI'da model istemiyorsanız use_models:false ekleyin.

```powershell
.\.venv\Scripts\python.exe -m riskmatch check-config config.local.json --mode real
.\.venv\Scripts\python.exe -m riskmatch analyze talepler.xlsx --mode real --config config.local.json --output runtime\sonuclar.xlsx --state-dir runtime\pilot
```

check-config ağsız biçim kontrolüdür. CLI gerçek erişimi için allow_real_connections:true gerekir. Webde bağlantı testine veya incelemeye basmak erişimi başlatır. CLI demo ve gui komutları kaldırıldı.

Web profili, Excel ve rapor farklı dosyalardır. .local.json profili DSN/servis adresi içerir ama parola/API anahtarı içermez. Excel/rapor/checkpoint runtime/web altında kalır; kurumun saklama politikasına göre yönetin. Bu sürüm çok kullanıcılı sunucu olarak tasarlanmamıştır.
