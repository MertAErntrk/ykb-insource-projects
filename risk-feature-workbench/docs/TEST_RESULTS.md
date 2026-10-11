# v0.5 ölçümleri

> **Sentetik veri; gerçek doğruluk değil.** Gerçek doğruluk iş bilgisayarında etiketli Excel ile ölçülür (`scripts/is_pc_dogrulama.ps1`).

Düzenek: `tests/benchmark_fixture.py` (220 dolgu tablo, **5.803 kolon**, 273 talep satırı: 51 elle yazılmış + 222 deterministik; 54 satır YOK = %19,8; 219 pozitif), sahte Oracle ve sahte embedding servisi. `hash` = n-gram vektörü, `e5` = yalnız geliştirme ortamında multilingual-e5-large (**önbellekli**; e5 süreleri model hesabını içermez). Hit@3, P@3 ve YOK'ta öneri varsayılan kapıyla havuz üzerinden hesaplandı. Kod: 419cccf + çalışma ağacı (P12, P17 ve `semantics.py` süre birimi düzeltmesi sonrası); tek koşu, tekrar yok.

```
PYTHONPATH="src;tests" python scripts/evaluate.py --fixture --embedder hash|e5 --filler-tables 220 --fixture-rows 273 --group-size 25 --rows N --out DIR
... --variant nolex|nolex_full|garbled   # zor varyantlar
... --consistency-sizes 25,10,1          # tutarlılık matrisi (son boyut ayrıca ters sırada)
```

| Embedder | Satır | Hit@3 | P@3 | YOK'ta öneri | İlk sonuç (s) | Toplam (s) | row_status |
|---|---|---|---|---|---|---|---|
| hash | 1 | 1,00 | 1,00 | - | 0,12 | 0,41 | found 1 |
| hash | 5 | 1,00 | 1,00 | - | 0,12 | 0,50 | found 5 |
| hash | 10 | 1,00 | 0,967 | - | 0,12 | 0,58 | found 10 |
| hash | 273 | 0,977 | 0,974 | 0/54 | 0,11 | 9,41 | found 167, weak_only 47, none_in_scope 59 |
| e5 | 1 | 1,00 | 1,00 | - | 0,25 | 0,50 | found 1 |
| e5 | 5 | 1,00 | 0,933 | - | 0,25 | 0,62 | found 5 |
| e5 | 10 | 1,00 | 0,967 | - | 0,20 | 0,73 | found 10 |
| e5 | 273 | 0,913 | 0,950 | 0/54 | 0,27 | 12,53 | found 167, weak_only 33, none_in_scope 73 |

Embedding çağrısı: indeks **3.885** (katalog, bir kez; indeksleme havuz aşamasında yapılır), satır başına **0,86** sorgu çağrısı (273 satırda 234; tekrarlı metinler önbellekten; 5 satırda 1,0). Uçtan uca aşamada ek çağrı 0 (vektörler önbellekte). hash ve e5 için sayılar aynıdır. `--rows` yalnız ölçülen satırı kısar; katalog ve indeks aynıdır.

**Tutarlılık** (hash, 273 satır, referans grup 25 ileri): grup 10 → 1,000 (0 farklı satır), grup 1 → 1,000 (0), grup 1 ters sıra → 1,000 (0). Toplam süre: grup 10 15,1 sn, grup 1 105,8 sn (ters 106,0 sn); referans 8,9 sn.

**Zor varyantlar** (273 satır):

| Varyant | Embedder | Hit@3 | P@3 | Havuz recall | YOK'ta öneri |
|---|---|---|---|---|---|
| normal | hash | 0,977 | 0,974 | 1,00 | 0/54 |
| eş anlamlı grup ve açılım kapalı (`nolex`) | hash | 0,918 | 0,932 | 0,977 | 2/54 |
| sözlük tamamen boş (`nolex_full`) | hash | 0,904 | 0,905 | 0,968 | 22/54 |
| talep adı bozuk (`garbled`) | hash | 0,795 | 0,966 | 0,950 | 1/54 |
| normal | e5 | 0,913 | 0,950 | 1,00 | 0/54 |
| `nolex` | e5 | 0,895 | 0,893 | 0,991 | 1/54 |
| `nolex_full` | e5 | 0,776 | 0,855 | 0,982 | 20/54 |
| `garbled` | e5 | 0,699 | 0,920 | 0,959 | 1/54 |

