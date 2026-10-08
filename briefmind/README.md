# BriefMind

Microsoft Teams toplantılarını **yerel makinede** dinleyip (ses + canlı altyazı), kurum içi
Whisper STT ve LLM servisleriyle yapılandırılmış toplantı notu üreten Windows masaüstü uygulaması.
Ses hiçbir yere kaydedilmez; yalnızca metin diske yazılır. Tüm işleme kurum içi servislerde yapılır.

## Mimari

```
Ses (hoparlör + mikrofon) ─ cümle bazlı VAD ─► Whisper STT (OpenAI uyumlu)   [ne söylendi]
Teams canlı altyazısı (UI Automation) ────────► konuşmacı + zaman           [kim söyledi]
        └─ zaman + metin benzerliğiyle eşleşir; uyum ölçümü (ses doğru mu)
Motor: parçalar (≤3000 token / 12 dk) ─► bölüm özeti (JSON) ─► inceleme (şüpheli terimler)
     ─► birleştirme ─► dayanak kontrolü (transkriptte olmayan madde düşer) ─► Markdown not
     ─► Outlook taslağı (takvimdeki katılımcılara)
```

## Dosyalar

| Dosya | Görev |
|---|---|
| `app.py` | PyQt5 masaüstü uygulaması (Canlı / İnceleme / Not / Sözlük / Geçmiş / Ayarlar) |
| `isler.py` | Arayüzü dondurmayan arka plan işleri (yakalama, not üretimi, yeniden özetleme, soru) |
| `gorunum.py` | Tema (QSS), notun HTML görünümü, ikonlar |
| `ayar.py` | `config.json` okuma/yazma |
| `motor.py` | Parçalama, arka plan özetleme, konuşmacı hizalama, dayanak kontrolü, not üretimi |
| `llm.py` | LLM istemcisi, bölüm/birleştirme promptları, token bütçeleri, notun deterministik temizliği |
| `metin.py` | Metin benzerliği (kelime kökü), Türkçe küçük harf, ad eşleşmesi |
| `ses.py` | Ses yakalama (WASAPI loopback + mikrofon), VAD, STT istemcisi, tekrar/halüsinasyon filtreleri |
| `yakalayici.py` | Teams altyazısını UI Automation ile okuma, toplantı adı, altyazıyı otomatik açma |
| `outlook.py` | Takvimden toplantı eşleştirme, taslak e-posta, oturum kullanıcısı |
| `sozluk.py` | Sözlük: alias uygulama, toplantı serisine göre terim kapsamı |
| `toplanti.py` | Komut satırı arayüzü (başla / bitir / liste) |
| `tools/` | Teşhis ve yardımcı scriptler (STT testi, Teams düğme keşfi, sözlük temizliği) |
| `deploy/` | GPU'suz Whisper servisi için OpenShift manifestleri (CPU, faster-whisper) |

## Kurulum

```powershell
pip install -r requirements.txt
copy config.example.json config.json      # adresleri doldur
copy sozluk.example.json sozluk.json
python app.py
```

`config.json`:

| Alan | Açıklama |
|---|---|
| `route` | OpenAI uyumlu LLM adresi, `/v1` ile biter (`https://<llm-adresi>/v1`; sondaki `/` ya da eksik `/v1` Kaydet'te düzeltilir) |
| `model` | Sunucudaki model kimliği (`GET /v1/models` → `id`). **Boş bırakılabilir:** sunucu tek model sunuyorsa o seçilir; yazılan ad sunucuda yoksa ve tek model varsa o kullanılır, Olaylar'a yazılır |
| `context` | Bağlam penceresi (token). `0`/boş: sunucunun `max_model_len` değeri (önerilen). Bir değer yazılırsa yalnızca **üst sınırdır**; sunucununkinden büyükse sunucunun değeri geçerli olur. Token bütçeleri (parça boyutu, özet/liste çıktısı, soru-cevap girdisi) bu pencereye göre ölçeklenir. Ayarlar → Bağlam penceresi |
| `llm_akis` | (varsayılan `true`) LLM istekleri akışlı (`stream`) gönderilir: OpenShift route'unun boşta kalma zaman aşımına (varsayılan 30 sn) uzun özetlerde takılmaz. `false`: eski akışsız istek |
| `llm_proxy` | (varsayılan `false`) `true`: LLM'e Windows/sistem proxy'si (`HTTPS_PROXY`) üzerinden bağlanılır. Adres tarayıcıda açılıyor ama uygulama "ulaşılamadı" diyorsa denenir |
| `llm_key`, `llm_tekrar`, `llm_zaman_asimi` | (isteğe bağlı) LLM Bearer anahtarı (sunucu `--api-key` ile açıldıysa); hata sonrası yeniden deneme sayısı (varsayılan 1); istek zaman aşımı (sn, varsayılan 300) |
| `parca_token`, `paralel` | (isteğe bağlı) Canlı parça boyutu (token) ve aynı anda işlenen parça sayısı; boşsa pencereye göre (16k: 3000/2, 32k+: 4000/2) |
| `stt_url`, `stt_model`, `stt_key` | OpenAI uyumlu STT adresi (`/v1/audio/transcriptions`), model, Bearer anahtarı |
| `kaynak` | `ses` (Whisper metni, konuşmacı altyazıdan) / `ikisi` / `altyazi` (yalnız Teams altyazısı). `ikisi`: metin Whisper'dan gelir; bir altyazı satırının 8 sn içinde benzer Whisper karşılığı gelmezse (Whisper kaçırmış) altyazı satırı eklenir, gelirse eklenmez (çift satır olmaz) |
| `ben` | Uygulamayı açan kişi (boşsa Outlook/Windows'tan alınır) |
| `mikrofon_cihaz` | (isteğe bağlı) Mikrofon cihaz numarası ya da adı; yoksa Windows varsayılanı. Doğru cihazı bulmak için: `python tools\ses_teshis.py` |
| `otomatik_not` | (varsayılan `true`) Toplantı bitince İnceleme beklenmez, not doğrudan üretilir. `false`: önce İnceleme sekmesi (eski akış). Ayarlar → "Toplantı bitince incelemeyi atla" |
| `stt_proxy` | (isteğe bağlı, varsayılan `true`) `false` yapılırsa STT'ye Windows proxy'si atlanarak doğrudan bağlanılır. STT'de sık `10053` bağlantı kopması görülürse denenir. |
| `not_sablonu` | `genel` / `haftalik` / `karar` / `birebir` / `calistay` (Ayarlar → Not şablonu) |
| `saklama_gun` | Notu üretilmiş toplantıların transkripti bu kadar gün sonra silinir; `0` kapalı (Ayarlar → Transkript saklama süresi) |
| `ca_bundle` | (isteğe bağlı) Kurum kök sertifikası dosyası (`.cer`, Base64). Doluysa LLM ve STT bağlantılarında TLS doğrulanır; boşsa doğrulama kapalıdır ve açılışta Olaylar'a bir kez yazılır. Aşağıdaki adımlara bakın. |

### TLS doğrulaması (`ca_bundle`)

Kurum içi sunucuların sertifikası kurumun kendi kök sertifikasıyla imzalıdır. Bu kök bir kez dışa aktarılır:

1. Edge'de LLM adresini açın (ör. `https://<llm-adresi>/v1/models`), adres çubuğundaki **kilit** simgesine tıklayın.
2. **Bağlantı güvenli** → **Sertifika geçerli** (sertifika simgesi) → **Ayrıntılar** sekmesi.
3. **Sertifika hiyerarşisi**nde en üstteki **kök** sertifikayı seçin.
4. **Dışarı aktar…** → biçim olarak **Base64 ile kodlanmış ASCII, tek sertifika** → örneğin
   `C:\Users\<kullanıcı>\kurum_kok.cer` olarak kaydedin.
5. `config.json`'a ekleyin: `"ca_bundle": "C:\\Users\\<kullanıcı>\\kurum_kok.cer"` (JSON'da `\` çift yazılır) ve
   uygulamayı yeniden başlatın. Olaylar'da "TLS doğrulaması kapalı" satırı görünmüyorsa doğrulama açıktır;
   `python tools\teshis.py` çıktısında da "TLS doğrulaması: açık" yazar. STT başka bir kökle imzalıysa
   iki sertifika aynı `.cer` dosyasına alt alta kopyalanabilir.

Exe üretmek için `derle.bat` (PyInstaller, `dist\BriefMind\`).

## Kullanım

1. Teams toplantısına gir (altyazı açık olsun; kapalıysa uygulama Alt+Shift+C ile açmayı dener).
2. **Başlat** — üstteki listeden toplantı seçilir (takvimden, Teams penceresine göre otomatik).
3. Konuşmalar cümle cümle Canlı sekmesine düşer; parçalar arka planda özetlenir. İki kişi aynı anda konuşunca
   Whisper genelde baskın sesi yazar; öbür kişinin Teams altyazısındaki satırı transkripte ayrıca eklenir
   (`kaynak: altyazi-cakisma`), sonradan gelen aynı sözlü Whisper satırı ikinci kez yazılmaz. Kısık gelen ses
   (tepe < 0,3) Whisper'a gitmeden yükseltilir.
4. **Bitir** → not doğrudan üretilir (`otomatik_not`); Not sekmesindeki şeritte aşama ve geçen süre görünür
   ("Parça 3/12 özetleniyor", "Özet paragrafı yazılıyor"…) → Outlook taslağı. Şüpheli terimler İnceleme
   sekmesinde bilgi için durur: onaylayıp **Uygula ve notu üret** ile sözlüğe alınır ve not yeniden üretilir.
   `otomatik_not` kapalıysa eski akış: İnceleme → **Uygula ve notu üret** → Not.

Ayarlar → **Kaydet** hemen geçerlidir; LLM adresi/modeli için uygulamayı yeniden başlatmak gerekmez. Açılışta ve
Kaydet'ten sonra LLM sunucusu arka planda sorulur (model adı, bağlam penceresi); uyarılar Olaylar'a düşer.
Ayarlar → **LLM sunucusunu test et** kısa bir sohbet denemesi yapar (süre, token/sn, cevap Türkçe mi). Ayrıntılı
ölçüm: `python tools\llm_teshis.py --tam` ([Teşhis rehberi](docs/TESHIS_REHBERI.md#yeni-llm-sunucusu)). Bir
parça özetlenemezse durum satırı "Not eksik" (turuncu) olur ve son LLM hatası gösterilir.
Komut satırı: `python toplanti.py basla --inceleme-yok` (ya da `bitir <klasör> --inceleme-yok`) incelemeyi atlar.

Kalıcı altyazı için Teams: … → Ayarlar → Erişilebilirlik → *Toplantılarımda her zaman alt yazıları göster*.

## Notla çalışma

- **Kaynağa git:** Karar ve aksiyonların yanındaki `⏱10:12:03`'e tıklayınca transkript o anda açılır.
- **Soru sor:** Not sekmesindeki kutuya "Test ortamı ne zaman hazır, kim söyledi?" gibi sorular yazılır; cevap yalnızca bu toplantının transkriptinden gelir.
- **Ara:** Geçmiş sekmesinde tüm toplantıların transkript ve notlarında arama yapılır.
- **Yeniden özetle:** Geçmiş sekmesinde bir toplantı seçilip basılır; **tüm** parçalar güncel sözlükle yeniden
  düzeltilip özetlenir ve yeni not üretilir (yarım kalmış kayıtlar için de). Eski not `not.md.yedek-YYYYMMDD-HHMM`
  olarak saklanır. Tek parça için: "Seçili parçayı yeniden özetle".
- **Düzenle / Word / PDF / Kişiye özel e-postalar:** Not sekmesindeki düğmeler.
- **Jira/Planner CSV:** Not sekmesi → "Jira/Planner CSV" aksiyon tablosunu `aksiyonlar.csv` olarak kaydeder
  (sütunlar `Summary, Assignee, Due Date, Description, Issue Type`; UTF-8). Jira: *Issues → Import issues from CSV*.
  Planner: CSV Excel'de açılıp görevler kopyalanır. Doğrudan Jira'ya kayıt açılmaz (kimlik bilgisi gerekmez).
- **Konuşma payı:** Geçmiş sekmesinde bir toplantı seçilince üstte "Konuşma payı: Elif %38, Berk %27, …"
  (satırın süresi = sonraki satıra kadar, en fazla 15 sn). Nota yazılmaz; `meta.json` → `konusma_paylari`.
- **Şablon ve saklama süresi:** Ayarlar → Not şablonu, Transkript saklama süresi.
- **Bilgilendirme:** Canlı sekmesi → "Katılımcıları bilgilendir" metni panoya kopyalar; Teams sohbetine yapıştırın.

## Test

```powershell
pip install pytest
python -m pytest tests
```

Testler LLM sunucusu ya da ses cihazı gerektirmez. GitHub Actions (`.github/workflows/briefmind.yml`, repo
kökünde) `briefmind/` değişince testleri Ubuntu + Python 3.11 ve Windows + Python 3.9 üzerinde çalıştırır; Actions
sekmesinden elle (**Run workflow**) tetiklenince Windows'ta exe derlenir ve `BriefMind` artefaktı olarak indirilir. Uçtan uca analiz ve yol haritası için bkz. [`docs/GELISTIRME_PLANI.md`](docs/GELISTIRME_PLANI.md).

## Gizlilik ve uyum

- Ses diske yazılmaz; STT'ye giden parça bellekte tutulur, yanıt gelince silinir.
- Sözlük, `config.json` ve `toplantilar/` klasörü `.gitignore`'dadır; repoya girmez.
- Kurum içi adresler yer tutucudur; gerçek adresler yalnızca `config.json`'da durur.
