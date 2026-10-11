# Metadata, yerel model ve arama sözleşmeleri

Bu modüller gerçek banka bağlantısı açmadan geliştirildi ve sentetik/nesne taklidi testlerle sınandı. Sağlanan Qwen adresleri yalnız **base URL** bilgisidir. Model adı, endpoint yolu, istek ve yanıt biçimi, embedding boyutu ve sürümü kurum içinde doğrulanmalıdır. Qwen3.8 ifadesi doğrulanmış bir teknik model kimliği olarak kullanılmaz.

## Oracle envanteri

Web sürümü kullanıcı/parola/DSN bilgisini yalnız bellekte `credentials` ile adapter'a geçirir; global ortam değişkenlerini değiştirmez. Kullanıcı bağlantı testiyle DB/container ve erişilebilir şemaları görür. Analiz yalnız seçilmiş şemaları tarar. `max_records` en fazla 10.000; fazla kayıt görülürse tarama eksik sayılır ve yokluk sonucu engellenir. `use_models:false` ile yalnız gerçek Oracle ad/metin araması çalışır.

`OracleAdapter(config).scan()` yalnız `ALL_TAB_COLUMNS` envanterini, `ALL_COL_COMMENTS` ve `ALL_TAB_COMMENTS` açıklamalarını, isteğe bağlı P/U anahtar kısıtlarını okur. Kolon açıklaması boşsa kolon yine döner. Müşteri satırı, örnek veri veya üretim tablosu sorgulanmaz. `ALL_*` erişim kapsamı veritabanı yetkilerine bağlıdır. `snapshot.scope` kaynak veritabanı, ortam, container ve hedef şemaları taşır. Bağlantının `CON_NAME` değeri zorunlu container ile; `expected_db_name` verilmişse `DB_NAME` değeri de onunla karşılaştırılır. Uyuşmazlık hata verir. `source_database` bir katalog etiketidir; gerçek DB adı denetimi için `expected_db_name` kullanılmalıdır.

Gerekli `oracle` alanları: `source_database`, `environment`, `container`, `schemas` (açık şema listesi), `user_env`, `password_env`, `dsn_env` (üç ortam değişkeni adı). İsteğe bağlı: `expected_db_name`, `wallet_dir_env`, `wallet_password_env`. `wallet_dir_env` verilirse değeri ortamda bulunmalıdır. Her şema filtre değeri SQL'e bind parametresiyle geçer. `SYS`, `SYSTEM`, `OUTLN`, `XDB` hedef olarak reddedilir. Diğer sistem şemalarının listeden çıkarılması kullanıcının kapsam onayına bağlıdır. Tarama yalnız yetkili ve listelenmiş şemalar hakkında bilgi verir. Hedef şemalardan birinde erişilebilir kolon görünmezse snapshot `complete=false` olur; boş sonucu kesin yokluk olarak sunmaz.

`catalog_id` kaynak/ortam/container/şema/nesne/kolondan üretilir; `metadata_version` yorumlar, teknik özellikler ve mevcut anahtar bilgisinin içerik hash'idir. Snapshot sürümü tüm kayıtların sürümlerini içerir. P/U kısıtı yalnız anahtar metadata'sıdır; ilişki, işlem zamanı, hesaplama veya kolon lineage kanıtı değildir. View metni MVP'de okunmaz. `verified_relationships` boş kalır, `definition.facts` uydurulmaz. Anahtar metadata'sı erişilemezse `complete=false`, kayıtların `missing` alanında neden bulunur. Oracle hataları DSN veya sır içermeyen kısa mesaj verir.

Oracle'ın [ALL_TAB_COLUMNS açıklaması](https://docs.oracle.com/en/database/oracle/oracle-database/26/refrn/ALL_TAB_COLUMNS.html) görünümün kullanıcıya erişilebilir tablo/view/cluster kolonlarını anlattığını belirtir; bu, erişilemeyen nesneler için yokluk kanıtı değildir.

## Yerel embedding

Web arayüzünde parola alanları ayrı tutulur. Her iki model adapter'ı bellekte `api_key` alabilir; CLI hâlâ `api_key_env` destekler. İsteğe bağlı `ca_file` kurumsal PEM CA zincirini kullanır; TLS doğrulaması açıktır. Kök vektör listesi için `response_path: "$"` desteklenir. Web bağlantı testi tek doğrulama metni gönderir, vektör boyutunu yanıttan okur. Servis yolu ve model kimliği otomatik tahmin edilmez.

