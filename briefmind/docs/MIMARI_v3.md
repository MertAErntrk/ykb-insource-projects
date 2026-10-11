# BriefMind — Hedef Mimari v3.1 ve Uygulama Planı

Girdi: 7 analiz ajanı + 2 bağımsız eleştiri (gerçeklik, kapsam). Kod değiştirilmedi. Satır numaraları mevcut HEAD'e göredir. Eleştiri itirazlarının kabul/ret listesi §11'de.

---

## 1. Hedef ve kabul ölçütleri

### "Hatasız" ne demek (dürüst tanım)
ARGE Whisper zaman damgası ve olasılık vermiyor. Loopback tek mono karışık kanal. Eşzamanlı konuşmada Whisper yalnız baskın sesi yazar. Bu gerçeklerle "hatasız":
1. **Kesin gösterilen her şey doğru.** Emin olunmayan satır `?` / `Ad?`, sıralı devir `A → B`, gerçek eşzamanlılık `A / B`, şüpheli kelime `⟨⟩`. Yanlış adı ya da yanlış terimi "kesin" göstermek tek kabul edilmeyen hata.
2. **Belirsizlik ölçülü.** Altyazı açıkken belirsiz oranı sınırlı; altyazı kapalıyken uzak satırların tümü `?` ve UI'da açık uyarı ("konuşmacı bilgisi yok"). Faz6 spike geçmeden "her konuşmacı doğru" hedefi altyazı kapalıyken karşılanmaz; bu açıkça kabul edilir.
3. **Nota belirsizlik taşınır.** Sahibi belirsiz aksiyon e-postaya/Jira'ya gitmez.

### Ölçülebilir hedefler (hepsi KLİP düzeyinde)

| Ölçüt | Tanım | Hedef | Ölçüm |
|---|---|---|---|
| SER-kesin | `spk_guven ≥ 0.7` satırlarda yanlış ad | ≤ %1 (altyazı açık) | tools/olcum.py + düzenek |
| Belirsiz | `?`, `Ad?`, `A / B` satır oranı | Faz1 sim ≤ %12 (klip bazlı, yeniden kesme sonrası); gerçek kayıtta ölçülüp hedef güncellenir | düzenek + replay |
| Yanlış "aynı anda" | sıralı devir klibinin `A / B` gösterilmesi | 0 (sıralı devir `A → B`) | düzenek |
| WER | Whisper vs elle düzeltilmiş 10 dk | Faz0 taban; Faz2+3 sonrası göreli −25% | olcum.py (ses kaydı izinliyse) |
| Bozulan satır | düzeltme hattının doğru kelimeyi bozması | 0 düzenek, ≤ %0.5 gerçek | `bozulan` sayacı |
| Halüsinasyon | sessizlik klibinden kesin satır | 0 kesin (şüpheli işaretli olabilir) | sentetik fixture + Faz0 etiketi |
| Not sahipliği | `spk_guven < 0.7` satırdan kesin sahip | 0 | pytest fixture |
| Çift satır | aynı söz iki kez | 0 | `cift` |
| Kayıp kısa onay | 0.3–0.6 sn `evet/tamam` kaybı | ≤ %20 | Faz0 |

### Faz0 zemin gerçeği (yerel, repo dışı, `.gitignore`)
- `olcum/klipler/NNNN.wav` + `klipler.jsonl` `{no, akis, bas, bit, ornek_bas, ornek_bit}` (bas/bit epoch; ornek_* akış örnek sayacı).
- `olcum/okumalar.jsonl`: her UIA okuması ham: `{okuma_no, bas_epoch, bit_epoch, gorunen: [[kim, metin_uzunluk, metin_hash]]}`. Metin değil hash (gizlilik); teşhis modunda metin.
- `olcum/olaylar.jsonl`: atama kararları, L/MAD, R ölçümleri, STT kuyruk/kesinti olayları.
- `olcum/gercek.jsonl`: `tools/etiketle.py` ile klip bazlı etiket: `{klip_no, speaker, cakisma, halusinasyon, text}`. Klip seç + konuşmacı seç + metni düzelt; elle ms yazılmaz.
- `tools/olcum.py`: WER (Türkçe normalize), SER-kesin, belirsiz, yanlış-aynı-anda, halüsinasyon, L/MAD. JSON çıktı, ad içermez.
- `tools/replay.py`: `klipler/` + `okumalar.jsonl` üzerinden `konusmaci.py` ve (isteğe bağlı) STT'yi çevrimdışı koşturur. Faz1-3 kapıları bununla ölçülür; halka tampon RAM'de olduğundan "eski kaydı yeniden çözme" yalnız bu kayıtla mümkündür.
- Ses kaydı yalnız `config.olcum_kaydi: true`. Politika izin vermezse: ses kaydı yok, WER kapıları yerine (a) `bozulan=0` mikro testleri, (b) kullanıcının kendi sesiyle 5 dk okuma kaydı (`tools/whisper_deneme.py`) üzerinden WER; SER/belirsiz yine ölçülür.
- Faz0 ölçüm listesine eklenenler: Teams aynı kişinin duraksama sonrası satırı bölüyor mu; kapanış revizyon gecikmesi; kişi başı L; büyük toplantıda `YAVAS_ARALIK=1.2` ile A/B oranı; okuyucu heartbeat boşlukları.

---

## 2. Mevcut durumdan bulgular (karar belirleyenler)

**Konuşmacı neden karışıyor:** zaman motor'a yalnız `HH:MM:SS` olarak geliyor, klip bitişi yok (ses.py:581, :215); `parca_bas` varış anı `now()` eksi tampon (ses.py:170), `toplam_sn` sayacı var ama kullanılmıyor (:154); `cumlelere_bol` karakter oranıyla dağıtıyor (ses.py:300-311); atama altyazı METNİNE dayanıyor, benzerlik ≥0.45 kesin sayılıyor (motor.py:586-592), <0.25 ve 2+ kişi → `?` (motor.py:495); `_tek_konusmaci` boş pencerede `all([])=True` (motor.py:534-537); gelecekteki altyazıya ×0.6 ceza (motor.py:584-585); altyazı zamanı UIA okuması bittikten sonra alınıyor (yakalayici.py:197), satır kimliği metin+büyüme zinciri (yakalayici.py:202, :274-280); kullanıcı adı adaylardan çıkmıyor, mikrofon satırı başkasına çevriliyor (motor.py:478-481); K5/A6 bozuk altyazı metnini yazıyor, `K5_UST=0` kapatmıyor (motor.py:408-444, :439-442); `kulaklik_var_mi` hata/kısa kayıtta True, varsayılan cihazı test ediyor, duyulur ton basıyor (ses.py:69-88). Sim: metin %100 bozukken 45/48 `?`; yalnız zaman kesişimiyle 0 yanlış ama klip düzeyinde 130 klipten 28 `A/B` (14'ü sıralı devir).

