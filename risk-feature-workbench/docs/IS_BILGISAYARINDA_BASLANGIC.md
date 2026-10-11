# İş bilgisayarında kullanım · 0.4.0

Python **3.9.7 x64** ve Windows PowerShell **5.1** desteklenir. Uygulama tarayıcıda çalışır. Demo seçeneği yoktur.

## 1. Güncelle ve aç

Önce uygulamanın açık PowerShell penceresinde **Ctrl+C** ile kapatın. `ykb-insource-projects\risk-feature-workbench` klasöründe:

```powershell
git pull --ff-only
.\scripts\start.ps1
```

Önceki web sürümü kuruluysa tekrar paket kurmanız gerekmez. İlk kurulum veya eksik paket hatası için:

```powershell
.\scripts\setup.ps1 -IndexUrl 'https://sdlc.yapikredi.com.tr/artifactory/api/pypi/external-python/simple'
.\scripts\start.ps1
```

Tarayıcı otomatik açılmazsa **http://127.0.0.1:8765** adresine gidin. PowerShell penceresini açık tutun. Eski ekran kalırsa tarayıcıda **Ctrl+F5** yapın. Python bulunamazsa setup komutuna `-Python 'C:\Python39\python.exe'` ekleyip gerçek Python yolunuzu kullanın.

## 2. Bağlan ve şemaları seç

1. Sayfanın üstündeki **EDWLIVE bağlantısı** alanına yalnız **Kullanıcı adı** ve **Parola** girin.
2. **Bağlan ve şemaları getir** düğmesine basın.
3. Açılan **Taranacak şemaları seçin** listesinden bir veya birden fazla şema işaretleyin. Arama kutusuyla listeyi süzebilirsiniz.
4. **Seçimi tamamla** düğmesine basın. Seçili şemalar ekranda görünür.
5. İlk denemede bir veya iki şema seçip **Seçili şemaları test et** düğmesine basın. **Bağlantı test sonuçları** bölümünde seçtiğiniz şemaların katalog erişimini kontrol edin.

DSN, sunucu, port, servis adı ve ortam uygulamada hazırdır. Ayar dosyası yüklemek, container veya şema adını elle yazmak gerekmez. Aynı analizde en fazla 30 şema seçilir.

Açılır liste, `ALL_USERS` üzerinden alınan **şema adaylarıdır**. Listede görünmesi o şemanın kolonlarına erişebildiğinizi göstermez. **Seçili şemaları test et**, yalnız seçilen her şema için `ALL_TAB_COLUMNS` üzerinden en fazla bir metadata satırı okur; müşteri kaydı okumaz. Satır bulunması o şemada en az bir kolonun görülebildiğini doğrular. Satır bulunmaması şemanın boş olmasına veya hesabın kolonları görememesine bağlı olabilir; bütün veritabanında veri bulunmadığı anlamına gelmez. Girişte tüm kolon kataloğunda `DISTINCT OWNER` taraması yapılmaz.

Parola sunucunun işlem belleğinde oturuma özel tutulur; tarayıcı çerezine, dosyaya veya rapora yazılmaz. **Bağlantıyı değiştir** oturumdaki parolayı siler. Bir saat kullanılmayan oturum sonraki istekte kapanır. Uygulamayı kapatmak bellekteki bağlantı bilgilerini temizler. Sayfa yenilenirse bağlantı durumunuz okunur; şemaları yeniden seçin.

## 3. Ekrandan birden fazla değişken ekle

**Ekrandan değişken ekle** seçeneğinde:

1. İlk değişkenin **Alan / kolon adı** ve **Açıklama** bilgilerini girin.
2. **+ Değişken ekle** ile istediğiniz kadar yeni satır açın (en fazla 100).
3. İstemediğiniz satırı **Sil** ile kaldırın. En az bir satır kalır.
4. **Değişkenleri ara** ile tüm satırları seçili şemalarda birlikte sorgulayın.

Excel zorunlu değildir. Her satırın adı ve açıklaması zorunludur. Aynı analizde eklediğiniz tüm değişkenler ayrı ayrı sonuçlanır. Ad/açıklama bilgileri ile ayrı bir Excel raporu da üretilir.

