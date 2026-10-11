# Risk Feature Workbench

Excel'deki değişkenleri gerçek Oracle kataloğunda arayan **yerel web uygulaması**. Her satır için olası **şema, tablo, kolon, veritabanı açıklaması, alternatif adaylar ve gerekçeler** gösterilir. Aynı fiziksel adın varlığı ile iş tanımına uyum ayrı değerlendirilir. Sonuçlar Excel olarak indirilebilir.

Uygulamada demo seçeneği yoktur. Graphify kurulmaz. Müşteri kayıtları sorgulanmaz; yalnız erişilebilir katalog metadata'sı okunur. Python **3.9.7 x64** ve Windows PowerShell **5.1** desteklenir. Node.js, masaüstü GUI veya Tcl/Tk gerekmez.

## Başlatma

Proje klasöründe:

```powershell
git pull --ff-only
# Kurumunuzdaki gerçek paket aynası adresini kullanın:
$env:PIP_INDEX_URL = 'https://KURUM-ARTIFACTORY-ADRESI/api/pypi/REPO/simple'
.\scripts\setup.ps1
.\scripts\start.ps1
```

Tarayıcıda **http://127.0.0.1:8765** açılır. PowerShell penceresi açık kalmalıdır; kapatmak için Ctrl+C. Uygulama yalnız bu bilgisayardan erişilir, internete yayımlanmaz.

**[Adım adım iş bilgisayarı rehberi](docs/IS_BILGISAYARINDA_BASLANGIC.md)** · [Model/Oracle sözleşmeleri](docs/ADAPTERS.md) · [Testler](docs/TEST_RESULTS.md)

## Kullanım

1. **Bağlantılar** ekranında ayar profilinizi içe aktarın veya Oracle DSN, kullanıcı adı ve parolasını girin.
2. **Oracle bağlantısını dene** ile kaynak/container ve erişilebilir şemaları alın. Taranacak şemaları seçin.
3. **Değişken inceleme** ekranında kendi `.xlsx` dosyanızı seçip **Dosyayı oku** düğmesine basın. Sayfaları ve sütunları kontrol edin.
4. **İncelemeyi başlat** ile gerçek katalogda arayın. **Detay** bütün adayların kaynak yolunu, açıklamasını ve kanıtlarını açar.
5. **Excel sonucunu indir** ile kaynak dosyanızı koruyan ayrı rapor alın.

Standart başlıklar: `Kategori | Alan Adı | Alan tipi | Açıklama | Halihazırda bildiriliyor mu`. Yalnız **Alan Adı** ve **Açıklama** sütunları zorunludur; başka başlıklar ekrandan eşlenebilir. Görüş/hazırlama süresi varsa korunur, zorunlu değildir. Bildirim bilgisi kullanıcı beyanıdır, veritabanı kanıtı sayılmaz.

**LLM + embedding desteği** isteğe bağlıdır. Kapalıyken de gerçek Oracle kataloğunda ad ve açıklama araması yapılır. Açıldığında iki servisin tam çağrı adresi, model kimliği, sürümü ve istek/yanıt sözleşmesi gerekir. Embedding boyutu bağlantı testinden okunur. Temel URL'den çağrı yolu veya model kimliği tahmin edilmez. LLM yorumu doğrulanmış eşleşme kararının yerine geçmez.

## Rapor

- **Sonuçlar:** orijinal alanlar, en güçlü adayın şema/tablo/kolonu, açıklaması, fiziksel ad kontrolü, iş tanımı uyumu ve uzman kararı.
- **Adaylar:** alternatif kaynaklar, kolon/tablo açıklamaları, arama skoru, gerekçe ve ayrı model yorumu. Skor doğruluk yüzdesi değildir.
- **Kanıtlar:** boyut bazında kaynak alıntıları, eksikler ve çelişkiler.
- **Çalışma bilgisi:** kapsam, tamamlanma, hatalar, sürümler ve zaman.
- **Girdi ...:** tüm kaynak sayfaları ve hücreler. Formüller metindir, çalıştırılmaz.

Bağlantı hatası veya katalog sınırı, veritabanında yok diye gösterilmez. Tamamlanan taramada bile yokluk yalnız erişilebilir seçili kapsamı anlatır. Açıklamalar yetersizse isim benzerliği kesin iş eşleşmesi sayılmaz. Serbest iş tanımı yorumlama sınırlıdır; kolon lineage ve farklı tablolar arası otomatik join doğrulaması yoktur.

## Yerel veri ve geliştirme

Parolalar/API anahtarları işlem belleğinde tutulur; indirilen `.local.json` profiline veya rapora kaydedilmez. Excel, rapor ve checkpoint bu bilgisayardaki `runtime/web/` dizinindedir. Gerçek dosyalar, profiller ve kurum sertifikaları Git dışında tutulur. Profil host/DSN içerebilir; kurum içinde saklayın.

```powershell
.\scripts\test.ps1
```

Sentetik fixture'lar yalnız otomatik test içindir, web uygulamasında sunulmaz. Gerçek Oracle/Qwen erişimi geliştirme bilgisayarından doğrulanmamıştır. Mimari geçmişi: [ARCHITECTURE](docs/ARCHITECTURE.md), [onaylı tanımlar](docs/REVIEWED_DEFINITIONS.md). Eski masaüstü prototipine ait belgeler güncel kullanım talimatı değildir.
