# Risk Feature Workbench · 0.5.0

Ekrandan eklenen değişkenleri veya Excel sözlüğünü gerçek Oracle kataloğunda arayan **yerel web uygulaması**. Her satır için en çok 3 aday **şema, tablo, kolon, veritabanı açıklaması, farklar ve kanıtla** gösterilir. Aynı fiziksel adın varlığı (ad eşleşmesi) ile iş tanımına uyum (iş anlamı) ayrı değerlendirilir. Sonuçlar Excel olarak indirilir.

Uygulamada demo seçeneği yoktur. Müşteri kayıtları sorgulanmaz; yalnız erişilebilir katalog metadata'sı okunur. Python **3.9.7 x64** ve Windows PowerShell **5.1** desteklenir. Node.js, masaüstü GUI veya Tcl/Tk gerekmez.

## Başlatma

Açık uygulamayı PowerShell penceresinde **Ctrl+C** ile kapatın. Proje klasöründe:

```powershell
git pull --ff-only
.\scripts\start.ps1
```

0.5 yeni paket gerektirmez; kurulu ortam yeterlidir. İlk kurulumda `start.ps1` öncesinde (paketler kurumun Artifactory PyPI aynasından iner):

```powershell
.\scripts\setup.ps1 -IndexUrl '<kurum-artifactory-pypi-simple-adresi>'
```

Tarayıcıda **http://127.0.0.1:8765** açılır. PowerShell penceresi açık kalmalıdır. Uygulama yalnız bu bilgisayardan erişilir, internete yayımlanmaz.

## Kullanım (özet)

Bağlan (yalnız kullanıcı adı ve parola; DM_CAD → DM → EDW otomatik taranır) → embedding testi ve indeks kendiliğinden başlar → Excel yükle → **İlk 1** → **Sıradaki 10 aranmamış** → **Tümü** → Excel sonucunu indir. Model açıkken eşleştirme indeks %100 olunca başlar. Ayrıntı ve sorun giderme: **[İş bilgisayarı rehberi](docs/IS_BILGISAYARINDA_BASLANGIC.md)**.

Standart Excel başlıkları: `Kategori | Alan Adı | Alan tipi | Açıklama | Halihazırda bildiriliyor mu`. Yalnız **Alan Adı** ve **Açıklama** zorunludur; farklı başlıklar ekrandan eşlenir. Bildirim bilgisi kullanıcı beyanıdır, veritabanı kanıtı sayılmaz.

## 0.5.0'da neler değişti

- **Arama:** birebir ad, sözlükle normalize ad, alan ağırlıklı BM25 ve anlamsal (embedding) kanallar ≤30'luk havuz üretir; gösterim kapısı en çok 3 aday bırakır (0, 1 veya 2 de olabilir). Ad eşleşmesi ve iş anlamı (güçlü / olası / zayıf / fark var / açıklama yok) ayrıdır; farklar ve kanıt yazılır, reddedilen yakın adaylar ayrı listelenir. Skor yüzde değildir.
- **Satır durumu:** aday bulundu, yalnız zayıf aday, seçili kapsamda aday yok, aday yok ama kapsam eksik ("DB'de yok" denmez), incelenemedi.
- **Hız ve kaynak:** katalog önbelleği (imza kontrollü, 8 saat); katalog vektörleri bir kez üretilir ve model parmak iziyle saklanır; tek uçuşlu embedding kapısı; arka plan indeksleyici (başlat/durdur/ilerleme/kalan süre). Model iş sırasında değişirse etkilenen satırlar uyarılır.
- **Web:** Excel bir kez yüklenir; İlk 1/5/10, Sıradaki 10 aranmamış, Tümü; grup boyutu 10/25/50; sonuçlar akışla gelir; **Durdur** kısmi sonuçları ve kısmi Excel raporunu korur; sayfa yenilenince iş yeniden bağlanır; "Tamamlandı" yalnız Excel yazıldıktan sonra görünür, rapor yazılamazsa sonuçlar ekranda kalır ve yeniden denenir.
- **LLM** (isteğe bağlı) yalnız gösterilen aday var ama hiçbiri "güçlü" değilse çağrılır; kanıt yerine geçmez.
- **Yapılandırma:** yeni `retrieval` anahtarları (`pool_size`, `lexical_k`, `semantic_k`, `shortlist_embed`, `background_size`, `gate`) [`config/real.example.json`](config/real.example.json) içindedir (yalnız CLI şablonu; web varsayılanları kodda). Eski `search_mode`, `candidate_limit`, `embedding_budget`, `limit`, `expanded_limit` zararsızdır, yok sayılır.
- Kullanım dışı kalan: 0.4'ün "hızlı aday araması / tüm katalogda anlamsal arama" seçimi.

