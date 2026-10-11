"""Faz 6 spike: ses_izi.py (ses izi ile altyazi kapali '?' satirlara yardim) ve Motor parca kapanisi kancasi.
Sahte cikarici (sentetik kume vektorleri): esik, marj, en az klip, 0.6 tavani ("Ad?", sahip degil), kapali yol (sherpa-onnx
yok / model yok / bayrak kapali), altyazi tabanli satirlara dokunulmazlik. Gercek model, ses cihazi, ag yok; adlar kurgusal."""
import copy
import datetime as dt
import math
import types

import numpy as np
import pytest

import llm
import motor as motor_mod
import ses_izi
from metin import gorunen_ad, kesin_ad_mi

T0 = 1760170000.0
A, B, C = "Ayşe Demir", "Burak Şahin", "Cem Yıldız"
DIM = 8


# ---------------------------------------------------------------- duzenek

def birim(*kat):
    v = np.zeros(DIM)
    for i, k in enumerate(kat):
        v[i] = k
    return v / np.linalg.norm(v)


def acili(a, taban=0):
    """taban eksenine kosinusu tam `a` olan birim vektor (digeri 7. eksen)."""
    v = np.zeros(DIM)
    v[taban] = a
    v[DIM - 1] = math.sqrt(1 - a * a)
    return v


class SahteCikarici(ses_izi.Cikarici):
    """Ses dizisinin ilk ornegi anahtar: anahtar -> onceden verilen vektor. Cagrilari sayar."""

    def __init__(self, vektorler=None):
        self.vektorler = dict(vektorler or {})
        self.cagri = 0

    def vektor(self, ses_16k):
        self.cagri += 1
        return self.vektorler[int(round(float(ses_16k[0])))]


class Halka:
    """ses_al_fn: satirin 'sesi' tablosuna gore ilk ornegi anahtar olan 16 kHz dizi (yoksa None)."""

    def __init__(self):
        self.anahtar = {}                                   # (akis, bas) -> anahtar
        self.cagri = []

    def __call__(self, akis, bas, bit):
        self.cagri.append((akis, bas, bit))
        a = self.anahtar.get((akis, bas))
        if a is None:
            return None
        return np.full(int(round((bit - bas) * 16000)), float(a), dtype=np.float32)


_no = [0]


def satir(ad="?", guven=0.0, bas=0.0, sure=3.0, **ek):
    _no[0] += 1
    s = {"surum": 2, "id": _no[0], "klip_no": _no[0], "akis": "loopback", "bas": T0 + bas, "bit": T0 + bas + sure,
         "ts": "10:00:00", "raw": "Bütçe tablosunu güncelledim.", "text": "Bütçe tablosunu güncelledim.",
         "speaker": ad, "spk_guven": guven, "adaylar": [], "devir": False, "cakisma": False, "supheli": False,
         "belirsiz": [], "duzeltmeler": [], "kaynak": "ses", "durum": "karar", "kapsama": True,
         "spk_neden": "baskin", "kimlik_turu": "kisi"}
    s.update(ek)
    return s


def kesin(ad, bas, **ek):
    return satir(ad, 0.95, bas=bas, **ek)


def soru(bas, **ek):
    """Altyazi kapaliyken '?' kalan loopback satiri (Konusmaci 'kapsama_yok' karari)."""
    return satir("?", 0.0, bas=bas, kapsama=False, spk_neden="kapsama_yok", **ek)


class Duzenek:
    """Sahte cikarici + halka + SesIzi; kisi kumeleri A:eksen0, B:eksen1, C:eksen2."""

    def __init__(self, **kw):
        self.cik = SahteCikarici()
        self.halka = Halka()
        self.logs = []
        kw.setdefault("log", self.logs.append)
        self.izi = ses_izi.SesIzi(self.cik, self.halka, **kw)
        self._anahtar = 0

    def ses(self, s, vektor):
        """satirin sesi: halkada yeni anahtar; cikarici bu anahtara `vektor`u verir."""
        self._anahtar += 1
        self.halka.anahtar[(s["akis"], s["bas"])] = self._anahtar
        self.cik.vektorler[self._anahtar] = np.asarray(vektor, dtype=np.float64)
        return s

    def egit(self, ad, sayi, eksen, bas0=0.0):
        """ad icin `sayi` kesin satir (hafif gurultulu kume) -> satirlar."""
        out = []
        for i in range(sayi):
            v = np.zeros(DIM)
            v[eksen] = 1.0
            v[DIM - 2 - (i % 3)] = 0.03 * (i + 1)           # gurultu 4-6. eksenlerde; 7. eksen (acili) dik kalir
            out.append(self.ses(kesin(ad, bas0 + 10 * i), v))
        return out