**Kelimeler neden bozuluyor:** sözlük bulanık eşlemesi ek siliyor ve yanlış pozitif üretiyor (sozluk.py:150-191; `komite→commit`); LLM düzeltmesi tek çifti parçanın tüm satırlarına IGNORECASE global replace (llm.py:842-847, motor.py:680-684); prompt Teams altyazısı varsayıyor (llm.py:426-433); kısa bağlamsız klipler, prompt kapalı (ses.py:40, :415-423); `tekrar_temizle` 1-gram siliyor (ses.py:252-268); halüsinasyon filtresi ölü (ses.py:326-334); mikrofon `np.interp` anti-aliasing yok (ses.py:127-132).

**Not hattı:** `sorumlu_dogrula` güvene bakmıyor (motor.py:991-1022); `A / B` sorumluyu çoğaltıyor (llm.py:1144-1151); `(?)` e-posta/Jira'da siliniyor (not_araclari.py:128-133, :203); liste birleştirme sessizce LLM'siz yedeğe düşüyor (llm.py:995-1000).

**Kabuk:** `YakalamaIsi.run` korumasız, `finally` yalnız `ekran_okuyucu` kapatıyor, `motor.bitir` çalışmayabilir (isler.py:41-152); `_excepthook` GUI dışı thread'de QMessageBox (app.py:1658-1666); `inceleme_hazir` toplantı sonunun tek el değiştirme noktası: motor bağlar, Başlat'ı açar, `_not_uret` tetikler (app.py:872-899, :976-992); STT sağlık hatasında altyazı moduna düşüş (ses.py:493-496, isler.py:64-72); kuyruk 60'ı aşınca klip düşüyor (ses.py:209-212).

---

## 3. Hedef mimari

### Akış
```
 [loopback 16k]──┐                                  ┌──[Teams paneli UIA]
 [mikrofon →16k] ┤ (FIR + decimation)               │  yakalayici.py: okuma {bas,bit epoch, gorunen}
                 ▼                                  ▼
   ses.py  VAD kısa klip, halka tampon (son 10 dk, RAM)
           klip = {akis, bas, bit(son sesli blok), ornek_bas, ornek_bit, no, ses}
                 │ STT kısa (canlı)           │ STT uzun pencere (Faz3, bayraklı)
                 ▼                            ▼
   stt.py  SttIstemci + halusinasyon_puani
                 │ SatirOlayi{klip_no, akis, bas, bit, text, supheli}
                 ▼
   konusmaci.py (YENİ) ◄── Cizelge: kişi başı aralık birleşimi, kimlik_turu, L/MAD, R, heartbeat
     karar: speaker | "?" | "Ad?" | "A → B" | "A / B"; klip içi devirde yeniden kesme
                 ▼
   motor.py  parça yönetimi, satır kaydı (raw+text+alanlar), jsonl
                 ▼ parça kapanınca
   duzelt.py (YENİ)  terim.py adayları + LLM satır bazlı düzeltme + diff kapısı (VE kuralı)
                 ▼
   llm.py  bolum_ozetle → birlestir → genel_ozet → not_markdown
   motor.py sorumlu_dogrula / dayanak (güven tüketir)  → not.md, e-posta/Jira (yalnız kesin sahip)
```

### Zaman tabanı
- Ses: akış başında `epoch_capa = time.time()`, her blokta `t = epoch_capa + toplam_sn` (ses.py:154 sayacı kullanılır). Her 60 sn `now() − t` farkı ölçülür; |fark| > 0.3 sn ise yeniden çapalanır ve `olaylar.jsonl`'e yazılır. `parca_bas` artık `now()`'dan türemez (ses.py:170 değişir).
- Altyazı: her okumanın `bas_epoch`/`bit_epoch` tutulur (yakalayici.py:197 değişir). R = son 10 okumanın gerçek periyodu (okuma süresi + uyku), sabit 1.2 değil.
- Heartbeat: okuyucu her okumada `son_okuma_epoch` yayar. 3×R boyunca okuma yoksa ya da `grup is None` ise çizelge "kapsama yok" durumuna geçer; bu aralıkta kararlar `?`, halüsinasyon kuralı altyazıya dayanmaz.

### Satır veri modeli (`altyazi.jsonl`, `parca_*.json`)
```json
{"id": 123, "klip_no": 57, "akis": "loopback|mikrofon", "bas": 1760170000.3, "bit": 1760170004.1,
 "ts": "10:03:44", "raw": "ham whisper", "text": "düzeltilmiş",
 "speaker": "Ad" | "?" | "A → B" | "A / B", "spk_guven": 0.0-1.0, "adaylar": [["A", 0.72], ["B", 0.18]],
 "devir": false, "cakisma": false, "supheli": false, "belirsiz": ["kelime"],
 "duzeltmeler": [{"eski": "x", "yeni": "y", "neden": "terim|ad"}], "kaynak": "ses|isaret|altyazi-yedek", "surum": 2}
```
`kaynak:"altyazi"`/`"altyazi-cakisma"` ve `ikisi` modu kalkar. **Geriye uyum:** `surum` alanı olmayan eski kayıtlarda `Motor.yukle` (motor.py:938) `bas=bit=_sn(ts)`, `spk_guven=0.5`, `durum="eski"` türetir; yeniden özetleme ve rag.py bu satırları "Ad?" gösterir, sahip üretmez.

### Gösterim kuralı (tek kural, her yüzey)
`spk_guven < 0.7` → `Ad?` (akış paneli, not.md transkripti, jsonl `speaker` alanı değil, görüntü katmanı). `?` → `Bilinmeyen`. `A → B` sıralı devir; `A / B (aynı anda)` yalnız `cakisma=true`. Erteleme süresince canlı ekranda `beklemede…` gösterilir, geçici ad yazılmaz; karar gelince `satir_guncelle`. `?`, `Ad?`, birleşik adlar `katilimcilar` listesine girmez (motor.py:315 ve :611-612 aynı eleme).

