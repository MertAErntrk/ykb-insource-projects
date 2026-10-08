# BriefMind — Uçtan uca analiz ve geliştirme planı

Tarih: 2026-09-29. Kapsam: `app.py`, `motor.py`, `llm.py`, `ses.py`, `yakalayici.py`, `sozluk.py`, `outlook.py`, `toplanti.py`.

## 1. Uygulama nasıl çalışıyor

```
Teams sesi (hoparlör loopback + mikrofon)
  └─ ses.py: 0.1 sn bloklar → uyarlanır gürültü eşiği → cümle sonu (0.55 sn sessizlik) / nefes / zorla kesim
     └─ STT kuyruğu → 2 işçi → Whisper (/v1/audio/transcriptions, verbose_json)
        └─ filtreler: tekrar temizleme, halüsinasyon, eko → motor.ses_satiri()
Teams canlı altyazısı (UI Automation, 0.6 sn'de bir)
  └─ yakalayici.py: konuşmacı + metin → motor.altyazi_satiri() → konuşmacı-zaman izi
motor.py: Whisper cümlesine konuşmacı ata (zaman + metin benzerliği) → sözlük → altyazi.jsonl
  └─ parça (≈3000 token / 12 dk / 3 dk sessizlik) kapanınca arka planda:
     düzeltme geçişi (LLM) → bölüm özeti JSON (LLM, "map") → şüpheli terim önerileri
Bitir → İnceleme (öneri onayı → sözlük) → değişen parçalar yeniden özetlenir
Notu üret → birleştirme ("reduce") → dayanak kontrolü → not.md → Outlook taslağı
```

## 2. Bildirilen sorunun kök nedenleri

> "Parça parça özetler anlamlı ama tam özet bazen İngilizce çıkıyor ya da yarım kalıyor (max token)."

Parça özetleri iyi, sorun birleştirme adımında. Koddan çıkan nedenler:

| # | Neden | Etkisi |
|---|---|---|
| N1 | Birleştirme tüm notu **tek bir uzun Markdown cevabı** olarak istiyordu (3500 token) ve düşünme açıktı. Qwen'in düşünme token'ları da `max_tokens` bütçesinden düşer. | Uzun toplantıda cevap tablonun ortasında kesiliyordu. İkinci deneme (4500) de kesilirse yarım not sessizce kaydediliyordu. |
| N2 | `chat_template_kwargs` içinde gönderilen `reasoning_effort` Qwen3 sohbet şablonunda tanımlı değil (gpt-oss'a özgü). Büyük olasılıkla etkisiz, yani düşünme "low" değil tam uzunlukta çalışıyor. | Bütçenin büyük kısmını İngilizce düşünme yiyor. |
| N3 | vLLM'de `--reasoning-parser` yoksa ya da cevap düşünme sırasında kesilirse, `<think>…` metni **content**'e düşer. Kod bunu temizlemiyordu. | Notun içinde İngilizce paragraflar. |
| N4 | Çıktının dili hiç kontrol edilmiyordu. | Model İngilizceye kayınca olduğu gibi kaydediliyordu. |
| N5 | Dayanak kontrolü özet cümlelerini **yalnızca transkriptle** karşılaştırıyordu. Özet cümleleri "görüşüldü", "ele alındı" gibi soyut fiiller taşır. İngilizce cümlelerin kelimeleri de Türkçe transkriptte hiç geçmez. | Doğru özet cümleleri de siliniyordu. İngilizce çıkan özet neredeyse tamamen boşalıyordu ("eksik kalıyor"). |
| N6 | Özetlenemeyen parça notta **sessizce** yer almıyordu. | Toplantının bir bölümü hiç iz bırakmadan kayboluyordu. |
| N7 | Kesilmiş JSON "onarım" çağrısıyla yarım liste olarak kabul ediliyordu. | Karar ya da aksiyonlar eksik kalabiliyordu. |

## 3. Bu değişiklikte düzeltilenler

### Not üretimi (`llm.py`, `motor.py`)
- **Yeni birleştirme.** Model artık tek uzun metin yazmıyor. Önce karar, aksiyon ve açık soru listeleri küçük bir JSON olarak birleştiriliyor. Sonra özet paragrafı ile "sonraki adım" ayrı, küçük bir JSON olarak alınıyor. Markdown notu kodda kuruluyor: başlıklar sabit ve Türkçe, tablo yarım kalamaz. Birleştirme adımında düşünme kapalı.
- **Kesilmeye dayanıklılık.** Kesik (`finish_reason=length`) liste cevabı kabul edilmiyor. Bir kez daha geniş bütçeyle deneniyor; yine olmazsa LLM'siz yedek devreye giriyor. Bu yedek, bölüm listelerini benzerliğe göre tekilleştiriyor; çelişkide sonraki bölüm geçerli oluyor, sorumlu ve tarih birleştiriliyor. LLM madde kaybederse (yarıdan az madde döndürürse) de aynı yedek kullanılıyor.
- **Dil koruması.** `ingilizce_mi()` ile çıktının dili kontrol ediliyor. Özet İngilizce gelirse dil kuralı vurgulanarak tekrar isteniyor; yine İngilizceyse bölüm özetleri sırayla kullanılıyor. Bu özetler Türkçe olduğu için not İngilizce çıkamıyor. Parça özetlerinde de aynı kontrol ve düşünmesiz tekrar var.
- **`<think>` temizliği.** Her LLM cevabından düşünme bloğu ayıklanıyor: tam blok, sadece kapanışı gelen blok ve kesilmiş blok.
- **Uzun toplantılar.** Bölüm özetleri bağlam penceresine sığmazsa, token bütçesine göre gruplanıp önce ara özet çıkarılıyor. Eskiden sabit 10 bölümlük gruplar vardı.
- **Dayanak kontrolü.** Özet cümleleri transkript ile bölüm özetlerinin birleşimine göre ölçülüyor. Bütün cümleler elenecek olursa özet boş bırakılmıyor. Karar ve aksiyonlar eskisi gibi yalnızca transkripte göre denetleniyor.
- **Eksik bölümler görünür.** Özetlenemeyen parçanın zaman aralığı notun sonunda "## Eksik bölümler" başlığıyla yazılıyor.
- **Diğer küçük düzeltmeler.** Düzeltme geçişi artık kelime sınırlı çalışıyor: "ata" düzeltmesi "hatalar" kelimesini bozmuyor. `json_schema` geri dönüşü yalnızca 400/422 hatasında yapılıyor; zaman aşımında istek iki kez tekrarlanmıyor. JSON onarımında da düşünme kapalı.

### Ses ve STT (`ses.py`, `motor.py`)
- **Zorla kesim.** Konuşma 12 sn boyunca hiç durmazsa kesim artık son 1,5 sn içindeki en sessiz blokta yapılıyor ve kalan ses sonraki parçaya aktarılıyor. Eskiden kelime ortadan bölünüyor, iki taraf da Whisper'da bozuk çıkıyordu.
- **STT kuyruğu.** Kuyrukta 12 parça birikince parça **atılıyordu**; STT kısa süre yavaşladığında konuşma kayboluyordu. Artık 12'de yalnızca uyarı veriliyor, parça ancak 60'ta atılıyor.
- **Whisper filtreleri.** Whisper'ın kendi kuralı uygulanıyor: bir parça ancak `no_speech_prob > 0.6` **ve** `avg_logprob < -1.0` ise atılıyor. Güven eşiği -1.2'den -1.5'e indi. Eski eşikler gürültülü ya da aksanlı gerçek cümleleri de eliyordu. Bilinen halüsinasyon kalıpları ("İzlediğiniz için teşekkür ederim" vb.) eleniyor.
- **Kısa satırlar.** Altyazının doğrulamadığı satırlar yalnızca 2 kelime veya daha kısaysa eleniyor (eskiden 3). Teams altyazısı bozukken gerçek kısa cümleler korunuyor.

### Teams altyazısı (`yakalayici.py`)
- Teams önceki bir satırı sonradan düzeltince hizalama bozuluyor ve ekrandaki tüm satırlar yeniden "yeni" olarak yayılıyordu. Aynı konuşmacının aynı ya da çok benzer cümlesi artık tekrar yayılmıyor ve hizalama bir sonraki okumada toparlanıyor.

### Testler
`tests/` klasöründe 16 test var ve LLM sunucusu ya da ses cihazı gerektirmiyor: `python -m pytest tests`. İngilizce özet, kesik liste, `<think>` sızıntısı, madde kaybı, dayanak kontrolü, eksik parça, ses bölme, kuyruk ve altyazı tekrarı senaryolarını kapsıyor.

## 4. Açık kalan sorunlar (plana alındı)

| # | Alan | Sorun | Öneri | Öncelik |
|---|---|---|---|---|
| A1 | LLM sunucusu | `reasoning_effort` etkisi ve `--reasoning-parser` doğrulanmadı | Sunucu argümanlarını kontrol et: `--reasoning-parser qwen3`, `--max-model-len`, structured outputs. `context` değeri gerçek `max-model-len` ile aynı olmalı. | Yüksek |
| A2 | Gözlemlenebilirlik | LLM çağrılarının `finish_reason`, token ve süre bilgileri kaydedilmiyor | Toplantı klasörüne `llm_log.jsonl` yaz (içerik değil, yalnızca istatistik). Sorun raporlarında ilk bakılacak yer bu olur. | Yüksek |
| A3 | Transkript | 2 STT işçisi cümleleri **sıra dışı** ekleyebiliyor | Satırı zaman damgasına göre sıralı ekle: kısa bir tampon ya da parça kapanırken `ts` sıralaması. | Yüksek |
| A4 | Transkript | 0,55 sn sessizlikte kesim çok kısa klipler üretiyor; Whisper'ın bağlamı az | Kayıtlı örnek setle (A9) ölçüp `CUMLE_SESSIZLIK`, `TAVAN_SN` ve `MIN_SN` değerlerini ayarla. Konuşmacıyı segment zamanlarından ata; klip uzasa da atama bozulmaz. | Orta |
| A5 | Transkript | Ses seviyesi normalizasyonu yok; kısık sesli konuşmacı zayıf | STT'ye göndermeden önce tepe normalizasyonu (en fazla 8x kazanç). | Orta |
| A6 | Transkript | `ikisi` modunda aynı konuşma hem Whisper'dan hem altyazıdan transkripte giriyor (çift metin) | `ikisi` modunda altyazıyı yalnızca konuşmacı ve doğrulama için kullan, ya da modu kaldır. | Orta |
| A7 | Teams | UI Automation ağacı her 0,6 sn'de baştan yürünüyor (CPU) | Altyazı grubunu önbellekle; değişiklik olayına (StructureChanged) abone ol. | Düşük |
| A8 | Güvenlik | Tüm HTTP çağrılarında `verify=False` | Kurum CA sertifikasını `config.json`'a ekle (`ca_bundle`) ve doğrulamayı aç. | Orta |
| A9 | Kalite ölçümü | Transkript ve not kalitesi ölçülmüyor | 5 toplantıdan 10'ar dk elle transkript çıkar: WER ile Whisper ayarlarını, karar/aksiyon isabetiyle not kalitesini ölç. Her değişiklikten önce ve sonra çalıştır. | Yüksek |
| A10 | Not | Göreli tarih çözümlemesi ("perşembeye") LLM'e bırakılmış | Tarihi kodda deterministik çöz, LLM yalnızca ifadeyi çıkarsın. | Orta |
| A11 | Kod yapısı | `app.py` 1573 satır; arayüz ile iş akışı iç içe | `app.py` → `ui/` (sayfalar) + `servis.py` (toplantı oturumu). `llm.py` ayarları import anında okuyor; ayar değişince yeniden başlatma gerekiyor, `llm.ayarla(cfg)` ekle. | Orta |
| A12 | Dağıtım | Test ve derleme elle yapılıyor | GitHub Actions: `pytest` (Linux) ve PyInstaller (Windows) derlemesi. | Orta |
| A13 | Not | Not uygulamada düzenlenemiyor | Not sekmesinde düzenleme, "yeniden üret" ve karar/aksiyonu transkriptteki satırına bağlama (tıklayınca kaynağı gösterme). | Düşük |

## 5. Yol haritası

**Faz 0: bu değişiklik (tamamlandı).** Not birleştirme, dil koruması, ses kaybı düzeltmeleri ve testler.

**Faz 1: doğrulama ve gözlem (1 hafta)**
1. İş bilgisayarında dalı çek, `pip install pytest` ve ardından `python -m pytest tests` çalıştır.
2. Önceden sorun çıkan 2-3 toplantının eski `not.md` dosyasını yedekle. Sonra notu yeniden üret: uygulamada Geçmiş → **Yarım kalanı tamamla** → İnceleme → **Uygula ve notu üret**, ya da komut satırında `python toplanti.py bitir toplantilar\<klasör>`. Yeni notu eskisiyle karşılaştır. Transkript ve parça özetleri değişmediği için kıyas doğrudan yapılabilir.
3. LLM sunucu argümanlarını kontrol et (A1) ve LLM istatistik günlüğünü ekle (A2).
4. Örnek değerlendirme setini hazırla (A9).

**Faz 2: transkript kalitesi (1-2 hafta).** A3 (sıralı ekleme), A5 (normalizasyon), A4 (ölçümle kesim ayarı), A6 (`ikisi` modu). Her adımda WER'i ölç.

**Faz 3: not kalitesi (1-2 hafta).** A10 (tarih çözümlemesi), toplantı türüne göre not şablonları (haftalık durum, karar toplantısı, 1:1), A13 (düzenleme ve kaynağa bağlama).

**Faz 4: ürünleşme (2 hafta).** A11 (modüllere ayırma, ayarların yeniden yüklenmesi), A8 (TLS), A12 (CI ve derleme), hata raporlama (`hata.log`'u tek tıkla paylaşma), sürüm numarası ve güncelleme kontrolü.

**Faz 5: isteğe bağlı.** Altyazısız konuşmacı ayrımı (diarization), toplantı sırasında canlı ara özet, izin verilirse Microsoft Graph toplantı transkriptinin ikinci kaynak olarak kullanılması.

## 6. İş bilgisayarında hızlı kontrol listesi

- [ ] `config.json` içindeki `context`, sunucunun `--max-model-len` değeriyle aynı mı?
- [ ] vLLM `--reasoning-parser` ile mi çalışıyor? (Değilse düşünce metni artık temizleniyor ama bütçeyi hâlâ harcıyor.)
- [ ] Olaylar panelinde "yerel birleştirme kullanıldı" ya da "bölüm özetleri sırayla kullanıldı" satırları çıkıyor mu? Sık çıkıyorsa LLM ayarına bakılmalı (A1, A2).
- [ ] "STT yavaş, N parça sırada" uyarısı sık görünüyor mu? Görünüyorsa STT servisinin kapasitesi yetmiyor.

## 7. Teşhis sonuçları (2026-09-29, iş bilgisayarı)

| Konu | Bulgu | Sonuç |
|---|---|---|
| vLLM | 0.16.0rc2, `--max-model-len 16384`, `--reasoning-parser qwen3`, MTP spekülatif çözümleme, structured outputs backend `auto` | Düşünme metni ayrı alana düşüyor; N3 (İngilizce `<think>` sızıntısı) bu sunucuda olmuyor. Temizlik yine de güvenlik için kodda kalıyor. |
| `reasoning_effort` | `low` düşünmeyi kısaltıyor (549 → 163 karakter) | N2 bu model için geçersiz: parametre çalışıyor. |
| `json_schema` + düşünme | Düşünme açıkken şema **uygulanmıyor**, model düz metin döndürüyor. Düşünme kapalıyken uygulanıyor. | **Yeni kök neden.** Parça özeti düşünmeli istendiği için bazen düz metin geliyordu. Kod bu metni "JSON onarımı"na gönderiyor, onarım yanlış anahtarlı bir JSON üretiyor, bu da **boş parça özeti** demek. Tam notun eksik kalmasının güçlü bir nedeni. **Düzeltildi:** düz metin gelirse istek düşünmesiz (şema zorunlu) tekrarlanıyor, zorunlu anahtarı olmayan JSON reddediliyor. |
| Tokenizer | Türkçede 3,10 karakter/token | `token_tahmin` (karakter/3) doğru ölçekte. |
| `config.json` | `context` alanı yok, varsayılan 16384 kullanılıyor | Sunucuyla aynı, sorun yok. |
| STT (ARGE) | `/health` 200; `verbose_json` segmentlerinde yalnızca `id, temperature, text, tokens` var. `start`, `end`, `avg_logprob` ve `no_speech_prob` **yok**. | Güven skoruna dayalı Whisper filtreleri bu serviste etkisiz; yalnızca metin sezgileri çalışıyor. Cümle zamanları klip süresine orantılı dağıtılıyor. ARGE'ye sorulmalı: segment zaman ve olasılık alanları açılabilir mi? |
| STT sessizlik testi | 2 sn sessizliğe `...` döndü | Kod bunu zaten eliyor (harf yok). |
| STT `test.wav` | `...` döndü, konuşma tanınmadı | Kayıt sessiz olabilir (yanlış mikrofon) ya da STT sorunlu. Dosya dinlenerek ayırt edilmeli. |
| Kayıtlar | Tek kayıt: 40 katılımcı, 0 parça, 0 satır | Ya kısa bir deneme, ya kayıtlar başka klasörde (`dist\BriefMind\toplantilar`), ya da yakalama hiç satır üretmemiş. `--kayit` ile doğru klasör verilerek tekrar bakılmalı. |

## 8. Birden fazla kişi konuşunca: kimin ne dediği karışır mı?

### Uygulama konuşmacıyı nasıl buluyor
- **Karşı taraf (hoparlör/loopback):** Teams'teki herkesin sesi tek bir karışık akış olarak gelir. Whisper kimin konuştuğunu bilmez. Konuşmacı, Teams canlı altyazısındaki satırlarla eşleştirilerek bulunur: aynı zaman aralığı (±30 sn) ve aynı söz.
- **Siz (mikrofon):** Ayrı bir akış, doğrudan "ben" olarak yazılır.
- **Teams altyazısı** her konuşmacıyı ayrı satırda ve adıyla verir; üst üste konuşmada da ayırır. Konuşmacı bilgisinin en güvenilir kaynağı budur.

### Bulunan riskler ve durumları

| # | Risk | Etkisi | Durum |
|---|---|---|---|
| K1 | Whisper cümlesi altyazıyla **harf düzeyinde** karşılaştırılıyordu. Alakasız iki Türkçe cümle bile %29-35 "benzer" çıkıyor, eşik %25'ti. | Birden fazla kişi konuşurken cümle neredeyse **rastgele birine** yazılıyordu. | **Düzeltildi.** Kelime kökü düzeyinde karşılaştırma: alakasız cümle 0, bozuk yazılmış aynı cümle 0,75-0,8. |
| K2 | Metin hiç eşleşmediğinde cümle, zamanca en yakın konuşmacıya **tahminle** yazılıyordu. | Sessiz dönemde yanlış kişi. Aksiyonun sorumlusu da yanlış çıkabiliyordu. | **Düzeltildi.** O aralıkta tek kişi konuşuyorsa ona yazılıyor. Birden fazla kişi konuşuyorsa `?` ile başlıyor; altyazı gelirse (8 sn içinde) atanıyor, gelmezse `?` kalıyor. |
| K3 | Kulaklıksız kullanımda karşı tarafın sesi mikrofona da giriyor. Eko tekilleştirmesi kaçırırsa satır **"ben"** olarak yazılıyordu. | Başkasının sözü size yazılıyordu. | **Düzeltildi.** Mikrofon satırı altyazıda başka birinin satırıyla güçlü eşleşirse o kişiye yazılıyor. Sizin kendi sözünüz (altyazıda kendi adınızla) "ben" olarak kalıyor. |
| K4 | 2 STT işçisi parçaları farklı hızda çözünce satırlar **ters sırayla** transkripte giriyordu (A3). | Konuşma akışı ve konuşmacı sırası bozuluyordu. | **Düzeltildi.** Satırlar kuyruğa giriş sırasıyla yayılıyor; hata olsa bile sıra takılmıyor. |
| K5 | İki kişi **aynı anda** konuşunca karışık ses tek parça olarak gidiyor. Whisper genelde baskın sesi yazıyor ya da iki sözü birleştiriyor. | Cümle tek kişiye yazılır, diğerinin sözü kaybolabilir. | **Düzeltildi (Aşama 2).** Whisper satırı A'ya yazılınca ±4 sn içinde başka bir konuşmacının (B) Whisper'ın yazmadığı (kökleri ≤ %30 örtüşen) altyazı satırı transkripte `altyazi-cakisma` olarak eklenir; aynı söz sonradan Whisper'dan gelirse ikinci kez yazılmaz. |
| K6 | Tek Whisper parçasında iki kişi arka arkaya konuşuyorsa (araya 0,55 sn'den kısa sessizlik), ARGE servisi segment zamanı vermediği için cümle zamanları tahminle dağıtılıyor. | Sıradaki konuşmacıya geçiş kayabilir. | **Plan.** (a) ARGE'den `verbose_json` segment zamanlarını iste. (b) Bir cümle iki farklı kişinin altyazısıyla eşleşiyorsa kelime hizalamasıyla böl. |
| K7 | Altyazı kapalıysa ya da açılamazsa karşı taraftaki herkes `?` olur. | Konuşmacı ayrımı yok. | Mevcut: uygulama altyazıyı otomatik açmayı dener. **Plan (Faz 5):** ses tabanlı konuşmacı ayrımı (diarization). |
| K8 | Aksiyon sorumlusu yalnızca LLM'in yorumuna dayanıyor. | "Ben yaparım" diyen kişi yanlış atanırsa sorumlu da yanlış çıkar. | **Plan.** Sorumluyu transkriptte o sözü söyleyen satırın konuşmacısıyla doğrula; uyuşmazsa `(?)` ekle. |

## 9. Durum güncellemesi

- **Tamamlandı:** A2 (her LLM çağrısının istatistiği `llm_log.jsonl`'e yazılıyor: görev, düşünme, token, `finish_reason`, süre, İngilizce mi; içerik yazılmıyor), A3/K4 (sıralı yayım), A10 (aksiyon tarihleri kodda çözülüyor; gün adı ile tarih çelişirse gün adı esas alınıyor), K1, K2, K3. Ayrıca STT kopmalarına karşı yeniden deneme, bozuk `config.json` uyarısı, mikrofon cihaz seçimi.
- **Sıradaki:** K8 (sorumlu doğrulama), K5 (çakışmada altyazı), A5 (ses normalizasyonu), A6 (`ikisi` modu), A11 (modüllere ayırma), A12 (CI).
- **Bilgi bekleyenler:** A8 (kurum CA sertifikası), K6(a) ve STT güven alanları (ARGE'ye soru), A9 (örnek toplantılarla ölçüm).

## 10. Diğer toplantı notu uygulamalarıyla karşılaştırma

Karşılaştırılanlar: Microsoft Teams Premium / Copilot (Intelligent Recap), Zoom AI Companion, Otter.ai, Fireflies.ai, Fathom, tl;dv, Read.ai, Granola. Bilgiler bu ürünlerin genel olarak bilinen özelliklerine dayanıyor; güncel sürümleri tek tek doğrulanmadı.

BriefMind'in bu ürünlerde olmayan güçlü yanı: **Veri kurum dışına çıkmıyor.** Ses kaydedilmiyor; STT ve LLM kurum içinde çalışıyor. Bir banka için hazır bulut ürünlerinin çoğu bu nedenle zaten kullanılamaz. Hedef, bu avantajı koruyarak onların not kalitesine yaklaşmak.

| Özellik | Piyasadaki uygulamalar | BriefMind (şu an) | Öneri | Öncelik |
|---|---|---|---|---|
| Özet paragrafı | Her zaman var; kısa TL;DR + ayrıntı | Bazen `-` çıkıyordu | **Düzeltildi:** asla boş değil, yedek zinciri var | — |
| Konu / bölüm akışı (chapters) | Zaman damgalı başlıklar | Yoktu | **Eklendi:** "Konu akışı" (bölüm aralığı + konular) | — |
| Karar / aksiyon ayrımı | Aksiyonlar ayrı ve kişiye atanmış | Kararlarda aksiyonlar da görünüyordu | **İyileştirildi:** somut işler aksiyona taşınıyor. Kişiye göre gruplama (N1) | Yüksek |
| Çok sayıda ince aksiyon | Benzerleri birleştirilir, kişi başına özet | 23 satırlık tablo | **N1:** "Kişi bazlı aksiyonlar" görünümü, aynı işin tekrarlarını birleştirme | Yüksek |
| Maddeden kaynağa gitme | Maddeye tıklayınca kaydın o anı açılır | Yok | **N2:** her karar/aksiyon için kaynak satırı ve zamanı; Not sekmesinde tıklayınca transkript | Yüksek |
| Notu düzenleme | Var | Yok (yalnızca yeniden üretme) | **A13:** Not sekmesinde düzenleme ve kaydetme | Orta |
| Toplantı türüne göre şablon | Var (satış, 1:1, stand-up…) | Tek şablon | **N3:** haftalık durum, karar toplantısı, 1:1, çalıştay şablonları | Orta |
| Önceki toplantıdan devam | Seri toplantıda açık aksiyonları hatırlatır | — | **İstenmedi** (kullanıcı kararı, 2026-09-29) | — |
| Toplantıya soru sorma ("Ask") | Var | Yok | **N5:** Not sekmesinde "Bu toplantıda X hakkında ne dendi?" (transkript + kurum içi LLM) | Orta |
| Toplantılar arası arama | Var | Yok | **N6:** Geçmiş sekmesinde tüm transkript ve notlarda arama | Orta |
| Konuşmacı istatistikleri | Konuşma süresi ve payı | Yok | **N7:** kişi başına konuşma süresi (satır zamanlarından) | Düşük |
| Paylaşım | E-posta, Teams, PDF, Word | Outlook taslağı | **N8:** kişiye özel aksiyon listesiyle e-posta; Word/PDF dışa aktarma | Orta |
| Görev sistemine aktarma | Jira, Asana, Planner | Yok | **N9:** aksiyonları Jira/Planner'a aktarma (kurum izni gerekir) | Düşük |
| Özel sözlük | Var | Var (sözlük + öneri onayı) | Mevcut; güçlü yan | — |
| Canlı not | Toplantı sırasında ara özet | Parça özetleri arka planda | **N10:** Canlı sekmesinde son parçanın özetini göster | Düşük |
| Bilgilendirme ve saklama | Katılımcılara bildirim, saklama süresi | Yok | **N11:** toplantı sohbetine "not alınıyor" bildirimi taslağı, `toplantilar/` için saklama süresi (ör. 90 gün sonra transkripti sil). KVKK açısından önemli. | Yüksek |

### Yol haritasına etkisi
- **Faz 3 (not kalitesi)** öne alındı: N1, N2, N11. (N4 istenmedi.)
- **Faz 4'e eklenenler:** N5, N6, N8, A13.
- **Faz 5'e eklenenler:** N3, N7, N9, N10.

## 11. Durum güncellemesi (2026-09-29, akşam)

Tamamlanan karşılaştırma maddeleri:

| Madde | Nasıl kullanılır |
|---|---|
| N1 Kişiye göre iş listesi | Notta "## Kişiye göre iş listesi": kişi başına iş sayısı ve işler; ortak sorumlu iki kişiye de yazılır, `belirsiz` sonda |
| N2 Kaynağa gitme | Karar ve aksiyonların yanında `⏱10:12:03`. Not sekmesinde tıklayınca transkript o anda açılır (±90 sn, satır vurgulu) |
| N3 Not şablonları | Ayarlar → Not şablonu: Genel, Haftalık durum, Karar toplantısı, Birebir, Çalıştay |
| N5 Toplantıya soru | Not sekmesi → "Bu toplantıya soru sor". Cevap yalnızca transkriptten, zamanlarıyla (tıklanabilir) |
| N6 Toplantılar arası arama | Geçmiş sekmesi → arama kutusu. Transkript ve notlarda arar; büyük/küçük harf ve Türkçe karakter duyarsız; sonuca tıklayınca o an açılır |
| N8 Paylaşım | Not sekmesi → "Kişiye özel e-postalar" (herkese yalnızca kendi işleri, taslak olarak), "Word", "PDF" |
| N11 Bilgilendirme ve saklama | Canlı sekmesi → "Katılımcıları bilgilendir" (metni panoya kopyalar, Teams sohbetine yapıştırılır). Ayarlar → Transkript saklama süresi: notu üretilmiş toplantıların transkripti N gün sonra silinir, not ve özetler kalır. Varsayılan: kapalı. |
| A13 Not düzenleme | Not sekmesi → "Düzenle" / "Kaydet" (not.md'ye yazılır) |

Bu sırada düzeltilen hata: Geçmiş'te bir notu yalnızca açmak toplantıyı "yarım" olarak işaretliyordu.

| Madde | Durum (Aşama 1, 2026-09-29) |
|---|---|
| Not kalitesi S1–S5 | **Tamamlandı.** Boş maddeli aksiyon atılır (tablo/kişi listesinde `-` yok); liste birleştirmede boş madde oranı > %20 ya da dolu madde kaybı varsa yerel birleştirme. Kodda son işlemler: kişi adı + iş fiili olan karar aksiyona taşınır, aksiyona benzeyen karar düşer (`karar_aksiyon_ayikla`); karar sonundaki tek adlı `(Elif)` kalkar; sorumlusu belirsiz, ≤ 4 kelimelik ve kaynağı olmayan aksiyon düşer; açık sorular temizlenir (modelin kendi anlama soruları, alıntı parantezleri, tekrarlar, cevaplanmışlar; en fazla 10). MAP/LISTE promptları buna göre sıkılaştırıldı. |
| K8 Sorumlu doğrulama | **Tamamlandı.** `motor.sorumlu_dogrula`: kaynak satırı birinci tekil şahıs taşıyorsa ("ben hazırlarım", "yapacağım") konuşan kişi esas alınır; `belirsiz` → o kişi, başka biri ve satırda adı geçmiyorsa `Ad (?)`. |
| N10 Canlı ara özet | **Tamamlandı.** Canlı sekmesinde "Şu ana kadar: N karar, M aksiyon, K açık soru" (LLM'siz, her parça özetlenince). |
| A11 Modüllere ayırma | **Tamamlandı.** `isler.py` (QThread'ler), `gorunum.py` (tema, `md_to_html`), `ayar.py` (`config.json`, `tls_dogrulama` yer tutucusu), `metin.py` (metin benzerliği). `llm.ayarla(cfg)`: Ayarlar → Kaydet sonrası LLM adresi/modeli yeniden başlatmadan geçerli. |
| A12 CI | **Tamamlandı.** `.github/workflows/briefmind.yml`: Ubuntu + Python 3.11 ve Windows + Python 3.9 testleri; elle tetiklenince (`workflow_dispatch`) Windows'ta PyInstaller derlemesi, `BriefMind` artefaktı. |
| Otomatik not (A) | **Tamamlandı.** Ayar `otomatik_not` (varsayılan açık): toplantı bitince inceleme beklenmez, not doğrudan üretilir; Not sekmesinde ilerleme şeridi (aşama + geçen süre, motorun `adim` olayları). Not üretilirken pencere kapatılırsa uyarı. Komut satırı: `--inceleme-yok`. |
| Yeniden özetle (B) | **Tamamlandı.** Geçmiş → "Yeniden özetle": eski not `not.md.yedek-YYYYMMDD-HHMM`, sözlük tüm parçalara, **tüm** parçalar yeniden düzeltme + özet, yeni not ("Yarım kalanı tamamla"nın yerine; yarım kayıtlar için de çalışır). |

**Aşama 1 tamamlandı** — ayrıntı ve kabul ölçütleri: [`UYGULAMA_PLANI_v2.md`](UYGULAMA_PLANI_v2.md) (bölüm 0, A, B, C). Bu sırada düzeltilen hata: paralel özetlemede bir parça, önceki parçanın dosyası yazılırken okunup yarım JSON'la özetsiz kalabiliyordu (`parca_oku` artık kilitli).

| Madde | Durum (Aşama 2, 2026-09-29) |
|---|---|
| K5 Aynı anda konuşma | **Tamamlandı.** `motor._cakisanlar`: Whisper satırı A'ya atanınca ±4 sn içinde başka konuşmacı B'nin, kelime köklerinin ≤ %30'u Whisper metninde (ve o aralıktaki diğer Whisper satırlarında) geçen altyazı satırı transkripte `kaynak="altyazi-cakisma"` ile, zaman sırasıyla eklenir. Altyazı izi girdileri artık sözlük (`ts, speaker, text, kullanildi, son`); `kullanildi="altyazi"` olan girdi bir daha eklenmez ve ona ≥ 0,45 benzeyen sonraki Whisper satırı yazılmaz. Kullanıcının kendi altyazısı eklenmez (mikrofon akışı ayrı). |
| A6 `ikisi` modu | **Tamamlandı.** Altyazı satırı hemen girmez, 8 sn bekler: bu sürede (ya da öncesinde) ≥ 0,45 benzer Whisper satırı gelirse düşer; gelmezse `kontrol()` onu `kaynak="altyazi"` ile ekler. Bitişte bekleyenler süre dolmadan eklenir. `altyazi` modu değişmedi (hemen ekler). |
| A5 Ses normalizasyonu | **Tamamlandı.** `ses.normalize_et` (`_gonder`'de): tepe < 0,3 ise kazanç `min(8, 0,9/tepe)`, `[-1, 1]` kırpma; yüksek ses değişmez. |
| A7 UIA yükü | **Tamamlandı.** `Yakalayici.oku` süresi ölçülür; son 10 okumanın ortalaması > 250 ms ise `onerilen_aralik()` 1,2 sn, değilse 0,6 sn. `isler.YakalamaIsi` ve `toplanti.py` döngüleri bunu kullanır. |
| A8 TLS | **Tamamlandı.** `config.json` → `ca_bundle`; `ayar.tls_dogrulama()` dosya varsa yolu, yoksa `False` döndürür. `llm` (httpx, `llm.ayarla` her kurulumda), `ses` (requests: sağlık + STT), `tools/teshis.py`, `tools/ses_teshis.py` kullanır. Açılışta Olaylar'a bir kez "TLS doğrulaması kapalı (config.json'da ca_bundle ayarlı değil)". Bozuk sertifika dosyası uygulamayı düşürmez (uyarı + kapalı). Edge'den kök sertifika dışa aktarma adımları README'de. |
| N7 Konuşma payı | **Tamamlandı.** `not_araclari.konusma_paylari`: satır süresi = sonraki satıra kadar (≤ 15 sn), son satır kelime sayısından. Nota yazılmaz; `notu_uret` → `meta.json` `konusma_paylari`; Geçmiş detayında "Konuşma payı: Elif %38, Berk %27, …". |
| N9 Jira/Planner | **Tamamlandı.** `not_araclari.jira_csv`: UTF-8 BOM'lu CSV, `Summary, Assignee, Due Date, Description, Issue Type` (`Task`); tarih yalnızca `YYYY-MM-DD` ise; Description "BriefMind — <başlık> (<tarih>)" + kaynak zamanı. Not sekmesinde "Jira/Planner CSV" düğmesi (varsayılan `aksiyonlar.csv`). REST entegrasyonu yok. |

**Aşama 2 tamamlandı.**

Bekleyen: **A9** kalite ölçümü (gerçek toplantılarla Whisper/altyazı uyumu, K5/A6'nın kaç satır eklediği, not kalitesi karşılaştırması) — kullanıcının kendi toplantı verisiyle yapılacak; repoya içerik girmez.

## 12. LLM sunucusu değişikliği (2026-10-08)

Kullanıcı LLM'i yeni bir vLLM sunucusuna (farklı model adı, yolu `/v1` ile biten yeni adres; 100-200 token/sn) taşıdı; özet çıkarırken hata alınıyordu (hata metni elimizde yoktu, sunucuya yalnız iş bilgisayarından erişiliyor). Koddan çıkarılan olası nedenler, olasılık sırasıyla: (1) `model` eski ad → her istek 404, parçalar sessizce özetsiz, not yine yeşil "Not hazır"; (2) OpenShift route zaman aşımı (varsayılan 30 sn) + SDK'nın 2 yeniden denemesi: kısa testler geçer, düşünmeli uzun bölüm özeti 504 alır; (3) `context` sunucunun penceresinden büyük → 400, ve her 400'ün "şema desteklenmiyor" sanılıp şemasız tekrarlanması; (4) eski `ca_bundle` yeni sertifikayı doğrulamıyor → yalnızca "Connection error.".

| Değişiklik | Ayrıntı |
|---|---|
| Sunucu tanısı | `llm.sunucuyu_tani()`: ilk LLM isteğinden önce (ve açılışta/Kaydet'te arka planda) `GET /v1/models`. Config modeli yoksa ve sunucu tek model sunuyorsa o seçilir; pencere = `max_model_len`, config `context` yalnızca üst sınır. Uyarılar Olaylar'a ve `llm_log.jsonl`'e (`olay: ayar`). `ayarla()` ağ isteği yapmaz. |
| Adres ve tokenizer | `route_normalize`: sondaki `/` atılır, `/v1` eklenir. `/tokenize` yolu sondaki `/v1` kesilerek kurulur (yol öneki korunur; eskiden `str.replace` her `/v1`'i değiştiriyordu); `messages` biçimiyle şablon dahil sayım; başarısızlık bir kez bildirilir, 5 dk tahmin; kısa zaman aşımı (5/10 sn), sayım önbelleği. |
| Hata metinleri | `llm.LlmHatasi` + `hata_metni(e)`: 404 model/yol, 400 bağlam (sunucu ve uygulama penceresiyle), 401/403 anahtar, 502-504/HTML (route zaman aşımı ipucu), zaman aşımı, bağlantı (gerçek neden `__cause__`'dan; TLS/proxy ipucu). Olaylar, "Not üretilemedi" penceresi, soru-cevap ve `llm_log.jsonl` (`http`, `tur`, 600 karakter) bu metni kullanır. |
| 400 ayrımı | Yalnız şema kaynaklı (ya da nedeni belirsiz) 400/422'de şemasız tekrar; şema reddedildiyse oturum boyunca gönderilmez (`sema_dustu`). Bağlam taşmasında tekrar yok; sunucunun penceresi metinden okunur ve istek bir kez kırpılmış bütçeyle denenir. |
| Akışlı istek | `llm_akis` (varsayılan açık): `stream=True` + `include_usage`; route'un boşta kalma zaman aşımına takılmaz. `max_retries` 2 → 1 (`llm_tekrar`), okuma zaman aşımı 900 → 300 sn (`llm_zaman_asimi`). `llm_proxy`, `llm_key` eklendi. |
| Bütçeler | `llm.butce(ad)`: pencereye göre üç kademe (<12k / 12k-32k / ≥32k). 16k'da eski değerler; 32k+: bölüm 4000, liste 8000, genel 2000, soru 2500 (+1500 düşünme), parça 4000. 8k'da soru düşünmesiz. Soru-cevap girdi bütçesinden ilk çağrının gerçek `max_tokens`'ı düşülür; girdi en fazla 48k token. Liste birleştirmede düşünme açılmadı (eski sunucuda düşünme açıkken `json_schema` uygulanmıyordu; yeni sunucuda teşhis ölçer). `motor.PARCA_TOKEN`/`PARALEL` 0 = otomatik; `parca_token`, `paralel` config'ten. |
| Sağlamlık | Genel özetin ara özet çağrısındaki hata artık notu durdurmaz. Düşünce etiketleri `<think>`, `<thinking>`, `<reasoning>`; içerikte düşünce görülürse `dusunce_icerikte`. |
| Arayüz | Ayarlar: "Bağlam penceresi" (0 = sunucudan otomatik), "LLM sunucusunu test et", model alanı boş bırakılabilir. Eksik bölümlü not turuncu "Not eksik" + son LLM hatası; hiç bölüm özetlenemediyse uyarı penceresi ve Outlook taslağı açılmaz. |
| Teşhis | Yeni `tools/llm_teshis.py` (bkz. [Teşhis rehberi](TESHIS_REHBERI.md#yeni-llm-sunucusu)); `tools/teshis.py`'nin LLM bölümü onun kısa kipi. |
| Testler | `tests/test_llm_sunucu.py` (31 test): adres/tokenize yolu, sunucu tanısı, hata sınıflandırması, 400 ayrımı, akış, bütçeler, teşhis aracının ağsız kısımları. |

Bekleyen (teşhis çıktısına bağlı): `reasoning_effort` / `enable_thinking` yeni şablonda etkisizse bölüm özetinin ilk çağrısı düşünmesiz yapılacak; `json_schema` düşünmeyle uygulanıyorsa liste birleştirmede düşünme denenebilir; 2 paralelde akış başına hız ≥ 100 token/sn ise `paralel: 3`.

## 13. Altyazı taşması (2026-09-30)

**Belirti (kullanıcının 14 katılımcılı, 1 saatlik toplantısı; içerik repoya girmedi, yalnız sayılar):** `altyazi.jsonl` 26.247 satır — kaynak `altyazi-cakisma` 25.308, `ses` 939; (konuşmacı, metin) birebir tekrar 24.801; önek zinciri (aynı satırın yarım halleri) 125; dakikada en çok 1086 satır; 223 parça (her biri ~3000 token, 18-25 saniyelik aralıklar). Not bu tekrarlarla üretildiği için kullanılamaz durumdaydı.

**Kök neden (koddan):** `Yakalayici._hizala` görünen satırları son 30 yayılan satırın (kuyruk) **kuyruğu** ile görünenlerin **başı** arasında hizalıyordu — pop-out penceresinin birkaç satır gösterdiği eski varsayım. Teams toplantı penceresinin altyazı paneli kalabalık toplantıda 30'dan fazla satır gösteriyor: hizalama her okumada 0 → panelin tamamı "yeni" → `_zaten_var` yalnızca son 30 satıra baktığı için kuyruk dışında kalan eski satırlar her 0,6 sn'de yeniden yayıldı (kayan taşma). Her yayılan satır `konusmaci_izi`'ne `kullanildi=None` ile yeni giriş oldu; K5 (`_cakisanlar`, aynı anda konuşma) her Whisper satırında ±4 sn içindeki bu girişleri "Whisper'ın kaçırdığı söz" sanıp `altyazi-cakisma` olarak transkripte ekledi. `ikisi` modunda aynı taşma A6 (`_bekleyen_altyazilar`) üzerinden olurdu; `altyazi` modunda doğrudan transkripte.

**Düzeltme:**

| Katman | Ne değişti |
|---|---|
| Yakalayıcı (`yakalayici.py`) | Yayım kararı panel boyutundan bağımsız: uzun satır (normalize ≥ 20 karakter) `(konuşmacı, normalize(metin))` son 2000 yayılmış satırda yoksa aday; kısa satır ("Evet.", gerçekten tekrar edilir) paneldeki bir önceki satırın kaydına göre tanınır (panelin başındaysa yalnız metniyle). **Kararlılık:** aday iki ardışık okumada aynı metinle görülünce yayılır; panelin son satırı (canlı) altına yeni satır gelene kadar bekler; konuşmacı başına canlı satır (`canli` sözlüğü, çok kişi aynı anda) ilk halinin zamanını korur; üstte hâlâ büyüyen satır alttakileri en fazla 10 okuma bekletir (panel sırası). Kararlı olmadan panelden kayan satır yine de yayılır. **Büyüme/düzeltme:** aday, aynı konuşmacının son 90 sn'de yayılmış son 3 satırından birinin önek-uzantısı ya da ≥ 0,9 benzeri ise ve o satır aday belirdiğinde hâlâ paneldeyken kaybolduysa yeni satır değil `{'guncelle': True, 'onceki_text'}`. |
| Motor (`motor.py`) | `satir_ekle` güncellemeyi açık parçada (`mevcut`) yerinde uygular (metin, ham hal, parça token sayısı), `altyazi.jsonl`'e `guncelle: true` kaydı ekler, `satir_guncelle` (`text`) olayı yayar; parça kapanmışsa eski hali kalır (Olaylar'a bir satır). K5/A6 ile eklenen satırın güncellemesi de transkripte yansır. **Güvenlik ağı:** son 60 sn'de > 150 altyazı satırı → bir kez "altyazı taşması: N satır/dk, altyazı eklemeleri askıya alındı"; K5/A6 eklemeleri son aşırı hızlı satırdan 60 sn sonrasına kadar askıda (Whisper satırları etkilenmez; askıdaki satırlar sonradan da eklenmez; `altyazi` modunda taşma sürerken birebir tekrarlar elenir). **K5 sınırları:** Whisper satırı başına en fazla 3 altyazı satırı; aynı konuşmacıdan ±4 sn içinde en fazla 1; son 2 dk'da transkripte girmiş bir satırla birebir aynı metin eklenmez (A6 için de). |
| Kurtarma | `motor.tasma_var_mi(klasor)`: dakikada > 120 satır ya da birebir tekrar > %30 (≥ 50 satırda). `motor.transkript_temizle(klasor, yedekle=True)`: birebir tekrarlar (ilki kalır; 20 karakterden kısa onaylarda yalnız 10 dk içindeki tekrar) ve aynı konuşmacının 10 dk içindeki önek-zinciri yarım halleri (en uzunu kalır; kelime ortasında kesilmiş hal yalnız 60 sn içinde) elenir, satırlar zamana göre sıralanır; `parcalar/` → `parcalar.yedek-YYYYMMDD-HHMM`, temiz satırlardan `PARCA_TOKEN`/`PARCA_SURE_SN`/sessizlik kuralıyla yeniden parçalanır (özetsiz); `altyazi.jsonl` ve `oneriler.json` yedeklenir, öneriler sıfırlanır, `meta.json` parça sayısı güncellenir. Saklama süresi bu yedekleri de siler (KVKK). |
| Arayüz | Geçmiş'te seçili kayıt taşmalıysa bilgi satırında uyarı. **Yeniden özetle** başlamadan taşmayı algılarsa önce temizler, Olaylar'a `taşma temizlendi: 26247 → N satır, 223 → M parça` yazar, sonra temiz parçaları özetler. |
| Araç | `python tools\transkript_temizle.py <klasör> [--kuru]`: ölçümü ve yapılacakları sayılarla gösterir; `--kuru` hiçbir şey yazmaz. `tools/teshis.py` kayıt özetinde taşma satırı. |
| Testler | `tests/test_tasma.py`: 40 satırlık panel + 14 konuşmacı ile 20 okuma (her satır bir kez), kayan panel, kısa onay tekrarları, büyüyen satır güncellemesi, çok konuşmacıda canlı satır; motor hız sınırı, K5 sınırları, güncellemenin açık/kapanmış parçada uygulanması; `transkript_temizle` sayıları ve yedekleri, `tasma_var_mi`, saklama; "Yeniden özetle"nin temizliği çağırması (sahte LLM). |

**Kurtarma adımları (kullanıcı):** 1) Kodu güncelle. 2) Uygulamada Geçmiş → taşmış kaydı seç (bilgi satırında "⚠ Altyazı taşması") → **Yeniden özetle**: temizlik + yeni not otomatik. İstenirse önce `python tools\transkript_temizle.py toplantilar\<klasör> --kuru` ile sayılara bakılır. Eski hali klasörde `*.yedek-*` olarak kalır; sorun yoksa elle silinebilir (saklama süresi de siler).

**Aynı iş akışındaki LLM düzeltmeleri (önceki incelemenin engelleyici olmayan bulguları):** `sunucuyu_tani()` bayrağı artık `GET /models` bitince kalkar ve `sor()` tanıyı kilit altında koşulsuz çağırır (tanı sürerken giden istek boş/eski model adıyla 404 alıyordu); `ayarla()` tanı sürerken çağrılırsa tanı yeni ayarla yinelenir. `route_normalize` sona yapıştırılmış uç noktayı (`/chat/completions`, `/completions`, `/models`, `/tokenize`) kırpar ve Olaylar'a maskeli "adres düzeltildi" yazar (route'a tam uç nokta yapıştırmak `…/v1/chat/completions/v1` üretiyordu). `token_say` önbelleği tek okuma (`.get`). Akış içi vLLM `error` olayı (HTTP durumu olmayan `APIError`) gövdeden sınıflandırılır; bağlam taşmasıysa pencere öğrenilip bir kez kırpılmış bütçeyle denenir. Ayarlar → "LLM sunucusunu test et" tanı sürerken sessiz kalmaz, tanı bitince test başlar. `tools/llm_teshis.py`: akışlı istek akışla ilgisiz nedenle (404/401) ya da akışsız istek de başarısızken `llm_akis: false` önerilmez; config modeli listede yoksa ve birden çok model varsa ilki kullanılıp [YÜKSEK] bulgu; `ca_bundle` ayarlı ama dosya yoksa [YÜKSEK]; `config.json` yoksa açıkça [YÜKSEK]; tam uç nokta yapıştırılmış route [YÜKSEK]; çıktıda kalan her `http(s)://` adresi `<ADRES>` (llm_log hata metinleri, ham gövdeler); maskeleme büyük/küçük harf duyarsız; `--hizli`'de bölüm numaraları ardışık; `tools/teshis.py` kısa kipi de alan adını maskeler.

