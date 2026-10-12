"""cihaz.py: otomatik mikrofon secimi + loopback karisimi, sahte arka uc ile (ses cihazi yok; senkron mod: her tur her cihazdan
bir 100 ms blok, saat sahte)."""
import collections
import importlib
import itertools
import math
import queue
import sys
import threading
import time
import types

import numpy as np
import pytest

import cihaz
import ses

T0 = 1_700_000_000.0
SONSUZ = float("inf")


def _sev(db):
    return 10.0 ** (db / 20.0)


def _db(x):
    x = np.asarray(x, dtype=np.float64)
    return 20 * math.log10(max(float(np.sqrt(np.mean(x * x))), 1e-12))


def _icinde(t, araliklar):
    return any(a <= t < b for a, b in araliklar)


class Sinyal:
    """Bir cihazin duydugu ses: gurultu tabani + kullanici konusmasi + (hoparlorden) karsi taraf. t: blogun bitis ani
    (goreli sn); blok [t-0.1, t] araliginin ortasina bakilir. sessiz=True: tam sifir (izin kapali / sessize alinmis)."""

    def __init__(self, taban_db=-70.0, kullanici=(), kullanici_db=-25.0, uzak=(), uzak_db=None, sessiz=(), seed=0):
        self.taban_db, self.kullanici, self.kullanici_db = taban_db, list(kullanici), kullanici_db
        self.uzak, self.uzak_db = list(uzak), uzak_db
        self.sessiz = [(-SONSUZ, SONSUZ)] if sessiz is True else list(sessiz or ())
        self.rng = np.random.default_rng(seed)

    def blok(self, t):
        orta = t - 0.05
        if _icinde(orta, self.sessiz):
            return np.zeros(cihaz.BLOK, dtype=np.float32)
        x = self.rng.standard_normal(cihaz.BLOK) * _sev(self.taban_db)
        if self.kullanici_db is not None and _icinde(orta, self.kullanici):
            x = x + self.rng.standard_normal(cihaz.BLOK) * _sev(self.kullanici_db)
        if self.uzak_db is not None and _icinde(orta, self.uzak):
            x = x + self.rng.standard_normal(cihaz.BLOK) * _sev(self.uzak_db)
        return x.astype(np.float32)


class SahteCihaz:
    def __init__(self, kimlik, ad, sinyal, bas=-SONSUZ, bit=SONSUZ, durgun=()):
        self.kimlik, self.ad, self.sinyal, self.bas, self.bit, self.durgun = kimlik, ad, sinyal, bas, bit, list(durgun)

    def var(self, t):
        return self.bas <= t < self.bit


class SahteKayit:
    def __init__(self, arka, c):
        self.arka, self.c = arka, c
        self.kapandi = False

    def oku(self):
        t = self.arka.goreli()
        if not self.c.var(t):
            raise OSError("cihaz çıkarıldı")
        if _icinde(t - 0.05, self.c.durgun):
            return None                                  # bu turda blok yok (surucu takildi)
        return self.c.sinyal.blok(t)

    def kapat(self):
        self.kapandi = True


class SahteArka(cihaz.Arka):
    """Senkron sahte arka uc. otomatik=True: her secici turunda saat 0.1 sn ilerler (tek secici + AkisYakalayici.run);
    False: saati test ilerletir (iki secici ayni anda). dur/bitis_n: bu kadar turdan sonra dur set edilir."""
    senkron = True

    def __init__(self, girisler=(), cikislar=(), vg=None, vc=None, otomatik=False, bitis_n=None, dur=None):
        self.g, self.c = list(girisler), list(cikislar)
        self.vg, self.vc = vg, vc
        self.otomatik, self.bitis_n, self.dur = otomatik, bitis_n, dur
        self.n = 0
        self.acilan = []

    def goreli(self):
        return self.n * 0.1

    def simdi(self):
        return T0 + self.n * 0.1

    def tik(self, secici):
        if self.otomatik:
            self.n += 1
            if self.bitis_n is not None and self.n >= self.bitis_n and self.dur is not None:
                self.dur.set()

    def _var(self, liste):
        t = self.goreli()
        return [x for x in liste if x.var(t)]

    def girisler(self):
        return [(x.kimlik, x.ad) for x in self._var(self.g)]

    def cikislar(self):
        return [(x.kimlik, x.ad) for x in self._var(self.c)]

    def varsayilan_giris(self):
        v = self.vg(self.goreli()) if callable(self.vg) else self.vg
        return v if v in [k for k, _ in self.girisler()] else None

    def varsayilan_cikis(self):
        v = self.vc(self.goreli()) if callable(self.vc) else self.vc
        return v if v in [k for k, _ in self.cikislar()] else None

    def _ac(self, liste, kimlik):
        for x in self._var(liste):
            if x.kimlik == kimlik:
                self.acilan.append(kimlik)
                return SahteKayit(self, x)
        raise OSError(f"yok: {kimlik}")

    def ac_giris(self, kimlik):
        return self._ac(self.g, kimlik)

    def ac_loopback(self, kimlik):
        return self._ac(self.c, kimlik)


class Kayit:
    """Olay toplayici: (goreli_an, tip, veri)."""

    def __init__(self, arka):
        self.arka, self.olaylar = arka, []

    def __call__(self, tip, veri):
        self.olaylar.append((round(self.arka.goreli(), 2), tip, veri))

    def loglar(self, bas=""):
        return [(t, v) for t, tip, v in self.olaylar if tip == "log" and str(v).startswith(bas)]

    def tip(self, tip):
        return [(t, v) for t, tp, v in self.olaylar if tp == tip]


def _surdur(arka, seciciler, sn, klip_acik_fn=None, rapor=None):
    """Elle saat: her turda saat 0.1 ilerler, her secici bir tur isler. -> {secici: [cikti...]} (bloklar ve Gecis).
    rapor (dict): kapanmadan once {secici: durum_raporu()} yazilir."""
    dur = threading.Event()
    gl = [(s, s.bloklar(dur, klip_acik_fn=klip_acik_fn)) for s in seciciler]
    out = {s: [] for s in seciciler}
    for _ in range(int(round(sn * 10))):
        arka.n += 1
        for s, g in gl:
            while True:
                x = next(g)
                if x is None:
                    break
                out[s].append((round(arka.goreli(), 2), x))
    if rapor is not None:
        rapor.update({s: s.durum_raporu() for s in seciciler})
    dur.set()
    for _, g in gl:
        g.close()
    return out


def _bloklar(cikti):
    return [x for _, x in cikti if not isinstance(x, cihaz.Gecis)]


def _gecisler(cikti):
    return [(t, x) for t, x in cikti if isinstance(x, cihaz.Gecis)]


def _konusma(bas, bit, sure, ara):
    """[bas, bit) icinde 'sure' sn konus, 'ara' sn sus."""
    out, t = [], bas
    while t < bit:
        out.append((t, min(bit, t + sure)))
        t += sure + ara
    return out


# ---------------------------------------------------------------- ilk secim

def test_sessiz_varsayilan_konusan_kulaklik_secilir_ses_kaybolmaz():
    """Kullanicinin dizustusunde olan: Windows varsayilan mikrofonu -98 dB (tam sessiz), kulaklik ses aliyor."""
    konus = [(0.5, 4.0), (6.0, 9.0)]
    arka = SahteArka(girisler=[SahteCihaz("D", "Mikrofon Dizisi", Sinyal(sessiz=True)),
                               SahteCihaz("H", "Kulaklık", Sinyal(-70, konus, -25, seed=1))], vg="D")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    out = _surdur(arka, [m], 10)[m]
    assert m.secili == "H"
    assert m.secili_mikrofon() == {"kimlik": "H", "ad": "Kulaklık", "otomatik": True, "neden": "Windows varsayılanı sessiz",
                                   "sessizde": False}
    assert [v for _, v in k.loglar("Mikrofon:")] == ["Mikrofon: Kulaklık (otomatik) — Windows varsayılanı sessiz"]
    assert k.tip("mikrofon")[0][1]["ad"] == "Kulaklık"
    # karar 1 sn bekledi ama bloklar tamponlandi: mantiksal akis kesintisiz, ilk konusma da icinde
    bl = _bloklar(out)
    assert len(bl) == 100
    assert _db(np.concatenate(bl[6:40])) > -35
    g = _gecisler(out)
    assert len(g) == 1 and g[0][1].kapat is False and g[0][1].an == pytest.approx(T0 + 0.1)


def test_varsayilan_ses_veriyorsa_hemen_secilir():
    arka = SahteArka(girisler=[SahteCihaz("L", "Dizüstü", Sinyal(-62, seed=2)),
                               SahteCihaz("H", "Kulaklık", Sinyal(-70, seed=3))], vg="L")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    out = _surdur(arka, [m], 2)[m]
    assert m.secili == "L" and _gecisler(out)[0][0] == pytest.approx(0.1)
    assert k.loglar("Mikrofon:") == [(0.1, "Mikrofon: Dizüstü (otomatik)")]
    assert m.tavan == cihaz.MIK_TAVAN