### Çizelge (yakalayici → konusmaci)
- Yakalayıcı olay değil **okuma** yayar: `{okuma_no, bas_epoch, bit_epoch, gorunen:[(kim, metin)]}`. Kararlı satır kimliği yok; konusmaci kişi başı aralık birleşimi tutar: aynı kişinin ardışık okumalarda görünen satırı `son`'u yalnız **kelime sayısı arttığında** ilerletir (noktalama/yeniden yazım `son`'u uzatmaz). Diske olay değil aralık yazılır; toplantı başına aralık tavanı (ör. 20k), aşılınca log + taşma modu (motor.py:45-52 TASMA mantığı korunur).
- `kimlik_turu = kisi | oda | genel`: Outlook davetlisinde ya da `katilimcilar`'da birebir varsa `kisi`; "Konuk", "Konuşmacı N", "Bilinmeyen", oda cihazı kalıpları → `genel/oda`. `_isim_gibi` (yakalayici.py:150-171) sezgisel adı yalnız ≥3 ayrı satırda görülürse ad sayar; aksi `?`. `oda/genel` etiketli satır "Ad (oda)" gösterilir, `spk_guven` tavanı 0.6, sahip asla kesin olmaz.

### Konuşmacı atama (yalnız zaman)
Girdi: klip `[bas, bit]`; çizelge aralıkları `[ilk − L − R/2, son − L + R/2]` (iki uçta simetrik gözlem bandı); L, MAD.
1. **Mikrofon klibi** (kulaklık bayrağından bağımsız): kullanıcının kendi altyazı aralığı klibi ≥0.5 kapsıyorsa → `ben`, 0.95. Kullanıcı altyazısı yok ve uzak kişi aktifse → **yankı, klip atılır** (loopback kopyası var; kimseye yazılmaz). Kullanıcı altyazısı yok, uzak kişi yok → `ben`, 0.6 (odada başka biri olabilir; sahip üretilmez). Pasif korelasyon (mikrofon RMS ↔ loopback RMS, 30 sn pencere) Faz1'de, `kulaklik_var_mi` tonu kaldırılır. Tespit kesinleşene kadar mikrofon `spk_guven` tavanı 0.7'nin altındadır.
2. **Loopback klibi**: `_ben_mi` adaylar çıkarılır; aday payı = kesişim / klip süresi.
3. **Karar**: baskın ≥ 0.60 ve ikinci < 0.25 → ad, `spk_guven = baskın − ikinci`. Baskın 0.25–0.60 ve ikinci < 0.25 → `Ad?`, `spk_guven = baskın`. İki aday ≥ 0.25 → **devir tespiti**: çizelgede A→B geçiş anı klip içindeyse `devir=true`, klip geçiş ±1 sn içindeki en düşük enerjili blokta (halka tampondan) bölünür, iki parça yeniden STT'ye gider, her biri ayrı karar alır; bölünemezse `A → B`, `spk_guven 0.5`, sahip belirsiz. Aralıklar gerçekten kesişiyorsa `A / B`, `cakisma=true`, metnin tümü `⟨⟩`. Kimse yok → `?`.
4. **Erteleme**: karar `bit + L + R` kapsanınca, en geç `bit + 15 sn`. Parça kapanışı bekler; değişirse jsonl'e güncelleme kaydı.
5. **L tahmini**: iki çapa: (a) ≥1 sn sessizlik sonrası klip başı ile sonraki ilk `ilk`; (b) duraklamış satırın yeniden büyümeye başladığı an ile klip başı. >5 sn örnek atılır (kırpılmaz). Kayan medyan + MAD; MAD > 1 sn ise `spk_guven` tavanı 0.6. Başlangıç 2 sn; ilk 3 çapaya kadar tavan 0.6.
6. **Doğrulama**: Faz1 kabulünden önce `oracle.py` planın tam kuralıyla yeniden koşar (klip=birim, R ölçülen, son=kelime artışı, devir bölme dahil). Mevcut oracle (cümle bazlı, R=0.6, son=gerçek bitiş) kanıt sayılmaz.

Kaldırılanlar: metin benzerliği (motor.py:586-592), ×0.6, ±30 sn pencere, `_tek_konusmaci`, `bekleyen_ses`, K5 `_cakisanlar`, A6 `_bekleyen_altyazilar`, `cumlelere_bol`.

### STT kesintisi
STT sağlık/istek hatası altyazı moduna düşürmez (ses.py:493-496, isler.py:64-72 değişir). Klipler `KUYRUK_TAVAN` yerine yerel diske (`olcum_kaydi` ya da `kesinti_kaydi` izinliyse) yazılır, servis dönünce sırayla çözülür. İzin yoksa UI'da "STT yok — transkript eksik (hh:mm–hh:mm)" durumu ve nota aynı not. Altyazı metni yedeği yalnız `config.altyazi_yedek: true` ise, satırlar `kaynak:"altyazi-yedek"`, `supheli=true`, nota "(altyazıdan, düşük güven)" ile girer.

### Ses gömmesi: Faz6 kapılı spike (değişmedi)
sherpa-onnx cp39 wheel var; model/RTF/ayna doğrulanmadı; eşzamanlı konuşmayı ayıramaz. Spike geçerse yalnız altyazı kapalıyken `?` satırları için parça kapanışında centroid doğrulaması.

### Modül sınırları
| Dosya | Durum | İçerik |
|---|---|---|
| `ses.py` | Faz1 yeniden yazım | yakalama, VAD, epoch çapası, halka tampon, klip aralığı, yeniden kesme |
| `stt.py` | Faz1'de ayrılır | `SttIstemci`, `halusinasyon_puani`, daraltılmış `tekrar_temizle`, kesinti kuyruğu |
| `konusmaci.py` | yeni | çizelge, kimlik_turu, L/MAD/R, kesişim, devir, erteleme, ben/yankı |
| `yakalayici.py` | değişiklik | okuma olayı + epoch, heartbeat, :233-239/:287 hataları |
| `motor.py` | büyük | `_konusmaci_bul`, `_cakisanlar`, `_bekleyenleri_coz`, `_tek_konusmaci`, `_bekleyen_altyazilar`, `sozlugu_uygula`, `kararlari_uygula` silinir; satır modeli v2; `yukle` göç |
| `terim.py` / `duzelt.py` / `metin.py` | yeni / yeni / genişler | §5, §4 |
| `llm.py`, `not_araclari.py`, `tarih.py` | değişiklik | §6 |
| `sozluk.py`, `sozluk.example.json`, `tools/sozluk_temizle.py` | silinir | |
| `tools/olcum.py`, `tools/etiketle.py`, `tools/replay.py` | yeni | §1 |
| `app.py`, `isler.py`, `ayar.py` | değişiklik | Sözlük/İnceleme kalkar, `toplanti_bitti` sinyali, hata köprüsü, meşgul durumu |

