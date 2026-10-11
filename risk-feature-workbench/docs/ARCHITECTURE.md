# Mimari değerlendirme ve karar kaydı

## 0.2 web sürümü

Güncel kullanıcı arayüzü Flask + Waitress üzerinde, yalnız 127.0.0.1 adresine bağlı yerel web uygulamasıdır. HTML/CSS/JavaScript paket içindedir; CDN, Node veya Tk gerekmez. Kullanıcı Oracle ve isteğe bağlı model bağlantılarını formdan girer, kendi Excel'ini yükler ve sayfa/sütun eşler. Tek arka plan worker ilerlemeyi raporlar; kaynak konumu/kolon açıklaması adaylarda ve Excel'de taşınır. Fiziksel varlık ve semantik destek ayrımı korunur.

Web API gerçek bağlantı yolunu kullanır; demo modu reddedilir. Parola ve API anahtarı iş belleğinde aktarılır, global ortam değişkenine veya profile yazılmaz. Host doğrulama, aynı origin kontrolü, oturum bağlı CSRF, HttpOnly/SameSite cookie, CSP, dosya ve iş sahipliği, dosya/boyut/kayıt sınırları uygulanır. Dosyalar runtime/web altında kalır. Bu sürüm tek kullanıcı yerel kullanım içindir; paylaşımlı ağa açılan sunucu değildir.

Aşağıdaki bölümler ilk semantik motorun tasarım gerekçelerini korur. Güncel kullanım rehberi IS_BILGISAYARINDA_BASLANGIC.md dosyasıdır.

Bu MVP metadata üzerinden kanıt arar; müşteri verisini hesaplamaz ve üretim veritabanına yazmaz. Sentetik örneklerin başarısı banka ortamının veya semantik doğruluk oranının doğrulandığı anlamına gelmez.

## Başlangıç tasarımına eleştiri

1. **Arama ve doğrulama farklı problemlerdir.** Kesin ad, BM25 ve embedding aday üretir. Yüksek benzerlik, ad eşitliği veya doğrulanmış bir model alıntısı iş anlamının eşitliğini kanıtlamaz. Karar deterministik boyut kontrollerinden çıkar.
2. **Yalnız kolon açıklaması yeterli olmayabilir.** “Son ayda farklı kişi” tanımı pencere sınırı, referans tarihi, yön ve kişi anahtarı belirtmez. Aynı cümlenin iki tarafta bulunması da bu eksikleri gidermez. İlk sürümde çok sayıda `insufficient` sonucu beklenen güvenli davranıştır.
3. **Oracle erişimi evrensel envanter değildir.** ALL_* görünürlük kapsamı, ortam ve container korunur. Eksik tarama veya timeout, yokluk değildir. Nesne bağımlılığı kolon lineage'ı veya doğrulanmış iş ilişkisi sayılmaz.
4. **LLM bir karar otoritesi değildir.** Yerel adapter yalnız sınırlı aday bağlamını yorumlar. Alıntı/kimlik doğrulaması halüsinasyonu azaltır ancak anlamsal doğruluk garantisi sağlamaz. `model_inference` kritik kanıt olarak kabul edilmez.
5. **Üretim bir ilişki ve kapsam problemidir.** Pay/payda bulunması tek başına yeterli değildir; kayıt seviyesi, referans tarihi, anahtar ve kapsam da uyuşmalıdır. Günlük tekil kişi sayılarını toplamak dönemlik tekil kişi sayısını vermez.
6. **Graphify iş semantiği motoru değildir.** Kod/doküman ilişkilerini inceleme rolü ayrı tutulur. Feature bağımlılıkları güvenli parser ve deterministik graph algoritmasıyla hesaplanır. Graphify çıktısı yalnız gerçekten çalıştırıldığı kapsamda raporlanmalıdır.

## Kararlar

| Konu | Karar ve gerekçe |
|---|---|
| Uygulama | Az bağımlılıklı Python paketi; UI, Excel, adapter, retrieval ve semantik katmanları ayrıdır. |
| Kanıt | Her fact `value`, `source`, `evidence` taşır. Açıklama, doğrulanmış metadata, doğrulanmış adlandırma ve model çıkarımı ayrılır. Kaynak metinler değişmeden korunur; normalleştirme yalnız karşılaştırma içindir. |
| Fail closed | Her kritik boyut compatible/conflict/missing alır. Herhangi bir çelişki `conflict`; eksik veya çözümlenmemiş parser sorunu `insufficient`; bütün gerekli kontroller geçerse `supported`. |
| İsimler | Doğrulanmış LM/LW/L2W/L3M yalnız pencere anlamını verir. İsmin “giden”, “max” veya “tutar” içermesi ölçüm kanıtına dönüştürülmez. Genel iki pencere son eki otomatik oran değildir. |
| Oran istisnası | Kullanıcının açıkça doğruladığı `Giden_max_havale_adeti` tabanı ve yapılandırmadaki `naming.ratio_bases` tabanları için boş açıklamada son iki pencere oranı üretilebilir. Sıfır/NULL politikası yine bilinmez. |
| Formül | İzinli ifade iki alan kimliğinin bölünmesidir. Sayısal sıfır-payda ve ayrı NULL politikası parse edilir; `eval`, Excel veya Python çalıştırması yoktur. Excel hücre referanslarını çözmek giriş katmanının görevidir. |
| Kritik alanlar | Her karşılaştırmada entity/grain/concept/measure; hareketlerde transaction/direction/aggregation/business_scope/null_policy; zamanlı ölçümlerde window/window_bounds/reference_date; tekil kişide person_key; tutar/oran/vadede unit; kategoride codes; max/ortalama/çok aşamalı agregasyonda grouping gerekir. Oranlar pay/payda/sıfır/NULL/referans tarihi gerektirir. |
| Üretim | Hazır desteklenen alan önce gelir. Desteklenen hazır pay/payda ve tam politikalar varsa yalnız `candidate` denir. MVP çapraz nesne join'ini otomatik doğrulamaz; aynı tam nesne yolu ve doğrulanmış anahtarlar gerekir. |
| İleri entegrasyon | İncelenmiş iş sözlüğü, ETL hesaplama tanımı ve doğrulanmış ilişkiler eklenebilir. Bu kaynaklar yokken isim veya model üzerinden sahte kesinlik yaratılmaz. |

