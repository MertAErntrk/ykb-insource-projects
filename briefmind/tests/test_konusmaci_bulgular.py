"""konusmaci.py: Faz 1 dogrulama bulgularinin regresyon testleri (yanki atma, ben adi eslesmesi, L basamagi, hizli
devir, uc donuslu klip). Kucuk sentetik toplanti: her soz ayri panel satiri, okumalar R araliginda, klipler STT
gecikmesiyle gelir; kararlar zemin gercegiyle karsilastirilir. Adlar sentetik; ag / ses / Qt yok."""
import random

from konusmaci import Konusmaci

T0 = 1760000000.0
BEN = "Deniz Kaya"
AYLA, BORA, CEM = "Ayla Demir", "Bora Tan", "Cem Uz"


def okumalar(utt, lag_fn, R=1.2, jit=0.15, seed=1, ad_fn=None, ilk_gec=0.3, panel=6):
    """utt: [(kim, bas, bit, nkel)] (goreli sn); lag_fn(t) -> altyazi gecikmesi."""
    rnd = random.Random(seed)
    son = max(u[2] for u in utt) + 8
    t, no, out = 0.0, 0, []
    while t < son:
        mid = t + 0.05
        rows = []
        for i, (kim, b, e, n) in enumerate(utt):
            gor = b + lag_fn(b) + ilk_gec
            if mid < gor:
                continue
            bitg = e + lag_fn(e)
            k = n if mid >= bitg else 1 + int((n - 1) * (mid - gor) / max(bitg - gor, 0.01))
            rows.append([ad_fn(kim) if ad_fn else kim, " ".join("k%d_%d" % (i, j) for j in range(k))])
        no += 1
        out.append({"tur": "okuma", "okuma_no": no, "bas_epoch": T0 + t, "bit_epoch": T0 + t + 0.12, "grup": True,
                    "gorunen": rows[-panel:]})
        t += R + rnd.uniform(-jit, jit)
    return out


def klipler(utt, ben, hoparlor=False, birles=0.4):
    def birlestir(us):
        out = []
        for kim, b, e, _ in sorted(us, key=lambda u: u[1]):
            if out and b - out[-1]["bit"] < birles:
                out[-1]["bit"] = max(out[-1]["bit"], e)
                out[-1]["kim"].append((kim, b, e))
            else:
                out.append({"bas": b, "bit": e, "kim": [(kim, b, e)]})
        return out
    lb = birlestir([u for u in utt if u[0] != ben])
    kl = [dict(c, akis="loopback") for c in lb]
    km = [dict(c, akis="mikrofon") for c in birlestir([u for u in utt if u[0] == ben])]
    if hoparlor:                                     # hoparlor: uzak sozun yankisi mikrofonda
        km += [dict(bas=c["bas"] + 0.05, bit=c["bit"] + 0.05, kim=[("YANKI", c["bas"], c["bit"])], akis="mikrofon")
               for c in lb]
    hepsi = sorted(kl + km, key=lambda c: c["bas"])
    for i, c in enumerate(hepsi):
        c["klip_no"] = i + 1
    return hepsi


def gercek(c):
    pay = {}
    for kim, b, e in c["kim"]:
        pay[kim] = pay.get(kim, 0) + max(0.0, min(e, c["bit"]) - max(b, c["bas"]))
    return max(pay.items(), key=lambda x: x[1])[0]