def test_en_fazla_dort_mikrofon_acilir_loopback_sahte_cihazi_yok():
    arka = SahteArka(girisler=[SahteCihaz(f"M{i}", f"Mik {i}", Sinyal(-60, seed=i)) for i in range(6)], vg="M5")
    m = cihaz.MikrofonSecici(arka, saat=arka.simdi)
    _surdur(arka, [m], 1)
    assert len(arka.acilan) == 4 and arka.acilan[0] == "M5"          # varsayilan once


# ---------------------------------------------------------------- tak-cikar

def test_toplanti_ortasinda_kulaklik_takilinca_gecilir():
    konus = _konusma(0.5, 60, 2.5, 1.5)
    arka = SahteArka(girisler=[SahteCihaz("L", "Dizüstü Mikrofon", Sinyal(-60, konus, -45, seed=4)),
                               SahteCihaz("H", "Kulaklık", Sinyal(-75, konus, -28, seed=5), bas=20.0)], vg="L")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    out = _surdur(arka, [m], 45)[m]
    assert m.secili == "H" and m.gecis_sayisi == 1
    t, g = _gecisler(out)[-1]
    assert 21.0 <= t <= 32.0 and g.kapat is True and g.kimlik == "H"
    assert k.loglar("Mikrofon: Kulaklık (otomatik)")[0][1].startswith("Mikrofon: Kulaklık (otomatik) — konuşma ")
    assert any("açıldı: Kulaklık" in v for _, v in k.loglar("  · "))
    assert len(_bloklar(out)) == 450                                  # sayac kesintisiz (tur basina bir blok)


def test_secili_cihaz_cikarilinca_hemen_gecilir_blok_kaybi_yok():
    konus = _konusma(0.5, 30, 2, 1)
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-75, konus, -28, seed=6), bit=15.0),
                               SahteCihaz("L", "Dizüstü", Sinyal(-60, konus, -45, seed=7))], vg="H")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    out = _surdur(arka, [m], 25)[m]
    assert m.secili == "L"
    g = _gecisler(out)
    assert [(round(t, 1), x.kimlik, x.kapat) for t, x in g] == [(0.1, "H", False), (15.0, "L", True)]
    assert g[1][1].neden == "önceki cihaz kayboldu"
    assert len(_bloklar(out)) == 250
    assert any("kapandı: Kulaklık (hata: OSError" in v for _, v in k.loglar("  · "))


def test_sesli_calisan_secili_cihaz_tam_sifira_duserse_sessize_alma_sayilir_gecilmez():
    """Teams sertifikali kulaklik sessize alininca tam sifir verir: dizustune gecilmez (sessizdeyken odada konusulan
    transkripte girmez); 20 sn sonra bir kez 'sessizde' bildirilir, ses donunce kalkar."""
    konus = _konusma(0.5, 60, 2, 1)
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-75, konus, -28, sessiz=[(10, 40)], seed=8)),
                               SahteCihaz("L", "Dizüstü", Sinyal(-60, konus, -45, seed=9))], vg="H")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    out = _surdur(arka, [m], 50)[m]
    assert m.secili == "H" and m.gecis_sayisi == 0 and len(_gecisler(out)) == 1
    bl = _bloklar(out)
    assert len(bl) == 500 and _db(np.concatenate(bl[105:400])) < -200        # sessizdeyken akisa dizustu sesi girmedi
    s = [(round(t), v["sessizde"]) for t, v in k.tip("mikrofon")]
    assert s == [(0, False), (30, True), (40, False)]
    assert len(k.loglar("Mikrofon sessizde: Kulaklık")) == 1 and k.loglar("✔ Mikrofon yeniden ses veriyor")
    assert not k.tip("ses_uyari")                                      # izin uyarisi degil


def test_hic_ses_vermemis_secili_cihaz_tam_sessizse_hemen_gecilir():
    """Ilk anda baska ses yoktu, secilen varsayilan hic ses vermedi: kulaklik ses verince hemen ona gecilir."""
    arka = SahteArka(girisler=[SahteCihaz("D", "Mikrofon Dizisi", Sinyal(sessiz=True)),
                               SahteCihaz("H", "Kulaklık", Sinyal(-70, [(6, 9)], -25, sessiz=[(-1, 5)], seed=8))], vg="D")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    out = _surdur(arka, [m], 10)[m]
    t, g = _gecisler(out)[-1]
    assert g.kimlik == "H" and 5.0 <= t <= 5.3 and g.neden == "önceki cihaz tam sessiz"


# ---------------------------------------------------------------- eko, iki mikrofon

def _eko_duzenegi(uzak_bagla):
    kullanici = [(k * 10 + 1, k * 10 + 3) for k in range(8)]
    uzak = [(k * 10 + 4, k * 10 + 9.5) for k in range(8)]
    # kulaklik mikrofonu da hoparlor sesini hafifce duyar (iki cihaz birden yukselir: tek cihaz sicramasi sayilmaz)
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-75, kullanici, -30, uzak, -62, seed=10)),
                               SahteCihaz("L", "Dizüstü Mikrofon", Sinyal(-60, kullanici, -50, uzak, -25, seed=11))],
                     cikislar=[SahteCihaz("S", "Hoparlör", Sinyal(-200, (), None, uzak, -20, sessiz=[], seed=12))],
                     vg="H", vc="S")
    k = Kayit(arka)
    lp = cihaz.LoopbackSecici(arka, olay=k, saat=arka.simdi)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi, uzak_fn=lp.aktif_mi if uzak_bagla else None)
    _surdur(arka, [lp, m], 70)
    return m


def test_hoparlorden_gelen_eko_mikrofon_secimini_kazanmaz():
    """Kulaklik mikrofonu secili; Teams sesi dizustu hoparlorunden cikiyor, dizustu mikrofonu karsi tarafi yuksek duyuyor.
    Karsi taraf konusurken (loopback aktif) kareler puanlanmaz: eko kazanamaz."""
    m = _eko_duzenegi(uzak_bagla=True)
    assert m.secili == "H" and m.gecis_sayisi == 0


def test_eko_korumasi_olmasa_dizustu_kazanirdi():
    m = _eko_duzenegi(uzak_bagla=False)
    assert m.secili == "L" and m.gecis_sayisi >= 1


def test_iki_mikrofon_kullaniciyi_duyuyor_daha_net_olan_kazanir_gidip_gelmez():
    konus = _konusma(0.5, 120, 3, 1)
    arka = SahteArka(girisler=[SahteCihaz("A", "Dizüstü", Sinyal(-60, konus, -42, seed=13)),
                               SahteCihaz("B", "USB Kulaklık", Sinyal(-70, konus, -35, seed=14))], vg="A")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    out = _surdur(arka, [m], 100)[m]
    assert m.secili == "B" and m.gecis_sayisi == 1
    t = _gecisler(out)[-1][0]
    assert 3.0 <= t <= 6.0                                             # ilk secim bekleme baslatmaz: 3 sn ortak konusma


def test_yakin_puanli_iki_mikrofonda_gecis_olmaz():
    konus = _konusma(0.5, 120, 3, 1)
    arka = SahteArka(girisler=[SahteCihaz("A", "Dizüstü", Sinyal(-60, konus, -42, seed=15)),
                               SahteCihaz("B", "USB", Sinyal(-60, konus, -38, seed=16))], vg="A")
    m = cihaz.MikrofonSecici(arka, saat=arka.simdi)
    _surdur(arka, [m], 100)
    assert m.secili == "A" and m.gecis_sayisi == 0                     # 4 dB < 6 dB esik


# ---------------------------------------------------------------- loopback karisimi

def test_teams_cikisi_yeni_cihaza_tasininca_loopback_yakalamaya_devam_eder():
    uzak_s = [(1.0, 10.0)]
    uzak_k = [(10.0, 35.0)]
    arka = SahteArka(cikislar=[SahteCihaz("S", "Hoparlör", Sinyal(-200, (), None, uzak_s, -20, seed=17), bit=25.0),
                               SahteCihaz("K", "Kulaklık", Sinyal(-200, (), None, uzak_k, -20, seed=18), bas=5.0)],
                     vc="S")
    k = Kayit(arka)
    lp = cihaz.LoopbackSecici(arka, olay=k, saat=arka.simdi)
    out = _surdur(arka, [lp], 32)[lp]
    bl = _bloklar(out)
    assert len(bl) == 320                                              # her tur bir karisim blogu (eksik = sifir)
    for bas, bit in ((2, 9), (11, 24), (26, 31)):
        assert _db(np.concatenate(bl[bas * 10:bit * 10])) > -25, (bas, bit)
    assert all(float(np.max(np.abs(b))) <= 1.0 for b in bl)
    loglar = [v for _, v in k.loglar("Hoparlör yakalama:")]
    assert loglar == ["Hoparlör yakalama: Hoparlör", "Hoparlör yakalama: Hoparlör + Kulaklık (karışım)",
                      "Hoparlör yakalama: Kulaklık"]
    g = _gecisler(out)
    assert [(round(t, 1), x.kimlik, x.kapat) for t, x in g] == [(0.1, "S", False), (25.0, "K", False)]
    assert lp.aktif_mi(T0 + 12.0, T0 + 12.5) and not lp.aktif_mi(T0 + 0.1, T0 + 0.8)


