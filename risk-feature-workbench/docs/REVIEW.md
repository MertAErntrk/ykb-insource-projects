# Bağımsız adversarial inceleme

İnceleyen: `graphify_review` alt ajanı. Kapsam: Graphify sorgusuyla kaynak ilişkileri, aday destek kapısı, Oracle açıklamaları/anahtarları, embedding cache, Excel taşıma ve devam ettirme. Graphify entegrasyonu aynı ajan tarafından yazılmıştır; bu bölüm uygulamanın diğer ajanlarca yazılan kodunun bağımsız incelemesidir.

## Yeniden üretilen bulgular ve çözüm

| Öncelik | Bulgu ve somut etki | Düzeltme / doğrulama |
|---|---|---|
| P1 | Net gelir ve brüt gelir aynı tanım sayılıyordu. `Başvuru bazında beyan edilen net gelir TRY.` / `Başvuru bazında beyan edilen brüt gelir TRY.` → `supported`. Kredi kartı limiti / tüketici kredisi limiti aynı hatayı üretiyordu. | Semantik ajan kontrollü ifade tüketimi ekledi; tanımsız niteleyici `unparsed_description_qualifier` olarak kesin eşleşmeyi engeller. İki özgün repro tekrar çalıştırıldı: ikisi de `insufficient`. Gerçek doğruluk oranı iddia edilmez. |
| P1 | Oracle adapter boş `definition.facts` ürettiği için kaynak kolon açıklamaları semantik parser'a hiç ulaşmıyordu. | Adapter gerçek `column_comment` üzerinden `parse_definition` çağırıyor, özgün açıklamayı koruyor. Kaynak kod ve adapter testleri kontrol edildi. |
| P1 | Oracle bağlantı ve metadata çağrısı sınırsız bekleyebiliyordu. | `tcp_connect_timeout` ve `connection.call_timeout` eklendi; canlı Oracle'a bağlanılmadı. |
| P1 | Devre dışı veya validate edilmemiş PK/U kısıtları üretim anahtarı kanıtı olarak taşınabiliyordu. | Envanter SQL `STATUS='ENABLED' AND VALIDATED='VALIDATED'` ile sınırlandı. Bu nesne anahtarıdır, kolon lineage değildir. |
| P2 | Aynı model adı/sürümüyle embedding endpoint veya request template değiştiğinde eski katalog vektörü cache'den alınabiliyordu. | Adapter konfigürasyonundan `cache_identity` hash'i eklendi. Cache kimliği ve finite numeric doğrulaması kaynakta kontrol edildi; retrieval testleri çalıştırıldı. |
| P1 | Dört minimum boyutun değeri boş string olup kaynak `verified_metadata` olduğunda boş=boş karşılaştırması `supported` üretiyordu. | Semantik ajan boş string/whitespace/list/dict/bool değerleri missing yaptı; unhashable değer çökmesini kapattı. Beş özgün repro tekrar çalıştırıldı: tümü `insufficient`. `zero_policy=0` geçerli kalır. |
| P2 | Qwen önerisi checkpoint'te saklanıyor, Excel'e taşınmıyordu. | Parent Excel Adaylar sayfasına model yorumu ve verbatim kaynak alıntısını ayrı sütunlar olarak ekledi. Modelin deterministik karar olmadığı açıkça etiketli. Kaynak kodda doğrulandı. |
| P2 | İlk row_id bütün workbook byte hash'ine bağlıydı; bir hücre değişikliği tüm kimlikleri değiştiriyordu. | Parent `prepare_requests` kimlikli çalışma kopyasını ekledi; `Talep Kimliği` kullanıcı düzenlemesinde korunur. `test_ids_persist_through_edit_of_prepared_copy` içeren Excel testleri 3/3 geçti. Hazırlanmamış farklı workbook bytes aynı kimlik sayılmaz. |

## Kontrol edilen güvenli davranışlar

- Yüksek retrieval skoru ve fiziksel isim tek başına `supported` yapmaz; kritik boyutlar ayrı kapıdan geçer.
- Boş tanım, ters oran, farklı zaman/kayıt seviyesi ve model çıkarımı yeterli kanıt sayılmaz.
- Oran DAG'si tüm satırlar üzerinde deterministik kurulur; yinelenen adlar keyfi ilk satıra bağlanmaz; eksik Havale_L3M uydurulmaz.
- Excel formülleri kod olarak çalıştırılmaz. Sonuç ve kopyalanan girdi metinleri açık string hücresi olarak yazılır; kullanıcı görüş/hazırlama süresi korunur.
- Başlığı bulunamayan dolu sayfa satırları input_errors olarak görünür; sonuç 'tüm dosya tamamlandı' diye gizlenmez.
- Checkpoint girdi bytes, katalog gerçek içeriği, uygulama kaynağı, config ve arama stratejisine bağlanır; hata içeren satırlar yeniden denenir. Metadata değişikliği ve silinme eski kararı taşımaz.
- Raw network/driver/credential exception metinleri sonuç/log'a yazılmaz; kurum ağına bağlanılmadı.
- Graphify runtime ağ/model/alt süreç engelleri gerçek negatif testle doğrulandı; HTML CDN bağımlılığı hash kontrollü yerel vendor'a alındı.

## Kanıt ve kapsam sınırı

İnceleme sırasında net/brüt ve ürün tipi false-positive repro'ları düzeltme öncesi `supported`, düzeltme sonrası `insufficient` verdi. Boş onaylı değer repro'su da kapatıldı. Sonrasında adapter 10/10, retrieval 6/6, Excel 3/3 ve pipeline 6/6 testleri bağımsız çalıştırılarak geçti (bu sayılar o andaki dosya durumudur; nihai toplam ana ajanın `TEST_RESULTS.md` dosyasındadır). Pipeline testleri partial kapsamı, embedding timeout'unu, silinen kolonu, kesilen checkpoint'ten devamı ve gerçek bağlantı hatasının demoya dönmemesini doğrular. Graphify ilk güvenli entegrasyon testleri 7/7 geçti. Kaynak değişince Graphify manifest testi bilinçli olarak başarısız olur; final refresh gerekir.

Oracle/Qwen canlı hizmet testleri yapılmadı. Demo embedding bir hashing simülatörüdür. Graphify `EXTRACTED/INFERRED` kenarları semantik/DB doğruluğu kanıtı değildir. SQL envanter erişimi ALL_* kapsamıyla sınırlıdır. Pilot için bağımsız uzman etiketli talep seti ve retrieval recall/abstention değerlendirmesi gereklidir.