def kos(utt, ben=BEN, ben_konfig=None, katilimcilar=(), lag_fn=lambda t: 1.0, R=1.2, hoparlor=False, kulaklik=None,
        seed=1, ad_fn=None):
    """-> (sorunlar [(etiket, klip, karar)], kararlar {klip_no: karar}, konusmaci)."""
    kon = Konusmaci(ben=ben_konfig if ben_konfig is not None else ben, katilimcilar=katilimcilar)
    if kulaklik is not None:
        kon.kulaklik_ayarla(kulaklik)
    kl = klipler(utt, ben, hoparlor=hoparlor)
    ev = [(o["bit_epoch"], 0, o) for o in okumalar(utt, lag_fn, R=R, seed=seed, ad_fn=ad_fn)]
    ev += [(T0 + c["bit"] + 1.0, 1, c) for c in kl]
    ev.sort(key=lambda x: (x[0], x[1]))
    kararlar = {}
    for t, tur, x in ev:
        if tur == 0:
            kon.okuma_ekle(x)
        else:
            kon.klip_ekle({"tur": "klip", "klip_no": x["klip_no"], "akis": x["akis"], "bas": T0 + x["bas"],
                           "bit": T0 + x["bit"]})
        for d in kon.kontrol(t):
            kararlar[d["klip_no"]] = d
    for d in kon.bitir():
        kararlar[d["klip_no"]] = d
    sorun = []
    for c in kl:
        d, g = kararlar[c["klip_no"]], gercek(c)
        if g == "YANKI":
            if not d["at"]:
                sorun.append(("YANKI_KALDI" + ("_KESIN" if d["spk_guven"] >= 0.7 else ""), c, d))
        elif d["at"]:
            sorun.append(("GERCEK_ATILDI", c, d))
        elif d["spk_guven"] >= 0.7 and not (d["speaker"] == g or (c["akis"] == "mikrofon" and g == ben)):
            sorun.append(("KESIN_YANLIS", c, d))
    return sorun, kararlar, kon


def toplanti_karisik(seed, hiz=False):
    rnd = random.Random(seed)
    kisiler = [AYLA, BORA, CEM, BEN]
    utt, t, onceki = [], 2.0, None
    while t < 600:
        kim = rnd.choice([k for k in kisiler if k != onceki])
        if rnd.random() < 0.25:
            d, n = rnd.uniform(0.4, 0.9), 1
        else:
            d = rnd.uniform(1.5, 8)
            n = max(2, int(d * 2.5))
        utt.append((kim, t, t + d, n))
        t += d + (rnd.uniform(0.2, 0.8) if hiz else rnd.uniform(0.6, 2.5))
        onceki = kim
    return utt


def toplanti_onayli(seed, n=60):
    """A/B donusumlu uzun sozler, aralarda digerinin kisa onayi ('evet')."""
    rnd = random.Random(seed)
    utt, t = [], 2.0
    for i in range(n):
        a, b = (AYLA, BORA) if i % 2 == 0 else (BORA, AYLA)
        d = rnd.uniform(4, 9)
        utt.append((a, t, t + d, int(d * 2.5)))
        t += d + rnd.uniform(1.0, 1.4)
        e = rnd.uniform(0.6, 0.9)
        utt.append((b, t, t + e, 1))
        t += e + rnd.uniform(1.0, 1.4)
    return utt


# ---------------------------------------------------------------- yanki: yalniz loopback'te ses varken

def test_toplanti_basi_kullanicinin_kisa_sozu_loopback_sessizken_atilmaz():
    """Bulgu: L henuz 2.0 (gercek ~0.5), kullanicinin 1 sn'lik 'evet'i ardindan 0.3 sn sonra uzak kisi: kullanici bandi
    klibin oncesine kayiyor, uzak bant klibi kapliyor -> at=True (o anda loopback'te ses yok, gercek soz siliniyordu)."""
    utt = [(AYLA, 1.0, 6.0, 12), (BEN, 7.5, 8.5, 1), (AYLA, 8.8, 13.0, 10), (BEN, 15.0, 16.0, 1), (BORA, 16.3, 20.0, 9)]
    for kul in (None, False, True):
        for seed in (1, 2, 3):
            sorun, kr, _ = kos(utt, katilimcilar=(AYLA, BORA, BEN), lag_fn=lambda t: 0.2, kulaklik=kul, seed=seed)
            assert not [s for s in sorun if s[0] == "GERCEK_ATILDI"], (kul, seed, [(s[1]["klip_no"], s[2]) for s in sorun])
            assert kr[2]["speaker"] == BEN and not kr[2]["at"]


def _ucli(seed=5, n=30, kisiler=(AYLA, BEN, BORA)):
    rnd = random.Random(seed)
    utt, t = [], 1.0
    for i in range(n):
        d = rnd.uniform(2, 6)
        utt.append((kisiler[i % len(kisiler)], t, t + d, int(d * 2.5)))
        t += d + rnd.uniform(1.0, 2.0)
    return utt


