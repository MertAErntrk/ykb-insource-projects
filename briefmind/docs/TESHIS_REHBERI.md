# Teşhis rehberi: iş bilgisayarından hangi bilgiler gerekiyor

Bu rehber, [`GELISTIRME_PLANI.md`](GELISTIRME_PLANI.md)'deki geliştirmeler için iş bilgisayarından toplanması gereken bilgileri anlatır.

## Önce kısa cevap

- **Claude'un erişemedikleri:** İş bilgisayarı, kurum ağı, Qwen (LLM) ve Whisper (STT) sunucuları, önceki sohbetler. Önceki sohbetlerde Qwen hakkında konuşulanlar, repoya yazılmadıysa görülemez.
- **Repodan bilinenler:** Bağlam penceresinin varsayılan değeri (16384; sunucu sorulamazsa) ve kendi CPU Whisper servisimizin manifesti (`deploy/whisper-cpu.yaml`). Model adı ve bağlam penceresi artık çalışırken sunucudan (`GET /v1/models`) okunur; gerçek adres ve model adı repoya yazılmaz.
- **Geliştirmelerin çoğu bu bilgilere bağlı değil.** Aşağıdaki tabloda hangi işin bilgi beklediği yazıyor.

| Plan maddesi | İş bilgisayarından bilgi gerekiyor mu? |
|---|---|
| A2 LLM istatistik günlüğü, A3 sıralı ekleme, A5 ses normalizasyonu, A6 `ikisi` modu, A7 UIA önbelleği, A10 tarih çözümleme, A11 modüllere ayırma, A12 CI, A13 not düzenleme | **Hayır**, hemen yapılabilir |
| A1 LLM sunucu ayarları | **Evet**: Adım 2 ve 3 |
| A4 kesim ayarı, A9 kalite ölçümü | **Evet**, ama veri sizde kalır: ölçüm aracını ben yazarım, siz çalıştırırsınız, yalnızca sayıları paylaşırsınız |
| A8 TLS doğrulaması | **Kısmen**: yalnızca kurum CA sertifikası dosyası var mı (Adım 5) |

## Paylaşmayın

- `config.json` dosyasının kendisi, iç adresler, anahtarlar ve token'lar
- `toplantilar/` klasöründeki transkript, `not.md` ve parça içerikleri
- Kişi adları ve toplantı başlıkları

Teşhis aracı adresleri `<LLM_ADRES>` / `<STT_ADRES>` olarak maskeler ve toplantı içeriği toplamaz. Yine de paylaşmadan önce çıktıya bir kez göz gezdirin.

---

## Yeni LLM sunucusu

LLM sunucusu (vLLM) değiştiğinde ya da "özet çıkarırken hata" görüldüğünde önce bu bölüm.

**1) `config.json` (yalnızca sizde; repoya girmez):**

```json
"route": "https://<yeni-llm-adresi>/v1",
"model": "",
"context": 0
```

- `route` `/v1` ile bitmeli (sonda `/` olmadan; uygulama Kaydet'te kendisi düzeltir).
- **Route'a tam uç nokta yapıştırmayın.** Tarayıcıdan/Postman'den kopyalanan `https://<adres>/v1/chat/completions` (ya da `/v1/completions`, `/v1/models`) yanlış: uygulama `/chat/completions`'ı kendisi ekler, eski sürüm `…/v1/chat/completions/v1/chat/completions` adresine gidip her isteğe 404 alıyordu. Yeni sürüm sondaki uç noktayı kırpar ve Olaylar'a `LLM: adres düzeltildi: LLM adresinin sonundaki '/chat/completions' uç noktası atıldı …` yazar; `config.json`'u yine de `https://<adres>/v1` olarak düzeltin. `llm_teshis.py` bunu `[YUKSEK] route'a tam uç nokta yapıştırılmış` olarak raporlar.
- `model`: boş bırakın (sunucu tek model sunuyorsa o seçilir) ya da `https://<adres>/v1/models` sayfasındaki `id` değerini aynen yazın. Eski model adı kalırsa sunucu her isteğe 404 verir; uygulama artık bunu açılışta görüp tek modele geçer ve Olaylar'a yazar.
- `context`: `0` (sunucunun `max_model_len` değeri kullanılır). Eski `16384` kalırsa yalnızca üst sınır olur.
- Uygulamadan: Ayarlar → **LLM sunucusunu test et**. Olaylar'da `LLM: LLM modeli: … · bağlam penceresi: …` satırı görünür.

**2) Teşhis aracı (uygulama kapalıyken, `briefmind` klasöründe):**

