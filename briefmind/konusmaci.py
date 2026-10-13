"""
konusmaci.py — yalniz zamana dayali konusmaci atamasi (docs/MIMARI_v3.md §3 "Cizelge", "Konusmaci atama").

Girdi:
  okuma_ekle({"tur":"okuma","okuma_no","bas_epoch","bit_epoch","grup","gorunen":[[kim, metin], ...],
              "degisti","donuk","yeniden_bul","pencere","okuma_sn"})
      Teams altyazi panelinin HER okumasi (bos olanlar dahil). Metin YALNIZ kelime sayisi, satir eslestirme ve
      (metin_ipucu acikken) kok duzeyinde ikincil ipucu icin kullanilir; hicbir yere yazilmaz, transkripte girmez.
      "?" adli satirlar (adi okunamayan uzun satir) etkinlik sayilir: kimlik_turu("?") == "hayalet".
  klip_ekle({"tur":"klip","klip_no","akis","bas","bit","text",...})   ses servisinin klip olayi (epoch).
  kulaklik_ayarla(True|False|None)                              pasif kulaklik tespiti.
Cikti: kontrol(an) / bitir() -> karar dict listesi (klip_no sirali):
  {"klip_no","speaker","spk_guven","adaylar","devir","cakisma","at","bol","neden", + "akis","kapsama","kimlik_turu",
   "cizelge_canli"}; gec yeniden puanlanan "?" karari ayni klip_no ile "guncelleme": True tasir.
  neden: baskin | zayif | kimse | hayalet | yapiskan | gec_satir | cakisma | devir | devir_bolunemedi | kapsama_yok
         | panel_donuk | ben_* | yanki* ; ek: +capa_az +L_basamak +L_supheli +L_onaysiz +mad +eksik +kisa +kisa_capa_az
         +bosluk +kaba +gec +seyrek +metin +oda +genel

Cizelge: kisi basi aralik birlesimi. Ardisik okumalarda gorunen satirlar hizalanir (kaydirma araniyor);
satirin `son`u kelime sayisi (gorulen en yuksek) arttiginda ya da kuyruk (son 2 kelime, satir basi da kaymis)
degistiginde ilerler; yeniden yazim/noktalama uzatmaz. Duraklamis satirin yeniden buyumesi yeni aralik acar
("devam"). Aralik konusma zamanina L ile kaydirilir, iki uca simetrik R/2 gozlem bandi eklenir:
[ilk - L - R/2, son - L + R/2]. Yavas okumada (okumalar arasi > duraklama) bant onceki okumaya kadar genisler.

Kapsama: okumalar arasi > max(3R, bosluk_sn) -> okuma boslugu; <= kisa_bosluk_sn ise satirlar korunur (yeniden
taban yok). Panel donuk_sn boyunca degismez ve hoparlorde konusma varsa "donuk" boslugu (neden panel_donuk).
Klip penceresinin > %50'si bosluksa karar "?" (kapsama_yok | panel_donuk); daha az bosluk tavan 0.6 (+bosluk).
Karar, panel klibin altyazi zamanindan sonra en az bir kez degisene (ya da son tarihe) kadar bekler.

Gecikme L: capa ornekleri (sessizlik sonrasi klip basi ~ aralik basi) kayan medyani (son L_sure_sn belirgin
sapiyorsa o alt pencere: suruklenme); aday penceresi L_ust = max(L_maks, L + L_pay), isinmada (capa_min'den az
ornek) capraz_ust ve birden cok aday varsa oy coklugu. Boslukta acilan (kaba) satir baslari aday sayilir ama ornek
vermez. Her capraz_aralik_sn'de onset'ler ile aralik baslarinin 0-10 sn capraz ilintisi (_L_capraz); iki ardisik
degerlendirme L'den belirgin farkli ayni degeri verirse L sifirlanir.

Metin ipucu (metin_ipucu=1; ikincil, asla birincil olcut degil): satir basina son metin_pencere_sn'nin ayirt edici
kokleri (>= 4 harf, durak disi; sayi ve buyuk harfli ad birebir). (1) klibin >= 2 ayirt edici kelimesi bir satirda
ilk kez gorulunce gecikme ornegi ("metin" capasi); (2) 60 sn boyunca hicbir klibin kelimesi altyazida gorunmez ama
satirlar degisiyorsa durum()["tutarsiz"]; (3) zaman iki aday verdiginde (devir / zayif pay; gercek ust uste binmede
degil) kok ortusmesi esitlik bozucu (tavan 0.6).

Zaman: tum epoch'lar (okuma bas/bit, klip bas/bit, kontrol(an)) saat.simdi() tabanlidir; duvar saati adimlarindan
etkilenmez (konusmaci.py kendisi zaman kaynagi cagirmaz).

Saf Python: Qt / ses / ag yok. Deterministik; klip basina O(kisi * log n).
"""
import bisect
import difflib
import re
from collections import OrderedDict, deque

VARSAYILAN_ESIKLER = {
    "baskin": 0.60,             # baskin aday payi >= -> ad
    "ikinci": 0.25,             # ikinci aday payi < -> tek aday; >= iki aday (devir / cakisma)
    "kesin": 0.70,              # gosterim esigi (spk_guven < kesin -> "Ad?"); burada yalniz tavanlar bunun alti
    "tavan": 0.60,              # belirsizlik tavani (capa az, MAD buyuk, oda/genel, eksik kapsama)
    "L_bas": 2.0,               # L baslangic (sn)
    "L_maks": 5.0,              # aday penceresi: L_ust = max(L_maks, L + L_pay); ustu atilir
    "L_pay": 2.0,               # aday penceresi payi (sn)
    "L_pencere": 31,            # kayan medyan pencere boyu (ornek sayisi)
    "L_sure_sn": 90.0,          # ... ve suresi: son bu kadar sn'nin medyani tum pencereden > max(guven_payi, MAD)
                                #     saparsa (suruklenen gecikme) L o olur; yoksa tum pencere (histerezis)
    "capa_min": 3,              # bu kadar capaya kadar tavan
    "basamak_n": 6,             # L basamagi: son N capanin en az K'si ...
    "basamak_k": 5,
    "basamak_sn": 0.6,          # ... L'den ayni yone >= max(bu, 2*MAD) saparsa pencere o capalara kisalir
    "basamak_tavan_capa": 5,    # basamak / saat capasi / capraz ilinti sonrasi bu kadar yeni capaya kadar tavan
    "saat_adim_sn": 0.3,        # ses saat capasi bu kadar sicradiysa (C1 'capa' olayi) L capalari duzeltilir
    "mad_maks": 1.0,            # MAD > -> tavan
    "R_bas": 1.2,               # R olculene kadar (sn)
    "R_pencere": 10,            # R = son N okuma periyodunun medyani
    "kapsama_kat": 3.0,         # okuma boslugu esigi: max(kapsama_kat * R, bosluk_sn)
    "bosluk_sn": 5.0,           # ... (UIA yurumesi okuma dongusunu birkac sn tutabilir: bosluk sayilmaz)
    "kisa_bosluk_sn": 20.0,     # bu kadar bosluktan sonra satirlar korunur (ust/aralik sifirlanmaz)
    "bosluk_pay": 0.5,          # klip penceresinin bu payindan fazlasi bosluksa karar "?" (kapsama_yok/panel_donuk);
                                #     klibin bu payi baskin kisinin kaba (bosluk sonrasi) baslangic bandindaysa tavan
    "donuk_sn": 12.0,           # panel bu kadar degismedi ve hoparlorde konusma var -> donuk
    "donuk_ses_sn": 5.0,        # ... son degisimden sonra en az bu kadar loopback konusmasi (tek kisa halusinasyon
                                #     klibi donuk saydirmaz)
    "capraz_pencere_sn": 180.0, # capraz ilinti: son bu kadar sn'nin onset'leri / aralik baslari
    "capraz_en_az": 4,          # ... en az bu kadar onset eslesmeli (ve >= %60)
    "capraz_ust_sn": 10.0,      # ... aranan gecikme 0..bu (0.25 adim); metin capasi ust siniri
    "capraz_aralik_sn": 30.0,   # ... degerlendirme araligi
    "capraz_yakin_sn": 60.0,    # ... yakin pencere: son bu kadar sn'nin onset'leri L'den farkli tepe veriyorsa gecikme
                                #     sicramis olabilir: L supheli (tavan), ikinci degerlendirmede L sifirlanir
    "supheli_onset_n": 3,       # art arda bu kadar sessizlik sonrasi onset'in satiri beklenen gecikmede yoksa (ya da
                                #     L'den cok erken: onceki konusmacinin gec satiri) L supheli: tavan, capraz ilinti
                                #     her yeni onset'te yakin pencerede (>= capraz_en_az onset) degerlendirilir
    "gec_satir_sn": 3.5,        # gec beliren satir payi: kisa klibin karari bu kadar ek okuma bekler; klip bitisinden
                                #     sonra bu kadar icinde aciklanmayan (onset'siz) yeni satir varsa tavan (+gec)
    "kisa_klip_bekle_sn": 2.0,  # bundan kisa klipte gec satir beklenir (uzun klipte satir klip icinde gorulur)
    "gec_yukseltme_sn": 30.0,   # "?" karari bu kadar sure yeniden puanlanir (ad cikarsa guncelleme)
    "yapiskan_sn": 4.0,         # yapiskan: tek kisinin araligi klip basindan en cok bu kadar once bitmis
    "yapiskan_guven": 0.6,
    "yapiskan_klip_sn": 8.0,    # yapiskan yalniz bu kadar kisa klipte
    "metin_ipucu": 1,           # 0: metin ipuclari kapali (yalniz zaman)
    "metin_pencere_sn": 60.0,   # satir/kisi basina kok hafizasi
    "metin_ortusme": 0.35,      # esitlik bozucu: kok ortusmesi >= bu ve digerinin >= 2 kati
    "erteleme_sn": 15.0,        # karar en gec bit + bu
    "sessizlik_sn": 1.0,        # capa (a): klip oncesi loopback sessizligi
    "duraklama_bosluk_sn": 0.5,  # capa (b): yeniden buyume capasinda klip oncesi bosluk
    "duraklama_sn": 1.0,        # satir bu kadar (ve en az 2R) buyumezse duraklamis sayilir
    "devir_pay": 0.3,           # devir toleransi = R + devir_pay + MAD (bant kesisimi bunun altindaysa sirali)
    "derinlik_maks": 2,         # yeniden kesme derinligi
    "bolunemedi_guven": 0.5,    # "A -> B" / "A / B" spk_guven
    "ben_kapsama": 0.5,         # mikrofon: kullanici altyazisi payi >= -> ben
    "ben_guven": 0.95,
    "ben_zayif": 0.60,          # mikrofon: kullanici altyazisi yok -> ben, bu guven
    "yanki_ses_pay": 0.5,       # yanki icin mikrofon klibinin loopback klipleriyle ortusme payi (her yolda sart)
    "lb_tolerans_sn": 0.5,      # mikrofon karari: son loopback klibi en az bit - bu kadara ulasmali (yanki kaynagi geldi)
    "lb_bekle_sn": 8.0,         # ... ya da bit + bu kadar beklenir (uzak taraf sustuysa loopback klibi gelmez)
    "ben_net": 0.8,             # loopback'te ses varken ben altyazisi bu payi asar ve uzak bant yoksa yanki degil
    "hayalet_satir": 3,         # sezgisel ad bu kadar ayri satirda gorulunce ad sayilir
    "kisa_klip_sn": 0.3,        # bundan kisa klipte zamanlama cozunurlugu yetmez -> tavan
    "kisa_soz_sn": 1.0,         # bundan kisa klipte kesinlik payi en az R/2 (tek kelimelik satir, hizli devir)
    "kisa_capa_min": 8,         # kisa klipte (< kisa_soz_sn) bu kadar capaya kadar tavan
    "guven_payi": 0.3,          # kesinlik icin zamanlama payi (sn; MAD buyukse MAD)
    "aralik_tavan": 20000,      # toplanti basi aralik tavani
    "budama_sn": 600.0,         # tavan asilinca bundan eski araliklar budanir (halka tampon 10 dk)
}

_KATLA = str.maketrans("çğıöşüâîû", "cgiosuaiu")
_PARANTEZ = re.compile(r"\([^)]*\)")
_ODA_SOZ = {"oda", "odasi", "salon", "salonu", "room", "rooms", "konferans", "conference", "mtr"}
_GENEL_SOZ = {"konuk", "guest", "misafir", "bilinmeyen", "unknown", "anonim", "anonymous"}
_NUMARALI = re.compile(r"^(konusmaci|speaker|katilimci|participant|kullanici|user)( \d+)?$")
_BOSLUK_ONCELIK = {"okuma": 1, "donuk": 2, "grup": 3}
# metin ipucu: ayirt edici sayilmayan yaygin kelimeler (ascii katli, >= 4 harf olanlar; kisalar zaten elenir)
_DURAK = {
    "icin", "olan", "olarak", "gibi", "daha", "sonra", "once", "zaman", "nasil", "neden", "hangi", "evet", "hayir",
    "tamam", "yani", "iste", "fakat", "ancak", "cunku", "bunu", "bunun", "bunlar", "sunu", "onun", "bizim", "sizin",
    "onlar", "biraz", "kadar", "boyle", "soyle", "oyle", "belki", "zaten", "hemen", "artik", "bile", "yine", "gene",
    "hani", "yoksa", "veya", "yahut", "degil", "olur", "oldu", "olacak", "oluyor", "olsun", "olmaz", "olmus", "vardi",
    "yoktu", "simdi", "bugun", "yarin", "biraz", "cok", "peki", "aynen", "tabii", "tabi", "galiba", "herhalde",
    "aslinda", "mesela", "ornegin", "yani", "sadece", "yalniz", "hepsi", "butun", "diger", "baska", "kendi",
    "orada", "burada", "surada", "ondan", "bundan", "sundan", "diye", "dedi", "dedim", "deriz", "mi", "mu",
    "the", "and", "that", "this", "with", "have", "will", "okay", "from", "what", "just", "like", "then", "than",
    "there", "here", "about", "would", "could", "should", "also", "into", "your", "they", "them", "were", "been",
}
_KELIME = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