def test_karisim_toplanip_kirpilir():
    arka = SahteArka(cikislar=[SahteCihaz("S", "A", Sinyal(-200, (), None, [(0, 9)], -3, seed=19)),
                               SahteCihaz("K", "B", Sinyal(-200, (), None, [(0, 9)], -3, seed=20))], vc="S")
    lp = cihaz.LoopbackSecici(arka, saat=arka.simdi)
    bl = _bloklar(_surdur(arka, [lp], 2)[lp])
    x = np.concatenate(bl)
    assert float(np.max(np.abs(x))) == pytest.approx(1.0)              # iki -3 dB kaynak toplami tepede kirpildi


# ---------------------------------------------------------------- uyarilar

def test_tum_mikrofonlar_sessizse_tek_uyari_ses_gelince_kalkar():
    arka = SahteArka(girisler=[SahteCihaz("D", "Mikrofon Dizisi", Sinyal(sessiz=True)),
                               SahteCihaz("E", "Kulaklık", Sinyal(-65, [(31, 40)], -30, sessiz=[(-1, 30)], seed=21))],
                     vg="D")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    _surdur(arka, [m], 45)
    u = k.tip("ses_uyari")
    assert [(round(t), v["var"]) for t, v in u] == [(10, True), (30, False)]
    assert "Gizlilik ve güvenlik → Mikrofon" in u[0][1]["metin"] and u[0][1]["tur"] == "mikrofon"
    assert len([v for _, v in k.loglar("! Mikrofondan ses gelmiyor")]) == 1
    assert k.loglar("✔ Mikrofon sesi geldi")
    assert m.secili == "E" and m.uyari is None


def test_mikrofon_hic_yoksa_uyari_metni_farkli():
    arka = SahteArka()
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    _surdur(arka, [m], 12)
    assert [v["metin"] for _, v in k.tip("ses_uyari")] == [cihaz.MIK_YOK_METNI]


def test_altyazida_konusma_varken_loopback_sessizse_uyari():
    arka = SahteArka(cikislar=[SahteCihaz("S", "Hoparlör", Sinyal(-200, (), None, [(30, 40)], -20, sessiz=[(0, 30)]))],
                     vc="S")
    k = Kayit(arka)
    altyazi = {"son": None}
    lp = cihaz.LoopbackSecici(arka, olay=k, saat=arka.simdi, altyazi_fn=lambda: altyazi["son"])
    dur = threading.Event()
    g = lp.bloklar(dur)
    for i in range(400):
        arka.n += 1
        if 50 <= i:                                                    # 5. sn'den sonra karsi taraf altyazida konusuyor
            altyazi["son"] = arka.simdi() - 1.0
        while next(g) is not None:
            pass
    dur.set()
    g.close()
    u = k.tip("ses_uyari")
    assert [(round(t), v["var"], v["tur"]) for t, v in u] == [(25, True, "loopback"), (30, False, "loopback")]
    assert len(k.loglar("! Teams'te konuşan var")) == 1


def test_altyazi_yoksa_loopback_uyarisi_yok():
    arka = SahteArka(cikislar=[SahteCihaz("S", "Hoparlör", Sinyal(sessiz=True))], vc="S")
    k = Kayit(arka)
    lp = cihaz.LoopbackSecici(arka, olay=k, saat=arka.simdi, altyazi_fn=lambda: None)
    _surdur(arka, [lp], 40)
    assert not k.tip("ses_uyari")


# ---------------------------------------------------------------- elle ayar

def test_elle_ayar_sessizse_otomatige_duser_ses_gelince_doner():
    konus = _konusma(0.5, 60, 2, 1)
    arka = SahteArka(girisler=[SahteCihaz("L", "Dizüstü Mikrofon", Sinyal(-60, konus, -45, seed=22)),
                               SahteCihaz("U", "USB Mikrofon (Realtek)", Sinyal(-65, konus, -35, sessiz=[(-1, 20)], seed=23))],
                     vg="L")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, elle="USB Mikrofon", olay=k, saat=arka.simdi)
    _surdur(arka, [m], 30)
    loglar = [v for _, v in k.loglar("Mikrofon:")]
    assert loglar[0] == "Mikrofon: Dizüstü Mikrofon (otomatik) — ayarlanan mikrofon sessiz, otomatik seçildi"
    assert loglar[1] == "Mikrofon: USB Mikrofon (Realtek) (elle ayarlı) — ayarlanan mikrofon yeniden ses veriyor"
    assert m.secili == "U" and m.secili_mikrofon()["otomatik"] is False


def test_elle_ayar_calisiyorsa_otomatik_secim_kapali():
    konus = _konusma(0.5, 90, 3, 1)
    arka = SahteArka(girisler=[SahteCihaz("L", "Dizüstü Mikrofon", Sinyal(-60, konus, -50, seed=24)),
                               SahteCihaz("H", "Kulaklık", Sinyal(-75, konus, -25, seed=25))], vg="H")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, elle="dizüstü", olay=k, saat=arka.simdi)
    _surdur(arka, [m], 60)
    assert m.secili == "L" and m.gecis_sayisi == 0
    assert [v for _, v in k.loglar("Mikrofon:")] == ["Mikrofon: Dizüstü Mikrofon (elle ayarlı)"]


def test_elle_ayar_secili_iken_cikarilirsa_otomatige_gecer_sessize_alinirsa_kalir():
    konus = _konusma(0.5, 30, 2, 1)
    arka = SahteArka(girisler=[SahteCihaz("U", "USB", Sinyal(-65, konus, -35, seed=26), bit=10.0),
                               SahteCihaz("L", "Dizüstü", Sinyal(-60, konus, -45, seed=27))], vg="L")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, elle="USB", olay=k, saat=arka.simdi)
    _surdur(arka, [m], 15)
    assert m.secili == "L"
    assert k.loglar("Mikrofon:")[-1][1] == ("Mikrofon: Dizüstü (otomatik) — önceki cihaz kayboldu; ayarlanan mikrofon "
                                           "kullanılamıyor, otomatik seçime geçildi")
    arka = SahteArka(girisler=[SahteCihaz("U", "USB", Sinyal(-65, konus, -35, sessiz=[(10, 99)], seed=26)),
                               SahteCihaz("L", "Dizüstü", Sinyal(-60, konus, -45, seed=27))], vg="L")
    m = cihaz.MikrofonSecici(arka, elle="USB", saat=arka.simdi)
    _surdur(arka, [m], 15)
    assert m.secili == "U" and m.gecis_sayisi == 0                     # tam sifir = sessize alma: birakilmaz


def test_elle_ayar_bulunamazsa_otomatik_ve_log():
    arka = SahteArka(girisler=[SahteCihaz("L", "Dizüstü", Sinyal(-60, seed=28))], vg="L")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, elle="Yok Böyle Mikrofon", olay=k, saat=arka.simdi)
    _surdur(arka, [m], 2)
    assert m.secili == "L"
    assert k.loglar("! Mikrofon: ayarlanan cihaz ('Yok Böyle Mikrofon') bulunamadı")


def test_elle_ayar_numarasi_sounddevice_adiyla_eslesir(monkeypatch):
    sahte = types.SimpleNamespace(query_devices=lambda no: {"name": "Mikrofon (USB Audio Dev"})   # MME 31 karakter
    monkeypatch.setitem(sys.modules, "sounddevice", sahte)
    arka = SahteArka(girisler=[SahteCihaz("L", "Dizüstü", Sinyal(-60, seed=29)),
                               SahteCihaz("U", "Mikrofon (USB Audio Device)", Sinyal(-60, seed=30))], vg="L")
    m = cihaz.MikrofonSecici(arka, elle=3, saat=arka.simdi)
    _surdur(arka, [m], 1)
    assert m.secili == "U" and m.secili_mikrofon()["otomatik"] is False


# ---------------------------------------------------------------- AkisYakalayici ile: sayac, klip zamanlari, klip butunlugu

def _akis(arka, n, **kw):
    a = ses.AkisYakalayici("mikrofon", queue.Queue(), sayac=itertools.count(), saat=arka.simdi, olay=kw.pop("olay", None))
    arka.otomatik, arka.bitis_n, arka.dur = True, n, a._dur
    a.secici = cihaz.MikrofonSecici(arka, saat=arka.simdi, olay=kw.pop("secici_olay", None), **kw)
    return a


def _klipler(a):
    out = []
    while not a.kuyruk.empty():
        p = a.kuyruk.get_nowait()
        out.append((round(p["bas_epoch"] - T0, 2), round(p["bit_epoch"] - T0, 2), p))
    return out


