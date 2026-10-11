# İş bilgisayarında kullanım · 0.3.0

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

DSN, sunucu, port, servis adı ve ortam uygulamada hazırdır. Ayar dosyası yüklemek, container veya şema adını elle yazmak gerekmez. Liste, bağlandığınız hesabın ALL_TAB_COLUMNS üzerinden görebildiği şemalardır; veritabanındaki bütün şemaların listesi olduğu varsayılmaz. Aynı analizde en fazla 30 şema seçilir.

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

## 5. Sonuçları incele

Her değişkenin karşısında olası **şema.tablo.kolon** görünür. **Detay**, alternatif adayları, gerçek kolon/tablo açıklamalarını ve eşleşme gerekçelerini açar. **Excel sonucunu indir** tüm girdileri, adayları ve kanıtları ayrı rapora kaydeder.

Fiziksel adın bulunması, iş tanımının aynı olduğunu tek başına kanıtlamaz. Benzer ad farklı anlam taşıyabilir. Arama skoru doğruluk yüzdesi değildir. Eksik metadata, bağlantı hatası veya 10.000 kolon sınırına ulaşılması durumunda tarama eksik gösterilir; “DB'de yok” sonucu verilmez. Sınır aşılırsa seçili şema sayısını azaltın.

İşlem sırasında sayfayı yenilemeyin. Tamamlanan raporlar `runtime/web/<iş-kimliği>/sonuclar.xlsx` altında kalır. Sonuçların aynı tarayıcı oturumuna ait olması gerekir. Müşteri kayıtları sorgulanmaz; yalnız katalog metadata'sı okunur.

## 6. İsteğe bağlı LLM ve embedding

**Bağlantılar → LLM ve embedding ayarlarını göster** bölümünü açın. Verdiğiniz iki kurum temel adresi hazır gelir. Tam çağrı yolu ve gerçek model kimliği yalnız temel adresten doğrulanamaz; servis sözleşmesini aşağıdaki alanlardan tamamlayın. Oracle araması için bu bölüm zorunlu değildir.

Her servis için model ekibinden alınacak bilgiler:

- **Tam çağrı adresi:** gerçek POST inference URL'si.
- **Model kimliği:** servisin beklediği model ID.
- **Model sürümü / dağıtım etiketi:** kullanılan dağıtımın sürümü; model değişince güncelleyin.
- **İstek ve yanıt biçimi:** OpenAI uyumlu mu, özel JSON mu? Embedding vektörü yanıtın neresinde?
- Gerekiyorsa **Bearer API anahtarı** ve **kurum CA PEM dosyası**.

LLM için **OpenAI Chat Completions uyumlu** yalnız servis bunu destekliyorsa seçilir. Özel sözleşmede `{context}` içeren JSON şablonu ve yanıt yolu gerekir. LLM yalnız verilen adaylar üzerinde kaynak alıntılı yorum üretebilir; yeni kolon veya kesin karar kabul edilmez. [Adapter ayrıntıları](ADAPTERS.md).

Embedding sözleşme seçimi yalnız şablonu doldurur; URL'yi değiştirmez:

| Servis biçimi | JSON gövde | Yanıt yolu |
|---|---|---|
| OpenAI Embeddings | `{"model":"{model}","input":"{texts}"}` | `data[*].embedding` |
| TEI /embed | `{"inputs":"{texts}"}` | `$` (kök vektör listesi) |
| Özel JSON | Kurumun belgelediği `{texts}` içeren şablon | Kurumun yanıtındaki noktalı yol |

**Embedding bağlantısını dene** başarılıysa boyut gerçek yanıttan okunur. **LLM bağlantısını dene** çağrıyı ve yanıt yapısını doğrular. Tam model listesi adresi verilirse **Model listesini al** kullanılabilir; OpenAI uyumlu `data[].id` bekler. Model ID elle de girilebilir.

Sonra inceleme sayfasında **LLM + embedding desteği** kutusunu açın. Bu seçenek Excel alan adı/açıklaması ile katalog metadata'sını seçilen kurum servislerine gönderir. Hata halinde dış servise geçilmez. TLS doğrulaması kapatılmaz.


## 7. Bağlantı hatası olursa

Hata mesajı artık hangi aşamanın başarısız olduğunu ve Oracle/driver kodunu gösterir: **oturum açma**, **veritabanı bağlamı okuma**, **erişilebilir şemaları listeleme**, **kolon ve açıklama kataloğu okuma** veya **anahtar kısıtları okuma**.

- `ORA-01017`: kullanıcı/parola kontrolü.
- `ORA-28000` / `ORA-28001`: hesap kilidi / parola süresi.
- `ORA-00942` / `ORA-01031`: ilgili katalog görünümü ve erişim yetkisi kontrolü.
- `DPY-3010` / `DPY-3015`: Oracle sunucusu veya parola doğrulayıcısının Thin sürücüyle uyumluluğu kontrolü.

Ham hata metni parola veya bağlantı bilgisi içerebileceğinden gösterilmez. Destek için ekrandaki **aşama ve hata kodu** yeterlidir; parolanızı paylaşmayın. CON_NAME desteklemeyen eski bağlamlarda DB_NAME kullanılır. Yetki ve ağ hataları bu uyumluluk yoluyla gizlenmez.

Kurum ağına ve gerçek kullanıcı hesabına geliştirme bilgisayarından erişilmedi. Bağlantının kurumda başarılı olduğu, yalnız iş bilgisayarındaki gerçek denemeyle doğrulanabilir.
