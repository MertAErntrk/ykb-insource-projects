# İş bilgisayarında web uygulamasını çalıştırma

Python 3.9.7 x64 kullanılır. Node.js, Tk veya masaüstü uygulama kurulumu gerekmez. Önceki kurulumu olan kullanıcı da web paketleri için setup.ps1 çalıştırmalıdır.

## 1. Güncelleme ve kurulum

PowerShell'i `ykb-insource-projects\risk-feature-workbench` klasöründe açın:

```powershell
git pull --ff-only
$env:PIP_INDEX_URL = 'https://KURUM-ARTIFACTORY-ADRESI/api/pypi/REPO/simple'
.\scripts\setup.ps1
.\scripts\start.ps1
```

İkinci satırdaki adresi kurumunuzda `pip install --index-url ...` için kullanılan gerçek adresle değiştirin. Aynı PowerShell oturumundaki bütün paket kurulumları bu aynayı kullanır. Mevcut PIP_INDEX_URL zaten doğruysa yeniden yazmanız gerekmez. Alternatif: `.\scripts\setup.ps1 -IndexUrl 'https://...'`.

Python otomatik bulunamazsa `.\scripts\setup.ps1 -Python 'C:\Python39\python.exe'` komutunda kendi Python yolunuzu belirtin. Eski `-Real` parametresi kabul edilir; artık her kurulum web + Oracle bağımlılıklarını yükler.

Tarayıcı otomatik açılmazsa **http://127.0.0.1:8765** adresine gidin. PowerShell penceresini açık bırakın. Kapatmak için Ctrl+C. Adres yalnız kendi bilgisayarınızdan erişilir.

## 2. Oracle bilgileri nereye girilecek?

**Bağlantılar → Oracle veritabanı** bölümünü açın.

| Ekrandaki alan | Ne girilecek? |
|---|---|
| Bağlantı metni (DSN) | Kurum TNS bağlantısı. `ALIAS=(DESCRIPTION=...)` tamamını yapıştırabilirsiniz. `host:port/service_name` da kabul edilir. |
| Kullanıcı adı | Oracle'a giriş yaptığınız kullanıcı. |
| Parola | Aynı hesabın parolası. Sohbete veya Git'e yazmayın. |
| Kaynak adı | Bağlantı testinden gelen veritabanı adı; sonuçlarda etiket olarak kullanılır. |
| Ortam etiketi | Çalıştığınız ortamı tanımlayan etiket. |
| Container | Bağlantı testinden otomatik gelir. Her analizde yeniden doğrulanır. |
| Taranacak şemalar | Testin getirdiği listeden seçin veya virgülle yazın. İş alanınızla ilgili şemaları seçin. |

**Oracle bağlantısını dene** düğmesine basın. Başarılıysa veritabanı/container alanları dolar ve erişilebilir şemalar listelenir. Bu aşamada henüz Excel analizi yapılmaz.

**Ayarları içe aktar** ile `.local.json` profilini yükleyebilirsiniz. **Ayarları indir** parola/API anahtarı içermeyen profil üretir. Bilgiler otomatik kalıcı kaydedilmez; sayfa yenilendiğinde yeniden profil yükleyin. Profili kurum bilgisayarınızda tutun.

## 3. Excel seçme

**Değişken inceleme → Excel dosyasını seçin → Dosyayı oku**.

Standart başlıklar: **Kategori | Alan Adı | Alan tipi | Açıklama | Halihazırda bildiriliyor mu**.

İlk 30 satırda başlık aranır. Farklı sütun adlarında karşılıkları açılır listeden seçin. **Alan adı** ve **Açıklama** zorunludur; diğerleri isteğe bağlıdır. İncelenecek sayfaları işaretleyin. Boş açıklama kanıt eksikliği yaratır; kesin anlam eşleştirmesi beklemeyin.

Dosya `.xlsx`, en fazla 12 MB; sayfa başına 20.000 satır/300 sütun, inceleme başına 2.000 değişken. Görüş/hazırlama süresi varsa korunur. Kaynak dosyanın üzerine yazılmaz.

## 4. Gerçek inceleme ve sonuç

**İncelemeyi başlat** düğmesine basın. LLM desteği kapalı olsa da gerçek Oracle kataloğunda isim ve açıklama aranır.

1. Değişkenin karşısında en güçlü adayın **şema.tablo.kolon** yolunu görün.
2. **Fiziksel ad**, aynı isimli kolonun seçili kapsamda bulunup bulunmadığını anlatır.
3. **İş tanımı uyumu** ayrı karardır. Aynı isim farklı anlam taşıyabilir; farklı isim doğru aday olabilir.
4. **Detay**, alternatif adayların tam kaynak yolunu, kolon/tablo açıklamasını, gerekçesini ve kanıtını gösterir.
5. **Excel sonucunu indir**, tüm adayları, kanıtları ve özgün girdileri içerir.

Kritik bilgi eksik sonucu, kolonun bulunmadığı anlamına gelmez; açıklama iş koşullarını doğrulamaya yetmiyor olabilir. Tamamlandı teknik işlem durumudur, bütün satırların onaylandığı anlamına gelmez.

Katalog sınırı 10.000 kolondur. Aşılırsa şemaları daraltın. Eksik taramada yokluk doğrulanmaz. İnceleme sırasında sayfayı yenilemeyin. Tamamlanan raporlar `runtime/web/<iş-kimliği>/sonuclar.xlsx` altındadır. Webde yarım kalan işe devam henüz yoktur; yeni analiz başlatılır. CLI aynı state diziniyle checkpoint kullanabilir.

## 5. LLM ve embedding

**Bağlantılar → Model servisleri** bölümünde temel adresleri **Temel adres** alanına girin. Bunlar not olarak korunur; otomatik yol eklenip çağrı yapılmaz.

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

## 6. Sık hatalar

- **PyPI / SSL WRONG_VERSION_NUMBER:** Artifactory adresini aynı terminalde PIP_INDEX_URL olarak tanımlayın. Yanlış proxy/TLS yolunu sertifika denetimini kapatarak çözmeyin.
- **No matching distribution:** önce ağ/ayna hatasını kontrol edin. Sabit sürüm kurum aynasında yoksa paket ekibine iletin; hash kilidini rastgele değiştirmeyin.
- **Oracle bağlantısı kurulamadı:** kurum ağı, DSN servis adı/port, kullanıcı/parola ve yetkileri kontrol edin. python-oracledb thin kullanılır; özel thick/client ihtiyacı varsa kurum desteği gerekir.
- **Container uyuşmuyor:** bağlantı testini tekrarlayın, doğru ortamı doğrulayın.
- **Şema listesi boş:** hesabın ALL_TAB_COLUMNS erişimi ilgili nesneleri göstermiyor olabilir.
- **TLS/CA hatası:** model ayarlarında BT'nin sağladığı PEM dosyasının gerçek yolunu kullanın.
- **Tam endpoint/model/sözleşme gerekli:** temel URL yetmez. Model desteğini kapatarak Oracle aramasını sürdürebilirsiniz.
- **8765 portu dolu:** açık uygulamayı bulun veya `.\.venv\Scripts\python.exe -m riskmatch serve --port 8766` kullanın.
- **PowerShell script politikası:** kurum politikasını değiştirmeden [alternatif komutları](WINDOWS.md) kullanın.

Gerçek Excel, sonuç, profil, parola, anahtar ve kurum adreslerini açık GitHub reposuna eklemeyin. Gerçek Oracle/Qwen bağlantısı iş bilgisayarınızda sınanmalıdır.
