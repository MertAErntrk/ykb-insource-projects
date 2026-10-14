# API Atlas — yerel API kataloğu

Bir uygulama/sayfa/düğme işlemi sırasında gözlenen HTTP çağrılarını HAR ve OpenAPI/Swagger JSON dosyalarından çıkarır. Parametreleri, istek/yanıt yapılarını ve işlem bağlamını yerel katalogda birleştirir. Kurum içi Qwen endpoint'i üzerinden Türkçe açıklama ve kullanım notu eklenebilir.

**Bu sürüm hedef uygulamanın API'lerini çalıştırmaz.** “Yeni Gelen Kayıtlar” veya “Üzerime al” gibi işlemlerin kayıttaki aday API'lerini bulmayı kolaylaştırır. Gerçek Çözüm Merkezi çağrıları henüz doğrulanmış değildir; örnekler tamamen sentetiktir.

## İş bilgisayarında başlatma

Python 3.9+ yeterlidir. Python standart kütüphanesi kullanılır: pip paketi, Node.js, Hermes, Ollama, Docker veya yerel model kurulumu gerekmez. Python 3.12+ mevcutsa onu kullanabilirsiniz. Python yorumlayıcısının kurumsal ortamda zaten çalışabildiği varsayılır.

```powershell
cd ykb-insource-projects
git pull
cd api-catalog
python app.py --open-browser
```

Klasörü kendi checkout konumunuza göre seçin. Alternatif: `run_windows.bat` dosyasına çift tıklayın. Arayüz: **http://127.0.0.1:8788**. Port doluysa `python app.py --port 8790 --open-browser` kullanın. Sunucu yalnızca `127.0.0.1` üzerinde dinler.

## İlk kullanım

1. **Örnek katalogla incele** düğmesiyle sentetik çağrıları görüntüleyin.
2. **Kayıt içe aktar** ile HAR veya OpenAPI/Swagger JSON dosyanızı seçin.
3. Uygulama, sayfa ve düğme/işlem adını yazın. İsterseniz ilgili alan adıyla sınırlandırın.
4. Bir API seçerek parametreleri, maskeli istek örneğini ve yanıt şemasını inceleyin.
5. **Qwen bağlantısı** paneline endpoint ve tam model ID'sini girin. Gerekiyorsa API anahtarını veya kurumsal CA dosya yolunu belirtin.
6. **Kaydet ve bağlantıyı dene** küçük bir test mesajı gönderir. **Qwen ile açıkla** yalnızca seçilen API'nin yapısal özetini modele gönderir.
7. Sayfa/işlem filtreleri veya Türkçe metin aramasıyla katalogda gezinip Markdown/JSON çıktısı alabilirsiniz. Çıktı, yeniden içe aktarılabilen HAR/OpenAPI dosyası değil katalog raporudur.

## Qwen bağlantısının sınırı

İlk sürüm **OpenAI-compatible chat/completions** biçimini destekler. Base URL'nin `/v1` ile biten hali veya tam `/chat/completions` URL'si kabul edilir. Model adı sunucunun beklediği tam ID olmalıdır. Kurumun endpoint'i başka bir protokol kullanıyorsa adaptör ayrıca uyarlanmalıdır; yalnızca bir URL'nin olması uyumluluğu kanıtlamaz.

Tool calling veya 64k bağlam zorunlu değildir; ajan çalıştırılmaz. Büyük API şemaları sessizce kırpılmaz; seçilen API özeti 24.000 karakteri aşarsa açıklama için daha küçük kayıt veya manuel not istenir. Aynı yapının mevcut açıklaması tekrar kullanılabilir; yeni bağlam/şema geldiğinde eski açıklama işaretlenir. Yeniden açıklama düğmesi modele yeniden istek gönderir.

