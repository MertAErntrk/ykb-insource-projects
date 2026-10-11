# Ortak uygulama sözleşmesi v1 (0.5.0 eklemeleriyle)

## 0.5.0 sonuç sözleşmesi (güncel)

Aşağıdaki "Veri" bölümündeki `Candidate` ve `Result` tanımlarının üzerine 0.5 alanları eklenmiştir; eski alanlar korunur. Anahtarlar `pipeline.py` ve `ranking.py` çıktısıdır.

### Satır sonucu (`results[i]`)

| Alan | Anlam |
|---|---|
| `row_id` | Talep kimliği (Excel satırından türetilir). |
| `row_status` | Birincil sonuç, aşağıdaki tabloya bakın. |
| `candidates` | Gösterilen adaylar, en çok `gate.max_shown` (3). 0, 1 veya 2 de olabilir. |
| `rejected_candidates` | Eşiği geçemeyen yakın adaylar (en çok `max_rejected`, 5); her birinde `rejected_reason`. Sonuç değil, açıklama içindir. |
| `coverage` | `{catalog_complete, semantic_scope: full\|partial\|none, index_coverage, pool_size, catalog_records}`. |
| `retrieval_info`, `retrieval_warnings`, `pool_ids` | Kanal sayıları, `embedding_skipped`, `circuit_open` benzeri uyarılar; havuzdaki katalog kimlikleri. |
| `model_review` | Yalnız LLM çalıştıysa: `assessments` listesi. Kanıt yerine geçmez. |
| `model_changed` | İş sırasında embedding modeli değiştiyse `true`. |
| `physical_name`, `semantic_status`, `production_status`, `reasons`, `dependencies`, `errors`, `expert_decision` | Eski alanlar. `semantic_status` artık özet/uyumluluk alanıdır. |

### `row_status` anlamları

| Değer | Anlam |
|---|---|
| `found` | En az bir aday gösterim kapısından geçti. |
| `weak_only` | Yalnız zayıf aday var; doğrulanmadan kullanılmamalı. |
| `none_in_scope` | Katalog tam ve anlamsal kapsam tam; seçili kapsamda güvenilir aday yok. |
| `none_partial` | Aday yok ama kapsam eksik (katalog veya indeks); "DB'de yok" denmez. |
| `not_evaluated` | Satır incelenemedi (girdi veya teknik hata); `errors` içine bakın. |
| `not_searched` | Yalnız web listesi ve Excel'de: satır henüz aranmadı. |

### Aday (`candidates[i]`, `rejected_candidates[i]`)

`catalog_id`; `source` `{schema, object, column, data_type, column_comment, table_comment}`; `locations` (aynı kolonun diğer konumları); `name_match` (`exact` | `normalized` | `partial` | `none`); `meaning` (`strong` | `likely` | `different` | `weak` | `no_description`); `differences` (`[{dimension, requested, actual, text}]`); `evidence` (metin satırları); `features` `{z, cos, coverage, channels}`; `score` ve `retrieval` (kanal listesi: exact, normalized, lexical, semantic); eski `path`, `checks`, `status`, `reasons`. Reddedilenlerde ek olarak `rejected_reason`. `name_match` (ad benzerliği) ile `meaning` (iş anlamı uyumu) ayrıdır; ad birebir aynı olup anlam farklı olabilir. `score` yüzde veya olasılık değildir.

LLM yalnız gösterilen aday var ve hiçbirinin `meaning` değeri `strong` değilse çağrılır; sonuç `model_review` içinde ayrıca durur.

### Excel çıktısı (`excelio.write_results`)

Sayfalar:
1. **Sonuçlar:** `Talep Kimliği`, `Kaynak Sayfa`, `Kaynak Satır`, girdideki özgün sütunlar, `Satır durumu`, `Kapsam`, her aday için (1–3) `Aday n kaynak`, `kolon açıklaması`, `ad eşleşmesi`, `iş anlamı`, `farklar ve kanıt`, `diğer konumlar`; sonra `Fiziksel ad kontrolü`, `Semantik uyum`, `Üretim ihtimali`, `Gerekçe`, `Satır hataları`, `Bağımlılıklar`, `Uzman kararı` (liste: İncelenmedi, Onaylandı, Reddedildi, Ek bilgi bekleniyor). Aranmamış satırlar "Aranmadı" görünür; model değişmişse `Satır durumu` uyarı taşır.
2. **Adaylar:** gösterilen ve reddedilen adaylar (`Gösterim` sütunu), tam kaynak yolu, şema/tablo/kolon, açıklamalar, ad eşleşmesi, iş anlamı, arama skoru (olasılık değil), model yorumu.
3. **Çalışma bilgisi:** kapsam, tamamlanma, hatalar, uyarılar, sürümler, zaman (`Bilgi`/`Değer`).
4. **Kanıtlar:** `Talep Kimliği`, `Katalog Kimliği`, `Boyut`, `Uyum`, talep/katalog değeri ve kaynak metinleri, kanıt türleri.
5. **Girdi N <sayfa adı>:** yüklenen çalışma kitabının tüm sayfaları (formüller metin, çalıştırılmaz).