## 4. Excel ile toplu ara

1. **Excel ile toplu ara** seçeneğine geçin.
2. `.xlsx` dosyanızı seçin. Dosya otomatik okunur; ikinci bir yükleme düğmesi yoktur.
3. Standart başlıklar otomatik eşleştirilir:
   **Kategori | Alan Adı | Alan tipi | Açıklama | Halihazırda bildiriliyor mu**.
4. Gerekirse **Sayfa seçimini ve sütun eşleştirmesini düzenle** bölümünü açın. Farklı başlıklar için Alan adı ve Açıklama karşılıklarını seçin; incelenecek sayfaları işaretleyin.
5. **Excel'deki değişkenleri ara** düğmesine basın.

Alan Adı ve Açıklama sütunları zorunludur; diğer alanlar isteğe bağlıdır. Başlıklar ilk 30 satırda aranır. Başlıkları tanınmayan sayfalar otomatik analize alınmaz; eşleştirme bölümü açılır ve seçim bekler. Dosya en fazla 12 MB, sayfa başına 20.000 satır/300 sütun ve analiz başına 2.000 değişken olabilir. Özgün dosyanın üzerine yazılmaz. Sekmeler arasında geçiş yaptığınızda elle girdikleriniz korunur.

Dosyanın yüklenmesi ve satır sayısının görünmesi, Excel'in okunabildiğini gösterir. Ardından ayrı bir işlem olarak **Oracle kataloğu taranır**. Bu sırada ekranda şema, tarama aşaması ve okunan kolon sayısı görünür; henüz değişkenlerin tamamlandığı gösterilmez.

Katalogdan hiç kayıt alınamazsa **“Katalog okunamadı; arama başlatılamadı”** mesajı gelir. Örneğin 273 satırlı Excel için **273 değişken hazır, sonuç üretilmedi** denir; incelenmeyen değişkenler için sonuç satırı veya indirilecek sonuç dosyası oluşturulmaz. Excel ve sütun eşleştirmeniz aynı sayfa oturumunda korunur. **Şema seçimine ve girdilere dön** düğmesiyle yalnız bir şema seçerek tekrar başlatın; dosyayı tekrar yüklemeniz gerekmez.

## 5. Sonuçları incele

273 satırlık Excel ve büyük şema için **Hızlı aday araması** seçili kalsın. Bu mod önce bütün okunmuş katalogda ad/açıklama eşleşmelerini bulur, ardından değişken başına en fazla 24 aday için embedding kullanır. İş başına 256 farklı katalog metni sınırından sonra ad/açıklama araması sürer; sınır ve modelin kullanılmadığı sonuçların detayında belirtilir. Kelime olarak benzemeyen adaylar kaçabilir. **Tüm katalogda anlamsal arama** bu kısıtı kaldırır, fakat ilk hazırlığı ve aramaları daha uzun sürebilir. Her iki mod da Oracle'dan gerçekten okunmuş kapsamla sınırlıdır.

İlk denemede **Her değişken için LLM yorumu ekle** kapalı kalsın. LLM yorumları kaynak aramasından ayrıdır; 273 ayrı model çağrısı ekleyebilir. Embedding desteğini açmak için yalnız embedding testinin başarılı olması yeterlidir; LLM yorumu da seçilirse LLM testi gerekir.

Sonuçların tamamını beklemeden, biten değişkenleri tabloda görebilirsiniz. Hazırlık sırasında işlenen metin sayısı, değişken adı, geçen süre ve son güncelleme görünür. **Durdur** mevcut servis çağrısı tamamlanınca yeni işlemleri durdurur; görünen kısmi sonuçları ve girdilerinizi korur. İptal edilen analiz nihai rapor gibi indirilemez; tekrar aramada yeni iş başlar. Satır sonuçları erken görünse de bağımlılık değerlendirmesi ve nihai Excel tamamlanana kadar ekran **Kısmi sonuçlar** der.

Her değişkenin karşısında olası **şema.tablo.kolon** görünür. **Detay**, alternatif adayları, gerçek kolon/tablo açıklamalarını ve eşleşme gerekçelerini açar. **Excel sonucunu indir** tüm girdileri, adayları ve kanıtları ayrı rapora kaydeder.

