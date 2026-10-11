# Kurumda bağlantı test senaryosu · 0.3.1

Bu senaryo iş bilgisayarında **gerçek Oracle hesabınız ve kurum servislerinizle** uygulanır. Amaç, hangi bağlantı adımının çalıştığını ve hangisinin hata verdiğini ayırmaktır. Geliştirme bilgisayarındaki otomatik testler gerçek kurum erişiminin yerine geçmez.

Oracle testleri yalnız katalog bilgisi okur; müşteri kaydı sorgulamaz ve veritabanına yazmaz. Model bağlantı testleri sabit, kısa bir doğrulama metni gönderir; Excel'inizi veya katalog verisini göndermez.

## 1. Güncel uygulamayı açın

Açık uygulamanın PowerShell penceresinde **Ctrl+C** yapın. `risk-feature-workbench` klasöründe:

```powershell
git pull --ff-only
.\scripts\start.ps1
```

Önceki web sürümü kuruluysa yeni paket kurulumu gerekmez. Tarayıcıda **Ctrl+F5** yapın. Test boyunca kurum ağı/VPN bağlantınızı açık tutun. Başlangıçta **LLM + embedding desteği** kapalı kalsın.

## 2. Oracle oturumunu ve şema listesini test edin

**Kullanıcı adı** ve **Parola** girip **Bağlan ve şemaları getir** düğmesine basın. **Bağlantı test sonuçları** bölümünü açın.

| Aşama | Beklenen sonuç | Başarısızsa ne anlama gelir? |
|---|---|---|
| Oracle oturumu açma | Kullanıcı adı ve parola kabul edilir. | Henüz katalog sorgusuna gelinmedi. Hata koduna göre hesap, ağ veya sürücü uyumluluğu incelenir. |
| Sorgu süre sınırı | Çağrı başına 10 saniye sınırı ayarlanır. | Sürücü/bağlantı ayarı uygulanamadı; sonraki adımlar çalıştırılmaz. |
| Veritabanı bağlamı | Veritabanı ve container bilgisi okunur. | Oturum açılmış olabilir; bağlam sorgusu ayrıca başarısızdır. |
| Şema adaylarını listeleme | Açılır listeye şema adları gelir. | Oturum ve bağlam adımları geçtiyse sorun şema listesini alma aşamasındadır. |

Liste `ALL_USERS` üzerinden en fazla 1.000 **şema adayı** içerir. Listeye girmek o şemanın kolonlarına erişimi kanıtlamaz. Liste sınırına ulaşıldığında uyarı gösterilir; listede bulunmayan bir şema için “yok” sonucu çıkarılmaz.

