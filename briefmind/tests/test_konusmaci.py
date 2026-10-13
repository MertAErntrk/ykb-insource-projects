"""konusmaci.py: yalniz zamana dayali konusmaci atamasi (cizelge, L/MAD/R, kapsama, devir, ben/yanki).

Sentetik duzenek: konusmalar (kim, bas, bit, gecikme) -> Teams paneli gibi buyuyen satirlar -> R araliginda
okumalar; klipler STT gecikmesiyle gelir; bol karari kesim duzenegiyle cocuk kliplere ya da bolunemedi'ye gider.
Adlar sentetik."""
import heapq
import itertools
import random

import pytest

import konusmaci as kmod
from konusmaci import Konusmaci

T0 = 1760170000.0
A, B, C, BEN = "Ayşe Demir", "Burak Şahin", "Cem Yıldız", "Deniz Kaya"
KATILIMCI = (A, B, C, BEN)


class Sim:
    def __init__(self, R=0.7, hiz=3.0, panel=12, okuma_suresi=0.1, gecikme=1.0, ben=BEN,
                 katilimcilar=KATILIMCI, esikler=None, kulaklik=True, R_sapma=0.0, seed=0, olay_fn=None,
                 donuk=None, donuk_bayrak=False, yeniden_ciz=None, kuyruk_kelime=None, soru_oran=0.0,
                 metin=False, bozuk=0.0, ilgisiz=False):
        self.R, self.hiz, self.panel, self.okuma_suresi, self.gecikme = R, hiz, panel, okuma_suresi, gecikme
        self.ben = ben
        self.R_sapma = R_sapma
        self.rng = random.Random(seed)
        self.kon = Konusmaci(ben=ben, katilimcilar=katilimcilar, esikler=esikler, olay_fn=olay_fn)
        self.kon.kulaklik_ayarla(kulaklik)
        self.sozler = []
        self.satirlar = {}           # satir_no -> [soz indexleri]
        self.kapali = []             # grup False araliklari
        self.takili = []             # okuma yok araliklari
        self.donuk = donuk           # (a, b): panel bu aralikta donuk (satirlar a anindaki haliyle kalir)
        self.donuk_bayrak = donuk_bayrak   # yakalayici gibi okumaya "donuk": True yaz (>= 12 sn donunca)
        self.yeniden_ciz = yeniden_ciz     # t: bu andan sonra panel yeniden cizilmis (tum satir metinleri farkli)
        self.kuyruk_kelime = kuyruk_kelime # satir en cok bu kadar kelime gosterir (basi duser, kuyruk kayar)
        self.soru_oran = soru_oran         # satirlarin bu payi adsiz ("?") gorunur
        self.metin = metin                 # sozlere sentetik kelimeler: klip "text" ve satir metni (metin ipucu)
        self.bozuk = bozuk                 # altyazi kelimelerinin bu payi bozuk (farkli kelime)
        self.ilgisiz = ilgisiz             # altyazi metni kliplerle ilgisiz (baska kelimeler)
        self._wrng = random.Random(seed * 7919 + 17)
        self.klipler = {}
        self.no = 0
        self.sonuc = {}
        self.bolunen = set()
        self.cocuklar = {}
        self.ek_okumalar = []        # (bas, gorunen) elle

    _HECE = [c + v for c in "bdfgklmnprstvyz" for v in "aeiou"]

    def _kelime_uret(self, n):
        """Sentetik, birbirinden ayirt edilir sozcukler (>= 6 harf); %15 durak kelime (ayirt edici degil)."""
        out = []
        for _ in range(n):
            if self._wrng.random() < 0.15:
                out.append(self._wrng.choice(("ve", "bir", "bu", "için", "ama", "yani", "evet")))
            else:
                out.append("".join(self._wrng.choice(self._HECE) for _ in range(3)))
        return out

    # ---- girdi
    def soz(self, kim, s, e, lag=2.0, satir=None):
        if satir is None:
            satir = len(self.satirlar)
            self.satirlar[satir] = []
        self.satirlar[satir].append(len(self.sozler))
        z = {"kim": kim, "s": T0 + s, "e": T0 + e, "lag": lag, "satir": satir}
        if self.metin:
            n = max(1, int(round((e - s) * self.hiz)))
            z["kelimeler"] = self._kelime_uret(n)
            z["panel"] = self._kelime_uret(n) if self.ilgisiz else list(z["kelimeler"])
            if self.bozuk:
                yedek = self._kelime_uret(n)
                z["panel"] = [yedek[j] if self._wrng.random() < self.bozuk else w for j, w in enumerate(z["panel"])]
        self.sozler.append(z)
        return satir

    def _klip_metni(self, klip):
        """Klibin akisindaki sozlerin klip icinde soylenen kelimeleri (kelime j: s + (j + 0.5) / hiz)."""
        if not self.metin:
            return ""
        kel = []
        for z in self.sozler:
            if self.akis_of(z["kim"]) != klip["akis"] or "kelimeler" not in z:
                continue
            for j, w in enumerate(z["kelimeler"]):
                an = z["s"] + (j + 0.5) / self.hiz
                if klip["bas"] <= an <= klip["bit"]:
                    kel.append((an, w))
        kel.sort()
        return " ".join(w for _, w in kel)

    def klip(self, bas, bit, akis="loopback", mutlak=False):
        no = self.no
        self.no += 1
        off = 0.0 if mutlak else T0
        self.klipler[no] = {"tur": "klip", "klip_no": no, "akis": akis, "bas": off + bas, "bit": off + bit,
                            "sure": bit - bas, "text": "", "ts": "", "rms": 0.1, "zarf": [], "ust_klip": None,
                            "supheli": False}
        return no

    def akis_of(self, kim):
        return "mikrofon" if kim == self.ben else "loopback"

    def otomatik_klipler(self, birlesim=0.3, maks=12.0):
        for akis in ("loopback", "mikrofon"):
            ar = sorted((z["s"], z["e"]) for z in self.sozler if self.akis_of(z["kim"]) == akis)
            birles = []
            for s, e in ar:
                if birles and s - birles[-1][1] < birlesim:
                    birles[-1][1] = max(birles[-1][1], e)
                else:
                    birles.append([s, e])
            for s, e in birles:
                n = max(1, int(-(-(e - s) // maks)))         # uzun konusma esit parcalara (VAD 12 sn ust siniri)
                for i in range(n):
                    self.klip(s + (e - s) * i / n, s + (e - s) * (i + 1) / n, akis, mutlak=True)

    # ---- panel
    def _kelime(self, z, t):
        if t < z["s"] + z["lag"]:
            return 0
        top = max(1, int(round((z["e"] - z["s"]) * self.hiz)))
        return min(top, 1 + int((t - z["s"] - z["lag"]) * self.hiz))

    def _satir_metni(self, no, idx, t):
        parcalar = []
        for i in idx:
            z = self.sozler[i]
            n = self._kelime(z, t)
            if self.metin:
                parcalar.extend(z["panel"][:n])
            else:
                parcalar.extend("r%dk%d" % (no, j) for j in range(len(parcalar), len(parcalar) + n))
        if self.kuyruk_kelime:
            parcalar = parcalar[-self.kuyruk_kelime:]
        if self.yeniden_ciz is not None and t - T0 >= self.yeniden_ciz:
            parcalar = ["x" + w for w in parcalar]
        return " ".join(parcalar)

    def gorunen(self, t):
        if self.donuk and T0 + self.donuk[0] <= t < T0 + self.donuk[1]:
            t = T0 + self.donuk[0]
        rows = []
        for no, idx in self.satirlar.items():
            n = sum(self._kelime(self.sozler[i], t) for i in idx)
            if n <= 0:
                continue
            ilk = min(self.sozler[i]["s"] + self.sozler[i]["lag"] for i in idx)
            kim = self.sozler[idx[0]]["kim"]
            if self.soru_oran and (no * 7919 + 13) % 100 < self.soru_oran * 100:
                kim = "?"
            rows.append((ilk, no, kim, self._satir_metni(no, idx, t)))
        rows.sort()
        return [[kim, metin] for _, _, kim, metin in rows[-self.panel:]]

    @staticmethod
    def _icinde(t, araliklar):
        return any(T0 + a <= t < T0 + b for a, b in araliklar)

    # ---- dogruluk
    def gercek(self, klip):
        pay = {}
        for z in self.sozler:
            if self.akis_of(z["kim"]) != klip["akis"]:
                continue
            o = min(z["e"], klip["bit"]) - max(z["s"], klip["bas"])
            if o > 0:
                pay[z["kim"]] = pay.get(z["kim"], 0.0) + o
        if not pay:
            return None
        return sorted(pay.items(), key=lambda x: (-x[1], x[0]))[0][0]

    def _kesim(self, klip, an):
        ar = sorted((z["s"], z["e"]) for z in self.sozler if self.akis_of(z["kim"]) == klip["akis"]
                    and z["e"] > klip["bas"] and z["s"] < klip["bit"])
        bosluklar = []
        for (s1, e1), (s2, e2) in zip(ar, ar[1:]):
            if s2 > e1:
                bosluklar.append((s2 + e1) / 2.0)
        aday = [g for g in bosluklar if abs(g - an) <= 1.0]
        kes = min(aday, key=lambda g: (abs(g - an), g)) if aday else an
        if kes - klip["bas"] < 1.0 or klip["bit"] - kes < 1.0:
            return None
        return kes

    # ---- kosu
    def _isle(self, kararlar, an, olaylar, sira):
        for k in kararlar:
            no = k["klip_no"]
            if k["bol"] is None:
                self.sonuc[no] = k
                continue
            klip = self.klipler[no]
            kes = self._kesim(klip, k["bol"])
            self.bolunen.add(no)
            if kes is None:
                self.sonuc[no] = self.kon.bolunemedi(no)
                continue
            for a, b in ((klip["bas"], kes), (kes, klip["bit"])):
                cn = self.no
                self.no += 1
                c = dict(klip, klip_no=cn, bas=a, bit=b, sure=b - a, ust_klip=no)
                self.klipler[cn] = c
                self.cocuklar.setdefault(no, []).append(cn)
                heapq.heappush(olaylar, (an + self.gecikme, 1, next(sira), "klip", c))

    def kos(self):
        sira = itertools.count()
        son = max([z["e"] + z["lag"] for z in self.sozler] + [k["bit"] for k in self.klipler.values()] + [T0]) + 20
        olaylar = []
        t = T0 - 5.0
        okuma_no = 0
        while t < son:
            if not self._icinde(t, self.takili):
                grup = not self._icinde(t, self.kapali)
                tob = t + self.okuma_suresi / 2.0
                ok = {"tur": "okuma", "okuma_no": okuma_no, "bas_epoch": t, "bit_epoch": t + self.okuma_suresi,
                      "grup": grup, "gorunen": self.gorunen(tob) if grup else []}
                if self.donuk_bayrak and self.donuk and T0 + self.donuk[0] + 12 <= tob < T0 + self.donuk[1]:
                    ok["donuk"] = True
                okuma_no += 1
                heapq.heappush(olaylar, (t + self.okuma_suresi, 0, next(sira), "okuma", ok))
            t += self.R + (self.rng.uniform(-self.R_sapma, self.R_sapma) if self.R_sapma else 0.0)
        for k in list(self.klipler.values()):
            if self.metin and not k["text"]:
                k["text"] = self._klip_metni(k)
            heapq.heappush(olaylar, (k["bit"] + self.gecikme, 1, next(sira), "klip", k))
        while olaylar:
            an, _, _, tur, veri = heapq.heappop(olaylar)
            if tur == "okuma":
                self.kon.okuma_ekle(veri)
            else:
                self.kon.klip_ekle(veri)
            self._isle(self.kon.kontrol(an), an, olaylar, sira)
        for _ in range(5):
            self._isle(self.kon.bitir(), son, olaylar, sira)
            if not olaylar:
                break
            while olaylar:
                an, _, _, tur, veri = heapq.heappop(olaylar)
                self.kon.klip_ekle(veri)
        return self.sonuc

    def son_kararlar(self):
        """Bolunen kliplerin yerine cocuklari: her ses parcasi icin son karar."""
        return {no: k for no, k in self.sonuc.items() if no not in self.bolunen or k["neden"] == "devir_bolunemedi"}


def kesin_mi(k):
    sp = k["speaker"]
    return (k["spk_guven"] >= 0.7 and not k["at"] and sp not in ("?", "") and "→" not in sp and " / " not in sp)


def yanlis_kesinler(sim):
    yanlis = []
    for no, k in sim.son_kararlar().items():
        if kesin_mi(k) and k["speaker"] != sim.gercek(sim.klipler[no]):
            yanlis.append((no, k, sim.gercek(sim.klipler[no])))
    return yanlis


def isinma(sim, lag=2.0, bas=0.0):
    """L capalari icin 4 ayri, sessizlikle ayrilmis konusma (klipleriyle). Bitis zamanini dondurur."""
    t = bas
    for kim, sure, bosluk in ((A, 2.0, 1.6), (B, 2.6, 2.4), (C, 1.8, 1.9), (A, 2.3, 2.1)):
        sim.soz(kim, t, t + sure, lag)
        sim.klip(t, t + sure)
        t += sure + bosluk
    return t


# ---------------------------------------------------------------- temel atama

def test_tek_konusmaci_baskin_ve_kesin():
    sim = Sim()
    t = isinma(sim)
    sim.soz(B, t, t + 4.0)
    no = sim.klip(t, t + 4.0)
    sim.kos()
    k = sim.sonuc[no]
    assert k["speaker"] == B and k["spk_guven"] >= 0.7 and k["neden"].startswith("baskin")
    assert k["adaylar"][0][0] == B
    assert sim.kon.durum()["capa"] >= 3


def test_capa_azken_tavan_06():
    sim = Sim()
    sim.soz(A, 0, 4.0)
    no = sim.klip(0, 4.0)
    sim.kos()
    k = sim.sonuc[no]
    assert k["speaker"] == A and k["spk_guven"] <= 0.6 and "capa_az" in k["neden"]


def test_kimse_yok_soru_isareti():
    sim = Sim()
    t = isinma(sim)
    no = sim.klip(t + 3, t + 6)        # sesi var, altyazisi yok
    sim.kos()
    assert sim.sonuc[no]["speaker"] == "?" and sim.sonuc[no]["neden"] == "kimse"


def test_zayif_kapsama_ad_soru():
    """Baskin 0.25-0.60 ve ikinci < 0.25 -> ad, spk_guven = baskin (< 0.7, gosterimde 'Ad?')."""
    sim = Sim()
    t = isinma(sim)
    sim.soz(C, t, t + 1.5)
    no = sim.klip(t, t + 4.5)           # klibin yaklasik 1/3'u altyazili
    sim.kos()
    k = sim.sonuc[no]
    assert k["speaker"] == C and 0.25 <= k["spk_guven"] < 0.6 and k["neden"].startswith("zayif")


@pytest.mark.parametrize("lag", [0.0, 2.0, 3.5])
def test_altyazi_gecikmesi_L_tahmini(lag):
    sim = Sim()
    t = isinma(sim, lag=lag)
    nolar = []
    for kim in (B, C, A, B):
        sim.soz(kim, t, t + 3.0, lag)
        nolar.append((sim.klip(t, t + 3.0), kim))
        t += 4.2
    sim.kos()
    d = sim.kon.durum()
    assert abs(d["L"] - (lag + sim.R / 2)) <= 0.6, d
    for no, kim in nolar:
        assert sim.sonuc[no]["speaker"] == kim and sim.sonuc[no]["spk_guven"] >= 0.7
    assert not yanlis_kesinler(sim)


def test_L_ust_ustu_ornek_atilir():
    """L oturduktan (2 sn) sonra gecikmesi 6 sn'ye sicrayan sozlerin capalari L_ust = max(L_maks, L + L_pay) disinda:
    atilir (L ya yerinde kalir ya capraz ilinti yeniden olcer), o kliplerin karari kesin degil, kesin yanlis yok.
    (Isinmada pencere capraz_ust: 6 sn'lik gecikme dogrudan ogrenilir; bkz. test_isinmada_gecikme_6_dogrudan_ogrenilir.)"""
    olaylar = []
    sim = Sim(olay_fn=olaylar.append)
    t = isinma(sim, lag=2.0)
    nolar = []
    for kim in (A, B, C, A):
        sim.soz(kim, t, t + 2.0, 6.0)
        nolar.append(sim.klip(t, t + 2.0))
        t += 9.0
    sim.kos()
    d = sim.kon.durum()
    assert d["atilan_ornek"] >= 3, d
    assert abs(d["L"] - (2.0 + sim.R / 2)) <= 0.6 or any(o["mesaj"] == "L çapraz ilinti" for o in olaylar), d
    assert all(sim.sonuc[no]["spk_guven"] <= 0.6 for no in nolar)
    assert not yanlis_kesinler(sim)


def test_isinmada_gecikme_6_dogrudan_ogrenilir():
    """Isinmada (capa_min'den az ornek) aday penceresi capraz_ust (10 sn): 6 sn'lik gecikme, pencerede tek aday
    oldugundan dogrudan ogrenilir; kesin yanlis yok."""
    sim = Sim()
    t = 0.0
    for kim in (A, B, C, A):
        sim.soz(kim, t, t + 2.0, 6.0)
        sim.klip(t, t + 2.0)
        t += 9.0
    sim.kos()
    d = sim.kon.durum()
    assert d["capa"] >= 3 and abs(d["L"] - (6.0 + sim.R / 2)) <= 0.6, d
    assert not yanlis_kesinler(sim)


def test_capa_b_duraklama_sonrasi_yeniden_buyume():
    """Monolog: tek satir, aralarda duraklama. Yeni satir yok; L yeniden buyume capasindan ogrenilir."""
    sim = Sim()
    satir = sim.soz(A, 0.0, 3.0, lag=3.0)
    sim.klip(0.0, 3.0)
    t = 5.0
    for _ in range(4):
        sim.soz(A, t, t + 3.0, lag=3.0, satir=satir)
        sim.klip(t, t + 3.0)
        t += 5.0
    sim.kos()
    d = sim.kon.durum()
    assert d["capa"] >= 3 and abs(d["L"] - (3.0 + sim.R / 2)) <= 0.6, d
    assert any(a["tur"] == "devam" for a in sim.kon.araliklar_listesi())


def test_mad_buyukse_tavan():
    sim = Sim()
    t = 0.0
    for i, kim in enumerate((A, B, C, A, B, C, A, B)):
        lag = 0.5 if i % 2 else 4.0
        sim.soz(kim, t, t + 2.0, lag)
        sim.klip(t, t + 2.0)
        t += 6.0
    sim.kos()
    d = sim.kon.durum()
    assert d["MAD"] > 1.0
    son = sim.sonuc[max(sim.sonuc)]
    assert son["spk_guven"] <= 0.6 and "mad" in son["neden"]


# ---------------------------------------------------------------- R ve kapsama

def test_R_son_10_okuma_periyodundan():
    kon = Konusmaci()
    t = T0
    for i in range(15):
        kon.okuma_ekle({"tur": "okuma", "okuma_no": i, "bas_epoch": t, "bit_epoch": t + 0.2, "grup": True,
                        "gorunen": []})
        t += 0.9
    assert abs(kon.durum()["R"] - 0.9) < 1e-6
    for i in range(10):
        kon.okuma_ekle({"tur": "okuma", "okuma_no": 100 + i, "bas_epoch": t, "bit_epoch": t + 0.1, "grup": True,
                        "gorunen": []})
        t += 0.5
    assert abs(kon.durum()["R"] - 0.5) < 1e-6


def test_altyazi_2dk_kapali_soru_isareti():
    sim = Sim()
    t = isinma(sim)
    sim.kapali.append((t + 5, t + 125))
    ici = []
    for i in range(6):
        s = t + 10 + i * 15
        sim.soz(A if i % 2 else B, s, s + 4)
        ici.append(sim.klip(s, s + 4))
    s = t + 135
    sim.soz(C, s, s + 4)
    sonra = sim.klip(s, s + 4)
    sim.kos()
    for no in ici:
        assert sim.sonuc[no]["speaker"] == "?" and sim.sonuc[no]["neden"] == "kapsama_yok"
        assert sim.sonuc[no]["kapsama"] is False
    assert sim.sonuc[sonra]["speaker"] == C


def test_okuyucu_takilmasi_heartbeat():
    sim = Sim()
    t = isinma(sim)
    sim.takili.append((t + 2, t + 40))
    sim.soz(B, t + 10, t + 14)
    no = sim.klip(t + 10, t + 14)
    sim.kos()
    assert sim.sonuc[no]["speaker"] == "?" and sim.sonuc[no]["neden"] == "kapsama_yok"


def test_kapsama_durumu_ve_acik_bosluk():
    kon = Konusmaci()
    for i in range(5):
        kon.okuma_ekle({"tur": "okuma", "okuma_no": i, "bas_epoch": T0 + i, "bit_epoch": T0 + i + 0.1,
                        "grup": True, "gorunen": []})
    kon.kontrol(T0 + 5)
    assert kon.durum()["kapsama"] is True
    kon.kontrol(T0 + 30)
    assert kon.durum()["kapsama"] is False
    kon.okuma_ekle({"tur": "okuma", "okuma_no": 9, "bas_epoch": T0 + 31, "bit_epoch": T0 + 31.1, "grup": False,
                    "gorunen": []})
    kon.kontrol(T0 + 31.2)
    assert kon.durum()["kapsama"] is False


# ---------------------------------------------------------------- erteleme

def test_erteleme_kapsama_tamamlanana_kadar():
    kon = Konusmaci()
    okuma = [0]
    t = [T0]

    def oku_kadar(son, gorunen):
        while t[0] < son:
            okuma[0] += 1
            kon.okuma_ekle({"tur": "okuma", "okuma_no": okuma[0], "bas_epoch": t[0], "bit_epoch": t[0] + 0.1,
                            "grup": True, "gorunen": gorunen})
            t[0] += 0.5

    oku_kadar(T0 + 4.0, [])
    oku_kadar(T0 + 5.6, [[A, "bir iki"]])
    kon.klip_ekle({"tur": "klip", "klip_no": 1, "akis": "loopback", "bas": T0 + 3, "bit": T0 + 5})
    assert kon.kontrol(T0 + 5.6) == [] and kon.bekleyen() == 1
    assert kon.bekleyen(T0 + 4, T0 + 4.5) == 1 and kon.bekleyen(T0 + 6, T0 + 7) == 0
    oku_kadar(T0 + 7.0, [[A, "bir iki üç dört"]])
    assert kon.kontrol(t[0]) == []                    # bit + L + R = +7.5 henuz okunmadi
    oku_kadar(T0 + 8.0, [[A, "bir iki üç dört"]])
    kr = kon.kontrol(t[0])
    assert len(kr) == 1 and kon.bekleyen() == 0 and kr[0]["kapsama"] is True


def test_erteleme_son_tarih_okuyucu_yok():
    kon = Konusmaci()
    kon.klip_ekle({"tur": "klip", "klip_no": 7, "akis": "loopback", "bas": T0, "bit": T0 + 3})
    assert kon.kontrol(T0 + 10) == []
    kr = kon.kontrol(T0 + 18.1)
    assert len(kr) == 1 and kr[0]["speaker"] == "?" and kr[0]["kapsama"] is False


def test_bitir_bekleyenleri_karara_baglar():
    kon = Konusmaci()
    kon.klip_ekle({"tur": "klip", "klip_no": 1, "akis": "loopback", "bas": T0, "bit": T0 + 3})
    kon.klip_ekle({"tur": "klip", "klip_no": 2, "akis": "mikrofon", "bas": T0, "bit": T0 + 3})
    kr = kon.bitir()
    assert [k["klip_no"] for k in kr] == [1, 2] and kon.bekleyen() == 0


# ---------------------------------------------------------------- devir / cakisma

def test_hizli_devir_ayri_klipler():
    sim = Sim()
    t = isinma(sim)
    sira = []
    for i in range(10):
        kim = (A, B)[i % 2]
        sim.soz(kim, t, t + 3.0)
        sira.append((sim.klip(t, t + 3.0), kim))
        t += 3.35
    sim.kos()
    kesin = [no for no, kim in sira if kesin_mi(sim.sonuc[no])]
    assert not yanlis_kesinler(sim)
    assert len(kesin) >= 7
    for no, kim in sira:
        assert sim.sonuc[no]["speaker"] in (kim, "?") or "→" in sim.sonuc[no]["speaker"]
        assert not sim.sonuc[no]["cakisma"]


def test_araya_girme_baskin_kalir():
    sim = Sim()
    t = isinma(sim)
    sim.soz(A, t, t + 9.0)
    sim.soz(B, t + 4.0, t + 4.5)          # 'evet' araya
    no = sim.klip(t, t + 9.0)
    sim.kos()
    k = sim.sonuc[no]
    assert k["speaker"] == A and not k["cakisma"] and k["bol"] is None


def test_gercek_cakisma_a_b():
    sim = Sim()
    t = isinma(sim)
    sim.soz(A, t, t + 6.0)
    sim.soz(B, t + 1.0, t + 5.5)
    no = sim.klip(t, t + 6.0)
    sim.kos()
    k = sim.sonuc[no]
    assert k["cakisma"] is True and k["speaker"] == f"{A} / {B}" and k["bol"] is None
    assert k["spk_guven"] <= 0.5 and not k["devir"]


def test_klip_ici_devir_bol_ve_cocuklar():
    sim = Sim()
    t = isinma(sim)
    sim.soz(A, t, t + 3.0)
    sim.soz(B, t + 3.3, t + 7.0)
    no = sim.klip(t, t + 7.0)
    sim.kos()
    assert no in sim.bolunen and len(sim.cocuklar[no]) == 2
    c1, c2 = sim.cocuklar[no]
    assert sim.sonuc[c1]["speaker"].startswith(A) or sim.sonuc[c1]["speaker"] == "?"
    assert sim.sonuc[c2]["speaker"].startswith(B) or "→" in sim.sonuc[c2]["speaker"]
    assert not any(sim.sonuc[c]["cakisma"] for c in (c1, c2))
    assert not yanlis_kesinler(sim)


def test_klip_ici_devir_karar_alanlari_ve_bolunemedi():
    kon_sim = Sim()
    t = isinma(kon_sim)
    kon_sim.soz(A, t, t + 3.0)
    kon_sim.soz(B, t + 3.3, t + 7.0)
    kon_sim.klip(t, t + 7.0)
    kayit = []
    gercek_isle = kon_sim._isle

    def isle(kararlar, an, olaylar, sira):
        kayit.extend(k for k in kararlar if k["bol"] is not None)
        return gercek_isle(kararlar, an, olaylar, sira)

    kon_sim._isle = isle
    kon_sim.kos()
    assert kayit
    k = kayit[0]
    assert k["devir"] is True and not k["cakisma"] and k["speaker"] == f"{A} → {B}"
    assert T0 + t + 2.4 <= k["bol"] <= T0 + t + 3.9
    b = kon_sim.kon.bolunemedi(k["klip_no"])            # kayit cocuklar uretildikten sonra da cagrilabilir
    assert b["klip_no"] == k["klip_no"]


def test_bolunemedi_a_ok_b():
    sim = Sim()
    t = isinma(sim)
    sim.soz(A, t, t + 0.8)
    sim.soz(B, t + 1.0, t + 1.9)
    no = sim.klip(t, t + 1.9)               # parcalar < 1 sn: kesilemez
    sim.kos()
    k = sim.sonuc[no]
    assert k["speaker"] == f"{A} → {B}" and k["spk_guven"] == 0.5 and k["devir"] and k["bol"] is None
    assert k["neden"] == "devir_bolunemedi"
    assert Konusmaci().bolunemedi(99)["speaker"] == "?"


# ---------------------------------------------------------------- kimlik_turu / hayalet

def test_hayalet_ad_uc_satira_kadar_soru():
    hayalet = "Toplantı Kaydı Başladı"
    sim = Sim()
    t = isinma(sim)
    nolar = []
    for i in range(4):
        sim.soz(hayalet, t, t + 3.0)
        nolar.append(sim.klip(t, t + 3.0))
        t += 4.5
    ilk = {}
    gercek_isle = sim._isle

    def isle(kararlar, an, olaylar, sira):
        for k in kararlar:
            ilk.setdefault(k["klip_no"], k)
        return gercek_isle(kararlar, an, olaylar, sira)

    sim._isle = isle
    sim.kos()
    # karar anindaki satir sayisi: 1., 2. klip "?" ; 3. satirdan sonra ad sayilir
    assert ilk[nolar[0]]["speaker"] == "?" and ilk[nolar[0]]["neden"] == "hayalet"
    assert sim.sonuc[nolar[-1]]["speaker"] == hayalet
    # gec yeniden puanlama: ad kisi sayilinca ilk klibin "?" karari ayni klip_no ile guncellenir
    assert sim.sonuc[nolar[0]]["speaker"] == hayalet and sim.sonuc[nolar[0]].get("guncelleme") is True
    assert sim.kon.kimlik_turu(hayalet) == "kisi"
    assert Konusmaci().kimlik_turu("Rastgele Bir Satır") == "hayalet"


@pytest.mark.parametrize("etiket,tur", [("Konuk", "genel"), ("Konuşmacı 2", "genel"), ("Speaker 3", "genel"),
                                        ("Bilinmeyen", "genel"), ("Ali Veli (Konuk)", "genel"),
                                        ("Toplantı Odası 3", "oda"), ("Kat 5 Salonu", "oda"),
                                        ("MTR Room 12", "oda")])
def test_oda_genel_etiketleri_tavan(etiket, tur):
    sim = Sim()
    assert sim.kon.kimlik_turu(etiket) == tur
    t = isinma(sim)
    sim.soz(etiket, t, t + 4.0)
    no = sim.klip(t, t + 4.0)
    sim.kos()
    k = sim.sonuc[no]
    assert k["speaker"] == etiket and k["spk_guven"] <= 0.6 and k["kimlik_turu"] == tur


def test_katilimci_birebir_kisi():
    kon = Konusmaci(ben=BEN, katilimcilar=[A, "Elif Arslan (Dış)"])
    assert kon.kimlik_turu(A) == "kisi"
    assert kon.kimlik_turu("Elif Arslan") == "kisi"
    assert kon.kimlik_turu("Ayşe") == "hayalet"          # birebir degil, sezgisel
    assert kon.kimlik_turu(BEN) == "ben"


def test_ben_mi_kurali():
    kon = Konusmaci(ben="Deniz Kaya")
    assert kon._ben_mi("Deniz Kaya") and kon._ben_mi("deniz kaya") and kon._ben_mi("Deniz")
    assert kon._ben_mi("Deniz Kaya (Siz)") and kon._ben_mi("Deniz Can Kaya")
    assert not kon._ben_mi("Deniz Arslan")
    assert not Konusmaci()._ben_mi("Deniz")


# ---------------------------------------------------------------- mikrofon: ben / yanki

def test_kullanici_konusuyor_ben_095_ve_loopback_adayi_degil():
    sim = Sim()
    t = isinma(sim)
    sim.soz(BEN, t, t + 4.0)
    mik = sim.klip(t, t + 4.0, akis="mikrofon")
    lb = sim.klip(t, t + 4.0, akis="loopback")          # (olmamali ama) loopback'te kullanici adayi yok
    sim.kos()
    assert sim.sonuc[mik]["speaker"] == BEN and sim.sonuc[mik]["spk_guven"] == 0.95
    assert sim.sonuc[lb]["speaker"] == "?"


@pytest.mark.parametrize("kulaklik,at", [(False, True), (None, True), (True, False)])
def test_yanki(kulaklik, at):
    sim = Sim(kulaklik=kulaklik)
    t = isinma(sim)
    sim.soz(B, t, t + 4.0)
    lb = sim.klip(t, t + 4.0)
    mik = sim.klip(t + 0.05, t + 4.0, akis="mikrofon")   # hoparlorden mikrofona sizan B
    sim.kos()
    k = sim.sonuc[mik]
    assert k["at"] is at
    if at:
        assert k["neden"] == "yanki"
    else:
        assert k["speaker"] == BEN and k["spk_guven"] <= 0.6
    assert sim.sonuc[lb]["speaker"] == B


def test_mikrofon_kimse_yok_ben_06():
    sim = Sim()
    t = isinma(sim)
    no = sim.klip(t + 1, t + 4, akis="mikrofon")
    sim.kos()
    assert sim.sonuc[no]["speaker"] == BEN and sim.sonuc[no]["spk_guven"] == 0.6 and not sim.sonuc[no]["at"]


def test_mikrofon_kapsama_yok_yanki_sesten():
    kon = Konusmaci(ben=BEN)
    kon.kulaklik_ayarla(False)
    kon.klip_ekle({"tur": "klip", "klip_no": 1, "akis": "loopback", "bas": T0, "bit": T0 + 4})
    kon.klip_ekle({"tur": "klip", "klip_no": 2, "akis": "mikrofon", "bas": T0 + 0.1, "bit": T0 + 4})
    kon.klip_ekle({"tur": "klip", "klip_no": 3, "akis": "mikrofon", "bas": T0 + 10, "bit": T0 + 12})
    kr = {k["klip_no"]: k for k in kon.bitir()}
    assert kr[1]["speaker"] == "?" and kr[2]["at"] is True and kr[3]["speaker"] == BEN
    kon.klip_ekle({"tur": "kulaklik", "durum": True, "guven": 0.9})
    assert kon.kulaklik is True


# ---------------------------------------------------------------- cizelge: revizyon, hizalama, tavan

def _oku(kon, no, t, gorunen, grup=True):
    kon.okuma_ekle({"tur": "okuma", "okuma_no": no, "bas_epoch": t, "bit_epoch": t + 0.1, "grup": grup,
                    "gorunen": gorunen})


def test_revizyon_son_u_uzatmaz():
    kon = Konusmaci()
    t = T0
    _oku(kon, 0, t, [])
    metinler = ["Bugün", "Bugün toplantı", "Bugün toplantı saat", "Bugün toplantı saat üçte"]
    for i, m in enumerate(metinler):
        t += 0.6
        _oku(kon, i + 1, t, [[A, m]])
    son_buyume = t + 0.05
    for i, m in enumerate(["Bugün toplantı saat üçte.", "Bugün toplantı, saat 3'te", "bugün toplantı saat üçte"]):
        t += 0.6
        _oku(kon, 10 + i, t, [[A, m]])
    ar = kon.araliklar_listesi()
    assert len(ar) == 1 and abs(ar[0]["son"] - son_buyume) < 1e-6


def test_panel_kaymasi_ve_yeni_satir():
    kon = Konusmaci()
    t = T0
    _oku(kon, 0, t, [[A, "eski satır bir"], [B, "eski satır iki"]])         # ilk okuma: taban, aralik yok
    assert kon.araliklar_listesi() == []
    t += 0.6
    _oku(kon, 1, t, [[B, "eski satır iki"], [C, "yeni"]])                    # ust satir dustu, C yeni
    t += 0.6
    _oku(kon, 2, t, [[B, "eski satır iki"], [C, "yeni satır geldi"]])
    ar = kon.araliklar_listesi()
    assert [(a["kim"], a["tur"]) for a in ar] == [(C, "yeni")]
    assert abs(ar[0]["son"] - (t + 0.05)) < 1e-6


def test_tekrarlanan_kisa_satir_ayri_aralik():
    kon = Konusmaci()
    t = T0
    _oku(kon, 0, t, [])
    seq = [[[A, "Evet."]], [[A, "Evet."]], [[A, "Evet."], [B, "tamam o zaman"]],
           [[A, "Evet."], [B, "tamam o zaman"], [A, "Evet."]]]
    for i, g in enumerate(seq):
        t += 0.6
        _oku(kon, i + 1, t, g)
    kim = [a["kim"] for a in kon.araliklar_listesi()]
    assert kim == [A, B, A]


def test_aralik_tavani_tasma_logu():
    olaylar = []
    kon = Konusmaci(esikler={"aralik_tavan": 5, "budama_sn": 1000.0}, olay_fn=olaylar.append)
    t = T0
    _oku(kon, 0, t, [])
    rows = []
    for i in range(12):
        t += 0.6
        rows = (rows + [[(A, B)[i % 2], "satır %d" % i]])[-8:]
        _oku(kon, i + 1, t, list(rows))
    d = kon.durum()
    assert d["tasma"] is True and d["araliklar"] <= 7
    assert any(o.get("tasma") for o in olaylar)


def test_aralik_tavani_budama():
    kon = Konusmaci(esikler={"aralik_tavan": 4, "budama_sn": 5.0})
    t = T0
    _oku(kon, 0, t, [])
    for i in range(20):
        t += 3.0
        _oku(kon, i + 1, t, [[(A, B)[i % 2], "satır %d" % i]])
    d = kon.durum()
    assert d["araliklar"] <= 4 and d["tasma"] is False


def test_grup_false_kapsama_boslugu_ve_sifirlama():
    kon = Konusmaci()
    t = T0
    for i in range(5):
        _oku(kon, i, t, [[A, "bir"]] if i else [])
        t += 0.5
    for i in range(5):
        _oku(kon, 10 + i, t, [], grup=False)
        t += 0.5
    _oku(kon, 20, t, [[A, "bir iki üç dört"]])     # donuste satir taban: yeni aralik acmaz
    assert [a["kim"] for a in kon.araliklar_listesi()] == [A]
    assert not kon._kapsama_var(T0 + 2.6, T0 + 4.0)
    assert kon._kapsama_var(T0 + 0.5, T0 + 2.0)


def test_esikler_override_ve_config():
    kon = Konusmaci(esikler={"esikler": {"baskin": 0.7, "erteleme_sn": 5, "bilinmeyen": 1}})
    assert kon.esik["baskin"] == 0.7 and kon.esik["erteleme_sn"] == 5 and "bilinmeyen" not in kon.esik
    assert Konusmaci().esik == kmod.VARSAYILAN_ESIKLER


def test_olay_fn_L_olcumu_ve_metin_yok():
    olaylar = []
    sim = Sim(olay_fn=olaylar.append)
    isinma(sim)
    sim.kos()
    L = [o for o in olaylar if "L_ornek" in o]
    assert L and all("L" in o or "capa" in o for o in L)
    assert not any("r0k" in str(o) for o in olaylar)            # altyazi metni olaylara sizmaz
    assert all("metin" not in a for a in sim.kon.araliklar_listesi())


def test_belirleyici_ayni_girdi_ayni_cikti():
    def kos():
        sim = Sim(seed=3, R_sapma=0.1)
        t = isinma(sim)
        sim.soz(A, t, t + 3.0)
        sim.soz(B, t + 3.3, t + 7.0)
        sim.soz(C, t + 8, t + 12)
        sim.soz(A, t + 9, t + 11)
        sim.otomatik_klipler()
        return sorted((k["klip_no"], k["speaker"], k["spk_guven"]) for k in sim.kos().values())
    assert kos() == kos()


# ---------------------------------------------------------------- rastgele (sabit tohum) ozellik testi

def _rastgele_toplanti(seed):
    rng = random.Random(seed)
    lag0 = (0.3, 2.0, 3.5)[seed % 3]
    sim = Sim(seed=seed, R=rng.choice((0.6, 0.8, 1.0)), R_sapma=0.08)
    t = isinma(sim, lag=lag0)
    onceki = None
    for _ in range(45):
        secenek = [A, B, C, BEN]
        kim = onceki if (onceki and rng.random() < 0.15) else rng.choice([k for k in secenek if k != onceki])
        sure = rng.choice((rng.uniform(0.4, 1.2), rng.uniform(1.5, 5.0), rng.uniform(5.0, 14.0)))
        lag = lag0 + rng.uniform(-0.2, 0.2)
        sim.soz(kim, t, t + sure, lag)
        r = rng.random()
        if r < 0.1:
            t += sure - rng.uniform(0.3, min(1.5, sure))       # cakisma
        elif r < 0.45:
            t += sure + rng.uniform(0.05, 0.4)                  # hizli devir
        else:
            t += sure + rng.uniform(0.5, 2.5)
        onceki = kim
    sim.otomatik_klipler()
    sim.kos()
    return sim


@pytest.mark.parametrize("seed", list(range(16)))
def test_ozellik_kesin_karar_hic_yanlis_degil(seed):
    sim = _rastgele_toplanti(seed)
    assert not yanlis_kesinler(sim)
    kararlar = sim.son_kararlar()
    assert kararlar
    # her bolunen klibin karari ya cocuklari ya da bolunemedi
    for no in sim.bolunen:
        assert no in sim.cocuklar or sim.sonuc[no]["neden"] == "devir_bolunemedi"


def test_ozellik_belirsiz_orani_makul():
    """Kesinlik kapisi icin genel saglik: rastgele toplantilarda kliplerin cogu kesin karar alir."""
    toplam = kesin = 0
    for seed in range(6):
        sim = _rastgele_toplanti(seed)
        for k in sim.son_kararlar().values():
            if k["akis"] == "loopback":
                toplam += 1
                kesin += kesin_mi(k)
    assert toplam and kesin / toplam >= 0.5


# ---------------------------------------------------------------- B0 / B2 / B2b: bosluk, donuk, gecikme, metin ipucu

def _konusma(sim, t, n=40, seed=1, lag=2.0, lag_fn=None, sure=(1.5, 6.0)):
    """n sirali soz (A/B/C, ardisik ayni kisi yok), sureler sure araliginda, aralar 0.6-2.0 sn."""
    rng = random.Random(seed)
    onceki = None
    for _ in range(n):
        kim = rng.choice([k for k in (A, B, C) if k != onceki])
        d = rng.uniform(*sure)
        sim.soz(kim, t, t + d, lag_fn(t) if lag_fn else lag)
        t += d + rng.uniform(0.6, 2.0)
        onceki = kim
    return t


def _lb_kararlar(sim, filt=lambda k, c: True):
    return [(no, k) for no, k in sim.son_kararlar().items()
            if k["akis"] == "loopback" and filt(k, sim.klipler[no])]


def _neden_sayimi(kararlar):
    say = {}
    for _, k in kararlar:
        n = k["neden"].split("+")[0]
        say[n] = say.get(n, 0) + 1
    return say


def _L_izle(sim):
    """kontrol(an) cagrilarinda (an - T0, L) izi."""
    izler = []
    orig = sim.kon.kontrol

    def kontrol(an):
        r = orig(an)
        izler.append((an - T0, sim.kon.L))
        return r

    sim.kon.kontrol = kontrol
    return izler


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_okuma_bosluklari_periyodik_kapsama_yok_degil(seed):
    """Olay kok nedeni: R=0.6 iken her 5 sn'de 1.5-3 sn okuma boslugu (UIA yurumesi). Eski heartbeat (3R=1.8) her
    boslugu kapsama yok sayip satirlari yeniden taban yapiyordu: aralik olusmuyor, her klip kapsama_yok. Kapi: kapsama_yok
    <= %5, aralik sayisi bosluksuz kosunun >= %80'i, kesin yanlis 0, kesin karar bosluksuzun >= %60'i."""
    sonuc = {}
    for bosluk in (False, True):
        sim = Sim(seed=seed, R=0.6)
        if bosluk:
            rng = random.Random(100 + seed)
            for k in range(0, 400, 5):
                g = rng.uniform(1.5, 3.0)
                sim.takili.append((k + 2.0, k + 2.0 + g))
        t = isinma(sim)
        _konusma(sim, t, seed=seed)
        sim.otomatik_klipler()
        sim.kos()
        lb = _lb_kararlar(sim)
        sonuc[bosluk] = (lb, _neden_sayimi(lb), sum(1 for _, k in lb if kesin_mi(k)), sim.kon.durum())
    lb, say, kesin, durum = sonuc[True]
    assert say.get("kapsama_yok", 0) + say.get("panel_donuk", 0) <= 0.05 * len(lb), say
    assert durum["araliklar"] >= 0.8 * sonuc[False][3]["araliklar"], (durum, sonuc[False][3])
    assert kesin >= 0.6 * sonuc[False][2], (kesin, sonuc[False][2])
    assert durum["bosluk_sn"] == 0 and durum["neden"] is None


def test_okuma_bosluklari_kesin_yanlis_yok():
    for seed in range(3):
        sim = Sim(seed=seed, R=0.6)
        rng = random.Random(100 + seed)
        for k in range(0, 400, 5):
            g = rng.uniform(1.5, 3.0)
            sim.takili.append((k + 2.0, k + 2.0 + g))
        t = isinma(sim)
        _konusma(sim, t, seed=seed)
        sim.otomatik_klipler()
        sim.kos()
        assert not yanlis_kesinler(sim), seed


def test_kisa_bosluk_satirlari_korur_karar_tavanli():
    """6 sn'lik okuma boslugu (> bosluk_sn, <= kisa_bosluk_sn): bosluk kaydedilir ama satirlar yeniden taban olmaz;
    bosluga %50'den az denk gelen klip araliklarla karar alir (tavan 0.6, +bosluk, kapsama False); bosluk icindeki klip
    kapsama_yok. durum(): bosluk_sn toplami, bosluk surerken neden okuma_yok."""
    olaylar = []
    sim = Sim(seed=0, olay_fn=olaylar.append)
    t = isinma(sim)
    sim.takili.append((t + 4.0, t + 10.0))
    sim.soz(B, t, t + 14.0)                         # bosluk boyunca da konusuyor
    kenar = sim.klip(t, t + 2.0)                    # penceresinin < %50'si boslukta (bosluk_pay)
    ici = sim.klip(t + 5.0, t + 9.0)                # tamamen boslukta
    sonra = sim.klip(t + 11.0, t + 14.0)
    sim.kos()
    assert any(o["mesaj"] == "okuma boşluğu" and o["sn"] >= 5.5 for o in olaylar)
    k = sim.sonuc[kenar]
    assert k["speaker"] == B and k["spk_guven"] <= 0.6 and "bosluk" in k["neden"] and k["kapsama"] is False
    assert sim.sonuc[ici]["neden"] == "kapsama_yok" and sim.sonuc[ici]["speaker"] == "?"
    assert sim.sonuc[sonra]["speaker"] == B and sim.sonuc[sonra]["kapsama"] is True
    assert 5.5 <= sim.kon.durum()["bosluk_sn"] <= 7.0
    ar = [a for a in sim.kon.araliklar_listesi() if a["kim"] == B]
    assert 1 <= len(ar) <= 2                        # satir korundu: bosluk sonrasi "yeni" sahte aralik yok
    assert all(a["tur"] in ("yeni", "devam", "eski") for a in ar)


def test_bosluk_payi_ve_durum_nedeni():
    kon = Konusmaci()
    t = T0
    for i in range(10):
        _oku(kon, i, t, [[A, "bir"]])
        t += 0.5
    kon.kontrol(t)
    assert kon.durum()["kapsama"] is True and kon.durum()["neden"] is None
    kon.kontrol(t + 8.0)                            # okuma yok (> bosluk_sn)
    d = kon.durum()
    assert d["kapsama"] is False and d["neden"] == "okuma_yok" and d["bosluk_sn"] >= 7.5
    _oku(kon, 20, t + 9.0, [[A, "bir iki"]])
    pay, tur = kon._bosluk_payi(t + 2.0, t + 10.0)
    assert abs(pay - 7.0 / 8.0) < 0.05 and tur == "okuma"
    assert kon._bosluk_payi(T0, T0 + 4.0) == (0.0, None)
    _oku(kon, 21, t + 9.5, [], grup=False)
    kon.kontrol(t + 9.6)
    assert kon.durum()["neden"] == "grup_yok" and kon.durum()["kapsama"] is False


@pytest.mark.parametrize("bayrak", [False, True])
def test_donuk_panel_panel_donuk_kimse_yok(bayrak):
    """Panel 60-110 sn donuk (satirlar degismiyor, hoparlorde konusma var). Kendi tespiti (bayrak False) ya da
    okumadaki 'donuk' bayragi (bayrak True; kendi sure tespiti kapali, son degisimden sonra >= donuk_ses_sn loopback
    konusmasi sarti bayrakta da gecerli). Kapi: pencere icindeki uzak kliplerde panel_donuk >= %90, kimse <= %5;
    pencere bitince adlar doner; loglar panel donuk / canlandi; durum neden panel_donuk."""
    olaylar = []
    esik = {"donuk_sn": 1e9} if bayrak else None
    sim = Sim(seed=1, donuk=(60.0, 110.0), donuk_bayrak=bayrak, esikler=esik, olay_fn=olaylar.append)
    nedenler = []
    orig = sim.kon.kontrol

    def kontrol(an):
        r = orig(an)
        nedenler.append((an - T0, sim.kon.durum()["neden"]))
        return r

    sim.kon.kontrol = kontrol
    t = isinma(sim)
    _konusma(sim, t, n=60, seed=1)
    sim.otomatik_klipler()
    sim.kos()
    ici = _lb_kararlar(sim, lambda k, c: 62 <= c["bas"] - T0 < 108)
    sonra = _lb_kararlar(sim, lambda k, c: c["bas"] - T0 >= 120)
    say = _neden_sayimi(ici)
    assert len(ici) >= 6 and say.get("panel_donuk", 0) >= 0.9 * len(ici), say
    assert say.get("kimse", 0) <= 0.05 * len(ici), say
    assert all(k["cizelge_canli"] is False for _, k in ici if k["neden"] == "panel_donuk")
    assert sonra and all(k["speaker"] not in ("?", "") for _, k in sonra) and not yanlis_kesinler(sim)
    mesajlar = [o["mesaj"] for o in olaylar]
    assert "panel donuk" in mesajlar and "panel canlandı" in mesajlar
    assert any(n == "panel_donuk" for a, n in nedenler if 75 <= a <= 108)
    assert all(n != "panel_donuk" for a, n in nedenler if a >= 125)


def test_sessizlikte_degismeyen_panel_donuk_degil():
    """Hoparlorde konusma yokken panel 40 sn degismez: donuk sayilmaz (kapsama True, neden None)."""
    sim = Sim(seed=0)
    t = isinma(sim)
    nedenler = []
    orig = sim.kon.kontrol

    def kontrol(an):
        r = orig(an)
        nedenler.append(sim.kon.durum()["neden"])
        return r

    sim.kon.kontrol = kontrol
    sim.soz(A, t + 45, t + 48)
    no = sim.klip(t + 45, t + 48)
    sim.kos()
    assert all(n is None for n in nedenler) and sim.sonuc[no]["speaker"] == A


def test_soru_satirlari_hayalet_kimse_degil():
    """Adi okunamayan ('?') satirlar etkinlik sayilir: karar '?' ama neden 'hayalet' (kimse degil), adaylar bos degil,
    kimlik_turu('?') her zaman hayalet (3 satirdan sonra da kisi olmaz)."""
    sim = Sim(seed=0, soru_oran=1.0)
    t = isinma(sim)
    _konusma(sim, t, n=20, seed=2)
    sim.otomatik_klipler()
    sim.kos()
    lb = _lb_kararlar(sim)
    say = _neden_sayimi(lb)
    assert lb and say.get("kimse", 0) == 0 and say.get("hayalet", 0) >= 0.9 * len(lb), say
    assert all(k["speaker"] == "?" and k["adaylar"] and k["adaylar"][0][0] == "?" for _, k in lb if k["neden"] == "hayalet")
    assert sim.kon.kimlik_turu("?") == "hayalet"


def test_kuyruk_metni_uzun_konusmada_buyume_sayilir():
    """Satir en cok 8 kelime gosterir (basi duser, kuyruk kayar): kelime sayisi artmasa da son 2 kelime ve ilk kelime
    degisince buyume -> uzun konusmanin son klibinde de p1 >= 0.6."""
    sim = Sim(seed=0, kuyruk_kelime=8)
    t = isinma(sim)
    sim.soz(B, t, t + 30.0)
    sim.otomatik_klipler()
    sim.kos()
    son = [k for no, k in sorted(sim.son_kararlar().items()) if sim.klipler[no]["bas"] >= T0 + t]
    assert len(son) >= 3
    assert son[-1]["speaker"] == B and son[-1]["adaylar"][0][1] >= 0.6 and son[-1]["spk_guven"] >= 0.7
    # yeniden yazim (kuyruk degisti ama bas ayni) buyume degil
    assert not Konusmaci._kuyruk_buyudu(["bugun", "toplanti", "saat", "ucte"], ["bugun", "toplanti", "saat", "3te"], 4)
    assert Konusmaci._kuyruk_buyudu(["a", "b", "c", "d", "e"], ["c", "d", "e", "f", "g"], 5)


def test_yeniden_cizim_sahte_capa_yok():
    """60. sn'de panel yeniden cizilir (tum satir metinleri farkli): tam uyumsuzluk + >= 2 satir -> hepsi taban,
    o okumada sahte 'yeni' aralik basi (capa adayi) yok; kesin yanlis 0; log."""
    olaylar = []
    sim = Sim(seed=0, yeniden_ciz=60.0, olay_fn=olaylar.append)
    t = isinma(sim)
    _konusma(sim, t, n=40, seed=3)
    sim.otomatik_klipler()
    sim.kos()
    baslar = [b - T0 for b in sim.kon._baslangiclar]
    assert not [b for b in baslar if 60.0 <= b <= 60.0 + 2 * sim.R]
    assert any(o["mesaj"] == "altyazı paneli yeniden çizildi" for o in olaylar)
    assert not yanlis_kesinler(sim)
    sonra = _lb_kararlar(sim, lambda k, c: c["bas"] - T0 >= 65)
    assert sum(1 for _, k in sonra if kesin_mi(k)) >= 0.6 * len(sonra)


def test_gec_yeniden_puanlama_guncelleme():
    """Teams gecikmesi 2 -> 4.2 sn'ye sicrar: ilk kisa klipler, satirlari eski L'ye gore gec kaldigi icin '?' (kimse)
    alir; L basamagi algilaninca 30 sn icinde ayni klip_no ile guncelleme: True karari (dogru ad) gelir."""
    olaylar = []
    sim = Sim(seed=0, olay_fn=olaylar.append)
    t = isinma(sim)
    nolar = []
    for i in range(9):
        kim = (A, B, C)[i % 3]
        sim.soz(kim, t, t + 1.5, lag=4.2)
        nolar.append((sim.klip(t, t + 1.5), kim))
        t += 2.7
    ilk = {}
    gercek_isle = sim._isle

    def isle(kararlar, an, olaylar_, sira):
        for k in kararlar:
            ilk.setdefault(k["klip_no"], k)
        return gercek_isle(kararlar, an, olaylar_, sira)

    sim._isle = isle
    sim.kos()
    no, kim = nolar[0]
    assert ilk[no]["speaker"] == "?" and ilk[no]["neden"] == "kimse" and "guncelleme" not in ilk[no]
    son = sim.sonuc[no]
    assert son["speaker"] == kim and son.get("guncelleme") is True and son["klip_no"] == no
    assert any(o["mesaj"] == "geç yeniden puan" and o["klip_no"] == no for o in olaylar)
    assert abs(sim.kon.durum()["L"] - (4.2 + sim.R / 2)) <= 0.6
    assert not yanlis_kesinler(sim)


def test_yapiskan_yalniz_tek_aday():
    """Altyazisiz kisa loopback klibi: yalniz A'nin araligi 4 sn icinde bitmisse 'A?' (0.6, yapiskan); iki kisi yakinda
    bitirdiyse '?'; kullanicinin altyazisi klibi kapsiyorsa (yanki olasi) yapiskan yok."""
    olaylar = []
    sim = Sim(seed=0, olay_fn=olaylar.append)
    t = isinma(sim)
    sim.soz(A, t, t + 4.0)
    sim.klip(t, t + 4.0)
    no = sim.klip(t + 5.0, t + 6.5)
    sim.kos()
    k = sim.sonuc[no]
    assert k["speaker"] == A and k["spk_guven"] == 0.6 and k["neden"].startswith("yapiskan") and k["kimlik_turu"] == "kisi"
    assert any(o["mesaj"] == "yapışkan" for o in olaylar)
    sim = Sim(seed=0)
    t = isinma(sim)
    sim.soz(A, t, t + 4.0)
    sim.klip(t, t + 4.0)
    sim.soz(B, t + 1.0, t + 4.5)
    sim.klip(t + 1.0, t + 4.5)
    no = sim.klip(t + 5.5, t + 7.0)
    sim.kos()
    assert sim.sonuc[no]["speaker"] == "?" and sim.sonuc[no]["neden"] == "kimse"
    sim = Sim(seed=0)
    t = isinma(sim)
    sim.soz(A, t, t + 4.0)
    sim.klip(t, t + 4.0)
    sim.soz(BEN, t + 5.0, t + 6.5)                  # kullanici konusuyor: loopback'teki ses yankidir
    no = sim.klip(t + 5.0, t + 6.5)
    sim.klip(t + 5.0, t + 6.5, akis="mikrofon")
    sim.kos()
    assert sim.sonuc[no]["speaker"] == "?"


def test_gecikme_7_capraz_ilinti_ile_L_oturur():
    """L 2 sn'ye oturduktan sonra gecikme 7 sn'ye sicrar (> L_ust: zaman capalari pencere disinda atilir); onset'ler
    ile aralik baslarinin capraz ilintisi (iki ardisik degerlendirme) L'yi sifirlar -> sicramadan sonra 120 sn icinde
    |L - 7| <= 0.7, sonra sapmaz, log 'L çapraz ilinti'; kesin yanlis yok."""
    for seed in range(2):
        olaylar = []
        sim = Sim(seed=seed, olay_fn=olaylar.append)
        izler = _L_izle(sim)
        t = isinma(sim, lag=2.0)
        t0 = t
        _konusma(sim, t, n=30, seed=seed, lag=7.0)
        sim.otomatik_klipler()
        sim.kos()
        ilk = next((a for a, L in izler if a >= t0 and abs(L - 7.0) <= 0.7), None)
        assert ilk is not None and ilk - t0 <= 120.0, (seed, ilk)
        assert all(abs(L - 7.0) <= 0.7 for a, L in izler if a >= ilk + 30), seed
        assert any(o["mesaj"] == "L çapraz ilinti" for o in olaylar)
        # yalniz zaman: duzenli sirayla konusmada 2 ve 7 sn'lik gecikme ayni satirlarla aciklanir (ortusme), sicrama
        # ancak capraz ilintiyle (<= 120 sn) algilanir; sonrasinda kesin yanlis yok (aradaki kararlar: acik_kalanlar)
        gec = [x for x in yanlis_kesinler(sim) if sim.klipler[x[0]]["bas"] - T0 >= ilk + 30]
        assert not gec, gec


def test_gecikme_7_isinmada_dogrudan_oturur():
    """Toplanti 7 sn gecikmeyle baslar: isinma penceresi capraz_ust oldugundan zaman capalari (oy coklugu) L'yi
    capraz ilintiye gerek kalmadan 60 sn icinde 7'ye oturtur; kesin yanlis yok."""
    for seed in range(2):
        sim = Sim(seed=seed)
        izler = _L_izle(sim)
        t = isinma(sim, lag=7.0)
        _konusma(sim, t, n=30, seed=seed, lag=7.0)
        sim.otomatik_klipler()
        sim.kos()
        ilk = next((a for a, L in izler if abs(L - 7.0) <= 0.7), None)
        assert ilk is not None and ilk <= 60.0, (seed, ilk)
        assert all(abs(L - 7.0) <= 0.7 for a, L in izler if a >= ilk + 30), seed
        assert not yanlis_kesinler(sim)


def test_gecikme_suruklenme_izlenir():
    """Gecikme 8 dakikada 2 -> 6 sn'ye surukleniyor: her dakika |L - (gecikme + R/2)| <= 0.8, kesin yanlis 0."""
    sim = Sim(seed=1)
    izler = _L_izle(sim)
    t = isinma(sim, lag=2.0)

    def lag_fn(s):
        return 2.0 + 4.0 * min(1.0, max(0.0, (s - 15) / 480.0))

    _konusma(sim, t, n=96, seed=2, lag_fn=lag_fn)
    sim.otomatik_klipler()
    sim.kos()
    for m in range(1, 9):
        an = m * 60.0
        L = [L for a, L in izler if a <= an][-1]
        assert abs(L - (lag_fn(an) + sim.R / 2)) <= 0.8, (m, L, lag_fn(an))
    assert not yanlis_kesinler(sim)


def test_L_yeniden_olc_zorla():
    kon = Konusmaci(olay_fn=lambda d: None)
    for i in range(6):
        o = T0 + 12.0 * i
        kon._onset_gecmis.append((o, 5.0))
        kon._baslangiclar.append(o + 7.0)
        kon._bas_tur.append("yeni")
    kon._ilk_okuma, kon._an = T0, T0 + 70.0
    kon.L_yeniden_olc()                            # ilk degerlendirme: L'den farkli tepe kaydedilir
    assert kon.L == 2.0 and kon._capraz_onceki is not None
    kon.L_yeniden_olc()                            # ikinci: ayni tepe -> L sifirlanir
    assert abs(kon.L - 7.0) <= 0.25 and kon._basamak_kalan > 0


def test_metin_ipucu_bozuk_altyazi_gecikmeyi_capalar():
    """Gecikme 7 (zaman capalari pencere disinda, capraz ilinti kapali): klibin ayirt edici kelimeleri satirda ilk
    gorulunce 'metin' capasi -> L 7'ye oturur; altyazi kelimelerinin %40'i bozukken de; kesin yanlis 0."""
    for bozuk in (0.0, 0.4):
        olaylar = []
        sim = Sim(seed=0, metin=True, bozuk=bozuk, esikler={"capraz_en_az": 999}, olay_fn=olaylar.append)
        t = isinma(sim, lag=7.0)
        _konusma(sim, t, n=20, seed=5, lag=7.0)
        sim.otomatik_klipler()
        sim.kos()
        assert abs(sim.kon.durum()["L"] - 7.0) <= 0.7, (bozuk, sim.kon.durum())
        assert sum(1 for o in olaylar if o["mesaj"] == "L örneği" and o["capa"] == "metin") >= 10
        assert not yanlis_kesinler(sim) and not any("kelime" in str(o) and "r0k" in str(o) for o in olaylar)
    olaylar = []
    sim = Sim(seed=0, metin=True, bozuk=0.4, esikler={"capraz_en_az": 999, "metin_ipucu": 0}, olay_fn=olaylar.append)
    t = isinma(sim, lag=7.0)
    _konusma(sim, t, n=20, seed=5, lag=7.0)
    sim.otomatik_klipler()
    sim.kos()
    assert not any(o["mesaj"] == "L örneği" and o["capa"] == "metin" for o in olaylar)   # ipucu kapali: metin capasi yok


def test_metin_tutarsiz_bayragi():
    """Altyazi satirlari degisiyor ama hicbir klibin kelimesi gorunmuyor (ilgisiz metin): 60 sn sonra
    durum()['tutarsiz'], bir kez log; zaman karari etkilenmez (kesin yanlis 0). Ilgili metinde bayrak yok."""
    olaylar = []
    sim = Sim(seed=0, metin=True, ilgisiz=True, olay_fn=olaylar.append)
    t = isinma(sim)
    _konusma(sim, t, n=30, seed=5)
    sim.otomatik_klipler()
    sim.kos()
    assert sim.kon.durum()["tutarsiz"] is True
    assert sum(1 for o in olaylar if o["mesaj"].startswith("altyazı metni kliplerle tutarsız")) == 1
    assert not yanlis_kesinler(sim)
    sim = Sim(seed=0, metin=True)
    t = isinma(sim)
    _konusma(sim, t, n=30, seed=5)
    sim.otomatik_klipler()
    sim.kos()
    assert sim.kon.durum()["tutarsiz"] is False


def test_metin_ipucu_kapali_yalniz_zaman_sonucu():
    """metin_ipucu=0 ile metinli kosum, metinsiz kosumla ayni kararlari verir."""
    def kos(**kw):
        sim = Sim(seed=2, R_sapma=0.1, **kw)
        t = isinma(sim)
        _konusma(sim, t, n=30, seed=6)
        sim.otomatik_klipler()
        sim.kos()
        return sorted((no, k["speaker"], k["spk_guven"], k["neden"]) for no, k in sim.sonuc.items())
    assert kos(metin=True, esikler={"metin_ipucu": 0}) == kos(metin=False)


def test_metin_esitlik_bozucu_bolunemedi():
    """Zaman iki aday verir (A'nin gecikmesi buyuk, bandi B'nin klibine tasar; kesilemeyen kisa devir): klip metni
    yalniz B'nin satiriyla ortusur -> B, tavan 0.6, neden devir_bolunemedi+metin; ipucu kapaliyken 'A → B'."""
    def kos(ipucu):
        sim = Sim(seed=0, metin=True, esikler={"metin_ipucu": ipucu})
        t = isinma(sim)
        sim.soz(A, t, t + 3.0, lag=3.2)
        sim.klip(t, t + 3.0)
        sim.soz(B, t + 3.3, t + 5.3, lag=2.0)
        no = sim.klip(t + 3.3, t + 5.3)
        sim.kos()
        return sim.sonuc[no]
    k = kos(1)
    assert k["speaker"] == B and k["spk_guven"] <= 0.6 and k["neden"].endswith("+metin"), k
    k0 = kos(0)
    assert k0["speaker"] in (f"{A} → {B}", B) and not k0["neden"].endswith("+metin"), k0


def test_ayirt_edici_kokler():
    kok = kmod._ayirt_edici("Bütçe tablosunu 15 Ekim'de Deniz güncelledi ve bunu için yapı")
    assert "#15" in kok and "@deniz" in kok and "butce" in kok and "tablo" in kok and "gunce" in kok
    assert "icin" not in kok and "bunu" not in kok and "ve" not in kok and not any(len(k) > 6 and k[0] not in "#@" for k in kok)


def test_cizelge_canli_alani():
    """Kapsama tam ve panel klipten sonra degismis -> cizelge_canli True; kapsama yok -> False."""
    sim = Sim(seed=0)
    t = isinma(sim)
    sim.soz(A, t, t + 3.0)
    no = sim.klip(t, t + 3.0)
    sim.takili.append((t + 10, t + 40))
    yok = sim.klip(t + 15, t + 18)
    sim.kos()
    assert sim.sonuc[no]["cizelge_canli"] is True and sim.sonuc[yok]["cizelge_canli"] is False
    assert Konusmaci().bolunemedi(5)["cizelge_canli"] is False


# ---------------------------------------------------------------- yeniden dogrulama bulgulari (G1 / G3 / G4)

def test_isinmada_ayni_kisinin_ardisik_sozleri_uzak_L_tohumlamaz():
    """Bulgu F4: isinmada (capa_min'den az ornek) genis aday penceresi (capraz_ust) ayni kisinin art arda sozlerinde
    satirin duraklama sonrasi periyodik 'devam' baslarini (soz + duraklama ~ 9 sn) capa sanip L'yi ~9'a kilitliyor,
    390 sn boyunca kesin yanlis uretiyordu. Uzak (> L_maks) 'devam' adayi ornek vermez, uzak 'yeni' adayi en az iki
    onset'in oyunu ister: gercek gecikme 3 sn, okuma bosluklu panelde 90 sn icinde |L - (3 + R/2)| <= 0.8, kesin
    yanlis 0."""
    for seed in (0, 1, 2):
        sim = Sim(seed=seed, R=0.6, R_sapma=0.1)
        izler = _L_izle(sim)
        rng = random.Random(seed + 11)
        t = 0.0
        for k in range(60):                                      # her 5 sn'de 1.5-3 sn okuma boslugu (olay bicimi)
            sim.takili.append((k * 5.0 + 2.0, k * 5.0 + 2.0 + rng.uniform(1.5, 3.0)))
        lag = 3.0
        for kim, sure, bosluk in ((A, 4.0, 2.0), (A, 3.5, 2.2), (A, 3.3, 1.8), (B, 3.6, 2.4), (B, 3.0, 2.1),
                                  (C, 2.8, 1.9), (A, 3.2, 2.3), (A, 3.4, 2.0)):
            sim.soz(kim, t, t + sure, lag)
            t += sure + bosluk
        _konusma(sim, t, n=36, seed=seed + 5, lag=lag)
        sim.otomatik_klipler()
        sim.kos()
        hedef = lag + sim.R / 2.0
        gec = [(a, L) for a, L in izler if a >= 90.0]
        assert gec and all(abs(L - hedef) <= 0.8 for a, L in gec), (seed, [x for x in gec if abs(x[1] - hedef) > 0.8][:5])
        assert not yanlis_kesinler(sim), (seed, yanlis_kesinler(sim))


def test_kisa_klip_gec_satirla_onceki_konusmaciya_kesin_atanmaz():
    """Bulgu: kisa klibin (< kisa_klip_bekle_sn) kendi satiri 2-3.5 sn gec belirince klibi yalniz onceki konusmacinin
    (okuma boslugunda uzayan, kaba sonlu) bandi kapliyor ve 1.0 ile kesin yanlis cikiyordu. Karar, ardindaki onset'siz
    satiri aciklayacak klibi bekler (en cok lb_bekle_sn); hala aciklanmayan satir varsa tavan (+gec) ya da metinle gec
    satirin kisisi 'Ad?'; hicbir durumda onceki konusmaci kesin degil, 'A / B' da degil."""
    for metin in (False, True):
        sim = Sim(seed=4, metin=metin, R=0.6)
        t = isinma(sim)
        sim.soz(A, t, t + 5.4)
        sim.soz(B, t + 5.95, t + 6.95, lag=5.0)                 # kisa soz, satiri 3 sn gec belirir
        sim.takili.append((t + 6.5, t + 9.6))                   # A'nin satir sonu okuma boslugunda (kaba son)
        sim.klip(t, t + 5.4)
        no = sim.klip(t + 5.95, t + 6.95)
        sim.kos()
        k = sim.sonuc[no]
        assert not (k["speaker"] == A and k["spk_guven"] >= 0.7), (metin, k)
        assert k["cakisma"] is False and " / " not in k["speaker"], (metin, k)
        assert k["speaker"] in ("?", A, B) and k["spk_guven"] <= 0.6, (metin, k)
        if k["speaker"] == A:
            assert "+gec" in k["neden"] and "+kaba" in k["neden"], k


def test_kaba_bant_ortusmesi_sahte_ayni_anda_vermez():
    """Bulgu: okuma boslugunda biten / baslayan bantlar (kaba uc) klibi iki kisiye de kaplatinca 'A / B' (cakisma)
    deniyordu; panel yeniden ciziminde ve gec satirlarda sahte cakisma 3-4 kat artiyordu. Gercek ust uste binme iki
    bandin KESIN (cekirdek) kismiyla olculur; yalniz kaba banttan gelen ortusmede sirali devir / 'Ad?' kalir."""
    sayim = {"cakisma": 0, "dogru": 0}
    for seed in range(4):
        sim = Sim(seed=seed, R=0.6, R_sapma=0.1)
        t = isinma(sim)
        rng = random.Random(seed + 3)
        kim, onceki = None, None
        klipler = []
        for _ in range(30):
            kim = rng.choice([k for k in (A, B, C) if k != onceki])
            d = rng.uniform(2.5, 6.0)
            sim.soz(kim, t, t + d)
            klipler.append((sim.klip(t, t + d), kim))
            bos = rng.uniform(0.8, 1.6)
            if rng.random() < 0.6:                               # gecis okuma boslugunda (kaba uclar)
                sim.takili.append((t + d + 2.0 - rng.uniform(0.5, 1.5), t + d + bos + 2.0 + rng.uniform(1.0, 2.5)))
            t += d + bos
            onceki = kim
        sim.kos()
        for no, kim in klipler:
            k = sim.son_kararlar()[no]
            if k["cakisma"] or " / " in k["speaker"]:
                sayim["cakisma"] += 1
            elif k["speaker"] == kim:
                sayim["dogru"] += 1
        assert not yanlis_kesinler(sim), (seed, yanlis_kesinler(sim))
    assert sayim["cakisma"] <= 2 and sayim["dogru"] >= 90, sayim