```powershell
python tools\llm_teshis.py --tam        # 3-5 dk; --hizli ile uzun ve paralel ölçümler atlanır (~1 dk)
```

Araç yalnızca dosyanın içindeki **sentetik** metinlerle (uydurma kişiler ve konular) çalışır; toplantı verisi göndermez. LLM adresi `<LLM_ADRES>`, kurum alan adı `<ALAN>` olarak maskelenir, anahtar yazılmaz. Ölçtükleri:

| Bölüm | Ne gösterir |
|---|---|
| 2 Bağlantı | TLS (ca_bundle / kapalı / sistem) × sistem proxy'si: hangi biçimle ulaşılıyor, uygulamanınki çalışıyor mu |
| 3 Model ve pencere | Sunucudaki model kimlikleri ve `max_model_len`; config `model` EŞLEŞİYOR / UYUŞMUYOR; `context` farkı |
| 4 Tokenizer | `/tokenize` erişimi ve süresi, karakter/token, sunucunun gerçek sayımıyla (`usage.prompt_tokens`) fark |
| 5 Düşünme | varsayılan / `reasoning_effort` / `enable_thinking=False`: düşünce uzunluğu, ayrı alana mı düşüyor, içerikte `<think>` ya da başka etiket var mı |
| 6 Şema | `json_schema` düşünmeli ve düşünmesiz gerçekten uygulanıyor mu |
| 7 Hız | token/sn (akışlı, akışsız), ilk token süresi, ön işleme hızı; ~7000 token'lık **uzun istek akışsız ve akışlı** (route zaman aşımı testi) |
| 8 Eşzamanlılık | 1/2/4 paralel istekte akış başına hız |
| 9 Bağlam probu | Pencereye yakın girdi kabul ediliyor mu; kasıtlı taşmada sunucunun tam hata metni ve uygulamanın bunu tanıyıp tanımadığı |
| 10 Uygulama yolu | Uygulamanın kendi istemcisiyle (openai, TLS, proxy, akış) sunucu tanısı, bütçeler ve kısa sohbet |
| 11 Tam boru hattı (`--tam`) | Gerçek düzeltme → bölüm özeti → birleştirme → soru-cevap; her LLM çağrısı (görev, düşünme, finish, token, süre, dil, hata) ve üretilen sentetik not |
| 12 llm_log özeti | Son toplantıların `llm_log.jsonl`'inden görev başına çağrı, finish dağılımı, süre ve **hata metinleri** (içerik yok) |

En sonda **Teşhis özeti** (öncelik sırasıyla olası kök nedenler) ve önerilen `config.json` değerleri yazılır.

**Paylaşılacak:** `llm_teshis_cikti.txt` dosyası ve (varsa) Olaylar panelindeki `✖ parça N özetlenirken hata: …` satırı. Paylaşmadan önce bir kez göz gezdirin.

**Sık görülen sonuçlar:**

