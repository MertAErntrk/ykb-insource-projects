"""Faz 1 motor olay akisi (MIMARI_v3 §3): klip -> satir v2 'beklemede…' -> konusmaci karari (satir_guncelle),
yanki klibi atilir (satir_sil), devirde yeniden kesme (satir_degistir) ya da bolunemedi ('A → B'), parca kapanisi
bekleyen kararlari bekler (en fazla 20 sn), Motor.yukle eski kayit gocu, gosterim kurali, katilimci elemesi,
altyazi metninin transkripte girmemesi (yalniz opt-in yedek, STT yokken), STT boslugu nota.
Ses cihazi, ag, LLM gerekmez; adlar sentetik."""
import datetime as dt
import json
import time as gercek_time

import pytest

import duzelt as duzelt_mod
import llm
import motor as motor_mod
import saat as saat_mod
from metin import gorunen_ad, kesin_ad_mi

T0 = 1760170000.0
A, B, C, BEN = "Ayşe Demir", "Burak Şahin", "Cem Yıldız", "Deniz Kaya"


# ---------------------------------------------------------------- duzenek

class Saat:
    """saat.simdi yerine: time() elle ilerler, digerleri gercek."""

    def __init__(self, t=T0):
        self.t = t

    def time(self):
        return self.t

    def __getattr__(self, ad):
        return getattr(gercek_time, ad)


def saat_kur(monkeypatch):
    """saat.simdi -> Saat (diger test modulleri kendi 'saat' fixture'inda kullanir)."""
    s = Saat()
    monkeypatch.setattr(saat_mod, "simdi", s.time)          # tum modullerin zaman kaynagi (saat.simdi)
    monkeypatch.setattr(saat_mod, "duvar", lambda t: t)     # sanal saatte duvar ofseti 0
    monkeypatch.setattr(saat_mod, "duvardan", lambda t: t)
    return s


@pytest.fixture
def saat(monkeypatch):
    return saat_kur(monkeypatch)


def klip(no, bas, bit, metin="Bütçe tablosunu güncelledim.", akis="loopback", ust=None):
    return {"tur": "klip", "klip_no": no, "akis": akis, "bas": T0 + bas, "bit": T0 + bit, "sure": bit - bas,
            "text": metin, "ts": dt.datetime.fromtimestamp(T0 + bas).strftime("%H:%M:%S"), "rms": 0.1,
            "zarf": [0.1] * int((bit - bas) * 10), "ust_klip": ust, "supheli": False}


def karar(no, speaker, guven=0.9, **ek):
    k = {"klip_no": no, "akis": "loopback", "speaker": speaker, "spk_guven": guven, "adaylar": [[speaker, guven]],
         "devir": False, "cakisma": False, "at": False, "bol": None, "neden": "baskin", "kapsama": True,
         "kimlik_turu": "kisi", "cizelge_canli": True}      # K3: kapsama var, panel donuk degil, klipten sonra degismis
    k.update(ek)
    return k


class SahteKonusmaci:
    """Motor'un olay akisini konusmaci algoritmasindan ayri sinamak icin: kararlar elle verilir."""

    def __init__(self):
        self.kararlar, self.bitir_kararlari, self.klipler, self.okumalar = [], [], [], []
        self.bolunemeyen, self.bitir_sayisi, self.kulaklik, self.kapsama = [], 0, None, True
        self.bekleyen_fn = None
        self.neden, self.bosluk_sn, self.l_olcum = None, 0.0, 0     # K3: durum()['neden'] / ['bosluk_sn']; L_yeniden_olc sayaci

    def klip_ekle(self, k):
        self.klipler.append(k)

    def okuma_ekle(self, o):
        self.okumalar.append(o)

    def kulaklik_ayarla(self, d):
        self.kulaklik = d

    def kontrol(self, an):
        out, self.kararlar = self.kararlar, []
        return out

    def bitir(self):
        self.bitir_sayisi += 1
        out, self.bitir_kararlari = self.bitir_kararlari, []
        return out

    def bekleyen(self, bas=None, bit=None):
        return self.bekleyen_fn(bas, bit) if self.bekleyen_fn else 0

    def bolunemedi(self, no):
        self.bolunemeyen.append(no)
        return karar(no, f"{A} → {B}", 0.5, devir=True, neden="devir_bolunemedi")

    def durum(self):
        return {"L": 2.0, "MAD": 0.0, "R": 0.6, "kapsama": self.kapsama, "araliklar": 0,
                "neden": self.neden, "bosluk_sn": self.bosluk_sn}

    def L_yeniden_olc(self):
        self.l_olcum += 1

    def kimlik_turu(self, ad):
        return "kisi"

    def araliklar_listesi(self):
        return []


def motor(tmp_path, ad="t", sahte=True, olaylar=None, **kw):
    kw.setdefault("ben", BEN)
    kw.setdefault("katilimcilar", [A, B, C, BEN])
    m = motor_mod.Motor(str(tmp_path / ad), "Test", dt.date.fromtimestamp(T0), duzelt=False,
                        olay=(lambda t, v: olaylar.append((t, v))) if olaylar is not None else None, **kw)
    m._isle = lambda veri, duzelt: None                   # LLM yok
    if sahte:
        m.konusmaci = SahteKonusmaci()
    return m