def test_cihaz_cikarilinca_sayac_kesintisiz_klip_zamanlari_dogru():
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-70, [(2, 5)], -25, seed=31), bit=8.0),
                               SahteCihaz("L", "Dizüstü", Sinyal(-62, [(10, 13)], -30, seed=32))], vg="H")
    a = _akis(arka, 160)
    a.run()
    assert a.hata is None
    kl = _klipler(a)
    assert [(b, e) for b, e, _ in kl] == [(pytest.approx(2.0, abs=0.11), pytest.approx(5.0, abs=0.11)),
                                         (pytest.approx(10.0, abs=0.11), pytest.approx(13.0, abs=0.11))]
    assert a.toplam_ornek == 160 * cihaz.BLOK and a.capa_sayisi == 0
    assert kl[1][2]["ornek_bas"] >= kl[0][2]["ornek_bit"]
    assert a.halka.al(0, a.toplam_ornek) is not None                   # halka kopmadi (ornek sayaci surekli)


def test_gecis_boslugu_saat_capasiyla_duzelir():
    """Secili cihaz takildi (blok yok), yeni cihaz ancak 12. sn'de geldi: sayac 4 sn geride kalir; gecisten sonraki ilk blokta
    capa yenilenir ('capa' olayi), sonraki klibin bas/bit'i dogru."""
    olaylar = []
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-70, [(2, 5)], -25, seed=33), durgun=[(8, 99)]),
                               SahteCihaz("L", "Dizüstü", Sinyal(-62, [(13, 15.5)], -30, seed=34), bas=11.0)], vg="H")
    a = _akis(arka, 180, olay=lambda t, v: olaylar.append((t, v)))
    a.run()
    kl = _klipler(a)
    assert [(b, e) for b, e, _ in kl] == [(pytest.approx(2.0, abs=0.11), pytest.approx(5.0, abs=0.11)),
                                         (pytest.approx(13.0, abs=0.11), pytest.approx(15.5, abs=0.11))]
    capa = [v for t, v in olaylar if t == "capa"]
    assert a.capa_sayisi == 1 and capa[0]["neden"] == "gecis" and capa[0]["fark"] == pytest.approx(4.0, abs=0.11)
    assert capa[0]["ornek"] == 80 * cihaz.BLOK                          # yeni capa yeni cihazin ilk blogundan gecerli


def test_gecis_aninda_baslayan_konusma_yeni_capayla_damgalanir():
    """Yeni cihaz geldigi anda kullanici konusuyor: klibin ilk bloklari da (gecisin ilk blogu dahil) dogru zamanda."""
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-70, (), -25, seed=39), durgun=[(3, 99)]),
                               SahteCihaz("L", "Dizüstü", Sinyal(-62, [(5.9, 8.5)], -30, seed=40), bas=5.5)], vg="H")
    a = _akis(arka, 110)
    a.run()
    kl = [(b, e) for b, e, _ in _klipler(a)]
    assert a.capa_sayisi == 1
    assert kl == [(pytest.approx(6.0, abs=0.11), pytest.approx(8.5, abs=0.11))]


def test_puana_dayali_gecis_acik_klibi_bolmez():
    on = [(9, 15)]
    # A: SNR 25 dB (klip acar: -35 dBFS > ses.SESSIZ_ESIK), B: SNR 50 dB -> B 12. sn'de aday (klip acik)
    arka = SahteArka(girisler=[SahteCihaz("A", "Dizüstü", Sinyal(-60, on, -35, seed=35)),
                               SahteCihaz("B", "Kulaklık", Sinyal(-70, on + [(18, 20)], -20, seed=36))], vg="A")
    gecis = []
    a = _akis(arka, 220, secici_olay=lambda t, v: gecis.append((arka.goreli(), v)) if t == "mikrofon" else None)
    a.run()
    kl = _klipler(a)
    gec_an = [t for t, v in gecis if v["kimlik"] == "B"][0]
    assert gec_an >= 15.5                                              # aday 12. sn'de hazirdi, klip kapanana kadar bekledi
    uzun = [(b, e) for b, e, _ in kl if b == pytest.approx(9.0, abs=0.11)]
    assert uzun == [(pytest.approx(9.0, abs=0.11), pytest.approx(15.0, abs=0.11))]   # tek parca
    assert [(b, e) for b, e, _ in kl][-1] == (pytest.approx(18.0, abs=0.11), pytest.approx(20.0, abs=0.11))
    assert a.toplam_ornek == 220 * cihaz.BLOK and a.capa_sayisi == 0


def test_acil_geciste_acik_klip_once_kapanir_iki_cihaz_karismaz():
    on = [(9, 14)]
    arka = SahteArka(girisler=[SahteCihaz("A", "Dizüstü", Sinyal(-60, on, -40, seed=37), bit=12.0),
                               SahteCihaz("B", "USB", Sinyal(-60, on, -40, seed=38))], vg="A")
    a = _akis(arka, 160)
    a.run()
    kl = [(b, e) for b, e, _ in _klipler(a)]
    assert kl == [(pytest.approx(9.0, abs=0.11), pytest.approx(12.0, abs=0.11)),
                  (pytest.approx(12.0, abs=0.11), pytest.approx(14.0, abs=0.11))]
    assert kl[0][1] <= 12.0 + 1e-6 and kl[1][0] >= kl[0][1] - 1e-6      # ortusme yok: her klip tek cihazin sesi


def test_secici_calismazsa_sounddevice_yoluna_duser(monkeypatch):
    class Akis:
        def __init__(self, samplerate, channels, dtype, blocksize, device, callback):
            self.cb = callback

        def __enter__(self):
            self.cb(np.full((cihaz.BLOK, 1), 0.1, dtype="float32"), cihaz.BLOK, None, None)
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(sys.modules, "sounddevice",
                        types.SimpleNamespace(InputStream=Akis, query_devices=lambda c, t: {"default_samplerate": 16000.0}))
    monkeypatch.setattr(cihaz, "ARKA_DENEME_SN", 0.5)

    class Bozuk(SahteArka):
        def ac_giris(self, kimlik):
            raise RuntimeError("erişim reddedildi")

    arka = Bozuk(girisler=[SahteCihaz("D", "Mikrofon", Sinyal(-60))], vg="D", otomatik=True)
    loglar = []
    a = ses.AkisYakalayici("mikrofon", queue.Queue(), saat=arka.simdi, olay=lambda t, v: loglar.append(v))
    a.secici = cihaz.MikrofonSecici(arka, saat=arka.simdi, olay=lambda t, v: loglar.append(v))
    g = a._bloklar_mikrofon()
    x = next(b for b in g if b is not None)
    assert len(x) == cihaz.BLOK and float(x[0]) == pytest.approx(0.1)
    assert any("otomatik cihaz seçimi çalışmadı" in str(v) for v in loglar)
    a._dur.set()
    g.close()


# ---------------------------------------------------------------- SesServisi baglantisi

def test_ses_servisi_secicileri_baglar_ve_gecis_kulaklik_penceresini_sifirlar(monkeypatch):
    kurulan = {}

    class SahteAkisT(threading.Thread):
        def __init__(self, kaynak, kuyruk, **kw):
            super().__init__(daemon=True)
            self.kaynak = kaynak
            kurulan[kaynak] = kw

        def run(self):
            pass

        def durdur(self):
            pass

    class Stt:
        def saglik(self):
            return True

    monkeypatch.setattr(ses, "AkisYakalayici", SahteAkisT)
    arka = SahteArka(girisler=[SahteCihaz("L", "Dizüstü", Sinyal())], cikislar=[SahteCihaz("S", "Hoparlör", Sinyal())])
    s = ses.SesServisi("https://stt.ornek", arka=arka, mik_cihaz="USB", altyazi_fn=lambda: None)
    s.stt = Stt()
    assert s.baslat() is True
    lp, m = kurulan["loopback"]["secici"], kurulan["mikrofon"]["secici"]
    assert isinstance(lp, cihaz.LoopbackSecici) and isinstance(m, cihaz.MikrofonSecici)
    assert m._uzak_fn == lp.aktif_mi and m.elle == "USB" and lp._altyazi_fn is not None
    assert kurulan["mikrofon"]["gecis_fn"] == s._gecis and kurulan["mikrofon"]["saat"] == arka.simdi
    assert s.secili_mikrofon() is None
    t = s._kulaklik_tespit
    for i in range(30):
        t.blok_ekle("mikrofon", T0 + i * 0.1, 0.01)
        t.blok_ekle("loopback", T0 + i * 0.1, 0.02)
    s._gecis("loopback", cihaz.Gecis("loopback", "S", "Hoparlör", kapat=False))
    assert len(t._kareler["mikrofon"]) == 30                           # loopback usta degisimi pencereyi bozmaz
    s._gecis("mikrofon", cihaz.Gecis("mikrofon", "H", "Kulaklık"))
    assert not t._kareler["mikrofon"] and not t._kareler["loopback"]
    s.durdur()


def test_ses_servisi_otomatik_kapali_ya_da_soundcard_yoksa_eski_yol(monkeypatch):
    loglar = []
    s = ses.SesServisi("", cihaz_otomatik=False, olay=lambda t, v: loglar.append(v))
    assert s._secicileri_kur() == (None, None) and "otomatik seçim kapalı" in loglar[-1]
    monkeypatch.setattr(cihaz, "varsayilan_arka", lambda: None)
    s = ses.SesServisi("", olay=lambda t, v: loglar.append(v))
    assert s._secicileri_kur() == (None, None) and "soundcard kurulu değil" in loglar[-1]