def test_ben_adi_teams_yazimindan_farkliysa_mikrofon_sozu_atilmaz():
    """Bulgu: Teams 'Kaya, Deniz' gosterirken ben 'Deniz Kaya' (ya da ben 'dkaya' / bos): kullanicinin altyazisi uzak
    kisi sayiliyor, kulaklik None/False iken mikrofondaki her gercek soz 'yanki' diye atiliyordu."""
    utt = _ucli()
    for ben_konfig, ad_fn in (("Deniz Kaya", lambda k: "Kaya, Deniz" if k == BEN else k), ("dkaya", None), ("", None)):
        for kul in (None, False, True):
            sorun, kr, kon = kos(utt, ben_konfig=ben_konfig, ad_fn=ad_fn, katilimcilar=(AYLA, BORA, "Kaya, Deniz"),
                                 kulaklik=kul)
            mik = [d for d in kr.values() if d["akis"] == "mikrofon"]
            assert len(mik) == 10 and not [d for d in mik if d["at"]], (ben_konfig, kul)
    _, kr, kon = kos(utt, ben_konfig="Deniz Kaya", ad_fn=lambda k: "Kaya, Deniz" if k == BEN else k,
                     katilimcilar=(AYLA, BORA), kulaklik=None)
    assert kon.kimlik_turu("Kaya, Deniz") == "ben"
    assert sum(1 for d in kr.values() if d["akis"] == "mikrofon" and d["neden"].startswith("ben_altyazi")) >= 8


def test_ben_esles_sira_virgul_konuk_ve_ayni_ilk_ad():
    kon = Konusmaci(ben="Deniz Kaya", katilimcilar=(AYLA, "Deniz Arslan"))
    assert kon._ben_esles("Deniz Kaya") == 2 and kon._ben_esles("Kaya, Deniz") == 2 and kon._ben_esles("Kaya Deniz") == 2
    assert kon._ben_esles("Deniz Kaya (Siz)") == 2 and kon._ben_esles("Deniz Can Kaya") == 2
    assert kon._ben_esles("Deniz (Konuk)") == 0 and kon.kimlik_turu("Deniz (Konuk)") == "genel"
    assert kon._ben_esles("Deniz (Guest)") == 0 and kon._ben_esles("Deniz Arslan") == 0
    assert kon._ben_esles("Deniz") == 0                       # listede baska 'Deniz ...' var: belirsiz
    assert Konusmaci(ben="Deniz Kaya")._ben_esles("Deniz") == 1  # yalniz ilk ad: zayif
    tek = Konusmaci(ben="Deniz", katilimcilar=(AYLA, BORA, "Deniz Arslan"))
    assert tek._ben_esles("Deniz Arslan") == 0 and tek.kimlik_turu("Deniz Arslan") == "kisi"
    assert Konusmaci(ben="Deniz")._ben_esles("Deniz Kaya") == 1
    assert Konusmaci()._ben_esles("Deniz") == 0


def test_ayni_ilk_adli_uzak_kisi_ve_konuk_kullanici_sayilmaz():
    """Bulgu: ben='Deniz' iken uzak 'Deniz Arslan' (ya da ben 'Deniz Kaya' iken 'Deniz (Konuk)') kullanici sayiliyordu:
    loopback klipleri '?' kaliyor, hoparlorde yankisi 'Deniz' 0.95 KESIN yaziliyordu."""
    for uzak, ben_konfig, katil in (("Deniz Arslan", "Deniz", (AYLA, BORA, "Deniz Arslan")),
                                    ("Deniz (Konuk)", "Deniz Kaya", (AYLA, BORA))):
        utt = _ucli(seed=7, kisiler=(AYLA, uzak, BORA))
        sorun, kr, kon = kos(utt, ben_konfig=ben_konfig, katilimcilar=katil, hoparlor=True, kulaklik=False)
        kl = {c["klip_no"]: c for c in klipler(utt, BEN, hoparlor=True)}
        lb = [d for no, d in kr.items() if d["akis"] == "loopback" and gercek(kl[no]) == uzak]
        assert len(lb) == 10 and all(d["speaker"] == uzak for d in lb), [d["speaker"] for d in lb]
        assert not [s for s in sorun if s[0].startswith("YANKI_KALDI_KESIN")]


