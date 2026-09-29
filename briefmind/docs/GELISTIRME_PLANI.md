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
| K5 | İki kişi **aynı anda** konuşunca karışık ses tek parça olarak gidiyor. Whisper genelde baskın sesi yazıyor ya da iki sözü birleştiriyor. | Cümle tek kişiye yazılır, diğerinin sözü kaybolabilir. | **Plan.** Çakışma anını altyazıdan tespit et: aynı aralıkta iki farklı konuşmacı satırı varsa o aralık için Teams altyazısı metnini kullan (karma kaynak). |
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

Kalanlar (Aşama 2): K5 (çakışmada altyazı), A5 (ses normalizasyonu), A6 (`ikisi` modu), A7 (UIA yükü), A8 (CA sertifikası, bilgi bekliyor), N7 (konuşma payı), N9 (Jira/Planner CSV).