def test_mikrofon_olayi_olcum_kaydina_yazilir(tmp_path):
    s = ses.SesServisi("")
    s.olcum_kaydi_ac(str(tmp_path))
    s._akis_olayi("mikrofon", {"kimlik": "H", "ad": "Kulaklık", "otomatik": True, "neden": ""})
    import json
    satirlar = [json.loads(x) for x in open(s._olcum.olaylar_jsonl, encoding="utf-8")]
    assert satirlar[-1]["tur"] == "mikrofon" and satirlar[-1]["ad"] == "Kulaklık"


def test_altyazi_son_fn_ben_disindaki_son_etkinlik():
    kisi = lambda ben, seg: types.SimpleNamespace(ben=ben, seg=seg)      # noqa: E731
    motor = types.SimpleNamespace(konusmaci=types.SimpleNamespace(_kisiler={
        "Ben": kisi(2, [[T0 + 1, T0 + 50, 0, 0, "x"]]), "Ayşe": kisi(0, [[T0 + 1, T0 + 9, 0, 0, "x"]]),
        "Mehmet": kisi(0, [[T0 + 2, T0 + 20, 0, 0, "x"]]), "Boş": kisi(0, [])}))
    assert cihaz.altyazi_son_fn(motor)() == T0 + 20
    assert cihaz.altyazi_son_fn(types.SimpleNamespace())() is None
    assert ses.altyazi_son_fn is cihaz.altyazi_son_fn


# ---------------------------------------------------------------- gercek arka uc (sahte soundcard modulu), okuyucu ipleri

def test_soundcard_arka_api_kullanimi():
    acilan = []

    class Kayitci:
        def __init__(self, ad, **kw):
            acilan.append((ad, kw))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def record(self, numframes):
            return np.full((numframes, 1), 0.25, dtype=np.float32)

    def cihaz_(id_, ad, loop=False):
        return types.SimpleNamespace(id=id_, name=ad, isloopback=loop, recorder=lambda **kw: Kayitci(ad, **kw))

    mik = [cihaz_("{0.0.1}.a", "Mikrofon Dizisi"), cihaz_("{0.0.1}.b", "Kulaklık Mikrofonu")]
    hop = [cihaz_("{0.0.0}.x", "Hoparlör")]
    sc = types.SimpleNamespace(
        all_microphones=lambda include_loopback=False: mik + ([cihaz_("{0.0.0}.x", "Hoparlör", True)] if include_loopback
                                                            else [cihaz_("{0.0.0}.y", "Sahte", True)]),
        all_speakers=lambda: hop, default_microphone=lambda: mik[1], default_speaker=lambda: hop[0],
        get_microphone=lambda id_, include_loopback=False: (cihaz_(id_, "Hoparlör", True) if include_loopback
                                                           else next(m for m in mik if m.id == id_)))
    a = cihaz.SoundcardArka(sc)
    assert a.girisler() == [("{0.0.1}.a", "Mikrofon Dizisi"), ("{0.0.1}.b", "Kulaklık Mikrofonu")]   # loopback haric
    assert a.cikislar() == [("{0.0.0}.x", "Hoparlör")]
    assert a.varsayilan_giris() == "{0.0.1}.b" and a.varsayilan_cikis() == "{0.0.0}.x"
    k = a.ac_giris("{0.0.1}.a")
    x = k.oku()
    assert x.shape == (cihaz.BLOK,) and x.dtype == np.float32
    k.kapat()
    a.ac_loopback("{0.0.0}.x").kapat()
    assert acilan == [("Mikrofon Dizisi", {"samplerate": 16000, "channels": 1, "blocksize": 1600}),
                      ("Hoparlör", {"samplerate": 16000, "channels": 1, "blocksize": 1600})]


def test_okuyucu_ipleri_cihaz_hatasi_yalniz_o_cihazi_kapatir():
    class IpliKayit:
        def __init__(self, ad, bozul):
            self.ad, self.bozul, self.n = ad, bozul, 0

        def oku(self):
            time.sleep(0.01)
            self.n += 1
            if self.bozul is not None and self.n > self.bozul:
                raise OSError("AUDCLNT_E_DEVICE_INVALIDATED")
            return np.full(cihaz.BLOK, 0.05 if self.ad == "Y" else 0.02, dtype=np.float32)

        def kapat(self):
            pass

    class IpliArka(cihaz.Arka):
        senkron = False

        def girisler(self):
            return [("X", "Bozulan"), ("Y", "Kulaklık")]

        def varsayilan_giris(self):
            return "X"

        def ac_giris(self, kimlik):
            return IpliKayit(kimlik, 5 if kimlik == "X" else None)

    k = []
    m = cihaz.MikrofonSecici(IpliArka(), olay=lambda t, v: k.append(v), saat=time.monotonic)
    dur = threading.Event()
    bloklar = []

    def tuket():
        for b in m.bloklar(dur):
            if b is not None and not isinstance(b, cihaz.Gecis):
                bloklar.append(float(b[0]))

    ip = threading.Thread(target=tuket, daemon=True)
    ip.start()
    son = time.monotonic() + 5
    while time.monotonic() < son and not (m.secili == "Y" and len(bloklar) > 20):
        time.sleep(0.02)
    dur.set()
    ip.join(timeout=3)
    assert not ip.is_alive()
    assert m.secili == "Y" and bloklar[-1] == pytest.approx(0.05)
    assert any("kapandı: Bozulan (hata: OSError" in str(v) for v in k)
    assert any(str(v).startswith("Mikrofon: Kulaklık (otomatik) — önceki cihaz kayboldu") for v in k)


# ---------------------------------------------------------------- islemci yuku

def test_dort_mikrofon_dort_loopback_hafif(capsys):
    konus = _konusma(0.5, 60, 2, 1)
    arka = SahteArka(girisler=[SahteCihaz(f"M{i}", f"Mik {i}", Sinyal(-60 - i, konus, -40 + i, seed=40 + i)) for i in range(4)],
                     cikislar=[SahteCihaz(f"S{i}", f"Çıkış {i}", Sinyal(-200, (), None, [(5, 50)], -20, seed=50 + i))
                               for i in range(4)], vg="M0", vc="S0")
    lp = cihaz.LoopbackSecici(arka, saat=arka.simdi)
    m = cihaz.MikrofonSecici(arka, saat=arka.simdi, uzak_fn=lp.aktif_mi)
    _surdur(arka, [lp, m], 60)
    us_m, us_l = m.blok_maliyeti_us(), lp.blok_maliyeti_us()
    tur_ms = (m.olcum["sn"] + lp.olcum["sn"]) / m.olcum["tur"] * 1000
    with capsys.disabled():
        print(f"\n  islem: mikrofon {us_m:.1f} us/blok, loopback {us_l:.1f} us/blok; 4+4 cihaz 100 ms turu {tur_ms:.3f} ms")
    assert m.olcum["blok"] == 4 * 600 and lp.olcum["blok"] == 4 * 600
    assert tur_ms < 10.0                                               # 100 ms'lik turun %10'undan az (tipik ~0.3 ms)


# ---------------------------------------------------------------- teshis araci

def test_ses_teshis_otomatik_rapor(monkeypatch, capsys):
    import importlib
    import os
    monkeypatch.chdir(os.getcwd())                                     # arac ice aktarilirken klasor degistirir
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monkeypatch.syspath_prepend(os.path.join(kok, "tools"))
    teshis = importlib.import_module("ses_teshis")
    arka = SahteArka(girisler=[SahteCihaz("D", "Mikrofon Dizisi", Sinyal(sessiz=True)),
                               SahteCihaz("H", "Kulaklık", Sinyal(-70, [(0.5, 4)], -25, seed=60))],
                     cikislar=[SahteCihaz("S", "Hoparlör", Sinyal(-200, (), None, [(1, 3)], -20, seed=61))],
                     vg="D", vc="S")
    s = teshis.otomatik(5, arka=arka)
    cikti = capsys.readouterr().out
    assert s["ad"] == "Kulaklık"
    assert "OTOMATİK SEÇİM: Kulaklık (otomatik) — Windows varsayılanı sessiz" in cikti
    assert "Mikrofon Dizisi" in cikti and "TAM SESSİZ" in cikti and "<- Windows varsayılanı" in cikti
    assert "Hoparlör loopback:" in cikti and "µs/blok" in cikti


def test_bluetooth_eller_serbest_mikrofonu_varsayilan_degilse_acilmaz():
    arka = SahteArka(girisler=[SahteCihaz("L", "Dizüstü Mikrofon", Sinyal(-60, seed=62)),
                               SahteCihaz("B", "Kulaklık (BT-100 Hands-Free AG Audio)", Sinyal(-60, seed=63))],
                     vg=lambda t: "L" if t < 5 else "B")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    _surdur(arka, [m], 4)
    assert arka.acilan == ["L"] and len(k.loglar("  · Bluetooth eller-serbest mikrofonu açılmadı")) == 1
    _surdur(arka, [m], 4)                                              # Windows varsayilani oldu: acilir
    assert "B" in arka.acilan
    assert cihaz.bt_eller_serbest_mi("Mikrofon (Eller Serbest)") and not cihaz.bt_eller_serbest_mi("USB Kulaklık")