`nolex` yalnız eş anlamlı gruplarını ve açılımlarını kapatır; yapısal ve genel kelime listeleri kalır. `nolex_full` sözlüğün tamamını boşaltır (eski "sözlük kapalı" koşusu; indeks ve semantik çözümleme boş `Lexicon` ile). `garbled`: ad bazlı crc32 ile ünlü düşürme / 3 harfe kısaltma / alt çizgi kaldırma.

**51 satırlık el yazımı küme** (`--fixture`, `--consistency`; 41 pozitif, 10 YOK):

| Embedder | Hit@3 | P@3 | YOK'ta öneri |
|---|---|---|---|
| hash | 0,976 | 0,992 | 0/10 |
| e5 | 0,951 | 0,983 | 0/10 |
| none (yalnız sözcüksel) | 0,854 | 0,990 | 0/10 |

Dikkat çekenler: 273 satırda hash Hit@3 0,977, e5 0,913; ikisinde de YOK satırlarında öneri 0/54. Grup boyutu ve sıra değişince sonuçlar özdeş (3 karşılaştırmada 0 farklı satır). Yalnız `nolex` YOK'ta öneriyi 0'dan 1-2'ye, Hit@3'ü hash'te 0,06, e5'te 0,02 düşürüyor; `nolex_full` YOK'ta öneriyi 22/54 (hash) ve 20/54'e (e5) çıkarıyor, Hit@3 hash'te 0,07, e5'te 0,14 düşüyor. Ad bozulunca Hit@3 hash'te 0,18, e5'te 0,21 düşüyor; YOK'ta öneri 1/54 kalıyor. Havuz recall her varyantta 0,95 ve üstü. Tek koşu, tekrar yok.

# Web sürümü doğrulaması — 0.4.0

2026-10-11 tarihinde gerçek Python 3.9.7 x64 ve Windows PowerShell 5.1 üzerinde doğrulandı. Kurum Oracle/Qwen servislerine bağlanılmadı; otomatik ve tarayıcı testlerinde Oracle bağlantısı ile model HTTP taşıması kontrollü olarak taklit edildi.

## 0.4.0 büyük katalog doğrulaması

**228 Python testi**, gerçek Python 3.9.7 x64 ve Windows PowerShell 5.1 ile **98,507 saniyede geçti**. Güncel çıktı: [test-results.txt](test-results.txt). Son metin düzeltmesinden sonra yedi pipeline testi, JavaScript sözdizimi ve 17 arayüz regresyon senaryosu tekrar geçti. Yeni çalışma bağımlılığı yoktur.

- Gerçek Excel okuma/yazma ve gerçek embedding adapter sözleşmesiyle **10.000 kolon, 273 talep ve 4096 boyut** sınandı. Son tam test koşusunda ilk satır **0,531 sn**, tüm çalışma **49,718 sn** sürdü. Bunlar kontrollü yerel ölçümlerdir; Oracle/HTTP taşıması taklittir, kurum performansı veya eşleşme doğruluğu iddiası değildir. Tarayıcı kontrolü aynı sırada çalıştığından süre donanım/yüke de bağlıdır.
- Katalog için **256**, sorgular için **273** embedding metni işlendi; **0 LLM çağrısı** yapıldı. Son katalog kayıtlarındaki tam fiziksel adlar da bulundu. Aynı satırın genişletilmiş aday incelemesinde sorgu vektörü tekrar çağrılmadı. Fiziksel eşleşme, eksik katalog, şema sınırı, kısa listede eşleşme olmaması ve bütçe sınırı test edildi.
- Geçici embedding hatası sonraki model çağrılarını durdururken kaynak adaylarını korur. İsteğe bağlı LLM hatası eşleşme kanıtını silmez. Kimliği sabit servis toparlandığında eksik model değerlendirmesi checkpoint'ten kalıcı biçimde atlanmaz; tam başarılı checkpoint hâlâ yeniden kullanılabilir. Model kimliği bilinmiyorsa analizler arası vektör veya sonuç kullanımı yoktur.
- 500 adapter POST çağrısında TLS context/opener yalnız bir kez kuruldu. CA değişikliği, geçersiz CA, ayrı adapter/anahtar izolasyonu, izinli host, boyut, retry ve yanıt sınırları korundu. Bu test socket havuzu veya gerçek servis hızlanması kanıtı değildir.
- Tarayıcıda yalnız embedding testi yapılarak hızlı arama başlatıldı; LLM testi gerekmedi. Kısmi satırlar iş sürerken göründü. **Durdur** sonrasında 53 sonuç korundu, rapor indirme açılmadı. Aynı 273 satırlık dosya yeniden yüklenmeden tekrar çalıştırıldı; **273/273** sonuç ve Excel indirme doğrulandı. İndirilen dosyada 273 sonuç, 6.552 aday ve özgün 273 girdi satırı vardı. Tarayıcı hata/uyarı kaydı boştu.

