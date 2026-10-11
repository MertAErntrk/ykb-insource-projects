# Web sürümü doğrulaması — 0.3.0

2026-10-11 tarihinde gerçek Python 3.9.7 x64 ve Windows PowerShell 5.1 üzerinde doğrulandı. Kurum Oracle/Qwen servislerine bağlanılmadı; test bağlantıları kontrollü taklit adapter'lardır.

## Kurulum

- Windows PowerShell 5.1 ile scripts/setup.ps1 tamamlandı; proje 0.2.0 olarak kuruldu.
- 18 çalışma bağımlılığı gerçek Python 3.9.7 resolver'ıyla sabitlenip SHA256 hash denetimiyle kuruldu. Flask 3.1.3, Waitress 3.0.2, oracledb 4.0.2, openpyxl 3.1.5 dahil.
- Python <3.10 için gereken importlib_metadata ve zipp kilit dosyasında yer alır. Python 3.12 üzerinde varsayımsal çözümle yetinilmedi.
- pip check: bağımlılık çakışması yok. JavaScript sözdizimi kontrolü başarılı.
- PowerShell tırnak aktarımı düzeltmesi ve Türkçe mesajlar için UTF-8 BOM korundu. Tk arayüzü kaldırıldı; start.ps1 web uygulamasını başlatır.

## Otomatik kontroller

115 test, gerçek Python 3.9.7 x64 ve Windows PowerShell 5.1 ile geçti. Önceki 103 kontrole ek olarak sabit DSN ile giriş, çoklu manuel değişken, çoklu şema, otomatik Excel eşlemesi, oturum ayrımı/süresi ve Oracle hata aşamaları doğrulandı.

0.3.0 yeni çalışma bağımlılığı eklemez. Önceki web kurulumu üzerinde güncelleme yeterlidir. Verilen kurum adresleri kodda tanımlıdır; parola veya API anahtarı bu değerlere dahil değildir.

| Alan | Kapsam |
|---|---|
| Web akışı | Gerçek-mode pipeline üzerinden Excel yükleme, otomatik beş başlık eşlemesi, özel başlık eşlemesi, Oracle taraması, kaynak tablo/kolon çıktısı, rapor indirme ve özgün hücre koruması |
| Gerçek bağlantı hatası | Eksik kapsam, unknown fiziksel yokluk; ham hata ve sırlar kullanıcıya/rapora taşınmaz |
| Model desteği | Açık endpoint/model/sözleşme gerekliliği, ayrı LLM ve embedding çağrıları, boyut kontrolü, kök liste ve data[*].embedding yolları |
| Parolalar | Profilde dışlama, iki bağlantıda ayrı credential aktarımı, global ortamı değiştirmeme, checkpoint ve sonuç JSON'larında secret sentinel bulunmaması |
| Web sınırları | CSRF, Origin, Host, dosya/iş sahipliği, bozuk/büyük dosya, eksik/çift sütun, eşzamanlı ikinci işin reddi |
| Kapsam | Oracle kayıt sınırında tarama eksik; yokluk iddiası engellenir |
| Semantik | Aynı isim farklı anlam, eksik kritik bilgi, farklı kişi/işlem/grain, pencere/referans/NULL/sıfır ayrımı, güvenilir kaynaksız destek engeli |
| Excel/bağımlılık | Tüm sayfaları koruma, formula metni, sabit kimlik, belirsiz/eksik/döngülü bağımlılıklar, doğrulanmamış join engeli |
| Devam/cache | Kesinti sonrası devam, hatalı satırı yeniden deneme, içerik/model/kapsam değişiminde eski cache kararını reddetme |

## Tarayıcı kontrolü

Paketin gerçek web sunucusu localhost'ta HTTP 200 verdi. 1280 px masaüstü ve 390 px dar ekran düzeni görsel olarak kontrol edildi. Ayrı, repoya dahil olmayan test sunucusunda yalnız Oracle bağlantısı taklit edildi; gerçek web/pipeline kodu kullanıldı:

- Yalnız kullanıcı adı/parola → erişilebilir şema açılır listesi → kapsam seçimi.
- Ekrandan iki değişken ekleme, üçüncü satırı silme ve kalan iki değişkeni birlikte analiz etme.
- Beş sütunlu Excel yükleme → otomatik sütun eşleştirme (düzenleme bölümü kapalı) → analiz ilerlemesi.
- Sonuç tablosunda fiziksel kolon ile iş tanımı uyumunun ayrı görünmesi.
- Detayda şema/tablo/kolon yolu, kolon ve tablo açıklaması, kaynak kanıtları.
- Tarayıcı hata/uyarı kaydı boş; parola alanları form içinde; favicon mevcut. 390 px kontrolde yatay taşma yok.

Bu tarayıcıda pointer otomasyonu bazı kontrolleri etkinleştirmedi; aynı kontroller klavyeyle doğrulandı. Uygulama içi tarayıcının indirme otomasyonu kendi aktarım hatasıyla kesildi. Rapor indirme HTTP uç noktası ve dönen XLSX içeriği otomatik testte başarıyla doğrulandı; bu, kurum tarayıcısında indirme testi yapıldığı anlamına gelmez.

## Kurumda doğrulanacaklar

Oracle DNS/port/service/container, kullanıcı yetkileri, taranacak gerçek şemalar, thin driver uyumluluğu, kurumsal paket aynası erişimi, CA/proxy, Qwen tam endpoint/model/yanıt sözleşmesi ve gerçek değişken eşleştirme doğruluğu. Bunlar iş bilgisayarında gerçek Excel ile sınanmalıdır.

İsim veya benzerlik skoru tek başına iş anlamı kanıtı değildir. Serbest iş tanımı parser'ı sınırlıdır; eksik veya anlaşılmayan tanımda çekimser sonuç tasarlanmış davranıştır. Çok kullanıcılı ağ sunucusu ve web arayüzünde kesintiden devam bu sürümün kapsamında değildir.