def okumalar(sozler, bitis, R=0.6, hiz=3.0, lag=2.0, kapali=(), metin=None):
    """Teams paneli: her soz (kim, s, e) ayri satir, s+lag'dan itibaren hiz kelime/sn buyur, e+lag'da durur.
    R araliginda okuma; kapali araliklarda grup yok (altyazi kapali). metin: satir metni (varsayilan anlamsiz)."""
    out, t, no = [], 0.0, 0
    while t <= bitis:
        no += 1
        grup = not any(a <= t < b for a, b in kapali)
        gor = []
        if grup:
            for i, (kim, s, e) in enumerate(sozler):
                if t < s + lag:
                    continue
                n = max(1, min(int((e - s) * hiz), 1 + int((t - s - lag) * hiz)))
                kelime = (metin or "altyazi%d kelime" % i).split()
                gor.append([kim, " ".join(kelime[j % len(kelime)] + str(j) for j in range(n))])
        out.append({"tur": "okuma", "okuma_no": no, "bas_epoch": T0 + t - 0.05, "bit_epoch": T0 + t,
                    "grup": grup, "gorunen": gor[-12:]})
        t = round(t + R, 3)
    return out


def akit(m, saat, sozler, klipler, bitis, gecikme=0.6, hal=None, **kw):
    """Okumalari ve klip olaylarini (bit + STT gecikmesi aninda) zaman sirasiyla motora verir; her olaydan sonra
    kontrol(). klipler: [(no, akis, bas, bit, metin)] (T0'a gore sn). hal: {klip_no: ses.py icsel hal_puani}."""
    olay = [(o["bit_epoch"], 0, "okuma", o) for o in okumalar(sozler, bitis, **kw)]
    for no, akis, bas, bit, metin in klipler:
        ev = klip(no, bas, bit, metin, akis)
        if hal and no in hal:
            ev["hal_puani"] = hal[no]
        olay.append((T0 + bit + gecikme, 1, "klip", ev))
    olay.sort(key=lambda x: (x[0], x[1]))
    for t, _, tur, o in olay:
        saat.t = t
        (m.okuma_ekle if tur == "okuma" else m.ses_olayi)(o)
        m.kontrol()
    saat.t = T0 + bitis + 20
    m.kontrol()


def jsonl(m):
    return motor_mod.altyazi_kayitlari(m.klasor)


# ---------------------------------------------------------------- beklemede -> karar

