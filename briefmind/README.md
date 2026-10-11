# BriefMind

Microsoft Teams toplantılarını **yerel makinede** dinleyip (ses + canlı altyazı), kurum içi
Whisper STT ve LLM servisleriyle yapılandırılmış toplantı notu üreten Windows masaüstü uygulaması.
Varsayılan ayarlarda ses hiçbir yere kaydedilmez; yalnızca metin diske yazılır (isteğe bağlı kayıtlar için bkz. Gizlilik). Tüm işleme kurum içi servislerde yapılır.

## Mimari

```
Ses (hoparlör + mikrofon) ─ kısa klip VAD (epoch) ─► Whisper STT (OpenAI uyumlu)   [ne söylendi]
Teams canlı altyazısı (UI Automation) ─ okuma (epoch) ─► konuşmacı aralıkları    [kim, ne zaman]
        └─ konusmaci.py: klip [bas, bit] ile aralıkların ZAMAN kesişimi (metin eşlemesi yok); altyazı metni
           transkripte girmez. Karar gelene kadar satır "beklemede…", sonra "Ad", belirsizse "Ad?", "Bilinmeyen",
           "A → B" (sıralı devir; mümkünse klip ikiye kesilir), "A / B (aynı anda)"
Motor: parçalar (≤3000 token / 12 dk) ─► terim düzeltmesi (LLM önerir, deterministik kapı karar verir)
     ─► bölüm özeti (JSON) ─► birleştirme ─► dayanak kontrolü (transkriptte olmayan madde düşer) ─► Markdown not
     ─► Outlook taslağı (takvimdeki katılımcılara)
```

## Dosyalar

| Dosya | Görev |
|---|---|
| `app.py` | PyQt5 masaüstü uygulaması (Canlı / Not / Geçmiş / Ayarlar) |
| `isler.py` | Arayüzü dondurmayan arka plan işleri (yakalama, not üretimi, yeniden özetleme, soru) |
| `gorunum.py` | Tema (QSS), notun HTML görünümü, ikonlar |
| `ayar.py` | `config.json` okuma/yazma |
| `motor.py` | Ses/okuma olaylarından satır v2 (`surum: 2`), konuşmacı kararlarının uygulanması (yankı atma, yeniden kesme), parçalama, arka plan özetleme, dayanak kontrolü, not üretimi; eski kayıt göçü |
| `konusmaci.py` | Yalnız zamana dayalı konuşmacı ataması: altyazı çizelgesi, gecikme (L/MAD), okuma periyodu (R), devir/çakışma, ben/yankı |
| `llm.py` | LLM istemcisi, bölüm/birleştirme promptları, token bütçeleri, notun deterministik temizliği |
| `rag.py` | Toplantıya soru için hibrit arama: transkript pencereleri, BM25 (Türkçe kök), embedding, RRF; vektör önbelleği toplantı klasöründe (`rag_dizin.npz`) |
| `metin.py` | Metin benzerliği (kelime kökü), Türkçe küçük harf, ad eşleşmesi, konuşmacı gösterim kuralı (`gorunen_ad`) |
| `ses.py` | Ses yakalama (WASAPI loopback + mikrofon), VAD, STT istemcisi, tekrar/halüsinasyon filtreleri |
| `cihaz.py` | Ses cihazlarının otomatik seçimi: mikrofonlar arasında ses veren/en net olanı seçer, tüm çıkışların loopback'ini karıştırır; tak-çıkar |
| `yakalayici.py` | Teams altyazısını UI Automation ile okuma, toplantı adı, altyazıyı otomatik açma |
| `outlook.py` | Takvimden toplantı eşleştirme, taslak e-posta, oturum kullanıcısı |
| `terim.py` | Terim havuzu (`terimler/terimler.json`): davet, önceki notlar ve katılımcılardan aday; bağımsız kanıtla öğrenme, geri alma |
| `duzelt.py` | Parça düzeltmesi: LLM yalnızca öneri verir, deterministik kapı (VE kuralı, ek uyumu, sayı/tarih sabit) karar verir; metin bayt bayt korunur |
| `kelime.py` | Türkçe kelime yardımcıları (normalize, yaygın kelime listesi `veri/yaygin_kelimeler.txt`) |
| `stt.py` | Whisper istemcisi, halüsinasyon puanı (`halusinasyon_puani`), tekrar temizleme |
| `saat.py` | Adıma dayanıklı zaman kaynağı: duvar saati sıçrasa da ses ve altyazı zaman çizelgeleri kaymaz |
| `toplanti.py` | Komut satırı arayüzü (başla / bitir / liste) |
| `tools/` | Teşhis ve yardımcı scriptler (STT testi, Teams düğme keşfi, terim aktarma ve geri alma, LLM teşhisi, taşmış transkript temizliği) |
| `deploy/` | GPU'suz Whisper servisi için OpenShift manifestleri (CPU, faster-whisper) |

