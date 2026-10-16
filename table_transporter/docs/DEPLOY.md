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

## OpenShift (imaj kümede build edilir)
PC'de yalnız `git` ve giriş yapılmış `oc` gerekir; Python, Node ve Docker gerekmez. Komutlar Windows PowerShell'de, `ykb-insource-projects` klonunun `table_transporter` klasöründen çalıştırılır (`cd ykb-insource-projects\table_transporter`). Gizli değerler depoya ve dosyaya yazılmaz; yalnız kümede (ConfigMap/Secret) durur.

**0) Ortamı tanı**
```powershell
oc project -q                                   # doğru dev namespace'i mi?
oc auth can-i create buildconfigs               # yes olmalı
oc auth can-i create builds --subresource=docker   # yes olmalı (Docker stratejisi)
oc auth can-i create buildconfigs --subresource=instantiatebinary   # yes olmalı (start-build --from-archive)
oc describe quota                               # pod 0.5-2 CPU / 1-4 Gi, build 1 CPU / 2 Gi ister
oc get storageclass                             # "(default)" yoksa pvc.yaml'a storageClassName gerekir
```

**1) Ortama özgü ayarlar (bir kez)**
```powershell
# Hedef olamayacak canlı alias'lar, virgülle (zorunlu; yoksa pod başlamaz)
oc create configmap verikopru-site --from-literal=TT_TARGET_DENY_ALIASES=<CANLI_ALIAS>
# Kendi tnsnames.ora dosyan (içerik depoda yok)
oc create configmap verikopru-tns --from-file=tnsnames.ora=<tnsnames.ora yolu>
# Oturum ve anonimleştirme anahtarları: rastgele üretilir, ekrana yazılmaz
function New-Key { $b = New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b) }
$k1 = New-Key; $k2 = New-Key
oc create secret generic verikopru-secrets "--from-literal=TT_SESSION_SECRET=$k1" "--from-literal=TT_ANON_HMAC_KEY=$k2"
Remove-Variable k1, k2
```
tnsnames.ora ya da canlı alias listesi sonradan değişirse aynı komutu `--dry-run=client -o yaml | oc apply -f -` ile verip `oc rollout restart deployment/verikopru` çalıştırın.

`TT_ANON_HMAC_KEY` değişirse aynı veri farklı anonimleştirilir (join bozulur) ve eski değerler çözülemez; şifre kasasına yedekleyin, değiştirmeyin: `oc extract secret/verikopru-secrets --keys=TT_ANON_HMAC_KEY --to=-`.

İsteğe bağlı: `deploy\openshift\configmap.yaml` içinde `LDAP_ADMIN_GROUPS` (yönetici AD grubu; CN ya da tam DN, virgülle) ve `LDAP_ALLOWED_GROUPS` (boş = Base DN'deki herkes). Servis hesabı (7 gün kontrolü) sonradan eklenebilir; yoksa pod açılır, tablolar "doğrulanamadı" kalır:
```powershell
oc set data secret/verikopru-secrets --from-file=service_accounts.toml=<service_accounts.toml yolu>
oc rollout restart deployment/verikopru
```

**2) İmajı build et**
```powershell
oc apply -f deploy\openshift\build.yaml
git archive --format=tar.gz -o "$env:TEMP\verikopru.tar.gz" HEAD
oc start-build verikopru --from-archive="$env:TEMP\verikopru.tar.gz" --follow
```
`git archive` yalnız commit'li dosyaları paketler (bu klasörden çalıştırılınca yalnız bu klasörü, kökte olacak biçimde paketler; `HEAD:table_transporter` yazmayın, alt klasörde boş arşiv üretir); yereldeki `tnsnames.ora`, `.env` ve `service_accounts.toml` kümeye gitmez. Küme Docker Hub, npm ya da PyPI'ye çıkamıyorsa build `FROM`, `pnpm install` ya da `pip install` adımında düşer: `build.yaml`'daki `buildArgs` bloğunu iç registry ve ayna adresleriyle açıp `oc apply -f deploy\openshift\build.yaml` ve `oc start-build` komutlarını yineleyin. Bu değerler BuildConfig'te düz metin durur: ayna adresine kullanıcı:şifre gömmeyin.

