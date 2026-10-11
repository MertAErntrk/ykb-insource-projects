# Risk Feature Workbench

Ekrandan eklenen birden fazla değişkeni veya Excel sözlüğünü gerçek Oracle kataloğunda arayan **yerel web uygulaması**. Her satır için olası **şema, tablo, kolon, veritabanı açıklaması, alternatif adaylar ve gerekçeler** gösterilir. Aynı fiziksel adın varlığı ile iş tanımına uyum ayrı değerlendirilir. Sonuçlar Excel olarak indirilebilir.

Uygulamada demo seçeneği yoktur. Graphify kurulmaz. Müşteri kayıtları sorgulanmaz; yalnız erişilebilir katalog metadata'sı okunur. Python **3.9.7 x64** ve Windows PowerShell **5.1** desteklenir. Node.js, masaüstü GUI veya Tcl/Tk gerekmez.

## Başlatma

Kurulu uygulamayı PowerShell penceresinde **Ctrl+C** ile kapatın. Proje klasöründe:

```powershell
git pull --ff-only
.\scripts\start.ps1
```

Önceki web sürümü kuruluysa yeniden kurulum gerekmez. İlk kurulumda `start.ps1` öncesinde:

```powershell
.\scripts\setup.ps1 -IndexUrl 'https://sdlc.yapikredi.com.tr/artifactory/api/pypi/external-python/simple'
```

Tarayıcıda **http://127.0.0.1:8765** açılır. PowerShell penceresi açık kalmalıdır; kapatmak için Ctrl+C. Uygulama yalnız bu bilgisayardan erişilir, internete yayımlanmaz.

**[Adım adım iş bilgisayarı rehberi](docs/IS_BILGISAYARINDA_BASLANGIC.md)** · **[Kurumda bağlantı test senaryosu](docs/BAGLANTI_TEST_SENARYOSU.md)** · [Model/Oracle sözleşmeleri](docs/ADAPTERS.md) · [Testler](docs/TEST_RESULTS.md)

## Kullanım

1. **EDWLIVE bağlantısı** alanına yalnız kullanıcı adı ve parolayı girin. Kurum DSN'i uygulamada hazırdır.
2. **Bağlan ve şemaları getir** sonrasında açılır listeden taranacak şemaları seçin. İlk denemede bir veya iki şema seçip **Seçili şemaları test et** düğmesiyle katalog erişimini doğrulayın.
3. **Ekrandan değişken ekle** bölümünde alan adı/açıklama girin; **+ Değişken ekle** ile satırları çoğaltın. Alternatif olarak **Excel ile toplu ara** seçip dosyanızı yükleyin.
4. Ekrandan eklenen değişkenler için **Değişkenleri ara**, Excel için **Excel'deki değişkenleri ara** düğmesine basın. Standart Excel başlıkları otomatik eşleştirilir; farklı başlıklar için eşleştirme bölümü açılır.
5. **Detay** bütün adayların kaynak yolunu, açıklamasını ve kanıtlarını açar. **Excel sonucunu indir** ayrı rapor üretir.

Standart başlıklar: `Kategori | Alan Adı | Alan tipi | Açıklama | Halihazırda bildiriliyor mu`. Yalnız **Alan Adı** ve **Açıklama** sütunları zorunludur; başka başlıklar ekrandan eşlenebilir. Görüş/hazırlama süresi varsa korunur, zorunlu değildir. Bildirim bilgisi kullanıcı beyanıdır, veritabanı kanıtı sayılmaz.

Şema listesi `ALL_USERS` üzerinden alınan **adayları** gösterir; listede görünmek o şemadaki kolonlara erişildiğini kanıtlamaz. Seçili şema testi her şema için en fazla bir katalog satırı okur. Tüm veritabanının kolon kataloğu giriş sırasında taranmaz. Her aşamanın süresi ve hata kodu **Bağlantı test sonuçları** bölümünde görünür; **Tanılama raporunu indir** yerel bir JSON raporu oluşturur.

**LLM + embedding desteği** isteğe bağlıdır. Kapalıyken de gerçek Oracle kataloğunda ad ve açıklama araması yapılır. **Bağlantılar** sayfasındaki kurum adresleri hazırdır. **LLM bağlantısını test et** ve **Embedding bağlantısını test et** düğmeleri servis bilgilerini okuyup küçük bir gerçek çağrıyla doğrular. Birden fazla model varsa listeden seçim yapıp testi yineleyin; gerekiyorsa API anahtarı girin. Başarılı test çağrı adresini, sözleşmeyi ve embedding boyutunu doldurur. Özel servis alanları **Gelişmiş / özel servis ayarları** altında bulunur. LLM yorumu doğrulanmış eşleşme kararının yerine geçmez.

