# API Atlas — URL'den API kataloğu

İlgilendiğiniz **ekranın URL'sini** verin. Araç sayfayı kurulu Edge/Chrome veya Playwright Chromium ile açar; sayfa yüklenirken kullanılan API çağrılarını ve yüklenen JavaScript dosyalarındaki API adresi adaylarını çıkarır. Normal soru-cevap Qwen endpoint'i bunları Türkçe açıklar. Ayrı bir uygulama arayüzü veya yerel web sunucusu yoktur.

Katalogda üç kanıt düzeyi bulunur:

| Kaynak | Anlamı |
|---|---|
| `har` | Tarayıcıdan gerçek istek/yanıt gözlendi. Canlı taramada da aynı ayrıştırıcı kullanılır. |
| `openapi` | İçe aktarılan veya sayfayla birlikte yüklenen API belgesinde tanımlı. Çalıştırıldığı anlamına gelmez. |
| `javascript` | Yüklenen JavaScript içinde adres adayı bulundu. Eksik/eski olabilir; otomatik denenmez. |

**Bir ekran URL'sinden uygulamanın bütün backend uç noktalarının eksiksiz çıkarılması mümkün değildir.** Düğmeyle açılan veriler, yüklenmeyen JavaScript parçaları, başka roller ve sunucular arası API'ler görünmeyebilir. Katalog kapsamı yeni sayfalar, HAR kayıtları ve belgeler ekledikçe büyür. Qwen yorumları ayrıca çıkarım olarak işaretlenir.

## 1. İş bilgisayarında hazırlık

```powershell
cd ykb-insource-projects
git pull
cd api-catalog
python --version
python app.py doctor
```

- **URL taraması / canlı kayıt:** Python 3.9+ ve isteğe bağlı Playwright paketi. Python 3.9 için uyumlu paket sürümleri sabitlenmiştir.
- **HAR/OpenAPI içe aktarma, Qwen ve raporlar:** Python 3.9+; üçüncü taraf paket gerekmez.
- Node.js, Hermes, Ollama, ajan CLI'si veya yerel model kurulumu gerekmez.

URL taraması için:

```powershell
python -m pip install --upgrade -r requirements-record.txt
```

Python 3.9'da Playwright 1.58.0, greenlet 3.2.4 ve typing-extensions 4.15.0 seçilir. Python 3.10+ için güncel Playwright aralığı kullanılır. Kurulum yalnızca hazır wheel paketlerini kabul eder; C/C++ derlemesi başlatmaz.

Windows'ta kurulu Edge için `--channel msedge` kullanılır; ayrı Chromium indirmeniz gerekmez. Chrome için `--channel chrome`. Paket kurulumu kurumda mümkün değilse aşağıdaki HAR yolunu kullanın. İnternetsiz kurulumda kurumunuzun paket deposunu veya uygun Python/Windows sürümü için hazırlanmış wheel dosyalarını kullanabilirsiniz.

## Kurulum hatası: greenlet wheel bulunamıyor

`Building wheel for greenlet` eski kurulumun kaynak paket seçtiğini gösterir. Son satır `Microsoft Visual C++ 14.0 or greater is required` ise kaynak derlemesi için gereken C++ derleyicisi bulunmamıştır; hazır wheel kullanımı derleme ihtiyacını kaldırır. Uyumsuz paket sürümü, Python mimarisi, eski pip veya kurumsal depoda eksik wheel buna neden olabilir; derleme çıktısının son satırları olmadan kesin neden belirlenemez.

Güncel `requirements-record.txt` kaynak paketleri kabul etmez. Güncel kodu `git pull` ile aldıktan sonra `python -m pip install --upgrade -r requirements-record.txt` kullanın. `No matching distribution found` gelirse, bildirilen paketin uyumlu hazır dosyası yapılandırılmış depoda bulunamamıştır. `python app.py doctor` Python yolu, bit sayısı ve kurulu paket sürümlerini gösterir.

Python 3.9 Windows için greenlet 3.2.4 dosyaları:

- 64 bit: `greenlet-3.2.4-cp39-cp39-win_amd64.whl`
- 32 bit: `greenlet-3.2.4-cp39-cp39-win32.whl`