def test_klip_satiri_hemen_beklemede_karar_gelince_guncellenir(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    s = m.ses_olayi(klip(7, 1.0, 3.5))
    yeni = [v for t, v in olaylar if t == "satir"]
    assert len(yeni) == 1 and yeni[0]["gorunen"] == "beklemede…" and gorunen_ad(yeni[0]) == "beklemede…"
    for alan in ("surum", "klip_no", "akis", "bas", "bit", "raw", "text", "speaker", "spk_guven", "adaylar", "devir",
                 "cakisma", "supheli", "belirsiz", "duzeltmeler", "kaynak"):
        assert alan in s, alan
    assert (s["surum"], s["klip_no"], s["kaynak"], s["bas"], s["bit"]) == (2, 7, "ses", T0 + 1.0, T0 + 3.5)
    assert jsonl(m)[0]["durum"] == "beklemede"
    assert m.konusmaci.klipler[0]["klip_no"] == 7                  # klip konusmaciya gitti
    m.konusmaci.kararlar = [karar(7, A, 0.92, adaylar=[[A, 0.95], [B, 0.03]])]
    m.kontrol()
    g = [v for t, v in olaylar if t == "satir_guncelle"]
    assert len(g) == 1 and g[0]["id"] == s["id"] and g[0]["speaker"] == A and g[0]["gorunen"] == A
    assert m.mevcut[0]["spk_guven"] == 0.92 and m.mevcut[0]["durum"] == "karar"
    kayit = jsonl(m)
    assert len(kayit) == 1 and kayit[0]["speaker"] == A and kayit[0]["adaylar"] == [[A, 0.95], [B, 0.03]]
    assert A in m.katilimcilar
    m.kapat()


def test_metinsiz_klip_satir_uretmez_karari_yok_sayilir(tmp_path, saat):
    m = motor(tmp_path)
    assert m.ses_olayi(klip(1, 0, 2, metin="  ")) is None
    m.konusmaci.kararlar = [karar(1, A)]
    m.kontrol()
    assert m.mevcut == [] and jsonl(m) == [] and len(m.konusmaci.klipler) == 1
    m.kapat()


# ---------------------------------------------------------------- yanki

def test_yanki_karari_satiri_siler(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    m.ses_olayi(klip(1, 0, 2, "Test ortamı perşembe hazır olacak."))
    yanki = m.ses_olayi(klip(2, 0.1, 2.0, "Test ortamı perşembe hazır olacak.", akis="mikrofon"))
    m.konusmaci.kararlar = [karar(1, A), karar(2, "?", 0.0, at=True, neden="yanki", akis="mikrofon")]
    m.kontrol()
    assert [s["klip_no"] for s in m.mevcut] == [1]
    assert ("satir_sil", {"id": yanki["id"], "neden": "yanki"}) in olaylar
    assert [s["klip_no"] for s in jsonl(m)] == [1]
    assert m.mevcut_tok == m._tok(m.mevcut[0])
    m.kapat()


# ---------------------------------------------------------------- yeniden kesme

def test_devir_yeniden_kesilir_parcalar_ozgun_satirin_yerine_girer(tmp_path, saat):
    olaylar, istek = [], []
    m = motor(tmp_path, olaylar=olaylar)
    m.yeniden_kes_fn = lambda no, an: istek.append((no, an)) or True
    m.ses_olayi(klip(1, 0, 2, "Önceki satır."))
    ozgun = m.ses_olayi(klip(2, 3, 9, "Raporu ben yazarım sen de testi koş."))
    m.ses_olayi(klip(3, 10, 12, "Sonraki satır."))
    m.konusmaci.kararlar = [karar(2, f"{A} → {B}", 0.5, devir=True, bol=T0 + 5.5, neden="devir")]
    m.kontrol()
    assert istek == [(2, T0 + 5.5)]
    assert m.mevcut[1]["durum"] == "beklemede"                     # parcalar beklenirken ad yazilmaz
    p2 = m.ses_olayi(klip(5, 5.5, 9, "Sen de testi koş.", ust=2))   # parcalar sirasiz gelebilir
    assert [s["klip_no"] for s in m.mevcut] == [1, 2, 3]
    p1 = m.ses_olayi(klip(4, 3, 5.5, "Raporu ben yazarım.", ust=2))
    assert [s["klip_no"] for s in m.mevcut] == [1, 4, 5, 3]
    d = [v for t, v in olaylar if t == "satir_degistir"]
    assert len(d) == 1 and d[0]["eski_id"] == ozgun["id"] and [y["id"] for y in d[0]["yeni"]] == [p1["id"], p2["id"]]
    m.konusmaci.kararlar = [karar(4, A), karar(5, B)]
    m.kontrol()
    assert [(s["klip_no"], s["speaker"]) for s in m.mevcut] == [(1, "?"), (4, A), (5, B), (3, "?")]
    assert [s["klip_no"] for s in jsonl(m)] == [1, 4, 5, 3]          # diskte de ozgun satirin yerinde
    assert m.konusmaci.bolunemeyen == []
    m.kapat()


@pytest.mark.parametrize("kes_fn", [None, lambda no, an: False])
def test_bolunemezse_a_b_karari(tmp_path, saat, kes_fn):
    m = motor(tmp_path)
    m.yeniden_kes_fn = kes_fn
    m.ses_olayi(klip(2, 3, 4.5))
    m.konusmaci.kararlar = [karar(2, f"{A} → {B}", 0.5, devir=True, bol=T0 + 3.7)]
    m.kontrol()
    s = m.mevcut[0]
    assert m.konusmaci.bolunemeyen == [2]
    assert s["speaker"] == f"{A} → {B}" and s["devir"] and gorunen_ad(s) == f"{A} → {B}"
    assert not kesin_ad_mi(s) and f"{A} → {B}" not in m.katilimcilar
    m.kapat()


def test_kesim_parcalari_gelmezse_sure_dolunca_bolunemedi(tmp_path, saat):
    m = motor(tmp_path)
    m.yeniden_kes_fn = lambda no, an: True
    m.ses_olayi(klip(2, 3, 9))
    m.konusmaci.kararlar = [karar(2, f"{A} → {B}", 0.5, devir=True, bol=T0 + 6)]
    m.kontrol()
    saat.t += motor_mod.KESIM_BEKLE_SN + 1
    m.kontrol()
    assert m.konusmaci.bolunemeyen == [2] and m.mevcut[0]["speaker"] == f"{A} → {B}"
    m.kapat()


def test_tek_parca_gelirse_ozgun_satir_tum_metniyle_korunur(tmp_path, saat):
    """Bulgu: suzgecte dusen / gec kalan parca yuzunden tek parca ozgun satirin yerine geciyor, diger yarinin metni
    ('Peki ya sen bakar misin?') transkriptten kayboluyordu. Ozgun satir 'A → B' olarak tum metniyle kalir."""
    m = motor(tmp_path)
    m.yeniden_kes_fn = lambda no, an: True
    m.ses_olayi(klip(2, 3, 9, "Raporu ben hazırlarım. Peki ya sen bakar mısın?"))
    m.konusmaci.kararlar = [karar(2, f"{A} → {B}", 0.5, devir=True, bol=T0 + 6)]
    m.kontrol()
    m.ses_olayi(klip(4, 3, 6, "Peki ya sen bakar mısın?", ust=2))
    saat.t += motor_mod.KESIM_BEKLE_SN + 1
    m.kontrol()
    assert [s["klip_no"] for s in m.mevcut] == [2] and [s["klip_no"] for s in jsonl(m)] == [2]
    assert m.mevcut[0]["text"] == "Raporu ben hazırlarım. Peki ya sen bakar mısın?"
    assert m.mevcut[0]["speaker"] == f"{A} → {B}" and m.konusmaci.bolunemeyen == [2]
    m.kapat()


@pytest.mark.parametrize("kapali", [False, True])
def test_gec_gelen_kesim_parcalari_ozgun_satirin_yerine_gecer_cift_satir_olmaz(tmp_path, saat, kapali):
    """Bulgu: parcalar KESIM_BEKLE_SN'den gec gelince ozgun satir 'A → B' tum metinle kaliyor, parcalar da ayri satir
    oluyordu (ayni cumle iki kez, iki ozete). Gec parcalar ikisi gelince ozgunun yerine gecer (kapanmis parcada da)."""
    m = motor(tmp_path)
    m.yeniden_kes_fn = lambda no, an: True
    m.ses_olayi(klip(2, 3, 9, "Bütçeyi ben güncellerim. Tabloyu sen kontrol et."))
    m.konusmaci.kararlar = [karar(2, f"{A} → {B}", 0.5, devir=True, bol=T0 + 6)]
    m.kontrol()
    saat.t += motor_mod.KESIM_BEKLE_SN + 1
    m.kontrol()
    assert m.mevcut[0]["speaker"] == f"{A} → {B}"
    if kapali:
        m.parca_kapat("token", zorla=True)
    m.ses_olayi(klip(4, 3, 6, "Bütçeyi ben güncellerim.", ust=2))
    m.ses_olayi(klip(5, 6, 9, "Tabloyu sen kontrol et.", ust=2))
    m.konusmaci.kararlar = [karar(4, A), karar(5, B)]
    m.kontrol()
    sat = m.tum_satirlar()
    assert [(s["klip_no"], s["speaker"]) for s in sat] == [(4, A), (5, B)]
    assert [s["klip_no"] for s in jsonl(m)] == [4, 5]
    if kapali:
        assert [s["klip_no"] for s in m.parca_oku(1)["satirlar"]] == [4, 5] and m.mevcut == []
    m.ses_olayi(klip(6, 6, 9, "Tabloyu sen kontrol et.", ust=2))         # fazladan parca: ayri satir olmaz
    assert [s["klip_no"] for s in m.tum_satirlar()] == [4, 5]
    m.kapat()


def test_metinsiz_parca_gelince_diger_parca_ozgun_satirin_yerine_gecer(tmp_path, saat):
    """Parcalardan birinin metni bos (STT o yarida soz duymadi): bos parca 'geldi' sayilir, diger parca ozgun satirin
    yerine gecer (ozgun satir 'A → B' kalmaz, parca bekletilmez)."""
    m = motor(tmp_path)
    m.yeniden_kes_fn = lambda no, an: True
    m.ses_olayi(klip(2, 3, 9, "Raporu ben hazırlarım."))
    m.konusmaci.kararlar = [karar(2, f"{A} → {B}", 0.5, devir=True, bol=T0 + 6)]
    m.kontrol()
    m.ses_olayi(klip(4, 3, 6, "", ust=2))
    m.ses_olayi(klip(5, 6, 9, "Raporu ben hazırlarım.", ust=2))
    m.konusmaci.kararlar = [karar(5, B)]
    m.kontrol()
    assert [(s["klip_no"], s["speaker"]) for s in m.tum_satirlar()] == [(5, B)]
    assert m.konusmaci.bolunemeyen == []
    m.kapat()


def test_gec_klip_kapanmis_parcaya_girer_transkript_kronolojik(tmp_path, saat):
    """Bulgu: STT kesintisi sonrasi gec cozulen klip acik parcaya giriyordu (transkript 1., 1200., 300. sn; parca
    araliklari monoton degil). Klip, bas'i dustugu kapanmis parcaya girer; parca yeniden yazilir ve ozetlenir."""
    m = motor(tmp_path)
    islenen = []
    m._isle = lambda veri, duzelt: islenen.append((veri["sira"], [s["klip_no"] for s in veri["satirlar"]]))
    m.ses_olayi(klip(1, 0, 2, "Birinci parçanın satırı."))
    m.konusmaci.kararlar = [karar(1, A)]
    m.kontrol()
    m.parca_kapat("token", zorla=True)
    m.ses_olayi(klip(2, 1200, 1202, "İkinci parçanın satırı."))
    m.konusmaci.kararlar = [karar(2, B)]
    m.kontrol()
    m.parca_kapat("token", zorla=True)
    m.ses_olayi(klip(3, 1500, 1502, "Açık parçanın satırı."))
    m.ses_olayi(klip(9, 300, 302, "Kesinti sonrası geç çözülen satır."))
    m.konusmaci.kararlar = [karar(9, C)]
    m.kontrol()
    assert [s["klip_no"] for s in m.parca_oku(1)["satirlar"]] == [1, 9]
    assert [s["klip_no"] for s in m.mevcut] == [3]
    assert [s["klip_no"] for s in m.tum_satirlar()] == [1, 9, 2, 3]
    assert m.parca_oku(1)["satirlar"][1]["speaker"] == C                # gec karar kapanmis parcaya yazildi
    p1, p2 = m.parca_oku(1), m.parca_oku(2)
    assert p1["bas"] <= p1["bit"] <= p2["bas"] and p1["bit"] == T0 + 302
    tr = m.transkript_metni()
    assert tr.index("Birinci") < tr.index("geç çözülen") < tr.index("İkinci") < tr.index("Açık")
    for i in m.isler:
        i.result(timeout=5)
    n = len(islenen)
    saat.t += motor_mod.OZET_YENI_SN + 1
    m.kontrol()
    m.kontrol()
    for i in m.isler:
        i.result(timeout=5)
    assert islenen[n:] == [(1, [1, 9])]                                  # parca 1 yeniden ozetlendi (bir kez)
    m.kapat()


def test_llm_duzeltmesi_satir_id_ile_eslesir_arada_satir_silinse_de(tmp_path, saat, monkeypatch):
    """Bulgu: duzeltme gecisi surerken kapanmis parcadan satir atilinca (gec yanki karari) zip hizasi kayiyor, 3. satir
    2. satirin duzeltilmis metnini aliyordu. Sonuclar satir id'siyle eslenir."""
    import threading
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date.fromtimestamp(T0), duzelt=True, ben=BEN,
                        katilimcilar=[A, B, C, BEN])
    m.konusmaci = SahteKonusmaci()
    monkeypatch.setattr(motor_mod, "DUZELT_MIN_TOKEN", 0)
    basladi, devam = threading.Event(), threading.Event()

    def duzelt(satirlar, adaylar, adlar, sor_fn=None):
        basladi.set()
        assert devam.wait(5)
        rapor = {"satirlar": {}, "gecersiz": [], "oneri": 0, "kabul": 0, "red": 0, "belirsiz": 0, "hata": None}
        return [dict(s, text=s["raw"].upper()) for s in satirlar], rapor

    monkeypatch.setattr(duzelt_mod, "duzelt", duzelt)
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: None)
    for no, metin in ((1, "birinci satir burada"), (2, "ikinci satir burada"), (3, "ucuncu satir burada")):
        m.ses_olayi(klip(no, no * 3, no * 3 + 2, metin))
    m.konusmaci.kararlar = [karar(1, A), karar(2, B), karar(3, C)]
    m.kontrol()
    m.parca_kapat("token", zorla=True)
    assert basladi.wait(5)
    m.konusmaci.kararlar = [karar(2, "?", 0.0, at=True, neden="yanki")]   # duzeltme surerken satir 2 atilir
    m.kontrol()
    devam.set()
    for i in m.isler:
        i.result(timeout=5)
    p = m.parca_oku(1)
    assert [(s["klip_no"], s["text"]) for s in p["satirlar"]] == [(1, "BIRINCI SATIR BURADA"),
                                                                  (3, "UCUNCU SATIR BURADA")]
    m.kapat()