Hızlı arama bütün kataloğun semantik eşdeğeri değildir: ad/açıklama ön elemesi kelime olarak benzemeyen adayları kaçırabilir; 256 kolon bütçesinden sonra ad/metin araması sürer. Uyarılar satır detayında ve çalışma özetinde gösterilir. Tam anlamsal arama ayrıca seçilebilir. Oracle katalog hazırlığı ve 10.000 kolon sınırı devam eder. Gerçek DM_CAD erişim süresi ve kullanıcı Excel'i bu bilgisayarda doğrulanmamıştır.

## Önceki 0.3.3 doğrulaması

**191 Python testi**, önceki 0.3.3 sürümünde gerçek Python 3.9.7 x64 ve Windows PowerShell 5.1 ile **28,448 saniyede geçti**. JavaScript sözdizimi ve 17 arayüz regresyon senaryosu da geçti. Yeni çalışma bağımlılığı yoktur.

- İsteğe bağlı model listesi ve TEI bilgi yollarının 404/405 yanıtı, yalnız gerçek model çağrısı doğrulanmışsa nötr **Desteklenmiyor** olarak gösterilir. Hata kodu ve süre raporda korunur. Başarısız gerçek çağrı, yetki/TLS sorunu ve bozuk vektör başarıya çevrilmez.
- Oracle taraması seçili şemaları ve en fazla 25 nesnelik grupları ayrı sorgular. Kolon, kolon açıklaması, tablo açıklaması ve anahtar bilgileri ayrı okunur. Bağlı parametreler, kayıt/süre sınırı, eksik görünürlük, kısmi kayıtların korunması, zaman aşımından sonra yeni bağlantı ve veritabanı/bağlam tutarlılığı test edilir.
- Aynı standart beş başlıkla oluşturulan **273 satırlık Excel** web üzerinden yüklenir. Başarılı senaryoda gerçek uygulama pipeline'ı, embedding/LLM adapter'ları, 273 sonuç, Excel indirme ve özgün hücrelerin korunması doğrulanır. Yalnız Oracle ve model HTTP taşıması taklit edilir; bu kullanıcının kurum Excel'i değildir.
- Üç şemada DPY-4024 üretilen senaryoda Excel okunur, ancak katalog alınamazsa iş katalog aşamasında durur: 0/273 işlenmiş satır, model çağrısı yok, sonuç satırı/raporu yok. Güvenli şema ve aşama tanılaması korunur; parola ham hata metnine veya yanıta sızmaz.
- Tarayıcıda 273 satırlık Excel ile katalog zaman aşımı ekranı görüldü. Şema seçimine dönüldüğünde dosya ve sütun eşlemesi korundu. Bağlantı düzeltildikten sonra aynı dosya yeniden yüklenmeden LLM+embedding açık analiz 273 sonuçla tamamlandı. İsteğe bağlı 404 satırlarının nötr gösterimi ve başarılı POST satırı görsel olarak doğrulandı; konsol hata/uyarı kaydı boştu.

