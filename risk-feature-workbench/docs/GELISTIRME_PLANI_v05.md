# Risk Feature Workbench v0.5 — geliştirme planı

Öncelik: doğru eşleşme → güvenilir çalışma → makul süre → düşük geliştirme ve model maliyeti.

## Her paketin akışı
1. **Uygula:** brief (amaç, sahip olunan dosyalar, sözleşme, kabul, bütçe) → builder. Model işin ağırlığına göre seçilir.
2. **Doğrula (ana oturum):** `git diff` ile sahiplik kontrolü, ilgili testler, gerekirse ölçüm. Ajan raporu kanıt sayılmaz.
3. **İncele:** yazarı olmayan reviewer. Riskli alanda (eşzamanlılık, veri bütünlüğü) Opus, diğerlerinde Sonnet.
4. **Düzelt:** bulgular doğrulanır. On satıra kadar düzeltmeyi ana oturum yapar; daha büyükse yeni paket açılır.
5. **Kaydet:** bu dosyadaki durum satırı güncellenir.

Model seçimi:
- Haiku: salt-okunur tarama.
- Sonnet: tek modül, mekanik iş, belge.
- Opus: modüller arası mantık, eşzamanlılık, kalite açısından kritik sıralama.

## Faz 0 — Ölçüm ve teşhis (tamamlandı)
- Kök nedenler ölçüldü:
  - Satır başına 20–24 aday gösteriliyordu.
  - Karşılığı olmayan 10 satırın 9–10'unda öneri çıkıyordu.
  - Her grupta katalog ve indeks yeniden hazırlanıyordu.
  - Bütçe uyarısı ilk 3 adayı keserek gizleniyordu.
- Ölçüm aracı (`scripts/evaluate.py`) yazıldı. Ölçütler: Recall@K, Hit@3, P@3, ilk doğru sıra, YOK'ta öneri, süre, tutarlılık.

## Faz 1 — Yeni arama çekirdeği (tamamlandı, doğrulandı)
| Paket | İçerik | Ajan / model | Doğrulama | İnceleme |
|---|---|---|---|---|
| P1–P3 | sözlük, katalog önbelleği, vektör deposu | builder / Sonnet | birim testleri | ana oturum |
| P4a | hibrit arama (exact, normalize, BM25, anlamsal, z-skoru) | builder / Opus | test + ölçüm | Faz 3 |
| P4b | paylaşılan kaynaklar, tek uçuş kapısı, arka plan indeks | builder / Opus | test | P9 (Opus) |
| P5 | sıralama ve gösterim kapısı (≤3 aday) | builder / Opus | test + ölçüm | Faz 3 |
| P5b | konu kelimesi koşulu | builder / Opus | ölçüm: e5 ve hash'te YOK'ta öneri 0/10, Hit@3 korundu | Faz 3 |
| P6/P6b | pipeline yeniden yazımı, eski testlerin uyarlanması | ana oturum + builder / Opus | tam paket | P9 (Opus) |
| P8a/P8b | web arka uç ve ön yüz (satır seçimi, gruplar, akış, durdur, yeniden bağlanma) | builder / Opus + Sonnet | test + tarayıcı | P9 (Opus) |
| P10 | ölçüm aracı ve iş PC doğrulama betiği | builder / Sonnet | test | ana oturum |

## Faz 2 — Güvenilirlik düzeltmeleri (tamamlandı)
Kaynak: P9 incelemesinin 8 bulgusu (pipeline, resources, web).