Fiziksel adın bulunması, iş tanımının aynı olduğunu tek başına kanıtlamaz. Benzer ad farklı anlam taşıyabilir. Arama skoru doğruluk yüzdesi değildir. Eksik metadata, bağlantı hatası veya 10.000 kolon sınırına ulaşılması durumunda tarama eksik gösterilir; “DB'de yok” sonucu verilmez. Sınır aşılırsa seçili şema sayısını azaltın.

Katalog taraması artık şema bazında, en fazla 25 tablo/görünüm içeren gruplarla yapılır. Kolonlar, açıklamalar ve anahtar bilgileri ayrı sorgulanır; büyük kolon/açıklama görünümleri tek sorguda birleştirilmez. Kayıtlar 200'lük alım parçalarıyla okunur. Varsayılan çağrı sınırı 30 saniye, toplam katalog tarama bütçesi 300 saniyedir. Daha önce okunmuş kayıtlar sonraki hatada korunur; kısmi katalogla üretilen sonuçlar eksik kapsam uyarısı taşır.

İşlem sırasında sayfayı yenilemeyin. Tamamlanan raporlar `runtime/web/<iş-kimliği>/sonuclar.xlsx` altında kalır. Sonuçların aynı tarayıcı oturumuna ait olması gerekir. Müşteri kayıtları sorgulanmaz; yalnız katalog metadata'sı okunur.

## 6. İsteğe bağlı LLM ve embedding

**Bağlantılar** sayfasını açın. Kurumun LLM ve embedding temel adresleri hazır gelir. Oracle araması için model desteği zorunlu değildir.

1. Servis anahtar istiyorsa ilgili **API anahtarı** alanını doldurun. İstemiyorsa boş bırakın.
2. LLM **Model kimliği** alanına `qwen38-27b` yazın. Embedding **Model kimliği** alanını boş bırakın; kurumda doğrulanan servis model adı istemiyor.
3. **LLM bağlantısını test et** düğmesine basın. Ardından **Embedding bağlantısını test et** düğmesine basın.
4. Birden fazla model bulunursa ilgili **Model kimliği** listesinden doğru modeli seçip aynı testi yeniden çalıştırın.
5. Embedding testi başarılıysa **Değişken inceleme** sayfasında embedding desteğini açın. LLM yorumlarını ayrıca istiyorsanız **Her değişken için LLM yorumu ekle** kutusunu seçin; bu durumda LLM testi de başarılı olmalıdır.

Test, standart servislerin model listesi veya servis bilgisi uçlarını kontrol eder ve sabit, kısa bir metinle gerçek çağrı yapar. Excel ve katalog bilgisi bu bağlantı testine gönderilmez. Servisin OpenAI/TEI uyumu yalnız adres adına bakılarak kabul edilmez. Başarılı test **tam çağrı adresini**, **istek/yanıt biçimini** ve embedding için gerçek **vektör boyutunu** doldurur. Model listesi birden fazla kimlik döndürürse otomatik rastgele model seçilmez. Gerçek dağıtım sürümü servisten okunamıyorsa bu durum etiketlenir; doğrulanmış sürüm bilgisi sayılmaz.

Kurumdaki 11.10.2026 tanılama çıktısında embedding `/v1/embeddings` yolu model adı olmadan **4096 boyutlu vektör** üretti. Bu nedenle embedding model listesi ve `/info` adımlarının `404` olması testi durdurmaz. Son gerçek çağrının başarılı olması beklenir. Model kimliği/sürümü boş kalabilir; ekranda **sunucunun varsayılan embedding modeli** kullanıldığı belirtilir. Boyut ve yanıt yolu yine gerçek yanıttan okunur. Aynı doğrulanmış sözleşme analizde de kullanılır. Model kimliği bilinmediği için kalıcı vektör önbelleği ve eski analiz sonucundan devam kapalıdır; yeni analizde vektörler yeniden üretilir.