`EmbeddingAdapter(config).embed(texts)` için `endpoint` **tam HTTPS URL**, `allowed_hosts` açık host listesi, `model`, `model_version`, `dimensions`, JSON `request_template` ve `response_path` zorunludur. Örneğin sözleşme doğrulandıktan sonra şablon `{"model":"{model}","input":"{texts}"}`, yol `data[*].embedding` seçilebilir; bu yalnız örnektir, verilen servisin gerçek sözleşmesi olduğu varsayılmaz. Yer tutucular yalnız bütün JSON string değeri olabilir ve liste tipini korur. `response_path` noktalı alanlar ve `[*]` dizisi destekler. Vektör sayısı, boyutu ve sonlu sayısal değerler doğrulanır. `max_batch` 1–64, `timeout_seconds` en çok 60, `max_retries` 0–2. Yanıt 1 MB, istek 512 KB ile sınırlıdır.

## Yerel LLM

`LLMAdapter(config).evaluate(request,candidates)` için `endpoint`, `allowed_hosts`, `model`, `model_version` ve açık `contract` zorunludur. `contract=openai_chat` **yalnız servis sözleşmesi doğrulanırsa** OpenAI chat JSON biçimini kullanır. Diğer sözleşmede `contract=template`, JSON `request_template` ve `response_path` gerekir; `{context}` alanı sınırlı talep ve adayları taşır. Çıktı `{ "assessments": [{ "catalog_id": "...", "evidence": "kaynak metinden aynen alıntı", "note": "..." }] }` biçiminde doğrulanır. Kimlik yalnız verilen adaylardan olabilir; kanıt aynı adayın ad, nesne veya yorum alanının gerçek alt dizisi olmalıdır. Şema dışı `status`, `supported`, puan vb. alanlar reddedilir. Bu doğrulama iş anlamının doğru olduğunu kanıtlamaz; LLM önerileri deterministik semantik destek kararını belirlemez.

`max_candidates` 1–24, `max_chars` her alan için 100–6000, `max_output_tokens` 64–2048. Varsayılanlar sırasıyla 8, 1800, 600'dür. İstek bağlamı, yanıt ve retry sınırları aşılmaz. CLI için anahtar `api_key_env` ile ortamdan, web için doğrudan işlem belleğinden alınır; örnek dosyaya sır girilmez. HTTPS sertifika doğrulaması Python varsayılanıyla açıktır, yönlendirmeler kapalıdır; host allowlist veya URL ihlali bağlantı açılmadan hata verir. Otomatik dış servis fallback'i yoktur.

## HybridIndex

`HybridIndex(snapshot, embedding=None, cache_dir=None)` ve `.search(request, limit=8, strategy='hybrid', expanded=False)` exact fiziksel kolon adı, BM25 token araması ve varsa embedding sonuçlarını reciprocal rank fusion ile sıralar. `strategy` ayrı değerlendirme için `exact`, `bm25`, `embedding`, `hybrid` olabilir. Türkçe harfler ve teknik isimler normalize edilir; kaynak metin değiştirilmez. Snapshot kaynak/ortam/container/şemaları indekslenmeden önce uygulanır. Talebin `scope` veya `schema` filtresi embedding çağrısından **önce** uygulanır; başka şemaların içeriği sorguya girmez. Boş sorgu, kapsam dışı talep veya hiç lexical eşleşme için aday zorlanmaz. Skor doğruluk yüzdesi değildir.

Embedding cache'i kapsam, snapshot imzası, model/sürüm/boyut ve aday kimliklerine bağlıdır; metadata silinmesi/değişmesi eski vektörlerin tekrar kullanımını engeller. Her kapsam ve model sürümü için eski cache dosyası kaldırılır. Kayıt sayısı 10.000, cache dosyası 10 MB ile sınırlıdır. `.assert_fresh(snapshot)` katalog sürümü, kapsamı ve kayıt/sürüm çiftleri değişirse `StaleIndexError` verir. Gerçek metadata cache dizini Graphify taramasına veya dış modele verilmemelidir.

Testler `python -m unittest discover -s tests -v` ile fake transport ve fake Oracle connector kullanır. Bunlar gerçek Oracle/Qwen bağlantısı veya eşleştirme doğruluğu testi değildir.