---

## 4. Whisper çıktısını düzeltme

### Klip stratejisi
- Kısa klip = birim (1.5–12 sn, ses.py:178-189). `bit` = son sesli blok sonu. `KONUSMA_ORANI` konuşma bölgesinde; 0.3–0.5 sn `evet` geçer.
- Halka tampon (son 10 dk, float16 ≈ 19 MB) **Faz1'de** gelir: devir bölme ve Faz3 ona dayanır (ses.py:560 `ses=None` kalkar).
- Uzun pencere (Faz3, `config.uzun_pencere`): 20–28 sn, sessizlikte bölünmüş, 1.5 sn örtüşme, prompt = önceki pencerenin son cümlesi. Metin `difflib.SequenceMatcher` ile kısa klip aralıklarına dağıtılır; hizalama < 0.6 ise kısa metin kalır. Kısa ve uzun metin farklı kelime verirse kelime `⟨⟩` işaretlenir (listede olmayan yanlış duyma için tek ucuz sinyal). **Yarış kuralı:** parça kapanışı kapsadığı aralığın uzun pencere sonucunu en fazla 20 sn bekler; gelmezse kısa metinle özetlenir ve parça `yeniden_ozetle` kuyruğuna girer (motor.py:658-672 değişir). Canlı ekranda sonradan değişen kelime soluk vurgulanır. GPU ~2x; WER kazancı < %10 ise kapalı.

### Prompt (Faz2 A/B)
Katılımcı adları + ≤20 ilgili terim, ≤120 token, temperature 0. Düşük RMS klibe prompt yok. Tekrar artarsa kapalı. ARGE'ye "prompt modele gidiyor mu" sorulmadan A/B anlamsız.

### Halüsinasyon filtresi (logprob yok)
`halusinasyon_puani(metin, sure_sn, tokens, rms, cizelge_etkin: bool|None)` → 0–1:
- kr/sn > 30 ya da < 2 → **tek başına şüpheli** (+0.6); 25–30 ya da 2–3 → +0.4
- tokens/sn > 12 → +0.3; `zlib` oranı > 2.4 → +0.4; ≥3-gram tekrar > 0.5 → +0.3
- kara liste (sentetik Türkçe sessizlik kalıpları + bugünkü 9 kalıp) → +0.5
- RMS düşük + metin uzun → +0.3
- **çizelge kuralı**: altyazı sağlıklıyken (heartbeat var) klip aralığında hiç etkinlik yoksa (loopback: uzak kişi; mikrofon: kullanıcı) → +0.6. Kapsama yoksa kural uygulanmaz.
Puan ≥ 0.5 → `supheli`, soluk, nota girmez; ≥ 0.9 atılır. Eşikler **Faz0'dan önce** sentetik halüsinasyon fixture'ı ile sabitlenir, Faz0 verisiyle yeniden ayarlanır; hepsi `config.esikler` altında.

### LLM düzeltme (`duzelt.py`)
- Girdi: satır (id + raw), `terim.adaylar` (≤40), katılımcı adları (`kisi` türü). Şema `[{"satir": id, "eski": "...", "yeni": "...", "neden": "terim|ad|yazim"}]`; `yeni` ekli tam biçim (LLM yazar), model `(?)` yazmaz.
- **Diff kapısı (deterministik, VE kuralı)**: `eski` satırda birebir geçer; tek satıra uygulanır; sayı/tarih/para değişmez; kelime sayısı farkı ≤1; satırın ≤ %30'u.
  - `neden=terim|ad`: `yeni` kök = listedeki terim/ad **VE** `eski`↔terim normalize edit uzaklığı ≤ 0.4 **VE** `eski` yaygın Türkçe kelime değil (frekans listesi, ilk ~5k) **VE** ek doğrulaması: `yeni = terim (+ ' ) + izinli ek`, ek ünlü/ünsüz uyumuna uygun (`Jenkins'e`, `komitede` korunur; `Kubernetesin` reddedilir).
  - `neden=yazim`: uygulanmaz, kelime `belirsiz` listesine girer.
  - Reddedilen log'a; `komite→commit` (edit ~0.5, yaygın kelime) iki kuraldan düşer.
- Prompt DUZELT_SISTEM Whisper'a göre, sentetik örnekler (llm.py:426-433). Frekans listesi: repo içi veri dosyası (pip gerekmez); kaynak/lisans Faz2'de seçilir (§10).

### Maliyet (saat başına, 16k ctx)
130 kelime/dk ≈ 58k kr/saat, `len//3` ≈ 19.5k, ad önekleriyle ≈ 23k token → ~8 parça × 3000 (llm.py:273).
- Düzeltme: 8 × (0.8k sistem + 3k + 0.3k) ≈ 33k giriş / 5k çıkış (dusunme=False, llm.py:831, bütçe 1200 llm.py:267).
- Bölüm özeti: 8 × 4.3k ≈ 34k / 24k. Birleştirme + genel + sorumlu ≈ 12k / 5k.
- Toplam ≈ 80k giriş / 34k çıkış; 150 tok/s'de çıkış ≈ 4 dk. Gerçek Türkçe oran `llm.py:755` usage loglarından kalibre edilir (Faz0 işi), tablo buna göre yeniden yazılır.
- Embedding sıcak yolda yok; terim tekilleştirme toplantı sonu ≤1 çağrı.

---

## 5. Sözlük yerine terim mimarisi

### Veri (`terimler/terimler.json`, `uygulama_klasoru()`)
```json
{"surum": 1, "terimler": [{"terim": "Kampanya Modülü", "varyantlar": ["kampanya modulu"],
  "kaynak": ["davet", "ogrenme"], "seriler": {"seri-slug": 3}, "genel": false,
  "ilk": "2026-10-01", "son": "2026-10-10", "guven": 0.8}],
 "gecmis": [{"t": "...", "islem": "ekle|geri_al", "terim": "...", "neden": "..."}]}
```