# ---------------------------------------------------------------- kesin / aday uygunlugu

@pytest.mark.parametrize("ek,beklenen", [
    ({}, True),
    ({"spk_guven": 0.89}, False),
    ({"spk_guven": 0.9}, True),
    ({"cakisma": True}, False),
    ({"devir": True}, False),
    ({"akis": "mikrofon"}, False),
    ({"bit": T0 + 1.99}, False),
    ({"bit": T0 + 2.0}, True),
    ({"supheli": True}, False),
    ({"kapsama": False}, False),
    ({"durum": "beklemede"}, False),
    ({"speaker": "A / B"}, False),
    ({"speaker": "A → B"}, False),
    ({"speaker": "?"}, False),
    ({"speaker": "Ayşe?"}, False),
    ({"kimlik_turu": "oda"}, False),
    ({"kaynak": "altyazi-yedek"}, False),
    ({"kaynak_konusmaci": "ses_izi"}, False),
])
def test_kesin_mi_ol_olcutleri(ek, beklenen):
    izi = Duzenek().izi
    s = kesin(A, 0.0)
    s.update(ek)
    assert izi.kesin_mi(s) is beklenen


def test_kesin_mi_ben_hic_ogrenilmez():
    izi = Duzenek(ben=A.upper()).izi
    assert not izi.kesin_mi(kesin(A, 0.0))
    assert izi.kesin_mi(kesin(B, 0.0))


@pytest.mark.parametrize("ek,beklenen", [
    ({}, True),
    ({"kapsama": True, "spk_neden": "kimse"}, False),            # altyazi var, kimse konusmuyor: altyazi tabanli karar
    ({"spk_neden": "zaman_asimi"}, False),
    ({"speaker": "Ayşe?", "spk_guven": 0.5}, False),
    ({"speaker": "A / B", "cakisma": True}, False),
    ({"speaker": "A → B", "devir": True}, False),
    ({"speaker": A, "spk_guven": 0.95}, False),
    ({"akis": "mikrofon"}, False),
    ({"supheli": True}, False),
    ({"durum": "beklemede"}, False),
    ({"kaynak_konusmaci": "ses_izi"}, False),
    ({"bit": T0 + 1.5}, False),
    ({"cakisma": True}, False),
])
def test_aday_mi_yalniz_altyazi_kapali_soru_satiri(ek, beklenen):
    izi = Duzenek().izi
    s = soru(0.0)
    s.update(ek)
    assert izi.aday_mi(s) is beklenen


# ---------------------------------------------------------------- esik / marj / en az klip

def _kurulu(**kw):
    """A (6 klip, eksen0), B (6 klip, eksen1)."""
    d = Duzenek(**kw)
    egit = d.egit(A, 6, 0, 0.0) + d.egit(B, 6, 1, 100.0)
    return d, egit


def test_esik_altinda_atanmaz_ustunde_atanir():
    d, egit = _kurulu()
    assert d.izi.parca_isle(egit) == []                       # kesin satirlar zaten ogrenilir, aday degil
    dusuk = d.ses(soru(300.0), acili(0.74))                   # cos(A) ~0.74 (ikinciye fark buyuk)
    yuksek = d.ses(soru(310.0), acili(0.80))
    sonuc = d.izi.parca_isle([dusuk, yuksek])
    assert [s["id"] for s, _ in sonuc] == [yuksek["id"]]
    assert sonuc[0][1]["speaker"] == A


def test_esik_kosinusu_centroid_ile_olculur_yani_gurultu_dayanikli():
    d, egit = _kurulu(esik=0.9)
    d.izi.parca_isle(egit)
    s = d.ses(soru(300.0), acili(0.95))
    assert d.izi.karar(s)["speaker"] == A
    s2 = d.ses(soru(310.0), acili(0.85))
    assert d.izi.karar(s2) is None


