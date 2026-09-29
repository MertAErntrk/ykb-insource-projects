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