Bu kontroller kurum veritabanının gerçek yük altındaki süresini doğrulamaz. Kurumda yeniden denemede sorun sürerse **Bağlantılar → Bağlantı test sonuçları → Analiz için katalog taraması** raporu hangi şema/aşamada zaman aşımı olduğunu gösterir. Uygulamada demo veri kullanılmaz.

## Önceki 0.3.2 doğrulaması

**172 Python testi**, gerçek Python 3.9.7 x64 ve Windows PowerShell 5.1 ile **14,705 saniyede geçti**. Bu önceki sürümün sonucudur; güncel çıktı ve sayı yukarıdadır. JavaScript sözdizimi ve aşağıda açıklanan 17 arayüz regresyon senaryosu da geçti. Yeni çalışma bağımlılığı yoktur.

- Kullanıcının kurumdan verdiği gerçek çıktı: embedding model listesi ve `/info` yolları 404; model adı içermeyen `POST /v1/embeddings` 200 ve 4096 boyutlu geçerli vektör. LLM `qwen38-27b` ile `/v1/chat/completions` çağrısı ve JSON yanıtı başarılı. Bu kanıt küçük script çağrılarına aittir; kurumda tam uygulama analizi doğrulaması değildir.
- Regresyonlar, bu desenin uygulama keşfi → ayar aktarımı → iki değişkenli gerçek-mod analiz boyunca çalıştığını doğrular. Yalnız Oracle ve HTTP taşıması taklit edilir. Kök vektör dizisi ve `data[*].embedding` yanıtlarının ikisi de kapsanır; 4096 boyut yanıttan okunur. İstekler yalnız `input` içerir ve tek metinliktir; gerçek analizde LLM çağrısı ve kaynak kanıtları da doğrulanır.
- Model kimliği/sürümü bilinmediğinde uydurulmaz; `server_default` ayarı dışa aktarma ve gerçek analizde korunur. Kalıcı vektör önbelleği ve checkpoint sonucu kullanılmaz. `deployment:` etiketi de gerçek sürüm sayılmaz. Aynı girdinin ikinci analizde model çağrılarını yeniden yaptığı test edilir.
- Model adı girilmiş ama istek şablonunda model seçimi bulunmayan standart embedding çağrısı, metadata doğrulaması olmadan başarı sayılmaz. Önceden doğrulanmış named-model/TEI ve özel sözleşme akışları korunur.
- Parola/API anahtarı rapor, profil ve checkpoint dosyalarına taşınmaz; auth/TLS hataları başarılı sayılmaz.

Yerel tarayıcı kontrolünde model kimliği boş embedding testi, aynı ayarla yeniden test, 4096 boyutun ve model alanı içermeyen şablonun doldurulması, LLM+embedding açık iki değişkenli analizin tamamlanması görüldü. Model adı değişince önceki endpoint/şablon/boyut doğrulaması temizlendi. Tarayıcı hata/uyarı kaydı boştu; bağlantı kartları görsel olarak kontrol edildi. Bu tarayıcı sunucusundaki Oracle ve model yanıtları kontrollü test yanıtlarıdır; uygulamaya demo modu eklenmedi.

## Kurulum

- İlk web sürümünde Windows PowerShell 5.1 ile scripts/setup.ps1 tamamlandı; proje 0.2.0 olarak kuruldu. Aşağıdaki 0.3.1 testleri aynı gerçek Python 3.9.7 ortamında güncel kaynak koduyla çalıştırıldı.
- 18 çalışma bağımlılığı gerçek Python 3.9.7 resolver'ıyla sabitlenip SHA256 hash denetimiyle kuruldu. Flask 3.1.3, Waitress 3.0.2, oracledb 4.0.2, openpyxl 3.1.5 dahil.
- Python <3.10 için gereken importlib_metadata ve zipp kilit dosyasında yer alır. Python 3.12 üzerinde varsayımsal çözümle yetinilmedi.
- pip check: bağımlılık çakışması yok. JavaScript sözdizimi kontrolü başarılı.
- PowerShell tırnak aktarımı düzeltmesi ve Türkçe mesajlar için UTF-8 BOM korundu. Tk arayüzü kaldırıldı; start.ps1 web uygulamasını başlatır.