Bu dosyaların kurumun paket deposunda erişilebilir olması gerekir. Günlük tarayıcıda F12 → HAR kaydı yolu hiçbir Playwright/greenlet kurulumu gerektirmez.

## 2. Qwen bağlantısı

```powershell
Copy-Item config.example.json qwen.local.json
notepad qwen.local.json
```

`endpoint` alanına gerçek adresi, `model` alanına sunucunun beklediği model ID'sini yazın. Örnek dosyadaki localhost adresi bir örnektir; araç Qwen sunucusu başlatmaz. Gerekliyse anahtar ve kurum sertifikası:

```powershell
$env:QWEN_API_KEY = "GERCEK_ANAHTAR"
$env:QWEN_CA_BUNDLE = "C:\sertifikalar\kurum-ca.pem"
python app.py doctor --config qwen.local.json --qwen
```

Ayar dosyasına anahtar yazılmaz. `api_key_env` ve `headers_from_env`, değerleri süreç ortamından okur. `QWEN_ENDPOINT`, `QWEN_MODEL`, `QWEN_CA_BUNDLE` ayar dosyasını geçersiz kılabilir. TLS sertifika doğrulaması açıktır; HTTP yönlendirmeleri izlenmez. Python'un standart proxy ortam değişkenleri geçerlidir.

Endpoint OpenAI biçiminde değilse `config.custom.example.json` dosyasını kopyalayın. Örneğin servis `{"question":"..."}` alıp `{"answer":"..."}` döndürüyorsa örnek hazırdır. `request_template` içindeki **tam dize değeri** `$prompt`, `$system`, `$combined_prompt`, `$model` ile değiştirilir. `response_path` JSON Pointer'dır: `/answer` veya `/data/0/text` gibi. Tool calling, agent veya stream desteği gerekmez.

## 3. Bir kez oturum açın

```powershell
python app.py login "https://portal.example.com/ilgili-ekran" --channel msedge
```

Açılan tarayıcıda normal kurumsal girişinizi yapın. Bitince terminale `stop` yazın. Araç `data/profiles/msedge` altında ayrı profil kullanır; günlük tarayıcınızdaki oturum otomatik kopyalanmaz. Sonraki taramalar **aynı channel ve data-dir** ile bu profili kullanır. Oturum süresi dolarsa yeniden login gerekir. Kurumsal SSO/politikaların bu tarayıcıda çalışması henüz kurum ortamında doğrulanmadı.

## 4. Ekran URL'sini tarayın

```powershell
python app.py scan "https://portal.example.com/ilgili-ekran" --channel msedge --app "Cozum Merkezi" --wait 20 --config qwen.local.json
```

Sayfa açılır, ilk yükleme tamamlandıktan sonra 20 saniye boyunca trafik kaydedilir. Form doldurma, düğme tıklama, atama veya yorum gönderme otomatik yapılmaz. Sayfanın kendisinin yüklenirken ürettiği çağrılar gözlenir. Qwen normal HTTP isteğiyle analiz edilir; ardından raporlar yazılır.

- Qwen ayarlanmadan da `--config` olmadan taranabilir. Yöntemler, parametreler ve yanıt alanları çıkarılır; iş amacı açıklamaları sınırlı kalır.
- `--headless`: tarayıcı penceresini gösterme; geçerli oturum önceden açılmış olmalı.
- `--scroll`: sayfayı kaydırarak ek yüklemeleri gözle.
- `--host portal.example.com --host api.example.com`: kayıt kapsamını bu alan adlarıyla sınırla. `*.example.com` kalıbı desteklenir. Varsayılan: bu kayıt penceresinde yüklenen tüm alan adları.
- `--executable-path "C:\...\msedge.exe"`: kurulu tarayıcı yolu.
- Birden fazla sayfa URL'si aynı scan komutuna verilebilir; sırayla taranır.

API kök adresi (`.../api/`) yerine **uygulama ekranının tam adresini**, gerekiyorsa `#!/...` bölümünü de verin. Sayfa giriş ekranına yönlenirse sıfır API çıkabilir veya yalnızca giriş çağrıları görülebilir; bu, uygulamanın API'si olmadığı anlamına gelmez.