# ---------------------------------------------------------------- parca kapanisi

def test_parca_kapanisi_bekleyen_karari_bekler(tmp_path, saat):
    m = motor(tmp_path)
    m.ses_olayi(klip(1, 0, 2, "Birinci satır."))
    m.parca_kapat("token")
    assert m.parca_no == 0 and m._kapanis is not None             # karar bekleniyor
    m.ses_olayi(klip(2, 3, 5, "Erteleme sırasında gelen satır."))
    saat.t += 5
    m.konusmaci.kararlar = [karar(1, A)]
    m.kontrol()
    assert m.parca_no == 1                                         # karar geldi: kapandi
    p = m.parca_oku(1)
    assert [s["klip_no"] for s in p["satirlar"]] == [1] and p["satirlar"][0]["speaker"] == A
    assert p["baslangic"] == dt.datetime.fromtimestamp(T0).strftime("%H:%M:%S")
    assert [s["klip_no"] for s in m.mevcut] == [2]                 # sonraki parcaya kaldi
    m.kapat()


def test_parca_kapanisi_20_sn_sonra_araligi_zorlar(tmp_path, saat):
    m = motor(tmp_path)
    m.ses_olayi(klip(1, 0, 2))
    m.konusmaci.bekleyen_fn = lambda bas, bit: 1                   # konusmacida yalniz bu aralik bekliyor
    m.konusmaci.bitir_kararlari = [karar(1, B, 0.6)]
    m.parca_kapat("token")
    saat.t += motor_mod.KAPANIS_BEKLE_SN - 1
    m.kontrol()
    assert m.parca_no == 0
    saat.t += 2
    m.kontrol()
    assert m.parca_no == 1 and m.konusmaci.bitir_sayisi == 1
    assert m.parca_oku(1)["satirlar"][0]["speaker"] == B
    m.kapat()


