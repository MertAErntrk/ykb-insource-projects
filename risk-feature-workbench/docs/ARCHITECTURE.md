# Mimari değerlendirme ve karar kaydı

## 0.5.0 mimarisi (güncel)

Hedef sırası: doğru eşleşme, güvenilir çalışma, makul süre. 0.4'teki "hızlı/tam arama modu" ayrımı kalktı: her satır aynı kanallardan geçer. Eski `search_mode`, `candidate_limit`, `embedding_budget`, `limit`, `expanded_limit` anahtarları yapılandırmada zararsızdır, yok sayılır. Aşağıdaki "0.4.0" ve sonraki bölümler geçmiş karar kaydıdır; arama akışı için bu bölüm esastır.

### Akış

```
Excel (bir kez yüklenir, web.py /api/upload)  ->  satır seçimi (İlk N / Sıradaki 10 / Tümü) -> gruplar (10/25/50)
  -> pipeline.analyze (grup başına; checkpoint)
       1. Katalog:  resources.catalog -> önbellek (imza kontrolü) | Oracle taraması
       2. Embedding: resources.embedding -> model parmak izi (8 kanarya) -> ad alanı (namespace)
       3. İndeks:   HybridIndex (katalog sürümü + ad alanı + ayar); dondurulmuş anlamsal küme
       4. Satır başına havuz (retrieval.py): exact ad | sözlükle normalize ad | alan ağırlıklı BM25 | anlamsal
            -> sıra birleştirme (RRF) -> en çok pool_size (30) aday
       5. Sıralama + gösterim kapısı (ranking.py): z-skoru, kosinüs, kelime örtüşmesi, konu kelimesi
            -> en çok 3 aday (0/1/2 de olabilir) + reddedilen yakın adaylar ayrı listede
       6. LLM (isteğe bağlı): yalnız gösterilen aday var ve hiçbiri meaning=strong değilse
       7. İş sonu: 2 kanaryayla model yeniden doğrulaması -> değişmişse tüm satırlar model_changed
  -> web: sonuçlar akışla (partial_results) gelir -> Excel yazılır (excelio.py) -> "Tamamlandı"
```

Not: "Skor" yüzde değildir; sıralama için kullanılır, doğruluk olasılığı değildir.

### Modüller

| Modül | Sorumluluk |
|---|---|
| `web.py`, `web_services.py` | Flask + Waitress, yalnız 127.0.0.1. Oturum, CSRF, iş yönetimi (tek işçi), akış, durdur, rapor, indeks paneli. |
| `pipeline.py` | Grup analizi: katalog, embedding bağlamı, indeks, satır döngüsü, LLM, checkpoint, model yeniden doğrulaması, `run_info`. Disk/kilit hatalarında checkpoint ve son çalıştırma yazımı hataya dayanıklıdır. |
| `resources.py` | Süreç geneli paylaşılan kaynaklar: `Resources` (katalog önbelleği, tek vektör deposu, parmak izli ad alanları, en çok 2 indeks LRU), `EmbeddingGate` (tek uçuş), `BackgroundIndexer`. |
| `catalog_cache.py` | Katalog anlık görüntüsü önbelleği (bellek + `runtime` altında JSON). Anahtar: kullanıcı+DSN+kapsam+şema karması (ham değerler saklanmaz). Yalnız tam (`complete`) katalog yazılır. |
| `vectorstore.py` | SQLite vektör deposu, ad alanı başına; model parmak izi (kanarya) ve doğrulama; bozulursa `tainted` işaretlenir. |
| `retrieval.py` | `HybridIndex`: kanallar, havuz, dondurulmuş anlamsal küme, devre kesici, kapsam bilgisi. |
| `ranking.py` | Gösterim kapısı (`GATE_DEFAULTS`), `name_match` ve `meaning` sınıfları, farklar ve kanıt, `row_status`. |
| `lexicon.py`, `data/lexicon.json` | Kısaltma/eş anlam sözlüğü; ad normalizasyonu. |
| `semantics.py` | Boyut bazlı (varlık, pencere, yön, ...) deterministik karşılaştırma; çelişki ve eksik kanıtı `differences` olarak verir. |
| `excelio.py` | Excel okuma ve rapor yazma (geçici dosya + doğrulama + `os.replace`). |
| `adapters/oracle.py`, `adapters/qwen.py` | Katalog okuma (yalnız metadata) ve embedding/LLM çağrıları. |

### Önbellek ve geçersiz kılma