## 5. Çıktılar ve arama

| Dosya | İçerik |
|---|---|
| `output/catalog.md` | Adres, amaç, kaynak, parametre, yanıt alanı, belirsizlik ve Python kullanım taslakları |
| `output/catalog.json` | Makine tarafından okunabilir birleşik katalog |
| `output/openapi-index.json` | Uygulama/sunucu bazında OpenAPI dosyaları |
| `output/openapi/*.json` | Gözlenen/belgelenen çağrılardan kısmi OpenAPI 3.1 sözleşmeleri |
| `output/openapi.json` | Yalnızca tek uygulama/sunucu grubu varsa kolay erişim kopyası |
| `output/api_client.py` | Gerçek giriş değerleriyle çağrılabilecek Python yardımcı modülü |

```powershell
python app.py search cif
python app.py search "kaydı açan"
python app.py search --method POST
python app.py show UCNOKTA_ID_BASLANGICI
python app.py stats
python app.py analyze --config qwen.local.json --limit 10
python app.py export
```

Qwen açıklaması şema/bağlam değişmediyse tekrar istenmez. `--force` yeniden analiz eder. Model hata verirse katalog ve mevcut açıklamalar korunur; komut başarısız analiz sayısını bildirip `2` çıkış kodu verir. Büyük şema model sınırını aşıyorsa analiz atlanır; katalogdaki gözlem korunur.

## 6. Düğme arkasındaki çağrıyı da kaydetmek

Örneğin “Üzerime Al” veya “Yorum Gönder” sayfa açılışında çağrılmayabilir. Bu durumda:

```powershell
python app.py record "https://portal.example.com/ilgili-ekran" --channel msedge --app "Cozum Merkezi"
```

Terminale `mark Uzerime al` yazın, ardından tarayıcıda normalde yapacağınız işlemi yapın. Yeni işlem için `mark Yorum gonder` yazılabilir. `stop` kaydı tamamlar. Etiketler bağlamdır; aynı anda gelen arka plan çağrıları da aynı etiketi alabilir. Bunlar tek başına düğme/API eşleşmesini kanıtlamaz.

## 7. Paket kurulamıyorsa HAR yolu

1. Günlük tarayıcınızda F12 → Network → Preserve log açın, kaydı temizleyin.
2. İlgili ekranı açın/yenileyin; gerekliyse bir işlemi normal şekilde yapın.
3. **Yanıt içeriklerini de içeren HAR** dışa aktarın. Yalnızca ekran görüntüsü alanları çıkarmaya yetmez. Sanitized HAR bazı auth başlıklarını çıkarır; iş verilerini bütünüyle kaldırdığı varsayılmamalı.
4. Dosyayı kurum bilgisayarında tutup içe aktarın:

```powershell
python app.py import "C:\kayitlar\ekran.har" --app "Cozum Merkezi" --action "Yeni gelen kayitlar" --config qwen.local.json
python app.py import "C:\kayitlar\diger-ekran.har" --app "Cozum Merkezi"
python app.py import "C:\belgeler\openapi.json" --app "Cozum Merkezi" --origin "https://portal.example.com"
```

Araç Chrome/Edge HAR dosyalarının yanında `reverse-api-engineer` tarafından üretilen HAR dosyalarını da alır. Ham HAR dosyasını kopyalamaz veya değiştirmez. OpenAPI/Swagger **JSON** desteklenir; YAML ve uzak `$ref` indirme desteği yoktur.

## 8. Python'dan aksiyon kullanımı

Üretilen `api_client.py` çalıştırıldığında yalnızca uç noktaları listeler. `from api_client import call` ile içe aktarılarak gerçek parametre ve **o uygulama için geçerli** kimlik doğrulama başlıkları verilmelidir. `catalog.md` çağrı taslaklarını içerir.