Yazım: geçici dosya -> satır sayısı doğrulama -> `os.replace`. Giriş dosyasının üzerine yazılmaz. Hata türleri: `locked`, `permission`, `disk_full`, `invalid`, `other`.

### Web API (`web.py`; yerel, oturum+CSRF korumalı, JSON)

| Yöntem ve yol | Amaç / önemli alanlar |
|---|---|
| GET `/` | Arayüz. |
| POST `/api/connect` | Oracle girişi + şema adayları. |
| GET `/api/connection` | Bağlantı kapsamı veya `{ok:false}`. |
| POST `/api/disconnect` | Oturum bağlantısını ve parolayı siler; indeksleyiciyi durdurur. |
| POST `/api/profile` | Ayar alanlarını temizleyip döndürür. |
| POST `/api/probe/<oracle\|llm\|embedding>` | Bağlantı testi (embedding/LLM: model keşfi ve küçük gerçek çağrı). |
| POST `/api/diagnostics/oracle-schemas` | Seçili şemalar için katalog erişim testi. |
| POST `/api/models/<name>` | Servisin model listesi. |
| POST `/api/upload` | `.xlsx` yükler (form `file`); `{id, filename, sheets}`. Excel bir kez yüklenir. |
| GET `/api/uploads/<id>/rows` | Satır listesi: `{rows:[{row_id, sheet, excel_row, name, description, category, searched, row_status, row_status_label}], total, searched}`. |
| POST `/api/analyze` | İş başlatır (202, `{id, upload_id}`). Alanlar: `upload_id` veya `input_type:"manual"` + `variables`; `row_ids` (seçili satırlar, yoksa tümü); `group_size` (1–100; arayüzde 10/25/50); `mapping` (sütun eşleme); `settings` (bağlantı/model). Etkin iş varsa 409. |
| GET `/api/jobs/latest` | Oturumun son işi (`id, status, upload_id`); sayfa yenilenince yeniden bağlanma. |
| GET `/api/jobs/<id>?since=N` | Durum: `status`, `progress {stage, completed, total, group, groups}`, `results` (N'den sonraki yeni satırlar; akış), `result_count`, `elapsed_seconds`, `report_ready`, `report_error`. |
| POST `/api/jobs/<id>/cancel` | Durdur; kısmi sonuçlar korunur. |
| POST `/api/jobs/<id>/report` | Raporu (yeniden) yazar; kısmi rapor dahil (`complete`, `report_failed`, `cancelled`, sonuçlu `failed`). |
| GET `/api/jobs/<id>/download` | Excel indirir (`report_ready` gerekir). |
| POST `/api/index/start` | Arka plan indekslemeyi başlatır (embedding ayarı gerekir). |
| GET `/api/index/status` | `{state: idle\|running\|done\|stopped\|failed, done, total, failures, rate_per_s, eta_s, message, index_coverage}`. |
| POST `/api/index/stop` | İndekslemeyi durdurur. |
| POST `/api/catalog/refresh` | Katalog önbelleğini siler, yeniden tarar; `{columns, source}`. |

İş durumları: `running`, `cancelling`, `reporting`, `complete`, `cancelled`, `failed`, `report_failed`. "Tamamlandı" yalnız Excel yazıldıktan sonra görünür; rapor yazılamazsa `report_failed` olur, sonuçlar ekranda kalır.

---

## Eski v1 sözleşmesi (alanlar korunur; 0.5 ile genişletildi)

Python >=3.9, paket `riskmatch`, src düzeni. Hazır Windows kurulumu ve hash kilitleri Python 3.9.7 x64 ile doğrulanır. Dahili API JSON uyumlu dict kullanır; harici kaynaklar doğrulanır. Dosya sahipliği PLAN.md içinde.

## Veri
Request: `{row_id, sheet, excel_row, name, description, data_type, category, original: {header:value}, definition}`.
Definition: `{facts: {dimension: {value: JSON, source: str, evidence: str}}, issues: [str], formula: null|{op:'divide', numerator:str, denominator:str, zero: number|null, null: str|null}}`.
Fact source: `description`, `naming_rule`, `verified_metadata`, `model_inference`. Eksik bilgi fact yokluğu; model çıkarımı doğrulama değildir.
Boyut adları: `entity, grain, concept, transaction, direction, measure, aggregation, grouping, window, window_bounds, reference_date, person_key, unit, codes, zero_policy, null_policy, numerator, denominator, business_scope`.
Catalog: `{catalog_id, source_database, environment, container, schema, object, column, object_type, data_type, column_comment, table_comment, metadata_version, captured_at, missing, definition, key_columns:[], verified_relationships:[]}`. Kararlı kimlik kaynak+ortam+container+şema+nesne+kolon hash'i; sürüm içerik hash'i.
Snapshot: `{records:[], scope:{source_database, environment, container, schemas:[]}, complete:bool, errors:[], version:str, captured_at:str}`.
Candidate: `{catalog_id, path, score, retrieval:[], checks:[{dimension,status:'compatible'|'conflict'|'missing',requested,actual,evidence}], status:'supported'|'conflict'|'insufficient', reasons:[]}`. score doğruluk değildir.
Result: `{row_id, physical_name:'present'|'absent_in_scope'|'unknown', semantic_status:'supported'|'insufficient'|'conflict'|'not_found_in_scope'|'incomplete', production_status:'not_evaluated'|'candidate'|'insufficient'|'source_only', reasons:[], candidates:[], dependencies:[], errors:[], expert_decision:''}`.

## Modül API'leri
`semantics.parse_definition(name, description, naming=None) -> Definition`
`semantics.evaluate_candidate(request, record) -> Candidate` (request/record definition varsa kullan, yoksa parse et)
`semantics.build_dependencies(requests) -> {nodes:[], edges:[], issues:{row_id:[str]}}` (tüm Excel satırlarını işler; yinelenen ad belirsiz; döngü kontrolü)
`semantics.assess_production(request, results_by_id, dependency_graph, records) -> {status,reasons,dependencies}`
`retrieval.HybridIndex(snapshot, embedding=None, cache_dir=None)`; `.search(request, limit=8, strategy='hybrid', expanded=False) -> list[record enriched with _score,_retrieval]`; `.assert_fresh(snapshot)`
`adapters.oracle.OracleAdapter(config).scan() -> Snapshot` (lazy import; envanter SQL read only)
`adapters.qwen.EmbeddingAdapter(config).embed(texts) -> list[list[float]]`
`adapters.qwen.LLMAdapter(config).evaluate(request,candidates) -> dict` (aday kimliği ve verbatim kanıt kontrolü; karar yetkisi yok)
`riskmatch.errors`: ConfigError, IntegrationError, StaleIndexError, InputError. Mesajlar secrets içermez.
`config.load_config(path=None, mode='demo') -> dict`; gerçek mod config zorunlu, otomatik fallback yok.
`excelio.read_requests(path) -> list[Request]`; `excelio.write_results(input_path, output_path, requests, results, run_info)`.
`pipeline.analyze(input_path, output_path, *, mode='demo', config_path=None, state_dir=None, resume=True, strategy='hybrid') -> dict` returns `{results, run_info, output}`.

## Konfigürasyon
JSON: `{mode, oracle:{...}, llm:{...}, embedding:{...}, retrieval:{limit, expanded_limit (eski, yok sayılır), pool_size, lexical_k, semantic_k, shortlist_embed, background_size, gate}, naming:{windows:{LM:'last_month',LW:'last_week',L2W:'last_2_weeks',L3M:'last_3_months'}}}`. Adapter'ların gerekli alanlarını sahipleri docs/ADAPTERS.md içinde belirtir. Gerçek sırlar yalnız env'den okunur.

## Güvenlik sınırı
Kurum adreslerine bu geliştirme sırasında bağlantı yapılmaz. Sadece açık gerçek-mod komutu bağlanır. TLS doğrulaması açık, bounded timeout/retry/context. Graphify yalnız kod + sentetik doküman staging alanında; kullanıcı girdileri, gerçek config/metadata/cache kapsam dışı. Excel formülleri çalıştırılmaz; rapor hücreleri metin olarak güvenli yazılır.