def test_hoparlorde_yanki_kesin_ben_almaz_gercek_soz_atilmaz():
    """Bulgu (40 tohum hoparlor): 9 yanki klibi 'ben_altyazi' 0.95 KESIN; loopback'te ses yokken 6 gercek soz atildi."""
    say = {}
    for seed in range(1, 21):
        utt = toplanti_karisik(seed)
        rnd = random.Random(seed * 7)
        sap = {round(u[1], 3): rnd.uniform(-0.4, 0.4) for u in utt}
        sorun, _, _ = kos(utt, katilimcilar=(AYLA, BORA, CEM, BEN), lag_fn=lambda t: 1.5 + sap.get(round(t, 3), 0.0),
                          kulaklik=False, hoparlor=True, seed=seed)
        for s in sorun:
            say[s[0]] = say.get(s[0], 0) + 1
    assert say.get("YANKI_KALDI_KESIN", 0) == 0 and say.get("GERCEK_ATILDI", 0) == 0, say
    assert say.get("YANKI_KALDI", 0) <= 5, say


def test_mikrofon_klibi_loopback_klibiyle_ortusmeden_yanki_atilmaz():
    kon = Konusmaci(ben=BEN, katilimcilar=(AYLA, BEN))
    kon.kulaklik_ayarla(False)
    for o in okumalar([(AYLA, 1.0, 5.0, 10)], lambda t: 1.0):
        kon.okuma_ekle(o)
    kon.klip_ekle({"tur": "klip", "klip_no": 1, "akis": "loopback", "bas": T0 + 1.0, "bit": T0 + 5.0})
    kon.klip_ekle({"tur": "klip", "klip_no": 2, "akis": "mikrofon", "bas": T0 + 1.05, "bit": T0 + 5.05})  # yanki
    kon.klip_ekle({"tur": "klip", "klip_no": 3, "akis": "mikrofon", "bas": T0 + 5.5, "bit": T0 + 6.0})    # sessizde soz
    k = {d["klip_no"]: d for d in kon.bitir()}
    assert k[2]["at"] and not k[3]["at"] and k[3]["speaker"] == BEN


def test_mikrofon_karari_yanki_kaynagi_loopback_klibini_bekler():
    """Yanki karari loopback sesine bakar: mikrofon klibi, ayni araligin loopback klibi gelmeden karara baglanmaz."""
    kon = Konusmaci(ben=BEN, katilimcilar=(AYLA, BEN))
    kon.kulaklik_ayarla(False)
    for o in okumalar([(AYLA, 1.0, 5.0, 10)], lambda t: 1.0):
        kon.okuma_ekle(o)
    kon.klip_ekle({"tur": "klip", "klip_no": 2, "akis": "mikrofon", "bas": T0 + 1.05, "bit": T0 + 5.05})
    assert kon.kontrol(T0 + 9.0) == []                        # loopback klibi henuz yok
    kon.klip_ekle({"tur": "klip", "klip_no": 1, "akis": "loopback", "bas": T0 + 1.0, "bit": T0 + 5.0, "text": ""})
    k = {d["klip_no"]: d for d in kon.kontrol(T0 + 9.5)}
    assert k[2]["at"] and k[2]["neden"].startswith("yanki")


# ---------------------------------------------------------------- L basamagi / saat adimi

def test_gecikme_sicramasinda_kesin_yanlis_yok_denecek_kadar_az():
    """Bulgu: Teams gecikmesi t=200'de 1.0 -> 2.8 sn: kayan medyan gec donuyor, MAD dusuk; 10 tohumda 42 kesin yanlis.
    Belirsiz capa (L_maks icinde birden cok uzak aday) ornek sayilmaz, basamak algilaninca pencere kisalir."""
    top = once = 0
    for seed in range(1, 11):
        sorun, _, _ = kos(toplanti_onayli(seed), katilimcilar=(AYLA, BORA), lag_fn=lambda t: 1.0 if t < 200 else 2.8,
                          seed=seed)
        top += sum(1 for s in sorun if s[0] == "KESIN_YANLIS")
        once += sum(1 for s in sorun if s[0] == "KESIN_YANLIS" and s[1]["bas"] < 200)
    assert once == 0 and top <= 5, top


def test_saat_adiminda_kesin_yanlis_denecek_kadar_az():
    """Bulgu: Windows saati +2 sn adim (okumalar hemen kayar, ses en gec 60 sn sonra capalanir): 20 tohumda 99."""
    top = 0
    for seed in range(1, 21):
        sorun, _, _ = kos(toplanti_onayli(seed), katilimcilar=(AYLA, BORA),
                          lag_fn=lambda t: 1.0 + (2.0 if 200 <= t < 260 else 0.0), seed=seed)
        top += sum(1 for s in sorun if s[0] == "KESIN_YANLIS")
    assert top <= 15, top