def test_varsayilan_arka_soundcard_yoksa_none_varsa_soundcard(monkeypatch):
    monkeypatch.setitem(sys.modules, "soundcard", None)               # import soundcard -> ImportError
    assert cihaz.varsayilan_arka() is None
    sahte = types.ModuleType("soundcard")
    monkeypatch.setitem(sys.modules, "soundcard", sahte)
    a = cihaz.varsayilan_arka()
    assert isinstance(a, cihaz.SoundcardArka) and a.sc is sahte


def test_acilamayan_cihaz_geri_cekilerek_denenir_log_susar():
    class Bozuk(SahteArka):
        def ac_giris(self, kimlik):
            if kimlik == "X":
                raise RuntimeError("AUDCLNT_E_UNSUPPORTED_FORMAT")
            return super().ac_giris(kimlik)

    arka = Bozuk(girisler=[SahteCihaz("X", "Bozuk", Sinyal()), SahteCihaz("L", "Dizüstü", Sinyal(-60, seed=64))], vg="L")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    _surdur(arka, [m], 400)
    denemeler = [t for t, v in k.loglar("! mikrofon cihazı açılamadı: Bozuk")]
    assert len(denemeler) == 2 and m._hata_n["X"] >= 5                 # 10, 20, 40, 80, 160 sn ... yalniz ilk ikisi loglanir
    assert m.secili == "L"


# ---------------------------------------------------------------- inceleme bulgulari (regresyon)

class IlkBlokSifir(Sinyal):
    """Ilk n blok tam sifir (WASAPI acilis paketi), sonra normal."""

    def __init__(self, *a, sifir_n=1, **k):
        super().__init__(*a, **k)
        self.kalan = sifir_n

    def blok(self, t):
        if self.kalan > 0:
            self.kalan -= 1
            return np.zeros(cihaz.BLOK, dtype=np.float32)
        return super().blok(t)


class Kapili(Sinyal):
    """Gurultu kapisi (Krisp benzeri / donanim kapisi): kullanici konusmuyorken tam sifir."""

    def blok(self, t):
        if not _icinde(t - 0.05, self.kullanici):
            return np.zeros(cihaz.BLOK, dtype=np.float32)
        return super().blok(t)


class DegisenTaban(Sinyal):
    """taban_fn(t) ile degisen gurultu tabani (AGC)."""

    def __init__(self, taban_fn, *a, **k):
        super().__init__(*a, **k)
        self.fn = taban_fn

    def blok(self, t):
        self.taban_db = self.fn(t - 0.05)
        return super().blok(t)


class Klavyeli(Sinyal):
    """Yazi donemlerinde bloklarin cogunda 10 ms'lik tus tiki."""

    def __init__(self, *a, yazi=(), tus_db=-38.0, oran=0.6, **k):
        super().__init__(*a, **k)
        self.yazi, self.tus_db, self.oran = list(yazi), tus_db, oran
        self.r2 = np.random.default_rng(99)

    def blok(self, t):
        x = super().blok(t)
        if _icinde(t - 0.05, self.yazi) and self.r2.random() < self.oran:
            x = x.copy()
            i = int(self.r2.integers(0, cihaz.BLOK - 160))
            x[i:i + 160] += (self.r2.standard_normal(160) * _sev(self.tus_db + 10)).astype(np.float32)
        return x


def _mik_olaylari(k):
    return [(t, v["ad"], v["neden"]) for t, v in k.tip("mikrofon")]


def test_ipli_modda_takilip_yetisen_okuyucunun_ardisik_bloklari_atilmaz(monkeypatch):
    """Okuyucu ipi gecikip WASAPI tamponundan yetisirken ardisik iki blok < 50 ms arayla gelir ve farkli turlara duser:
    ikincisi atilmamali (eskiden varis anina gore suzuluyordu). Sahte okuyucu ipi: blok varis anlari elle verilir, her
    blok kendi turunda islenir (bloklar() dongusunun govdesi: _topla + _isle)."""
    okuyucular = []

    class SahteOkuyucu:
        def __init__(self, ac_fn, ad, saat, bildir, hazirla=None):
            self.q, self.bitti, self.hata = collections.deque(), False, None
            okuyucular.append(self)

        def start(self):
            pass

        def durdur(self):
            pass

    monkeypatch.setattr(cihaz, "Okuyucu", SahteOkuyucu)

    class IpliArka(cihaz.Arka):
        def girisler(self):
            return [("H", "Kulaklık")]

        def varsayilan_giris(self):
            return "H"

    saat = {"t": T0}
    m = cihaz.MikrofonSecici(IpliArka(), saat=lambda: saat["t"])
    assert m._senkron is False
    m._say(T0)
    desen = [0.1, 0.1, 0.17, 0.03]                                   # 100, 100, 170, 30 ms: takil, yetis
    cikan, a = [], T0
    for no in range(1, 49):
        a += desen[no % len(desen)]
        saat["t"] = a
        okuyucular[0].q.append((np.full(cihaz.BLOK, no / 1e3, dtype=np.float32), a))
        m._bildir.set()
        for x in m._isle(m._topla(), a, None):
            if not isinstance(x, cihaz.Gecis):
                cikan.append(int(round(float(x[0]) * 1e3)))
    assert cikan == list(range(1, 49))                               # eksik sira numarasi yok


def test_ipli_modda_gercek_okuyucu_ipleriyle_yetisme_deseninde_blok_kaybi_yok():
    class YetisenKayit:
        def __init__(self):
            self.n = 0

        def oku(self):
            time.sleep((0.05, 0.05, 0.085, 0.015)[self.n % 4])       # takilip yetisen WASAPI okumasi
            self.n += 1
            return np.full(cihaz.BLOK, 0.01 + self.n / 1e4, dtype=np.float32)

        def kapat(self):
            pass

    class IpliArka(cihaz.Arka):
        def girisler(self):
            return [("H", "Kulaklık")]

        def varsayilan_giris(self):
            return "H"

        def ac_giris(self, kimlik):
            return YetisenKayit()

    m = cihaz.MikrofonSecici(IpliArka(), saat=time.monotonic)
    dur = threading.Event()
    gelen = []

    def tuket():
        for b in m.bloklar(dur):
            if b is not None and not isinstance(b, cihaz.Gecis):
                gelen.append(int(round((float(b[0]) - 0.01) * 1e4)))

    ip = threading.Thread(target=tuket, daemon=True)
    ip.start()
    time.sleep(2.0)
    dur.set()
    ip.join(timeout=3)
    assert len(gelen) > 20
    assert gelen == list(range(gelen[0], gelen[0] + len(gelen)))   # ardisik, kayip yok


@pytest.mark.parametrize("ad,L,H", [
    ("tek sifir blok", IlkBlokSifir(-60, _konusma(1, 60, 3, 2), -45, seed=2), Sinyal(-75, _konusma(1, 60, 3, 2), -28, seed=1)),
    ("konusma yok", IlkBlokSifir(-60, (), -45, seed=2), Sinyal(-75, (), -28, seed=1)),
    ("AGC", DegisenTaban(lambda t: -75 if t < 0.5 else -50, -50, _konusma(1, 60, 3, 2), -22, seed=2),
     Sinyal(-75, _konusma(1, 60, 3, 2), -28, seed=1)),
])
def test_gurultu_tabani_tek_sifir_bloga_ya_da_gecici_dususe_takilmaz(ad, L, H):
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", H), SahteCihaz("L", "Dizüstü Mikrofon", L)], vg="H")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    r = {}
    _surdur(arka, [m], 60, rapor=r)
    assert m.secili == "H" and m.gecis_sayisi == 0, _mik_olaylari(k)
    taban = {x["kimlik"]: x["taban_db"] for x in r[m]}
    assert taban["L"] == pytest.approx(-60 if ad != "AGC" else -50, abs=1.5)


def test_sessizden_cikan_varsayilanin_tabani_sifirlanir():
    """Kullanicinin durumu: varsayilan dizi -98 dB ile basladi (kulaklik secildi), 20. sn'de sessize alma kalkti."""
    konus = _konusma(1, 70, 3, 2)
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-75, konus, -28, seed=1)),
                               SahteCihaz("L", "Dizüstü Mikrofon", Sinyal(-60, konus, -45, sessiz=[(-1e9, 20)], seed=2))],
                     vg="L")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    r = {}
    _surdur(arka, [m], 70, rapor=r)
    assert m.secili == "H" and m.gecis_sayisi == 0, _mik_olaylari(k)
    assert {x["kimlik"]: x["taban_db"] for x in r[m]}["L"] == pytest.approx(-60, abs=1.5)


def test_tek_sifir_blokla_yanlis_mikrofona_gecilmez_klipler_tam():
    konus = _konusma(1, 60, 3, 2)
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-75, konus, -28, seed=1)),
                               SahteCihaz("L", "Dizüstü Mikrofon", IlkBlokSifir(-60, konus, -46, seed=2))], vg="H")
    a = _akis(arka, 600)
    a.run()
    assert len(_klipler(a)) == 12 and a.secici.secili == "H"