def test_parca_kapanisi_zorlanirken_baska_aralik_bekliyorsa_satir_bilinmeyen_gec_karar_dosyaya(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    m.ses_olayi(klip(1, 0, 2))
    m.konusmaci.bekleyen_fn = lambda bas, bit: 1 if bas is not None else 3
    m.parca_kapat("token")
    saat.t += motor_mod.KAPANIS_BEKLE_SN + 1
    m.kontrol()
    assert m.parca_no == 1 and m.konusmaci.bitir_sayisi == 0
    s = m.parca_oku(1)["satirlar"][0]
    assert s["speaker"] == "?" and s["spk_neden"] == "zaman_asimi" and gorunen_ad(s) == "Bilinmeyen"
    m.konusmaci.kararlar = [karar(1, C, 0.8)]                      # gec karar: kapanmis parca guncellenir
    m.kontrol()
    assert m.parca_oku(1)["satirlar"][0]["speaker"] == C and jsonl(m)[0]["speaker"] == C
    assert any(t == "satir_guncelle" and v["speaker"] == C for t, v in olaylar)
    m.kapat()


def test_bitir_bekleyenleri_zorlar_bol_kararini_bolemez(tmp_path, saat, monkeypatch):
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: None)
    m = motor(tmp_path)
    m.yeniden_kes_fn = lambda no, an: pytest.fail("bitiste yeniden kesme olmaz")
    m.ses_olayi(klip(1, 0, 2))
    m.ses_olayi(klip(2, 3, 9))
    m.ses_olayi(klip(3, 10, 11))
    m.konusmaci.bitir_kararlari = [karar(1, A), karar(2, "x", bol=T0 + 6)]
    m.bitir()
    assert m.konusmaci.bitir_sayisi == 1 and m.konusmaci.bolunemeyen == [2]
    sat = m.tum_satirlar()
    assert [s["speaker"] for s in sat] == [A, f"{A} → {B}", "?"] and not any(s["durum"] == "beklemede" for s in sat)
    meta = json.loads(open(m.klasor + "/meta.json", encoding="utf-8").read())
    assert meta["durum"] == "bitti" and meta["stt_eksik"] == []
    m.kapat()