def test_L_basamagi_algilanir_pencere_kisalir_tavan():
    log = []
    kon = Konusmaci(olay_fn=log.append)
    for _ in range(20):
        kon._L_ornek_ekle(1.0, "a")
    assert kon.L == 1.0
    for _ in range(5):
        kon._L_ornek_ekle(3.0, "a")
    assert any(d.get("mesaj") == "L basamağı" for d in log)
    assert abs(kon.L - 3.0) < 1e-9 and kon._basamak_kalan > 0
    k = kon._karar_ver({"klip_no": 1, "akis": "loopback", "bas": T0, "bit": T0 + 2.0, "_derinlik": 0}, True)
    assert k["spk_guven"] <= 0.6                                # basamak sonrasi tavan
    # ses saat capasi (fark +2): basamak bu adimdandi -> L geri kayar
    kon.saat_capasi("loopback", 2.0)
    assert abs(kon.L - 1.0) < 1e-9 and kon._onsetler == []
    # tek tuk sapan ornek basamak sayilmaz
    kon2 = Konusmaci(olay_fn=log.append)
    for x in [1.0] * 20 + [3.0, 1.0, 3.0, 1.0, 1.1, 3.0]:
        kon2._L_ornek_ekle(x, "a")
    assert abs(kon2.L - 1.0) < 0.11 and kon2._basamak_kalan == 0


def test_saat_capasi_basamak_yokken_sapan_capalari_atar():
    kon = Konusmaci()
    for x in [1.0] * 10 + [3.0, 3.0]:
        kon._L_ornek_ekle(x, "a")
    assert kon._basamak_kalan == 0
    kon.saat_capasi("loopback", 2.0)
    assert list(kon._L_ornek) == [1.0] * 10 and kon._basamak_kalan > 0
    kon.klip_ekle({"tur": "capa", "akis": "mikrofon", "fark": 0.1})    # kucuk fark: yok sayilir
    assert list(kon._L_ornek) == [1.0] * 10


# ---------------------------------------------------------------- hizli devir, kisa klip

def test_hizli_devirde_kisa_klip_sonraki_konusmaciya_kesin_atanmaz():
    """Bulgu: hizli devirde tek kelimelik kisa klip (0.5-0.9 sn) okuma nicemlemesi (0..R) yuzunden sonraki
    konusmaciya KESIN ataniyordu (40 tohumda 11)."""
    top = 0
    for seed in range(1, 21):
        utt = toplanti_karisik(seed, hiz=True)
        sorun, _, _ = kos(utt, katilimcilar=(AYLA, BORA, CEM, BEN), lag_fn=lambda t: 1.5, kulaklik=True, seed=seed)
        top += sum(1 for s in sorun if s[0] == "KESIN_YANLIS")
    assert top == 0, top


# ---------------------------------------------------------------- uc donuslu klip

def test_uc_donuslu_klipte_sinir_ortusmeleri_toplanip_ayni_anda_denmez():
    """Bulgu: A->B->A klibinde (bosluklar 0.3 sn) iki sinirin bant ortusmeleri toplanip tol'u asiyor, gercek ust uste
    binme yokken 'A / B' cakisma=True cikiyordu. Ortusme sinir basina olculur."""
    sahte = 0
    for seed in range(1, 11):
        rnd = random.Random(seed)
        utt, t = [], 2.0
        for i in range(12):
            a, b = (AYLA, BORA) if i % 2 == 0 else (BORA, AYLA)
            for kim, d in ((a, rnd.uniform(2.5, 4.0)), (b, rnd.uniform(1.5, 2.5)), (a, rnd.uniform(2.5, 4.0))):
                utt.append((kim, t, t + d, int(d * 2.5)))
                t += d + rnd.uniform(0.1, 0.35)
            t += rnd.uniform(1.5, 2.5)
        _, kr, _ = kos(utt, katilimcilar=(AYLA, BORA), lag_fn=lambda t: 1.0, seed=seed)
        sahte += sum(1 for d in kr.values() if d["cakisma"])
    assert sahte == 0, sahte


# ---------------------------------------------------------------- okuma bosluklari (gercek toplanti bulgusu)

