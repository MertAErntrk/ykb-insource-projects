# İş bilgisayarında güncelleme ve servis adresleri

## Kısa cevap
- Adresler doğruysa **hiçbir dosyayı değiştirmezsiniz**. Pull yapın, uygulamayı açın.
- Bir adres değiştiyse değiştireceğiniz tek dosya `src\riskmatch\deployment.py`'dir: Oracle, LLM ve embedding adresleri oradadır.
- Parola ve API anahtarı hiçbir dosyaya yazılmaz; ekrana girilir.
- `config\` klasörüne dokunmayın. Web uygulaması ve ölçüm betiği bu klasörü kullanmaz; `config\*.example.json` yalnız eski komut satırı yolunun şablonudur.

## 1. Pull ve açılış
1. Uygulamanın açık olduğu PowerShell penceresinde **Ctrl+C** ile uygulamayı kapatın.
2. Proje klasöründe şunları çalıştırın:

```powershell
cd C:\...\ykb-insource-projects\risk-feature-workbench
git fetch origin
git checkout risk-feature-workbench-v05
git pull --ff-only
.\scripts\start.ps1
```

- PR `main`'e birleştiyse `risk-feature-workbench-v05` yerine `main` yazın.
- 0.5 sürümü yeni paket gerektirmez, `setup.ps1` çalıştırmanız gerekmez.
- Açılışta "No module named …" hatası alırsanız bir kez şunu çalıştırın:
  ```powershell
  .\scripts\setup.ps1 -IndexUrl '<kurum-artifactory-pypi-simple-adresi>'
  ```
- Tarayıcıda **http://127.0.0.1:8765** adresini açın ve **Ctrl+F5** ile sayfayı tazeleyin; eski ekran önbellekte kalmasın.

## 2. Hangi bilgi nereye yazılır

| Bilgi | Nereye | Kalıcı mı |
|---|---|---|
| Oracle adresi (DSN), kaynak veritabanı adı | `deployment.py` → `ORACLE_DSN`, `SOURCE_DATABASE` | Evet (git'te) |
| LLM temel adresi | `deployment.py` → `LLM_BASE_URL` | Evet (git'te) |
| Embedding temel adresi | `deployment.py` → `EMBEDDING_BASE_URL` | Evet (git'te) |
| Oracle kullanıcı adı ve parola | Ekranın üstündeki **EDWLIVE bağlantısı** alanı | Hayır, oturumluk |
| LLM / embedding API anahtarı | **Model bağlantı ayarları** (Bağlantılar) sayfası → **API anahtarı (gerekiyorsa)** | Hayır, oturumluk |
| Kurum CA sertifikası | Bağlantılar → **Gelişmiş / özel servis ayarları** → **Kurum CA sertifikası dosyası (.pem)**; dosyanın yolunu yazın, ör. `C:\sertifikalar\kurum-ca.pem` | Hayır, oturumluk |
| Model kimliği | Bağlantılar → **Model kimliği (varsa)**; boş kalırsa servisten okunur | Hayır, oturumluk |

## 3. Adres değiştirme (`deployment.py`)
`src\riskmatch\deployment.py` dosyasını Not Defteri ile açın. İlgili satırlar şunlardır:

```python
LLM_BASE_URL = "https://<llm-sunucusu>/v1"
EMBEDDING_BASE_URL = "https://<embedding-sunucusu>"
```

Kurallar:
- **Temel adresi** yazın, tam çağrı adresini değil. Sona `/chat/completions`, `/embeddings` ya da `/embed` eklemeyin.
- **LLM:** Adres `/v1` ile bitebilir; bitmiyorsa uygulama kendisi ekler. Ardından `/v1/models` ve `/v1/chat/completions` adreslerini dener.
- **Embedding:** Sunucunun kök adresini yazın. Uygulama sırayla şunları dener:
  1. `/info` ile `/embed` (TEI biçimi);
  2. olmazsa `/v1/embeddings` (OpenAI biçimi).

  Adresi `/v1` ile yazarsanız da çalışır.
- Adres `https://` ile başlamalı, çift tırnak içinde olmalı ve sonunda `/` olmamalı.
- **Oracle:** Adres aynı dosyadaki `ORACLE_DSN` satırındadır. Yalnız `HOST`, `PORT` ve `SERVICE_NAME` değerlerini değiştirin.

Kaydettikten sonra:
1. Uygulamayı **Ctrl+C** ile kapatın, `.\scripts\start.ps1` ile yeniden açın.
2. Tarayıcıda **Ctrl+F5** yapın.
3. Bağlantılar sayfasında **Temel servis adresi** alanında yeni adresi görün.
4. **LLM bağlantısını test et** ve **Embedding bağlantısını test et** düğmelerine basın.

Test geçince **Gelişmiş** bölümündeki **Tam çağrı adresi** alanı kendiliğinden dolar; elle doldurmanız gerekmez.

## 4. Dosyayı değiştirmeden deneme
Bağlantılar sayfasında **Temel servis adresi** alanını değiştirip test edin; o oturum için yeni adres kullanılır. Sayfa yenilenince ya da uygulama yeniden açılınca adres `deployment.py`'deki değere döner. Adresin kalıcı olmasını istiyorsanız 3. adımı uygulayın.

