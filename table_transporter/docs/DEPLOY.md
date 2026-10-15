# Dağıtım

## Yerel çalıştırma (Windows)
```powershell
cd backend; py -3.12 -m venv .venv; .venv\Scripts\python -m pip install -r requirements.lock; cd ..
Copy-Item backend\.env.example backend\.env   # TT_SESSION_SECRET ve TT_ANON_HMAC_KEY (>=32 bayt, farklı) ve TT_TARGET_DENY_ALIASES (canlı alias) doldurun
.\run_local.ps1 [-Port 8000] [-SkipBuild]
```
Betik frontend'i derler, `TT_FRONTEND_DIST`'i ayarlar, `TT_COOKIE_SECURE` tanımsızsa `false` yapar (http localhost) ve tek worker ile uvicorn başlatır. Adres: `http://verikopru.localhost:8000` (Chrome/Edge `*.localhost`'u yerel makineye çözer; olmazsa `http://127.0.0.1:8000`).

## İmaj build
```bash
podman build -f Containerfile -t verikopru:1 .
# Banka iç registry'si:
podman build -f Containerfile \
  --build-arg NODE_IMAGE=registry.example.local/node:24-slim \
  --build-arg PYTHON_IMAGE=registry.example.local/python:3.12-slim \
  -t registry.example.local/ns/verikopru:1 .
podman push registry.example.local/ns/verikopru:1
```
`backend/requirements.lock` varsa onunla, yoksa `requirements.txt` ile kurulur. `.containerignore` gizli dosyaları (`.env*`, `config/tnsnames.ora`, `config/service_accounts.toml`) imaja sokmaz.

## OpenShift
```bash
oc new-project verikopru
# tnsnames.ora (Secret değil, ConfigMap; manifestlerde içerik yok)
oc create configmap verikopru-tns --from-file=tnsnames.ora=config/tnsnames.ora
# Hedef olamayacak canlı alias'lar (zorunlu; depoda tutulmaz, yoksa pod başlamaz)
oc create configmap verikopru-site --from-literal=TT_TARGET_DENY_ALIASES=<CANLI_ALIAS>
# Gizliler
oc create secret generic verikopru-secrets \
  --from-literal=TT_SESSION_SECRET="$(openssl rand -base64 48)" \
  --from-literal=TT_ANON_HMAC_KEY="$(openssl rand -base64 48)" \
  --from-file=service_accounts.toml=config/service_accounts.toml
# Önce deploy/openshift/configmap.yaml (LDAP_*, FORWARDED_ALLOW_IPS), deployment.yaml (image) ve
# route.yaml (host: verikopru.<apps alan adı>) düzenlenir
oc apply -k deploy/openshift
oc get route verikopru   # https://verikopru.<apps alan adı>
```
`TT_ANON_HMAC_KEY` değişirse aynı veri farklı anonimleştirilir (join bozulur); yedekleyin, değiştirmeyin.

## X-Forwarded-For (`FORWARDED_ALLOW_IPS`)
Giriş kısıtlaması (`request.client.host`) gerçek istemci IP'sine bakar. Pod, Route/HAProxy arkasında olduğundan uvicorn `--proxy-headers` ile çalışır ve `FORWARDED_ALLOW_IPS`'i yerel olarak okur. Değer router pod'larının geldiği küme ağı CIDR'ı olmalıdır (varsayılan yer tutucu `10.128.0.0/14`; kümenize göre değiştirin).

`*` kullanılmaz: uvicorn güvenilen her kaynakta zincirin en solundaki değeri alır; bu değeri istemci kendisi uydurabilir, böylece giriş kısıtlaması her istekte farklı sahte IP ile atlatılır. Güvenilen aralık dar tutulunca router'ın eklediği gerçek değer kullanılır.

## Neden replicas=1, Recreate
Oturumlar süreç içi, veritabanı SQLite (RWO PVC) ve hedef kilidi süreç içi; iki pod tutarsızlık ve DB bozulması yaratır. `Recreate` eski pod durmadan yenisini başlatmaz. Ölçeklemeyin.

## Pod yeniden başlarsa
- Oturumlar düşer; kullanıcılar yeniden giriş yapar.
- `running` işler açılışta `recover_on_startup` ile `failed`/`partial` olur; yeniden başlatılır.
- İş geçmişi ve izleme `/data` PVC'sinde kalır.

## Yedekleme
`/data/verikopru.db` (SQLite) tek durum kaynağıdır. Tutarlı yedek için:
```bash
oc rsh deploy/verikopru python -c "import sqlite3;s=sqlite3.connect('/data/verikopru.db');d=sqlite3.connect('/data/backup.db');s.backup(d)"
oc cp verikopru/<pod>:/data/backup.db ./backup.db
```
Ya da PVC snapshot / altyapı yedeği kullanın. Dosyayı çalışırken düz kopyalamayın.

## Oracle Thick / NNE
İmajda Instant Client yok (Thin mod). Sunucu Native Network Encryption ya da Thin'in desteklemediği özellik gerektiriyorsa: Instant Client'ı imaja ekleyin (Containerfile'a ekleme gerekir), `TT_ORACLE_THICK_LIB_DIR` ile dizini verin ve `libaio` paketini kurun.