# ---------------------------------------------------------------- gosterim ve katilimcilar

def test_gorunen_ad_kurali():
    assert gorunen_ad({"speaker": A, "spk_guven": 0.9}) == A
    assert gorunen_ad({"speaker": A, "spk_guven": 0.69}) == A + "?"
    assert gorunen_ad({"speaker": "?", "spk_guven": 0.0}) == "Bilinmeyen"
    assert gorunen_ad({"speaker": f"{A} / {B}", "spk_guven": 0.5, "cakisma": True}) == f"{A} / {B} (aynı anda)"
    assert gorunen_ad({"speaker": f"{A} → {B}", "spk_guven": 0.5, "devir": True}) == f"{A} → {B}"
    assert gorunen_ad({"speaker": "Toplantı Odası 2", "spk_guven": 0.6, "kimlik_turu": "oda"}) == \
        "Toplantı Odası 2 (oda)"
    assert gorunen_ad({"speaker": "?", "durum": "beklemede"}) == "beklemede…"
    assert gorunen_ad({"speaker": A, "spk_guven": 0.5, "durum": "eski"}) == A + "?"
    assert gorunen_ad({"speaker": A}) == A                         # guven alani yok (gocsuz): degismez
    assert motor_mod.blok_metni([{"speaker": A, "spk_guven": 0.6, "text": "bir"},
                                 {"speaker": A, "spk_guven": 0.6, "text": "iki"},
                                 {"speaker": "?", "spk_guven": 0.0, "text": "üç"}]) == f"{A}?: bir iki\nBilinmeyen: üç"


def test_belirsiz_adlar_katilimcilara_girmez(tmp_path, saat):
    m = motor(tmp_path, katilimcilar=[])
    for no in range(1, 8):
        m.ses_olayi(klip(no, no * 3, no * 3 + 2))
    m.konusmaci.kararlar = [karar(1, "?", 0.0), karar(2, A, 0.6), karar(3, f"{A} → {B}", 0.5, devir=True),
                            karar(4, f"{A} / {B}", 0.5, cakisma=True), karar(5, "Konuk", 0.6, kimlik_turu="genel"),
                            karar(6, "Oda 3", 0.95, kimlik_turu="oda"), karar(7, B, 0.85)]
    m.kontrol()
    assert m.katilimcilar == [B]
    m.kapat()


def test_kulaklik_olayi_konusmaciya_gecer(tmp_path, saat):
    m = motor(tmp_path)
    m.ses_olayi({"tur": "kulaklik", "durum": False, "guven": 0.8})
    assert m.kulaklik is False and m.konusmaci.kulaklik is False
    m.ses_olayi({"tur": "kulaklik", "durum": None, "guven": 0.0})
    assert m.kulaklik is None and m.konusmaci.kulaklik is None
    m.kapat()