API anahtarı arayüzde girildiğinde yalnızca çalışan Python sürecinin belleğinde tutulur. Program kapandıktan sonra yeniden girilmelidir. İsterseniz PowerShell'de `QWEN_BASE_URL`, `QWEN_MODEL`, `QWEN_API_KEY`, `QWEN_CA_BUNDLE` ortam değişkenlerini ayarlayabilirsiniz. `.env.example` yalnızca ayar isimlerini gösterir; `.env` otomatik okunmaz. Sertifika doğrulaması kapatılmaz. Python'un standart proxy ortam değişkenleri geçerlidir.

## Kayıt alma ve paylaşma

Ayrıntılı adımlar: [İlk kayıtları alma rehberi](docs/ILK_KAYITLAR.md).

Ham HAR diske kopyalanmaz. Ayrıştırma sırasında gerçek request/response alan değerleri örneklerden kaldırılır; kimlik bilgisi başlıklarının yalnızca türü saklanır. Katalogda uygulama/alan adları, parametre isimleri, kullanıcı tarafından yazılan etiketler ve belge açıklamaları kalabilir. Bu nedenle dışa aktarılmış katalogları da kurum verisi olarak değerlendirin ve paylaşmadan önce inceleyin.

SQLite ve Qwen bağlantı ayarları `data/` altında tutulur. Bu klasör, HAR, veritabanı ve çıktı klasörleri `.gitignore` ile dışlanır. Gerçek kayıtları, kurum adreslerini, müşteri/çalışan verilerini veya anahtarları repoya eklemeyin.

## Doğruluk sınırları

- Katalog yalnızca içe aktarılan belgeleri ve gözlenen trafiği kapsar. Kullanılmayan backend/sunucular arası API'leri kendiliğinden keşfetmez.
- CSS/görsel istekleri süzülür; API yolu, JSON yanıt veya Fetch/XHR işaretleri aday seçiminde kullanılır. Bazı çağrılar yanlış aday olabilir veya atlanabilir.
- Bir kayıt sırasında görülen tüm çağrılar verilen işlem etiketiyle ilişkilidir; bu, hepsini düğmenin doğrudan tetiklediğini kanıtlamaz. Kayıt öncesi Network listesini temizleyin.
- Sayısal/UUID benzeri yol parçaları `{id}` biçimine dönüştürülür. Parametre adları çıkarımdır. Sabit sayısal yollar bazı uygulamalarda yanlış birleştirilebilir.
- HAR query parametrelerinin zorunluluğu bilinmiyor olarak bırakılır. Şemalarda gözlenen alanlardan zorunluluk çıkarılmaz. OpenAPI'nin beyan ettiği zorunlu alanlar korunur.
- GraphQL adlandırılmış operasyonları ayrı kaydedilir. JSON olmayan gövdeler, SOAP/XML, base64 yanıtlar ve WebSocket mesajları tam şema analizi kapsamında değildir.
- İlk sürüm OpenAPI/Swagger **JSON** alır; YAML dönüştürülmelidir. Referanslar yalnızca belge içinden çözülür; uzak referanslar indirilmez.
- Qwen açıklamaları çıkarımdır. Belgeyle veya kontrollü entegrasyon testiyle doğrulanmadan üretim otomasyonu için kesin API sözleşmesi kabul edilmemelidir.

## Kontroller

```powershell
python diagnose.py
python -m unittest discover -s tests -v
```

Qwen ayarları arayüzden kaydedildikten sonra `python diagnose.py --qwen` ile yeni bir süreçten test yapılabilir. API anahtarı belleğe girildiyse bu yeni süreç onu devralmaz; gerekiyorsa `QWEN_API_KEY` ortam değişkenini o PowerShell oturumunda ayarlayın. Test çıktısı endpoint adresini veya anahtarı yazmaz.

## Kaynak proje

[kalil0321/reverse-api-engineer](https://github.com/kalil0321/reverse-api-engineer), HAR → model analizi yaklaşımı için incelendi (referans commit `ab9919b`). Bu uygulama bağımsız yazılmıştır; kaynak projenin ajanları, otomatik izin onayı, credential hardcoding talimatları veya hedef API çalıştırma akışı kullanılmaz.
