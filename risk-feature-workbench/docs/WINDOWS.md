# Windows kurulumu ve kurum pilotu

İlk kurulum için [adım adım iş bilgisayarı rehberini](IS_BILGISAYARINDA_BASLANGIC.md) izleyin; bu belge ayrıntılı pilot notlarını içerir.

## 1. Yerel kurulum

Python 3.9.7 x64, pip, venv ve Tcl/Tk bileşenleri gerekir. Kaynak Python >=3.9 uyumludur; sağlanan hash kilitleri **Windows x64 / Python 3.9.7** için çözülmüştür. Proje klasöründe PowerShell açın:

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

Kurum PowerShell script çalıştırmayı engelliyorsa kurum politikasını değiştirmeyin; eşdeğer komutları kullanın:

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-build.lock
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-demo.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m riskmatch gui
```

Kurum paket aynası varsa aynı sabit sürümleri ve hash'leri kullanın. İnternetsiz bilgisayara wheel'leri kurumun izin verdiği yöntemle taşıyın ve pip `--no-index --find-links <wheel-klasörü>` seçeneklerini ekleyin. `.venv` başka bilgisayara kopyalanmaz; yeniden oluşturulur. Bu uygulamanın kurulumuna Graphify dahil değildir.

Tcl/Tk başlatılamazsa Python kurulumundaki ilgili bileşeni onarın. CLI aynı Excel akışını çalıştırır. Bu geliştirmede gizli Tk penceresi ve örnek-seç/analiz/kaydet akışı gerçekten çalıştırıldı; dosya kaydetme diyaloğunun hedefi otomatik testte taklit edildi.

## 2. Gerçek yapılandırma

1. `config/real.example.json` dosyasını proje köküne **config.local.json** adıyla kopyalayın. `.gitignore` bu adı, `.env`, wallet ve `runtime/` dizinlerini dışlar. Yerel dosyayı Git'e zorla eklemeyin.
2. Oracle `source_database` (kaynak etiketi), `environment`, `container` ve izinli `schemas` alanlarını doldurun. Gerçek DB adını ayrıca `expected_db_name` ile doğrulayabilirsiniz. `container` canlı `CON_NAME` ile kontrol edilir. Şema erişimi boşsa tarama eksik sayılır.
3. Kullanıcı/şifre/DSN yalnız yapılandırmadaki `*_env` alanlarının gösterdiği ortam değişkenlerinden okunur. DSN, host/port/service_name içeren Oracle Easy Connect veya kurum TNS/wallet adı olabilir. Örnek: `RISK_ORACLE_DSN` değerini kuruma ait gerçek DSN ile iş bilgisayarınızda tanımlayın; değerleri sohbete veya dosyaya yazmayın. Şifreyi terminal geçmişine yazmamak için kurumun sır yönetimini kullanın.
4. Wallet kullanılmıyorsa `wallet_dir_env` ve `wallet_password_env` alanlarını kaldırın. Kullanılıyorsa bu env değişkenlerini gerçek dizin/parolaya bağlayın. Thin mode varsayılandır; kurum thick/client veya özel TLS gerektiriyorsa pilotta uyumluluğu ayrıca doğrulayın. LLM/embedding bağlantıları Python varsayılan sertifika deposunu kullanır. JSON içinde `ca_file` desteği yoktur. Kurum CA zinciri Python tarafından güvenilir olmalıdır; gerekirse BT ekibinin sağladığı PEM zinciriyle `SSL_CERT_FILE` ortam değişkenini aynı oturumda tanımlayın. TLS doğrulamasını kapatmayın.
5. LLM için tam `endpoint`, gerçek `model`, `model_version`, izinli `allowed_hosts`, `contract` girin. Örnek dosyada kurum içi adres bulunmaz; bir base URL tam çağrı adresi yerine geçmez. `openai_chat` ancak kurum API'si doğrulanırsa seçilir. Diğer biçim `template` + açık request/response eşlemesidir.
6. Embedding için **ayrı** tam endpoint, model/sürüm/boyut ve sözleşme girin. `request_template` içinde belgelenen `{texts}` ve isteğe göre `{model}` kullanılır. `response_path` örneğin `data[*].embedding` yalnız gerçek servis o biçimi döndürüyorsa geçerlidir. URL'den endpoint yolu eklenmez. `notes` alanları çalıştırma ayarı değildir.
7. API anahtarları gerekiyorsa belirtilen `api_key_env` değişkenlerini doldurun; anonim kurum servisi ise gerekmeyen `api_key_env` alanını kaldırın. Authorization varsayılan Bearer; farklı şema gerekirse adapter sözleşmesini kurumun API'sine göre genişletin, bilgiyi URL sorgusuna yazmayın.
8. Gerekli alanlar tamamlanınca `allow_real_connections` değerini `true` yapın. Bu flag tek başına ağ başlatmaz; ayrıca açık gerçek analiz komutu gerekir.

```powershell
.\scripts\setup.ps1 -Real
.\.venv\Scripts\python.exe -m riskmatch check-config config.local.json --mode real
```

Bu kontrol şema/limit/endpoint/model sözleşmesinin biçimini doğrular. DNS, sertifika, env sırlarının doğruluğu, Oracle erişimi veya model inference başarısı kanıtı değildir.

## 3. Gerçek pilot kontrol listesi

- [ ] Hedef DB/container/ortam/şema kapsamı ekip tarafından onaylandı; farklı ortam kopyaları karışmıyor.
- [ ] Oracle read-only kullanıcıda gerekli ALL_TAB_COLUMNS ve yorum erişimi var. Opsiyonel P/U constraint metadata hataları görünür.
- [ ] DNS, TLS zinciri, kurum CA/wallet ve firewall iş bilgisayarında doğrulandı.
- [ ] LLM tam model kimliği, context/output sınırı ve JSON cevap sözleşmesi doğrulandı.
- [ ] Embedding tam yol/payload/mapping/boyut/sürüm doğrulandı; model değişiminde eski cache kullanılmıyor.
- [ ] Küçük, kurum tarafından izinli bir metadata kapsamıyla `analyze --mode real` çalıştırıldı. Tamamlanma/hata/coverage alanları incelendi.
- [ ] Gerçek işlem verisi çekilmedi, üretim yazımı yapılmadı, dış servise metadata gitmedi.
- [ ] Aynı ad farklı iş tanımı, net/brüt, başvurulan/onaylanan, kişi/hesap/işlem ayrımları uzman tarafından kontrol edildi.
- [ ] Pencere sınırı, referans tarihi, kişi anahtarı, grain, yön, zero/NULL kararları onaylı kaynakla tamamlandı.
- [ ] Kesinti ve aynı dosyayla devam pilotta doğrulandı; sonuçlar ve yerel cache kurumun izinli dizininde.
- [ ] Bağımsız uzman etiketli kabul setinde false support, abstention/coverage, recall@k ve uzman onayı ölçüldü.

```powershell
.\.venv\Scripts\python.exe -m riskmatch prepare talepler.xlsx --output talepler_kimlikli.xlsx
.\.venv\Scripts\python.exe -m riskmatch analyze talepler_kimlikli.xlsx --mode real --config config.local.json --output runtime\inceleme.xlsx --state-dir runtime\pilot
```

Gerçek mod başarısız olduğunda sonuç eksik olarak raporlanır, demo kataloğuna dönmez. Paket kurulumu sırasında genel PyPI kullanılmış olması bankacılık entegrasyonunun doğrulandığı anlamına gelmez. Oracle/Qwen bağlantıları bu geliştirmede yapılmadı.

## 4. Yerel veriler ve işletim

`runtime/` checkpoint, embedding cache, son koşu ve bağımlılık JSON'u içerir; gerçek modda kurum metadata'sı bulunabilir. Bunları dışarı aktarmayın. Graphify bu dizinleri hiçbir zaman taramaz. Cache sürüm/kapsam/model/sözleşme ile ayrılır; silinen katalog nesneleri yeni snapshot ile indeks/kararlardan çıkarılır. Eski koşu checkpoint'leri otomatik silinmez; kurumun saklama politikasına göre kaldırılır.

Oracle bağlantı timeout'u varsayılan 10 saniye, round-trip timeout 30 saniye; model istekleri 30 saniye ve en fazla sınırlı retry. Seri çalışma varsayılandır; kontrolsüz batch/concurrency yoktur. İlk sürüm embedding indeksinde 10.000 kayıt koruma sınırı vardır; daha geniş DWH için onaylı şema kapsamlarını bölün veya indeks stratejisini ayrıca ölçüp genişletin. Sınıra takılma 'alan yok' değildir.
