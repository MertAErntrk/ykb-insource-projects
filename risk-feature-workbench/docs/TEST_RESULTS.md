# Web sürümü doğrulaması — 0.3.1

2026-10-11 tarihinde gerçek Python 3.9.7 x64 ve Windows PowerShell 5.1 üzerinde doğrulandı. Kurum Oracle/Qwen servislerine bağlanılmadı; otomatik ve tarayıcı testlerinde Oracle bağlantısı ile model HTTP taşıması kontrollü olarak taklit edildi.

## Kurulum

- İlk web sürümünde Windows PowerShell 5.1 ile scripts/setup.ps1 tamamlandı; proje 0.2.0 olarak kuruldu. Aşağıdaki 0.3.1 testleri aynı gerçek Python 3.9.7 ortamında güncel kaynak koduyla çalıştırıldı.
- 18 çalışma bağımlılığı gerçek Python 3.9.7 resolver'ıyla sabitlenip SHA256 hash denetimiyle kuruldu. Flask 3.1.3, Waitress 3.0.2, oracledb 4.0.2, openpyxl 3.1.5 dahil.
- Python <3.10 için gereken importlib_metadata ve zipp kilit dosyasında yer alır. Python 3.12 üzerinde varsayımsal çözümle yetinilmedi.
- pip check: bağımlılık çakışması yok. JavaScript sözdizimi kontrolü başarılı.
- PowerShell tırnak aktarımı düzeltmesi ve Türkçe mesajlar için UTF-8 BOM korundu. Tk arayüzü kaldırıldı; start.ps1 web uygulamasını başlatır.

## Otomatik kontroller

**146 Python testi**, gerçek Python 3.9.7 x64 ve Windows PowerShell 5.1 ile **16,514 saniyede geçti**. Çıktı: [test-results.txt](test-results.txt). Önceki 0.3.0 sürümündeki 115 teste, bağlantı tanılaması için 31 test eklendi. `pip check` bağımlılık çakışması bildirmedi; JavaScript sözdizimi kontrolü geçti.

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
