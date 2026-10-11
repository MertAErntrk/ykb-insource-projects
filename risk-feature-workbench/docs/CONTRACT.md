# Ortak uygulama sözleşmesi v1

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
JSON: `{mode, oracle:{...}, llm:{...}, embedding:{...}, retrieval:{limit:8, expanded_limit:24}, naming:{windows:{LM:'last_month',LW:'last_week',L2W:'last_2_weeks',L3M:'last_3_months'}}}`. Adapter'ların gerekli alanlarını sahipleri docs/ADAPTERS.md içinde belirtir. Gerçek sırlar yalnız env'den okunur.

## Güvenlik sınırı
Kurum adreslerine bu geliştirme sırasında bağlantı yapılmaz. Sadece açık gerçek-mod komutu bağlanır. TLS doğrulaması açık, bounded timeout/retry/context. Graphify yalnız kod + sentetik doküman staging alanında; kullanıcı girdileri, gerçek config/metadata/cache kapsam dışı. Excel formülleri çalıştırılmaz; rapor hücreleri metin olarak güvenli yazılır.
