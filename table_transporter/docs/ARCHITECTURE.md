# VeriKöprü Mimarisi

Ekiplerin Oracle DB'ler arasında anonimleştirilmiş tablo taşımasını sağlayan web uygulaması. Sözleşme: `docs/API.md`.

## Katmanlar
- `frontend/` React + Vite + TS sihirbazı (Giriş, Bağlantılar, Tablo, Kolonlar, Hedef, Özet, İlerleme, Geçmiş/Temizlik). Yalnız `/api` ile konuşur; tipler `src/api/types.ts`.
- `backend/app/main.py` FastAPI fabrikası (`--factory`), güvenlik başlıkları, tek biçimli hata gövdesi.
- `auth/` LDAP (AD) girişi + süreç içi oturum + CSRF. `oracle/` tns, tanımlayıcı, guard, SQL şablonları, SafeConnection, havuz, sözlük, uçlar.
- `anon/` kurallar, Feistel FPE, önizleme. `transfer/` filtre, pipeline, uyumluluk, servis, SSE, uçlar.
- `log/` SQLite (WAL) iş kayıtları. `cleanup/` 7 gün kontrolü, DROP script üretimi.

## Silmeme garantisi (7 katman)
1. SQL yalnız `sql_templates.py` sabit şablonlarından `Stmt` olarak üretilir; serbest SQL yok.
2. `SafeConnection` ham str kabul etmez; ham connection/cursor, callproc/callfunc dışarı açılmaz.
3. Guard: tek ifade; SELECT, WITH..SELECT, INSERT, CREATE TABLE (kolon tanımlı; CTAS/OR REPLACE yok); `;` ve yorum yok; değerler bind.
4. Tanımlayıcılar fullmatch + sözlükle doğrulanır, çift tırnakla yazılır.
5. `import oracledb` yalnız safe_connection/pools; AST taraması yasak çağrı ve sözcükleri yakalar; DROP yalnız `cleanup/drop_script.py` (DB modülü import etmez).
6. Append hedefi gerçek TABLE ve ENABLED trigger'sız; şema-seviyesi trigger varsa CREATE reddedilir.
7. Filtre serbest WHERE değil: kolon + operatör + bind değer.
Garanti uygulama katmanındadır; DB yetkisi değildir.

## Veri akışı
Okuyucu thread `fetch_df_batches` (Arrow ~100k satır) -> `Queue(maxsize=4)` -> anonimleştirme -> yazıcı (create: direct path; append: `executemany`, tek transaction, commit en sonda; hata/iptalde rollback). Okuma `FETCH FIRST :limit ROWS ONLY` ile 5.000.000 üst sınırlıdır. Create'te tablo yaratılır yaratılmaz `tracked_tables`'a yazılır (write-ahead izleme). İlerleme SSE ile akar, 2 sn polling yedeği vardır.

## Oturum ve kimlik
LDAP simple bind (`KFS\kullanici`, LDAPS) + Base DN altında tam 1 sonuçlu arama. Sunucu tarafı oturum (HttpOnly/SameSite=Strict/Secure çerez, `X-CSRF-Token`, 30 dk boşta TTL). Oracle şifresi saklanmaz: `create_pool`'a bir kez verilir, `Session.resources["source"|"target"]` yalnız havuz referansı tutar. Çalışan iş kendi havuz referansını tutar; logout işi öldürmez (sorulur). İş uçlarında sahiplik kontrolü (başkasının işi 404). Canlı alias (`TT_TARGET_DENY_ALIASES`) yalnız kaynak olabilir.

## Dağıtım
Yerel: `run_local.ps1` (uvicorn tek worker, `TT_FRONTEND_DIST` ile frontend build'i FastAPI sunar). OpenShift: çok aşamalı Containerfile, Deployment replicas=1 (süreç içi oturum + SQLite), PVC `/data`, ConfigMap (tnsnames.ora, gizli olmayan ayar), Secret (servis hesapları, HMAC, oturum anahtarı), Service, Route (edge TLS). Pod yeniden başlayınca kullanıcılar yeniden girer; açılışta `running` işler `failed/partial` işaretlenir.