def test_marj_ikinci_kisiye_yakinsa_atanmaz():
    # esik 0.5: yalniz marj belirleyici
    d2, egit2 = _kurulu(esik=0.5)
    d2.izi.parca_isle(egit2)
    esit = d2.ses(soru(300.0), birim(1, 1))
    assert d2.izi.karar(esit) is None                         # marj ~0
    yakin = d2.ses(soru(310.0), birim(1.0, 0.88))             # fark ~0.09
    assert d2.izi.karar(yakin) is None
    uzak = d2.ses(soru(320.0), birim(1.0, 0.84))              # fark ~0.12
    k = d2.izi.karar(uzak)
    assert k is not None and k["speaker"] == A and k["ses_izi"]["marj"] >= 0.10


def test_en_az_5_klip_olmayan_kisiye_atanmaz():
    d = Duzenek()
    d.izi.parca_isle(d.egit(B, 6, 1, 100.0) + d.egit(A, 4, 0, 0.0))
    s = d.ses(soru(300.0), acili(0.98))
    assert d.izi.karar(s) is None                             # A: 4 klip
    d.izi.parca_isle(d.egit(A, 5, 0, 500.0)[4:])              # 5. klip
    assert d.izi.merkezler.klip_sayisi(A) == 5
    assert d.izi.karar(s)["speaker"] == A


def test_az_klipli_ikinci_kisi_yine_rakip_sayilir():
    """Ikinci kisi 1 klipli bile olsa marj ona gore olculur (yanlis atamayi onler)."""
    d = Duzenek(esik=0.5)
    d.izi.parca_isle(d.egit(A, 6, 0, 0.0) + d.egit(B, 1, 1, 100.0))
    s = d.ses(soru(300.0), birim(1, 1))
    assert d.izi.karar(s) is None


def test_tek_taninan_kisi_varken_atanmaz():
    d = Duzenek()
    d.izi.parca_isle(d.egit(A, 8, 0))
    s = d.ses(soru(300.0), acili(0.99))
    assert d.izi.karar(s) is None


def test_ilk_ogrenme_ayni_parcada_kesin_satirlardan_sonra_soru_cozulur():
    """Parca icinde sira onemsiz: once tum kesin satirlar ogrenilir, sonra '?' karara baglanir."""
    d = Duzenek()
    kes = d.egit(A, 6, 0, 100.0) + d.egit(B, 6, 1, 200.0)
    s = d.ses(soru(10.0), acili(0.9))
    sonuc = d.izi.parca_isle([s] + kes)
    assert [x["id"] for x, _ in sonuc] == [s["id"]]


# ---------------------------------------------------------------- atama sonucu: tavan 0.6, "Ad?", sahip degil

def test_atanan_satir_spk_guven_tavan_06_ad_soru_isareti_ve_kesin_degil():
    d, egit = _kurulu()
    d.izi.parca_isle(egit)
    s = d.ses(soru(300.0), acili(0.99))
    (_, alan), = d.izi.parca_isle([s])
    assert d.izi.uygula(s, alan) is True
    assert s["speaker"] == A and s["kaynak_konusmaci"] == "ses_izi" and s["spk_neden"] == "ses_izi"
    assert s["spk_guven"] <= 0.6
    assert gorunen_ad(s) == A + "?"
    assert not kesin_ad_mi(s)
    assert set(alan["ses_izi"]) == {"skor", "marj", "klip"}


def test_tavan_config_ile_yukseltilemez():
    d, egit = _kurulu(tavan=0.99)
    d.izi.parca_isle(egit)
    s = d.ses(soru(300.0), acili(0.99))
    assert d.izi.karar(s)["spk_guven"] == 0.6


def test_uygula_arada_altyazi_karari_geldiyse_yazmaz():
    d, egit = _kurulu()
    d.izi.parca_isle(egit)
    s = d.ses(soru(300.0), acili(0.99))
    (_, alan), = d.izi.parca_isle([s])
    s.update(speaker=B, spk_guven=0.95, kapsama=True, spk_neden="baskin")
    assert d.izi.uygula(s, alan) is False and s["speaker"] == B and "kaynak_konusmaci" not in s


