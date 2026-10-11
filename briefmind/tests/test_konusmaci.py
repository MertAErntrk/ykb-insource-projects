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
                 katilimcilar=KATILIMCI, esikler=None, kulaklik=True, R_sapma=0.0, seed=0, olay_fn=None):
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
        self.klipler = {}
        self.no = 0
        self.sonuc = {}
        self.bolunen = set()
        self.cocuklar = {}
        self.ek_okumalar = []        # (bas, gorunen) elle

    # ---- girdi
    def soz(self, kim, s, e, lag=2.0, satir=None):
        if satir is None:
            satir = len(self.satirlar)
            self.satirlar[satir] = []
        self.satirlar[satir].append(len(self.sozler))
        self.sozler.append({"kim": kim, "s": T0 + s, "e": T0 + e, "lag": lag, "satir": satir})
        return satir

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

    def gorunen(self, t):
        rows = []
        for no, idx in self.satirlar.items():
            n = sum(self._kelime(self.sozler[i], t) for i in idx)
            if n <= 0:
                continue
            ilk = min(self.sozler[i]["s"] + self.sozler[i]["lag"] for i in idx)
            kim = self.sozler[idx[0]]["kim"]
            rows.append((ilk, no, kim, " ".join("r%dk%d" % (no, j) for j in range(n))))
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
                okuma_no += 1
                heapq.heappush(olaylar, (t + self.okuma_suresi, 0, next(sira), "okuma", ok))
            t += self.R + (self.rng.uniform(-self.R_sapma, self.R_sapma) if self.R_sapma else 0.0)
        for k in list(self.klipler.values()):
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


def test_L_5sn_ustu_ornek_atilir():
    sim = Sim()
    t = 0.0
    for kim in (A, B, C, A):
        sim.soz(kim, t, t + 2.0, 6.0)
        sim.klip(t, t + 2.0)
        t += 9.0
    sim.kos()
    d = sim.kon.durum()
    assert d["capa"] == 0 and d["L"] == 2.0 and d["atilan_ornek"] >= 3
    assert all(k["spk_guven"] <= 0.6 for k in sim.sonuc.values())


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
    sim.kos()
    # karar anindaki satir sayisi: 1., 2. klip "?" ; 3. satirdan sonra ad sayilir
    assert sim.sonuc[nolar[0]]["speaker"] == "?" and sim.sonuc[nolar[0]]["neden"] == "hayalet"
    assert sim.sonuc[nolar[-1]]["speaker"] == hayalet
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