## Nasıl çalışır (v3)

- **Konuşmacı zamandan bulunur.** Metin Whisper'dan gelir; konuşmacı, klibin zaman aralığının Teams altyazı çizelgesiyle kesişmesinden bulunur (metin eşlemesi yok). Karar gelene kadar satır "beklemede…" görünür.
- **Gösterim.** `Ad` (güven ≥ 0,7); `Ad?` (ad var ama güven düşük); `Bilinmeyen` (kimse yok); `A → B` (klipte sıralı devir); `A / B (aynı anda)` (çakışma: bu satırdan karar ya da aksiyon çıkmaz, metin ⟨⟩ içinde).
- **Terimler otomatik.** Terim düzeltmesi ve öğrenme onay istemez; LLM yalnızca öneri verir, kelime sınırı, ek uyumu ve sayı/tarih kapıları karar verir.
- **Belirsizler paneli.** Kararsız kelimeler, emin olunmayan konuşmacılar ve şüpheli klipler Not sekmesinde salt okunur listelenir; `ts:` bağlantısı transkripti o ana açar. Şüpheli klip nota girmez.
- **Sahiplik temkinli.** Sorumlu yalnız kesin kaynaktan atanır (güvenli konuşmacının kendi taahhüdü ya da adıyla görev verilen katılımcı); belirsiz sahipler "Sahibi belirsiz" bloğunda kalır, e-posta ve Jira taslağına girmez.
- **Ölçüm araçları.** `tools/olcum.py` (WER, SER, belirsiz oranı), `tools/etiketle.py` (klip etiketleme), `tools/replay.py` (kaydı çevrimdışı yeniden işletme). Kayıt yalnızca `olcum_kaydi` açıkken yazılır.

## Kurulum

