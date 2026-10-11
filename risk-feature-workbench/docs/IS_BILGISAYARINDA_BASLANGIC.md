# İş bilgisayarında kullanım · 0.5.0

Python **3.9.7 x64** ve Windows PowerShell **5.1** desteklenir. Uygulama tarayıcıda çalışır. Demo seçeneği yoktur. 0.5'te yeni paket bağımlılığı yoktur; 0.4 kurulumu aynen kullanılır.

## 1. Güncelle ve aç

Önce uygulamanın açık PowerShell penceresinde **Ctrl+C** ile kapatın. `ykb-insource-projects\risk-feature-workbench` klasöründe:

```powershell
git pull --ff-only
.\scripts\start.ps1
```

Önceki sürüm kuruluysa tekrar paket kurmanız gerekmez. İlk kurulum veya eksik paket hatası için (paketler kurumun Artifactory PyPI aynasından iner; adresi kurum dokümanından alın):

```powershell
.\scripts\setup.ps1 -IndexUrl '<kurum-artifactory-pypi-simple-adresi>'
.\scripts\start.ps1
```

Tarayıcı otomatik açılmazsa **http://127.0.0.1:8765** adresine gidin. PowerShell penceresini açık tutun. Eski ekran kalırsa **Ctrl+F5** yapın. Python bulunamazsa setup komutuna `-Python 'C:\Python39\python.exe'` ekleyin. Port doluysa sanal ortamın Python'uyla `-m riskmatch serve --port 8766` çalıştırılabilir.

## 2. İlk kullanım adımları

1. **Bağlan:** üstteki **EDWLIVE bağlantısı** alanına yalnız kullanıcı adı ve parola; **Bağlan ve şemaları getir**.
2. **Şema:** açılır listeden bir veya iki şema seçin (en fazla 30), **Seçimi tamamla**, ardından **Seçili şemaları test et**. Liste `ALL_USERS` adaylarıdır; görünmek kolonlara erişimi kanıtlamaz. Test, şema başına en çok bir metadata satırı okur.
3. **Embedding testi:** **Bağlantılar** sayfasında **Embedding bağlantısını test et** (model kimliği boş kalabilir). LLM isteğe bağlıdır; yalnız gösterilen aday var ama hiçbiri "güçlü anlam uyumu" değilse çağrılır ve kanıt yerine geçmez.
4. **İndeks:** **İndeks ilerlemesi** panelinde **İndekslemeyi başlat**. Katalog vektörleri bir kez üretilip bu bilgisayarda saklanır; ilerleme ve kalan süre görünür. **İndekslemeyi durdur** ile bırakılır; sonra kalan metinlerden sürer. İndeks bitmeden de arama yapılabilir; kapsam eksikse sonuçta belirtilir.
5. **Excel:** `.xlsx` dosyasını yükleyin (bir kez; sonraki aramalarda yeniden yüklenmez). Standart başlıklar otomatik eşlenir: Kategori, **Alan Adı**, Alan tipi, **Açıklama**, Halihazırda bildiriliyor mu. Yalnız Alan Adı ve Açıklama zorunludur; farklı başlıklar için eşleştirme bölümünü açın. Dosya en çok 12 MB, sayfa başına 20.000 satır, analiz başına 2.000 değişken olabilir. Ekrandan en çok 100 değişken de eklenebilir.
6. **İlk 1:** önce tek satır arayın; sonucu, adayı ve **Detay**'ı kontrol edin (İlk 5 ve İlk 10 kısayolları da vardır).
7. **Sıradaki 10 aranmamış:** daha önce aranmış satırlar atlanır. Satırları elle de işaretleyebilirsiniz.
8. **Tümü:** kalan bütün satırlar. **Grup boyutu** 10/25/50; sonuçlar akışla tabloya düşer. Ara vermek için **Durdur**: o ana kadarki sonuçlar korunur ve **Kısmi raporu oluştur** ile kısmi Excel alınır. Sayfayı yenilerseniz çalışan iş yeniden bağlanır. "Tamamlandı" yalnız Excel yazıldıktan sonra görünür; rapor yazılamazsa (dosya açık, disk dolu) sonuçlar ekranda kalır, nedeni giderip yeniden denersiniz.
9. **Excel sonucunu indir:** raporda *Sonuçlar, Adaylar, Çalışma bilgisi, Kanıtlar* ve *Girdi ...* sayfaları bulunur ([sütunlar](CONTRACT.md)).

### Sonuçları okuma