Önceki ekrandaki `DPY-4024`, Oracle çağrısının süre sınırını aşmasıdır. Ekranda bu hata şema listeleme aşamasında olduğundan kullanıcı/parola adımını geçmişti; bu tek başına yetki hatası olduğunu göstermiyordu. Yeni sürüm, giriş sırasında tüm `ALL_TAB_COLUMNS` görünümünden farklı sahipleri çıkarmak yerine sınırlı şema listesi okur. Kurumdaki gecikmenin gerçek sebebi yeni testin aşama ve süreleriyle ayrıca doğrulanmalıdır. [Oracle sürücüsünün zaman aşımı açıklaması](https://python-oracledb.readthedocs.io/en/v4.0.1/user_guide/appendix_c.html).

## 3. Yalnız bir veya iki şemanın erişimini test edin

1. Açılır listeden işinizle ilgili, adını bildiğiniz **bir veya iki şema** seçin.
2. **Seçimi tamamla**, ardından **Seçili şemaları test et** düğmesine basın.
3. Test raporundaki **Şema erişimi · ...** satırlarını inceleyin.

| Sonuç | Yorum ve sonraki adım |
|---|---|
| Geçti | Seçili şemadan en az bir kolonun adı/tipi ve açıklama kataloglarına sınırlı erişim doğrulandı. Değişken aramasına geçin. |
| Görünür kolon bulunamadı | Şema boş olabilir veya hesabınız kolonları göremiyor olabilir. İşinizde kullandığınız başka bir şemayı seçin; gerekiyorsa veritabanı ekibiyle o şemanın görünürlüğünü kontrol edin. |
| `DPY-4024` / `DPI-1067` | Katalog sorgusu zaman sınırını aştı. Bir şemayla tekrar deneyin; tekrarlarsa rapordaki aşama, süre ve kodla sorgu/ağ durumunu inceleyin. |
| `ORA-00942` / `ORA-01031` | İlgili katalog görünümü veya erişim izni kontrol edilmeli. |
| Atlandı | O aşama denenmedi. Önceki hata veya toplam süre bütçesi nedeniyle test edilmemiş şema başarılı sayılmaz. |

Her şema için **en fazla bir katalog satırı** okunur. Çağrı başına en fazla 10 saniye sınırı ve seçili şema testi için 45 saniyelik işlem bütçesi uygulanır. Zaman aşımı veya bağlantı kesintisinden sonra kalan şemalar aynı oturumla denenmez. Bu test, bütün tabloların tarandığı veya müşteri verisine erişildiği anlamına gelmez.

## 4. LLM ve embedding'i ayrı ayrı test edin

**Bağlantılar** sayfasında iki temel adres hazırdır. Başlangıçta gelişmiş alanları doldurmanız gerekmez.

1. Servis gerektiriyorsa ilgili **API anahtarını** girin.
2. **LLM bağlantısını test et** düğmesine basın.
3. Birden fazla model dönerse **Model kimliği** listesinden kurumunuzun kullanacağı modeli seçin ve testi yineleyin.
4. **Embedding bağlantısını test et** düğmesiyle aynı işlemi embedding için uygulayın.

| Kontrol | Başarı ölçütü |
|---|---|
| HTTPS ve sertifika ayarları | Yerel TLS/CA ayarı hazırlanır. Bu adım tek başına sunucuya erişimi kanıtlamaz. |
| Model listesi / TEI model bilgisi | Servisin bildirdiği gerçek model kimliği okunur. Çoklu modelde kullanıcı seçimi beklenir. |
| LLM gerçek denemesi | Küçük istek yanıtlanır ve uygulamanın beklediği JSON yanıt sözleşmesi doğrulanır. |
| Embedding gerçek denemesi | Küçük metinden geçerli sayısal vektör alınır; boyutu gerçek yanıttan okunur. |

Standart model listesi için `/v1/models`, embedding servis türüne göre `/info` gibi yollar denenir. Denenen bir yolun `404` olması, sonrasında başka uyumlu yol doğrulandıysa bütün testin başarısız olduğu anlamına gelmez. Servisin genel sonucu ile son gerçek çağrı sonucuna bakın. Yalnız model listesinin okunması da başarılı inference testi sayılmaz.

Başarıda doğrulanan çağrı adresi ve sözleşme alanları otomatik doldurulur. Gerçek model sürümü bildirilmemişse `deployment:` etiketi doğrulanmış bir sürüm numarası sayılmaz. Standart sözleşme bulunamazsa **Gelişmiş / özel servis ayarları** altında servis ekibinin verdiği tam çağrı adresi ve sözleşme kullanılabilir.

| Kod / durum | Yapılacak kontrol |
|---|---|
| `MODEL_SELECTION_REQUIRED` | Model listesinden seçim yapın ve testi yeniden çalıştırın. |
| `HTTP_401` / `HTTP_403` | O servisin API anahtarı ve erişim izni. |
| `DNS` / `NETWORK` / `TIMEOUT` | Kurum ağı/VPN, isim çözümleme, proxy ve servis erişimi. |
| `TLS_CERTIFICATE` | İlgili servisin gelişmiş ayarına kurum CA PEM dosyasının yolunu girin. |
| `TLS_HANDSHAKE` | HTTPS adresi ve kurum proxy ayarı. |
| `HTTP_404` / `HTTP_405` | Servisin desteklediği gerçek yol/yöntem; varsa sonraki başarılı keşif adımı. |
| `HTTP_400` / `HTTP_422` / `RESPONSE_CONTRACT` | Model kimliği ve istek/yanıt biçimi. Servisin yanıt vermesi uygulama sözleşmesini karşıladığı anlamına gelmez. |
| `HTTP_502` / `HTTP_503` / `HTTP_504` | Model servisi ve kurum ağ geçidinin hazır olma durumu. |

Önceki **“LLM/Embedding tam çağrı adresi gerekli”** mesajları eksik form alanından kaynaklanıyordu; servisle bağlantı kurulmadan üretiliyordu. Yeni test temel adresle başlayıp gerçek çağrıyla doğrulama yapar.

## 5. Gerçek değişkenlerle aramayı deneyin

Önce erişim testi geçen dar kapsamla başlayın:

1. **Değişken inceleme → Ekrandan değişken ekle** bölümünde kendi sözlüğünüzden gerçek bir alan adı ve açıklama girin. İlk kontrol için seçili şemada fiziksel yerini bildiğiniz bir alanı kullanın.
2. **+ Değişken ekle** ile ikinci gerçek değişkeninizi ekleyin.
3. Model desteği kapalıyken **Değişkenleri ara** düğmesine basın. İki girdi için ayrı sonuç, şema/tablo/kolon adayları ve varsa gerçek açıklamalar geldiğini kontrol edin.
4. İki model bağlantısı da doğrulandıysa **LLM + embedding desteği** seçeneğini açıp aynı girdilerle tekrar çalıştırın. Model yorumunu fiziksel katalog kanıtından ayrı değerlendirin.
5. Son olarak **Excel ile toplu ara** bölümünden kendi Excel'inizi seçin. `Alan Adı` ve `Açıklama` eşleşmelerini ve seçili sayfaları kontrol ederek aramayı başlatın.
6. **Excel sonucunu indir** ile girdilerin ve sonuçların raporda bulunduğunu doğrulayın.

Bir kolonun aynı adla bulunması, iş tanımının aynı olduğunu kanıtlamaz. Tarama eksikse “DB'de yok” sonucunu kabul etmeyin. 10.000 kolon sınırı veya zaman aşımı görülürse şema kapsamını daraltın. Başarılı tek satırlık erişim testi, tam katalog taramasının tamamlanacağını garanti etmez.

## 6. Hata sürerse paylaşılacak bilgi

**Tanılama raporunu indir** düğmesiyle o denemeye ait JSON raporunu kaydedin. Raporda test adımı, durum, süre ve güvenli hata kodu bulunur; parola, API anahtarı ve ham servis yanıtı bulunmaz. Bu rapor bağlantı denemelerini kapsar; arama sırasında oluşan bir hata için ayrıca ekrandaki analiz durumunu ve hata aşamasını belirtin.

Tekrar tekrar bütün şemaları denemek yerine **hangi adım geçti, ilk hangi adım kaldı, hangi kod ve sürede kaldı** bilgisini kullanın. Başarı ölçütü, kurumda Oracle oturumunun açılması, seçtiğiniz şemaların erişim testinin geçmesi ve kullanacaksanız her iki modelin gerçek denemesinin doğrulanmasıdır.