def test_bt_eller_serbest_diger_mikrofonlar_tam_sessizse_acilir():
    """Varsayilan dizustu dizisi tam sessiz, Bluetooth kulaklik Teams'te zaten HFP'de: HFP mikrofonu acilir ve secilir."""
    konus = _konusma(1, 60, 3, 2)
    arka = SahteArka(girisler=[SahteCihaz("D", "Mikrofon Dizisi (Realtek)", Sinyal(sessiz=True, seed=1)),
                               SahteCihaz("B", "Kulaklık (BT-100 Hands-Free AG Audio)", Sinyal(-65, konus, -26, seed=2))],
                     vg="D")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    out = _surdur(arka, [m], 30)[m]
    assert m.secili == "B" and "B" in arka.acilan
    assert k.loglar("  · Bluetooth eller-serbest mikrofonu açılıyor (diğer mikrofonlar tam sessiz)")
    assert not k.tip("ses_uyari")
    assert _db(np.concatenate(_bloklar(out)[40:])) > -35                # kullanicinin konusmasi akista


def test_bt_eller_serbest_kulaklik_zaten_hfp_modundaysa_acilir():
    arka = SahteArka(girisler=[SahteCihaz("L", "Dizüstü Mikrofon", Sinyal(-60, seed=62)),
                               SahteCihaz("B", "Kulaklık (BT-100 Hands-Free AG Audio)", Sinyal(-60, seed=63))],
                     cikislar=[SahteCihaz("S", "Hoparlör", Sinyal(sessiz=True)),
                               SahteCihaz("BC", "Kulaklık (BT-100 Hands-Free AG Audio)",
                                          Sinyal(-200, (), None, [(4, 99)], -25, seed=64))], vg="L", vc="S")
    k = Kayit(arka)
    lp = cihaz.LoopbackSecici(arka, olay=k, saat=arka.simdi)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi, hfp_fn=lp.hfp_aktif_mi)
    _surdur(arka, [lp, m], 3)
    assert "B" not in arka.acilan and not lp.hfp_aktif_mi()          # A2DP: acilmaz (dinleyenin sesi bozulmasin)
    _surdur(arka, [lp, m], 4)                                        # HFP cikisinda ses var: kulaklik zaten HFP'de
    assert lp.hfp_aktif_mi() and "B" in arka.acilan
    assert k.loglar("  · Bluetooth eller-serbest mikrofonu açılıyor (kulaklık zaten eller-serbest modunda)")


def test_tavan_doluyken_varsayilan_olan_yeni_cikis_acilir_karsi_taraf_kaybolmaz():
    """Dock'lu dizustu: 4 cikis (hoparlor + 3 monitor) acik; 10. sn'de USB kulaklik takilip varsayilan olur, Teams sesi
    oraya gider: hic ses vermemis bir monitor kapatilir, kulaklik acilir."""
    cik = [SahteCihaz("S", "Hoparlör", Sinyal(-200, (), None, [(1, 10)], -20, sessiz=[], seed=1))] + \
          [SahteCihaz(f"M{i}", f"Monitör {i}", Sinyal(sessiz=True)) for i in range(3)] + \
          [SahteCihaz("K", "USB Kulaklık", Sinyal(-200, (), None, [(12, 60)], -20, sessiz=[], seed=9), bas=10.0)]
    arka = SahteArka(cikislar=cik, vc=lambda t: "K" if t >= 10 else "S")
    k = Kayit(arka)
    lp = cihaz.LoopbackSecici(arka, olay=k, saat=arka.simdi, altyazi_fn=lambda: arka.simdi())
    r = {}
    bl = _bloklar(_surdur(arka, [lp], 60, rapor=r)[lp])
    acik = [x["kimlik"] for x in r[lp]]
    assert "K" in acik and "S" in acik
    assert _db(np.concatenate(bl[150:600])) > -25                       # karsi taraf yakalandi
    assert not [v for _, v in k.tip("ses_uyari") if v["var"]]
    assert any("yer açıldı (USB Kulaklık)" in v for _, v in k.loglar("  · loopback cihazı kapandı: Monitör"))
    assert len(acik) <= cihaz.LOOP_TAVAN


def test_tavan_doluyken_takilan_varsayilan_kulaklik_acilir_ve_secilir():
    konus = _konusma(1, 60, 3, 2)
    gir = [SahteCihaz("D", "Mikrofon Dizisi", Sinyal(sessiz=True, seed=1)),
           SahteCihaz("W", "Webcam", Sinyal(-55, konus, -48, seed=2)),
           SahteCihaz("O", "Dock", Sinyal(sessiz=True, seed=3)),
           SahteCihaz("C", "Monitör Kamerası", Sinyal(-58, konus, -50, seed=4)),
           SahteCihaz("H", "Kulaklık", Sinyal(-75, konus, -25, seed=5), bas=10.0)]
    arka = SahteArka(girisler=gir, vg=lambda t: "H" if t >= 10 else "D")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    r = {}
    _surdur(arka, [m], 40, rapor=r)
    acik = [x["kimlik"] for x in r[m]]
    assert "H" in acik and m.secili == "H", _mik_olaylari(k)
    assert len(acik) <= cihaz.MIK_TAVAN and "W" in acik and "C" in acik      # sesli olanlar kapanmadi


def test_tavan_doluyken_hic_ses_vermeyen_cihaz_bekleyene_yer_birakir():
    """Bastan 5 mikrofon var, ilk 4'un ikisi tam sessiz: sesli 5. mikrofon bir sure sonra acilir."""
    gir = [SahteCihaz("D", "Dizi", Sinyal(sessiz=True)), SahteCihaz("O", "Dock", Sinyal(sessiz=True)),
           SahteCihaz("W", "Webcam", Sinyal(-55, seed=2)), SahteCihaz("C", "Kamera", Sinyal(-58, seed=3)),
           SahteCihaz("U", "USB Mikrofon", Sinyal(-65, seed=4))]
    arka = SahteArka(girisler=gir, vg="D")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    r = {}
    _surdur(arka, [m], 7, rapor=r)
    acik = [x["kimlik"] for x in r[m]]
    assert arka.acilan == ["D", "O", "W", "C", "U"]                   # U ilk sayimda yer bulamadi, 3. sn'de acildi
    assert "U" in acik and "D" in acik and "O" not in acik and len(acik) == 4   # varsayilan korunur
    assert k.loglar("  · mikrofon cihazı kapandı: Dock (en fazla 4 cihaz: yer açıldı (USB Mikrofon))")


def test_sessize_alinmis_kulaklikta_odadaki_konusma_transkripte_girmez():
    yan = [(15, 20), (25, 32)]
    konus = [(1, 5), (45, 50)]
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-70, konus + yan, -28, sessiz=[(10, 40)], seed=1)),
                               SahteCihaz("L", "Dizüstü Mikrofon", Sinyal(-60, konus + yan, -32, seed=2))], vg="H")
    a = _akis(arka, 550)
    a.run()
    kl = [(b, e) for b, e, _ in _klipler(a)]
    assert kl == [(pytest.approx(1.0, abs=0.11), pytest.approx(5.0, abs=0.11)),
                  (pytest.approx(45.0, abs=0.11), pytest.approx(50.0, abs=0.11))]
    assert a.secici.secili == "H" and a.secici.gecis_sayisi == 0


def test_sessizdeyken_windows_varsayilani_degisirse_ona_gecilir():
    konus = _konusma(1, 40, 3, 2)
    arka = SahteArka(girisler=[SahteCihaz("L", "Dizüstü", Sinyal(-60, konus, -40, sessiz=[(5, 99)], seed=1)),
                               SahteCihaz("H", "Kulaklık", Sinyal(-75, konus, -28, seed=2), bas=12.0)],
                     vg=lambda t: "H" if t >= 12 else "L")
    k = Kayit(arka)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
    _surdur(arka, [m], 20)
    assert m.secili == "H" and _mik_olaylari(k)[-1][2] == "Windows varsayılanı değişti"


def test_gurultu_kapili_kulaklikla_gidip_gelme_olmaz():
    konus = [(i * 10 + 1, i * 10 + 5) for i in range(18)]
    uzak = [(i * 10 + 5.5, i * 10 + 10.5) for i in range(18)]
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık (Krisp)", Kapili(-70, konus, -28, seed=1)),
                               SahteCihaz("L", "Dizüstü Mikrofon", Sinyal(-60, konus, -40, seed=2))],
                     cikislar=[SahteCihaz("S", "Kulaklık çıkış", Sinyal(-200, (), None, uzak, -20, sessiz=[], seed=3))],
                     vg="H", vc="S")
    k = Kayit(arka)
    lp = cihaz.LoopbackSecici(arka, olay=k, saat=arka.simdi)
    m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi, uzak_fn=lp.aktif_mi)
    _surdur(arka, [lp, m], 180)
    assert m.secili == "H" and m.gecis_sayisi == 0, _mik_olaylari(k)
    assert not k.tip("ses_uyari")


