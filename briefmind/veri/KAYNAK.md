# veri/ kaynakları ve lisansları

## yaygin_kelimeler.txt

Yaygın Türkçe kelime (kök) listesi. `kelime.yaygin_mi` okur; `duzelt.py` kapısı ve `terim.py`
öğrenme kuralı "`eski` yaygın Türkçe kelime değil" şartını bununla sınar (`komite` → `commit` reddi).

**Kaynak:** [wordfreq](https://github.com/rspeer/wordfreq) 3.1.1, `wordfreq/data/small_tr.msgpack.gz`
(Robyn Speer, Exquisite Corpus). wordfreq'in Türkçe verisi şu derlemlerden birleştirilmiştir:
Wikipedia, OPUS OpenSubtitles 2018, OSCAR (web), Twitter.

**Lisans:** wordfreq kodu Apache-2.0; veri dosyaları **CC BY-SA 4.0**
(<https://creativecommons.org/licenses/by-sa/4.0/>). Bu dosya o verinin uyarlamasıdır ve
**CC BY-SA 4.0** ile dağıtılır (ShareAlike). Lisans yalnız bu veri dosyasını kapsar; depodaki kod
bundan etkilenmez. Yeniden dağıtımda bu atıf korunmalıdır.

**Atıf:** Robyn Speer. (2022). rspeer/wordfreq: v3.0 (v3.0.2). Zenodo.
<https://doi.org/10.5281/zenodo.7199437>. OpenSubtitles verisi: P. Lison ve J. Tiedemann (2016),
"OpenSubtitles2016: Extracting Large Parallel Corpora from Movie and TV Subtitles", LREC 2016;
<http://www.opensubtitles.org/>.

**Değişiklikler (uyarlama adımları):**
1. `small_tr` listesindeki her kelime `kelime.normalize` ile normalize edildi; Türk alfabesi dışı
   harf, rakam ya da boşluk içerenler ve tek harfliler atıldı; aynı normal biçimler toplandı.
2. **Kök (lemma) frekansı:** Türkçe eklemeli olduğundan yüzey biçim sırası yanıltıcıdır (`komite`
   yüzey sırası ~10k, `komitede`, `komiteye` ayrı sayılır). Her kelime `kelime.kok_ayir` (ad
   çekimi: çoğul, iyelik, hal, -ki, ek-fiil; ünlü/ünsüz uyumu, yumuşama) ile en kısa ≥3 harfli köke
   indirgendi; kök derlemde ayrı kelime olarak geçmeli ve frekansı kelimenin en az %5'i olmalı.
   Frekanslar kökte toplandı.
3. **İngilizce/özel ad elemesi:** Türkçeye özgü harf içermeyen ve wordfreq `large_en` frekansı
   Türkçe frekansının ≥ %50'si olan kelimeler atıldı (`the`, `john`, `okay` ...); bunların içinden
   Türkçede gerçekten yaygın olanlar elle korundu (`program`, `test`, `model`, `ad`, `red`, `terminal`,
   `nice`, `seven` ...).
4. **Teknik terim elemesi:** tipik terim/ürün adları yaygın sayılmadı (`commit`, `pipeline`, `jira`,
   `jenkins`, `sprint`, `deploy`, `sql`, `server`, `data`, `link`, `mail` ...). Türkçede gerçekten
   yaygın olanlar (`komite`, `kampanya`, `proje`, `kod`, `sunucu`, `rapor`) listede kalır.
5. Kök frekansına göre **tüm kökler** (24220) yazıldı (satır başına bir, normalize). Adım 3'te elle
   korunanlara `volt` eklendi (İngilizce frekansı eşiğe takılıyordu).
6. **Elle eklenen Türkçe kelimeler** (dosyanın sonunda `#` açıklamalı blok): derlemde (small_tr) hiç
   geçmeyen ama sık kullanılan bir terim adıyla çarpışan gerçek Türkçe kelimeler: `doker` (Docker),
   `redif` (Redis). Bu blok uyarlama değil, proje katkısıdır.

**Neden tüm kökler (plan "ilk ~5k"):** plan sayısı yüzey biçim varsayımıyla yazılmıştı. İlk 5000 kök
`terminal` (~8100. kök), ilk 10000 kök `volt` (~9600.), `jest` (~10600.) gibi gerçek Türkçe kelimeleri
kaçırıyordu; kapı bunları terime çeviriyordu (`volt` → `Vault`, `jest` → `Jest`). Liste büyüdükçe kapı
yalnız daha tutucu olur (yaygın kelime terime ya da ada çevrilmez, metin değişmez). Bedeli: sık geçen
kişi adları (`ekinci`, `serkan`) da listede olduğundan küçük harfle yazılmış bu adlar büyük harfe
düzeltilmez. Liste dışındaki Türkçe kelimeler için ayrıca `kelime.turkce_gibi_mi` (ses yapısı) devrededir.
Gerekirse dosyanın ilk N satırı kullanılarak küçültülebilir (sıra frekans sırasıdır).

**Yeniden üretim:** wordfreq 3.1.1 tekerleğindeki `small_tr` ve `large_en` msgpack dosyaları
`msgpack` ile okunur (kova `i` → frekans `10^(-i/100)`), yukarıdaki adımlar uygulanır.