## Önceki 0.3.1 otomatik kontrolleri

**146 Python testi**, önceki 0.3.1 doğrulamasında gerçek Python 3.9.7 x64 ve Windows PowerShell 5.1 ile **16,514 saniyede geçti**. Önceki 0.3.0 sürümündeki 115 teste, bağlantı tanılaması için 31 test eklendi. `pip check` bağımlılık çakışması bildirmedi; JavaScript sözdizimi kontrolü geçti. Güncel sürümün test sayısı ve çıktısı yukarıdadır.

0.3.1 yeni çalışma bağımlılığı eklemez. Önceki web kurulumu üzerinde güncelleme yeterlidir. Verilen kurum adresleri kodda tanımlıdır; parola veya API anahtarı bu değerlere dahil değildir.

| Alan | Kapsam |
|---|---|
| Web akışı | Gerçek-mode pipeline üzerinden Excel yükleme, otomatik beş başlık eşlemesi, özel başlık eşlemesi, Oracle taraması, kaynak tablo/kolon çıktısı, rapor indirme ve özgün hücre koruması |
| Oracle tanılama | ALL_USERS ile sınırlı aday listesi; girişte tüm kolon kataloğunu taramama; oturum/bağlam/şema hatalarını ayırma; DPY-4024 kodu ve önceki başarılı aşamaları koruma |
| Seçili şema testi | Bağlı şema parametresi, en fazla bir metadata satırı, boş sonuçta erişimi doğrulanmış saymama, zaman bütçesi ve kesinti sonrası kalan adımları atlama |
| Bağlantı hatası | Taklit edilen gerçek-mod bağlantı hatasında eksik kapsam ve unknown fiziksel yokluk; ham hata ve sırlar kullanıcıya/rapora taşınmaz |
| Model desteği | Temel adresten OpenAI/TEI keşfi; gerçek analiz adapter'ıyla küçük çağrı yanıtını doğrulama; çoklu modelde kullanıcı seçimi; açık özel sözleşme; boyut kontrolü, kök liste ve data[*].embedding yolları |
| Model hata ayrımı | DNS/TLS/HTTP/yanıt biçimi hataları, model listesi başarısını inference başarısı saymama, yanlış model veya boyutu reddetme, açık özel çağrı adresinde sessiz alternatif denememe, süre ve yanıt boyutu sınırları |
| Parolalar | Profilde dışlama, iki bağlantıda ayrı credential aktarımı, global ortamı değiştirmeme, checkpoint ve sonuç JSON'larında secret sentinel bulunmaması |
| Web sınırları | CSRF, Origin, Host, dosya/iş sahipliği, bozuk/büyük dosya, eksik/çift sütun, eşzamanlı ikinci işin reddi; tanılama uçlarında oturum ve seçili şema doğrulaması |
| Kapsam | Oracle kayıt sınırında tarama eksik; yokluk iddiası engellenir |
| Semantik | Aynı isim farklı anlam, eksik kritik bilgi, farklı kişi/işlem/grain, pencere/referans/NULL/sıfır ayrımı, güvenilir kaynaksız destek engeli |
| Excel/bağımlılık | Tüm sayfaları koruma, formula metni, sabit kimlik, belirsiz/eksik/döngülü bağımlılıklar, doğrulanmamış join engeli |
| Devam/cache | Kesinti sonrası devam, hatalı satırı yeniden deneme, içerik/model/kapsam değişiminde eski cache kararını reddetme |

Repoya dahil olmayan, ek paket gerektirmeyen Node tabanlı denetimde **17 arayüz regresyon senaryosu** geçti. Kapsam: şema değişince eski test sonucunun geçersizleşmesi, model testlerinin bağımsızlığı, tanılama raporunda sırların dışlanması, test sırasında sözleşme seçiminin kilitlenmesi ve geç gelen ilk bağlantı durumu yanıtının yeni durumu bozmaması. Bu denetim 146 Python testinin dışındadır; tarayıcı veya kurum servis testi yerine geçmez.