def test_kisik_ama_dusuk_tabanli_mikrofon_klip_acamiyorsa_kazanmaz():
    """Gurultu bastirmali dizi: SNR 41 dB ama konusma -47 dBFS (VAD esiginin altinda); kulaklik SNR 34, -28 dBFS."""
    konus = _konusma(1, 60, 3, 2)
    arka = SahteArka(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-62, konus, -28, seed=1)),
                               SahteCihaz("L", "Mikrofon Dizisi (NS)", Sinyal(-88, konus, -47, seed=2))], vg="H")
    a = _akis(arka, 600)
    a.run()
    assert a.secici.secili == "H" and a.secici.gecis_sayisi == 0
    assert len(_klipler(a)) == 12
    assert cihaz.KLIP_ESIK_DB >= 20 * math.log10(ses.SESSIZ_ESIK) + 5.9        # ses.py VAD esigiyle uyumlu


def test_klavye_tikirtilari_konusma_sayilmaz():
    donem = 12
    konus = [(i * donem + 1, i * donem + 4) for i in range(10)]
    yazi = [(i * donem + 5, i * donem + 12) for i in range(10)]
    for tus_db, oran in ((-40, 0.6), (-34, 0.8), (-30, 0.8)):
        arka = SahteArka(girisler=[
            SahteCihaz("H", "Kulaklık", Klavyeli(-75, konus, -28, seed=1, yazi=yazi, tus_db=-80)),
            SahteCihaz("L", "Dizüstü Mikrofon", Klavyeli(-60, konus, -42, seed=2, yazi=yazi, tus_db=tus_db, oran=oran))],
            vg="H")
        k = Kayit(arka)
        m = cihaz.MikrofonSecici(arka, olay=k, saat=arka.simdi)
        _surdur(arka, [m], 110)
        assert m.secili == "H" and m.gecis_sayisi == 0, (tus_db, _mik_olaylari(k))


def test_sessize_alinmis_ama_tam_sifir_olmayan_varsayilan_secilmez_ilk_konusma_kaybolmaz():
    konus = _konusma(1, 40, 3, 2)
    arka = SahteArka(girisler=[SahteCihaz("D", "Mikrofon Dizisi", Sinyal(-85, (), None, seed=1)),
                               SahteCihaz("H", "Kulaklık", Sinyal(-70, konus, -28, seed=2))], vg="D")
    gec = []
    a = _akis(arka, 400, secici_olay=lambda t, v: gec.append((round(arka.goreli(), 1), v["ad"], v["neden"]))
              if t == "mikrofon" else None)
    a.run()
    assert gec == [(1.1, "Kulaklık", "Windows varsayılanı neredeyse sessiz")]
    kl = [(b, e) for b, e, _ in _klipler(a)]
    assert len(kl) == 8 and kl[0] == (pytest.approx(1.0, abs=0.11), pytest.approx(4.0, abs=0.11))


def test_ilk_saniyelerdeki_gecici_acma_hatasi_eski_yola_dusurmez():
    class GeciciHata(SahteArka):
        def ac_giris(self, kimlik):
            if self.goreli() < 6.0:
                raise OSError("AUDCLNT_E_DEVICE_IN_USE")
            return super().ac_giris(kimlik)

    arka = GeciciHata(girisler=[SahteCihaz("H", "Kulaklık", Sinyal(-70, [(8, 12)], -28, seed=1))], vg="H")
    a = _akis(arka, 200)
    sd = []
    a._bloklar_sd = lambda: sd.append(arka.goreli()) or iter(())
    a.run()
    assert sd == [] and arka.acilan == ["H"] and a.secici.secili == "H"
    assert [(b, e) for b, e, _ in _klipler(a)] == [(pytest.approx(8.0, abs=0.11), pytest.approx(12.0, abs=0.11))]


def test_soundcard_com_baslatilmadan_once_yuklenir(monkeypatch, tmp_path):
    """soundcard 0.4.6 yuklenirken CoInitializeEx cagirir ve ayni ipte ikinci cagrinin S_FALSE (1) donusunu hata sayar:
    secici ve okuyucu ipleri once soundcard'i yuklemeli, COM'u sonra baslatmali."""
    durum = threading.local()

    class Ole32:
        def CoInitializeEx(self, ayrilmis, mod):
            if getattr(durum, "acik", False):
                return 1                                             # S_FALSE: bu ipte zaten acik
            durum.acik = True
            return 0

    ole = Ole32()
    monkeypatch.setattr(cihaz, "_ole32", lambda: ole)
    (tmp_path / "soundcard.py").write_text(
        "import cihaz\n"
        "_hr = cihaz._ole32().CoInitializeEx(None, 0)\n"
        "if _hr != 0:\n"
        "    raise RuntimeError('Error 0x%x' % (0x100000000 + _hr))   # soundcard check_error(S_FALSE)\n"
        "def all_microphones(include_loopback=False):\n    return []\n"
        "def all_speakers():\n    return []\n"
        "def default_microphone():\n    raise RuntimeError('yok')\n"
        "def default_speaker():\n    raise RuntimeError('yok')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    onceki = sys.modules.pop("soundcard", None)
    sonuc = {}
    try:
        def ters_sira():                                             # sahte ole32 sorunu gercekten uretiyor mu
            cihaz._com_hazirla()
            try:
                importlib.import_module("soundcard")
                sonuc["ters"] = None
            except RuntimeError as e:
                sonuc["ters"] = str(e)
            sys.modules.pop("soundcard", None)

        ip = threading.Thread(target=ters_sira)
        ip.start()
        ip.join(5)
        assert sonuc["ters"] == "Error 0x100000001"

        loglar = []

        def calis(secici):
            dur = threading.Event()
            g = secici.bloklar(dur)
            t = threading.Timer(0.3, dur.set)
            t.start()
            try:
                for _ in g:
                    pass
            except Exception as e:                                   # pragma: no cover - hata ise test duser
                loglar.append(repr(e))

        arka = cihaz.SoundcardArka()
        ipler = [threading.Thread(target=calis, args=(s,)) for s in
                 (cihaz.MikrofonSecici(arka, olay=lambda t, v: loglar.append(v)),
                  cihaz.LoopbackSecici(arka, olay=lambda t, v: loglar.append(v)))]
        for i in ipler:
            i.start()
        for i in ipler:
            i.join(5)
        assert "soundcard" in sys.modules and arka.sc is sys.modules["soundcard"]
        assert not [v for v in loglar if "sayılamadı" in str(v) or "Error" in str(v)], loglar
    finally:
        sys.modules.pop("soundcard", None)
        if onceki is not None:
            sys.modules["soundcard"] = onceki


def test_adi_okunamayan_cihaz_sayimi_ve_digerlerinin_acilmasini_bozmaz():
    class Kayitci:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def record(self, numframes):
            return np.full((numframes, 1), 0.1, dtype=np.float32)

    class Cihaz_:
        def __init__(self, id_, ad, loop=False, bozuk=False):
            self.id, self._ad, self.isloopback, self._bozuk = id_, ad, loop, bozuk

        @property
        def name(self):
            if self._bozuk:
                raise RuntimeError("Error 0x80070490")                # cihaz o an cikarildi / bozuk surucu
            return self._ad

        def recorder(self, **kw):
            return Kayitci()

    mik = [Cihaz_("{1}.a", "Mikrofon Dizisi"), Cihaz_("{1}.b", "Sanal", bozuk=True), Cihaz_("{1}.c", "Kulaklık")]
    hop = [Cihaz_("{0}.x", "Hoparlör"), Cihaz_("{0}.y", "Bozuk Çıkış", bozuk=True)]

    def get_microphone(*a, **k):                                     # soundcard._match_device tum adlari okur
        raise AssertionError("get_microphone kullanilmamali")

    sc = types.SimpleNamespace(
        all_microphones=lambda include_loopback=False: mik + ([Cihaz_(h.id, h._ad, True, h._bozuk) for h in hop]
                                                              if include_loopback else []),
        all_speakers=lambda: hop, default_microphone=lambda: mik[2], default_speaker=lambda: hop[0],
        get_microphone=get_microphone)
    a = cihaz.SoundcardArka(sc)
    assert a.girisler() == [("{1}.a", "Mikrofon Dizisi"), ("{1}.c", "Kulaklık")]
    assert a.cikislar() == [("{0}.x", "Hoparlör")]
    assert a.ac_giris("{1}.c").oku().shape == (cihaz.BLOK,)
    assert a.ac_loopback("{0}.x").oku().shape == (cihaz.BLOK,)
    with pytest.raises(RuntimeError, match="bulunamadı"):
        a.ac_loopback("{1}.a")                                       # mikrofon kimligi loopback degil
    loglar = []
    m = cihaz.MikrofonSecici(a, olay=lambda t, v: loglar.append(v), senkron=True)
    m._say(0.0)
    assert [x for x, _ in m.cihazlar()] == ["{1}.c", "{1}.a"] and not [v for v in loglar if "sayılamadı" in v]