## Rapor (Excel)

- **Sonuçlar:** orijinal alanlar, satır durumu, kapsam, her aday için kaynak, kolon açıklaması, ad eşleşmesi, iş anlamı, farklar ve kanıt.
- **Adaylar:** gösterilen ve reddedilen adaylar, gerekçe, ayrı model yorumu. Skor doğruluk yüzdesi değildir.
- **Kanıtlar:** boyut bazında kaynak alıntıları, eksikler ve çelişkiler.
- **Çalışma bilgisi:** kapsam, tamamlanma, hatalar, uyarılar, sürümler, zaman.
- **Girdi ...:** tüm kaynak sayfaları ve hücreler. Formüller metindir, çalıştırılmaz.

Bağlantı hatası veya eksik katalog, veritabanında yok diye gösterilmez. Tamamlanan taramada bile yokluk yalnız erişilebilir seçili kapsamı anlatır. Kolon lineage'ı ve farklı tablolar arası otomatik join doğrulaması yoktur.

## Belgeler

- [İş bilgisayarı rehberi](docs/IS_BILGISAYARINDA_BASLANGIC.md) · [Kurumda bağlantı test senaryosu](docs/BAGLANTI_TEST_SENARYOSU.md)
- [Mimari (0.5 akışı, önbellek, hata yolları, eşzamanlılık)](docs/ARCHITECTURE.md) · [Sözleşme (sonuç alanları, Excel, API)](docs/CONTRACT.md)
- [Model/Oracle sözleşmeleri](docs/ADAPTERS.md) · [Testler](docs/TEST_RESULTS.md) · [v0.5 geliştirme planı](docs/GELISTIRME_PLANI_v05.md)

## Yerel veri ve geliştirme

Parolalar/API anahtarları işlem belleğinde tutulur; çereze veya rapora kaydedilmez. Oracle bağlantısı bir saat kullanılmadığında kapanır. Excel, rapor, katalog önbelleği ve vektör deposu bu bilgisayardaki `runtime/` altındadır. Gerçek dosyalar, profiller ve kurum sertifikaları Git dışında tutulur. Kurum bağlantı adresleri `src/riskmatch/deployment.py` içindedir; parola ve servis anahtarı burada bulunmaz. Adres değiştirme ve pull sonrası adımlar: [docs/IS_PC_KURULUM_AYARLARI.md](docs/IS_PC_KURULUM_AYARLARI.md). Web uygulaması ve ölçüm betiği `config/` klasörünü kullanmaz; `config/*.example.json` yalnız eski komut satırı (CLI) yolunun şablonudur.

```powershell
.\scripts\test.ps1
powershell -ExecutionPolicy Bypass -File scripts\is_pc_dogrulama.ps1 -Excel C:\yol\etiketli.xlsx   # iş bilgisayarında ölçüm (web ile aynı ayarlar)
```

Sentetik fixture'lar yalnız otomatik test içindir, web uygulamasında sunulmaz. Gerçek Oracle/embedding erişimi, kurum katalog boyutu ve gerçek doğruluk geliştirme bilgisayarından doğrulanmamıştır; iş bilgisayarında ölçülür ([rehber, bölüm 7](docs/IS_BILGISAYARINDA_BASLANGIC.md)). Eski masaüstü prototipine ait belgeler güncel kullanım talimatı değildir.