- **Katalog:** ömür 8 saat (`Resources.catalog_ttl_seconds=28800`). Önbellek varsa önce ucuz bir Oracle imzası alınır; imza saklananla aynıysa önbellek kullanılır, farklıysa veya imza alınamazsa yeniden taranır. İmza alınamazsa yeni tarama önbelleğe yazılmaz. Eksik (`complete=False`) katalog hiç önbelleğe yazılmaz. "Kataloğu yenile" (`/api/catalog/refresh`) girdiyi siler ve yeniden tarar.
- **Vektörler:** metin karması başına bir kez, model ad alanı altında saklanır. Parmak izi: 8 kanarya metninin vektörleri saklananlarla karşılaştırılır; yeterince benzerse (kosinüs ve Gram farkı eşikleri `vectorstore.py` içinde) aynı ad alanı, değilse yeni ad alanı açılır. Bağlam parmak izi 600 sn önbelleklenir. `VectorStore.prune` vardır ancak uygulama kodunda çağrılmaz; eski ad alanlarının otomatik temizliği yoktur.
- **İndeks:** anahtar = katalog sürümü + ad alanı + indeks ayarları. Katalog sürümü, model veya ayar değişince yeni indeks kurulur.
- **Model değişimi:** iş sonunda 2 kanaryayla yeniden doğrulama; farklıysa ad alanı `tainted` olur, ilgili bağlam/indeks/indeksleyiciler düşer ve işteki tüm satırlar `model_changed` işaretlenir (Excel'de uyarı). Doğrulama yapılamazsa iş "Eksik" sayılır.
- **Checkpoint:** grup başına; yalnız hatasız, embedding'i atlanmamış, `model_changed` olmayan ve aynı indeks kapsamıyla üretilmiş satırlar yeniden kullanılır.

### Hata yolları

| Durum | Davranış |
|---|---|
| Oracle zaman aşımı (`DPY-4024`), izin (`ORA-00942`/`ORA-01031`), kimlik hatası | Güvenli kodla bildirilir; ham sürücü metni gösterilmez. Okunmuş kayıtlar korunur. |
| Katalog hiç okunamadı | Arama başlamaz, sonuç satırı üretilmez ("Katalog okunamadı"). |
| Katalog eksik (kısmi) | Arama sürer; `catalog_complete=false`; satır `none_in_scope` olamaz, "DB'de yok" denmez. Önbelleğe yazılmaz. |
| Embedding 401/403/404, 429, 5xx, zaman aşımı | 429/500/502/503/504 ve ağ hatası `max_retries` (varsayılan 1, en çok 2) kadar yeniden denenir; sonra `IntegrationError` (HTTP kodu mesajda, ham yanıt yok). Satır için embedding atlanır (`embedding_skipped`), ad ve kelime kanıtıyla devam edilir. |
| Bozuk vektör (sayı/boyut uyuşmuyor) | `IntegrationError`; aynı şekilde atlanır. |
| Devre kesici | Arama sırasında art arda 3 embedding hatası -> `circuit_open`: iş sonuna kadar (sonraki iş başında sıfırlanır) embedding çağrılmaz, uyarı yazılır. Arka plan indeksleyici art arda 5 hatada durur (durum `failed`). |
| LLM hatası | Kaynak kanıtı silinmez; yalnız `model_review` eksik kalır. |
| Rapor yazılamaz (dosya açık, izin yok, disk dolu, doğrulanamadı) | `ReportWriteError`; mevcut rapor değişmez; sonuçlar ekranda kalır; iş `report_failed`, "yeniden dene" (`/api/jobs/<id>/report`) ile tekrarlanır. |
| Checkpoint/vektör deposu diske yazılamaz | Çalışma sürer, `run_info` uyarısı yazılır; vektör deposu hataları `VectorStoreError` olarak sarılır. Disk kilidi/dolu için ayrıntı davranışı kurumda **doğrulanmadı**. |

### Eşzamanlılık

- **Tek uçuş kapısı:** `EmbeddingGate` aynı anda en çok bir embedding çağrısı gönderir; bekleyen arama çağrıları bekleyen indeks çağrılarından önce gider (arama önceliği).
- **Arka plan indeksleyici:** eksik katalog metinlerini kapıdan düşük öncelikle, vektörleştirir (daemon iş parçacığı; durumlar: running, done, stopped, failed; başlat/durdur/ilerleme/kalan süre web panelinden). Aynı anahtar için tek indeksleyici paylaşılır.
- **Dondurulmuş anlamsal küme:** indeksin anlamsal kümesi kurulurken ve `refresh_index()` ile sabitlenir. Çalışan bir işin kümesini indeksleyici değiştirmez; bu yüzden bir satırın havuzu yalnız (satır, katalog, dondurulmuş küme) üçlüsüne bağlıdır ve satır sırasından bağımsızdır. Yeni vektörler sonraki işte görünür.
- **Web:** oturum başına tek etkin analiz (ikincisi 409); iş sayısı üst sınırı 20, yüklenen dosya üst sınırı 30.

### Yapılandırma (`retrieval`)

`pool_size` (5–60, varsayılan 30), `lexical_k` (5–200, 40), `semantic_k` (0–200, 30), `shortlist_embed` (0–50, 10), `background_size` (16–512, 128), `gate` (sözlük; boşsa `ranking.GATE_DEFAULTS`: `z_min` 2.5, `c_min` 0.5, `z_strong` 4.0, `c_low` 0.3, `margin` 1.5, `lexical_min_coverage` 0.8, `lexical_min_tokens` 2, `max_shown` 3, `max_rejected` 5, `require_subject` true). `gate` içinde bilinmeyen anahtar hata verir.

## 0.4.0 büyük katalog optimizasyonu (geçmiş kayıt)

Önceki akış ilk değişkenin embedding aramasında bütün katalog vektörlerini hazırlıyor, sonra her sorguyu bütün vektörlerle Python'da karşılaştırıyordu. 273 satır, 273 katalog kaydı değildir: 10.000 kolon ve 4096 boyutta bir geçiş 2,73 milyon vektör karşılaştırması oluşturur. İkinci maliyet, üretim kanıtı değerlendirmesinde aynı katalog tanımlarının her talep için tekrar çözümlenmesiydi.

Web varsayılanı iki aşamalıdır: kapsam içindeki tam ad ve ters kelime indeksiyle BM25 kısa listesi; ardından yalnız en fazla 24 aday üzerinde embedding sıralaması. Tam adlar kısa listede önceliklidir. Kelime eşleşmesi yoksa rastgele kolonlar modele gönderilmez. İş başına en fazla 256 farklı katalog metni vektörleştirilir; bütçe sonunda ad/metin kanıtları korunur, modelin atlandığı açıkça raporlanır. Bu bir yaklaşık aday bulma stratejisidir; anlamsal geri çağırımın aynı kaldığı iddia edilmez. Fiziksel ad kontrolü bütün okunmuş katalogda kalır; kısa listede aday olmaması semantik yokluk kanıtı değildir.

Aynı analizde eş metinler, normalize edilmiş float32 vektörler ve sorgu vektörleri yeniden kullanılır. Genişletilmiş aday değerlendirmesi aynı vektörleri kullanır. Model kimliği bilinmeyen servis için analizler arası kalıcı yeniden kullanım hâlâ kapalıdır. Ortak vector DB veya kurum servisinde model kurulumu yapılmaz. İlk embedding hatasından sonra hızlı mod kalan çağrıları atlar; ham hata/sır taşınmaz. LLM yorumu ayrı seçilir ve varsayılan kapalıdır; başarısız yorum kaynak kanıtlarını silmez. Hızlı web model çağrıları 15 saniyelik bağlantı/okuma timeout'u ve sıfır otomatik tekrar kullanır; bütün iş için kesin duvar saati garantisi değildir.

Kullanıcı tam anlamsal aramayı açıkça seçebilir. Bu yol bütün okunmuş katalog vektörlerini üretir ve uzun sürebilir; ilerleme artık servis grupları bazında görünür. Her iki modda işbirlikli iptal mevcut çağrı döndükten sonra yeni işi engeller. Sonuçlar tamamlandıkça oturuma ait arayüzde yayınlanır; bağımlılık/üretim değerlendirmesi bitene kadar kısmi olarak etiketlenir. İptal edilen iş tamamlanmış Excel raporu gibi indirilemez. Hızlı mod checkpoint yazımları birinci satırda, ardından 10 satır veya 5 saniyelik aralıklarla birleştirilir.

Oracle taraması ilk hazırlıkta hâlâ gereklidir; katalog erişim ve 10.000 kolon sınırı korunur. Bir sonraki kurumsal mimari adımı, model revision'ı sabitlenmiş, kullanıcı yetkileriyle filtrelenen ve değişen/silinen metadata'yı takip eden ortak indeks olabilir. Bu sürüm bu altyapının kurulduğunu iddia etmez.

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