| Çıktıda | Anlamı | Ne yapılır |
|---|---|---|
| `modelini tanımıyor (HTTP 404)` / `UYUŞMUYOR` | `model` eski ad | `model` boş ya da `/v1/models`'teki `id` |
| `bağlam penceresini aşıyor (HTTP 400)` | `context` sunucudan büyük | `context: 0` |
| `HTTP 504 (HTML: route/proxy)`, uzun akışsız istek başarısız | OpenShift route zaman aşımı | `llm_akis: true` (varsayılan); kalıcı: route'a `haproxy.router.openshift.io/timeout=600s` |
| `ulaşılamadı … CERTIFICATE_VERIFY_FAILED` | `ca_bundle` yeni sunucuyu doğrulamıyor | Yeni adresin kök sertifikasını dışa aktarın (README, TLS) |
| Bağlantı yalnız `proxy=açık` ile çalışıyor | Sunucuya sistem proxy'si üzerinden ulaşılıyor | `llm_proxy: true` |
| `HTTP 401/403` | Sunucu anahtar istiyor | `llm_key` |
| `route'a tam uç nokta yapıştırılmış` | `route` `…/v1/chat/completions` gibi | `route`: yalnızca `https://<adres>/v1` |
| `ca_bundle' ayarlı ama dosya bulunamadı` | Yol yanlış: TLS doğrulaması kapalı çalışır | Tam yol (JSON'da `\\`), ya da alanı boşaltın |
| `config.json bulunamadı` | Araç/uygulama başka klasörden çalışıyor ya da dosya hiç yok | `config.example.json` → `config.json`; uygulamada Ayarlar → Kaydet |
| `enable_thinking=False düşünmeyi KAPATMIYOR` | Yeni sohbet şablonu parametreyi tanımıyor | Çıktıyı paylaşın (kodda şablon parametresi değişir) |

---

## Adım 1: Kodu güncelleyin (2 dk)

```powershell
cd ykb-insource-projects
git pull
cd briefmind
pip install pytest
python -m pytest tests
```

**Paylaşılacak:** Son satır (örneğin `16 passed`). Hata varsa hata çıktısı.

## Adım 2: Teşhis aracını çalıştırın (5 dk)

`briefmind` klasöründe, uygulama kapalıyken çalıştırın:

```powershell
python tools\teshis.py
```

İsterseniz STT'yi gerçek sesle de deneyebilirsiniz. Toplantı kaydı kullanmayın; kendi sesinizle 15 sn'lik bir deneme kaydı yeterli. Tanınan metin çıktıya yazılmaz.

```powershell
python tools\kayit_wav.py 15          # test.wav oluşturur
python tools\teshis.py --wav test.wav
```

Araç `teshis_cikti.txt` dosyasını üretir. İçinde şunlar olur:
1. Python ve paket sürümleri
2. `config.json` alanları (adresler maskeli, anahtarlar gizli)
3. LLM (`tools\llm_teshis.py`'nin kısa kipi; ayrıntı için [Yeni LLM sunucusu](#yeni-llm-sunucusu)): bağlantı biçimleri, model adı eşleşmesi, `max_model_len`, tokenizer oranı ve kısa sohbet testleri. Bu testler şunu gösterir: düşünme metni ayrı alana mı ayrılıyor, `reasoning_effort` bir işe yarıyor mu, `enable_thinking=False` çalışıyor mu, `json_schema` destekleniyor mu. Her testte kısa, genel bir soru sorulur; toplantı verisi gönderilmez.
4. STT: sağlık kontrolü, sessizliğe metin uydurup uydurmadığı, segment alanları (`no_speech_prob`, `avg_logprob` var mı)
5. Son 10 toplantı kaydı, yalnızca sayılarla: parça sayısı, özetsiz parça sayısı, satır sayısı, zamanı geriye giden satırlar (A3), notun İngilizce olup olmadığı ve eksik başlıklar
6. `hata.log` dosyasının son 15 satırı. Bu kısmı paylaşmadan önce özellikle kontrol edin.
7. Birim testlerinin sonucu

**Paylaşılacak:** `teshis_cikti.txt` dosyasının içeriği. Sohbete yapıştırabilir ya da dosyayı yükleyebilirsiniz.

## Adım 3: Qwen (vLLM) sunucusunun başlatma ayarları (A1)

Sunucuyu kimin yönettiğine göre iki yol var:

**a) OpenShift'e erişiminiz varsa**
1. OpenShift konsolu → doğru proje (namespace) → **Workloads → Deployments** (ya da StatefulSets / InferenceService) → Qwen servisi → **YAML** sekmesi.
2. `containers:` → `args:` (ya da `command:`) altındaki satırları bulun.
3. Komut satırıyla:
   ```powershell
   oc get deploy <qwen-deploy-adi> -o jsonpath="{.spec.template.spec.containers[0].args}"
   oc logs <qwen-pod-adi> | Select-String "max_model_len|reasoning|structured|guided|chat_template|version"
   ```

**b) Sunucuyu ARGE yönetiyorsa** şu soruları iletin:
1. vLLM sürümü nedir?
2. `--reasoning-parser` açık mı, hangi değerle (`qwen3` / `deepseek_r1`)?
3. `--max-model-len` kaç?
4. Structured output (`response_format: json_schema`) açık mı, hangi backend ile?
5. Özel bir `--chat-template` kullanılıyor mu?
6. Eşzamanlı istek sınırı ve istek zaman aşımı (route timeout) nedir?