| Satır durumu | Anlam |
|---|---|
| Aday bulundu | 1–3 aday gösterim kapısından geçti. **Ad eşleşmesi** (birebir/kısaltma/kısmi) ile **iş anlamı** (güçlü/olası/zayıf/fark var/açıklama yok) ayrı okunur; **farklar ve kanıt** neyin uyuşmadığını yazar. |
| Yalnız zayıf aday | Doğrulanmadan kullanmayın. |
| Seçili kapsamda güvenilir aday yok | Katalog tam ve anlamsal kapsam tam; yalnız seçtiğiniz şemalar için geçerlidir. |
| Aday yok — anlamsal arama kapsamı tam değil | "DB'de yok" **denmez**; indeksi tamamlayıp yeniden arayın. |
| İncelenemedi | Girdi/teknik hata; satır hataları sütununa bakın. |
| Aranmadı | Satır henüz aranmadı. |

Reddedilen yakın adaylar Adaylar sayfasında "Reddedildi: ..." gerekçesiyle durur. Arama skoru yüzde veya doğruluk olasılığı değildir. Fiziksel adın bulunması iş tanımının aynı olduğunu kanıtlamaz. Satır "model değişti" uyarısı taşıyorsa embedding modeli iş sırasında değişmiştir; işi yeniden çalıştırın.

## 3. Bağlantı ve güvenlik ayrıntıları

DSN, sunucu, port ve ortam uygulamada hazırdır; ayar dosyası yüklemek gerekmez. Parola işlem belleğinde oturuma özel tutulur; çereze, dosyaya veya rapora yazılmaz. **Bağlantıyı değiştir** parolayı siler; bir saat kullanılmayan oturum kapanır. Yalnız katalog metadata'sı okunur; müşteri kaydı sorgulanmaz.

Oracle katalog taraması şema başına en çok 25 tablo/görünümlük gruplarla, kolon/açıklama/anahtar ayrı sorgularla yapılır; çağrı sınırı 30 sn, toplam bütçe 300 sn, tarama sınırı 10.000 kolondur. Katalog önbelleği 8 saat geçerlidir ve ucuz bir imza kontrolüyle doğrulanır; sınır aşılırsa seçili şema sayısını azaltın.

Katalog hiç okunamazsa arama başlamaz; Excel ve eşleştirme korunur, **Şema seçimine ve girdilere dön** ile tek şemayla yeniden denersiniz. Katalog kısmi okunursa sonuçlar eksik kapsam uyarısı taşır.

## 4. İsteğe bağlı LLM ve embedding

**Bağlantılar** sayfasını açın; kurum adresleri hazır gelir.

1. Servis anahtar istiyorsa **API anahtarı** alanını doldurun; istemiyorsa boş bırakın.
2. LLM **Model kimliği** alanına `qwen38-27b` yazın; embedding **Model kimliği**'ni boş bırakın (kurumda sunucunun varsayılan modeliyle 4096 boyutlu vektör döndüğü görülmüştür).
3. **LLM bağlantısını test et**, sonra **Embedding bağlantısını test et**. Birden fazla model listelenirse doğrusunu seçip testi yineleyin.
4. Başarılı test tam çağrı adresini, biçimi ve gerçek vektör boyutunu doldurur. Model listesi veya `/info` yollarındaki `404/405`, gerçek çağrı başarılıysa **Desteklenmiyor** olarak görünür ve normaldir.

0.5, model kimliği boş olsa da 8 sabit metnin vektörlerinden bir **model parmak izi** çıkarır: model değişirse yeni vektör alanı açılır, eski vektörler karıştırılmaz. İş sonunda 2 metinle yeniden doğrulama yapılır; model değişmişse işteki satırlar uyarı alır. **Gelişmiş / özel servis ayarları** yalnız standart test servisi tanımazsa açılır; kurum CA sertifikası gerekiyorsa PEM dosyası buradan verilir (TLS doğrulaması kapatılmaz). [Adapter ayrıntıları](ADAPTERS.md).

Model desteği açık analiz, değişken adı/açıklaması ile katalog metadata'sını seçilen kurum servislerine gönderir. Embedding art arda 3 kez hata verirse o iş ad ve kelime aramasıyla sürer ve uyarı yazılır.

## 5. Doğrulama betiği (`scripts\is_pc_dogrulama.ps1`)

Gerçek ortamda ölçüm içindir. Önce `config\real.example.json` dosyasını `config\real.json` olarak kopyalayıp doldurun (`allow_real_connections: true`; sır yazmayın, `*_env` alanları ortam değişkeni **adlarını** taşır). Tanımsız Oracle ortam değişkenleri için betik yalnız o süreçte, diske yazmadan ister. `config\real.json` git dışında tutulur (`.gitignore`). Embedding servisi anahtar istiyorsa betikten önce aynı oturumda `$env:RISK_EMBEDDING_API_KEY` (ya da `embedding.api_key_env` alanındaki ad) ayarlanır; betik bu değişken yoksa yalnız uyarır.

```powershell
# Yalnız probe (embedding gecikmesi, parmak izi, doğrulama)
powershell -ExecutionPolicy Bypass -File scripts\is_pc_dogrulama.ps1 -Config config\real.json

# Etiketli Excel ile havuz/kapı taraması + uçtan uca koşu + tutarlılık
powershell -ExecutionPolicy Bypass -File scripts\is_pc_dogrulama.ps1 -Config config\real.json `
    -Excel C:\yol\etiketli.xlsx -LabelColumn "Beklenen kolon" -Rows 100 -GroupSize 25