## 5. Sonraki pull'da çakışma
`deployment.py` git'te izlenir. Dosyayı yerelde değiştirdiyseniz sonraki `git pull` şu uyarıyla durabilir: *"Your local changes … would be overwritten"*.

- **Önerilen:** Kalıcı adres değişikliğini repoya işletin (commit). Böylece her bilgisayar aynı adresi alır ve çakışma olmaz.
- **Geçici çözüm:**
  ```powershell
  git stash
  git pull --ff-only
  git stash pop
  ```
  `stash pop` çakışma verirse `deployment.py`'yi açın. `<<<<<<<` ile `>>>>>>>` arasında doğru adresi bırakın ve işaret satırlarını silin.

## 6. İndeks bir kez kurulur
- İlk bağlantıda embedding testi geçince indeks kendiliğinden başlar. Sağdaki **Embedding indeksi** panelinde şunlar görünür:
  - yüzde;
  - belge sayısı (`a / b belge`);
  - **Tahmini kalan** süre.
- Model açıkken arama ancak indeks %100 olunca başlar. Aramayı önce başlatırsanız "İndeks tamamlanması bekleniyor · %NN · tahmini kalan …" yazar ve arama indeks bitince kendiliğinden sürer.
- Vektörler bu bilgisayarda `runtime\web\vectors.sqlite` dosyasında saklanır. Sonraki açılışlarda yalnız yeni ya da değişen kolonlar embed edilir; tam indeks tekrarlanmaz.
- Her bilgisayar kendi indeksini bir kez kurar.
- **Embedding adresi değişip model aynı kalırsa** indeks korunur. Uygulama modeli adresten değil, sabit deneme metinlerinin vektöründen tanır.
- **Embedding modeli değişirse** indeks bir kez baştan kurulur. Bu beklenen bir durumdur.
- İndekslemede belgeler embedding servisine toplu gönderilir: bir istekte 16 metin.
- Servis model listesi vermiyorsa uygulama "sunucu varsayılanı" kipine geçer. Bu kipte bağlantı testi toplu isteği ayrıca dener: 16 metin gönderir ve vektörlerin sayısını ve sırasını doğrular.
  - Deneme geçerse mesajda "Toplu istek doğrulandı; istekler 16 metinlik gruplarla gönderilecek" yazar.
  - 16 reddedilirse 4 denenir. O da geçmezse istekler tek metinlik gider ve mesaj bunu söyler.
- 4096 boyutlu modelde 16 vektörlük yanıt yaklaşık 1,4 MB tutar; uygulama yanıt sınırını vektör boyutuna göre ayarlar, ayar gerekmez.
- Toplu istek analiz sırasında reddedilirse metinler tek tek yeniden gönderilir; indeks yarıda kalmaz.
- Daha önce tek tek embed edilen kısım korunur; indeks kaldığı yerden toplu gönderimle sürer.

## 6a. Embedding yavaşsa: tanılama betiği
Betik uygulamanın kendi bağlantı testini çalıştırır, model kimliğinin okunabileceği adresleri dener (`/v1/models`, `/models`, `/info`, `/health`, `/version`, `/openapi.json`) ve tek metinlik ile toplu istek hızını ölçer. Yalnız sabit deneme metinleri gönderir; API anahtarını yazdırmaz.

```powershell
.venv\Scripts\python.exe scripts\embedding_teshis.py --belge 150000
```

- `--belge`: indeks panelindeki toplam belge sayısı (`a / b belge`'deki `b`). Betik bu sayıya göre tahmini indeks süresini yazar.
- `--ca C:\sertifikalar\kurum-ca.pem`: TLS hatası alırsanız ekleyin.
- `--model <kimlik>`: servis ekibinin bildirdiği model kimliğini dener.
- API anahtarı gerekiyorsa `--anahtar-sor` ekleyin; anahtar gizli olarak sorulur.

Çıktının sonunda "Ne yapmalı" bölümü bir sonraki adımı söyler.

## 7. Sorun giderme

| Belirti | Olası neden ve çözüm |
|---|---|
| Test "bulunamadı / 404" diyor | Adres yanlış ya da sonunda fazladan bir yol var (`/embeddings`, `/chat/completions`). Temel adresi yazın. |
| "Yetkisiz / 401 / 403" | API anahtarı eksik ya da yanlış. Bağlantılar sayfasında girin. |
| SSL / sertifika hatası | Kurum CA sertifikası tanınmıyor. `.pem` dosyasının yolunu Gelişmiş bölümüne yazın. |
| Zaman aşımı | Kurum ağı ya da VPN bağlı değil, veya sunucu kapalı. |
| İndeks tamamlanamadı | Arama yapılmaz; yüzde ve eksik belge sayısı yazılır. Embedding testini tekrarlayın, sonra panelde **Başlat** düğmesine basın. Kaldığı yerden sürer. |
| Ekran eski görünüyor | **Ctrl+F5** yapın. |

## 8. Ölçüm betiği
Web ile aynı ayarları kullanır (DM_CAD, DM, EDW; embedding açıkken önce tam indeks):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\is_pc_dogrulama.ps1 -Excel C:\yol\etiketli.xlsx
```

Ayrıntılar: [IS_BILGISAYARINDA_BASLANGIC.md](IS_BILGISAYARINDA_BASLANGIC.md).