## Sınırlar ve kullanım sözleşmesi

`parse_definition` kontrollü Türkçe/İngilizce kavramları tanır. Karmaşık serbest metin, iç içe formül, birden çok toplulaştırmanın sırası ve kapsam olumsuzlaması otomatik tamamlanmaz. Boş açıklamalar isimden hayalî iş anlamı kazanmaz. Faiz türü/faiz oranı, beyan edilen/doğrulanmış gelir ve başvurulan/onaylanan limit ayrı kavram/kapsamdır. HTML/br ve Türkçe harfler normalleştirilir; çok satırlı kod listeleri desteklenir.

İncelenmiş `definition.facts` sağlayıcısı `verified_metadata` etiketini ancak gerçekten incelenmiş, kaynak gösterilebilir bilgiler için kullanmalıdır. Bu etiket model çıktısına yeniden ad verilerek üretilemez. Parser mevcut metadata doğruluğunu bağımsız olarak ispatlayamaz. Aynı iş anlamındaki farklı pay/payda adlarının eşdeğerliği için ayrıca onaylı eşleme gerekir; MVP bunları otomatik eşitlemez.

`build_dependencies` bütün Excel'i tek geçerli satır kimliği alanında ele alır. Graph düğümü `{row_id,name,definition}`, kenarı `{source,target,role}` biçimindedir. Aynı adın birden fazla satırda olması referansı belirsiz yapar. Eksik `Havale_L3M` tanımı uydurulmaz. İteratif iki geçişli SCC algoritması, büyük dosyalarda recursion sınırına takılmadan bütün döngü üyelerini işaretler. Bir bağımlılığın aday olması, hazır desteklenen metrik olmasına eşdeğer değildir; MVP zincirleme üretimi kesinleştirmez.

`assess_production` sonucundaki `candidate` uygulanacak SQL veya üretim garantisi değildir. Aynı nesne/anahtar doğrulamasıyla bile uzman incelemesi gerekir. `source_only` yalnız uygun işlem türünde hareket seviyeli metadata bulunduğunu söyler; bütün veri ihtiyacının karşılandığını söylemez. Kaynak alanlar ile hazır aggregate alanlar farklı tutulur.

## Semantik doğrulama kaydı

2026-10-11, yerel Windows, Python unittest; banka ağına bağlantı yok.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -p test_semantics.py -v
```

İlk çalıştırma: 31 test geçti (0.008 saniye). İnceleme sonrasında çok satırlı kodlar, SCC kapsamı, oran ve serbest açıklama sonundaki çözümlenmemiş iş kısıtları ve boş metadata engeli güçlendirildi. Son birleşik çalıştırmada **41 semantik + 5 bağımsız Excel/pipeline testi geçti, 0 hata, 0.492 saniye**. Onaylı sidecar ve uçtan uca üretim adayı için ayrıca **8 test geçti** (0.282 saniye). Ayrıntılı bulgular `SEMANTIC_REVIEW.md` içindedir; tümleşik çalıştırma sonuçları ana test raporundadır.

Testler: aynı ad/yanlış anlam, farklı ad/tam kanıt, aynı eksik metnin yetersizliği, model çıkarımının reddi; max/sum ve agregasyon sırası; adet/tutar/kişi/hesap ayrımı; yön/pencere/ters oran; sıfır ve NULL ayrımı; grain/referans tarihi/kişi anahtarı; boş ve çelişkili metadata; Türkçe/HTML/kod listesi; bütün sayfalarda bağımlılık çözümü; yinelenen ad/eksik tanım/döngü/SCC; 1.201 düğüm; doğrulanmış üretim adayı, hazır olmayan bağımlılık ve çapraz nesne join engeli; günlük distinct sayılarının otomatik toplanmaması.

Gerçek pilotta bağımsız uzman etiketli veri gerekir: aynı/similar adlarda zor negatifler, eksik metadata, farklı ortam kopyaları ve oran yönü örnekleri dahil edilmelidir. Kelime, embedding ve hibrit retrieval karşılaştırması aday yakalama ile karar doğruluğunu ayrı ölçmelidir. Demo üzerinden bankacılık doğruluk yüzdesi veya sıfır hata garantisi çıkarılmaz.