Başarılı gerçek çağrıya ulaşılırsa isteğe bağlı model bilgisi yollarındaki `GET 404/405` satırları **Desteklenmiyor** olarak gösterilir. Bu, embedding üretiminin başarısız olduğu anlamına gelmez. Gerçek üretim çağrısı (`POST`) başarısızsa hata kırmızı kalır; yalnız bilgi yollarının isteğe bağlı olması bu hatayı gizlemez. Kullanıcının paylaştığı sonraki ekranlarda da embedding gerçek çağrısının başarılı olduğu görüldü; Oracle katalog taraması ayrıca değerlendirilir.

**Gelişmiş / özel servis ayarları** bölümünü yalnız standart test servis biçimini tanımazsa veya kurumunuz özel bir sözleşme kullanıyorsa açın. Buradan model ekibinin verdiği tam çağrı adresi, sürüm, JSON şablonu ve yanıt yolunu girebilirsiniz. Kurum CA sertifikası gerekiyorsa PEM dosyasını da bu bölümden seçilen servisin alanına yazın. TLS doğrulaması kapatılmaz. [Adapter ayrıntıları](ADAPTERS.md).

Model desteği açık analiz, değişken adı/açıklaması ile katalog metadata'sını seçilen kurum servislerine gönderir. Hata halinde dış servise geçilmez. LLM yorumu bir adayın iş tanımına kesin uyduğunu tek başına kanıtlamaz.


## 7. Bağlantı hatası olursa

**Bağlantı test sonuçları** bölümünü açın. Hangi adımın geçtiğini, hangisinin başarısız olduğunu, süresini ve Oracle/driver veya HTTP kodunu görebilirsiniz. **Tanılama raporunu indir** ile yerel JSON raporunu alın. Rapor parola, API anahtarı ve ham sunucu yanıtı içermez.

- `DPY-4024`: Oracle çağrısı zaman sınırını aştı. Hata şema listeleme, tek kolon erişim testi veya tam katalog taraması sırasında oluşabilir; rapordaki aşamaya bakın. Excel 273 satır olarak okunmuşken katalog aşamasında oluşan hata Excel'in bozuk olduğunu göstermez. Tek şema seçerek tekrar deneyin; sürerse **Analiz için katalog taraması** raporundaki şema, aşama ve süreyle inceleyin.
- `ORA-01017`: kullanıcı/parola kontrolü.
- `ORA-28000` / `ORA-28001`: hesap kilidi / parola süresi.
- `ORA-00942` / `ORA-01031`: ilgili katalog görünümü ve erişim yetkisi kontrolü.
- `DPY-3010` / `DPY-3015`: Oracle sunucusu veya parola doğrulayıcısının Thin sürücüyle uyumluluğu kontrolü.
- Model testinde `401` / `403`: servis anahtarı veya erişim izni kontrolü.
- Model testinde `404/405`: isteğe bağlı bilgi yolu ve ardından başarılı gerçek çağrı varsa **Desteklenmiyor** normaldir. Gerçek `POST` çağrısında hata varsa servis ekibinin çağrı sözleşmesini kontrol edin.
- Sertifika hatası: kurumun sağladığı CA PEM dosyasını ilgili servisin gelişmiş ayarına girin.

Önceki sürümde görülen **“LLM/Embedding tam çağrı adresi gerekli”**, servis çağrısından önceki eksik form doğrulamasıydı. Yeni standart test temel adresle başlayabilir; bu mesaj tek başına servislerin kapalı olduğunu göstermiyordu.

Sıralı gerçek ortam denemesi için **[Kurumda bağlantı test senaryosu](BAGLANTI_TEST_SENARYOSU.md)** belgesini izleyin. Ham hata metni parola veya bağlantı bilgisi içerebileceğinden gösterilmez. Destek için aşama, hata kodu ve tanılama raporunu kullanın; parolanızı paylaşmayın. CON_NAME desteklemeyen eski bağlamlarda DB_NAME kullanılır. Yetki ve ağ hataları bu uyumluluk yoluyla gizlenmez.

Kurum ağına ve gerçek kullanıcı hesabına geliştirme bilgisayarından erişilmedi. Kullanıcının çıktıları küçük LLM/embedding çağrılarının kurumda çalıştığını doğruladı. Tam Oracle katalog taramasının ve Excel analizinin kurumda tamamlanması ayrıca doğrulanmalıdır; sorguların küçültülmesi gerçek ortamda her zaman aşımının giderildiği garantisi değildir.
