# İş bilgisayarında adım adım başlangıç

Uygulama Windows'ta bir pencere açar. Excel'i ve çalışma modunu seçip **Analiz et ve sonucu kaydet** düğmesine basarsınız. Ayrıntılı sonuçlar yeni bir Excel dosyasına yazılır. Web sunucusu, Graphify kurulumu veya Excel makrosu gerekmez.

Önce demo ile kurulumu doğrulayın, sonra kurum ayarlarını girin. Demo banka bağlantılarını doğrulamaz. Gerçek Oracle ve Qwen bağlantıları iş bilgisayarında henüz denenmelidir.

## 1. Projeyi alın

Repo bilgisayarınızda zaten varsa **repo kökünde** PowerShell açın:

```powershell
git status --short
git switch main
git pull --ff-only origin main
cd risk-feature-workbench
```

`git status` yerel değişiklik gösterirse bunları koruyun; pull çakışmasını `reset --hard` ile çözmeyin. Farklı bir dalda çalışıyorsanız kendi dal düzeninizi bozmadan `main` güncellemesini alın.

Repo henüz yoksa proje tutacağınız klasörde:

```powershell
git clone https://github.com/MertAErntrk/ykb-insource-projects.git
cd ykb-insource-projects\risk-feature-workbench
```

Sonraki bütün komutlar **risk-feature-workbench** klasöründe çalıştırılır. Repo herkese açıktır; kurum adresleri, erişim bilgileri ve gerçek Excel dosyaları repoya konulmamıştır.

## 2. Python sürümünü kontrol edin

```powershell
py -0p
py -3.9 --version
```

Bu projenin hazır kurulumu iş bilgisayarınızdaki **Python 3.9.7, 64 bit** sürümüne göre hazırlanmıştır. Python yükseltmeniz gerekmez. pip, venv ve pencere için Tcl/Tk bileşenleri bulunmalıdır.

`py` komutu yoksa `python --version` ile mevcut Python'u kontrol edin. Kurulum betiği `py -3.9`, yoksa `python` kullanır. Birden fazla Python varsa çalıştırıcının tam yolunu verebilirsiniz:

```powershell
.\scripts\setup.ps1 -Python 'C:\kurum-python\python.exe'
```

Bu yol örnektir; kendi Python 3.9.7 konumunuzu yazın. Betik 64 bit ve 3.9 sürüm kontrolü yapar. Eski bir denemeden kalan 3.12 `.venv` klasörü varsa uygulamayı kapatıp klasörü başka adla saklayın ve kurulumu tekrarlayın; ortamlar birbirinin yerine kullanılamaz.

## 3. Kurun ve ekranı açın

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

Bu işlem projeye özel `.venv` ortamını kurar ve **Risk alanı incelemesi** penceresini açar. Paket indirmek için kurumun onaylı Python paket kaynağına erişim gerekir; demo banka ağına bağlanmaz.