def test_altyazi_tabanli_satirlara_dokunulmaz():
    d, egit = _kurulu()
    d.izi.parca_isle(egit)
    digerleri = [
        d.ses(satir(B, 0.95, bas=400.0), acili(0.99)),                               # kesin Ad
        d.ses(satir(A, 0.5, bas=410.0), acili(0.99)),                                # Ad? (altyazi)
        d.ses(satir("?", 0.0, bas=420.0, spk_neden="kimse"), acili(0.99)),           # altyazi var, kimse yok
        d.ses(satir(f"{A} → {B}", 0.5, bas=430.0, devir=True), acili(0.99)),
        d.ses(satir(f"{A} / {B}", 0.0, bas=440.0, cakisma=True), acili(0.99)),
        d.ses(soru(450.0, akis="mikrofon"), acili(0.99)),
    ]
    once = copy.deepcopy(digerleri)
    sonuc = d.izi.parca_isle(digerleri)
    assert sonuc == [] and digerleri == once


# ---------------------------------------------------------------- ogrenme kurallari

def test_yalniz_kesin_satirlar_ogrenilir_cikarici_cagrisi_da_o_kadar():
    d = Duzenek()
    uygun = d.egit(A, 3, 0)
    elenen = [d.ses(satir(A, 0.89, bas=200.0), birim(1)), d.ses(kesin(A, 210.0, cakisma=True), birim(1)),
              d.ses(kesin(A, 220.0, sure=1.9), birim(1)), d.ses(kesin(A, 230.0, akis="mikrofon"), birim(1)),
              d.ses(satir("?", 0.0, bas=240.0), birim(1))]
    d.izi.parca_isle(uygun + elenen)
    assert d.cik.cagri == 3 and d.izi.merkezler.klip_sayisi(A) == 3


def test_ayni_satir_tekrar_islenince_cift_sayilmaz():
    d = Duzenek()
    kes = d.egit(A, 3, 0)
    d.izi.parca_isle(kes)
    d.izi.parca_isle(kes)
    assert d.izi.merkezler.klip_sayisi(A) == 3 and d.cik.cagri == 3


def test_halkadan_cikmis_ses_ya_da_cikarici_hatasi_sessizce_atlanir():
    d = Duzenek()
    yok = kesin(A, 0.0)                                       # halkada ses yok
    d.izi.parca_isle([yok])
    assert d.izi.merkezler.klip_sayisi(A) == 0

    class Bozuk(ses_izi.Cikarici):
        def vektor(self, ses_16k):
            raise RuntimeError("model")
    d2 = Duzenek()
    d2.izi.cikarici = Bozuk()
    s = d2.ses(soru(5.0), birim(1))
    assert d2.izi.parca_isle([d2.ses(kesin(A, 50.0), birim(1)), s]) == [] and s["speaker"] == "?"


def test_kisa_ses_kirpilmis_halka_atlanir():
    d = Duzenek()
    s = d.ses(kesin(A, 0.0), birim(1))
    d.halka.anahtar.pop((s["akis"], s["bas"]))
    d.izi.ses_al_fn = lambda akis, bas, bit: np.zeros(8000, dtype=np.float32)      # 0.5 sn (satir 3 sn diyor)
    d.izi.parca_isle([s])
    assert d.izi.merkezler.klip_sayisi(A) == 0 and d.cik.cagri == 0


def test_merkezler_gecersiz_vektor_ve_boyut_tutarsizligi():
    m = ses_izi.Merkezler()
    assert not m.ekle(A, np.zeros(DIM)) and not m.ekle(A, [float("nan")] * DIM) and not m.ekle(A, [])
    assert m.ekle(A, birim(1)) and not m.ekle(A, np.ones(3))
    assert m.sirala(np.ones(3)) == [] and m.sirala(np.zeros(DIM)) == []
    assert m.klip_sayisi(A) == 1


def test_uzun_klip_ortadan_kirpilir():
    d = Duzenek()
    uzun = d.ses(kesin(A, 0.0, sure=90.0), birim(1))
    gorulen = []
    eski = d.cik.vektor
    d.cik.vektor = lambda ses: (gorulen.append(len(ses)), eski(ses))[1]
    d.izi.parca_isle([uzun])
    assert gorulen == [int(ses_izi.MAKS_SN * 16000)]


# ---------------------------------------------------------------- kapali yol / kurulum