### Kaynaklar
1. Outlook davet konu/gövde/ek adları: özel ad ve büyük harfli öbekler, `terim_gibi_mi`. **Kara liste**: Teams katılım metni, imza, yasal uyarı, URL, e-posta, telefon kalıpları (sentetik regex listesi).
2. Aynı serinin önceki `not.md` başlık/kalın terimleri.
3. Altyazı katılımcı adları (`kisi` türü).
4. **Öğrenme (bağımsız kanıt şartı)**: kapının kabul ettiği `terim|ad` düzeltmesi, aynı seride 2 ayrı toplantıda görülür **VE** hedef terim davette / önceki notta / katılımcı adında açıkça geçer **VE** `eski` yaygın Türkçe kelime değilse `varyant` olur. LLM'in kendi tekrarı tek başına kanıt değildir. `genel=true` yükseltmesi ≥5 ayrı seri. Her öğrenme `gecmis`'e yazılır, sayaç tutulur; `tools/terim_geri_al.py` ile geri alınır. Faz0 WER öğrenme açık/kapalı karşılaştırılır.
5. Elle: `terimler/elle.txt`.

### Kapsam ve besleme
`seri = slug(baslik)` korunur; aday = seri + `genel` + kök eşleşmesi (`ilgili_terimler`, motor.py:82-90). Beslenen yerler: Whisper prompt (Faz2), `duzelt.py` beyaz listesi, `baglam_metni` "Terimler" alanı (llm.py:813-822; imza korunur). Metne kör değişiklik: HİÇ.