`DPY-4024`, Oracle çağrısının zaman sınırını aştığını belirtir; tek başına parola veya yetki sorunu demek değildir. Ayrıntılı ayırma adımları [bağlantı test senaryosundadır](docs/BAGLANTI_TEST_SENARYOSU.md). Gerçek kurum bağlantısı ancak iş bilgisayarındaki testle doğrulanabilir.

**0.4.0:** Web'de varsayılan **Hızlı aday araması**, ad/açıklama indeksinden en fazla 24 aday seçer ve yalnız bu adaylar için embedding kullanır. İş başına 256 farklı katalog metni sınırı vardır; sınırdan sonra metin araması sürer ve sonuçlarda açıkça belirtilir. Tam ad kontrolü bütün okunmuş katalogda yapılır. Kelime olarak benzemeyen adaylar kaçabilir; **Tüm katalogda anlamsal arama** ayrıca seçilebilir. LLM yorumu ayrı ve varsayılan olarak kapalıdır. Sonuçlar satır satır görünür; aşama, metin sayısı, geçen süre ve **Durdur** düğmesi vardır. Yeni paket kurulumu gerekmez.

Oracle kataloğu şema ve en fazla 25 tablo içeren gruplarla okunur; kolon ve açıklama görünümleri tek büyük sorguda birleştirilmez. Katalog hiç okunamazsa arama başlamaz. Kısmi kayıtlar korunur; eksik kapsamda yokluk sonucu verilmez. Katalog tarama sınırı 10.000 kolondur; hızlı aday araması bu sınırı kaldırmaz.

Embedding için model kimliğini boş bırakın; LLM için doğrulanan model `qwen38-27b`'dir. Gerçek embedding çağrısı başarılıysa isteğe bağlı `/v1/models` ve `/info` yollarındaki 404/405, nötr **Desteklenmiyor** durumuyla gösterilir. Son gerçek çağrının başarısız olması hâlâ hata sayılır. Kullanıcının kurum testinde `/v1/embeddings` model adı olmadan 4096 boyut döndürmüştür; aynı sözleşme analizde kullanılır.

## Rapor

- **Sonuçlar:** orijinal alanlar, en güçlü adayın şema/tablo/kolonu, açıklaması, fiziksel ad kontrolü, iş tanımı uyumu ve uzman kararı.
- **Adaylar:** alternatif kaynaklar, kolon/tablo açıklamaları, arama skoru, gerekçe ve ayrı model yorumu. Skor doğruluk yüzdesi değildir.
- **Kanıtlar:** boyut bazında kaynak alıntıları, eksikler ve çelişkiler.
- **Çalışma bilgisi:** kapsam, tamamlanma, hatalar, sürümler ve zaman.
- **Girdi ...:** tüm kaynak sayfaları ve hücreler. Formüller metindir, çalıştırılmaz.

Bağlantı hatası veya katalog sınırı, veritabanında yok diye gösterilmez. Tamamlanan taramada bile yokluk yalnız erişilebilir seçili kapsamı anlatır. Açıklamalar yetersizse isim benzerliği kesin iş eşleşmesi sayılmaz. Serbest iş tanımı yorumlama sınırlıdır; kolon lineage ve farklı tablolar arası otomatik join doğrulaması yoktur.

## Yerel veri ve geliştirme

Parolalar/API anahtarları işlem belleğinde tutulur; tarayıcı çerezine veya rapora kaydedilmez. Oracle bağlantısı bir saat kullanılmadığında kapanır; Bağlantıyı değiştir oturumu temizler. Excel, rapor ve checkpoint bu bilgisayardaki `runtime/web/` dizinindedir. Gerçek dosyalar, profiller ve kurum sertifikaları Git dışında tutulur. Kurum bağlantı adresleri `src/riskmatch/deployment.py` içindedir; kullanıcı parolası ve servis anahtarı burada bulunmaz. Ayar profili yüklemek gerekmez.

```powershell
.\scripts\test.ps1
```

Sentetik fixture'lar yalnız otomatik test içindir, web uygulamasında sunulmaz. Gerçek Oracle/Qwen erişimi geliştirme bilgisayarından doğrulanmamıştır. Mimari geçmişi: [ARCHITECTURE](docs/ARCHITECTURE.md), [onaylı tanımlar](docs/REVIEWED_DEFINITIONS.md). Eski masaüstü prototipine ait belgeler güncel kullanım talimatı değildir.