def test_kur_bayrak_kapaliyken_none_ve_log_yok():
    log = []
    assert ses_izi.kur({}, Halka(), log=log.append) is None
    assert ses_izi.kur({"ses_izi": False, "ses_izi_model": "x.onnx"}, Halka(), log=log.append) is None
    assert ses_izi.kur({"ses_izi": "true"}, Halka(), log=log.append) is None      # yalniz gercek bool
    assert log == []


def test_kur_sherpa_yoksa_tek_log_satiri(tmp_path, monkeypatch):
    model = tmp_path / "gomme.onnx"
    model.write_bytes(b"x")
    monkeypatch.setitem(__import__("sys").modules, "sherpa_onnx", None)          # import ImportError verir
    log = []
    assert ses_izi.kur({"ses_izi": True, "ses_izi_model": str(model)}, Halka(), log=log.append) is None
    assert len(log) == 1 and "sherpa-onnx" in log[0]


@pytest.mark.parametrize("model", [None, "", "yok.onnx", "model.bin"])
def test_kur_model_yoksa_tek_log_satiri(tmp_path, model):
    if model == "model.bin":
        (tmp_path / model).write_bytes(b"x")
        model = str(tmp_path / model)
    log = []
    assert ses_izi.kur({"ses_izi": True, "ses_izi_model": model}, Halka(), log=log.append) is None
    assert len(log) == 1 and "ses_izi_model" in log[0]


def test_kur_ses_kaynagi_yoksa_kapali():
    log = []
    assert ses_izi.kur({"ses_izi": True}, None, cikarici=SahteCikarici(), log=log.append) is None and len(log) == 1


def test_kur_sherpa_sarmalayici_sahte_modulle(tmp_path):
    model = tmp_path / "gomme.onnx"
    model.write_bytes(b"x")
    kayit = {}

    class Akim:
        def accept_waveform(self, sample_rate, waveform):
            kayit["sr"], kayit["n"] = sample_rate, len(waveform)

        def input_finished(self):
            kayit["bitti"] = True

    class Cik:
        def __init__(self, cfg):
            kayit["cfg"] = cfg

        def create_stream(self):
            return Akim()

        def is_ready(self, akim):
            return True

        def compute(self, akim):
            return [0.0, 3.0, 4.0]

    sherpa = types.SimpleNamespace(
        SpeakerEmbeddingExtractorConfig=lambda **kw: types.SimpleNamespace(validate=lambda: True, **kw),
        SpeakerEmbeddingExtractor=Cik)
    log = []
    izi = ses_izi.kur({"ses_izi": True, "ses_izi_model": str(model), "ses_izi_esik": 0.8}, Halka(), log=log.append,
                      sherpa=sherpa)
    assert isinstance(izi, ses_izi.SesIzi) and isinstance(izi.cikarici, ses_izi.SherpaCikarici) and log == []
    assert izi.esik == 0.8 and kayit["cfg"].model == str(model) and kayit["cfg"].provider == "cpu"
    v = izi.cikarici.vektor(np.zeros(32000, dtype=np.float32))
    assert list(v) == [0.0, 3.0, 4.0] and kayit["sr"] == 16000 and kayit["n"] == 32000 and kayit["bitti"]


def test_kur_model_yuklenemezse_tek_log_satiri(tmp_path):
    model = tmp_path / "gomme.onnx"
    model.write_bytes(b"x")

    def patla(**kw):
        raise OSError("bozuk")
    sherpa = types.SimpleNamespace(SpeakerEmbeddingExtractorConfig=patla, SpeakerEmbeddingExtractor=None)
    log = []
    assert ses_izi.kur({"ses_izi": True, "ses_izi_model": str(model)}, Halka(), log=log.append, sherpa=sherpa) is None
    assert len(log) == 1 and "yüklenemedi" in log[0]


@pytest.mark.parametrize("ham,beklenen", [(None, 0.75), ("x", 0.75), (0.2, 0.75), (1.5, 0.75), (0.8, 0.8), ("0.9", 0.9)])
def test_kur_esik_gecersizse_varsayilan(ham, beklenen):
    izi = ses_izi.kur({"ses_izi": True, "ses_izi_esik": ham}, Halka(), cikarici=SahteCikarici())
    assert izi.esik == beklenen and izi.marj == 0.10 and izi.min_klip == 5 and izi.tavan == 0.6


# ---------------------------------------------------------------- Motor kancasi