### Silme ve geçiş listesi
| Yer | İşlem |
|---|---|
| sozluk.py, sozluk.example.json, tools/sozluk_temizle.py | sil; `normalize`, `terim_gibi_mi` → metin.py |
| motor.py:31,184,938-941; :604,643 `sozluk.uygula`; :723-735, :770-790, :798-818, :834-839; :283-289 `baglam()` | `terim.TerimHavuzu` / `terim.adaylar`; öğrenme `terim.gozlem_ekle` |
| llm.py:426-433, :826-848 | `duzelt.py` + yeni prompt |
| ses.py:40,371-381,417-420; isler.py:56 `ipucu_fn` (lambda) | `terim.prompt(seri, adlar)` |
| isler.py:30-32,45,212-236; :160-175 TamamlamaIsi | değiştir / sil |
| app.py:33,47,745,1226,1266,1301; :36-37,93,313-341,1026,1043-1081; :176-179,211-225,872-1010 | Sözlük sekmesi sil; `inceleme_hazir` → `toplanti_bitti` (motor bağlama + Başlat + `_not_uret` korunur), salt-okunur "Belirsizler" paneli |
| gorunum.py:113; toplanti.py:25,48-104,124,150,163; tools/whisper_deneme.py:27-37,73 | sil / `terim.prompt` |
| tests/test_akis.py:12,62,68-77,80-90,128-146; tests/test_tasma.py:331-343 | yeniden yaz |
| derle.bat:25-26; `/.github/workflows/briefmind.yml:78` (repo kökü, briefmind/ değil); .gitignore:3-5; README; docs/*PLANI* | güncelle |
| disk sozluk.json → `tools/terim_aktar.py` | yalnız `terimler[]`, yedekli |
Silme ajanı kuralı: `graphify affected` + `grep -rn "Sozluk\|sozluk" --include=*.py` çapraz kontrol (AST lambda içindeki çağrıyı kaçırabilir, isler.py:56).

---

## 6. Not üretimine etkisi
- `blok_metni` (motor.py:170): `spk_guven < 0.7` → `Ad?:`; `?` → `Bilinmeyen:`; `A → B` ve `A / B` olduğu gibi; `cakisma=true` satırın tümü `⟨⟩`. `supheli` nota girmez. MAP_SISTEM: "⟨⟩ içinden karar/aksiyon/sayı/tarih üretme, yalnız 'tartışıldı' bağlamı". `dayanak_kontrolu` bu satırları dayanak saymaz.
- `sorumlu_dogrula` (motor.py:991): sahip alanı `{"ad", "durum": kesin|belirsiz|yok}`. Kesin yollar: (a) kaynak satır `spk_guven ≥ 0.7`, `cakisma=false`, `ben` + gelecek/geniş zaman; (b) **adlandırılmış sahip**: kaynak satır konuşmacısı kesin ve metindeki ad çizelgedeki bir `kisi` katılımcıyla normalize birebir eşleşiyor ve `⟨⟩` içinde değil ("Ayşe, sen cumaya hazırla"). Diğer her şey `belirsiz`. `bana/benim/bende` kaldırılır (motor.py:982). Fixture: "X sen yap" / "ben yaparım" / "bana gönder" / `A → B` / `Ayşen` yanlış duyma.
- `(?)` kodla basılır; `acik_sorulari_temizle` (llm.py:1280) silmez. Dayanak satır bazlı (1–3 satır, kök örtüşmesi ≥0.5); dayanak `spk_guven < 0.7` → sahip belirsiz; `⟨⟩` payı > %30 → madde belirsiz.
- `kisiye_ozel_epostalar`, `jira_csv` yalnız `durum=kesin`; belirsizler "Sahibi belirsiz" bloğu. `tarih_coz` `birlestir` sonrası; kademeli liste birleştirme; parça örtüşmesi son 6 satır + açık kararlar (~400 token).
- Altyazı kapalı toplantıda not başına "Konuşmacı bilgisi yok" uyarısı ve `?` oranı.

---

## 7. Hatalar ve dayanıklılık (faz ataması)
**Faz0 (acil, düşük maliyet)**: `_tek_konusmaci` boş pencerede False (motor.py:534-537); `K5_UST` append öncesi, varsayılan 0 (motor.py:439-442); mikrofon→başkası yalnız kulaklık yokken (motor.py:478-481); `threading.excepthook` + sinyal köprüsü, QMessageBox yalnız ana thread (app.py:1658-1666); `YakalamaIsi.run` try/finally içinde `motor.bitir` + `inceleme_hazir` her yolda emit (isler.py:41-152); Başlat kilidi: `TamamlamaIsi` çalışırken Başlat kapalı (app.py:699-701, :884); `ParcaIsi.run` koruma (isler.py:189); `tools/servis_kesfet.py` ile LLM 404 ve ARGE `openapi.json` keşfi.
**Faz1**: ses.py:516-520 `durdur()` son parça; :563-571 HOL 60 sn; :425-446 yeniden deneme/zaman aşımı; :69-88 ton kalkar; yakalayici.py:233-239, :287, :150-171; isler.py:84-108 `basladi`; motor.py:637-640, :651-656, :162-167 `_sn` gece yarısı.
**Faz2**: ses.py:127-132 FIR (`scipy.signal.resample_poly` aynadaysa, yoksa numpy FIR); :252-268, :585-595 tekrar kuralları; :157, :286, :379, :444; motor.py:75 Türkçe İ; `inceleme_hazir` → `toplanti_bitti` geçişi (sonnet, offscreen test).
**Faz4**: tarih.py:28,49-52; motor.py:956-962, :976-987; llm.py:122, :198, :224, :396, :578, :710, :1252; motor.py:1270; not_araclari.py:83.
**Faz5**: tek meşgul durum makinesi (Faz0 kilidini genişletir); Outlook/UIA/STT testi thread'e (CoInitialize); closeEvent bitti sinyalini bekler, açık parça 30 sn'de diske; ayar.py utf-8-sig + atomik + şema; yerleşim; `satir_guncelle` tek satır; takvim; requirements sabitleme + exe duman; `hata.log` %LOCALAPPDATA%; gerçek adlar sentetikle (outlook.py:169, motor.py:528,986, tools/whisper_deneme.py:34, tests/test_akis.py:64, tests/test_asama2.py:62,269,283); tools/teshis.py maskeleme.

---

## 8. Test stratejisi
- **Birim** (3.9 + 3.11): `konusmaci` (kesişim, devir, L/MAD, R, heartbeat, erteleme, ben/yankı/atma, kimlik_turu, hayalet ad), `stt.halusinasyon_puani` (sentetik fixture: sessizlik, gürültüye uydurulmuş akıcı kısa cümle, kr/sn uçları), `duzelt.diff_kapisi` (VE kuralı, ek uyumu vakaları, yaygın kelime reddi, `komite→commit` reddi), `terim` (bağımsız kanıt, geri alma, kara liste), `sorumlu_dogrula` fixture'ları, `not_markdown`, `not_araclari`, `Motor.yukle` eski kayıt göçü, `ayar`.
- **Düzenek** (`tests/e2e/`, sentetik): hızlı devir, araya girme, aynı anda, klip içi sıralı devir, ben+uzak, altyazı 2 dk kapalı, gecikme 0/2/3.5, tekrarlanan cümle, halüsinasyon klibi, kulaklıksız yankı, okuyucu takılması, STT kesintisi. Kapılar: `yanlis=0`, `cift=0`, `bozulan=0`, yanlış-aynı-anda 0, altyazı açık belirsiz ≤ %12, kapalıyken uzak `?` %100. Sim varsayımları Faz0 verisiyle güncellenir.
- **Replay**: her faz sonunda `tools/replay.py` Faz0 kaydında koşar; `olcum.py` raporu önceki faza göre kötüleşmez.
- **CI** (`/.github/workflows/briefmind.yml`, repo kökü): compileall, pyflakes, pytest, düzenek (<2 dk), offscreen kabuk duman testi (Faz0'dan itibaren: worker istisnası + `motor.bitir`), `grep` ad/URL sızıntı. CI public PyPI'den kurar; **ayna kontrolü** ayrı: kullanıcı PC'sinde `pip download -r requirements.txt --python-version 3.9 --platform win_amd64 --only-binary=:all:` listesi, her faz öncesi.
- **Kalibrasyon**: tüm eşikler (`0.60/0.25`, `0.7`, halüsinasyon `0.5/0.9`, L sınırları, bekleme 20 sn) `config.esikler`'de; Faz0 sonrası mimar replay raporuyla ayarlar, ayarlanan değerler düzenek fixture'larına sabit olarak yazılır (regresyon).

---

## 9. Fazlı uygulama planı
Her ajan graphify ile konumlandırır, yalnız gereken satır aralığını okur. Doğrulayıcı bağımsız ajan. Bütçeler ajan başına üst sınır (giriş/çıkış).

| Faz | Kapsam | Kabul testi | Boyut | Ajan |
|---|---|---|---|---|
| **0 Ölçüm + acil** | `tools/olcum.py`, `etiketle.py`, `replay.py`; `config.olcum_kaydi` → klip wav + `klipler.jsonl` (epoch + örnek sayacı) + `okumalar.jsonl` (ham UIA, bas/bit epoch); düzenek → `tests/e2e/`; §7 Faz0 düzeltmeleri (hata köprüsü, try/finally `motor.bitir`, Başlat kilidi dahil); uç nokta keşfi; token oranı kalibrasyonu | Düzenek altyazı-kapalı senaryoda yanlış 9→≤5, 14→≤10, `?` artar (yanlış 0 Faz1 kapısıdır); tekrar senaryosu değişmez (bilinçli); K5 kapalıyken altyazı metni 0; offscreen: worker istisnası çökertmez, `motor.bitir` çağrılır, çift Başlat engellenir | ~900 satır | olcum/etiketle/replay: **sonnet** ≤50k/≤15k. Acil düzeltmeler + kilit: **sonnet** ≤40k/≤8k (haiku değil: thread/sinyal yolu). Düzenek taşıma: **haiku** ≤30k/≤8k. Doğrulayıcı: sonnet ≤30k/≤3k |
| **1 Zaman + konuşmacı** | Epoch çapası + örnek sayacı; okuma olayı + R + heartbeat; halka tampon; `konusmaci.py` (çizelge birleşimi, kimlik_turu, L/MAD, karar, devir bölme + yeniden STT, erteleme, ben/yankı/atma, pasif korelasyon); `stt.py` ayrımı; motor'dan metin benzerliği/K5/A6/`_tek_konusmaci` silinir; STT kesinti politikası; gösterim kuralı; `Motor.yukle` göçü; oracle yeniden koşumu; Faz1 hataları | Düzenek 12 senaryo: yanlış 0, çift 0, yanlış-aynı-anda 0, belirsiz ≤ %12; L ±0.6 sn; replay Faz0 kaydında SER-kesin ≤ %1; 144+ test yeşil | ~1500 satır | `konusmaci.py` + motor: **opus** ≤130k/≤28k. ses.py/stt.py/yakalayici.py: **sonnet** ≤80k/≤18k. Testler + replay entegrasyonu: **sonnet** ≤50k/≤12k. Doğrulayıcı: opus ≤60k/≤5k (algoritma), sonnet ≤40k/≤3k |
| **2 Terim + düzeltme + filtre** | §5 silme listesi, terim.py (bağımsız kanıt öğrenme, kara liste, geri alma), duzelt.py (VE kapısı, ek uyumu, frekans listesi), DUZELT_SISTEM, `halusinasyon_puani` (çizelge kuralı), tekrar kuralları, FIR, prompt A/B kancası, `inceleme_hazir`→`toplanti_bitti`, Sözlük/İnceleme UI kaldırma, `terim_aktar.py`, README/derle/workflow | `bozulan=0`; mikro test 18 kelimede 0 ek kaybı + ek uyumu vakaları; `komite→commit` reddedilir; halüsinasyon fixture 0 kesin geçiş; replay WER kötüleşmedi; öğrenme açık/kapalı WER farkı ≥0; offscreen: toplantı sonu not üretimi çalışır; `grep Sozluk` boş | ~1600 satır | Silme + dokümantasyon: **haiku** ≤50k/≤8k (mekanik kısım). `inceleme_hazir` geçişi + UI: **sonnet** ≤50k/≤10k. terim.py: **sonnet** ≤60k/≤15k. duzelt.py kapısı + prompt: **opus** ≤70k/≤15k. Filtre/VAD/FIR: **sonnet** ≤50k/≤10k. Doğrulayıcı: opus ≤40k/≤4k (kapı), sonnet ≤50k/≤4k |
| **3 Uzun pencere** (bayraklı) | Arka plan pencere çözümü, difflib hizalama, LCS örtüşme, önceki cümle prompt, kısa/uzun farkı `⟨⟩`, yarış kuralı + `yeniden_ozetle` | Replay WER göreli −25% (hedef) / −10% (açık kalma şartı); parça kapanış gecikmesi ≤ 20 sn; GPU yükü ölçülür | ~700 satır | Hizalama: **opus** ≤90k/≤18k. İşçi/kuyruk: **sonnet** ≤50k/≤10k. Doğrulayıcı: opus ≤50k/≤4k |
| **4 Not hattı** | §6 tamamı + Faz4 hataları | Fixture'lar: düşük güven → belirsiz 0 kesin; adlandırılmış sahip doğru; `A / B` sahip 0; ⟨⟩ satırdan karar 0; 170+ madde birleşti; e-posta/Jira yalnız kesin | ~900 satır | sorumlu/dayanak: **sonnet** ≤80k/≤18k. Birleştirme + prompt: **sonnet** ≤60k/≤12k. Küçük hatalar: **haiku** ≤30k/≤5k. Doğrulayıcı: opus ≤60k/≤5k |
| **5 Kabuk + paketleme** | §7 Faz5 | Offscreen: BOM config, kapanış veri kaybetmez, durum makinesi; exe açılır; ad sızıntısı 0 | ~900 satır | Durum makinesi (Faz2 ajanının devamı): **sonnet** ≤80k/≤18k. COM/UIA thread: **sonnet** ≤50k/≤10k. Paketleme/temizlik: **haiku** ≤50k/≤10k. Doğrulayıcı: sonnet ≤50k/≤4k |
| **6 Ses gömmesi spike** | ayna testi, RTF/DER, `?` centroid prototipi | RTF < 0.3, `?`'lerin ≥ %50'si doğru, yanlış 0 | ~300 | **sonnet** ≤40k/≤10k; karar kullanıcı + mimar |
| **7 Canlı not, bilgi tabanı** | rag.py arama, canlı özet | ayrı plan | — | sonraki döngü |

Sıra: 0 → 1 → 2 → 4 → 5 → 3 → 6. Toplam ≈ 1.65M giriş / 0.32M çıkış. Opus yalnız konuşmacı çekirdeği, kapı, hizalama ve doğrulamalar. Ortak kurallar: dosya tamamı okunmaz; Python 3.9 sözdizimi; sentetik adlar; repo dosyasına host/URL/token yok; her değişiklik test; `pytest -q tests` 3.9 venv.

---

## 10. Riskler ve açık sorular
**Kullanıcıdan:** (1) toplantı sesini diske kaydetmek izinli mi (Faz0 WER buna bağlı; değilse §1 yedek ölçüt); (2) kulaklık/hoparlör kullanım oranı; (3) banka aynası `pip download` kontrol listesi (sherpa-onnx, scipy, sabitlenmiş sürümler); (4) Türkçe kelime frekans listesi kaynağı/lisansı (repo içi veri; aday: açık lisanslı derlem sayımı, Faz2'de seçilir); (5) git geçmişindeki gerçek adlar için geçmiş yeniden yazımı.
**Kullanıcı kararları (2026-10-11):** (1) PoC olduğu için Faz0 ses kaydı (`olcum_kaydi`) yapılabilir. (2) Çoğunlukla kulaklık, bazen hoparlör: elle ayar yok; kulaklık/hoparlör durumu pasif korelasyonla (mikrofon RMS ↔ loopback RMS) sürekli ve otomatik tespit edilir, ton çalınmaz. (3) ARGE'ye ve platform ekibine soru sorulmaz; servis yetenekleri deneme araçlarıyla ölçülür: `tools/stt_kesfet.py` (form alanları, segment/kelime zamanı, olasılık alanları, prompt etkisi) ve `tools/servis_kesfet.py --llm` (LLM sohbet yolu). Aşağıdaki ARGE/platform maddeleri bu araçların çıktısıyla cevaplanır; desteklenmeyen yetenek "yok" kabul edilir.
**ARGE'den:** segment `start/end`; `no_speech_prob/avg_logprob/compression_ratio`; `timestamp_granularities=word`; `prompt` gerçekten gidiyor mu; **kapasite**: uzun pencere ~2x yük ve kesinti sonrası biriken kuyruk — karar eşiği: p95 klip gecikmesi > 10 sn ise uzun pencere kapalı kalır.
**Platformdan:** `qwen38-27b` 404 (Faz0 keşfi; çözülene kadar eski uç nokta, llm.py:122); reranker yok.
**Teknik:** L/MAD yanlışsa sistematik kayma (tavan 0.6 + replay); Teams satır bölme davranışı sim'den farklıysa eşikler config'den; hizalama kayması (<0.6 kısa metin); kapı sıkıysa düzeltme oranı düşer (kabul edilen bedel); devir bölmesi iki kısa klip üretir, Whisper kısa klipte kötüleşebilir (Faz1 düzenekte ölçülür, 1.0 sn altı parça bölünmez, `A → B` kalır); `ikisi` modunun kaldırılması bilinçli.

---

## 11. Eleştirilere yanıt
**Gerçeklik lensi**
1. Oracle plan kuralını doğrulamıyor, klip bazlı A/B %21.5 — **kabul**: devir bölme Faz1'e, `A → B` etiketi, hedef klip bazlı ≤ %12, oracle yeniden koşulur.
2. Faz0 "yanlış 0" tutmuyor — **kabul**: kabul "9→≤5, 14→≤10, `?` artar"; yanlış 0 Faz1 kapısı. Metin benzerliğini Faz0'da kapatma — **ret**: Faz1'de zaten siliniyor, Faz0'da ikinci yarım yol açmaz.
3. Faz0 verisi Faz1'i kalibre edemez, replay yok — **kabul**: `okumalar.jsonl` ham UIA + klip wav + örnek sayacı, `tools/replay.py`; "halka tampon kayıtları" cümlesi düzeltildi.
4. Zaman ms hassas değil, R sabit değil — **kabul**: epoch çapası + `toplam_sn`, periyodik yeniden çapalama, okuma bas/bit epoch, R ölçülür.
5. STT kesintisi tanımsız — **kabul**: diske biriktirme (izinliyse), açık "transkript eksik" durumu, işaretli altyazı yedeği yalnız opt-in.
6. Mikrofon 0.95 kırılgan bayrağa dayanıyor — **kabul**: kulaklıktan bağımsız kural, pasif korelasyon Faz1, ton kaldırılır, tavan 0.7.
7. Çökme düzeltmesi Faz5'te — **kabul**: excepthook + try/finally `motor.bitir` + Başlat kilidi Faz0'a.
8. İnceleme silme mekanik değil — **kabul**: sonnet, `toplanti_bitti` sinyali korunur, offscreen test; Faz5 durum makinesi aynı ajanın devamı.
9. Kararlı satır kimliği yok, olay şişmesi — **kabul**: okuma olayı + kişi başı aralık birleşimi, `son` yalnız kelime artışında, diske aralık, tavan.
10. Hayalet konuşmacı — **kabul**: kimlik_turu + davetli/katılımcı eşleşmesi ya da ≥3 satır.
11. Ek taşıma Türkçede yanlış — **kabul**: LLM ekli biçimi yazar, kapı terim+ek+uyum doğrular; `yazim` uygulanmaz.
12. Uzun pencere parça kapanışıyla yarışıyor — **kabul**: 20 sn bekleme + `yeniden_ozetle`.
13. Token tahmini düşük — **kabul**: ~23k/saat, 8 parça, usage loglarıyla kalibrasyon.
14. `?`/`A / B` katılımcı listesine giriyor — **kabul**.
15. CI yolu ve ayna — **kabul**: workflow yolu repo kökü, `pip download` ayna listesi.
Eksikler: geriye uyum (`surum`, `yukle` göçü) **eklendi**; klip bazlı etiketleme aracı **eklendi**; ses kaydı izinsizse yedek ölçüt **eklendi**; Teams satır bölme sorusu Faz0 listesinde; LLM 404 keşfi Faz0'da; ARGE kapasite karar eşiği **eklendi**; odadaki başka kişi (mikrofon, uzak yok → `ben` 0.6, sahip yok) **eklendi**; graphify + grep çapraz kontrol **eklendi**.

**Kapsam lensi**
1. Oracle R=0.6/cümle bazlı, bant asimetrik — **kabul**: oracle yeniden koşumu, `±R/2` simetrik bant.
2. Klip içi sıralı devir Faz3'te ve WER bayrağına bağlı — **kabul**: Faz1'e, bayraktan ayrı, `A → B` ≠ `A / B`.
3. Halüsinasyon puanı tek özellikle eşiği geçmiyor, çizelge sinyali yok — **kabul**: çizelge kuralı +0.6, kr/sn uç tek başına yeterli, tokens/sn eklendi, fixture Faz0 öncesi.
4. Diff kapısı VEYA — **kabul**: VE kuralı + yaygın kelime reddi; `yazim` uygulanmaz (0.25 koluna gerek kalmadı).
5. Öğrenme kendini doğruluyor — **kabul**: bağımsız kaynak şartı, `genel` ≥5 seri, geri alma kaydı, açık/kapalı WER.
6. Ek koruma — **kabul** (bkz. gerçeklik 11).
7. Karar kuralı boşluğu, gösterim tek kural değil — **kabul**: 0.25–0.60 → `Ad?`; `spk_guven < 0.7` her yüzeyde `Ad?`.
8. `son` revizyonla uzuyor — **kabul**: kelime sayısı artışı kuralı, ham büyüme kaydı.
9. Yankı çift satır / 0.95 — **kabul**: yankı klibi atılır, kullanıcı altyazısıyla doğrulanınca `ben`.
10. Adlandırılmış sahip yolu — **kabul**: §6 (b) yolu + fixture'lar.
11. `A / B` metni karar kaynağı olmamalı — **kabul**: tümü `⟨⟩`, MAP kuralı, dayanak dışı.
12. Oda/genel etiket — **kabul**: kimlik_turu.
13. Faz0'a çökme, klip kaydı, olay günlüğü, etiketleme yardımcısı — **kabul**.
14. İnceleme silme sonnet'e, stt.py ayrımı Faz1'de — **kabul**.
15. L çapası monologda bozuluyor — **kabul**: büyüme sürdürme çapası, >5 sn atılır, MAD.
Eksikler: heartbeat/kapsama **eklendi**; erteleme sırasında `beklemede…` **eklendi**; kısa/uzun farkı `⟨⟩` **eklendi**; davet kara listesi **eklendi**; frekans listesi kaynağı açık soru olarak **eklendi**; kişi başı L ve `YAVAS_ARALIK` Faz0 listesinde; altyazı kapalı uyarısı ve dürüst hedef beyanı §1/§6'da; eşik kalibrasyon süreci §8'de.