# ---------------------------------------------------------------- gercek konusmaci ile uctan uca

def test_konusmaci_zamandan_atanir_altyazi_metni_kullanilmaz(tmp_path, saat):
    """Altyazi metni Whisper metniyle hic ortusmuyor (Teams altyazisi bozuk): atama yalniz zamandan, dogru."""
    olaylar = []
    m = motor(tmp_path, sahte=False, olaylar=olaylar)
    sozler = [(A, 1, 4), (B, 6, 9), (A, 11, 14), (B, 16, 19), (A, 21, 24), (B, 26, 29)]
    klipler = [(i + 1, "loopback", s, e, f"Gündem maddesi {i} üzerine konuşuyoruz.") for i, (k, s, e) in
               enumerate(sozler)]
    akit(m, saat, sozler, klipler, 32, metin="alakasız bozuk altyazı")
    assert [s["speaker"] for s in m.mevcut] == [A, B, A, B, A, B]
    assert [gorunen_ad(s) for s in m.mevcut][-2:] == [A, B]        # capalar olustu: kesin
    assert all("alakasız" not in s["text"] and s["kaynak"] == "ses" for s in m.mevcut)
    assert not any(t == "satir" and v.get("kaynak") != "ses" for t, v in olaylar)
    m.kapat()


def test_altyazi_kapaliyken_klip_bilinmeyen(tmp_path, saat):
    m = motor(tmp_path, sahte=False)
    sozler = [(A, 1, 4), (B, 6, 9), (A, 11, 14)]
    klipler = [(1, "loopback", 1, 4, "Birinci."), (2, "loopback", 6, 9, "İkinci."), (3, "loopback", 31, 34, "Üçüncü.")]
    akit(m, saat, sozler, klipler, 36, kapali=((20, 60),))
    assert m.mevcut[-1]["speaker"] == "?" and gorunen_ad(m.mevcut[-1]) == "Bilinmeyen"
    m.kapat()


# ---------------------------------------------------------------- altyazi metni / yedek / STT boslugu