- POST/PUT/PATCH/DELETE için `allow_write=True` açıkça belirtilmelidir. POST arama da yapabilir; kontrol bu yüzden ihtiyatlıdır.
- JavaScript adayları gerçek istek/belge ile doğrulanmadan yardımcı modülden çalıştırılamaz.
- Oturum, CSRF, kayıt sürümü ve adımların sırası katalogdan otomatik tamamlanmaz. Atama → yorum → süreç ilerletme zinciri için gerçek istekler ayrıca doğrulanmalıdır.
- Yardımcı modül NTLM/Kerberos/SSO giriş akışını kendiliğinden uygulamaz. Yalnızca doğru API yolunu bilmek oturumsuz erişim sağlamaz.
- JSON/form-urlencoded gövdeler desteklenir. XML/multipart/binary için `raw_body` ve uygun Content-Type elle sağlanır. GraphQL sorgu metni maskelendiğinden kullanıcı tarafından doldurulmalıdır.
- Yazma işlemleri otomatik yeniden denenmez; yorumun iki kez gönderilmesi gibi sonuçlar önlenir. Gerçek iş kurallarına uygun hata yönetimi ayrıca eklenmelidir.

## Veri ve doğruluk sınırları

API gövdelerindeki gerçek skaler değerler katalog örneklerinde kaldırılır; auth başlıklarının değerleri kaydedilmez. Alan adları, türler, adresler, kullanıcı etiketleri ve belge açıklamaları kalır. Sayısal/UUID/bazı harf-rakam kimlikleri yolda `{id}` olur; her türlü hassas değeri tanıma garantisi yoktur. Sabit sayısal yollar yanlış birleştirilebilir; HAR import için `--keep-paths` bunu kapatır ancak gerçek kimlikler çıktıda kalabilir.

**Tarayıcı profili normal tarayıcı gibi gerçek oturum bilgisi saklar.** Profil, veritabanı, raporlar ve `*.local.json` git dışında tutulur. Qwen'e katalog şeması ve işlem etiketleri gönderilir; gerçek gövde değerleri gönderilmez. Kullanıcı etiketi/belge metni hassas bilgi içerebilir; bunları da kurum verisi olarak değerlendirin.

Yanıt gövdesi yoksa şema çıkarılmaz. JSON gövdeleri 500 KB, JavaScript analizi 2 MB ile sınırlıdır. Şemalar derinlik/alan sınırlarıyla ve dizilerin ilk 20 elemanıyla örneklenir; sınırlama işaretleri şemada tutulur. Tüm alanların veya tüm iş kurallarının görüldüğü varsayılmaz. WebSocket mesajları, Service Worker üzerinden görünmeyen çağrılar ve dinamik JavaScript ifadeleri tam kapsanmaz. Aday adreslere otomatik istek, uç nokta tahminiyle tarama veya erişim atlatma yapılmaz.

## Referans repo ve testler

[kalil0321/reverse-api-engineer](https://github.com/kalil0321/reverse-api-engineer), commit `ab9919b652eca1c7b323c3f7c7b37ded3cef12a0` temel alındı:

- HAR → model analizi → kullanım belgesi yaklaşımı ve HAR dosyalarıyla uyumluluk.
- OpenAPI dokümantasyon talimatları kaynak olarak alınıp `catalog_app/prompts/analysis.md` içinde soru-cevap analizine uyarlandı. Özgün talimat `upstream_docs.md` içinde tutuluyor.
- MIT lisansı `THIRD_PARTY_LICENSES.txt` içinde bulunur.
- Kaynak reponun Python 3.11+ ajan SDK'ları ve otomatik kod çalıştırma akışı bu araçta bağımlılık değildir. Anahtarları koda gömme talimatı kullanılmaz.

```powershell
python app.py demo
python -m unittest discover -s tests -v
```

Demo bütünüyle sentetiktir. Gerçek tarayıcı testi isteğe bağlıdır:

```powershell
python -m playwright install chromium
$env:ATLAS_BROWSER_TEST = "1"
python -m unittest discover -s tests -v
```

19 test, gerçek yerel tarayıcı testi dahil geçti; ayrıntılar [docs/TESTLER.md](docs/TESTLER.md) içinde. Kurum içi Qwen ve Çözüm Merkezi bağlantısı bu geliştirme ortamından test edilmedi. Tasarım ayrıntıları: [docs/TASARIM.md](docs/TASARIM.md).
