# VeriKöprü API Sözleşmesi

Frontend ve backend paketlerinin TEK sözleşmesidir. Tipler: `frontend/src/api/types.ts` (birebir). Plandan fazla/eksik doldurulan yerler **P0b varsayımı** diye işaretlidir.

## Genel
- Taban: `/api`. JSON, UTF-8. Zaman damgaları ISO-8601 UTC (`2026-10-15T09:30:00Z`).
- Kimlik: çerez `tt_session` (HttpOnly, SameSite=Strict, Secure). Değiştiren istekler (POST/DELETE) `X-CSRF-Token` başlığı ister (değer login/me yanıtındaki `csrf_token`). P0b varsayımı: çerez adı `tt_session`.
- Hata gövdesi her zaman `{"error": "<KOD>", "detail": "<Türkçe mesaj>"}`. Gövdede başka alan, şifre, bağlantı dizesi, traceback yok.
- 422 doğrulama: `{"error":"VALIDATION","detail":"body.user, body.password"}` (yalnız alan yolları, değer yok).
- `GET /api/health` → `{"status":"ok"}` (kimlik gerektirmez).

### Hata kodları
| Kod | HTTP | Anlam |
|---|---|---|
| AUTH_INVALID | 401 | Kullanıcı adı/şifre hatalı |
| AUTH_FORBIDDEN | 403 | Giriş doğru ama grup/Base DN yetkisi yok |
| AUTH_THROTTLED | 429 | Aynı kullanıcı/IP için çok fazla başarısız giriş (5 dk içinde 5) |
| LDAP_UNAVAILABLE | 503 | LDAP sunucusuna ulaşılamadı / zaman aşımı |
| SESSION_EXPIRED | 401 | Oturum yok/süresi doldu |
| CSRF_INVALID | 403 | CSRF başlığı eksik/yanlış |
| VALIDATION | 422 | İstek şeması geçersiz |
| DB_CONNECT_FAILED | 400 | Oracle bağlantısı kurulamadı (şifre, TNS, dinleyici, zaman aşımı) |
| DB_DISCONNECTED | 409 | Bağlantı koptu / hiç kurulmadı |
| DB_PERMISSION | 403 | ORA-01031 / ORA-00942 |
| LIMIT_EXCEEDED | 422 | Satır sayısı `TT_ROW_LIMIT` (5.000.000) üstü |
| TABLE_EXISTS | 409 | Hedef ad dolu (ORA-00955 veya varlık kontrolü) |
| TARGET_LOCKED | 409 | Aynı hedefe eşzamanlı iş var |
| TARGET_DENIED | 403 | Alias hedef olamaz (`TT_TARGET_DENY_ALIASES`) |
| INCOMPATIBLE | 422 | Uyumluluk raporunda `error` var |
| RULE_INVALID | 422 | Kural kolon/hedefle uyumsuz (PK'de random vb.) |
| TRIGGER_PRESENT | 409 | Hedefte ENABLED trigger / şema-seviyesi trigger |
| JOB_RUNNING | 409 | Çalışan iş var (logout, aynı iş) |
| NOT_FOUND | 404 | Kayıt yok / başkasının işi / bilinmeyen yol |
| ORA_ERROR | 400 | Diğer Oracle hataları; `detail` = `ORA-<5 hane>[: Türkçe açıklama]` (P0b varsayımı: kod `ORA_<n>` değil `ORA_ERROR`, numara detail'de) |
| INTERNAL | 500 | Beklenmeyen hata |

ORA eşlemesi: 01017/12154/12514/12541/12170 → DB_CONNECT_FAILED; 03113/03114/03135 → DB_DISCONNECTED; 01031/00942 → DB_PERMISSION; 00955 → TABLE_EXISTS; 00001/01400/12899/30036/01555 → ORA_ERROR (anlaşılır detail).

## Kimlik — `/api/auth`
| Metot Yol | İstek | Yanıt | Hatalar |
|---|---|---|---|
| POST `/auth/login` | `{"username":"mert","password":"..."}` | 200 `{"user":User,"csrf_token":"..."}` + çerez | AUTH_INVALID, AUTH_FORBIDDEN, AUTH_THROTTLED, LDAP_UNAVAILABLE, VALIDATION |
| POST `/auth/logout` | `{"cancel_running":false}` (gövde isteğe bağlı) | 204 | JOB_RUNNING (çalışan iş var ve `cancel_running` verilmedi; detail "işiniz sürüyor", iş iptal edilsin mi?), CSRF_INVALID, SESSION_EXPIRED |
| GET `/auth/me` | — | 200 `{"user":User,"csrf_token":"..."}` | SESSION_EXPIRED |

`User = {"username":"mert","display_name":"Mert Ali","groups":["CN=..."],"is_admin":false}`. Kullanıcı adı `^[A-Za-z0-9._-]{1,64}$`; boş/boşluk şifre → AUTH_INVALID (bind denenmez). P0b varsayımı: logout'ta `cancel_running:true` ise çalışan işler iptal istenir, oturum kapanır.

## Oracle — `/api` (tns + db)
**GET `/tns/aliases?q=`** → 200
```json
{"aliases":[{"alias":"DEMO_TEST","host":"dbhost1","port":1453,"service_name":"DEMOTEST","target_allowed":true},
            {"alias":"DEMOLIVE","host":"scan1","port":1521,"service_name":"DEMO","target_allowed":false}]}
```
`q`: alias içinde arama (büyük/küçük harf duyarsız). `target_allowed=false` → UI hedef listesinde göstermez. (P0b varsayımı: host/port/service_name döner.)

**POST `/db/connect`** `{"role":"source|target","alias":"DEMO_TEST","user":"U","password":"***"}` → 200
```json
{"role":"source","alias":"DEMO_TEST","db_version":"19.0.0.0","user":"U","mode":"thin","direct_path":true,"max_ident_len":128}
```
`mode`: `thin|thick`; `direct_path`: yalnız Thin ve sürüm uygunsa true; `max_ident_len`: 12.1→30, 12.2+→128. Hatalar: DB_CONNECT_FAILED, TARGET_DENIED (role=target ve alias yasaklı), NOT_FOUND (bilinmeyen alias), VALIDATION. Şifre saklanmaz, yanıtta ve logda yok.

**DELETE `/db/connect/{role}`** → 204 (çalışan iş kendi pool'unu tutar, etkilenmez).

**GET `/db/status`** → 200 `{"source":ConnectionInfo|null,"target":ConnectionInfo|null}`; `ConnectionInfo = {role,alias,db_version,user,mode,direct_path,max_ident_len}`. Kopuk bağlantı `null` döner (P0b varsayımı: durum ucu hata atmaz).

**GET `/db/{role}/schemas?q=`** → 200 `{"schemas":["DEMO","HR"]}`. Yalnız bağlanan kullanıcının ALL_TABLES'ta gördüğü (kalıcı) tabloların sahipleri (`SELECT DISTINCT OWNER`); Oracle'ın kendi şemaları (`ALL_USERS.ORACLE_MAINTAINED='Y'`, 12.1+) hariç. Hatalar: DB_DISCONNECTED, DB_PERMISSION.

**GET `/db/{role}/tables?schema=&q=&limit=&offset=`** → 200
```json
{"items":[{"schema":"DEMO","name":"MUSTERI"}],"total":42,"limit":50,"offset":0}
```
Yalnız ALL_TABLES sözlüğü okunur; tablolara SELECT/COUNT atılmaz, satır sayısı ya da tahmini dönmez (kapsam filtreyle belirlenir, bkz. `/transfers/count`). `limit` varsayılan 50, en çok 200; ön yüz 100 ister ve aramayla daraltır. `total` sayfa dolduysa `offset+n+1` ("daha fazlası var"), değilse kesin; yalnız gerçek TABLE'lar listelenir; `schema` zorunlu.

**GET `/db/{role}/tables/{schema}/{table}/columns`** → 200
```json
{"columns":[{"position":1,"name":"PARTY_ID","data_type":"NUMBER","char_length":0,"char_used":null,"data_length":22,
 "precision":12,"scale":0,"nullable":false,"virtual":false,"identity":false,"hidden":false,"default":null,
 "is_pk_or_unique":true,"supported":true,"unsupported_reason":null}]}
```
`char_used`: `"B"|"C"|null`. `supported=false` (LOB/RAW/LONG/XML/TZ'li TIMESTAMP) → UI "taşınamaz", yalnız `exclude`. (`position`, `unsupported_reason`: P0b varsayımı.)

## Taşıma — `/api/transfers`
Ortak parçalar:
- `TableRef = {"schema":"DEMO","table":"MUSTERI"}`
- **Filter** (serbest WHERE yok; satırlar AND ile birleşir — P0b varsayımı):
  `{"column":"BOLGE","op":"eq","value":"IST"}`; `op ∈ eq|ne|lt|le|gt|ge|like|in|between|is_null|is_not_null`. `in` → `"values":[...]` (en çok 1000), `between` → `"values":[alt,üst]`, `is_null/is_not_null` değersiz. Değerler string/number; tarih ISO-8601 string; tür dönüşümü sunucuda, hepsi bind.
  **Kapsam zorunlu:** `count`, `preview` ve `POST /transfers` için ya en az bir filtre ya da açık `"full_table":true` (tablonun tamamı; varsayılan false) gerekir. `full_table:false` ve filtresiz istek → 422 `VALIDATION` "Taşınacak kümeyi belirlemek için en az bir filtre gerekli." `full_table:true` iken `filters` boş olmalı; dolu → 422 `VALIDATION` "Tablonun tamamı seçiliyken filtre verilemez." Tam tablo da 5M tavanına tabidir (aşarsa `over_limit` / LIMIT_EXCEEDED, filtre girilmeli). Tüm satırları seçen (totolojik) koşullar da reddedilir → 422 `VALIDATION` `"'<KOLON>' koşulu tüm satırları seçer; kümeyi daraltan bir koşul girin."` Totoloji kuralları: `is_not_null` NOT NULL kolonda; `like` deseni yalnız `%`/`_` içerip en az bir `%` taşıyorsa; NUMBER(p,s) için M=10^(p-s) ile `gt/ge ≤ -M`, `lt/le ≥ M`, `between` alt ≤ -M ve üst ≥ M, `ne` değeri |v| ≥ M ya da s'den fazla ondalıklı (p yoksa uygulanmaz); DATE/TIMESTAMP için `gt/ge ≤ 0001-01-01`, `lt/le ≥ 9999-12-31`, `between` ikisini kapsıyorsa. Anlamsal olarak "neredeyse hepsi" (ör. `ID > 0`) ayırt edilemez; asıl tavan filtreli sayımın 5M'yi aşmamasıdır (`over_limit`).
- **Rule** (kolon başına): `{"column":"TCKN","type":"num_token","params":{}}`
  | type | params | not |
  |---|---|---|
  | keep | `{}` | olduğu gibi |
  | exclude | `{}` | kolon hedefe yazılmaz |
  | null | `{}` | NULL yazılır |
  | constant | `{"value":"X"}` | sabit değer (string) |
  | mask | `{"show_last":4}` | son N karakter açık, kalanı `*` (P0b varsayımı: `*`) |
  | hmac | `{}` | HMAC-SHA256 hex, hedef uzunluğa sığmalı |
  | num_token | `{}` | yalnız NUMBER kaynak: birebir Feistel sayısal permütasyon; işaret, basamak sayısı, kesir ve ölçek korunur |
  | text_token | `{}` | yalnız metin kaynak: her karakter kendi sınıfında (A-Z, a-z, 0-9, ÇĞİÖŞÜ, çğıöşü) birebir permüte; diğer karakterler (boşluk, noktalama, `@`, `.`) yerinde; uzunluk ve UTF-8 bayt uzunluğu aynen. Tamamı rakam ve 0 ile başlamıyorsa (ya da tek hane) num_token ile aynı sonuç (NUMBER kolonla join korunur); 0 ile başlıyorsa baştaki 0 kalır |
  | date_token | `{}` | yalnız DATE/TIMESTAMP (saat dilimsiz): yıl korunur, yılın günü yıl içinde permüte; gece yarısı (00:00:00) aynen, değilse gün içi saniye permüte; kesirli saniye aynen |
  | date_shift | `{"anchor":"PARTY_ID","max_days":365}` | satır başına tek ofset (±max_days), anchor kolonunun HMAC'inden; `max_days` P0b varsayımı (1..3650) |
  | random | `{}` | tipe uygun rastgele değer |
  Kısıt: PK/UNIQUE kolonda yalnız keep, num_token, text_token, date_token, sığan hmac (aksi RULE_INVALID); NOT NULL hedefte null/boş constant/boşa düşen mask → RULE_INVALID. num_token/text_token/date_token kaynak (ve append'te hedef) kategorisi uymazsa RULE_INVALID; NULL → NULL.
  **Standart katalog** (her veri tipine taban kural; birebir, çıktı kaynak tipi ve uzunluğundan farklı değil, geri çevrilebilir): NUMBER → `num_token`; VARCHAR2/NVARCHAR2/CHAR/NCHAR → `text_token`; DATE/TIMESTAMP → `date_token`; FLOAT/BINARY_FLOAT/BINARY_DOUBLE → standart yok.

**POST `/transfers/count`** `{"source":TableRef,"filters":[Filter],"full_table":false}` → 200 `{"count":1234567,"limit":5000000,"over_limit":false,"timed_out":false}`. Sayım her zaman tavanlıdır: `SELECT COUNT(*) FROM (SELECT 1 FROM "S"."T" [WHERE ...] FETCH FIRST :tt_cap ROWS ONLY)`, `tt_cap = limit + 1`; hiçbir yolda limit+1'den fazla satır okunmaz. Sınır aşılırsa kesin sayı bilinmez: `{"count":null,"over_limit":true,"timed_out":false,...}`. Zaman aşımında `{"count":null,"over_limit":false,"timed_out":true,...}`. İptal: istemci isteği keser (AbortController), sunucu sorguyu iptal eder (P0b varsayımı). Hatalar: DB_DISCONNECTED, RULE/VALIDATION (kolon yok), DB_PERMISSION.

**POST `/transfers/preview`** `{"source":TableRef,"filters":[Filter],"full_table":false,"rules":[Rule]}` → 200
```json
{"columns":[{"column":"TCKN","type":"num_token","before":["123","..."],"after":["987","..."],"null_before":0,"null_after":0}]}
```
`before/after`: ilk 5 satır (string|number|null), `null_*`: bu 5 satırdaki NULL sayısı (P0b varsayımı). Hatalar: RULE_INVALID.

**POST `/transfers/compat`** `{"source":TableRef,"target":TableRef,"rules":[Rule]}` → 200
```json
{"status":"warn","target_is_table":true,"has_enabled_trigger":false,
 "items":[{"column":"AD","level":"warn","message":"Hedef kolon daha kısa (50 < 80)."}]}
```
`status`/`level ∈ ok|warn|error`. Hata kodu atmaz; engeller `error` olarak raporlanır (başlatmada INCOMPATIBLE / TRIGGER_PRESENT olur). Hedef yoksa NOT_FOUND.

**POST `/transfers`** → 202 `{"job_id":"b3f1c9e0-..."}` (uuid4 string; P0b varsayımı)
```json
{"source":{"schema":"DEMO","table":"MUSTERI"},
 "target":{"schema":"TEST","table":"MUSTERI_ANON_20261015","mode":"create"},
 "rules":[{"column":"TCKN","type":"num_token","params":{}}],
 "filters":[{"column":"BOLGE","op":"eq","value":"IST"}],
 "full_table":false,
 "confirm_target":"TEST.MUSTERI_ANON_20261015",
 "confirm_unmasked_live":false}
```
`mode`: `create|append`. `rules` kaynağın TÜM kolonlarını kapsar (eksik kolon → VALIDATION). `confirm_target` elle yazılan `ŞEMA.TABLO` ve hedefle eşit olmalı (aksi VALIDATION). `confirm_unmasked_live`: kaynak alias'ı canlı (deny listesinde) ve `keep` kolon varsa true olmalı (aksi VALIDATION). Sunucu sayımı yeniden (tavanlı) yapar; aşımda LIMIT_EXCEEDED: `full_table` ise "Tablo 5.000.000 satırın üzerinde; tamamı taşınamaz. Filtre girerek kümeyi daraltın.", değilse "Seçiminiz 5.000.000 satırı aşıyor; lütfen filtreyi daraltın." (sayı `TT_ROW_LIMIT`'ten). Tam tablo işinde `jobs.filters = []` saklanır (ön yüz "Tam tablo"yu buradan türetir) ve `transfer_start` denetim detayına ` [tam tablo]` eklenir. Hatalar: LIMIT_EXCEEDED, TABLE_EXISTS, TARGET_LOCKED, TARGET_DENIED, INCOMPATIBLE, RULE_INVALID, TRIGGER_PRESENT, DB_DISCONNECTED, VALIDATION.

**GET `/transfers?mine=true`** → 200 `{"items":[Job]}`. `mine=false` yalnız admin (yoksa AUTH_FORBIDDEN). En yeni önce, en çok 200 (P0b varsayımı).
**GET `/transfers/{id}`** → 200 `Job` (+ `columns`). Başkasının işi → 404 NOT_FOUND.
**POST `/transfers/{id}/cancel`** → 202 `Job` (status `cancelling`; bitmiş işte aynen döner — P0b varsayımı: idempotent).

**Job**
```json
{"id":"b3f1c9e0-...","owner":"mert","status":"running","source_alias":"DEMO_TEST","source_schema":"DEMO","source_table":"MUSTERI",
 "target_alias":"DEMO_DEV","target_schema":"TEST","target_table":"MUSTERI_ANON_20261015","mode":"create",
 "filters":[],"total_rows":1200000,"rows_read":300000,"rows_written":300000,"rows_per_sec":85000.0,"eta_sec":10,
 "error_code":null,"error_detail":null,"created_at":"...","started_at":"...","finished_at":null,
 "columns":[{"column_name":"TCKN","rule_type":"num_token","params":{},"source_type":"NUMBER","position":1}]}
```
`columns` yalnız detay ucunda; listede yok. `status ∈ queued|running|cancelling|done|failed|partial|cancelled`.

### SSE — `GET /transfers/{id}/events` (`text/event-stream`)
Her olay `event: <ad>\ndata: <JSON>\n\n`. 15 sn'de bir `: ping` yorum satırı. Bağlanınca ilk olarak güncel durum olayı gönderilir; terminal olaydan sonra akış kapanır (P0b varsayımı).
| olay | data |
|---|---|
| progress | `{"job_id","status":"running","rows_read","rows_written","total_rows","rows_per_sec","eta_sec"}` |
| cancelling | `{"job_id","status":"cancelling"}` |
| done | `{"job_id","status":"done","rows_written","finished_at","target":{"schema","table"}}` |
| failed | `{"job_id","status":"failed"|"partial","error":"ORA_ERROR","detail":"...","rows_written"}` |
| cancelled | `{"job_id","status":"cancelled","rows_written"}` |
İstemci SSE kopunca 2 sn'de bir `GET /transfers/{id}` ile yoklar.

## Değer çözme — `/api/anon`
**POST `/anon/reveal`** → 200. Giriş yapmış her kullanıcı (CSRF gerekli; girişsiz 401). Oracle'a bağlanmaz, SQL yok, işe bağlı değildir.
```json
{"rule":"num_token","values":["98765432109"," 4521 ","x"]}
→ {"rule":"num_token","items":[{"value":"98765432109","original":"12345678901","error":null},
                               {"value":"4521","original":"1203","error":null},
                               {"value":"x","original":null,"error":"Sayı bekleniyordu."}]}
```
`rule`: `num_token` | `text_token` | `date_token` (başkası 422 VALIDATION). `values`: 1-100 değer, boşluklar kırpılır, boş satırlar atılır, her biri en çok 4000 karakter (aşımda 422 VALIDATION). `value` ve `original` string. num_token: ondalık/ölçek korunur (`12.50` → `12.50`). date_token girdisi `YYYY-MM-DD[ HH:MM:SS[.ffffff]]`, `GG.AA.YYYY[ SS:DD:ss[.ffffff]]` ya da `GG/AA/YYYY[...]`; çıktı girdiyle aynı biçimde. Geçersiz değer (sayı olmayan giriş, 38 basamaktan uzun tamsayı kısmı, geçersiz tarih) istisna değil satır hatasıdır (`original: null`, `error` Türkçe).
Anahtar küresel olduğundan aynı değer tüm tablolarda aynı token. HMAC, maske, sabit, boş (null), rastgele ve tarih kaydırma tek yönlüdür. `TT_ANON_HMAC_KEY` değişirse eski token'lar çözülemez (yanlış değer döner). Her çözme, çözmeden önce `audit_log`'a `reveal` olarak yazılır (`job_id` NULL, `detail`: `"<rule>: <n> değer"`); denetim kaydı yazılamazsa çözme yapılmaz. Değerler ve sonuçlar ne denetim kaydına ne uygulama loguna yazılır.

## Temizlik — `/api/cleanup`
**GET `/cleanup?status=`** → 200 `{"items":[TrackedTable]}`; kullanıcı kendi tablolarını, admin hepsini görür. `status` isteğe bağlı filtre (P0b varsayımı).
```json
{"id":1,"job_id":"b3f1c9e0-...","owner":"mert","alias":"DEMO_DEV","schema":"TEST","table":"MUSTERI_ANON_20261015",
 "object_id":123456,"object_created":"2026-10-15T09:00:00Z","verified_visible_at":"2026-10-15T09:05:00Z",
 "table_status":"active","expires_at":"2026-10-22T09:00:00Z","last_checked_at":null}
```
`table_status ∈ active|expired_present|deleted_verified|unverifiable`.
**GET `/cleanup/drop-script?job_ids=a,b`** → 200 `text/plain; charset=utf-8`, `Content-Disposition: attachment; filename="drop_script.sql"`. Yalnız uygulamanın yarattığı, `expired_present` ve OBJECT_ID+CREATED eşleşen tablolar; başlık yorumlu. Uygun tablo yoksa NOT_FOUND.
**POST `/cleanup/check/{job_id}`** → 200 `TrackedTable` (servis hesabıyla anlık kontrol). Hatalar: NOT_FOUND, DB_CONNECT_FAILED (servis hesabı/alias yok).

## SQLite (WAL) tabloları
`jobs`: id TEXT PK, owner TEXT, status TEXT, source_alias, source_schema, source_table, target_alias, target_schema, target_table, mode TEXT, filters_json TEXT, total_rows INTEGER, rows_read INTEGER, rows_written INTEGER, rows_per_sec REAL, error_code TEXT, error_detail TEXT, created_at TEXT, started_at TEXT, finished_at TEXT.
`job_columns`: id INTEGER PK, job_id TEXT FK, position INTEGER, column_name TEXT, source_type TEXT, rule_type TEXT, params_json TEXT (kural; veri yok).
`tracked_tables`: id INTEGER PK, job_id TEXT FK, owner TEXT, alias TEXT, schema_name TEXT, table_name TEXT, object_id INTEGER, object_created TEXT, verified_visible_at TEXT NULL, table_status TEXT, expires_at TEXT, last_checked_at TEXT NULL.
`table_checks`: id INTEGER PK, tracked_table_id INTEGER FK, checked_at TEXT, visible INTEGER, object_id INTEGER NULL, object_created TEXT NULL, result_status TEXT, error TEXT NULL.
`audit_log`: id INTEGER PK, at TEXT, username TEXT, action TEXT (login|logout|connect|transfer_start|cancel|drop_script|check|reveal), job_id TEXT NULL, detail TEXT NULL (şifre/veri yok).
(Kolon adları P0b varsayımı; sahibi P6'dır, değişiklik burada güncellenir.)