```powershell
pip install -r requirements.txt
copy config.example.json config.json      # adresleri doldur
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
| `llm_dusunme_butcesi` | (isteğe bağlı, varsayılan `1500`) Düşünmeli LLM çağrılarında vLLM `thinking_token_budget` alanı olarak gönderilir; düşünme `max_tokens`'in tamamını yiyip cevabı boş bırakamaz (cevaba en az %40 kalır). `0`: alan gönderilmez. Sunucu alanı 400 ile reddederse oturum boyunca gönderilmez ve Olaylar'a bir kez yazılır |
| `parca_token`, `paralel` | (isteğe bağlı) Canlı parça boyutu (token) ve aynı anda işlenen parça sayısı; boşsa pencereye göre (16k: 3000/2, 32k+: 4000/2) |
| `stt_url`, `stt_model`, `stt_key` | OpenAI uyumlu STT adresi (`/v1/audio/transcriptions`), model, Bearer anahtarı |
| `kaynak` | Yalnız `ses`: metin Whisper'dan, konuşmacı Teams altyazısının **zamanından**. `ikisi` ve `altyazi` kaldırıldı (eski değer `ses` sayılır). STT erişilemezse altyazı moduna düşülmez: Canlı sekmesinde "STT yok — transkript eksik (hh:mm–hh:mm)" şeridi, notta aynı satır |
| `altyazi_yedek` | (varsayılan `false`) `true`: STT yokken Teams altyazı metni yedek olarak girer (`kaynak: altyazi-yedek`, şüpheli; notta "altyazıdan, düşük güven"; sahip/aksiyon üretmez). Ayarlar → "STT yokken Teams altyazı metnini yedek olarak nota al" |
| `altyazi_goster` | (varsayılan `false`) Canlı ekranda Teams altyazısı da `[altyazı]` etiketiyle görünür; transkripte girmez |
| `kesinti_kaydi` | (varsayılan `false`) STT yokken klipler toplantı klasörüne (`olcum/kesinti/`) yazılır, STT dönünce sırayla çözülür (`olcum_kaydi` açıkken zaten açık) |
| `kesinti_devam` | (varsayılan `true`) Önceki oturumdan diskte kalan STT kesinti kuyruğu (`olcum/kesinti/`) açılışta (STT ayaktaysa) otomatik çözülür; Olaylar'a ilerleme yazılır, not yeniden üretilmez (Geçmiş → Yeniden özetle). `false`: kuyruk diskte beklemeye devam eder |
| `esikler` | (isteğe bağlı) Konuşmacı atama eşikleri, ör. `{"baskin": 0.6, "ikinci": 0.25, "kesin": 0.7, "erteleme_sn": 15}` (tam liste: `konusmaci.VARSAYILAN_ESIKLER`). Halüsinasyon eşikleri de burada: `hal_supheli` (varsayılan 0,5; satır nota girmez, Belirsizler'e düşer), `hal_at` (0,9; satır silinir) |
| `ben` | Uygulamayı açan kişi (boşsa Outlook/Windows'tan alınır) |
| `mikrofon_cihaz` | (isteğe bağlı, **gerekmez**) Elle mikrofon: cihaz adı (ya da eski numara). Boş: otomatik seçim (bkz. [Ses cihazları](#ses-cihazları-otomatik)). Ayarlanan cihaz yoksa, çıkarılırsa ya da hiç ses vermiyorsa otomatiğe dönülür, ses verince ona geri geçilir (sessize alınınca bırakılmaz) |
| `cihaz_otomatik` | (varsayılan `true`) `false`: eski yol — yalnız Windows varsayılan hoparlörü ve `mikrofon_cihaz`/varsayılan mikrofon (tak-çıkar izlenmez) |
| `stt_ipucu` | (varsayılan `true`) Whisper'a prompt olarak katılımcı adları ve serinin en çok 20 terimi gider; düşük sesli kliplere gitmez. Ayarlar'dan açılıp kapatılır |
| `stt_repetition_penalty` | (varsayılan boş) Boşken STT'ye gönderilmez; sayı (ör. `1.1`) yazılırsa `repetition_penalty` olarak gider. Deneme amaçlı |
| `stt_proxy` | (isteğe bağlı, varsayılan `true`) `false` yapılırsa STT'ye Windows proxy'si atlanarak doğrudan bağlanılır. STT'de sık `10053` bağlantı kopması görülürse denenir. |
| `not_sablonu` | `genel` / `haftalik` / `karar` / `birebir` / `calistay` (Ayarlar → Not şablonu) |
| `saklama_gun` | Notu üretilmiş toplantıların transkripti bu kadar gün sonra silinir; `0` kapalı (Ayarlar → Transkript saklama süresi) |
| `embed_url` | (isteğe bağlı) Embedding servisi (OpenAI uyumlu `POST /v1/embeddings`, gövde `{"input": [...]}`). Doluysa "Toplantıya sor" hibrit arama yapar: kelime (BM25) + anlam (vektör), RRF ile birleştirilir. Boşsa ya da servis hata verirse yalnızca kelime araması. Servisin yollarını bulmak için: `python tools\servis_kesfet.py <adres>` |
| `embed_boyut` / `embed_key` / `embed_proxy` | Vektör boyutu (varsayılan `1024`; 4096'lık Qwen3-Embedding vektörü kırpılıp normalize edilir), anahtar (gerekirse), Windows proxy'si |
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

**Duman testi:** `python app.py --duman` pencereyi ekransız kurar (ağ, Outlook, UIA ve zamanlayıcı yok), paketlenen
modülleri ve `veri\` dosyalarını açmayı dener; çıkış kodu `0` = tamam (derlenmiş exe için de geçerli: `BriefMind.exe --duman`). Sonuç satırları konsol yoksa
`hata.log`'un yanındaki `duman.txt`'ye de yazılır.

**hata.log konumu:** `%LOCALAPPDATA%\BriefMind\hata.log` (yazılamazsa uygulama klasörü; 1 MB'ı aşınca `hata.log.1`'e
döner). `ToplantiNotu.bat` ve `python tools\teshis.py` bu yolu kullanır. Paylaşmadan önce içeriğine bakın.

Exe üretmek için `derle.bat` (PyInstaller, `dist\BriefMind\`); `veri\` klasörü (yaygın kelime listesi) exe'ye eklenir.

## Ses cihazları (otomatik)

Kimsenin cihaz ayarlaması gerekmez; kulaklıkla, dizüstü hoparlör/mikrofonuyla ya da toplantı ortasında kulaklık takıp
çıkararak çalışılabilir.

- **Mikrofon:** açık tüm mikrofonlar (en fazla 4) dinlenir. Hiç ses vermeyen (−90 dB altı, ör. Windows izni kapalıyken
  gelen −98 dB) cihaz 2 sn'de elenir. Windows varsayılanı ses veriyorsa o, vermiyorsa (ya da −80 dB'den kısıksa) ses
  veren ilk mikrofon seçilir. Kullanıcı konuşurken başka bir mikrofon ≥ 6 dB daha net duyuyorsa (≥ 3 sn konuşma, iki
  geçiş arası ≥ 10 sn, klip açabilecek seviyede) ona geçilir; karşı taraf konuşurken ölçüm yapılmaz (hoparlörden
  mikrofona giren eko seçimi kazanamaz), klavye tıkırtıları sayılmaz. Geçiş klip sınırında yapılır; seçili cihaz
  çıkarılır ya da blok vermezse hemen geçilir (açık klip önce kapanır). Çalışırken tam sıfıra düşen seçili mikrofon
  (kulaklıkta sessize alma, gürültü kapısı) bırakılmaz: sessizdeyken odada konuşulan transkripte girmez; Canlı sekmesinde
  "— sessizde" yazar. Bluetooth kulaklığın eller-serbest mikrofonu yalnız gerektiğinde açılır (varsayılansa, diğer
  mikrofonlar tam sessizse ya da kulaklık zaten eller-serbest modundaysa). Canlı sekmesinde "Mikrofon: <ad> (otomatik)"
  yazar, Olaylar'a her seçim düşer.
- **Hoparlör (karşı taraf):** tüm çıkış cihazlarının (en fazla 4) loopback'i karıştırılır: Teams sesi hangi cihazdan
  çıkarsa çıksın yakalanır. Cihaz listesi 3 sn'de bir yenilenir; 4'ten fazla cihaz varsa Windows varsayılanı ve yeni
  takılan cihaz, hiç ses vermeyenin yerini alır.
- **Uyarılar:** tüm mikrofonlar 10 sn tam sessizse (hiçbiri daha önce ses vermediyse) bir kez şerit + Olaylar uyarısı çıkar (Windows Ayarlar → Gizlilik ve
  güvenlik → Mikrofon → "Masaüstü uygulamalarının mikrofonunuza erişmesine izin ver"); ses gelince kalkar. Altyazıda
  karşı taraf konuşurken 20 sn hoparlör sesi yoksa da uyarı çıkar.
- **Teşhis:** `python tools\ses_teshis.py --otomatik` (8 sn konuş, bir ses çal): cihaz başına seviye, tam sessizlik ve
  uygulamanın seçeceği mikrofon.

## Kullanım

1. Teams toplantısına gir (altyazı açık olsun; kapalıysa uygulama Alt+Shift+C ile açmayı dener).
2. **Başlat** — üstteki listeden toplantı seçilir (takvimden, Teams penceresine göre otomatik).
3. Konuşmalar klip klip Canlı sekmesine düşer; konuşmacı önce "beklemede…" görünür, altyazı çizelgesi o anı
   kapsayınca (en geç 15 sn) yerleşir. Güven 0,7'nin altındaysa ad "Ad?" yazılır, kimse yoksa "Bilinmeyen".
   Klip içinde konuşmacı değiştiyse klip en sessiz noktadan ikiye kesilip yeniden çözülür; kesilemezse "A → B".
   Kulaklıksız (hoparlör) kullanımda mikrofona giren karşı taraf sesi atılır. Altyazı 60 sn'den uzun okunamazsa
   "Konuşmacı bilgisi yok" şeridi çıkar. Parçalar arka planda özetlenir; kapanış, satırlarının konuşmacı kararını
   en fazla 20 sn bekler. Kısık gelen ses (tepe < 0,3) Whisper'a gitmeden yükseltilir.
4. **Bitir** → not doğrudan üretilir; onay istenmez. Not sekmesindeki şeritte aşama ve geçen süre görünür
   ("Parça 3/12 özetleniyor", "Özet paragrafı yazılıyor"…) → Outlook taslağı. Kararsız kalınan kelimeler,
   emin olunmayan konuşmacılar, şüpheli (halüsinasyon puanı yüksek) klipler ve özetleyicinin bozuk bıraktığı terimler
   Not sekmesindeki **Belirsizler** panelinde salt okunur listelenir; `ts:HH:MM:SS` bağlantısı transkripti o ana açar.
   Şüpheli klip nota girmez. Düzeltmeyi yeniden denemek için **Notu yeniden üret** düğmesi vardır.

Ayarlar → **Kaydet** hemen geçerlidir; LLM adresi/modeli için uygulamayı yeniden başlatmak gerekmez. Açılışta ve
Kaydet'ten sonra LLM sunucusu arka planda sorulur (model adı, bağlam penceresi); uyarılar Olaylar'a düşer.
Ayarlar → **LLM sunucusunu test et** kısa bir sohbet denemesi yapar (süre, token/sn, cevap Türkçe mi). Ayrıntılı
ölçüm: `python tools\llm_teshis.py --tam` ([Teşhis rehberi](docs/TESHIS_REHBERI.md#yeni-llm-sunucusu)). Bir
parça özetlenemezse durum satırı "Not eksik" (turuncu) olur ve son LLM hatası gösterilir.
Komut satırı: `python toplanti.py basla` (ya da `bitir <klasör>`). Eski `--inceleme-yok` bayrağı kabul edilir, yok sayılır.

Kalıcı altyazı için Teams: … → Ayarlar → Erişilebilirlik → *Toplantılarımda her zaman alt yazıları göster*.

## Terimler (otomatik, onaysız)

- Terim havuzu `terimler/terimler.json` dosyasında, uygulamanın yanında tutulur (exe'de exe klasöründe); toplantı serisine göre ayrılır. Onay istenmez.
- Adaylar davetten (başlık, gündem, davet metni, ek adları), aynı serinin önceki `not.md` dosyalarından ve katılımcı adlarından gelir. Terim ancak bağımsız kanıtla öğrenilir; düzeltilmiş metin ve LLM çıktısı kanıt sayılmaz.
- Parça düzeltmesinde LLM yalnızca öneri verir; her öneri kelime sınırı, ek uyumu, sayı/tarih sabitliği ve en çok %30 değişim kapısından geçer. Yazım önerileri uygulanmaz; kelime Belirsizler'e düşer.
- Yanlış öğrenilen bir terim için: `python tools\terim_geri_al.py "Terim"` (geçmiş: `--liste`). Geri alınan bir daha öğrenilmez.
- Elle terim eklemek için `terimler\elle.txt`: satır başına `Terim` ya da `Terim | varyant1; varyant2` (`#` yorum). Bu dosya JSON'a yazılmaz.
- Eski `sozluk.json` varsa `python tools\terim_aktar.py sozluk.json` ile terimleri taşınır (yedekli; alias'lar taşınmaz).

## Notla çalışma

- **Kaynağa git:** Karar ve aksiyonların yanındaki `⏱10:12:03`'e tıklayınca transkript o anda açılır.
- **Soru sor:** Not sekmesindeki kutuya "Test ortamı ne zaman hazır, kim söyledi?" gibi sorular yazılır; cevap yalnızca bu toplantının transkriptinden gelir.
- **Ara:** Geçmiş sekmesinde tüm toplantıların transkript ve notlarında arama yapılır.
- **Yeniden özetle:** Geçmiş sekmesinde bir toplantı seçilip basılır; **tüm** parçalar güncel terim havuzuyla yeniden
  düzeltilip özetlenir ve yeni not üretilir (yarım kalmış kayıtlar için de). Eski not `not.md.yedek-YYYYMMDD-HHMM`
  olarak saklanır. Tek parça için: "Seçili parçayı yeniden özetle".
  Kayıtta **altyazı taşması** varsa (aynı satırlar yüzlerce kez; Geçmiş bilgi satırında "⚠ Altyazı taşması") önce
  transkript temizlenir: birebir tekrarlar ve yarım halleri elenir, parçalar temiz satırlardan yeniden kurulur;
  eski hali `parcalar.yedek-…`, `altyazi.jsonl.yedek-…`, `oneriler.json.yedek-…` olarak klasörde kalır. Olaylar'da
  `taşma temizlendi: 26247 → N satır, 223 → M parça`. Uygulamasız: `python tools\transkript_temizle.py
  toplantilar\<klasör> --kuru` (yalnız sayılar), `--kuru` olmadan temizler. Ayrıntı: [geliştirme planı, bölüm 13](docs/GELISTIRME_PLANI.md#13-altyazı-taşması-2026-09-30).
- **Düzenle / Word / PDF / Kişiye özel e-postalar:** Not sekmesindeki düğmeler.
- **Jira/Planner CSV:** Not sekmesi → "Jira/Planner CSV" aksiyon tablosunu `aksiyonlar.csv` olarak kaydeder
  (sütunlar `Summary, Assignee, Due Date, Description, Issue Type`; UTF-8). Jira: *Issues → Import issues from CSV*.
  Planner: CSV Excel'de açılıp görevler kopyalanır. Doğrudan Jira'ya kayıt açılmaz (kimlik bilgisi gerekmez).
- **Konuşma payı:** Geçmiş sekmesinde bir toplantı seçilince üstte "Konuşma payı: Selin %38, Emre %27, …"
  (satırın süresi = sonraki satıra kadar, en fazla 15 sn). Nota yazılmaz; `meta.json` → `konusma_paylari`.
- **Şablon ve saklama süresi:** Ayarlar → Not şablonu, Transkript saklama süresi.
- **Bilgilendirme:** Canlı sekmesi → "Katılımcıları bilgilendir" metni panoya kopyalar; Teams sohbetine yapıştırın.

## Ölçüm (Faz 0)

Konuşmacı/WER ölçümü için **isteğe bağlı, yerel** kayıt: `config.json` → `"olcum_kaydi": true` (varsayılan `false`).
Açıkken toplantı klasörüne `olcum/` yazılır (`toplantilar/` zaten `.gitignore`'da; repoya girmez): `klipler/NNNN.wav`
(STT'ye giden klipler, 16 kHz mono), `klipler.jsonl` (zaman/örnek sayacı, ham STT metni), `okumalar.jsonl` (ham UIA
panel okumaları). Kayıt işi `ses.SesServisi.olcum_kaydi_ac(klasor)` ve `Yakalayici.okuma_kaydi_ac(klasor)` ile bağlanır.

```powershell
python tools\etiketle.py toplantilar\<klasor> --bas 0 --bit 10   # ilk 10 dk'yı dinle, konuşmacı + metin etiketle (olcum/gercek.jsonl)
python tools\olcum.py toplantilar\<klasor> --token               # WER, SER-kesin, belirsiz oran, halüsinasyon (yalnız sayı) + karakter/token
python tools\replay.py toplantilar\<klasor> [--stt] [--bas 0 --bit 10]   # kaydı bugünkü hattan çevrimdışı geçirip metrikleri hesapla
```

## Ses izi (Faz 6 spike, isteğe bağlı, varsayılan kapalı)

Teams altyazısı kapalıyken konuşmacısı `?` kalan satırlara **yalnızca ad adayı** vermek için deneme: `config.json` →
`"ses_izi": true`, `"ses_izi_model": "<gömme modeli>.onnx"` (isteğe bağlı `"ses_izi_esik": 0.75`). `sherpa-onnx`
zorunlu bağımlılık değildir (`pip install sherpa-onnx`); paket ya da model yoksa özellik tek log satırıyla kapanır, not hattı
etkilenmez. Parça kapanınca (arka planda) kesin loopback satırlarından (`spk_guven` ≥ 0.9, çakışma yok, ≥ 2 sn; ses halka
tampondan) kişi başı centroid kurulur; altyazısız (`kapsama_yok`) `?` satırı yalnız en iyi kosinüs ≥ eşik **ve** ikinciye
fark ≥ 0.10 **ve** kişinin ≥ 5 centroid klibi varsa atanır. Atanan satırın `spk_guven` değeri en çok 0.6'dır: her yerde `Ad?`
görünür, sahip/katılımcı üretmez, `kaynak_konusmaci: "ses_izi"`. Altyazı kararı olan satırlara dokunulmaz. Ses izi (centroid)
yalnız toplantı süresince bellekte tutulur, diske yazılmaz. Kapı ölçümü Faz 0 kaydında (RTF < 0.3, `?`'lerin ≥ %50'si doğru,
yanlış 0):

```powershell
python tools\ses_izi_dene.py toplantilar\<klasor> --model gomme.onnx   # RTF, kapsama, doğru/yanlış, eşik önerisi
```

Motor bağlantısı: `Motor.ses_izi_kur(ayar, ses_al_fn)` (`ses_al_fn(akis, bas, bit)` → 16 kHz ses, halka tampon).

## Test

```powershell
pip install pytest
python -m pytest tests
```

Testler LLM sunucusu ya da ses cihazı gerektirmez. GitHub Actions (`.github/workflows/briefmind.yml`, repo
kökünde) `briefmind/` değişince testleri Ubuntu + Python 3.11 ve Windows + Python 3.9 üzerinde çalıştırır; Actions
sekmesinden elle (**Run workflow**) tetiklenince Windows'ta exe derlenir ve `BriefMind` artefaktı olarak indirilir. Uçtan uca analiz ve yol haritası için bkz. [`docs/GELISTIRME_PLANI.md`](docs/GELISTIRME_PLANI.md).

## Gizlilik ve uyum

- Varsayılan ayarlarda ses diske yazılmaz; STT'ye giden parça bellekte tutulur, yanıt gelince silinir. Tek
  istisnalar, varsayılan kapalı olan `olcum_kaydi` (yukarıdaki Ölçüm bölümü) ve `kesinti_kaydi` (STT kesilince
  klipleri `olcum/kesinti/` altına yazar) ayarlarıdır; ikisi de yerel ve repo dışıdır.
- Embedding açıksa transkript pencereleri yalnızca kurum içi embedding servisine gönderilir; vektörler toplantı klasöründe (bilgisayarda) kalır, merkezi bir veritabanına yazılmaz.
- `config.json`, `toplantilar/`, `terimler/` (terim havuzu) ve eski `sozluk.json` `.gitignore`'dadır; repoya girmez.
- Kurum içi adresler yer tutucudur; gerçek adresler yalnızca `config.json`'da durur.