PowerShell betikleri engelleniyorsa politika değiştirmeden aynı işi şu komutlarla yapın:

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-build.lock
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-demo.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m riskmatch gui
```

Bir kurulum komutu hata verirse sonraki adıma geçmeyin. Paket aynası/vekil sunucu gerekiyorsa kurumun pip ayarlarını kullanın. İnternetsiz kurulum ayrıntıları [Windows notlarında](WINDOWS.md); `requirements-build.lock` pip 25.3 ve setuptools 80.9.0 sürümlerini de sabitler. Manuel/offline kurulumda önce bu dosyayı, sonra demo veya gerçek-mod kilidini kurun.

## 4. Önce demo çalıştırın

1. Pencerede **Demo · sentetik katalog** seçili kalsın.
2. **Sentetik örneği kullan** düğmesine basın.
3. **Analiz et ve sonucu kaydet** düğmesine basın.
4. Açılan kayıt penceresinde yeni çıktı dosyasının yerini seçin.
5. İş bitince dosyayı Excel'de açın. **Sonuçlar**, **Adaylar**, **Çalışma bilgisi**, **Kanıtlar** ve iki kaynak girdi sayfası olmalı; 28 talep incelenir.

Ekran açılamazsa aynı demoyu komutla çalıştırabilirsiniz:

```powershell
.\.venv\Scripts\python.exe -m riskmatch demo --output-dir runtime\demo
```

Çıktı `runtime\demo\demo_sonuclar.xlsx` olur. Hazır örnek de `examples\demo_run\demo_sonuclar.xlsx` içindedir.

## 5. Gerçek çalışma için yalnız yerel ayar dosyasını değiştirin

Normal pilot için kaynak kod değişikliği gerekmez; kurum bağlantı bilgilerini bir kez yerel dosyaya girersiniz:

```powershell
Copy-Item config\real.example.json config.local.json
notepad config.local.json
```

Dosya zaten varsa tekrar kopyalamayın; dolu ayarlarınızı silmeyin. `config.local.json` Git tarafından dışlanır. JSON'da yorum satırı veya son elemandan sonra fazladan virgül kullanmayın.

| Yerel dosyadaki alan | Ne yazılacak / kimden alınacak |
|---|---|
| `oracle.source_database` | Kaynak için ayırt edici katalog etiketi. Bağlantı adresi değildir. |
| `oracle.environment` | Kapsamın ortam etiketi; örneğin kurumun DEV/UAT tanımı. |
| `oracle.container` | Bağlanılan Oracle'ın gerçek `CON_NAME` değeri; DBA'dan alın. Uygulama bunu bağlantıda karşılaştırır. |
| `oracle.schemas` | Yetkiniz olan hedef şema adları, JSON listesi: `["ONAYLI_SEMA"]`. İlk denemeyi küçük bir şemayla yapın. |
| `oracle.expected_db_name` | İsteğe bağlı ek kontrol; kullanacaksanız gerçek `DB_NAME` değerini ekleyin. |
| `oracle.user_env`, `password_env`, `dsn_env` | **Değer değil, ortam değişkeni adıdır.** Örnekteki `RISK_ORACLE_...` adları kalabilir; değerler 6. adımda girilir. |
| `oracle.wallet_dir_env`, `wallet_password_env` | Wallet kullanılmıyorsa bu iki satırı kaldırın. Kullanılıyorsa DBA'nın verdiği dizin/parola için aynı isimli ortam değişkenlerini tanımlayın. |
| `llm.endpoint` | LLM servisinin **tam HTTPS çağrı adresi**. Yalnız `/v1` taban adresi yeterli değildir. |
| `llm.allowed_hosts` | Endpoint'in yalnız alan adını içeren liste; `https://`, port veya yol eklemeyin. |
| `llm.model`, `llm.model_version` | Servis ekibinin doğruladığı model kimliği ve dağıtım sürümü. |
| `llm.contract` | Servis gerçekten chat completions JSON sözleşmesini destekliyorsa `"openai_chat"`; farklıysa `"template"` ve açık istek/yanıt eşlemesi gerekir. |
| `embedding.endpoint`, `allowed_hosts` | **Ayrı embedding servisinin** tam HTTPS çağrı adresi ve alan adı listesi. |
| `embedding.model`, `model_version`, `dimensions` | Gerçek embedding modeli, sürümü ve dönen vektörün sayısal boyutu. `dimensions` tırnaksız tam sayı olmalı. |
| `embedding.request_template`, `response_path` | Servisin gerçek istek ve yanıt biçimi; aşağıdaki açıklamaya bakın. |
| İki serviste `api_key_env` | Anahtar gerekiyorsa örnekteki ortam değişkeni adları kalır. Servis anonim erişim veriyorsa ilgili `api_key_env` satırını kaldırın. |
| `allow_real_connections` | Diğer ayarlar tamamlanınca `true` yapın. Bu tek başına bağlantı başlatmaz; gerçek analiz ayrıca başlatılmalıdır. |

LLM ve embedding bilgilerini servis sahibinden ayrı ayrı alın. Bir servis adresinden model adı, API yolu veya boyut tahmin edilmez. Şifre/API anahtarı bu JSON'a yazılmaz.

Örneğin **yalnız kurum servisi bu biçimi gerçekten kullanıyorsa** embedding eşlemesi şöyle olabilir:

```json
"request_template": {"model": "{model}", "input": "{texts}"},
"response_path": "data[*].embedding"
```

Bu parça tam bir dosya değildir; mevcut embedding bölümündeki ilgili alanların yerine konur. `{texts}` ve `{model}` uygulamanın yer tutucularıdır, değişmeden kalır. `{"inputs": "{texts}"}` / `vectors[*].values` gibi başka bir sözleşme varsa servis belgesine göre uyarlayın. Ayrıntılar: [adapter sözleşmeleri](ADAPTERS.md).

Bağlantı ve model ayarları için `src/` dosyalarını değiştirmeniz gerekmez. Kurum servisi farklı kimlik doğrulama, Oracle Thick/Client veya mevcut şablon sisteminin desteklemediği bir protokol istiyorsa adapter geliştirmesi gerekir; standart ayarla desteklendiği varsayılmaz.

## 6. Oracle ve model erişim bilgilerini aynı PowerShell oturumunda girin