```

Parametreler: `-Config` (varsayılan `config\real.json`), `-Excel`, `-LabelColumn` (varsayılan "Beklenen kolon"), `-Rows` (0 = tümü), `-GroupSize` (25), `-StateDir` (önbellek dizini; boşsa geçici). Çıktı `runtime\dogrulama\<zaman>\` altında `rapor.md`, `rapor.json`, `havuz.json`. Excel'li koşu uzun sürebilir.

## 6. Sorun giderme

**Bağlantı test sonuçları** bölümü her adımın süresini ve hata kodunu gösterir; **Tanılama raporunu indir** parola ve ham yanıt içermeyen JSON üretir. Ayrıntılı sıra: [Kurumda bağlantı test senaryosu](BAGLANTI_TEST_SENARYOSU.md).

| Belirti | Olası neden | Ne yapılır |
|---|---|---|
| `DPY-4024` | Oracle çağrısı zaman sınırını aştı (tek başına parola/yetki sorunu değildir) | Tek şemayla yeniden deneyin; raporda aşamaya bakın. Excel'in okunmuş olması sorunun Excel'de olmadığını gösterir. |
| `ORA-01017` | Kullanıcı/parola | Bilgileri kontrol edin. |
| `ORA-28000` / `ORA-28001` | Hesap kilidi / parola süresi | Hesap sahibiyle çözün. |
| `ORA-00942` / `ORA-01031` | Katalog görünümüne yetki yok | Yetkiyi kontrol ettirin. |
| `DPY-3010` / `DPY-3015` | Sunucu/parola doğrulayıcı Thin sürücüyle uyumsuz | Kurum DB ekibine bildirin. |
| "Katalog okunamadı; arama başlatılamadı" | Hiç katalog kaydı alınamadı | **Şema seçimine ve girdilere dön**, tek şemayla tekrar. |
| Embedding/LLM testinde 401/403 | Anahtar veya erişim izni | API anahtarını/izni kontrol edin. |
| Embedding 429 / 5xx / zaman aşımı | Servis yoğun veya ulaşılamıyor | Birkaç dakika sonra yineleyin. Servis kendiliğinden 1 kez yeniden dener; sürerse iş ad+kelime aramasıyla devam eder. Arka plan indeksi art arda 5 hatada durur: yeniden başlatın. |
| "Embedding servisi art arda hata verdi" uyarısı | Devre kesici açıldı | Satırlar yalnız ad ve kelime kanıtıyla değerlendirildi; servis düzelince yeniden arayın. |
| Sertifika hatası | Kurum CA | CA PEM dosyasını servisin gelişmiş ayarına girin. |
| "Aday yok — anlamsal arama kapsamı tam değil" | İndeks bitmedi | İndeksi tamamlayın, satırları yeniden arayın. |
| Satırda "model değişti" uyarısı | Embedding modeli iş sırasında değişti | İşi yeniden çalıştırın. |
| Rapor yazılamadı (dosya açık / izin / disk dolu) | Hedef Excel'de açık, yazma izni yok, disk dolu | Dosyayı kapatın veya yer açın, raporu yeniden yazdırın. Sonuçlar kaybolmaz. |
| "Bir analiz devam ediyor" (409) | Aynı oturumda etkin iş var | Bitmesini bekleyin veya **Durdur**. |
| Port kullanılıyor | Uygulama zaten açık | Açık pencereyi Ctrl+C ile kapatın veya `--port 8766`. |
| Eski ekran | Tarayıcı önbelleği | Ctrl+F5. |

Ham hata metni parola/bağlantı bilgisi içerebileceğinden gösterilmez; destek için aşama, hata kodu ve tanılama raporunu kullanın, parolanızı paylaşmayın.

## 7. Kurumda doğrulanmamış olanlar

Geliştirme bilgisayarından kurum ağına ve gerçek hesaba erişilmemiştir. Şunlar iş bilgisayarında ölçülmelidir (bölüm 5 betiği):

- Gerçek Oracle katalog boyutu, tarama süresi, 10.000 kolon sınırına ulaşılıp ulaşılmadığı.
- Gerçek embedding modelinin kimliği, kalitesi ve tam indeks süresi.
- LLM katkısı.
- Kurum etiketli Excel ile gerçek doğruluk (ilk 3'te isabet, karşılığı olmayan satırlarda yanlış öneri oranı).
- Parmak izi ve model değişimi davranışının kurum servisinde çalışması; tam Excel analizinin uçtan uca tamamlanması.
- Disk dolu ve dosya kilidi durumlarının kurum diskinde davranışı.

Sorguların küçültülmesi her zaman aşımının giderildiği anlamına gelmez.