**Paylaşılacak:** Yalnızca bayraklar ve değerleri, örneğin `--max-model-len 16384 --reasoning-parser qwen3`. Adres, token ve secret satırlarını çıkarın.

## Adım 4: Whisper (STT) servisinin ayarları

- **ARGE'nin GPU servisi (`whisper-large-v3-turbo-prod`):** Motor ne (faster-whisper / vLLM / başka)? `beam_size`, `vad_filter` ve `condition_on_previous_text` değerleri nedir? `verbose_json` ile segment ve `avg_logprob` dönüyor mu? Bu sonuncuyu Adım 2 zaten gösterir.
- **Kendi CPU servisimiz:** Ayarları `deploy/whisper-cpu.yaml` dosyasında, repoda duruyor. Ek bilgi gerekmiyor.

**Paylaşılacak:** Bu dört sorunun cevabı.

## Adım 5: Kurum CA sertifikası (A8)

Uygulama şu an TLS doğrulamasını kapatıyor (`verify=False`). Doğrulamayı açmak için kurum kök sertifikası gerekir. Sertifikayı paylaşmayın; yalnızca şunları söyleyin:
- BT'nin dağıttığı bir `.pem` / `.crt` / `.cer` kök sertifika dosyası var mı? Genelde intranet'te ya da BT portalında bulunur.
- Yoksa: `certmgr.msc` → Güvenilen Kök Sertifika Yetkilileri altında kurumun adını taşıyan bir sertifika görünüyor mu?

Uygulamaya `config.json` → `"ca_bundle": "C:\\...\\kurum.pem"` ayarını ben eklerim; dosya sizin makinenizde kalır.

## Adım 6: Kalite ölçümü (A9, daha sonra)

Bunun için şimdilik bir şey yapmanız gerekmiyor. Ölçüm aracını ben yazacağım. Siz birkaç toplantının 10 dakikalık bölümünü elle düzelteceksiniz; araç sizin makinenizde çalışacak ve yalnızca sayıları üretecek (kelime hata oranı, karar/aksiyon isabeti). Paylaşacağınız şey yalnızca o sayılar olacak.

---

## Özet: bana dönmeniz gerekenler

1. Adım 1'in son satırı
2. `teshis_cikti.txt` dosyasının içeriği
3. vLLM bayrakları (Adım 3)
4. Whisper servisi cevapları (Adım 4)
5. CA sertifikası var mı (Adım 5)

Bunları beklerken A2, A3, A5, A6 ve A10'a başlayabilirim; bu işler bu bilgilere bağlı değil.