Gerçek modun Oracle sürücüsünü kurun:

```powershell
.\scripts\setup.ps1 -Real
```

Betik çalıştırılamıyorsa:

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-real.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
```

Oracle bilgilerini komut geçmişine düz metin şifre yazmadan girin:

```powershell
$oracleCredential = Get-Credential -Message 'Oracle kullanıcınızı ve parolanızı girin'
$env:RISK_ORACLE_USER = $oracleCredential.UserName
$env:RISK_ORACLE_PASSWORD = $oracleCredential.GetNetworkCredential().Password
$env:RISK_ORACLE_DSN = Read-Host 'DBA tarafından verilen DSN (host:port/service_name veya TNS adı)'
Remove-Variable oracleCredential
```

API anahtarı isteyen servisler için aşağıdaki işlevi tanımlayıp **yalnız gereken satırları** çalıştırın:

```powershell
function Set-RiskSessionSecret([string]$Name) {
    $secretValue = Read-Host "$Name değerini girin" -AsSecureString
    try {
        [Environment]::SetEnvironmentVariable(
            $Name,
            [System.Net.NetworkCredential]::new('', $secretValue).Password,
            'Process'
        )
    } finally {
        $secretValue.Dispose()
    }
}
Set-RiskSessionSecret RISK_LLM_API_KEY
Set-RiskSessionSecret RISK_EMBEDDING_API_KEY
```

Wallet kullanılıyorsa dizin için `$env:RISK_ORACLE_WALLET_DIR = Read-Host 'Wallet dizini'`, parola gerekiyorsa `Set-RiskSessionSecret RISK_ORACLE_WALLET_PASSWORD` kullanın.

Bu değerler mevcut PowerShell sürecine ve onun açtığı uygulamaya aktarılır. **Uygulamayı aynı PowerShell'den açın.** Pencereyi başka yerden açarsanız bu değerleri alamaz. Yeni PowerShell oturumunda yeniden girmeniz gerekir. `.env` dosyası kendiliğinden okunmaz; kalıcı sistem ortam değişkeni oluşturmanız gerekmez.

## 7. Ayarları kontrol edin, gerçek Excel'i hazırlayın

```powershell
.\.venv\Scripts\python.exe -m riskmatch check-config config.local.json --mode real
New-Item -ItemType Directory -Force private, runtime | Out-Null
```

`check-config` ağ bağlantısı yapmadan dosyanın biçimini doğrular. Başarılı olması şifre, DNS, sertifika, Oracle yetkisi veya model yanıtının çalıştığını kanıtlamaz.

Kendi talep dosyanızı `private\talepler.xlsx` olarak saklayın. Örnek dosyayı şablon olarak kullanabilirsiniz. Gerekli başlıklar:

```text
Kategori | Alan Adı | Alan tipi | Açıklama | Halihazırda bildiriliyor mu | Görüş | Hazırlama süresi
```

Başlıklar aynı olmalı; `Talep Kimliği` ek sütunu isteğe bağlıdır. Birden fazla sayfa desteklenir. Yalnız `.xlsx` okunur; `.xls` dosyasını önce Excel'de `.xlsx` olarak kaydedin.

Satırları sonraki denemelerde düzenleyip sıralayacaksanız önce kalıcı kimlikli kopya üretin:

```powershell
.\.venv\Scripts\python.exe -m riskmatch prepare private\talepler.xlsx --output private\talepler_kimlikli.xlsx
```

Bundan sonra bu kaynak kopyayı kullanın ve `Talep Kimliği` değerlerini değiştirmeyin. Sonuç Excel'ini yeniden girdi olarak kullanmayın.

## 8. Gerçek analizi ekrandan çalıştırın

Erişim bilgilerini girdiğiniz **aynı PowerShell** penceresinde:

```powershell
.\scripts\start.ps1
```

1. **Gerçek · kurum bağlantıları** seçin.
2. **Excel seç** ile `private\talepler_kimlikli.xlsx` dosyanızı seçin.
3. **Ayar dosyası seç** ile `config.local.json` dosyasını seçin.
4. **Analiz et ve sonucu kaydet** düğmesine basın.
5. Çıktıyı örneğin `runtime\gercek_sonuclar.xlsx` olarak kaydedin.

Oracle ve Qwen bağlantıları bu analiz sırasında başlar. Oracle'dan erişilebilir katalog ve açıklama bilgileri okunur; müşteri işlem satırları hesaplanmaz, üretim tablolarına yazılmaz.

Aynı işin ekransız karşılığı:

```powershell
.\.venv\Scripts\python.exe -m riskmatch analyze private\talepler_kimlikli.xlsx --mode real --config config.local.json --output runtime\gercek_sonuclar.xlsx --state-dir runtime\pilot
```

Dosya üretilmesi bütün bağlantıların başarılı olduğu anlamına gelmez. Önce **Çalışma bilgisi → Tamamlanma / Hatalar** alanlarını kontrol edin. Eksik çalışmada uygulama demo verisine dönmez. Sonra **Sonuçlar**, **Adaylar**, **Kanıtlar** sayfalarını inceleyin. Eksik kanıt “alan kesin yok” demek değildir; benzerlik skoru başarı yüzdesi değildir. Uzman kararı Excel'de tamamlanır.

Kesilen bir işi aynı girdi, ayar ve state diziniyle yeniden başlatabilirsiniz. Tamamlanan uygun satırlar korunur; hatalı satırlar yeniden denenir. Değişen kaynak/ayar/model/kod için eski kararlar geçersizleşir.

## 9. Sonraki güncellemelerde

Uygulamayı kapatın. Proje klasöründen:

```powershell
git pull --ff-only origin main
.\scripts\setup.ps1 -Real
.\scripts\start.ps1
```

Yalnız demo kullanıyorsanız `-Real` olmadan kurun. Yerel `config.local.json`, `.venv`, `private/` ve `runtime/` Git'e dahil değildir; pull bunların yerine örnek ayar yazmaz. Örnek dosyayı mevcut yerel ayarın üzerine yeniden kopyalamayın. `git status --short` ile gönderilecek dosyaları her zaman kontrol edin.

## Takılırsanız

| Belirti | Bakılacak yer / sonraki adım |
|---|---|
| `py` veya Python bulunamıyor | `python --version` ile kontrol edin; betiğe `-Python` ile Python 3.9.7 çalıştırıcısının tam yolunu verin. |
| Betik çalıştırılması engellendi | 3. ve 6. adımdaki doğrudan Python komutlarını kullanın. |
| pip indirme/sertifika hatası | Kurumun paket aynası, proxy ve onaylı CA ayarlarını kontrol edin; hash doğrulamasını kapatmayın. |
| Tcl/Tk/pencere hatası | Python Tcl/Tk bileşenini kontrol edin; bu sırada CLI ile demo/analyze akışı kullanılabilir. |
| Gerçek bağlantılar kapalı | Yerel dosyada `allow_real_connections: true` ve arayüzde Gerçek mod seçili olmalı. |
| Tam endpoint / model / boyut hatası | 5. adımdaki ilgili LLM veya embedding alanını servis sözleşmesinden doldurun. |
| Oracle ortam değişkenleri boş | 6. adımı tekrar edin ve uygulamayı aynı PowerShell'den açın. |
| Oracle bağlantısı kurulamadı | DSN, VPN/kurum ağı, kullanıcı yetkisi ve gerekiyorsa wallet'ı DBA ile doğrulayın. Ayrıntılı hata değerleri sırların açığa çıkmaması için gösterilmez. |
| Yanlış DB/container | `container` ve varsa `expected_db_name` ile gerçek bağlantı hedefi uyuşmuyor; DBA'dan gerçek değerleri alın. |
| Şema için erişilebilir kolon görülmedi | Şema adı ve `ALL_*` katalog erişimi kontrol edilmeli; bu hata şemada veri olmadığı anlamına gelmez. |
| Model HTTP 401/403 | API anahtarı ortam değişkeni, servis yetkisi ve Bearer sözleşmesini kontrol edin. |
| Model TLS/erişim hatası | Tam endpoint, DNS/firewall ve Python'ın güvendiği kurum CA zincirini kontrol edin. JSON'da `ca_file` ayarı desteklenmez; gerekiyorsa BT'nin sağladığı PEM zincirini aynı oturumda `SSL_CERT_FILE` ile tanımlayın. |
| Embedding yanıt biçimi/boyutu hatası | `request_template`, `response_path`, `dimensions` alanlarını gerçek servis örneğiyle karşılaştırın. |
| Dosya kaydedilemiyor | Hedef Excel dosyasını kapatın, yazılabilir başka bir dosya adı seçin. |
| İndeks kayıt sınırı | İlk sürümde sınır 10.000 katalog kaydıdır. İlk deneme için şema kapsamını daraltın; büyük DWH için indeks geliştirmesi gerekir. |

Kurulumun yerel testleri için `.\scripts\test.ps1` çalıştırılabilir. Bu testler sentetik veriler ve taklit servislerle çalışır; canlı banka erişimi gerektirmez. Ayrıntılı doğrulama kapsamı: [TEST_RESULTS.md](TEST_RESULTS.md).