def motor(tmp_path, ad="m", **kw):
    olaylar = []
    kw.setdefault("katilimcilar", [A, B, C])
    m = motor_mod.Motor(str(tmp_path / ad), "Test", dt.date.fromtimestamp(T0), duzelt=False,
                        olay=lambda t, v: olaylar.append((t, v)), **kw)
    m.olaylar = olaylar
    return m


def parca_kur(m, satirlar):
    """satirlari acik parcaya koyup kapatir (Motor._parca_kapat_simdi); _isle'yi elle calistirmak icin submit'i atlar."""
    m.mevcut = list(satirlar)
    m._parca_kapat_simdi("test", None)
    for f in m.isler:
        f.result(timeout=30)
    return m._parca_veri[m.parca_no]


@pytest.fixture
def llmsiz(monkeypatch):
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: llm.normalize_ozet({}))


def test_motor_bayrak_kapaliyken_hicbir_satir_degismez(tmp_path, llmsiz):
    m = motor(tmp_path)
    assert m.ses_izi is None and m.ses_izi_kur({"ses_izi": False}, Halka(), cikarici=SahteCikarici()) is False
    d = Duzenek()
    satirlar = d.egit(A, 6, 0, 0.0) + d.egit(B, 6, 1, 100.0) + [d.ses(soru(300.0), acili(0.99))]
    once = copy.deepcopy(satirlar)
    veri = parca_kur(m, satirlar)
    assert veri["satirlar"] == once and d.cik.cagri == 0
    m.kapat()


def test_motor_kanca_soru_satirini_ad_soru_isaretine_cevirir(tmp_path, llmsiz):
    m = motor(tmp_path)
    d = Duzenek()
    assert m.ses_izi_kur({"ses_izi": True}, d.halka, cikarici=d.cik) is True
    d.izi = m.ses_izi
    kes = d.egit(A, 6, 0, 0.0) + d.egit(B, 6, 1, 100.0)
    hedef = d.ses(soru(300.0), acili(0.95))
    kimse = d.ses(satir("?", 0.0, bas=310.0, spk_neden="kimse"), acili(0.95))      # altyazi tabanli: dokunulmaz
    adli = d.ses(satir(B, 0.5, bas=320.0), acili(0.95))
    kimse0, adli0 = copy.deepcopy(kimse), copy.deepcopy(adli)
    satirlar = kes + [hedef, kimse, adli]
    for s in satirlar:
        m._jsonl(s)
    veri = parca_kur(m, satirlar)
    h = next(s for s in veri["satirlar"] if s["id"] == hedef["id"])
    assert h["speaker"] == A and h["spk_guven"] <= 0.6 and h["kaynak_konusmaci"] == "ses_izi"
    assert gorunen_ad(h) == A + "?" and not kesin_ad_mi(h)
    assert next(s for s in veri["satirlar"] if s["id"] == kimse["id"]) == kimse0
    assert next(s for s in veri["satirlar"] if s["id"] == adli["id"]) == adli0
    # olay + jsonl guncelleme kaydi + kalici parca dosyasi
    g = [v for t, v in m.olaylar if t == "satir_guncelle" and v["id"] == hedef["id"]]
    assert len(g) == 1 and g[0]["gorunen"] == A + "?"
    kayit = [k for k in motor_mod.altyazi_kayitlari(m.klasor) if k["id"] == hedef["id"]]
    assert kayit and kayit[-1]["speaker"] == A and kayit[-1]["kaynak_konusmaci"] == "ses_izi"
    assert m.parca_oku(veri["sira"])["satirlar"][-3]["speaker"] == A
    assert A in m.katilimcilar and m.katilimcilar.count(A) == 1
    m.kapat()


def test_motor_ses_izi_sahip_uretmez_ve_katilimci_eklemez(tmp_path, llmsiz):
    m = motor(tmp_path, katilimcilar=[])
    d = Duzenek()
    m.ses_izi_kur({"ses_izi": True}, d.halka, cikarici=d.cik)
    kes = d.egit(A, 6, 0, 0.0) + d.egit(B, 6, 1, 100.0)
    hedef = d.ses(soru(300.0), acili(0.95))
    parca_kur(m, kes + [hedef])
    assert hedef["speaker"] == A and not kesin_ad_mi(hedef)
    assert "Ayşe Demir" not in m.katilimcilar and "Ayşe Demir?" not in m.katilimcilar
    metin = "Raporu yarın ben hazırlarım."
    hedef["text"] = metin
    kesin_satir = satir(A, 0.95, bas=900.0, text=metin)
    assert motor_mod._sahip_belirle(kesin_satir, [(A, False)], [A, B], None)[0] == "kesin"     # kontrol: ayni metin kesin
    assert motor_mod._sahip_belirle(hedef, [(A, False)], [A, B], None)[0] == "belirsiz"        # ses izi satiri sahip degil
    m.kapat()