## 0.3.1 tarayıcı kontrolü

Repoya dahil olmayan yerel test sunucusunda uygulamanın gerçek web arayüzü kullanıldı; yalnız Oracle bağlantısı ve model HTTP taşıması taklit edildi. Aşağıdaki etkileşimler tarayıcıdan doğrulandı:

- Şema listeleme aşamasında DPY-4024 gösterimi ve önceki başarılı aşamaların korunması; yeniden girişin çalışması.
- Şema seçimi ve **Seçili şemaları test et** akışı; şema değişince önceki test raporunun geçersizleşmesi.
- Yalnız temel adresle OpenAI uyumlu LLM keşfi ve yanıt doğrulaması.
- Standart OpenAI yolu bulunmayınca TEI embedding keşfi, yanıt doğrulaması ve boyutun doldurulması.
- HTTP 401 hatasının servis raporunda anlaşılır biçimde gösterilmesi.
- Model ayarı değişince önceki doğrulamanın ve ilgili raporun geçersizleşmesi.

Yeni bağlantı ekranı 1280 × 900 ve 390 × 844 boyutlarında görsel olarak kontrol edildi. Dar ekranda yatay taşma yoktu; model kartları ve test düğmeleri tek sütunda kullanılabildi. Tarayıcı hata/uyarı kaydı boştu. Tanılama raporları servis bazında açılıp kapanır ve genel başarı durumu ayrıca gösterilir.

Bu kontroller, kurum servislerinin gerçekten erişilebilir olduğunu göstermez. Gerçek ortamda uygulanacak akış [Bağlantı test senaryosu](BAGLANTI_TEST_SENARYOSU.md) belgesindedir.

## Önceki 0.3.0 tarayıcı kapsamı

Önceki 0.3.0 sürümünde paketin gerçek web sunucusu localhost'ta HTTP 200 verdi. 1280 px masaüstü ve 390 px dar ekran düzeni görsel olarak kontrol edildi. Ayrı, repoya dahil olmayan test sunucusunda yalnız Oracle bağlantısı taklit edildi; gerçek web/pipeline kodu kullanıldı:

- Yalnız kullanıcı adı/parola → erişilebilir şema açılır listesi → kapsam seçimi.
- Ekrandan iki değişken ekleme, üçüncü satırı silme ve kalan iki değişkeni birlikte analiz etme.
- Beş sütunlu Excel yükleme → otomatik sütun eşleştirme (düzenleme bölümü kapalı) → analiz ilerlemesi.
- Sonuç tablosunda fiziksel kolon ile iş tanımı uyumunun ayrı görünmesi.
- Detayda şema/tablo/kolon yolu, kolon ve tablo açıklaması, kaynak kanıtları.
- Tarayıcı hata/uyarı kaydı boş; parola alanları form içinde; favicon mevcut. 390 px kontrolde yatay taşma yok.

Bu önceki kontrolde pointer otomasyonu bazı kontrolleri etkinleştirmedi; aynı kontroller klavyeyle doğrulandı. Uygulama içi tarayıcının indirme otomasyonu kendi aktarım hatasıyla kesildi. Rapor indirme HTTP uç noktası ve dönen XLSX içeriği otomatik testte başarıyla doğrulandı; bu, kurum tarayıcısında indirme testi yapıldığı anlamına gelmez.

## Kurumda doğrulanacaklar

Oracle DNS/port/service/container, kullanıcı yetkileri, taranacak gerçek şemalar, thin driver uyumluluğu, kurumsal paket aynası erişimi, CA/proxy, Qwen tam endpoint/model/yanıt sözleşmesi ve gerçek değişken eşleştirme doğruluğu. Bunlar iş bilgisayarında gerçek Excel ile sınanmalıdır.

İsim veya benzerlik skoru tek başına iş anlamı kanıtı değildir. Serbest iş tanımı parser'ı sınırlıdır; eksik veya anlaşılmayan tanımda çekimser sonuç tasarlanmış davranıştır. Çok kullanıcılı ağ sunucusu ve web arayüzünde kesintiden devam bu sürümün kapsamında değildir.