def test_altyazi_satiri_yalniz_gorunum_yedek_yalniz_stt_yokken(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    m.altyazi_satiri({"ts": "10:00:01", "speaker": A, "text": "Raporu cuma günü gönderirim"})
    assert m.mevcut == [] and [t for t, _ in olaylar] == ["altyazi_izi"]
    m.kapat()
    y = motor(tmp_path, ad="y", altyazi_yedek=True)
    y.altyazi_satiri({"ts": "10:00:01", "speaker": A, "text": "Raporu cuma günü gönderirim"})
    assert y.mevcut == []                                          # STT calisiyor: yedek girmez
    y.stt_durumu_ayarla({"durum": "kesinti", "bas": T0, "eksik": []})
    s = y.altyazi_satiri({"ts": "10:00:05", "speaker": A, "text": "Test ortamı hazır"})
    assert s["kaynak"] == "altyazi-yedek" and s["supheli"] and gorunen_ad(s) == A + "?" and not kesin_ad_mi(s)
    y.altyazi_satiri({"ts": "10:00:05", "speaker": A, "text": "Test ortamı perşembe hazır",
                      "guncelle": True, "onceki_text": "Test ortamı hazır"})
    assert len(y.mevcut) == 1 and y.mevcut[0]["text"] == "Test ortamı perşembe hazır"
    assert "(altyazıdan, düşük güven)" in motor_mod.blok_metni(y.mevcut)
    aks = motor_mod.sorumlu_dogrula([{"madde": "Test ortamı perşembe hazır", "sorumlu": "belirsiz", "tarih": "-"}],
                                    y.mevcut)
    assert aks[0]["sorumlu"] == "belirsiz"                         # yedek satir sahip uretmez
    y.kapat()


def test_bosluk_olayi_eksik_transkript_gec_klip_boslugu_kapatir(tmp_path, saat):
    m = motor(tmp_path)
    m.ses_olayi({"tur": "bosluk", "akis": "loopback", "bas": T0 + 60, "bit": T0 + 64, "neden": "stt_yok"})
    m.ses_olayi({"tur": "bosluk", "akis": "loopback", "bas": T0 + 70, "bit": T0 + 75, "neden": "stt_yok"})
    m.ses_olayi({"tur": "bosluk", "akis": "loopback", "bas": T0 + 400, "bit": T0 + 405, "neden": "stt_yok"})
    assert m.stt_eksik() == [[T0 + 60, T0 + 75], [T0 + 400, T0 + 405]]
    metin = motor_mod.stt_eksik_metni(m.stt_eksik())
    hhmm = dt.datetime.fromtimestamp(T0 + 60).strftime("%H:%M")
    assert metin.startswith("STT yok — transkript eksik (" + hhmm + "–")
    m.ses_olayi(klip(9, 400, 405, "Kesintiden sonra çözülen klip."))   # diskten gec cozuldu
    assert m.stt_eksik() == [[T0 + 60, T0 + 75]] and m.ses_olay_sayisi == 4
    m.kapat()


# ---------------------------------------------------------------- eski kayit gocu, gece yarisi

def _eski_kayit(tmp_path):
    k = tmp_path / "2025-10-10_eski"
    (k / "parcalar").mkdir(parents=True)
    (k / "meta.json").write_text(json.dumps({"baslik": "Eski", "tarih": "2025-10-10", "durum": "tamam",
                                             "katilimcilar": [A], "parca": 1, "kaynak": "altyazi"}),
                                 encoding="utf-8")
    satirlar = [{"id": 1, "ts": "23:59:50", "speaker": A, "text": "Raporu ben hazırlarım.", "raw": "x",
                 "kaynak": "altyazi"},
                {"id": 2, "ts": "00:00:10", "speaker": B, "text": "Tamam.", "raw": "x", "kaynak": "ses"}]
    (k / "parcalar" / "parca_001.json").write_text(json.dumps(
        {"sira": 1, "neden": "token", "token": 10, "baslangic": "23:59:50", "bitis": "00:00:10",
         "satirlar": satirlar, "duzeltmeler": [], "ozet": None}), encoding="utf-8")
    return k


def test_yukle_eski_kayit_goc_eder_ad_soru_isaretli_sahip_yok(tmp_path):
    k = _eski_kayit(tmp_path)
    m = motor_mod.Motor.yukle(str(k))
    sat = m.tum_satirlar()
    gece = dt.datetime(2025, 10, 10, 23, 59, 50).timestamp()
    assert [(s["spk_guven"], s["durum"]) for s in sat] == [(0.5, "eski"), (0.5, "eski")]
    assert sat[0]["bas"] == sat[0]["bit"] == gece and sat[1]["bas"] == gece + 20      # gece yarisi sarmali
    assert [gorunen_ad(s) for s in sat] == [A + "?", B + "?"]
    assert motor_mod.blok_metni(sat).startswith(A + "?: ")
    aks = motor_mod.sorumlu_dogrula([{"madde": "Raporu hazırlamak", "sorumlu": "belirsiz", "tarih": "-"}], sat)
    assert aks[0]["sorumlu"] == "belirsiz"                         # eski satir sahip uretmez
    assert json.loads((k / "parcalar" / "parca_001.json").read_text(encoding="utf-8"))["satirlar"][0].get(
        "durum") is None                                           # goc yalniz okurken: dosya degismez
    m.kapat()


def test_sn_fark_gece_yarisi():
    assert motor_mod._sn_fark("00:00:05", "23:59:50") == 15
    assert motor_mod._sn_fark("23:59:50", "00:00:05") == -15
    assert motor_mod._sn_fark("10:00:30", "10:00:00") == 30
    assert motor_mod._sn("bozuk") == 0


def test_parca_araligi_gece_yarisini_gecer(tmp_path, saat):
    gece = dt.datetime(2025, 10, 10, 23, 59, 50).timestamp()
    m = motor(tmp_path)
    for no, d in ((1, 0), (2, 20)):
        k = klip(no, 0, 2)
        k.update(bas=gece + d, bit=gece + d + 2, ts=dt.datetime.fromtimestamp(gece + d).strftime("%H:%M:%S"))
        m.ses_olayi(k)
    m.konusmaci.kararlar = [karar(1, A), karar(2, B)]
    m.kontrol()
    m.parca_kapat("test")
    p = m.parca_oku(1)
    assert (p["baslangic"], p["bitis"]) == ("23:59:50", "00:00:12")   # eskiden min/max metin: 00:00:10–23:59:50
    m.kapat()


def test_kapsama_60_sn_yoksa_uyari_olayi(tmp_path, saat):
    olaylar = []
    m = motor(tmp_path, olaylar=olaylar)
    m.konusmaci.kapsama = False
    m.kontrol()
    saat.t += motor_mod.KAPSAMA_UYARI_SN - 1
    m.kontrol()
    assert not any(t == "kapsama" for t, _ in olaylar)
    saat.t += 2
    m.kontrol()
    m.kontrol()
    assert [v["var"] for t, v in olaylar if t == "kapsama"] == [False]
    m.konusmaci.kapsama = True
    m.kontrol()
    assert [v["var"] for t, v in olaylar if t == "kapsama"] == [False, True]
    m.kapat()


def test_konusmaci_gunlugu_ve_cizelge_diske(tmp_path, saat):
    m = motor(tmp_path, sahte=False)
    sozler = [(A, 1, 4), (B, 6, 9)]
    akit(m, saat, sozler, [(1, "loopback", 1, 4, "Bir."), (2, "loopback", 6, 9, "İki.")], 12)
    m.parca_kapat("test")
    cizelge = json.loads(open(m.klasor + "/cizelge.json", encoding="utf-8").read())
    assert {a["kim"] for a in cizelge["araliklar"]} == {A, B} and "L" in cizelge["durum"]
    assert all(set(a) == {"kim", "ilk", "son", "tur"} for a in cizelge["araliklar"])       # metin yok
    m.kapat()