| Paket | İçerik | Ajan / model | Durum |
|---|---|---|---|
| P11a | web: 409 sırası, Tamamlanma yeniden hesabı, gruplar arası birleşik durum ve `model_changed`, iş sürerken bağlantı kesme, `failed` işte kısmi rapor, sweep | builder / Opus | tamam; 7 kabul testi; tam paket 352/352 |
| P11b | pipeline: checkpoint ve son çalıştırma yazımı hataya dayanıklı; doğrulanamayan model kontrolü işi "Eksik" yapar. resources: indeksleyici çalışan işin kümesini tazelemez | ana oturum | tamam; 22 test + yeni disk-dolu testi geçti |
| P11r | P11a + P11b diff incelemesi | reviewer / Opus | tamam: 1 önemli (rapor hatasından sonra başka iş satırları silebiliyor) + 3 küçük → P11a yazarı düzeltti; inceleme betiği artık 10/10 satır, web testleri 61/61 |

## Faz 3 — Eşleşme kalitesi incelemesi
| Paket | İçerik | Ajan / model | Durum |
|---|---|---|---|
| P12r | retrieval.py + ranking.py + lexicon (P4a, P5, P5b) için taze göz | reviewer / Sonnet | tamam: 4 önemli + 3 küçük bulgu |
| P12 | P12r düzeltmeleri: havuz kesimi grup bazında, margin için anlam sınıfı, gömmesiz kelime kuralı, küçük katalog z, kısa kök soyma | builder / Opus | tamam. Bulgu 3 daraltıldı: tek kelime yalnız kolon adında geçerse yeter. Doğrulama: tam paket 369/369. Ölçüm (Hit@3 / P@3 / YOK'ta öneri, tutarlılık 1.0): e5 0.951 / 0.983 / 0/10; hash 0.976 / 0.992 / 0/10; gömmesiz 0.854 / 0.99 / 0/10 |

Ek olarak sözleşmeye uyum, sıra bağımsızlığı ve eşik dışı sızıntı kontrol edilir.

## Faz 4 — Uçtan uca ve ölçüm
- **P13 Tarayıcı (ana oturum, geliştirme düzeneği) — tamam.** Giriş, Excel yükleme, İlk 1 → Sıradaki 10 (yeniden yükleme yok), Tümü 3 grupta, akışlı ilerleme, durdurma → kısmi sonuç/kısmi rapor/indirme, sayfa yenileme → yeniden bağlanma, ekrandan giriş (eşleşen + karşılıksız satır), indeks paneli (başlat, ilerleme, kalan süre, durdur). Bulunan arayüz hatası yok.
- **P14 Ölçüm (builder / Sonnet).**
  - Koşular: 1, 5, 10 ve 273 satır; hash ve e5.
  - Sıra tutarlılığı.
  - Zor varyantlar: sözlük kapalı, bozulmuş adlar.
  - Sonuçlar `docs/TEST_RESULTS.md` dosyasına yazılır.
  - Ön ölçüm (P12 öncesi) yazıldı; 273 satırda Hit@3 düştü (hash 0.863, e5 0.804).
- **P17 273 satır ıska analizi (reviewer / Opus) — tamam.**
  - Havuzda doğru aday her zaman var (Recall@havuz 1.0).
  - 21 ıska düzenek hatası: ek satırların tipi hep "Sayısal".
  - 4 ıska "gün sayısı"nın adet sayılması; 2 ıska yorumsuz birebir adın arkaya düşmesi.
  - "Sözlük kapalı" varyantı yapısal listeyi de boşaltıyordu.
  - Düzeltmeler: P17a düzenek (builder / Sonnet), P17b sıralama + toplama yorumu (builder / Opus) — tamam.
  - Düzenek düzelince 14/54 YOK satırında öneri göründü: "bayrak/flag" konu kelimesi sayılıyordu. Ana oturum "bayrak"ı yapısal listeye ekledi (+1 test).
  - Doğrulama: tam paket 374/374. Hit@3 / P@3 / YOK'ta öneri (tutarlılık hepsinde 1.0):
    - 51 satır: hash 0.976 / 0.992 / 0/10; e5 0.951 / 0.983 / 0/10; gömmesiz 0.854 / 0.99 / 0/10 (değişmedi).
    - 273 satır: hash 0.977 / 0.976 / 0/54 (önce 0.863); e5 0.922 / 0.951 / 0/54 (önce 0.804).
  - İsteğe bağlı İngilizce kısaltma eklemeleri yapılmadı.
- **P14f Son ölçüm (builder / Sonnet) — tamam.**
  - Tüm koşular son koddan sonra yeniden yapıldı; sonuçlar `docs/TEST_RESULTS.md`'de.
  - 273 satır, 5.803 kolon: hash 0.977 / 0.974 / 0/54; e5 0.913 / 0.950 / 0/54. Tutarlılık 1.0 (grup 25/10/1 ve ters sıra).
  - Varyantlar (Hit@3, hash / e5): `nolex` 0.918 / 0.895; `nolex_full` 0.904 / 0.776; `garbled` 0.795 / 0.699.
  - Ana oturum: satır sayısı ifadesi düzeltildi. e5'te 0.922 ile 0.913 farkı dolgu tablo sayısından (120 / 220); süre birimi düzeltmesi e5 sonucunu değiştirmedi.

## Faz 5 — Belgeler ve teslim
- **P15 Belgeler (builder / Sonnet) — tamam.**
  - Güncellenecekler: ARCHITECTURE, CONTRACT, IS_BILGISAYARINDA_BASLANGIC, README, config örnekleri.
  - Sürüm 0.5.0.
- **P16 Son inceleme (reviewer / Opus) — tamam (araç sınırıyla kısmi kapsam).**
  - 1 önemli bulgu: "gün sayısı" süresi birimsiz kalıyordu; aynı tanım bile "supported" olamıyordu.
  - 1 küçük bulgu: "son 3 ay adet" süre sanılıyordu; "günü sayısı" ayrı sınıfa düşüyordu.
  - Ana oturum düzeltti: süre birimi yazılır, pencere arkasındaki kelime hariç, iyelik biçimleri dahil; Türkçe birim etiketleri. Testler eklendi; tam paket 374/374.
  - Sorun bulunmayanlar: birebir ad sıralaması, hata yolu belgesi, 20 uç nokta, Tamamlanma, Python 3.9 sözdizimi, yeni bağımlılık yok.
  - Ana oturum taraması: Oracle sorguları yalnız sözlük görünümleri (ALL_OBJECTS, ALL_TAB_COLUMNS, yorumlar, kısıtlar); kaynakta sır ve gerçek adres yok (testlerde yalnız .invalid).
- **P16b (reviewer / Sonnet) — tamam.** İş bilgisayarı belgeleri ↔ betik ve önbellek dosyaları incelendi.
  - Önemli: `config/real.json` git dışında değildi, kurum değerleri yanlışlıkla commit edilebilirdi.
    - Düzeltme: `.gitignore`'a eklendi; örnek dosyanın notu aynı ada hizalandı.
  - Embedding anahtar ortam değişkeni belgede yoktu.
    - Düzeltme: belgeye eklendi; betik değişken yoksa uyarır. PS 5.1 ayrıştırması temiz.
  - Bozuk kanarya kaydı parmak izinde ValueError veriyordu.
    - Düzeltme: o kayıt atlanır ve yeni ad alanı açılır (+1 test).
  - Düzeltilmeyen küçükler:
    - Çöken yazımdan kalan `.tmp` dosyaları temizlenmiyor.
    - İki ayrı süreç aynı anda ad alanı açarsa biri hata alır; uygulama tek süreç olduğu için kabul edildi.
- **Teslim.**
  - Türkçe son rapor.
  - Commit d40a7a9, yerel `risk-feature-workbench-v05` dalında. Tam paket 375/375.
  - Push etmeden önce dal kullanıcıya sorulur. origin/main 4 BriefMind commit'i önde; bu projeye dokunmuyorlar.

## Faz 6 — Kurum ortamında başarı ölçümü
İş bilgisayarındaki ilk koşu (EDWLIVE / DM_CAD, 7.410 kolon, 273 satır): ekranda "Tamamlandı" yazdığında anlamsal indeks %71'deydi. Analiz, başladığı andaki indeksle çalışıyor. Kullanıcı eşleşmeleri zayıf buldu; önce ölçülecek, sonra geliştirilecek.

| Paket | İçerik | Ajan / model | Durum |
|---|---|---|---|
| P18a | evaluate.py gerçek mod: `--resources-dir` (web indeksini yeniden kullanır), boş etiket = etiketsiz, TABLO.KOLON çözümü, satır bazlı ıska teşhisi (`satirlar.json`, `iskalar.md`), katalog özeti (`katalog_ozet.*`), kapsam uyarısı | builder / Opus → reviewer / Sonnet | tamam. İnceleme: 2 küçük bulgu (atlanan satırlar görünmüyordu). Ana oturum düzeltti: girdi hatasıyla atlanan satırlar raporda; etiket sütunu olmayan sayfalar ölçüm dışı (+1 test) |
| P18b | `scripts/etiket_sablonu.py`: web sonuç Excel'inden etiketleme dosyası (aday yolları hazır, tabakalı örnek) | builder / Sonnet → reviewer / Sonnet | tamam. İncelemenin "kritik" bulgusu eski `parse_label`'a göreydi (P18a ile çözüldü). Ana oturum: şablon → `load_rows` testi eklendi, 2 parçalı aday notu |
| P18c | Ölçüm aracı web ile aynı bağlantı ve model ayarlarıyla (`login_settings` + `probe_oracle` + `discover_model` + `build_run`); config dosyası gerekmez | builder / Opus → 2 reviewer / Opus (sır sızıntısı; web eşdeğerliği + PS 5.1) | tamam. İlk reviewer sonuç döndürmeden durdu; inceleme iki dar bakışla yeniden koşuldu: 3 küçük bulgu. Ana oturum düzeltti: kullanıcının kendi şeması listelenmez, sır karşılaştırması büyük/küçük harf duyarsız, göreli `-Excel`/`-Config` çağrılan klasöre göre çözülür; ek olarak `-EmbeddingModel`/`-LlmModel` (+2 test). Tam paket 397/397; sentetik 51 satır değişmedi (hash 0.976 / 0.992 / 0/10). Kurum ağında koşulmadı |

Ölçümden sonra (kullanıcı onayıyla):
- İndeks şema seçilince başlasın; eksik indeksle çalıştırmada sorulsun; iş "Tamamlandı (kısmi anlamsal kapsam)" olsun; kısmi satırlar için "Yeniden ara".
- Kalite: ıska sınıflarına göre (sözlükte olmayan kısaltmalar, boş kolon yorumları, z eşiği, konu kelimesi kuralı).

## Faz 7 — Katmanlı eşleştirme ve türetme açıklaması
İstek: karşılık DM_CAD (INP_/OUT_ hariç) → DM → EDW sırasıyla aransın. Önce tablo açıklaması, sonra kolon açıklaması okunsun. Oran değişkenleri (`X_LW_L3M` = `X_LW / X_L3M`) bileşenleriyle açıklansın. Sonuç beş sınıfta verilsin: doğrudan, türetilebilir, belirsiz, bulunamadı, karar verilemedi. Plan Fable (architect) ile yapıldı, kullanıcı onayladı.

| Paket | İçerik | Ajan / model | Durum |
|---|---|---|---|
| G | Graphify `update` + `affected`; config: `retrieval.schema_tiers`, `oracle.exclude_objects`, `naming.ratio_rule`, kayıt sınırı ayarlanabilir (üst 100.000) | ana oturum | tamam |
| P0 | Medallion sahte kataloğu: DM_CAD.MVA_CUSTOMER_MONEY_XFER_DAILY (EFT/havale/SWIFT × tutar/adet × W1W/W1M/W3M), DM ve EDW benzerleri, INP_/OUT_ tuzakları | builder / Sonnet | tamam. Kapsam alanları snapshot ile aynı olacak şekilde ana oturum düzeltti |
| P1 | Sözlük ve anlam: L=son (LM, LW, L3M), W1M kolon son eki, EFT/havale/SWIFT/WU ayrımı, oran kuralı, talep birim söylemiyorsa TL notu | builder / Opus | tamam |
| P2 | SQL'de INP_/OUT_ dışlaması, katman sırasıyla tarama, şema bazlı tamlık, önbellek sürümü, web'de seçim sırası | builder / Sonnet | tamam |
| P3 | Önce-tablo kanalı (tablo belgesi mevcut embedding endpoint'iyle, BM25 yalnız yedek), seçilen tabloların kolonlarına anında embedding, kanıt satırı | builder / Opus | tamam. İki test beklentisi mevcut sözcük kanalı davranışına göre düzeltildi |
| P4a | `derivation.py`: katman planı, karar, oran türetme, Türkçe gerekçe metinleri | builder / Opus | tamam |
| P4b | Pipeline'a bağlama ve kabul testleri A1–A5 | builder / Opus | kısmi döndü (A3). Ana oturum tamamladı: oran talebine taban olguları, varlık notu, bileşen dönemi karşılaştırması. Ek olarak açıklamayla en az 4 boyutu doğrulanan aday z eşiği altında da "zayıf" olarak gösterilir (`gate.fact_min`) |
| P5 | Excel sütunları (Sonuç, Kaynak, Dayanak, Türetme yöntemi), web rozet/filtre/detay, DM_CAD/DM/EDW varsayılan seçili, evaluate sonuç dağılımı | builder / Sonnet | tamam. Tarayıcıda elle denenmedi |
| R | Tüm değişikliğin incelenmesi | reviewer / Opus | tamam. 5 bulgu, hepsi ana oturumda düzeltildi: (1, yüksek) farklı tabanlı oran kolonu yalnız dönem eklerinin uyuşmasıyla "doğrudan" çıkabiliyordu. Artık işlem türü ya da kavram açıklamayla doğrulanmalı; `fact_min` ad kuralı olgularını saymıyor. (2) İndeks, taramanın dışlama kuralını snapshot'tan alıyor. (3) Kaynağı olmayan bileşen "aranan katmanlarda bulunamadı", "aday var, doğrulanamadı" ya da "aranamadı" diye ayrılıyor. (4) Web'de bileşen dönemi Türkçe. (5) Katalog anahtarına varsayılan dışlama kuralı giriyor. +2 test |

Ölçüm (hash embedding, sentetik):
- 273 satır: Hit@3 0.977 → 0.982, P@3 0.974 → 0.974, karşılığı olmayan satırlarda öneri 0/54.
- 51 satır: 0.976 / 0.983 / 0/10 (önceki koşuyla aynı).
- Sonuç dağılımı (273): doğrudan 28, belirsiz 187, karar verilemedi 58. "Karar verilemedi" satırları sentetik kapsamda DM ve EDW olmadığı içindir. "Belirsiz" satırların çoğunda kolon birimi ya da açıklama boyutu doğrulanamıyor. Doğrudan sınıfı için anlam desteği şart tutuldu; benzerlik skoru tek başına yetmiyor.
- Tam paket 467/467. İnceleme düzeltmelerinden sonra ölçüm değişmedi.

## Faz 8 — Sabit şemalar ve tam indeks kapısı
İstek: eşleştirme indeks %100 olduktan sonra başlasın. Şema seçimi kalksın: risk alanı için DM_CAD, DM ve EDW taranır; kullanıcı yalnız kullanıcı adı ve parola girer.

| Paket | İçerik | Ajan / model | Durum |
|---|---|---|---|
| P1 | Pipeline kapısı: model açıkken indeks eksiksiz olana kadar bekler (iptal edilebilir, yüzde ve kalan süre), durmuş indeksi en çok 3 kez yeniden başlatır, tamamlanamazsa arama yapmaz. `HybridIndex.queue_counts()` (tablo + kolon) | builder / Opus | tamam |
| P2 | Web: tarama kapsamı sabit (DM_CAD → DM → EDW ∩ görünür şemalar); istemciden gelen şema listesi yok sayılır; görünmeyen katman uyarısı; indeks durumu `complete` | builder / Sonnet | tamam |
| P3 | Arayüz: şema seçici kalktı, taranacak şemalar yazı olarak; bağlanınca embedding testi ve indeks kendiliğinden; embedding varsayılan açık, LLM varsayılan kapalı | builder / Sonnet | tamam. Tarayıcıda elle denenmedi |
| R | İnceleme | reviewer / Opus | tamam. 4 bulgu, ana oturumda düzeltildi: (1, orta) indeks beklemesi tek analiz yuvasını tutuyordu; artık iş, bağlantının indeksi bitene kadar yuva dışında bekler, sonra sıraya girer. (2) `index_start` önbellekten dönen aynı indeksleyiciyi durdurmuyor. (3) `complete` kapıyla aynı ölçütü (tablo + kolon) kullanıyor. (4) Bekleme yüzdesi yeniden başlatmada sıfırlanmıyor. +4 test |

Tam paket 486/486. Ölçüm değişmedi (hash embedding, sentetik): 273 satırda Hit@3 0.982, P@3 0.974, öneri 0/54; 51 satırda 0.976 / 0.983 / 0/10.

### Faz 8 ek: indeks paneli ve hız
- Panel tüm katalog belgelerini sayar (`a / b belge`, tablo + kolon), katalog okunurken "Hazırlanıyor" gösterir, çalışırken tahmini kalan süreyi yazar. Kapının beklediği indeksleyici panelde görünür; yeniden bağlanılmışsa yeni bağlantının indeksine dokunulmaz.
- İndeksleyici adaptörün `max_batch` değeriyle toplu gönderir (açık model 16; servis varsayılanı 1). Sahte servisle 3.164 belge: 3.164 yerine 210 istek. Aynı çalışma klasörüyle yeniden açılışta 0 belge yeniden embed edildi.
- Bekleme metni bir dakikanın altında saniye yazar. `evaluate.py` web moduyla aynı kapsamı (DM_CAD, DM, EDW) ve tam indeks koşulunu kullanır; `-Schemas` isteğe bağlı.
- Servis varsayılanı kipinde bağlantı testi toplu isteği dener (16, olmazsa 4): vektör sayısı ve sırası (ortadaki bilinen metin) doğrulanırsa o boyut kullanılır. Analizde toplu istek reddedilirse (400/413/422 ya da eksik vektör) metinler tek tek gönderilir; üç redden sonra tek metne düşülür. Yanıt sınırı vektör boyutuna göre büyür (varsayılan 1 MB; 4096 boyut × 16 ≈ 1,4 MB).
- İş bilgisayarı ölçümü (kullanıcı çıktısı, 4096 boyut): toplu istek 32'ye kadar sıralı; tek metin 8,5 metin/sn, 16'lı toplu 38,6 metin/sn. Tanılama: `scripts/embedding_teshis.py`.
- Rehber: [IS_PC_KURULUM_AYARLARI.md](IS_PC_KURULUM_AYARLARI.md). Tam paket 493/493; ölçüm değişmedi (0.982 / 0.974 / 0/54; 0.976 / 0.983 / 0/10). Tarayıcıda görsel kontrol yapılmadı.

## Kurum ortamında doğrulanmamış olanlar
- Gerçek Oracle katalog boyutu ve tarama süresi.
- Gerçek embedding modelinin kimliği ve kalitesi.
- LLM katkısı.
- Kurum etiketli Excel ile gerçek doğruluk.

Bunlar `scripts/is_pc_dogrulama.ps1` ile iş bilgisayarında ölçülür.