def test_motor_kanca_hata_verirse_parca_islenir(tmp_path, llmsiz):
    m = motor(tmp_path)
    loglar = []
    m.log = loglar.append

    class Patlayan:
        def parca_isle(self, satirlar):
            raise RuntimeError("model")
    m.ses_izi = Patlayan()
    veri = parca_kur(m, [satir(A, 0.95, bas=0.0)])
    assert veri["ozet"] is not None and veri["satirlar"][0]["speaker"] == A
    assert any("ses izi atlandı" in x for x in loglar)
    m.kapat()


def test_motor_ses_izi_kur_tek_log_satiri_sherpa_yok(tmp_path, monkeypatch):
    m = motor(tmp_path)
    loglar = []
    m.log = loglar.append
    model = tmp_path / "g.onnx"
    model.write_bytes(b"x")
    monkeypatch.setitem(__import__("sys").modules, "sherpa_onnx", None)
    assert m.ses_izi_kur({"ses_izi": True, "ses_izi_model": str(model)}, Halka()) is False
    assert m.ses_izi is None and len(loglar) == 1
    m.kapat()


def test_motor_yeniden_isleme_cift_ogrenmez(tmp_path, llmsiz):
    m = motor(tmp_path)
    d = Duzenek()
    m.ses_izi_kur({"ses_izi": True}, d.halka, cikarici=d.cik)
    kes = d.egit(A, 6, 0, 0.0) + d.egit(B, 6, 1, 100.0)
    veri = parca_kur(m, kes)
    cagri = d.cik.cagri
    m._isle(veri, False)                                      # gec satir yolu: parca yeniden islenir
    assert d.cik.cagri == cagri and m.ses_izi.merkezler.klip_sayisi(A) == 6
    m.kapat()


# ---------------------------------------------------------------- tools/ses_izi_dene.py

def _kayit(tmp_path, bilinmeyen=False, blok=6):
    """Sentetik Faz 0 kaydi: 12 dk, her dk 6 klip (10 sn arayla, 3 sn), A/B dönüşümlü; her 3. dk altyazi kapali."""
    import json
    import wave
    kl = tmp_path / "toplanti"
    (kl / "olcum" / "klipler").mkdir(parents=True)
    vekt, no = {}, 0
    with open(kl / "olcum" / "klipler.jsonl", "w", encoding="utf-8") as fk, \
            open(kl / "olcum" / "gercek.jsonl", "w", encoding="utf-8") as fg:
        for dk in range(12):
            for j in range(blok):
                no += 1
                kisi = (A, B)[j % 2]
                anahtar = no
                v = np.zeros(DIM)
                v[j % 2] = 1.0
                v[2 + (no % 4)] = 0.05 * (no % 3)                       # kume ici hafif gurultu
                vekt[anahtar] = v
                fk.write(json.dumps({"no": no, "akis": "loopback", "bas": T0 + dk * 60 + j * 10, "sure": 3.0,
                                     "bit": T0 + dk * 60 + j * 10 + 3}) + "\n")
                gercek_kisi = kisi
                if bilinmeyen and dk == 5 and j == 0:
                    gercek_kisi = "?"                                    # kapali blokta, sesi A'ya benzeyen ama bilinmeyen kisi
                fg.write(json.dumps({"klip_no": no, "speaker": gercek_kisi, "cakisma": False, "halusinasyon": False,
                                     "text": "x"}) + "\n")
                with wave.open(str(kl / "olcum" / "klipler" / f"{no:04d}.wav"), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(16000)
                    w.writeframes((np.full(48000, anahtar, dtype="<i2")).tobytes())
    return str(kl), vekt


class DosyaCikarici(ses_izi.Cikarici):
    def __init__(self, vekt):
        self.vekt = vekt

    def vektor(self, ses_16k):
        return self.vekt[int(round(float(ses_16k[0]) * 32767))]


def _arac():
    import importlib.util
    import os
    yol = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "ses_izi_dene.py")
    spec = importlib.util.spec_from_file_location("ses_izi_dene", yol)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_arac_kapi_gecer_ve_esik_onerir(tmp_path):
    arac = _arac()
    klasor, vekt = _kayit(tmp_path)
    r = arac.calistir(klasor, DosyaCikarici(vekt), esikler=(0.5, 0.75, 0.9), blok_dk=1.0, kapali_her=3)
    assert r["klip"] == 72 and r["gomulen"] == 72 and r["soru"] == 24
    for s in r["esikler"]:
        assert s["yanlis"] == 0 and s["dogru"] == s["cozulen"]
    s75 = next(s for s in r["esikler"] if s["esik"] == 0.75)
    assert s75["kapsama"] == 1.0 and s75["dogru_oran"] == 1.0
    assert r["rtf"] is not None and r["rtf"] < 0.3
    assert r["kapi"]["gecti"] and r["onerilen_esik"] == 0.5
    cikti = []
    arac.yazdir(r, cikti.append)
    assert any("KAPI GEÇTİ" in x for x in cikti)
    assert "Ayşe" not in " ".join(cikti) and "Burak" not in " ".join(cikti)           # yalniz sayi