def test_her_5_snde_okuma_boslugu_kapsama_yok_vermez():
    """Bulgu (gercek toplanti): R=0.61 iken okuma dongusundeki 5 sn'lik UIA yurumesi 1.8 sn'lik heartbeat'i asip her
    5 sn'de bosluk aciyordu; her bosluktan sonra satirlar taban sayilip ust sifirlaniyor, aralik hic olusmuyordu:
    347 satirin 298'i kapsama_yok, 45'i kimse. Esik max(3R, 5 sn) ve satirlarin korunmasiyla: kapsama_yok <= %5,
    kesin yanlis 0, kesin karar bosluksuz kosunun >= %60'i, aralik sayisi >= %80'i."""
    def kos_bosluklu(bosluk):
        utt = toplanti_karisik(3)
        kon = Konusmaci(ben=BEN, katilimcilar=(AYLA, BORA, CEM, BEN))
        kon.kulaklik_ayarla(True)
        kl = klipler(utt, BEN)
        rnd = random.Random(9)
        delikler = []
        t = 2.0
        while t < 620:
            delikler.append((t, t + rnd.uniform(1.5, 3.0)))
            t += 5.0
        oku = okumalar(utt, lambda t: 1.0, R=0.6, jit=0.05)
        if bosluk:
            oku = [o for o in oku if not any(a <= o["bas_epoch"] - T0 < b for a, b in delikler)]
        ev = [(o["bit_epoch"], 0, o) for o in oku]
        ev += [(T0 + c["bit"] + 1.0, 1, c) for c in kl]
        ev.sort(key=lambda x: (x[0], x[1]))
        kararlar = {}
        for t, tur, x in ev:
            if tur == 0:
                kon.okuma_ekle(x)
            else:
                kon.klip_ekle({"tur": "klip", "klip_no": x["klip_no"], "akis": x["akis"], "bas": T0 + x["bas"],
                               "bit": T0 + x["bit"]})
            for d in kon.kontrol(t):
                kararlar[d["klip_no"]] = d
        for d in kon.bitir():
            kararlar[d["klip_no"]] = d
        lb = [(c, kararlar[c["klip_no"]]) for c in kl if c["akis"] == "loopback"]
        yanlis = [d for c, d in lb if d["spk_guven"] >= 0.7 and d["speaker"] != gercek(c)]
        kesin = sum(1 for c, d in lb if d["spk_guven"] >= 0.7 and d["speaker"] == gercek(c))
        yok = sum(1 for _, d in lb if d["neden"] in ("kapsama_yok", "panel_donuk"))
        return len(lb), yok, kesin, yanlis, kon.durum()

    n0, yok0, kesin0, yanlis0, d0 = kos_bosluklu(False)
    n1, yok1, kesin1, yanlis1, d1 = kos_bosluklu(True)
    assert n1 == n0 and not yanlis0 and not yanlis1, (yanlis0, yanlis1)
    assert yok1 <= 0.05 * n1, (yok1, n1)
    assert kesin1 >= 0.6 * kesin0, (kesin1, kesin0)
    assert d1["araliklar"] >= 0.8 * d0["araliklar"], (d1, d0)
    assert d1["bosluk_sn"] == 0 and d1["neden"] is None


def test_gercek_cakismada_metin_esitlik_bozucu_uygulanmaz():
    """Gercek ust uste binme (iki bant da klibi kapsiyor): klip metni yalniz B'nin satiriyla ortusse de karar 'A / B'
    (cakisma=True, ⟨⟩ korumasi) kalir; metin esitlik bozucu yalniz devir / zayif payda (urun kurali: karisik metinli
    cakisma satiri tek ada indirgenmez)."""
    import test_konusmaci as T
    for ipucu in (1, 0):
        sim = T.Sim(seed=0, metin=True, esikler={"metin_ipucu": ipucu})
        t = T.isinma(sim)
        sim.soz(T.A, t, t + 6.0)
        sim.soz(T.B, t + 1.0, t + 5.5)
        no = sim.klip(t, t + 6.0)
        zb = sim.sozler[-1]
        sim.klipler[no]["text"] = " ".join(zb["kelimeler"])          # metin yalniz B'nin kelimeleri
        sim.kos()
        k = sim.sonuc[no]
        assert k["cakisma"] is True and k["speaker"] == f"{T.A} / {T.B}" and k["bol"] is None, (ipucu, k)
        assert k["neden"] == "cakisma" and k["spk_guven"] <= 0.5