**3) Uygula ve aç**
```powershell
oc apply -k deploy\openshift
oc rollout status deployment/verikopru --timeout=5m
oc get route verikopru -o jsonpath="{.spec.host}"   # https://<bu adres>
oc logs deployment/verikopru --tail=50
```
Route adresi kümece verilir (`verikopru-<namespace>.<apps alan adı>`). Giriş zaman aşımına düşüyorsa pod'un LDAPS (636) ya da Oracle portlarına çıkışı kapalıdır (NetworkPolicy/egress); platform ekibine açtırın.

**İç registry kapalıysa** (`oc get is verikopru -o jsonpath="{.status.dockerImageRepository}"` boş, build `InvalidOutputReference` verir): imaj kurum registry'sine gönderilir, ImageStream o imajı izler. Node/npm gerekmesin diye `Containerfile.prebuilt` (ön yüz `prebuilt/frontend-dist`'ten) kullanılır. `<REGISTRY>`, `<PUSH_YOLU>`, `<PYTHON_TABAN_IMAJ>` (Python ≥ 3.11) ve `<PULL_SECRET>` kurumunuza göre; adresler depoya yazılmaz:
```powershell
$patch = @"
spec:
  strategy:
    dockerStrategy:
      dockerfilePath: Containerfile.prebuilt
      pullSecret: {name: <PULL_SECRET>}
      buildArgs:
        - {name: PYTHON_IMAGE, value: "<PYTHON_TABAN_IMAJ>"}
  output:
    to: {kind: DockerImage, name: "<REGISTRY>/<PUSH_YOLU>/verikopru:latest"}
    pushSecret: {name: <PULL_SECRET>}
"@
[IO.File]::WriteAllText("$env:TEMP\bc-patch.yaml", $patch)
oc patch bc/verikopru --type=merge --patch-file="$env:TEMP\bc-patch.yaml"
oc secrets link default <PULL_SECRET> --for=pull
# build (2. adım) bittikten sonra, bir kez:
oc tag "<REGISTRY>/<PUSH_YOLU>/verikopru:latest" verikopru:latest --scheduled
```
`<PUSH_YOLU>` uygulamanın kendi imaj yeridir; taban imajın adresi ASLA yazılmaz (build başarılı olursa ortak taban imajın üzerine yazar). Bu düzende güncelleme: build, ardından `oc import-image verikopru:latest` (ImageStream yeni imajı görür, pod yenilenir). `oc apply -f deploy\openshift\build.yaml` yeniden çalıştırılırsa patch da yinelenir.

**Güncelleme:** `git pull`, ardından 2. adımdaki `git archive` ve `oc start-build` komutları; build bitince pod kendiliğinden yenilenir (`image.openshift.io/triggers`). Manifest ya da `configmap.yaml` değiştiyse `oc apply -k deploy\openshift`. Her ikisi de pod'u yeniden başlatır (`apply -k` imaj alanını geri yazdığı için iki kez olabilir) ve çalışan taşıma `failed`/`partial` olur: çalışan iş yokken yapın.

## X-Forwarded-For (`FORWARDED_ALLOW_IPS`)
Giriş kısıtlaması (`request.client.host`) gerçek istemci IP'sine bakar. Pod, Route/HAProxy arkasında olduğundan uvicorn `--proxy-headers` ile çalışır ve `FORWARDED_ALLOW_IPS`'i yerel olarak okur. Değer router pod'larının geldiği küme ağı CIDR'ı olmalıdır (varsayılan yer tutucu `10.128.0.0/14`; kümenize göre değiştirin).

Doğrulama: bir giriş denemesinden sonra `oc logs deployment/verikopru --tail=20` satırındaki `POST /api/auth/login` isteğinin IP'si istemcinin (PC'nin) adresi olmalı. Küme içi bir adres görünüyorsa router bu aralığın dışından geliyordur; o zaman herkes aynı IP'den gelmiş sayılır ve 5 hatalı giriş herkesi 5 dakika kilitler. Görünen adresin ağını `FORWARDED_ALLOW_IPS`'e ekleyin.

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