def _norm(s, parantez_sil=True):
    """Turkce kucuk harf + ascii katlama + noktalama temizligi (karsilastirma icin)."""
    s = (s or "").replace("İ", "i").replace("I", "ı").lower().translate(_KATLA)
    if parantez_sil:
        s = _PARANTEZ.sub(" ", s)
    s = re.sub(r"['’`´]", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _ayirt_edici(metin):
    """Metnin ayirt edici belirtecleri (metin ipucu): >= 4 harfli, durak disi kelimelerin 5 harflik koku (Turkce
    ekleri kabaca yok sayar, motor._kokler ile ayni yaklasim); sayilar ve buyuk harfle baslayan kelimeler (ad)
    birebir ('#' / '@' onekiyle). Metin saklanmaz, yalniz bu kume karsilastirilir."""
    out = set()
    for m in _KELIME.finditer(metin or ""):
        w = m.group(0)
        if w.isdigit():
            out.add("#" + w)
            continue
        n = _norm(w).replace(" ", "")
        if not n:
            continue
        if w[0].isupper() and len(n) >= 3:
            out.add("@" + n)
        if len(n) >= 4 and n not in _DURAK:
            out.add(n[:5])
    return out


def _medyan(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _birlesim(parcalar, a, b):
    """[a, b] icine kirpilmis parcalarin birlesim uzunlugu."""
    kirp = sorted((max(x, a), min(y, b)) for x, y in parcalar if y > a and x < b)
    top, ux, uy = 0.0, None, None
    for x, y in kirp:
        if y <= x:
            continue
        if ux is None or x > uy:
            if ux is not None:
                top += uy - ux
            ux, uy = x, y
        elif y > uy:
            uy = y
    if ux is not None:
        top += uy - ux
    return top


def _en_buyuk_kesisim(p1, p2, a, b):
    """Iki parca kumesinin [a, b] icindeki en uzun tek ortusmesi (sinir basina)."""
    en = 0.0
    for x1, y1 in p1:
        for x2, y2 in p2:
            o = min(y1, y2, b) - max(x1, x2, a)
            if o > en:
                en = o
    return en


class _Kisi:
    __slots__ = ("ad", "seg", "ilkler", "maks_sure", "bant_maks", "kaba_var", "satir", "ben", "kokler")

    def __init__(self, ad, ben):
        self.ad = ad
        self.seg = []          # [ilk, son, r_ilk, r_son, tur, kaba_son]  (altyazi zamani, epoch); ilk sirali
        self.ilkler = []       # kaba_son > 0: son, yavas okumada (okuma boslugunda) bu kadar genis araligin sonuna
        self.maks_sure = 0.0   #   yazildi (buyumenin ani gozlenmedi: gercek son araligin herhangi bir yerinde)
        self.bant_maks = 0.0   # en genis baslangic bandi yarisi (yavas okuma / bosluk sonrasi)
        self.kaba_var = False  # kaba (genis bantli) bas ya da son var mi
        self.satir = 0         # ayri satir sayisi (hayalet ad kurali)
        self.ben = ben         # 0 | 1 (zayif ben eslesmesi) | 2 (kesin)
        self.kokler = deque(maxlen=600)   # (ilk gorulme t, kok): metin ipucu esitlik bozucu


class _Satir:
    __slots__ = ("kim", "kelimeler", "ust", "son_buyume", "seg", "taban", "no", "kokler")
    _sayac = 0

    def __init__(self, kim, kelimeler, t, taban):
        self.kim = kim
        self.kelimeler = kelimeler
        self.ust = len(kelimeler)   # gorulen en yuksek kelime sayisi (revizyon uzatmasin)
        self.son_buyume = t
        self.seg = None
        self.taban = taban          # baslangici bilinmeyen satir (ilk okuma / kapsama donusu / yeniden cizim)
        _Satir._sayac += 1
        self.no = _Satir._sayac
        self.kokler = {}            # kok -> satirda ilk gorulme t (metin ipucu)


class Konusmaci:
    def __init__(self, ben=None, katilimcilar=(), esikler=None, olay_fn=None):
        self.ben = (ben or "").strip() or None
        self.esik = dict(VARSAYILAN_ESIKLER)
        if esikler:
            if isinstance(esikler.get("esikler"), dict):     # config sozlugunun tamami verildiyse
                esikler = esikler["esikler"]
            for k, v in esikler.items():
                if k in self.esik and isinstance(v, (int, float)) and not isinstance(v, bool):
                    self.esik[k] = v
        self.olay_fn = olay_fn
        self._katilimci = {_norm(self._ad_sirala(a)) for a in (katilimcilar or ()) if a and _norm(a)}
        self._kisiler = OrderedDict()
        self._onceki = None                 # onceki okumanin satirlari (_Satir listesi)
        self._son_ham = None                # onceki okumanin ham satirlari (degisim izi)
        self._son_degisim = None            # satirlarin en son degistigi okuma ani (altyazi zamani)
        self._son_okuma_bas = None
        self._son_okuma_bit = None
        self._ilk_okuma = None
        self._periyot = deque(maxlen=int(self.esik["R_pencere"]))
        self.R = float(self.esik["R_bas"])
        self._bosluk_bas, self._bosluk_bit, self._bosluk_tur = [], [], []   # kapsama yok araliklari (altyazi zamani)
        self._grupsuz_bas = None
        self._acik = None                   # su an acik kapsama boslugu (bas, an)
        self._donuk = False                 # panel donuk (degismiyor, hoparlorde konusma var)
        self._donuk_bas = None
        self._an = None
        self._surum = 0                     # cizelge degisim sayaci (gec yeniden puanlama icin)
        self._L_ornek = deque(maxlen=int(self.esik["L_pencere"]))
        self._L_zaman = deque(maxlen=int(self.esik["L_pencere"]))   # orneklerin konusma zamani (None: bilinmiyor)
        self._L_tur = deque(maxlen=int(self.esik["L_pencere"]))     # orneklerin turu (a | b | metin | capraz)
        self.L = float(self.esik["L_bas"])
        self.MAD = 0.0
        self._atilan_ornek = 0
        self._ardisik_atilan = 0            # art arda L_ust disina dusen ornek (gecikme sicradi: L supheli, tavan)
        self._basamak_kalan = 0             # L basamagi / saat capasi sonrasi tavanli kalan capa sayisi
        self._son_basamak = None            # son algilanan L basamagi (sn; saat capasi gelince geri alinir)
        self._baslangiclar, self._bas_tur = [], []        # uzak kisi aralik baslari (capa adaylari; kesin zamanli)
        self._bas_seg = []                  # ... ayni sirayla (seg, kisi): capraz ilintide aralik sonu da denetlenir
        self._onset_bit = {}                # onset (klip basi) -> klip bitisi (capraz ilinti: satir klip bitene dek surmeli)
        self._tutarli_onset = 0             # suphe altinda art arda L ile uyumlu capa (2 olunca onset suphesi kalkar)
        self._kaba_baslar = deque(maxlen=400)   # (ilk, bant/2, tur, seg): yavas okumada acilan (kesin zamani bilinmeyen) baslar
        self._onsetler = []                 # [(klip_bas, onceki_bosluk)] loopback, sirali (capa adaylari)
        self._onset_gecmis = deque(maxlen=400)   # ayni, capraz ilinti icin tutulur
        self._aciklanmayan = deque(maxlen=50)    # ornek vermeyen onset'ler: sonraki capalarin aday satiri onlarin olabilir
        self._capa_satirlar = deque(maxlen=100)  # capa olmus aralik baslari (bir satir tek onset'in capasidir)
        self._capa_gecmis = deque(maxlen=int(self.esik["L_pencere"]))   # capa basi aday gecikme listeleri
        self._capa_listede = set()          # aday listesi _capa_gecmis'e girmis onset'ler (bekletilen yeniden eklenmez)
        self._capraz_son = None             # son capraz ilinti degerlendirme ani
        self._capraz_onceki = None          # onceki degerlendirmenin L'den farkli tepe degeri
        self._capraz_onceki_o = None        # ... o degerlendirmenin en yeni onset'i (onay bagimsiz veri ister)
        self._dis_kanit_an = None           # L_yeniden_olc (motor: atama basarisiz) ani: ayni veriyle onaya izin
        self._capraz_zorla = False
        self._capraz_yeni_onset = False     # son degerlendirmeden beri yeni onset geldi (L onaysizken erken degerlendirme)
        self._L_supheli_bas = None          # capraz ilinti L'den farkli tepe verdi / art arda aciklanamayan onset
                                            # (gecikme sicramis olabilir): tavan
        self._supheli_onset = 0             # art arda satiri beklenen gecikmede bulunmayan onset (supheli_onset_n)
        self._supheli_kaynak = None         # suphenin kaynagi: capraz | onset
        self._L_onay = None                 # capraz ilintinin dogruladigi L (yoksa / L bundan belirgin saptiysa tavan)
        self._son_lb_bit = None
        self._lb = deque(maxlen=400)        # loopback klip araliklari (kapsama yokken yanki tespiti; donuk tespiti)
        self._lb_hepsi = deque(maxlen=400)  # ... yeniden kesme cocuklari dahil (satir basi aciklamasi: kesim noktasi da onset)
        self._bekleyen = {}
        self._gec = OrderedDict()           # "?" kararli klipler: gec yeniden puanlama
        self._bol = OrderedDict()           # bol karari verilmis klipler (bolunemedi / cocuk derinligi)
        self._kok_gorulme = {}              # kok -> [(t, satir_no)] (metin ipucu; metin_pencere_sn ile budanir)
        self._kok_budama = None
        self._metin_bekleyen = []           # metin capasi bekleyen klipler
        self._metin_eslesmeyen = 0          # son eslesmeden beri kelimesi altyazida gorunmeyen klip sayisi
        self._son_metin_esleme = None
        self._tutarsiz = False
        self._canli = False                 # karar verilirken: cizelge canli mi (karar alani)
        self.kulaklik = None
        self._seg_sayisi = 0
        self._tasma = False

    # ------------------------------------------------------------------ yardimcilar

    def _log(self, mesaj, **ek):
        if self.olay_fn is None:
            return
        try:
            d = {"tur": "log", "kaynak": "konusmaci", "mesaj": mesaj}
            d.update(ek)
            self.olay_fn(d)
        except Exception:
            pass

    @staticmethod
    def _ad_sirala(ad):
        """'Kaya, Deniz' (Soyad, Ad: Outlook/AD yazimi) -> 'Deniz Kaya'."""
        ad = (ad or "").strip()
        if ad.count(",") == 1:
            soy, isim = [x.strip() for x in ad.split(",", 1)]
            if soy and isim:
                return f"{isim} {soy}"
        return ad

    def _ben_esles(self, ad):
        """Altyazidaki ad uygulamayi kullanan kisi mi? 2: kesin (ayni ad; kelime sirasi / 'Soyad, Ad' bicimi farkli
        olabilir; ya da ikinci ad farki 'Deniz Kaya' ~ 'Deniz Can Kaya'), 1: zayif (yalniz ilk ad: ayarda 'Deniz' altyazida
        'Deniz Kaya' ya da tersi; ad listede ayri bir kisi degil ve listede ayni ilk adli baska tam ad yok; ben guveni
        tavanli), 0: degil. Konuk / oda etiketli ad ('Deniz (Konuk)') ve listede ayri kisi olarak gecen ayni ilk adli ad
        ('Deniz Arslan') ben sayilmaz."""
        if not self.ben or not ad:
            return 0
        ham = set(_norm(ad, parantez_sil=False).split())
        if ham & (_GENEL_SOZ | _ODA_SOZ):
            return 0
        a, b = _norm(self._ad_sirala(self.ben)).split(), _norm(self._ad_sirala(ad)).split()
        if not a or not b:
            return 0
        sa, sb = set(a), set(b)
        if a == b or (len(sa) > 1 and sa == sb):
            return 2
        nb, na = " ".join(b), " ".join(a)
        if len(sa) > 1 and len(sb) > 1 and (sa <= sb or sb <= sa) and a[0] == b[0]:
            if nb in self._katilimci and na in self._katilimci:
                return 0                            # listede iki ayri kisi
            return 2
        if a[0] == b[0] and (len(a) == 1) != (len(b) == 1):
            # yalniz ilk ad ('Deniz' ~ 'Deniz Kaya'): zayif. Altyazi adi listede ayri bir kisiyse ya da listede ayni
            # ilk adli baska tam ad varsa belirsiz -> ben degil
            tam = nb if len(b) > 1 else na
            if len(b) > 1 and nb in self._katilimci:
                return 0
            if any(k.split()[0] == a[0] and len(k.split()) > 1 and k != tam for k in self._katilimci):
                return 0
            return 1
        return 0

    def _ben_mi(self, ad):
        return self._ben_esles(ad) > 0

    def _kisi(self, ad):
        k = self._kisiler.get(ad)
        if k is None:
            k = _Kisi(ad, self._ben_esles(ad))
            self._kisiler[ad] = k
        return k

    def kimlik_turu(self, ad):
        """"ben" | "kisi" | "oda" | "genel" | "hayalet" (sezgisel ad, henuz < hayalet_satir satir; "?" her zaman)."""
        if ad == "?":
            return "hayalet"
        n = _norm(ad, parantez_sil=False)
        kel = set(n.split())
        if kel & _ODA_SOZ:
            return "oda"
        if kel & _GENEL_SOZ or _NUMARALI.match(_norm(ad)):
            return "genel"
        if self._ben_mi(ad):
            return "ben"
        if _norm(self._ad_sirala(ad)) in self._katilimci:
            return "kisi"
        k = self._kisiler.get(ad)
        if k is not None and k.satir >= self.esik["hayalet_satir"]:
            return "kisi"
        return "hayalet"

    def _duraklama(self):
        return max(float(self.esik["duraklama_sn"]), 2.0 * self.R)

    def _R_guncelle(self):
        if self._periyot:
            self.R = min(5.0, max(0.05, _medyan(self._periyot)))

    def _bosluk_esigi(self):
        """Okumalar arasi bundan uzun sure -> okuma boslugu (kapsama yok)."""
        return max(float(self.esik["kapsama_kat"]) * self.R, float(self.esik["bosluk_sn"]))

    def _L_ust(self):
        """Capa aday penceresi: max(L_maks, L + L_pay); ustundeki gecikme ornegi atilir. (MAD ile genisletme denendi:
        belirsiz capalari artiriyor, L sicramasinda dogru adayi eliyordu.)"""
        return max(float(self.esik["L_maks"]), self.L + float(self.esik["L_pay"]))

    def _bosluk_ekle(self, a, b, tur="okuma"):
        if b <= a:
            return
        bas, bit, turler = self._bosluk_bas, self._bosluk_bit, self._bosluk_tur
        while bas and bas[-1] <= b and bit[-1] >= a:            # ortusen / bitisik: birlestir
            a = min(a, bas.pop())
            b = max(b, bit.pop())
            t2 = turler.pop()
            if _BOSLUK_ONCELIK.get(t2, 0) > _BOSLUK_ONCELIK.get(tur, 0):
                tur = t2
        i = len(bas) if not bas or a >= bas[-1] else bisect.bisect_left(bas, a)
        bas.insert(i, a)
        bit.insert(i, b)
        turler.insert(i, tur)
        self._surum += 1

    def _bosluk_payi(self, x0, x1):
        """Altyazi zamani [x0, x1]'in bosluk payi (0-1) ve en buyuk payli bosluk turu ("okuma"|"donuk"|"grup"|None)."""
        if x1 <= x0:
            return 0.0, None
        pay = {}
        if self._ilk_okuma is None:
            return 1.0, "okuma"
        if self._ilk_okuma > x0:
            pay["okuma"] = min(x1, self._ilk_okuma) - x0
        i = bisect.bisect_left(self._bosluk_bas, x1)
        while i > 0:
            i -= 1
            o = min(self._bosluk_bit[i], x1) - max(self._bosluk_bas[i], x0)
            if self._bosluk_bit[i] <= x0:
                break
            if o > 0:
                t = self._bosluk_tur[i]
                pay[t] = pay.get(t, 0.0) + o
        if self._acik is not None:
            o = min(self._acik[1], x1) - max(self._acik[0], x0)
            if o > 0:
                t = "grup" if self._grupsuz_bas is not None else "okuma"
                pay[t] = pay.get(t, 0.0) + o
        if not pay:
            return 0.0, None
        top = min(sum(pay.values()), x1 - x0)
        tur = max(pay.items(), key=lambda kv: (kv[1], _BOSLUK_ONCELIK.get(kv[0], 0)))[0]
        return top / (x1 - x0), tur

    def _kapsama_var(self, x0, x1):
        """Altyazi zamani [x0, x1] okumalarla kapsandi mi (bosluk/grupsuz/okuyucu takilmasi yok)?"""
        if self._ilk_okuma is None or self._ilk_okuma > x0:
            return False
        i = bisect.bisect_left(self._bosluk_bas, x1) - 1
        if i >= 0 and self._bosluk_bit[i] > x0:
            return False
        if self._acik is not None and self._acik[1] > x0 and self._acik[0] < x1:
            return False
        return True

    def _acik_guncelle(self, an):
        self._acik = None
        if an is None or self._son_okuma_bit is None:
            return
        if self._grupsuz_bas is not None:
            self._acik = (self._grupsuz_bas, max(an, self._grupsuz_bas))
        elif an - self._son_okuma_bit > self._bosluk_esigi():
            self._acik = (self._son_okuma_bit, an)

    def _degisti_sonra(self, t):
        return self._son_degisim is not None and self._son_degisim >= t

    def _lb_sure(self, a, b):
        """[a, b] icindeki loopback konusma suresi (klip olaylarindan)."""
        if b <= a:
            return 0.0
        return _birlesim([p for p in self._lb if p[1] > a and p[0] < b], a, b)

    # ------------------------------------------------------------------ donuk panel

    def _donuk_baslat(self, an, kaynak):
        if self._donuk:
            return
        self._donuk = True
        self._donuk_bas = self._son_degisim if self._son_degisim is not None else self._son_okuma_bit
        if self._donuk_bas is None:
            self._donuk_bas = an
        self._bosluk_ekle(self._donuk_bas, an, "donuk")
        self._log("panel donuk", sn=round(an - self._donuk_bas, 1), kaynak=kaynak)

    def _donuk_denetle(self, an):
        """Kendi tespiti: satirlar donuk_sn'dir degismedi ve son degisimden sonra >= donuk_ses_sn loopback konusmasi
        var (hoparlorde konusma yoksa degismeyen panel normaldir)."""
        if self._donuk or self._son_degisim is None or an - self._son_degisim < self.esik["donuk_sn"]:
            return
        if self._lb_sure(self._son_degisim, an) >= self.esik["donuk_ses_sn"]:
            self._donuk_baslat(an, "konusmaci")

    def _donuk_bitir(self, an, kaynak):
        if not self._donuk:
            return
        self._donuk = False
        sure = an - self._donuk_bas if self._donuk_bas is not None else 0.0
        if sure > self.esik["kisa_bosluk_sn"]:
            self._onceki = None                     # uzun donukluk: satirlar yeniden taban
        self._surum += 1
        self._log("panel canlandı", sn=round(sure, 1), kaynak=kaynak)

    # ------------------------------------------------------------------ cizelge

    def _seg_ac(self, kim, t, tur, bant=None, capa=True):
        k = self._kisi(kim)
        R = self.R
        if self._tasma and k.seg:
            seg = k.seg[-1]
            seg[1] = max(seg[1], t)
            seg[3] = R
            k.maks_sure = max(k.maks_sure, seg[1] - seg[0])
            return seg
        r_ilk = R if bant is None else max(R, bant)
        seg = [t, t, r_ilk, R, tur, 0.0]
        k.seg.append(seg)
        k.ilkler.append(t)
        k.bant_maks = max(k.bant_maks, r_ilk / 2.0)
        if r_ilk > 2.0 * R + 1e-9:
            k.kaba_var = True
        self._seg_sayisi += 1
        self._surum += 1
        if tur in ("yeni", "devam") and not k.ben:
            if capa:
                self._baslangiclar.append(t)
                self._bas_tur.append(tur)
                self._bas_seg.append((seg, k))
            else:
                self._kaba_baslar.append((t, r_ilk / 2.0, tur, seg))
        if self._seg_sayisi > self.esik["aralik_tavan"]:
            self._tasma_kontrol(t)
        return seg

    def _tasma_kontrol(self, t):
        sinir = t - float(self.esik["budama_sn"])
        top = 0
        for k in self._kisiler.values():
            if k.seg and k.seg[0][1] < sinir:
                k.seg = [s for s in k.seg if s[1] >= sinir]
                k.ilkler = [s[0] for s in k.seg]
            top += len(k.seg)
        self._seg_sayisi = top
        i = bisect.bisect_left(self._baslangiclar, sinir)
        if i:
            del self._baslangiclar[:i]
            del self._bas_tur[:i]
            del self._bas_seg[:i]
        if top > self.esik["aralik_tavan"] and not self._tasma:
            self._tasma = True
            self._log(f"konuşmacı çizelgesi taşması: {top} aralık > tavan {int(self.esik['aralik_tavan'])}; "
                      "yeni satırlar kişinin son aralığına eklenecek", tasma=True)

    def _uzat(self, s, t, kaba=0.0):
        """Satirin araligini t'ye uzatir. kaba > 0: buyume yavas okumada (okuma boslugunda) gozlendi, son bu kadar
        genis araligin sonuna yazildi (gercek son belirsiz: _kaba_payi ve _iki_aday cekirdek bandi bunu dusurur)."""
        seg = s.seg
        if t > seg[1]:
            seg[1] = t
            seg[3] = self.R
            seg[5] = kaba if kaba > 2.0 * self.R + 1e-9 else 0.0
            k = self._kisiler[s.kim]
            k.maks_sure = max(k.maks_sure, seg[1] - seg[0])
            if seg[5] > 0:
                k.kaba_var = True
            self._surum += 1

    @staticmethod
    def _uyumlu(p, kim, kel):
        if p.kim != kim:
            return False
        pw = p.kelimeler
        if not pw or not kel:
            return True
        n = min(len(pw), len(kel))
        a, b = pw[:n + 2], kel[:n + 2]
        if a[:n] == b[:n]:
            return True
        if difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.6:
            return True
        # yeniden yazim + buyume (bosluk sonrasi tek okumada): kelime sayisi artmis, ilk kelimelerin en az yarisi ayni
        return len(kel) > len(pw) and sum(1 for x, y in zip(pw, kel) if x == y) >= 0.5 * len(pw)

    @staticmethod
    def _kuyruk_buyudu(eski, kel, ust):
        """Kelime sayisi artmadan satirin kuyrugu ilerledi mi? Teams uzun satirda basi dusurur: son 2 kelime VE ilk
        kelime degismis (yalniz kuyruk degisimi yeniden yazimdir, buyume sayilmaz)."""
        return (len(kel) >= ust and len(kel) >= 3 and len(eski) >= 3 and kel[-2:] != eski[-2:]
                and kel[0] != eski[0])

    def _hizala(self, onceki, yeni):
        """Yeni okumanin satirlarini oncekilerle esle: paneldeki kaydirma k araniyor (en cok uyum, esitlikte
        en kucuk k). Donus: (eslesen _Satir|None listesi, ortusme boyu n)."""
        if not onceki:
            return [None] * len(yeni), 0
        en_k, en_puan = 0, 0
        for k in range(len(onceki)):
            n = min(len(yeni), len(onceki) - k)
            if n <= en_puan:
                break
            puan = 0
            for i in range(n):
                if self._uyumlu(onceki[k + i], yeni[i][0], yeni[i][1]):
                    puan += 1
            if puan > en_puan:
                en_k, en_puan = k, puan
        if en_puan == 0:
            return [None] * len(yeni), 0
        n = min(len(yeni), len(onceki) - en_k)
        es, yazim = [], 0
        for i in range(len(yeni)):
            p = onceki[en_k + i] if i < n else None
            if p is not None and not self._uyumlu(p, yeni[i][0], yeni[i][1]):
                # hizada baska satirlar eslesti: ayni kisinin ayni konumdaki buyumus satiri kalici yeniden yazimdir
                # (kelimeler tamamen degisse de); yeni satir / taban sayilirsa onceki araligi uzamaz, bosluktaki
                # buyume sonraki konusmaciya kalirdi
                if p.kim == yeni[i][0] and p.kelimeler and len(yeni[i][1]) >= len(p.kelimeler):
                    yazim += 1
                else:
                    p = None
            es.append(p)
        if yazim:
            self._log("satır yeniden yazıldı (hizada, aynı kişi, büyümüş)", satir=yazim)
        return es, n

    def okuma_ekle(self, okuma):
        try:
            bas = float(okuma["bas_epoch"])
            bit = float(okuma.get("bit_epoch") or bas)
        except (KeyError, TypeError, ValueError):
            return
        bit = max(bit, bas)
        if self._son_okuma_bas is not None and bas < self._son_okuma_bas:
            self._log("okuma sırası bozuk, atlandı", okuma_no=okuma.get("okuma_no"))
            return
        grup = bool(okuma.get("grup", True))
        t = (bas + bit) / 2.0
        if self._son_okuma_bas is not None and bas > self._son_okuma_bas:
            self._periyot.append(bas - self._son_okuma_bas)
            self._R_guncelle()
        onceki_bit = self._son_okuma_bit
        d = None if onceki_bit is None else bas - onceki_bit
        if d is not None and d > self._bosluk_esigi():
            self._bosluk_ekle(onceki_bit, bas, "okuma")           # okuyucu takildi: kapsama boslugu
            self._log("okuma boşluğu", sn=round(d, 2))
            if d > self.esik["kisa_bosluk_sn"]:
                self._onceki = None                                # uzun bosluk: satirlar yeniden taban
        if self._ilk_okuma is None:
            self._ilk_okuma = bas
        self._son_okuma_bas = bas
        self._son_okuma_bit = bit if onceki_bit is None else max(onceki_bit, bit)
        self._acik = None
        if not grup:
            if self._grupsuz_bas is None:
                self._grupsuz_bas = onceki_bit if onceki_bit is not None else bas
                self._surum += 1
            self._onceki = None
            self._son_ham = None
            self._donuk_bitir(bas, "grup_yok")
            return
        gozlem_bas = onceki_bit                      # bu okumadan once satirlarin son gozlendigi an
        if self._grupsuz_bas is not None:
            self._bosluk_ekle(self._grupsuz_bas, bas, "grup")
            self._grupsuz_bas = None
        yeni, ham = [], []
        for g in okuma.get("gorunen") or ():
            try:
                kim, metin = g[0], g[1]
            except (IndexError, TypeError, KeyError):
                continue
            kim = (kim or "").strip()
            if not kim:
                continue
            metin = metin or ""
            ham.append((kim, metin))
            yeni.append((kim, _norm(metin).split(), metin))
        ham = tuple(ham)
        degisti = self._son_ham is None or ham != self._son_ham
        self._son_ham = ham
        if degisti:
            if self._donuk:
                gozlem_bas = self._donuk_bas         # donukluk boyunca satirlar gozlenmedi
                self._donuk_bitir(bas, "degisim")
            self._son_degisim = t
            self._surum += 1
        else:
            # yakalayici bayragi yalniz erken tetik: son degisimden sonra >= donuk_ses_sn loopback konusmasi sart
            # (gercek sessizlikte tek halusinasyon klibi paneli donuk saydirmaz)
            if (okuma.get("donuk") and not self._donuk and self._son_degisim is not None
                    and self._lb_sure(self._son_degisim, bit) >= self.esik["donuk_ses_sn"]):
                self._donuk_baslat(bit, "okuma")
            else:
                self._donuk_denetle(bit)
            if self._donuk:
                self._bosluk_ekle(self._donuk_bas, bit, "donuk")
        ipucu = bool(self.esik["metin_ipucu"])
        taban = self._onceki is None
        es, n = self._hizala(self._onceki, yeni)
        if self._onceki is not None and len(self._onceki) >= 2 and len(yeni) >= 2 and n == 0:
            taban = True                             # tam uyumsuzluk: panel yeniden cizildi, sahte "yeni" yok
            self._log("altyazı paneli yeniden çizildi", satir=len(yeni))
        yavas = gozlem_bas is not None and t - gozlem_bas > self._duraklama()
        # satir degisimleri: (indeks, satir|None, kim, kel, metin, buyudu, yeni kelime sayisi)
        degisim = []
        for i, (kim, kel, metin) in enumerate(yeni):
            s = es[i]
            if s is None:
                degisim.append([i, None, kim, kel, metin, bool(kel), len(kel)])
                continue
            buyudu = len(kel) > s.ust or self._kuyruk_buyudu(s.kelimeler, kel, s.ust)
            degisim.append([i, s, kim, kel, metin, buyudu, max(len(kel) - len(s.kelimeler), 2) if buyudu else 0])
        # yavas okumada gozlenmeyen (gozlem_bas, t] araligi, degisen satirlara panel sirasiyla kelime sayisina oranli
        # bolunur (ardisik konusma varsayimi): buyuyen satirin sonu / yeni satirin basi kendi alt araligina duser,
        # bant alt aralik kadar genis. Normal okumada alt aralik = t (bant R).
        araliklar = {}
        if yavas:
            toplam = sum(d[6] for d in degisim if d[5] and (d[1] is None and not (taban or d[0] < n) or d[1] is not None))
            x = gozlem_bas
            for d in degisim:
                if not d[5] or (d[1] is None and (taban or d[0] < n)):
                    continue
                pay = (t - gozlem_bas) * (d[6] / float(toplam)) if toplam else (t - gozlem_bas)
                araliklar[d[0]] = (x, x + pay)
                x += pay
        satirlar = []
        for i, s, kim, kel, metin, buyudu, yeni_kelime in degisim:
            a_i, b_i = araliklar.get(i, (t, t))
            if yavas and i in araliklar:
                ilk, bant, capa = (a_i + b_i) / 2.0, (b_i - a_i) + self.R, (b_i - a_i) <= 2.0 * self.R
            else:
                ilk, bant, capa = t, None, True
            if s is None:
                s = _Satir(kim, kel, t, taban=taban or i < n)
                k = self._kisi(kim)
                k.satir += 1
                if not s.taban and kel:
                    s.seg = self._seg_ac(kim, ilk, "yeni", bant=bant, capa=capa)
                    if ipucu:
                        self._kok_kaydet(s, kim, metin, ilk, kaba=not capa)
                satirlar.append(s)
                continue
            if buyudu:
                s.ust = max(s.ust, len(kel))
                # duraklama yalniz GOZLENEN sureyle olculur: yavas okumada (okuma boslugu) gozlenmeyen aralik
                # duraklama kaniti degildir; bosluk oncesi buyuyen satir boslukta da buyumustur (sahte "devam" /
                # sahte 'b' capasi acilmaz)
                bekleme = (gozlem_bas if i in araliklar else a_i) - s.son_buyume
                kaba_son = (b_i - a_i) if i in araliklar else 0.0
                if s.seg is not None and bekleme <= self._duraklama():
                    self._uzat(s, b_i, kaba=kaba_son)
                else:
                    if s.taban and s.seg is None and bekleme <= self._duraklama():
                        tur = "eski"                    # zaten buyuyordu: baslangic bilinmiyor
                    elif s.seg is None and not s.taban:
                        tur = "yeni"
                    else:
                        tur = "devam"                   # duraklama sonrasi yeniden buyume
                    # bosluk sonrasi 'devam': yeniden buyumenin ani gozlenmedi, 'b' capasi olmaz (kaba bas)
                    s.seg = self._seg_ac(kim, ilk, tur, bant=bant, capa=capa and not (tur == "devam" and bant is not None))
                    if b_i > ilk:
                        self._uzat(s, b_i, kaba=kaba_son)
                s.son_buyume = t
                if ipucu:
                    self._kok_kaydet(s, kim, " ".join(metin.split()[-yeni_kelime:]), ilk, kaba=not capa)
            s.kelimeler = kel
            satirlar.append(s)
        self._onceki = satirlar
        if ipucu:
            self._kok_buda(t)

    # ------------------------------------------------------------------ metin ipucu (ikincil sinyal)

    def _kok_kaydet(self, s, kim, metin, t, kaba=False):
        """Satirda ilk kez gorulen ayirt edici kokleri kaydeder (satir, kisi ve genel dizin). Taban satirin eski
        kelimeleri kaydedilmez (ne zaman belirdigi bilinmiyor). t: kelimelerin belirdigi an (yavas okumada alt
        araligin ortasi; kaba: alt aralik > 2R, gecikme ornegi olmaz)."""
        k = self._kisi(kim)
        for kok in _ayirt_edici(metin):
            if kok in s.kokler:
                continue
            s.kokler[kok] = t
            k.kokler.append((t, kok))
            self._kok_gorulme.setdefault(kok, []).append((t, s.no, kaba))

    def _kok_buda(self, t):
        if self._kok_budama is not None and t - self._kok_budama < 30.0:
            return
        self._kok_budama = t
        sinir = t - float(self.esik["metin_pencere_sn"]) - float(self.esik["capraz_ust_sn"])
        for kok in list(self._kok_gorulme):
            liste = [x for x in self._kok_gorulme[kok] if x[0] >= sinir]
            if liste:
                self._kok_gorulme[kok] = liste
            else:
                del self._kok_gorulme[kok]

    def _metin_capalari(self, an):
        """(1) Icerik capali gecikme: bekleyen klibin >= 2 ayirt edici kelimesi ayni satirda [bas, bit + capraz_ust]
        icinde ilk kez gorulduyse gecikme = gorulme - (bas + kelimenin klip icindeki tahmini yeri) -> "metin" capasi.
        (2) Tutarlilik: eslesmeyen klipler birikir, satirlar degisiyorsa cizelge tutarsiz."""
        if not self._metin_bekleyen:
            return
        ust = float(self.esik["capraz_ust_sn"])
        kalan = []
        for rec in self._metin_bekleyen:
            bas, bit, kokler = rec["bas"], rec["bit"], rec["kokler"]
            sure = max(bit - bas, 0.05)
            satir_es = {}
            for i, kok in enumerate(kokler):
                for tg, sno, kaba in self._kok_gorulme.get(kok, ()):
                    if bas <= tg <= bit + ust:
                        satir_es.setdefault(sno, []).append((tg, i, kaba))
            en = None
            for sno, es in satir_es.items():
                if len(es) >= 2 and (en is None or (len(es), -min(x[0] for x in es)) > (len(en), -min(x[0] for x in en))):
                    en = es
            if en is not None:
                n = len(kokler)
                # klibin en ondeki (klip basina en yakin) eslesen kelimesi: yer tahmini (bas + sure * i / n) klip
                # sonuna dogru sapar (VAD kuyruk sessizligi klibe dahil, kelime hizi degisken); ilk kelime ~sapmasiz
                # kelimenin klip icindeki yeri bas + sure * i / n: klip sonuna dogru kelimeler ~0.1-0.3 sn erken olculur
                # (VAD kuyruk sessizligi klibe dahil, kelime hizi degisken); yukari duzeltmeler (sesli sure, kelime
                # dizini, ilk kelime) L'yi gercege yaklastirsa da karar kapilarini (kirli / belirsiz) bozuyor: karar
                # bantlari bu olcuye kalibre (bkz. tests/e2e/test_panel_e2e.py METIN_L_NEDEN)
                x = _medyan([tg - (bas + sure * i / n) for tg, i, _ in en])
                # kaba (yavas okumada genis alt aralikta beliren) kelimeler gecikme ornegi olmaz: belirme ani belirsiz
                if 0.0 <= x <= ust and not any(kaba for _, _, kaba in en):
                    self._L_ornek_ekle(x, "metin", bas)
                self._son_metin_esleme = an
                self._metin_eslesmeyen = 0
                if self._tutarsiz:
                    self._tutarsiz = False
                    self._log("altyazı metni kliplerle yeniden tutarlı")
                continue
            if an is not None and an > rec["son"]:
                self._metin_eslesmeyen += 1
                continue
            kalan.append(rec)
        self._metin_bekleyen = kalan
        if (not self._tutarsiz and self._metin_eslesmeyen >= 3 and an is not None and self._son_degisim is not None
                and an - self._son_degisim <= self.esik["metin_pencere_sn"]
                and an - (self._son_metin_esleme if self._son_metin_esleme is not None else self._ilk_okuma or an)
                >= self.esik["metin_pencere_sn"]):
            self._tutarsiz = True
            self._log("altyazı metni kliplerle tutarsız: satırlar değişiyor ama kliplerin kelimeleri görünmüyor",
                      klip=self._metin_eslesmeyen)

    def _metin_secimi(self, k, adlar):
        """(3) Esitlik bozucu: klip kokleri ile adaylarin bant icindeki satir koklerinin ortusmesi. Biri >= metin_ortusme
        ve digerinin >= 2 kati ise o ad; yoksa None. Yalniz zaman iki aday verdiginde cagrilir; tavan 0.6."""
        kokler = k.get("kokler")
        if not kokler or len(kokler) < 2 or not self.esik["metin_ipucu"]:
            return None
        kume = set(kokler)
        L, pay = self.L, max(1.0, self.R + self.MAD)
        a, b = k["bas"] + L - pay, k["bit"] + L + pay
        oran = []
        for ad in adlar:
            kisi = self._kisiler.get(ad)
            gor = {kok for t, kok in kisi.kokler if a <= t <= b} if kisi is not None else set()
            oran.append(len(kume & gor) / float(len(kume)))
        en = max(range(len(adlar)), key=lambda i: (oran[i], -i))
        diger = max([o for i, o in enumerate(oran) if i != en] or [0.0])
        if oran[en] >= self.esik["metin_ortusme"] and oran[en] >= 2.0 * diger:
            return adlar[en]
        return None

    # ------------------------------------------------------------------ L tahmini

    def _L_hesapla(self):
        """L / MAD: pencerenin (son L_pencere ornek) medyani. Son L_sure_sn'nin orneklerinin (en az L_pencere/4) medyani
        bundan > max(guven_payi, alt pencerenin MAD'i) sapiyor ve o MAD mad_maks'i asmiyorsa gecikme surukleniyordur:
        L / MAD o alt pencereden (histerezis: seyrek capada 90 sn penceresinin titremesi devir kesim noktasini
        oynatmasin). Egim kestirimi denendi, gurultulu gecikmede L'yi kaydirip yanlis kesinlik urettigi icin
        kullanilmaz."""
        if not self._L_ornek:
            return
        hepsi = list(self._L_ornek)
        L = _medyan(hepsi)
        MAD = _medyan([abs(x - L) for x in hepsi])
        cift = list(zip(self._L_zaman, self._L_ornek))
        son = max((t for t, _ in cift if t is not None), default=None)
        if son is not None:
            sinir = son - float(self.esik["L_sure_sn"])
            secili = [x for t, x in cift if t is None or t >= sinir]
            en_az = max(int(self.esik["capa_min"]), int(self.esik["L_pencere"]) // 4)
            if en_az <= len(secili) < len(hepsi):
                Lp = _medyan(secili)
                MADp = _medyan([abs(x - Lp) for x in secili])
                # az ve daginik ornekle (periyodik bosluk + yanlis capalar) alt pencere L'yi surukleyemez
                if abs(Lp - L) > max(float(self.esik["guven_payi"]), MADp) and MADp <= self.esik["mad_maks"]:
                    L, MAD = Lp, MADp
        self.L, self.MAD = L, MAD

    def _L_ornek_ekle(self, x, tur, t=None):
        # metin capasi ve isinma (capa_min'den az ornek: aday penceresi de capraz_ust) icin ust sinir capraz_ust
        isinma = len(self._L_ornek) < int(self.esik["capa_min"])
        ust = float(self.esik["capraz_ust_sn"]) if (tur == "metin" or isinma) else self._L_ust()
        if x > ust or x < 0:
            self._atilan_ornek += 1
            if tur != "metin":
                self._ardisik_atilan += 1
                if self._ardisik_atilan == int(self.esik["basamak_k"]):
                    self._capraz_zorla = True           # L pencereden sapti: capraz ilinti hemen
            self._log("L örneği atıldı", L_ornek=round(x, 3), capa=tur)
            return
        if tur != "metin":
            self._ardisik_atilan = 0
        L0, MAD0 = self.L, self.MAD
        self._L_ornek.append(x)
        self._L_zaman.append(t)
        self._L_tur.append(tur)
        if self._basamak_kalan > 0:
            self._basamak_kalan -= 1
        n = int(self.esik["basamak_n"])
        if len(self._L_ornek) > n:
            # basamak (Teams gecikmesi sicradi / saat adimi): son n capanin medyani L'den belirgin sapti.
            # Kayan medyan gec doner, MAD iki tepeli dagilimda dusuk kalir: pencere son n capaya kisalir, tavan.
            son = list(zip(self._L_zaman, self._L_ornek, self._L_tur))[-n:]
            esik = max(self.esik["basamak_sn"], 2.0 * MAD0)
            k = int(self.esik["basamak_k"])
            ust_l = [c for c in son if c[1] - L0 >= esik]
            alt = [c for c in son if L0 - c[1] >= esik]
            if len(ust_l) >= k or len(alt) >= k:
                son = ust_l if len(ust_l) >= k else alt      # yeni duzeyin capalari (eski aday secimleri disarida)
                maxlen = int(self.esik["L_pencere"])
                self._L_ornek = deque([c[1] for c in son], maxlen=maxlen)
                self._L_zaman = deque([c[0] for c in son], maxlen=maxlen)
                self._L_tur = deque([c[2] for c in son], maxlen=maxlen)
                self._capa_gecmis = deque(list(self._capa_gecmis)[-n:], maxlen=maxlen)
                self._basamak_kalan = int(self.esik["basamak_tavan_capa"])
                self._son_basamak = _medyan([c[1] for c in son]) - L0
                self._log("L basamağı", L_eski=round(L0, 3), L_yeni=round(_medyan([c[1] for c in son]), 3),
                          MAD=round(MAD0, 3))
        self._L_hesapla()
        self._log("L örneği", L_ornek=round(x, 3), capa=tur, L=round(self.L, 3), MAD=round(self.MAD, 3),
                  R=round(self.R, 3), **({"klip_bas": round(t, 3)} if t is not None else {}))
        if (self._L_onaysiz() and not self._capraz_zorla and tur != "capraz" and self._an is not None
                and (self._capraz_son is None or self._an - self._capraz_son >= float(self.esik["capraz_aralik_sn"]) / 4.0)):
            # capalardan ogrenilen L capraz ilintiyle dogrulanana kadar tavan (+L_onaysiz): isinmada komsu konusmacinin
            # satiri (yakin) ya da sonraki konusmacinin satiri (uzak) capa sanilabilir; en gec yarim aralikta degerlendir
            self._capraz_zorla = True

    def _L_onaysiz(self):
        """Mevcut L dogrulanmadi: capalar tek basina kesinlik vermez (isinmada komsu satirlar capa sanilip yanlis L
        ogrenilebilir). Dogrulama: capraz ilinti (>= capraz_en_az onset) L ile uyumlu (±max(1, 2 MAD)) ya da bagimsiz
        metin capalari (>= capa_min, ±max(0.6, MAD)) zaman capalarini (>= capa_min) destekliyor."""
        onay = self._L_onay
        if onay is not None and abs(self.L - onay) <= max(1.0, 2.0 * self.MAD):
            return False
        n = int(self.esik["capa_min"])
        marj = max(0.6, min(1.0, self.MAD))
        zaman = sum(1 for tr in self._L_tur if tr in ("a", "b"))
        metin = sum(1 for x, tr in zip(self._L_ornek, self._L_tur) if tr == "metin" and abs(x - self.L) <= marj)
        return not (zaman >= n and metin >= n)

    def _L_sapma(self):
        """Son basamak_n capanin medyaninin L'den sapmasi: basamak kesinlesmeden once kesinlik payini genisletir."""
        n = int(self.esik["basamak_n"])
        if len(self._L_ornek) < n:
            return 0.0
        son = list(self._L_ornek)
        sapma = abs(_medyan(son[-n:]) - self.L)
        s2 = [v - self.L for v in son[-2:]]
        if s2[0] * s2[1] > 0:                       # son iki capa ayni yone: basamagin ilk isareti
            sapma = max(sapma, min(abs(s2[0]), abs(s2[1])))
        return sapma

    def saat_capasi(self, akis, fark):
        """Ses servisi saat capasini yeniledi (C1 'capa' olayi): o akisin klip epoch'lari fark kadar ileri sicradi
        (eskiden Windows saati adim atti: okumalar hemen kaydi, ses en gec CAPA_ARALIK_SN sonra; artik zaman kaynagi
        saat.simdi() adimdan etkilenmez, bu yol ses cihazi saati kaymasi icin kalir). Loopback icin aradaki L
        capalari fark kadar sapmistir: L basamagi bu adimdan algilanmissa pencere fark kadar geri kaydirilir, degilse
        sapan capalar atilir; bekleyen capa adaylari (eski saat) dusurulur. Her durumda basamak_tavan_capa capa
        boyunca tavan."""
        try:
            fark = float(fark)
        except (TypeError, ValueError):
            return
        if abs(fark) < self.esik["saat_adim_sn"]:
            return
        if akis == "loopback":
            sb = self._son_basamak
            maxlen = int(self.esik["L_pencere"])
            if sb is not None and abs(sb - fark) <= abs(fark) / 2.0:
                self._L_ornek = deque([v - fark for v in self._L_ornek], maxlen=maxlen)
                self._capa_gecmis = deque([[(x - fark, tr) for x, tr in l] for l in self._capa_gecmis], maxlen=maxlen)
            else:
                yon = 1.0 if fark > 0 else -1.0
                cift = [(t, v, tr) for t, v, tr in zip(self._L_zaman, self._L_ornek, self._L_tur)
                        if (v - self.L) * yon < abs(fark) / 2.0]
                self._L_ornek = deque([v for _, v, _ in cift], maxlen=maxlen)
                self._L_zaman = deque([t for t, _, _ in cift], maxlen=maxlen)
                self._L_tur = deque([tr for _, _, tr in cift], maxlen=maxlen)
            self._son_basamak = None
            self._onsetler = []                 # eski saatle bekleyen capa adaylari
            self._onset_gecmis.clear()
            self._aciklanmayan.clear()
            self._capraz_onceki = None
            self._L_hesapla()
        self._basamak_kalan = int(self.esik["basamak_tavan_capa"])
        self._log("ses saat çapası", akis=akis, fark=round(fark, 3), L=round(self.L, 3), MAD=round(self.MAD, 3))

    def _capalari_coz(self, an, zorla=False):
        """Capa: sessizlik (>= sessizlik_sn) sonrasi loopback klip basi ile sonraki uzak aralik basi (yeni satir:
        capa a; duraklamis satirin yeniden buyumesi: capa b, bosluk >= duraklama_bosluk_sn). Altyazi gecikmesi
        sessizlikten uzunsa "sonraki ilk" aralik bir ONCEKI konusmanin satiri olabilir: klip basindan sonraki
        L_ust = max(L_maks, L + L_pay) (isinmada capraz_ust) icindeki, daha once capa olmamis tum aralik baslari ve kaba
        baslar (bant/2 toleransli; ornek vermez) aday; son L_pencere capanin oy cokluguyla tutarli olan secilir (gecmis
        yokken mevcut L'ye en yakin). L_ust icinde aday yoksa ornek atilir (kirpilmaz). Adaylar birbirinden
        max(0.35, R/2)'den fazla ayrisiyorsa (belirsiz capa) ornek sayilmaz (isinmada oy coklugu >= capa_min liste ayni
        degeri destekliyorsa sayilir): oy coklugu eski L'yi destekledigi icin gecikme sicradiginda dogru aday reddedilip
        L geri cekiliyordu. Klip ile aralik basi arasinda kapsama boslugu olmamali."""
        if not self._onsetler:
            return
        kalan = []
        bl = self._baslangiclar
        capraz_ust = float(self.esik["capraz_ust_sn"])
        capa_min = int(self.esik["capa_min"])
        isinma = len(self._L_ornek) < capa_min
        # isinmada pencere capraz_ust: gecikme L_maks'tan buyukken (7 sn) 5 sn'lik pencere yalniz onceki konusmacinin
        # satirini goruyor ve yanlis L ogreniliyordu; genis pencerede birden cok aday varsa yalniz oy coklugu
        # (>= capa_min liste) destekledigi aday sayilir
        L_ust = max(self._L_ust(), capraz_ust) if isinma else self._L_ust()
        L_maks = float(self.esik["L_maks"])
        tol = max(0.35, self.R / 2.0)
        marj = max(1.0, 2.0 * self.MAD)        # L'den cok erken satir (onceki konusmacinin gec satiri) siniri
        for o, bosluk in self._onsetler:
            okundu = self._son_okuma_bit is not None and self._son_okuma_bit > o + L_ust + self.R
            if not (okundu or zorla):
                kalan.append((o, bosluk))
                continue
            # supheli onset sayimina girer: isinma disinda, sessizlik sonrasi, panel canli (donuk degil) ve onset'in
            # satir penceresi okumalarla kapsanmis (bosluk / donuk panelde satir yoklugu kanit degildir)
            sayilir = (not isinma and bosluk >= self.esik["sessizlik_sn"] and not self._donuk
                       and self._kapsama_var(o, o + L_ust + self.R))
            j = bisect.bisect_left(bl, o)
            adaylar, satir_var = [], False          # aday: (gecikme, tur, tolerans); kaba baslarin toleransi bant/2
            while j < len(bl) and bl[j] - o <= L_ust:
                e, tur = bl[j], self._bas_tur[j]
                satir_var = True
                gerek = self.esik["sessizlik_sn"] if tur == "yeni" else self.esik["duraklama_bosluk_sn"]
                # bir onceki onset'in capasi olmus satir yeniden aday olmaz (gecikme sicrayinca onceki konusmacinin
                # satiri 'daha yakin' diye secilip L eski duzeyde kaliyordu). Onset'in sessizligi bu tur icin
                # yetersizse satir ornek vermez ama onset'i aciklar ("kaba" gibi: komsu satir tek aday sanilmasin)
                if e not in self._capa_satirlar and self._kapsama_var(o - self.R, e):
                    if bosluk < gerek or (isinma and tur == "devam" and e - o > L_maks):
                        # isinmada genis pencerenin uzak (> L_maks) 'devam' basi: ayni kisinin art arda sozlerinde
                        # satirin duraklama sonrasi baslari periyodik (soz + duraklama ~ 9 sn), onset'i aciklar ama
                        # ornek vermez (uzak 'b' capalari birbirini destekleyip yanlis L tohumluyordu)
                        tur = "kaba"
                    adaylar.append((e - o, tur, 0.0))
                j += 1
            # kaba baslar (yavas okumada / okuma boslugunda acilan, ani belirsiz): onset'i aciklar ve adaydir ama ornek
            # vermez; periyodik boslukta kesin basli satir az kalinca onceki konusmacinin satiri tek aday sanilmasin
            for e, y, _, _sg in self._kaba_baslar:
                if o - y <= e <= o + L_ust + y:
                    adaylar.append((e - o, "kaba", y))
            if not adaylar:
                sessiz = bosluk >= self.esik["sessizlik_sn"]
                if sessiz:
                    self._atilan_ornek += 1
                if sayilir and not self._suren_satir_var(o):
                    self._onset_aciklanamadi(an, o, "satir_yok")
                # pencerede hic aralik basi yoksa onset aciklanmamistir: satiri gecikmeyle sonra gelebilir, sonraki
                # capalarin adayi onun olabilir. Isinmada her klip basi, sonra yalniz sessizlik sonrasi onset'ler izlenir
                if satir_var:
                    if sessiz:
                        self._log("L örneği yok (uygun aralık başı yok)", klip_bas=round(o, 3))
                elif sessiz or isinma:
                    self._aciklanmayan.append(o)
                    if sessiz:
                        self._log("L örneği atıldı (L_maks içinde aralık başı yok)", klip_bas=round(o, 3))
                continue
            if o not in self._capa_listede:
                self._capa_gecmis.append([(d, tr) for d, tr, _ in adaylar])
                self._capa_listede.add(o)
                if len(self._capa_listede) > 2000:
                    self._capa_listede = {x for x in self._capa_listede if x >= o - 600.0}
            c, oy = self._capa_oyu(max(0.35, self.R) if isinma else None)
            d, tur, y = min(adaylar, key=lambda x: (max(0.0, abs(x[0] - c) - x[2]), x[0]))
            if tur == "kaba":
                self._log("L örneği yok (kaba aralık başı)", klip_bas=round(o, 3), L_ornek=round(d, 3))
                if sayilir and d + y < self.L - marj and self._onsetsiz_satir(o + d, sayim=False):
                    # kaba bandin ust kenari bile L'den belirgin erken ve satiri mevcut gecikmeyle hicbir onset
                    # aciklamiyor: onceki konusmacinin (gec) satiri olabilir
                    self._onset_aciklanamadi(an, o, "erken_kaba")
                continue
            if isinma and d > L_maks:
                # isinmada genis pencerenin uzak (> L_maks) 'yeni' adayi: konusmaci surerken (kendi satiri buyur, yeni
                # satir acilmaz) pencerede yalniz SONRAKI konusmacinin satiri kalir ve tek aday diye kabul ediliyordu;
                # uzak aday ancak en az bir baska onset'in listesi de ayni degeri (tolerans + jitter) verince sayilir
                tol2 = max(0.35, self.R) + 0.25
                oy2 = sum(1 for l2 in self._capa_gecmis if any(abs(x - d) <= tol2 and t2 != "kaba" for x, t2 in l2))
                if oy2 < 2:
                    if not zorla and (an is None or an - o <= 2.0 * float(self.esik["capraz_aralik_sn"])):
                        kalan.append((o, bosluk))
                    else:
                        self._log("L örneği sayılmadı (ısınmada tek onset'li uzak aday)", klip_bas=round(o, 3),
                                  L_ornek=round(d, 3))
                    continue
            belirsiz = any(max(0.0, abs(x - d) - y2) > tol for x, _, y2 in adaylar)
            if belirsiz and isinma and oy < capa_min:
                # isinmada belirsiz onset bekletilir: sonraki onset'lerin aday listeleri ayni degeri destekleyince
                # (>= capa_min liste) sayilir; 60 sn icinde destek gelmezse dusurulur
                if not zorla and (an is None or an - o <= 2.0 * float(self.esik["capraz_aralik_sn"])):
                    kalan.append((o, bosluk))
                else:
                    self._log("L örneği sayılmadı (ısınmada belirsiz çapa)", klip_bas=round(o, 3),
                              adaylar=[round(x, 3) for x, _, _ in adaylar])
                continue
            if belirsiz and not isinma:
                # diger adaylar baska onset'lerin satiri olarak mevcut gecikmeyle aciklaniyorsa (onceki / sonraki
                # konusmacinin satiri) belirsizlik yok: periyodik okuma boslugunda her onset'in penceresine komsu
                # satirlar dusuyor, hepsi atilinca L ornegi kalmiyordu. Aciklanamayan aday (gecikme sicramasi
                # olabilir) varsa ornek sayilmaz; sonraki capalari belirsizlestirmez (pencerede satir var)
                tol2 = max(0.35, self.R)
                aciklandi = all(x == d or any(o2 != o and abs(o + x - o2 - c) <= tol2 + y2 for o2, _ in self._onset_gecmis)
                                for x, _, y2 in adaylar)
                if not aciklandi:
                    self._log("L örneği sayılmadı (belirsiz çapa)", klip_bas=round(o, 3),
                              adaylar=[round(x, 3) for x, _, _ in adaylar])
                    continue
            # aday satir, araligi aciklanmamis (satiri L_ust icinde bulunmayan) bir onceki onset'in olabilir:
            # gecikme pencereden buyukken onceki konusmacinin satiri bu klibe capa sanilip L yanlis ogreniliyordu
            e = o + d
            if any(o2 < o and e - o2 <= capraz_ust for o2 in self._aciklanmayan):
                self._aciklanmayan.append(o)
                self._log("L örneği sayılmadı (önceki onset açıklanmamış)", klip_bas=round(o, 3), L_ornek=round(d, 3))
                if sayilir and self._onsetsiz_satir(e, sayim=False):
                    self._onset_aciklanamadi(an, o, "onceki_aciklanmamis")
                continue
            if sayilir and d < self.L - marj and self._onsetsiz_satir(e, sayim=False):
                # satir L'den belirgin erken belirdi: bu onset'in satiri olmayabilir (gecikme sicradiysa onceki
                # konusmacinin gec satiri tam bu araliga duser ve 'yeni' capa sanilip sayaci sifirliyordu): aciklanamayan
                # sayilir; suphe altinda ornek de sayilmaz (L'yi kirletmesin), degilse ornek girer (kararsiz gecikmede
                # MAD buyur: tavan)
                self._onset_aciklanamadi(an, o, "erken_satir")
                if self._L_supheli_bas is not None:
                    self._log("L örneği sayılmadı (L'den çok erken satır)", klip_bas=round(o, 3),
                              L_ornek=round(d, 3), L=round(self.L, 3))
                    continue
            elif sayilir and abs(d - self.L) <= marj:
                self._supheli_onset = 0                  # satiri beklenen gecikmede: aciklanamayan dizisi bitti
                if self._supheli_kaynak == "onset":
                    self._tutarli_onset += 1
                    if self._tutarli_onset >= 2:
                        self._L_suphe_bitir(an, "tutarli_capa")
            self._capa_satirlar.append(e)
            self._L_ornek_ekle(d, "a" if tur == "yeni" else "b", o)
        self._onsetler = kalan

    def _suren_satir_var(self, o):
        """Onset'in beklenen satir aninda (o + L) zaten acik olan (o + L - R'den once baslamis, o + L + R'den sonra da
        buyuyen) uzak aralik var mi: ayni konusmacinin surdurmesi (okuma boslugu duraklamayi gizler, yeni bas acilmaz);
        onset o zaman aciklanamayan sayilmaz. Gecikme sicrayinca onceki konusmacinin gec satiri da bunu saglayabilir
        (soz araligi genisse): sayac o onset'i kacirir, sonrakiler yakalar."""
        a, b = o + self.L - self.R, o + self.L + self.R
        for kisi in self._kisiler.values():
            if kisi.ben:
                continue
            i = bisect.bisect_left(kisi.ilkler, a)
            while i > 0:
                i -= 1
                seg = kisi.seg[i]
                if seg[0] < a - 60.0:
                    break
                if seg[0] < a and seg[1] > b:
                    return True
        return False

    def _onset_aciklanamadi(self, an, o, neden):
        """Sessizlik sonrasi onset'in satiri beklenen gecikmede bulunamadi (pencerede satir yok ama sahipsiz satir var /
        onceki onset aciklanmamis / satir L'den cok erken) ve ilgili satiri mevcut gecikmeyle hicbir onset aciklamiyor.
        Art arda supheli_onset_n kez: gecikme sicramis olabilir -> L supheli (kararlarda tavan, motor uyarir) ve
        capraz ilinti hemen (her yeni aciklanamayan onset'te) degerlendirilir."""
        self._supheli_onset += 1
        if an is not None and (self._capraz_son is None or an - self._capraz_son >= 5.0):
            self._capraz_zorla = True               # yakin pencere capraz ilintisi hemen (en sik 5 sn'de bir)
        n = int(self.esik["supheli_onset_n"])
        if self._supheli_onset < n:
            return
        if self._L_supheli_bas is None and an is not None:
            self._L_supheli_bas, self._supheli_kaynak = an, "onset"
            self._tutarli_onset = 0
            self._surum += 1
            self._log("L şüpheli: art arda onset'lerin satırı beklenen gecikmede yok", L=round(self.L, 3),
                      onset=self._supheli_onset, neden=neden, klip_bas=round(o, 3))

    def _capa_oyu(self, tol=None):
        """Gecmis capalarin aday gecikmelerinden en cok capanin destekledigi deger ve destek sayisi (kac aday listesi;
        tolerans max(0.35, R/2), isinmada R: okuma nicemlemesi +-R/2); gecmis yoksa (L, 0). Kaba adaylar (ornek
        vermeyen) oy vermez."""
        if tol is None:
            tol = max(0.35, self.R / 2.0)
        en, en_oy, en_anahtar = self.L, 0, None
        for liste in self._capa_gecmis:
            for c, tr in liste:
                if tr == "kaba":
                    continue
                oy = sum(1 for l2 in self._capa_gecmis if any(abs(x - c) <= tol and t2 != "kaba" for x, t2 in l2))
                anahtar = (-oy, abs(c - self.L), c)
                if en_anahtar is None or anahtar < en_anahtar:
                    en, en_oy, en_anahtar = c, oy, anahtar
        return en, en_oy

    def _capraz_puan(self, onsetler, bl, kaba, tol, ust, an=None):
        """Her gecikme adimi (0..ust, 0.25 sn) icin bir aralik basiyla (ya da kaba basla, bant toleransli) eslesen
        onset sayisi: [(puan, d)]. Kesin basli aralik, onset'in klibi bitene dek (bit + d - pay) surmeli: sirayla
        konusmada soz suresi ~ gecikme olunca onceki konusmacinin satiri sonraki onset'e d ~ 0'da da uyar (takma
        tepe); aralik sonu klip bitisiyle uyusmayan eslesme sayilmaz. Hala buyuyen aralik (son ~2R icinde) ve
        16 kelime kesimiyle ayni kisinin hemen ardindan acilan aralik (sonu kesimde) uyar sayilir."""
        puanlar = []
        R = self.R
        pay = tol + R + 1.0
        d = 0.0
        while d <= ust + 1e-9:
            puan = 0
            for o in onsetler:
                bit = self._onset_bit.get(o)
                j = bisect.bisect_left(bl, o + d - tol)
                uydu = False
                while j < len(bl) and bl[j] <= o + d + tol:
                    if bit is None or self._son_uyar(j, bit + d - pay, an):
                        uydu = True
                        break
                    j += 1
                if uydu:
                    puan += 1
                elif any(abs(o + d - e) <= tol + y and (bit is None or self._seg_surer(sg, bit + d - pay, an))
                         for e, y, sg in kaba):
                    puan += 1
            puanlar.append((puan, d))
            d += 0.25
        return puanlar

    def _seg_surer(self, seg, en_az_son, an):
        return seg[1] >= en_az_son - 1e-9 or (an is not None and seg[1] >= an - 2.0 * self.R - 0.5)

    def _son_uyar(self, j, en_az_son, an):
        """j. kesin basli aralik en az en_az_son'a dek suruyor mu (ya da hala buyuyor / ayni kisinin devam araligi
        sonundan en cok R + 1 sn sonra basliyor: kesim)."""
        if j >= len(self._bas_seg):
            return True
        seg, kisi = self._bas_seg[j]
        son = seg[1]
        if self._seg_surer(seg, en_az_son, an):
            return True                                     # suruyor / hala buyuyor olabilir
        i = bisect.bisect_right(kisi.ilkler, son + 1e-6)
        return i < len(kisi.ilkler) and kisi.ilkler[i] <= son + self.R + 1.0 and kisi.seg[i][1] >= en_az_son - 1e-9

    def _L_suphe_bitir(self, an, neden):
        if self._L_supheli_bas is not None:
            self._log("L şüphesi kalktı", neden=neden, sn=round(an - self._L_supheli_bas, 1), L=round(self.L, 3),
                      kaynak=self._supheli_kaynak)
            self._L_supheli_bas, self._supheli_kaynak = None, None
            self._supheli_onset, self._tutarli_onset = 0, 0
            self._surum += 1

    def _L_capraz(self, an, zorla=False):
        """Sessizlik sonrasi onset'ler ile uzak aralik baslarinin 0..capraz_ust capraz ilintisi (0.25 sn adim, son
        capraz_pencere_sn). Tepe >= capraz_en_az onset ve >= %60 eslesme veriyor, L'den belirgin farkli ve iki ardisik
        degerlendirmede ayni ise L o degere sifirlanir (capa penceresi L_ust = L + L_pay yeniden buyur). Yakin pencere
        (son capraz_yakin_sn): tam pencere eski gecikmeyle doluyken sicrama orada gorunur; yakin tepe L'den farkli ve
        L'nin puanindan >= 2 fazlaysa ayni yol. L'den farkli tepe ilk goruldugunde L supheli (kararlarda tavan; motor
        uyarir) -- sifirlanana ya da uyumlu tepe gelene kadar. Uyumlu tepe L'yi dogrular (_L_onay; L > L_maks iken
        yakin adaylardan yuksek puanli olmali); dogrulanana kadar kararlarda tavan (+L_onaysiz)."""
        if an is None:
            return
        aralik = float(self.esik["capraz_aralik_sn"])
        if not zorla:
            if self._capraz_son is not None and an - self._capraz_son < aralik:
                return
            if self._ilk_okuma is None or an - self._ilk_okuma < aralik:
                return
        if self._L_supheli_bas is not None and an - self._L_supheli_bas > (3.0 if self._supheli_kaynak == "capraz" else 2.0) * aralik:
            self._L_suphe_bitir(an, "dogrulanamadi")      # onset suphesi 2, capraz suphesi 3 aralikta dusurulur
        pencere = float(self.esik["capraz_pencere_sn"])
        yakin = float(self.esik["capraz_yakin_sn"])
        ust = float(self.esik["capraz_ust_sn"])
        sess = self.esik["sessizlik_sn"]
        en_az = int(self.esik["capraz_en_az"])
        onsetler = [o for o, b in self._onset_gecmis if o >= an - pencere and b >= sess]
        en_az_onay = max(3, en_az - 1)              # L ile uyumlu tepe (dogrulama) icin en az onset (sifirlama: en_az)
        if len(onsetler) < en_az_onay:
            return                                  # degerlendirme yapilmadi: yeterli onset gelince yeniden denenir
        self._capraz_son = an
        bl = self._baslangiclar
        tol = max(0.35, self.R / 2.0 + 0.2)         # okuma nicemlemesi (0..R) + gecikme titremesi
        # kaba baslar (yavas okumada / bosluk icinde acilan, bandi genis): kendi bandi icinde eslesir; okuma boslugu
        # periyodikse kesin basli satir az kalir, capraz ilinti yalniz bunlarla gecikmeyi bulabilir
        kaba = [(e, y, sg) for e, y, _, sg in self._kaba_baslar if e >= an - pencere - ust]
        L_maks = float(self.esik["L_maks"])
        marj = max(1.0, 2.0 * self.MAD)             # L'den belirgin farkli tepe (sifirlama / suphe)
        marj_onay = max(0.6, min(1.0, self.MAD))    # L ile uyumlu tepe (dogrulama): daha siki
        uyum, fark = None, None
        supheli = self._L_supheli_bas is not None
        yakin_uyum = None                           # yakin pencerenin L ile uyumu (None: yeterli onset yok)
        for tam, pen in ((True, onsetler), (False, [o for o in onsetler if o >= an - yakin])):
            # yakin pencere az onset'le gurultulu: >= en_az + 2 onset (suphe altinda en_az), >= %70 eslesme ve L'nin
            # eslesmesi <= %40 (suphe altinda %60: onceki konusmacinin gec satirlari L'ye de rastlar) sart
            if len(pen) < (en_az if (tam or supheli) else en_az + 2):
                continue
            puanlar = self._capraz_puan(pen, bl, kaba, tol, ust, an)
            en_puan = max(p for p, _ in puanlar)
            if not tam:
                # yakin pencere L ile uyumlu: L civarinin puani >= %60 ve en yuksek puanin en cok 1 altinda
                Lp = max([p for p, d in puanlar if abs(d - self.L) <= marj] or [0])
                yakin_uyum = Lp >= 0.6 * len(pen) and Lp >= en_puan - 1
            if en_puan < en_az_onay or en_puan < (0.6 if tam else 0.7) * len(pen):
                continue
            # en yuksek puanli platolar (bitisik d'ler; duzenli sirayla konusmada iki ayri plato olabilir: L'ye en
            # yakin plato alinir, iki platonun ortasi degil)
            platolar, run = [], []
            for p, d in puanlar:
                if p == en_puan:
                    run.append(d)
                elif run:
                    platolar.append(run)
                    run = []
            if run:
                platolar.append(run)
            tepe = min((_medyan(r) for r in platolar), key=lambda x: abs(x - self.L))
            L_puan = max([p for p, d in puanlar if abs(d - self.L) <= marj] or [0])
            if abs(tepe - self.L) <= marj:
                if tam and abs(tepe - self.L) <= marj_onay:
                    uyum = (tepe, en_puan, max([p for p, d in puanlar if d <= L_maks] or [0]))
                continue
            # arama sinirina (ust) degen plato gercek tepe degil: genis gecikmede her onset bir aralik basina rastlar
            # (sinir artigi) ya da gecikme ust'ten buyuk; L'den farkli tepe olarak kullanilmaz
            ic = [r for r in platolar if r[-1] < ust - 1e-9]
            if not ic:
                continue
            tepe = min((_medyan(r) for r in ic), key=lambda x: abs(x - self.L))
            if abs(tepe - self.L) <= marj:
                continue
            if en_puan >= max(en_az, L_puan + 2) and (tam or L_puan <= (0.6 if supheli else 0.4) * len(pen)):
                fark = (tepe, en_puan, len(pen), "tam" if tam else "yakin", max(pen))
                break
        if fark is not None:
            tepe, en_puan, n_onset, kaynak, son_o = fark
            ayni = self._capraz_onceki is not None and abs(self._capraz_onceki - tepe) <= 0.5
            # ikinci degerlendirme bagimsiz olmali: ilkinden sonra gelen en az bir onset (zorla degerlendirme ayni
            # veriyi yeniden olcer); motor atama basarisizligi bildirdiyse (L_yeniden_olc) ayni veri yeter
            yeni_veri = (self._capraz_onceki_o is None or son_o > self._capraz_onceki_o + 1e-6
                         or (self._dis_kanit_an is not None and an - self._dis_kanit_an <= aralik))
            if ayni and not yeni_veri:
                return                                  # supheli acik kalir; yeni onset gelince yeniden denenir
            if ayni:
                L0 = self.L
                # capraz ilinti >= capraz_en_az onset'le dogrulandi: L kesinlesmis sayilir (capa_min ornekle tohumlanir;
                # tek ornekle yeniden isinmaya girilip komsu satirlar capa sanilmasin)
                n0 = int(self.esik["capa_min"])
                self._L_ornek = deque([tepe] * n0, maxlen=int(self.esik["L_pencere"]))
                self._L_zaman = deque([an] * n0, maxlen=int(self.esik["L_pencere"]))
                self._L_tur = deque(["capraz"] * n0, maxlen=int(self.esik["L_pencere"]))
                self._capa_gecmis.clear()
                self._capa_listede.clear()
                self._aciklanmayan.clear()
                self.L, self.MAD = tepe, 0.0
                self._ardisik_atilan = 0
                self._basamak_kalan = int(self.esik["basamak_tavan_capa"])
                self._son_basamak = None
                self._capraz_onceki = None
                self._L_onay = tepe
                self._surum += 1
                self._log("L çapraz ilinti", L_eski=round(L0, 3), L_yeni=round(tepe, 3), puan=en_puan,
                          onset=n_onset, pencere=kaynak)
                self._L_suphe_bitir(an, "sifirlandi")
            else:
                self._capraz_onceki, self._capraz_onceki_o = tepe, son_o
                self._capraz_son = an - aralik / 2.0            # L'den farkli tepe: dogrulama yarim aralikta
                if self._L_supheli_bas is None or self._supheli_kaynak != "capraz":
                    # onset suphesi capraz ilintiyle dogrulandi: uyari sayaci (motor ATAMA_GECIKME_SN) buradan baslar
                    self._L_supheli_bas, self._supheli_kaynak = an, "capraz"
                    self._surum += 1
                    self._log("L şüpheli: çapraz ilinti farklı gecikme veriyor", L=round(self.L, 3),
                              tepe=round(tepe, 3), puan=en_puan, onset=n_onset, pencere=kaynak)
            return
        self._capraz_onceki = None
        if uyum is not None:
            tepe, en_puan, yakin_puan = uyum
            # L > L_maks: yakin (<= L_maks) adaylardan daha yuksek puanli olmali (onceki konusmacinin satiri da eslesir)
            if self.L <= L_maks or en_puan > yakin_puan:
                if self._L_onaysiz():
                    self._log("L çapraz ilinti doğruladı", L=round(self.L, 3), tepe=round(tepe, 3), puan=en_puan,
                              onset=len(onsetler))
                    self._surum += 1
                self._L_onay = tepe
            # onset kaynakli suphe: tam pencere eski gecikmeyle dolu olabilir, yakin pencere de (yeterli onset varsa)
            # L ile uyumlu olmali; onset gelene kadar suphe surer (en cok 3 aralik)
            if self._supheli_kaynak != "onset" or yakin_uyum:
                self._L_suphe_bitir(an, "uyumlu")

    def L_yeniden_olc(self):
        """Atama basarisizliginda (motor B5): capraz ilinti hemen degerlendirilir; bir sonraki kontrol'de de."""
        self._capraz_zorla = True
        self._dis_kanit_an = self._an
        self._log("L yeniden ölçülüyor", L=round(self.L, 3), MAD=round(self.MAD, 3))
        if self._an is not None:
            self._L_capraz(self._an, zorla=True)

    # ------------------------------------------------------------------ klipler

    def kulaklik_ayarla(self, durum):
        self.kulaklik = durum if durum in (True, False) else None

    def klip_ekle(self, klip):
        tur = klip.get("tur", "klip")
        if tur == "kulaklik":
            self.kulaklik_ayarla(klip.get("durum"))
            return
        if tur == "capa":
            self.saat_capasi(klip.get("akis"), klip.get("fark"))
            return
        if tur != "klip":
            return
        try:
            no = int(klip["klip_no"])
            bas = float(klip["bas"])
            bit = float(klip["bit"])
        except (KeyError, TypeError, ValueError):
            self._log("geçersiz klip olayı atlandı")
            return
        bit = max(bit, bas)
        akis = klip.get("akis") or "loopback"
        ust = klip.get("ust_klip")
        derinlik = 0
        if ust is not None:
            derinlik = self._bol.get(ust, {}).get("_derinlik", 0) + 1
        kokler, kelime = [], None
        if isinstance(klip.get("text"), str):
            kelime = len(klip["text"].split())
            if self.esik["metin_ipucu"] and klip["text"].strip():
                gor = set()
                for w in _KELIME.findall(klip["text"]):          # klip icindeki sira (konusma yeri tahmini icin)
                    for kok in _ayirt_edici(w):
                        if kok not in gor:
                            gor.add(kok)
                            kokler.append(kok)
        self._bekleyen[no] = {"klip_no": no, "akis": akis, "bas": bas, "bit": bit, "ust_klip": ust,
                              "_derinlik": derinlik, "kokler": kokler, "kelime": kelime}
        if akis == "loopback" and ust is None:
            bosluk = float("inf") if self._son_lb_bit is None else bas - self._son_lb_bit
            # isinmada (capa_min'den az ornek) her klip basi izlenir: satiri bulunmayan klip sonraki capalari belirsizlestirir
            if (bosluk >= min(self.esik["sessizlik_sn"], self.esik["duraklama_bosluk_sn"])
                    or len(self._L_ornek) < self.esik["capa_min"]):
                self._onsetler.append((bas, bosluk))
                self._onset_gecmis.append((bas, bosluk))
                self._onset_bit[bas] = bit
                if len(self._onset_bit) > 500:
                    self._onset_bit = {o: b for o, b in self._onset_bit.items() if o >= bas - 600.0}
                self._capraz_yeni_onset = True
            self._son_lb_bit = bit if self._son_lb_bit is None else max(self._son_lb_bit, bit)
            self._lb.append((bas, bit))
        if akis == "loopback":
            self._lb_hepsi.append((bas, bit))
            if ust is not None and bas in self._onset_bit and bit < self._onset_bit[bas]:
                self._onset_bit[bas] = bit       # yeniden kesilen klibin ilk parcasi: onset'in sozu kesimde biter
            if len(kokler) >= 2:
                self._metin_bekleyen.append({"no": no, "bas": bas, "bit": bit, "kokler": kokler,
                                             "son": bit + float(self.esik["capraz_ust_sn"]) + 2.0 * self.R})

    def bekleyen(self, bas=None, bit=None):
        n = 0
        for k in self._bekleyen.values():
            if (bit is None or k["bas"] < bit) and (bas is None or k["bit"] > bas):
                n += 1
        return n

    def _parcalar(self, kisi, x0, x1, bant=True, ek=0.0, cekirdek=False, segler=False):
        """Kisinin konusma zamanina kaydirilmis (L) parcalari; bant: iki uca R/2 (+ ek, eksi ise daraltir).
        [x0, x1] ile kesisenler. cekirdek: kaba (yavas okumada / boslukta acilan ya da uzayan) bantlarin yalniz kesin
        kismi: bas en gec olasi baslangic (genis bandin sonu), son en erken olasi bitis (kaba_son kadar geri)."""
        L = self.L
        pay = 2.5 + abs(ek) + kisi.bant_maks
        i = bisect.bisect_left(kisi.ilkler, x1 + L + pay)
        alt = x0 + L - pay - kisi.maks_sure
        out = []
        while i > 0:
            i -= 1
            seg = kisi.seg[i]
            if seg[0] < alt:
                break
            if cekirdek:
                a = seg[0] - L + seg[2] / 2.0 - self.R
                b = seg[1] - L - seg[5] + seg[3] / 2.0
            elif bant:
                a, b = seg[0] - L - seg[2] / 2.0 - ek, seg[1] - L + seg[3] / 2.0 + ek
            else:
                a, b = seg[0] - L, seg[1] - L
            if a <= x1 and b >= x0 and b >= a:
                out.append((a, b, seg) if segler else (a, b))
        out.reverse()
        return out

    def _pay(self, kisi, bas, bit, ek=0.0):
        return min(1.0, _birlesim(self._parcalar(kisi, bas, bit, ek=ek), bas, bit) / max(bit - bas, 0.05))

    def _kaba_payi(self, kisi, bas, bit, sure):
        """Klibin [bas, bit], kisinin kaba bantlarina dusen payi (0-1): yavas okumada (okuma boslugunda) acilan aralik
        basi (bant > 2R, satirin belirdigi an gozlenmemis aralikta) ve boslukta uzayan aralik sonu (kaba_son: buyume
        gozlenmedi, son araligin herhangi bir yerinde olabilir; bant onceki konusmacinin sozunu sonraki klibe tasir)."""
        L, R = self.L, self.R
        if not kisi.kaba_var:
            return 0.0
        parcalar = []
        i = bisect.bisect_left(kisi.ilkler, bit + L + kisi.bant_maks + 1.0)
        alt = bas + L - kisi.bant_maks - 1.0 - kisi.maks_sure
        while i > 0:
            i -= 1
            seg = kisi.seg[i]
            if seg[0] < alt:
                break
            if seg[2] > 2.0 * R + 1e-9:
                parcalar.append((seg[0] - L - seg[2] / 2.0, seg[0] - L + seg[2] / 2.0))
            if seg[5] > 0 and bas >= seg[1] - L - seg[5] - R / 2.0:
                # klip, araligin kesin sonundan (bosluk basi) sonra basliyor: bandin boslukta uzayan kuyrugundadir
                # (araligin kendi sozune ait klip kesin kismiyla ortusur, sayilmaz)
                parcalar.append((seg[1] - L - seg[5], seg[1] - L + seg[3] / 2.0))
        return _birlesim(parcalar, bas, bit) / sure if parcalar else 0.0

    def _hazirlik(self, k, an):
        """None: bekle; aksi (tamam: bool) -> karar ver. Klip penceresinin > bosluk_pay'i bosluksa hemen (kapsama yok);
        yoksa okumalar bit + L + R'yi gecip panel klibin altyazi zamanindan sonra en az bir kez degisince; en gec
        son tarih (bit + erteleme_sn)."""
        L, R = self.L, self.R
        son_tarih = an is not None and an >= k["bit"] + self.esik["erteleme_sn"]
        if k["akis"] == "mikrofon" and self.kulaklik is not True and not son_tarih:
            # yanki karari loopback sesine bakar: klibin araligindaki loopback klipleri gelmeden karar yok
            if self._son_lb_bit is None or self._son_lb_bit < k["bit"] - self.esik["lb_tolerans_sn"]:
                if an is None or an < k["bit"] + self.esik["lb_bekle_sn"]:
                    return None
        x0, x1 = k["bas"] + L - R, k["bit"] + L + R
        if self._ilk_okuma is not None:
            pay, _ = self._bosluk_payi(x0, x1)
            if pay > self.esik["bosluk_pay"]:
                return True
        bekle = x1
        kisa = k["akis"] == "loopback" and k["bit"] - k["bas"] < self.esik["kisa_klip_bekle_sn"]
        if kisa:
            bekle += float(self.esik["gec_satir_sn"])      # kisa soz: gec beliren satiri (kendi satiri) gorebilmek icin
        if self._son_okuma_bit is not None and self._son_okuma_bit >= bekle:
            if self._degisti_sonra(k["bas"] + L):
                if (kisa and not son_tarih and an is not None and an < k["bit"] + self.esik["lb_bekle_sn"]
                        and self._gec_bekle(k)):
                    return None      # ardindaki satiri aciklayacak klip henuz gelmedi (en cok bit + lb_bekle_sn)
                return True
        if son_tarih:
            return False
        return None

    def _karar(self, k, speaker, guven, neden, adaylar=(), kapsama=True, devir=False, cakisma=False, at=False,
               bol=None, kimlik=None):
        return {"klip_no": k["klip_no"], "akis": k["akis"], "speaker": speaker,
                "spk_guven": round(max(0.0, min(1.0, guven)), 3),
                "adaylar": [[a, round(p, 3)] for a, p in list(adaylar)[:3]],
                "devir": devir, "cakisma": cakisma, "at": at, "bol": bol, "neden": neden,
                "kapsama": kapsama, "kimlik_turu": kimlik, "cizelge_canli": bool(self._canli)}

    def _gorunen_ad(self, ad):
        return "?" if self.kimlik_turu(ad) == "hayalet" else ad

    def _karar_ver(self, k, tamam):
        bas, bit = k["bas"], k["bit"]
        sure = max(bit - bas, 0.05)
        L, R = self.L, self.R
        pay, btur = self._bosluk_payi(bas + L - R, bit + L + R)
        kapsama = pay <= 0.0
        # cizelge canli: kapsama tam, panel donuk degil, panel klibin altyazi zamanindan sonra degisti (ya da son
        # degisimden beri hoparlorde konusma yok: sessizlikte degismeyen panel canlidir)
        sessiz = self._son_degisim is not None and self._lb_sure(self._son_degisim, bit) < self.esik["donuk_ses_sn"]
        self._canli = bool(kapsama and not self._donuk and (self._degisti_sonra(bas + L) or sessiz))
        tavan, tv_neden = 1.0, []
        if len(self._L_ornek) < self.esik["capa_min"] or not any(x != "metin" for x in self._L_tur):
            # isinma: en az capa_min ornek ve en az bir ZAMAN capasi (a / b / capraz); yalniz metin capalari
            # (ikincil sinyal, yavas okumada sapmali) tavani kaldirmaz
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["capa_az"]
        elif self._basamak_kalan > 0:
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["L_basamak"]
        elif self._ardisik_atilan >= int(self.esik["basamak_k"]) or self._L_supheli_bas is not None:
            # art arda basamak_k zaman capasi L_ust disina dustu / capraz ilinti L'den farkli gecikme gordu (gecikme
            # sicradi: capraz ilinti duzeltene kadar): L supheli
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["L_supheli"]
        elif self._L_onaysiz():
            # capalardan ogrenilen L capraz ilintiyle (>= capraz_en_az onset) henuz dogrulanmadi
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["L_onaysiz"]
        if self.MAD > self.esik["mad_maks"]:
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["mad"]
        if not tamam:
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["eksik"]
        if sure < self.esik["kisa_klip_sn"]:
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["kisa"]
        elif sure < self.esik["kisa_soz_sn"] and len(self._L_ornek) < self.esik["kisa_capa_min"]:
            # kisa sozde L hatasi (az capa, +-0.4 sn) payin tamamini degistirir: daha cok capaya kadar tavan
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["kisa_capa_az"]
        if pay > 0.0:
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["bosluk"]
        ek = ("+" + "+".join(tv_neden)) if tv_neden else ""
        yok = pay > self.esik["bosluk_pay"]
        if k["akis"] == "mikrofon":
            return self._mik_karar(k, sure, not yok, tavan, ek, btur)
        if yok:
            return self._karar(k, "?", 0.0, "panel_donuk" if btur == "donuk" else "kapsama_yok", kapsama=False)
        adaylar = []
        for ad, kisi in self._kisiler.items():
            if kisi.ben or not kisi.seg:
                continue
            p = _birlesim(self._parcalar(kisi, bas, bit), bas, bit) / sure
            if p > 0:
                adaylar.append((ad, min(1.0, p)))
        adaylar.sort(key=lambda x: (-x[1], x[0]))
        gor = [(self._gorunen_ad(a), p) for a, p in adaylar]
        p1 = adaylar[0][1] if adaylar else 0.0
        p2 = adaylar[1][1] if len(adaylar) > 1 else 0.0
        ikinci, baskin = self.esik["ikinci"], self.esik["baskin"]
        if p1 < ikinci:
            ad = self._yapiskan(k, sure)
            if ad is not None:
                tur = self.kimlik_turu(ad)
                g = min(self.esik["yapiskan_guven"], tavan, self.esik["kesin"] - 0.01)
                self._log("yapışkan", klip_no=k["klip_no"], p1=round(p1, 3))
                return self._karar(k, ad, g, "yapiskan" + ek, gor, kimlik=tur)
            return self._karar(k, "?", 0.0, "kimse", gor, kapsama=kapsama)
        if p2 < ikinci:
            ad = adaylar[0][0]
            tur = self.kimlik_turu(ad)
            if tur == "hayalet":
                return self._karar(k, "?", 0.0, "hayalet", gor, kapsama=kapsama, kimlik=tur)
            if p1 >= baskin:
                # kesinlik payi: tek satirlik kisa sozde bant sozun sonunu kapsamaz, sonraki konusmacinin bandi
                # L sapmasiyla klibe tasabilir. spk_guven'deki "ikinci" diger adaylarin bandi w kadar
                # genisletilerek olculur (kategori plan bandiyla secilir, yalniz guven duser).
                # w: zamanlama belirsizligi. Kisa klipte (< kisa_soz_sn) okuma nicemlemesi (0..R) belirleyici: w >= R/2
                w = max(float(self.esik["guven_payi"]), self.MAD, self._L_sapma())
                if sure < self.esik["kisa_soz_sn"]:
                    w = max(w, self.R / 2.0)
                p2g = p2
                for ad2, kisi2 in self._kisiler.items():
                    if ad2 != ad and not kisi2.ben and kisi2.seg:
                        p2g = max(p2g, self._pay(kisi2, bas, bit, w))
                g, neden = p1 - p2g, "baskin"
                kp = self._kaba_payi(self._kisiler[ad], bas, bit, sure)
                if kp >= self.esik["bosluk_pay"]:     # klibin bu payi kaba (bosluk) bandina dusuyorsa tavan (bosluk gibi)
                    # klibin bu payi kisinin yavas okumada (okuma boslugunda) acilmis genis baslangic bandina dusuyor:
                    # satirin ne zaman belirdigi bilinmiyor, bant onceki konusmacinin sozunu de kaplayabilir
                    g, neden = min(g, self.esik["tavan"]), neden + "+kaba"
                gec_kim = self._gec_satir_var(k, ad) if sure < self.esik["kisa_klip_bekle_sn"] else None
                if gec_kim is not None:
                    # kisa klip: hemen ardinda onset'siz (gec beliren) baska bir kisinin satiri acildi; bu klibin
                    # satiri olabilir, tek aday onceki konusmacinin uzayan bandidir. Metin esitlik bozucu: klip metni
                    # gec satirla ortusuyorsa o kisi ("Ad?"), tek adayla ortusuyorsa karar kalir, yoksa tavan (+gec)
                    sec = self._metin_secimi(k, [ad, gec_kim])
                    if sec == gec_kim and self.kimlik_turu(sec) != "hayalet":
                        return self._karar(k, sec, min(g, self.esik["tavan"], tavan), "gec_satir+metin" + ek, gor,
                                           kapsama=kapsama, kimlik=self.kimlik_turu(sec))
                    if sec != ad:
                        g, neden = min(g, self.esik["tavan"]), neden + "+gec"
                kelime = k.get("kelime")
                if self.esik["metin_ipucu"] and kelime and sure >= 1.5 and kelime / sure < 0.75:
                    # klip suresine gore cok az kelime (< 0.75 kelime/sn; konusma ~2): metin klibin kucuk bir
                    # kismindan (ust uste binmede duyulan taraf, kesim artigi); kimin sozu oldugu zamanla kesinlesmez
                    g, neden = min(g, self.esik["tavan"]), neden + "+seyrek"
            else:
                g, neden = p1, "zayif"
                if len(adaylar) > 1:
                    sec = self._metin_secimi(k, [adaylar[0][0], adaylar[1][0]])
                    if sec is not None and sec != ad:
                        tur2 = self.kimlik_turu(sec)
                        if tur2 != "hayalet":
                            ad, tur, neden = sec, tur2, "zayif+metin"
                            g = min(g, self.esik["tavan"])
            if tur in ("oda", "genel"):
                g, neden = min(g, self.esik["tavan"]), neden + "+" + tur
            return self._karar(k, ad, min(g, tavan), neden + ek, gor, kapsama=kapsama, kimlik=tur)
        return self._iki_aday(k, adaylar[0][0], adaylar[1][0], gor, tavan, kapsama)

    def _gec_satir_var(self, k, ad):
        """Kisa klip: klip bitisinden sonra [bit + L - R, bit + L + gec_satir_sn + R] icinde ad disinda bir kisinin
        'yeni' satiri acilmis ve o ana denk beliren satirlar o anin loopback klip baslarindan (o + L) fazlaysa biri
        onset'siz (gec beliren) satirdir: bu klibin olabilir (satiri gec gelen kisa soz onceki konusmacinin bandina
        dusuyordu)."""
        L, R = self.L, self.R
        a, b = k["bit"] + L - R, k["bit"] + L + float(self.esik["gec_satir_sn"]) + R
        for ilk, kim in self._yeni_baslar(a, b):
            if kim != ad and self._onsetsiz_satir(ilk):
                return kim
        return None

    def _tasan_bant(self, seg, bas, tol):
        """Bant klip basindan (bas) en az tol once basliyor ve bu arada biten bir loopback klibi var (bant klipler arasi
        sessizligi asip bu klibe tasiyor)."""
        L = self.L
        a = seg[0] - L - seg[2] / 2.0
        if a >= bas - tol:
            return False
        return any(a <= p[1] <= bas - 0.3 for p in self._lb)

    def _gec_bekle(self, k):
        """Kisa klibin ardindaki pencerede (klip bitisinden sonra baslayan) baska bir kisinin 'yeni' satiri var ve onu
        aciklayabilecek loopback klibi (satirin onset ani son bilinen klip bitisinden sonra) henuz gelmemis olabilir:
        gec satir sayimi (_onsetsiz_satir) icin bekle (en gec son tarih)."""
        L, R = self.L, self.R
        a, b = k["bit"] + L - R, k["bit"] + L + float(self.esik["gec_satir_sn"]) + R
        tol = max(1.5, R + self.MAD + 0.5)
        son = self._son_lb_bit if self._son_lb_bit is not None else float("-inf")
        for ilk, _kim in self._yeni_baslar(a, b):
            if ilk - L > k["bit"] and ilk - L + tol > son:
                return True
        return False

    def _yeni_baslar(self, a, b):
        """Uzak kisilerin [a, b] icinde baslayan 'yeni' araliklari: [(ilk, kim)]."""
        out = []
        for kim, kisi in self._kisiler.items():
            if kisi.ben:
                continue
            i = bisect.bisect_left(kisi.ilkler, a)
            while i < len(kisi.ilkler) and kisi.ilkler[i] <= b:
                if kisi.seg[i][4] == "yeni":
                    out.append((kisi.ilkler[i], kim))
                i += 1
        return out

    def _onsetsiz_satir(self, ilk, sayim=True):
        """ilk aninda beliren 'yeni' satir onset'siz mi: o ana (ilk - L, +-tol) denk gelen loopback klip basi yok
        (sayim=False; gec beliren satir: bandi gercek sozunden sonraya kaymis) ya da o anda beliren satirlar o anin klip
        baslarindan fazla (sayim=True; biri gec beliren satir ya da ayni klibe birlesmis hizli devir). O(kisi)."""
        L = self.L
        tol = max(1.5, self.R + self.MAD + 0.5)       # gecikme titremesi + okuma nicemlemesi + VAD basi
        n_on = sum(1 for p in self._lb_hepsi if abs(p[0] + L - ilk) <= tol)
        if not sayim:
            return n_on == 0
        return len(self._yeni_baslar(ilk - tol, ilk + tol)) > n_on

    def _yapiskan(self, k, sure):
        """Yapiskan konusmaci: cizelge canli, panel donuk degil, kisa loopback klibi ve yalniz BIR kisinin bandi klip
        basindan en cok yapiskan_sn once (ya da klip icinde) bitmis, baska kimsenin bandi yakinda yok, kullanicinin
        altyazisi klibi kapsamiyor (yanki degil) -> o kisi ("Ad?", tavan 0.6)."""
        if (not self._canli or self._donuk or sure > self.esik["yapiskan_klip_sn"]
                or k.get("ust_klip") is not None):
            return None
        bas, bit = k["bas"], k["bit"]
        ys = float(self.esik["yapiskan_sn"])
        aday = None
        for ad, kisi in self._kisiler.items():
            if not kisi.seg:
                continue
            if kisi.ben:
                if self._pay(kisi, bas, bit) >= self.esik["ikinci"]:
                    return None                       # kullanicinin altyazisi: loopback klibi yanki olabilir
                continue
            bantlar = self._parcalar(kisi, bas - ys, bit + ys)
            if not bantlar:
                continue
            a0, b0 = bantlar[-1]
            if len(bantlar) == 1 and a0 <= bas and bas - ys <= b0 <= bit:
                if aday is not None:
                    return None                       # iki kisi yakinda bitirdi: belirsiz
                aday = ad
            else:
                return None                           # baska birinin bandi klibe yakin
        if aday is None or self.kimlik_turu(aday) == "hayalet":
            return None
        return aday

    def _iki_aday(self, k, a, b, gor, tavan, kapsama=True):
        bas, bit = k["bas"], k["bit"]
        R = self.R
        ka, kb = self._kisiler[a], self._kisiler[b]
        ba, bb = self._parcalar(ka, bas, bit), self._parcalar(kb, bas, bit)
        # cakisma devir siniri basina olculur: A->B->A klibinde iki sinirin bant ortusmeleri toplanmaz
        ortak = _en_buyuk_kesisim(ba, bb, bas, bit)
        tol = R + self.esik["devir_pay"] + self.MAD
        ga, gb = self._gorunen_ad(a), self._gorunen_ad(b)
        g = min(self.esik["bolunemedi_guven"], tavan)
        if ortak > tol:
            # gercek ust uste binme: iki bandin KESIN (cekirdek) kismi da klibi kapsiyor; metin esitlik bozucu uygulanmaz
            # ("A / B", cakisma=True kalir: notta ⟨⟩ korumasi; metin yalniz devir / zayif payda ikincil ipucu)
            ca3 = self._parcalar(ka, bas, bit, cekirdek=True, segler=True)
            cb3 = self._parcalar(kb, bas, bit, cekirdek=True, segler=True)
            ca, cb = [(x, y) for x, y, _ in ca3], [(x, y) for x, y, _ in cb3]
            en, sa_, sb_ = 0.0, None, None
            for x1, y1, s1 in ca3:
                for x2, y2, s2 in cb3:
                    o = min(y1, y2, bit) - max(x1, x2, bas)
                    if o > en:
                        en, sa_, sb_ = o, s1, s2
            # klipten once baslayip araya giren loopback sessizligini (onceki klibin bitisini) asarak bu klibe tasan bant:
            # gec beliren satir (bandi gercek sozunden sonraya kaymis) -- ayri klipler arasinda sessizlik vardir, gercek
            # ust uste binme ayni klipte kalir -> kesin ortusme sayilmaz
            tasan = any(self._tasan_bant(sg, bas, tol) for sg in (sa_, sb_) if sg is not None)
            if en > tol and not tasan:
                ad = "?" if ga == "?" and gb == "?" else f"{ga} / {gb}"
                return self._karar(k, ad, g, "cakisma", gor, kapsama=kapsama, cakisma=True)
            # ortusme yalniz kaba (okuma boslugunda acilan / uzayan, ani gozlenmemis) banttan: sahte "ayni anda"
            # (yeniden cizim / gec satir / bosluk sonrasi bant onceki konusmacinin sozunu tasir). Metin esitlik bozucu,
            # yoksa cekirdegi klibi kapsayan tek kisi "Ad?" (tavan); ikisi de degilse sirali devir mantigi
            sec = self._metin_secimi(k, [a, b])
            if sec is not None and self.kimlik_turu(sec) != "hayalet":
                return self._karar(k, sec, min(g, self.esik["tavan"]), "cakisma+metin", gor, kapsama=kapsama,
                                   kimlik=self.kimlik_turu(sec))
            sure = max(bit - bas, 0.05)
            pa, pb = _birlesim(ca, bas, bit) / sure, _birlesim(cb, bas, bit) / sure
            baskin, ikinci = self.esik["baskin"], self.esik["ikinci"]
            for ad, p, p2 in ((a, pa, pb), (b, pb, pa)):
                if p >= baskin and p2 < ikinci and self.kimlik_turu(ad) != "hayalet":
                    return self._karar(k, ad, min(g, self.esik["tavan"]), "cakisma+kaba", gor, kapsama=kapsama,
                                       kimlik=self.kimlik_turu(ad))
        # sirali devir: once baslayan ilk
        sa = min(max(x, bas) for x, _ in ba)
        sb = min(max(x, bas) for x, _ in bb)
        if (sb, b) < (sa, a):
            a, b, ga, gb, ka, kb = b, a, gb, ga, kb, ka
        hb = [p for p in self._parcalar(kb, bas - R / 2.0, bit + R / 2.0, bant=False)]
        s2 = min(x for x, _ in hb) if hb else min(max(x, bas) for x, _ in self._parcalar(kb, bas, bit)) + R / 2.0
        ha = [y for x, y in self._parcalar(ka, bas - R / 2.0, bit + R / 2.0, bant=False) if x <= s2]
        e1 = max(ha) if ha else s2
        # kesim: B'nin basi (ilk - L; L baslardan ogrenildigi icin yansiz) A'nin sonundan (son - L: parcali gosterimde
        # ~R/2 erken) guvenilir: 1:3 agirlik (olculen medyan sapma -0.3 -> ~0)
        an = (min(e1, s2 + tol) + 3.0 * s2) / 4.0
        self._log("devir kesim", klip_no=k["klip_no"], e1=round(e1, 3), s2=round(s2, 3), an=round(an, 3))
        ad = "?" if ga == "?" and gb == "?" else f"{ga} → {gb}"
        bolunebilir = bas < an < bit and k["_derinlik"] < self.esik["derinlik_maks"]
        if not bolunebilir:
            sec = self._metin_secimi(k, [a, b])
            if sec is not None and self.kimlik_turu(sec) != "hayalet":
                return self._karar(k, sec, min(g, self.esik["tavan"]), "devir_bolunemedi+metin", gor,
                                   kapsama=kapsama, kimlik=self.kimlik_turu(sec))
            return self._karar(k, ad, g, "devir_bolunemedi", gor, kapsama=kapsama, devir=True)
        karar = self._karar(k, ad, g, "devir", gor, kapsama=kapsama, devir=True, bol=round(an, 3))
        self._bol[k["klip_no"]] = {"karar": dict(karar), "_derinlik": k["_derinlik"], "adlar": (a, b), "k": k}
        while len(self._bol) > 500:
            self._bol.popitem(last=False)
        return karar

    def _mik_karar(self, k, sure, kapsama, tavan, ek, btur=None):
        """Mikrofon klibi. Yanki (at=True) YALNIZ kulaklik True degilken ve o anda loopback'te ses varken
        (loopback klipleriyle ortusme >= yanki_ses_pay): altyazi bandi tek basina klibi sildirmez (L oturmamis,
        ben adi Teams yazimiyla eslesmemis ya da kullanicinin kendi altyazisi uzak kisi sanilmis olabilir)."""
        bas, bit = k["bas"], k["bit"]
        ben_ad = self.ben or "Ben"
        zayif = min(self.esik["ben_zayif"], tavan)
        lb = _birlesim([p for p in self._lb if p[1] > bas and p[0] < bit], bas, bit) / sure
        yanki_olasi = self.kulaklik is not True and lb >= self.esik["yanki_ses_pay"]
        if not kapsama:
            if yanki_olasi:
                return self._karar(k, "?", 0.0, "yanki_ses", kapsama=False, at=True)
            return self._karar(k, ben_ad, zayif, "ben_panel_donuk" if btur == "donuk" else "ben_kapsama_yok",
                               kapsama=False, kimlik="ben")
        ben_p, ben_kesin_p, uzak = [], [], []
        for ad, kisi in self._kisiler.items():
            if not kisi.seg:
                continue
            pr = self._parcalar(kisi, bas, bit)
            if kisi.ben:
                ben_p.extend(pr)
                if kisi.ben >= 2:
                    ben_kesin_p.extend(pr)
            else:
                p = _birlesim(pr, bas, bit) / sure
                if p > 0:
                    uzak.append((self._gorunen_ad(ad), min(1.0, p)))
        uzak.sort(key=lambda x: (-x[1], x[0]))
        bp = min(1.0, _birlesim(ben_p, bas, bit) / sure)
        bp_kesin = min(1.0, _birlesim(ben_kesin_p, bas, bit) / sure)
        gor = [(ben_ad, bp)] + uzak[:2]
        up = uzak[0][1] if uzak else 0.0
        ikinci, ben_kapsama = self.esik["ikinci"], self.esik["ben_kapsama"]
        # ben altyazisi kesin: zayif ad eslesmesi (yalniz ilk ad) ya da loopback'te ayni anda ses varken tavanli
        kesin = min(self.esik["ben_guven"], tavan) if bp_kesin >= ben_kapsama else zayif
        if yanki_olasi:
            if bp >= self.esik["ben_net"] and up < ikinci:
                return self._karar(k, ben_ad, kesin, "ben_altyazi" + ek, gor, kimlik="ben")
            if up >= ikinci or bp < ben_kapsama:
                return self._karar(k, "?", 0.0, "yanki", gor, at=True)
            return self._karar(k, ben_ad, zayif, "ben_yanki_suphe" + ek, gor, kimlik="ben")
        if bp >= ben_kapsama:
            return self._karar(k, ben_ad, kesin, "ben_altyazi" + ek, gor, kimlik="ben")
        if up >= ikinci:
            return self._karar(k, ben_ad, zayif, "ben_kulaklik" if self.kulaklik is True else "ben_uzak_bant", gor,
                               kimlik="ben")
        return self._karar(k, ben_ad, zayif, "ben_zayif", gor, kimlik="ben")

    # ------------------------------------------------------------------ karar akisi

    def _gec_kaydet(self, k, karar, an):
        if karar["akis"] != "loopback" or an is None:
            return
        if karar["speaker"] == "?" and karar["neden"] in ("kimse", "hayalet"):
            self._gec[k["klip_no"]] = {"k": k, "an": an, "surum": self._surum, "gec": False}
        elif "+gec" in karar["neden"] and karar["speaker"] != "?":
            # kisa klip, ardindaki onset'siz satir yuzunden tavanli (bekleme suresi doldu): satiri aciklayan klip
            # gelince (tavan kalkinca) ayni klip_no ile guncellenir
            self._gec[k["klip_no"]] = {"k": k, "an": an, "surum": self._surum, "gec": True}

    def _gec_yeniden_puanla(self, an):
        """"?" kararli klipler gec_yukseltme_sn boyunca cizelge degistikce yeniden puanlanir; ad cikarsa (ya da
        bosluk panel_donuk'a donusturse) ayni klip_no ile "guncelleme": True karari."""
        out = []
        if an is None:
            return out
        for no, rec in list(self._gec.items()):
            if an - rec["an"] > self.esik["gec_yukseltme_sn"]:
                del self._gec[no]
                continue
            if rec["surum"] == self._surum:
                continue
            rec["surum"] = self._surum
            yeni = self._karar_ver(rec["k"], True)
            if rec.get("gec"):
                if "+gec" in yeni["neden"] or yeni["speaker"] == "?" or self._gec_bekle(rec["k"]):
                    continue                                 # aciklayan klip hala gelmedi / tavan surer
                yeni["guncelleme"] = True
                del self._gec[no]
                self._log("geç yeniden puan", klip_no=no, neden=yeni["neden"], gecikme=round(an - rec["an"], 1))
                out.append(yeni)
                continue
            if yeni["speaker"] == "?" and yeni["neden"].startswith("kimse"):
                yeni = self._gec_satir_karari(rec["k"], yeni)
            if yeni["speaker"] == "?" and yeni["neden"] != "panel_donuk":
                continue
            if yeni.get("bol") is not None:
                yeni = self.bolunemedi(no)
            yeni["guncelleme"] = True
            del self._gec[no]
            self._log("geç yeniden puan", klip_no=no, neden=yeni["neden"], gecikme=round(an - rec["an"], 1))
            out.append(yeni)
        return out

    def _gec_satir_karari(self, k, karar):
        """Gec beliren satir: "kimse" kalan loopback klibi icin bantlar max(1.0, 2R) genisletilince yalniz BIR kisinin
        bandi klibin >= baskin payini kapsiyorsa (baska kimse >= ikinci degil) "Ad?" (tavan, neden gec_satir); kesin ad
        uretmez, kullanicinin altyazisi klibi kapsiyorsa (yanki) uygulanmaz."""
        if k["akis"] != "mikrofon" and k.get("ust_klip") is None and not self._donuk:
            bas, bit = k["bas"], k["bit"]
            ek = max(1.0, 2.0 * self.R)
            adaylar = []
            for ad, kisi in self._kisiler.items():
                if not kisi.seg:
                    continue
                p = self._pay(kisi, bas, bit, ek)
                if kisi.ben and p >= self.esik["ikinci"]:
                    return karar
                if not kisi.ben and p > 0:
                    adaylar.append((ad, p))
            adaylar.sort(key=lambda x: (-x[1], x[0]))
            if (adaylar and adaylar[0][1] >= self.esik["baskin"] and (len(adaylar) < 2 or adaylar[1][1] < self.esik["ikinci"])
                    and self.kimlik_turu(adaylar[0][0]) not in ("hayalet",)):
                ad = adaylar[0][0]
                g = min(self.esik["tavan"], self.esik["kesin"] - 0.01)
                gor = [(self._gorunen_ad(a), p) for a, p in adaylar]
                return self._karar(k, ad, g, "gec_satir", gor, kapsama=karar["kapsama"], kimlik=self.kimlik_turu(ad))
        return karar

    def kontrol(self, an):
        """Hazir kararlar (klip_no sirali): kapsama tamam (son okuma >= bit + L + R ve panel klipten sonra degisti),
        kapsama yok kesinlesti, ya da son tarih (bit + erteleme_sn). Ardindan gec yeniden puanlanan "?" kararlari
        ("guncelleme": True)."""
        if an is not None:
            self._an = an if self._an is None else max(self._an, an)
            if self._donuk:
                self._bosluk_ekle(self._donuk_bas, an, "donuk")
            elif self._son_okuma_bit is not None and an - self._son_okuma_bit < self._bosluk_esigi():
                self._donuk_denetle(an)
        self._acik_guncelle(an)
        self._capalari_coz(an)
        if self.esik["metin_ipucu"]:
            self._metin_capalari(an)
        if (self._capraz_yeni_onset and an is not None and self._L_onaysiz() and self._L_ornek
                and (self._capraz_son is None or an - self._capraz_son >= float(self.esik["capraz_aralik_sn"]) / 4.0)):
            self._capraz_zorla = True               # L dogrulanmadi: her yeni onset'te (en gec ceyrek aralikta) dene
        if self._capraz_zorla:
            self._capraz_yeni_onset = False
        self._L_capraz(an, zorla=self._capraz_zorla)
        self._capraz_zorla = False
        hazir = []
        for no in sorted(self._bekleyen):
            k = self._bekleyen[no]
            tamam = self._hazirlik(k, an)
            if tamam is None:
                continue
            karar = self._karar_ver(k, tamam)
            hazir.append(karar)
            self._gec_kaydet(k, karar, an)
        for kr in hazir:
            self._bekleyen.pop(kr["klip_no"], None)
        hazir.extend(self._gec_yeniden_puanla(an))
        return hazir

    def bitir(self):
        """Bekleyen tum klipleri eldeki veriyle karara bagla."""
        self._acik = None
        if self._grupsuz_bas is not None and self._son_okuma_bit is not None:
            self._acik = (self._grupsuz_bas, max(self._son_okuma_bit, self._grupsuz_bas) + 1e9)
        self._capalari_coz(None, zorla=True)
        if self.esik["metin_ipucu"]:
            self._metin_capalari(self._an)
        hazir = []
        L, R = self.L, self.R
        for no in sorted(self._bekleyen):
            k = self._bekleyen[no]
            tamam = self._son_okuma_bit is not None and self._son_okuma_bit >= k["bit"] + L + R
            hazir.append(self._karar_ver(k, tamam))
        self._bekleyen.clear()
        self._gec.clear()
        self._metin_bekleyen = []
        return hazir

    def bolunemedi(self, klip_no):
        """bol karari verilen klip yeniden kesilemedi: "A → B", spk_guven bolunemedi_guven, sahip belirsiz."""
        kayit = self._bol.pop(klip_no, None)
        if kayit is None:
            return {"klip_no": klip_no, "akis": "loopback", "speaker": "?", "spk_guven": 0.0, "adaylar": [],
                    "devir": False, "cakisma": False, "at": False, "bol": None, "neden": "bilinmeyen_klip",
                    "kapsama": True, "kimlik_turu": None, "cizelge_canli": False}
        karar = dict(kayit["karar"])
        karar.update(bol=None, devir=True, neden="devir_bolunemedi",
                     spk_guven=round(min(karar["spk_guven"], self.esik["bolunemedi_guven"]), 3))
        k = kayit.get("k")
        if k is not None:                                   # metin ipucu esitlik bozucu (tavan 0.6, "Ad?")
            sec = self._metin_secimi(k, list(kayit["adlar"]))
            if sec is not None and self.kimlik_turu(sec) != "hayalet":
                self._canli = bool(karar.get("cizelge_canli"))
                return self._karar(k, sec, min(karar["spk_guven"], self.esik["tavan"]), "devir_bolunemedi+metin",
                                   [tuple(x) for x in karar["adaylar"]], kapsama=karar["kapsama"],
                                   kimlik=self.kimlik_turu(sec))
        return karar

    # ------------------------------------------------------------------ durum

    def _bosluk_toplam(self):
        top = sum(b - a for a, b in zip(self._bosluk_bas, self._bosluk_bit))
        if self._acik is not None:
            top += max(0.0, self._acik[1] - self._acik[0])
        return top

    def durum(self):
        neden = None
        if self._son_okuma_bit is None:
            neden = "okuma_yok"
        elif self._grupsuz_bas is not None:
            neden = "grup_yok"
        elif self._donuk:
            neden = "panel_donuk"
        elif self._an is not None and self._an - self._son_okuma_bit > self._bosluk_esigi():
            neden = "okuma_yok"
        return {"L": round(self.L, 3), "MAD": round(self.MAD, 3), "R": round(self.R, 3), "kapsama": neden is None,
                "neden": neden, "bosluk_sn": round(self._bosluk_toplam(), 1), "donuk": self._donuk,
                "tutarsiz": self._tutarsiz, "araliklar": self._seg_sayisi, "capa": len(self._L_ornek),
                "L_supheli": (self._L_supheli_bas is not None and self._L_onay is not None     # dogrulanmis L celisti
                              and self._supheli_kaynak == "capraz"),                            # (capraz ilinti gordu)
                "L_supheli_kaynak": self._supheli_kaynak,
                "L_onaysiz": self._L_onaysiz(),
                "atilan_ornek": self._atilan_ornek, "tasma": self._tasma, "kulaklik": self.kulaklik,
                "bekleyen": len(self._bekleyen)}

    def araliklar_listesi(self):
        """Diske yazilacak cizelge: [{"kim","ilk","son","tur"}] (altyazi zamani, epoch). Metin icermez."""
        out = []
        for ad, k in self._kisiler.items():
            for s in k.seg:
                out.append({"kim": ad, "ilk": round(s[0], 3), "son": round(s[1], 3), "tur": s[4]})
        out.sort(key=lambda d: (d["ilk"], d["kim"]))
        return out