def test_arac_bilinmeyen_kisiye_atama_yanlis_sayilir_kapi_gecmez(tmp_path):
    arac = _arac()
    klasor, vekt = _kayit(tmp_path, bilinmeyen=True)
    r = arac.calistir(klasor, DosyaCikarici(vekt), esikler=(0.5, 0.9), blok_dk=1.0, kapali_her=3)
    assert all(s["yanlis"] == 1 and s["bilinmeyene"] == 1 for s in r["esikler"])
    assert not r["kapi"]["gecti"] and r["onerilen_esik"] is None and r["yanlis_klip"]["0.5"] == [31]


def test_arac_rtf_kapisi(tmp_path):
    arac = _arac()
    klasor, vekt = _kayit(tmp_path)

    class Yavas(DosyaCikarici):
        def vektor(self, ses_16k):
            import time
            time.sleep(0.002)
            return super().vektor(ses_16k)
    # klip basina 3 sn ses, 0.002 sn gomme: RTF ~0.0007 (kapi icinde); kapi disi RTF oner() ile sinanir
    r = arac.calistir(klasor, Yavas(vekt), esikler=(0.75,), blok_dk=1.0, kapali_her=3)
    assert r["rtf"] < 0.3 and r["kapi"]["gecti"]
    k = arac.oner([dict(esik=0.75, soru=24, cozulen=24, dogru=24, yanlis=0, dogru_oran=1.0)], 0.31)
    assert k[0] is None and "RTF" in k[1][0]


def test_arac_az_dogru_oran_kapiyi_gecirmez():
    arac = _arac()
    esik, nedenler = arac.oner([dict(esik=0.7, soru=20, cozulen=5, dogru=5, yanlis=0, dogru_oran=0.25)], 0.1)
    assert esik is None and "doğru oran" in nedenler[0]
    esik, _ = arac.oner([dict(esik=0.7, soru=20, cozulen=12, dogru=11, yanlis=1, dogru_oran=0.55),
                         dict(esik=0.8, soru=20, cozulen=10, dogru=10, yanlis=0, dogru_oran=0.5)], 0.1)
    assert esik == 0.8


def test_arac_main_model_yoksa_kod_2_ve_kapi_kodlari(tmp_path, capsys):
    arac = _arac()
    klasor, vekt = _kayit(tmp_path)
    assert arac.main([klasor]) == 2                                   # --model yok
    assert arac.main([str(tmp_path / "bos")]) == 2                    # kayit yok
    assert arac.main([klasor, "--blok-dk", "1", "--esikler", "0.5,0.75", "--json", str(tmp_path / "r.json")],
                     cikarici=DosyaCikarici(vekt)) == 0
    import json
    assert json.load(open(tmp_path / "r.json", encoding="utf-8"))["kapi"]["gecti"] is True
    assert arac.main([klasor, "--esikler", "x"], cikarici=DosyaCikarici(vekt)) == 2
    capsys.readouterr()
