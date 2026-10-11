"""
konusmaci.py — yalniz zamana dayali konusmaci atamasi (docs/MIMARI_v3.md §3 "Cizelge", "Konusmaci atama").

Girdi:
  okuma_ekle({"tur":"okuma","okuma_no","bas_epoch","bit_epoch","grup","gorunen":[[kim, metin], ...]})
      Teams altyazi panelinin HER okumasi (bos olanlar dahil). Metin YALNIZ kelime sayisi ve satir eslestirme
      icin kullanilir; hicbir yere yazilmaz, transkripte girmez.
  klip_ekle({"tur":"klip","klip_no","akis","bas","bit",...})   ses servisinin klip olayi (epoch).
  kulaklik_ayarla(True|False|None)                              pasif kulaklik tespiti.
Cikti: kontrol(an) / bitir() -> karar dict listesi (klip_no sirali):
  {"klip_no","speaker","spk_guven","adaylar","devir","cakisma","at","bol","neden", + "akis","kapsama","kimlik_turu"}

Cizelge: kisi basi aralik birlesimi. Ardisik okumalarda gorunen satirlar hizalanir (kaydirma araniyor);
satirin `son`u yalniz kelime sayisi (gorulen en yuksek) arttiginda ilerler, yeniden yazim/noktalama uzatmaz.
Duraklamis satirin yeniden buyumesi yeni aralik acar ("devam"). Aralik konusma zamanina L ile kaydirilir,
iki uca simetrik R/2 gozlem bandi eklenir: [ilk - L - R/2, son - L + R/2].

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
    "L_maks": 5.0,              # bundan buyuk L ornegi atilir (kirpilmaz)
    "L_pencere": 31,            # kayan medyan pencere boyu
    "capa_min": 3,              # bu kadar capaya kadar tavan
    "basamak_n": 6,             # L basamagi: son N capanin en az K'si ...
    "basamak_k": 5,
    "basamak_sn": 0.6,          # ... L'den ayni yone >= max(bu, 2*MAD) saparsa pencere o capalara kisalir
    "basamak_tavan_capa": 5,    # basamak / saat capasi sonrasi bu kadar yeni capaya kadar tavan
    "saat_adim_sn": 0.3,        # ses saat capasi bu kadar sicradiysa (C1 'capa' olayi) L capalari duzeltilir
    "mad_maks": 1.0,            # MAD > -> tavan
    "R_bas": 1.2,               # R olculene kadar (sn)
    "R_pencere": 10,            # R = son N okuma periyodunun medyani
    "kapsama_kat": 3.0,         # 3R boyunca okuma yok -> kapsama yok
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


def _norm(s, parantez_sil=True):
    """Turkce kucuk harf + ascii katlama + noktalama temizligi (karsilastirma icin)."""
    s = (s or "").replace("İ", "i").replace("I", "ı").lower().translate(_KATLA)
    if parantez_sil:
        s = _PARANTEZ.sub(" ", s)
    s = re.sub(r"['’`´]", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


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
    __slots__ = ("ad", "seg", "ilkler", "maks_sure", "satir", "ben")

    def __init__(self, ad, ben):
        self.ad = ad
        self.seg = []          # [ilk, son, r_ilk, r_son, tur]  (altyazi zamani, epoch); ilk sirali
        self.ilkler = []
        self.maks_sure = 0.0
        self.satir = 0         # ayri satir sayisi (hayalet ad kurali)
        self.ben = ben         # 0 | 1 (zayif ben eslesmesi) | 2 (kesin)


class _Satir:
    __slots__ = ("kim", "kelimeler", "ust", "son_buyume", "seg", "taban")

    def __init__(self, kim, kelimeler, t, taban):
        self.kim = kim
        self.kelimeler = kelimeler
        self.ust = len(kelimeler)   # gorulen en yuksek kelime sayisi (revizyon uzatmasin)
        self.son_buyume = t
        self.seg = None
        self.taban = taban          # baslangici bilinmeyen satir (ilk okuma / kapsama donusu)


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
        self._son_okuma_bas = None
        self._son_okuma_bit = None
        self._ilk_okuma = None
        self._periyot = deque(maxlen=int(self.esik["R_pencere"]))
        self.R = float(self.esik["R_bas"])
        self._bosluk_bas, self._bosluk_bit = [], []      # kapsama yok araliklari (altyazi zamani)
        self._grupsuz_bas = None
        self._acik = None                   # su an acik kapsama boslugu (bas, an)
        self._an = None
        self._L_ornek = deque(maxlen=int(self.esik["L_pencere"]))
        self.L = float(self.esik["L_bas"])
        self.MAD = 0.0
        self._atilan_ornek = 0
        self._basamak_kalan = 0             # L basamagi / saat capasi sonrasi tavanli kalan capa sayisi
        self._son_basamak = None            # son algilanan L basamagi (sn; saat capasi gelince geri alinir)
        self._baslangiclar, self._bas_tur = [], []        # uzak kisi aralik baslari (capa adaylari)
        self._onsetler = []                 # [(klip_bas, onceki_bosluk)] loopback, sirali
        self._capa_gecmis = deque(maxlen=int(self.esik["L_pencere"]))   # capa basi aday gecikme listeleri
        self._son_lb_bit = None
        self._lb = deque(maxlen=400)        # loopback klip araliklari (kapsama yokken yanki tespiti)
        self._bekleyen = {}
        self._bol = OrderedDict()           # bol karari verilmis klipler (bolunemedi / cocuk derinligi)
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
        """"ben" | "kisi" | "oda" | "genel" | "hayalet" (sezgisel ad, henuz < hayalet_satir satir)."""
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

    def _bosluk_ekle(self, a, b):
        if b <= a:
            return
        if self._bosluk_bit and a <= self._bosluk_bit[-1]:         # ortusen: birlestir
            self._bosluk_bit[-1] = max(self._bosluk_bit[-1], b)
            return
        self._bosluk_bas.append(a)
        self._bosluk_bit.append(b)

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
        elif an - self._son_okuma_bit > self.esik["kapsama_kat"] * self.R:
            self._acik = (self._son_okuma_bit, an)

    # ------------------------------------------------------------------ cizelge

    def _seg_ac(self, kim, t, tur):
        k = self._kisi(kim)
        R = self.R
        if self._tasma and k.seg:
            seg = k.seg[-1]
            seg[1] = max(seg[1], t)
            seg[3] = R
            k.maks_sure = max(k.maks_sure, seg[1] - seg[0])
            return seg
        seg = [t, t, R, R, tur]
        k.seg.append(seg)
        k.ilkler.append(t)
        self._seg_sayisi += 1
        if tur in ("yeni", "devam") and not k.ben:
            self._baslangiclar.append(t)
            self._bas_tur.append(tur)
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
        if top > self.esik["aralik_tavan"] and not self._tasma:
            self._tasma = True
            self._log(f"konuşmacı çizelgesi taşması: {top} aralık > tavan {int(self.esik['aralik_tavan'])}; "
                      "yeni satırlar kişinin son aralığına eklenecek", tasma=True)

    def _uzat(self, s, t):
        seg = s.seg
        if t > seg[1]:
            seg[1] = t
            seg[3] = self.R
            k = self._kisiler[s.kim]
            k.maks_sure = max(k.maks_sure, seg[1] - seg[0])

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
        return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.6

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
        es = []
        for i in range(len(yeni)):
            p = onceki[en_k + i] if i < n else None
            es.append(p if p is not None and self._uyumlu(p, yeni[i][0], yeni[i][1]) else None)
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
        if self._son_okuma_bit is not None and bas - self._son_okuma_bit > self.esik["kapsama_kat"] * self.R:
            self._bosluk_ekle(self._son_okuma_bit, bas)           # heartbeat: okuyucu takildi
            self._onceki = None
        if self._ilk_okuma is None:
            self._ilk_okuma = bas
        onceki_bit = self._son_okuma_bit
        self._son_okuma_bas = bas
        self._son_okuma_bit = bit if onceki_bit is None else max(onceki_bit, bit)
        self._acik = None
        if not grup:
            if self._grupsuz_bas is None:
                self._grupsuz_bas = onceki_bit if onceki_bit is not None else bas
            self._onceki = None
            return
        if self._grupsuz_bas is not None:
            self._bosluk_ekle(self._grupsuz_bas, bas)
            self._grupsuz_bas = None
        yeni = []
        for g in okuma.get("gorunen") or ():
            try:
                kim, metin = g[0], g[1]
            except (IndexError, TypeError, KeyError):
                continue
            kim = (kim or "").strip()
            if not kim or kim == "?":
                continue
            yeni.append((kim, _norm(metin).split()))
        taban = self._onceki is None
        es, n = self._hizala(self._onceki, yeni)
        satirlar = []
        for i, (kim, kel) in enumerate(yeni):
            s = es[i]
            if s is None:
                s = _Satir(kim, kel, t, taban=taban or i < n)
                k = self._kisi(kim)
                k.satir += 1
                if not s.taban and kel:
                    s.seg = self._seg_ac(kim, t, "yeni")
                satirlar.append(s)
                continue
            s.kelimeler = kel
            if len(kel) > s.ust:
                s.ust = len(kel)
                if s.seg is not None and t - s.son_buyume <= self._duraklama():
                    self._uzat(s, t)
                else:
                    if s.taban and s.seg is None and t - s.son_buyume <= self._duraklama():
                        tur = "eski"                    # zaten buyuyordu: baslangic bilinmiyor
                    elif s.seg is None and not s.taban:
                        tur = "yeni"
                    else:
                        tur = "devam"                   # duraklama sonrasi yeniden buyume
                    s.seg = self._seg_ac(kim, t, tur)
                s.son_buyume = t
            satirlar.append(s)
        self._onceki = satirlar

    # ------------------------------------------------------------------ L tahmini

    def _L_ornek_ekle(self, x, tur):
        if x > self.esik["L_maks"] or x < 0:
            self._atilan_ornek += 1
            self._log("L örneği atıldı", L_ornek=round(x, 3), capa=tur)
            return
        L0, MAD0 = self.L, self.MAD
        self._L_ornek.append(x)
        if self._basamak_kalan > 0:
            self._basamak_kalan -= 1
        n = int(self.esik["basamak_n"])
        if len(self._L_ornek) > n:
            # basamak (Teams gecikmesi sicradi / saat adimi): son n capanin medyani L'den belirgin sapti.
            # Kayan medyan gec doner, MAD iki tepeli dagilimda dusuk kalir: pencere son n capaya kisalir, tavan.
            son = list(self._L_ornek)[-n:]
            m = _medyan(son)
            esik = max(self.esik["basamak_sn"], 2.0 * MAD0)
            k = int(self.esik["basamak_k"])
            ust = [v for v in son if v - L0 >= esik]
            alt = [v for v in son if L0 - v >= esik]
            if len(ust) >= k or len(alt) >= k:
                son = ust if len(ust) >= k else alt          # yeni duzeyin capalari (eski aday secimleri disarida)
                self._L_ornek = deque(son, maxlen=int(self.esik["L_pencere"]))
                self._capa_gecmis = deque(list(self._capa_gecmis)[-n:], maxlen=int(self.esik["L_pencere"]))
                self._basamak_kalan = int(self.esik["basamak_tavan_capa"])
                self._son_basamak = _medyan(son) - L0
                self._log("L basamağı", L_eski=round(L0, 3), L_yeni=round(_medyan(son), 3), MAD=round(MAD0, 3))
        self.L = _medyan(self._L_ornek)
        self.MAD = _medyan([abs(v - self.L) for v in self._L_ornek])
        self._log("L örneği", L_ornek=round(x, 3), capa=tur, L=round(self.L, 3), MAD=round(self.MAD, 3),
                  R=round(self.R, 3))

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
        (Windows saati adim atti: okumalar hemen kaydi, ses en gec CAPA_ARALIK_SN sonra). Loopback icin aradaki L
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
                self._capa_gecmis = deque([[x - fark for x in l] for l in self._capa_gecmis], maxlen=maxlen)
            else:
                yon = 1.0 if fark > 0 else -1.0
                self._L_ornek = deque([v for v in self._L_ornek if (v - self.L) * yon < abs(fark) / 2.0],
                                      maxlen=maxlen)
            self._son_basamak = None
            self._onsetler = []                 # eski saatle bekleyen capa adaylari
            if self._L_ornek:
                self.L = _medyan(self._L_ornek)
                self.MAD = _medyan([abs(v - self.L) for v in self._L_ornek])
        self._basamak_kalan = int(self.esik["basamak_tavan_capa"])
        self._log("ses saat çapası", akis=akis, fark=round(fark, 3), L=round(self.L, 3), MAD=round(self.MAD, 3))

    def _capalari_coz(self, an, zorla=False):
        """Capa: sessizlik (>= sessizlik_sn) sonrasi loopback klip basi ile sonraki uzak aralik basi (yeni satir:
        capa a; duraklamis satirin yeniden buyumesi: capa b, bosluk >= duraklama_bosluk_sn). Altyazi gecikmesi
        sessizlikten uzunsa "sonraki ilk" aralik bir ONCEKI konusmanin satiri olabilir: klip basindan sonraki
        L_maks icindeki tum aralik baslari aday, son L_pencere capanin oy cokluguyla tutarli olan secilir
        (gecmis yokken mevcut L'ye en yakin). L_maks icinde aday yoksa ornek atilir (kirpilmaz). capa_min capadan
        sonra adaylar birbirinden max(0.35, R/2)'den fazla ayrisiyorsa (belirsiz capa) ornek sayilmaz: oy coklugu
        eski L'yi destekledigi icin gecikme sicradiginda dogru aday reddedilip L geri cekiliyordu.
        Klip ile aralik basi arasinda kapsama boslugu olmamali."""
        if not self._onsetler:
            return
        kalan = []
        bl = self._baslangiclar
        L_maks = float(self.esik["L_maks"])
        for o, bosluk in self._onsetler:
            okundu = self._son_okuma_bit is not None and self._son_okuma_bit > o + L_maks + self.R
            if not (okundu or zorla):
                kalan.append((o, bosluk))
                continue
            j = bisect.bisect_left(bl, o)
            adaylar = []
            while j < len(bl) and bl[j] - o <= L_maks:
                e, tur = bl[j], self._bas_tur[j]
                gerek = self.esik["sessizlik_sn"] if tur == "yeni" else self.esik["duraklama_bosluk_sn"]
                if bosluk >= gerek and self._kapsama_var(o - self.R, e):
                    adaylar.append((e - o, tur))
                j += 1
            if not adaylar:
                if bosluk >= self.esik["sessizlik_sn"]:
                    self._atilan_ornek += 1
                    self._log("L örneği atıldı (L_maks içinde aralık başı yok)", klip_bas=round(o, 3))
                continue
            self._capa_gecmis.append([d for d, _ in adaylar])
            c = self._capa_oyu()
            d, tur = min(adaylar, key=lambda x: (abs(x[0] - c), x[0]))
            tol = max(0.35, self.R / 2.0)
            if len(self._L_ornek) >= self.esik["capa_min"] and any(abs(x - d) > tol for x, _ in adaylar):
                self._log("L örneği sayılmadı (belirsiz çapa)", klip_bas=round(o, 3),
                          adaylar=[round(x, 3) for x, _ in adaylar])
                continue
            self._L_ornek_ekle(d, "a" if tur == "yeni" else "b")
        self._onsetler = kalan

    def _capa_oyu(self):
        """Gecmis capalarin aday gecikmelerinden en cok capanin destekledigi deger (tolerans max(0.35, R/2))."""
        tol = max(0.35, self.R / 2.0)
        en, en_anahtar = self.L, None
        for liste in self._capa_gecmis:
            for c in liste:
                oy = sum(1 for l2 in self._capa_gecmis if any(abs(x - c) <= tol for x in l2))
                anahtar = (-oy, abs(c - self.L), c)
                if en_anahtar is None or anahtar < en_anahtar:
                    en, en_anahtar = c, anahtar
        return en

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
        self._bekleyen[no] = {"klip_no": no, "akis": akis, "bas": bas, "bit": bit, "ust_klip": ust,
                              "_derinlik": derinlik}
        if akis == "loopback" and ust is None:
            bosluk = float("inf") if self._son_lb_bit is None else bas - self._son_lb_bit
            if bosluk >= min(self.esik["sessizlik_sn"], self.esik["duraklama_bosluk_sn"]):
                self._onsetler.append((bas, bosluk))
            self._son_lb_bit = bit if self._son_lb_bit is None else max(self._son_lb_bit, bit)
            self._lb.append((bas, bit))

    def bekleyen(self, bas=None, bit=None):
        n = 0
        for k in self._bekleyen.values():
            if (bit is None or k["bas"] < bit) and (bas is None or k["bit"] > bas):
                n += 1
        return n

    def _parcalar(self, kisi, x0, x1, bant=True, ek=0.0):
        """Kisinin konusma zamanina kaydirilmis (L) parcalari; bant: iki uca R/2 (+ ek, eksi ise daraltir).
        [x0, x1] ile kesisenler."""
        L = self.L
        pay = 2.5 + abs(ek)
        i = bisect.bisect_left(kisi.ilkler, x1 + L + pay)
        alt = x0 + L - pay - kisi.maks_sure
        out = []
        while i > 0:
            i -= 1
            seg = kisi.seg[i]
            if seg[0] < alt:
                break
            if bant:
                a, b = seg[0] - L - seg[2] / 2.0 - ek, seg[1] - L + seg[3] / 2.0 + ek
            else:
                a, b = seg[0] - L, seg[1] - L
            if a <= x1 and b >= x0 and b >= a:
                out.append((a, b))
        out.reverse()
        return out

    def _pay(self, kisi, bas, bit, ek=0.0):
        return min(1.0, _birlesim(self._parcalar(kisi, bas, bit, ek=ek), bas, bit) / max(bit - bas, 0.05))

    def _hazirlik(self, k, an):
        """None: bekle; aksi (tamam: bool) -> karar ver."""
        L, R = self.L, self.R
        son_tarih = an is not None and an >= k["bit"] + self.esik["erteleme_sn"]
        if k["akis"] == "mikrofon" and self.kulaklik is not True and not son_tarih:
            # yanki karari loopback sesine bakar: klibin araligindaki loopback klipleri gelmeden karar yok
            if self._son_lb_bit is None or self._son_lb_bit < k["bit"] - self.esik["lb_tolerans_sn"]:
                if an is None or an < k["bit"] + self.esik["lb_bekle_sn"]:
                    return None
        if self._son_okuma_bit is not None and self._son_okuma_bit >= k["bit"] + L + R:
            return True
        if self._ilk_okuma is not None and not self._kapsama_var(k["bas"] + L - R, k["bit"] + L + R):
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
                "kapsama": kapsama, "kimlik_turu": kimlik}

    def _gorunen_ad(self, ad):
        return "?" if self.kimlik_turu(ad) == "hayalet" else ad

    def _karar_ver(self, k, tamam):
        bas, bit = k["bas"], k["bit"]
        sure = max(bit - bas, 0.05)
        L, R = self.L, self.R
        kapsama = self._kapsama_var(bas + L - R, bit + L + R)
        tavan, tv_neden = 1.0, []
        if len(self._L_ornek) < self.esik["capa_min"]:
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["capa_az"]
        elif self._basamak_kalan > 0:
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["L_basamak"]
        if self.MAD > self.esik["mad_maks"]:
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["mad"]
        if not tamam:
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["eksik"]
        if sure < self.esik["kisa_klip_sn"]:
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["kisa"]
        elif sure < self.esik["kisa_soz_sn"] and len(self._L_ornek) < self.esik["kisa_capa_min"]:
            # kisa sozde L hatasi (az capa, +-0.4 sn) payin tamamini degistirir: daha cok capaya kadar tavan
            tavan, tv_neden = self.esik["tavan"], tv_neden + ["kisa_capa_az"]
        ek = ("+" + "+".join(tv_neden)) if tv_neden else ""
        if k["akis"] == "mikrofon":
            return self._mik_karar(k, sure, kapsama, tavan, ek)
        if not kapsama:
            return self._karar(k, "?", 0.0, "kapsama_yok", kapsama=False)
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
            return self._karar(k, "?", 0.0, "kimse", gor)
        if p2 < ikinci:
            ad = adaylar[0][0]
            tur = self.kimlik_turu(ad)
            if tur == "hayalet":
                return self._karar(k, "?", 0.0, "hayalet", gor, kimlik=tur)
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
            else:
                g, neden = p1, "zayif"
            if tur in ("oda", "genel"):
                g, neden = min(g, self.esik["tavan"]), neden + "+" + tur
            return self._karar(k, ad, min(g, tavan), neden + ek, gor, kimlik=tur)
        return self._iki_aday(k, adaylar[0][0], adaylar[1][0], gor, tavan)

    def _iki_aday(self, k, a, b, gor, tavan):
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
            ad = "?" if ga == "?" and gb == "?" else f"{ga} / {gb}"
            return self._karar(k, ad, g, "cakisma", gor, cakisma=True)
        # sirali devir: once baslayan ilk
        sa = min(max(x, bas) for x, _ in ba)
        sb = min(max(x, bas) for x, _ in bb)
        if (sb, b) < (sa, a):
            a, b, ga, gb, ka, kb = b, a, gb, ga, kb, ka
        hb = [p for p in self._parcalar(kb, bas - R / 2.0, bit + R / 2.0, bant=False)]
        s2 = min(x for x, _ in hb) if hb else min(max(x, bas) for x, _ in self._parcalar(kb, bas, bit)) + R / 2.0
        ha = [y for x, y in self._parcalar(ka, bas - R / 2.0, bit + R / 2.0, bant=False) if x <= s2]
        e1 = max(ha) if ha else s2
        an = (min(e1, s2 + tol) + s2) / 2.0
        ad = "?" if ga == "?" and gb == "?" else f"{ga} → {gb}"
        bolunebilir = bas < an < bit and k["_derinlik"] < self.esik["derinlik_maks"]
        if not bolunebilir:
            return self._karar(k, ad, g, "devir_bolunemedi", gor, devir=True)
        karar = self._karar(k, ad, g, "devir", gor, devir=True, bol=round(an, 3))
        self._bol[k["klip_no"]] = {"karar": dict(karar), "_derinlik": k["_derinlik"], "adlar": (a, b)}
        while len(self._bol) > 500:
            self._bol.popitem(last=False)
        return karar

    def _mik_karar(self, k, sure, kapsama, tavan, ek):
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
            return self._karar(k, ben_ad, zayif, "ben_kapsama_yok", kapsama=False, kimlik="ben")
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

    def kontrol(self, an):
        """Hazir kararlar (klip_no sirali): kapsama tamam (son okuma >= bit + L + R), kapsama yok kesinlesti,
        ya da son tarih (bit + erteleme_sn)."""
        if an is not None:
            self._an = an if self._an is None else max(self._an, an)
        self._acik_guncelle(an)
        self._capalari_coz(an)
        hazir = []
        for no in sorted(self._bekleyen):
            k = self._bekleyen[no]
            tamam = self._hazirlik(k, an)
            if tamam is None:
                continue
            hazir.append(self._karar_ver(k, tamam))
        for kr in hazir:
            del self._bekleyen[kr["klip_no"]]
        return hazir

    def bitir(self):
        """Bekleyen tum klipleri eldeki veriyle karara bagla."""
        self._acik = None
        if self._grupsuz_bas is not None and self._son_okuma_bit is not None:
            self._acik = (self._grupsuz_bas, max(self._son_okuma_bit, self._grupsuz_bas) + 1e9)
        self._capalari_coz(None, zorla=True)
        hazir = []
        L, R = self.L, self.R
        for no in sorted(self._bekleyen):
            k = self._bekleyen[no]
            tamam = self._son_okuma_bit is not None and self._son_okuma_bit >= k["bit"] + L + R
            hazir.append(self._karar_ver(k, tamam))
        self._bekleyen.clear()
        return hazir

    def bolunemedi(self, klip_no):
        """bol karari verilen klip yeniden kesilemedi: "A → B", spk_guven bolunemedi_guven, sahip belirsiz."""
        kayit = self._bol.pop(klip_no, None)
        if kayit is None:
            return {"klip_no": klip_no, "akis": "loopback", "speaker": "?", "spk_guven": 0.0, "adaylar": [],
                    "devir": False, "cakisma": False, "at": False, "bol": None, "neden": "bilinmeyen_klip",
                    "kapsama": True, "kimlik_turu": None}
        karar = dict(kayit["karar"])
        karar.update(bol=None, devir=True, neden="devir_bolunemedi",
                     spk_guven=round(min(karar["spk_guven"], self.esik["bolunemedi_guven"]), 3))
        return karar

    # ------------------------------------------------------------------ durum

    def durum(self):
        kapsama = (self._son_okuma_bit is not None and self._grupsuz_bas is None
                   and (self._an is None or self._an - self._son_okuma_bit <= self.esik["kapsama_kat"] * self.R))
        return {"L": round(self.L, 3), "MAD": round(self.MAD, 3), "R": round(self.R, 3), "kapsama": kapsama,
                "araliklar": self._seg_sayisi, "capa": len(self._L_ornek), "atilan_ornek": self._atilan_ornek,
                "tasma": self._tasma, "kulaklik": self.kulaklik, "bekleyen": len(self._bekleyen)}

    def araliklar_listesi(self):
        """Diske yazilacak cizelge: [{"kim","ilk","son","tur"}] (altyazi zamani, epoch). Metin icermez."""
        out = []
        for ad, k in self._kisiler.items():
            for s in k.seg:
                out.append({"kim": ad, "ilk": round(s[0], 3), "son": round(s[1], 3), "tur": s[4]})
        out.sort(key=lambda d: (d["ilk"], d["kim"]))
        return out
