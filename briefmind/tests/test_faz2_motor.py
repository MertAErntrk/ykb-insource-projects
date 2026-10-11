"""Faz 2 motor entegrasyonu (MIMARI_v3 §4-5, §7 Faz2): parca kapaninca duzelt.duzelt (LLM onerir, kapi karar verir;
metne kor degisiklik yok), terim.gozlem_ekle / terim.ogren cagrilari, davet + onceki not terimleri, halusinasyon
cizelge kurali (supheli satir soluk, nota girmez), toplanti_bitti akisi (offscreen), Belirsizler paneli (salt okunur),
sozluk/inceleme izlerinin silinmesi. Ses cihazi, ag, LLM gerekmez; adlar sentetik."""
import datetime as dt
import json
import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # PyQt5 import edilmeden ONCE

import pytest

import duzelt as duzelt_mod
import llm
import motor as motor_mod
import terim as terim_mod
from metin import gorunen_ad
from stt import halusinasyon_puani
from test_motor_faz1 import A, B, BEN, T0, SahteKonusmaci, akit, karar, klip, saat_kur


@pytest.fixture
def saat(monkeypatch):
    return saat_kur(monkeypatch)


# ---------------------------------------------------------------- duzenek

def _havuz(tmp_path, ad="terimler"):
    return terim_mod.TerimHavuzu(str(tmp_path / ad / "terimler.json"))


def _oneri_sor(oneriler):
    """duzelt.duzelt sor_fn: [(eski_ifade_icerigi -> oneri)] ; satir id'si mesajdaki [id] satirindan bulunur."""
    cagrilar = []

    def sor(mesajlar, sema):
        cagrilar.append(mesajlar)
        out = []
        for satir in mesajlar[1]["content"].split("SATIRLAR:\n", 1)[1].splitlines():
            sid, _, metin = satir[1:].partition("] ")
            for anahtar, (yeni, neden) in oneriler.items():
                if anahtar in metin:
                    out.append({"satir": int(sid), "eski": anahtar, "yeni": yeni, "neden": neden})
        return {"oneriler": out}
    sor.cagrilar = cagrilar
    return sor


def _toplanti(tmp_path, ad="2026-10-05_haftalik-mimari", baslik="Haftalık Mimari", havuz=None, gundem="",
              davet=None, tarih=dt.date(2026, 10, 5), duzelt=True, **kw):
    """Gercek terim havuzu + sahte konusmaci; parca isleme (duzeltme + ozet) gercek, LLM sahte."""
    m = motor_mod.Motor(str(tmp_path / "toplantilar" / ad), baslik, tarih, havuz or _havuz(tmp_path),
                        kw.pop("katilimcilar", [A, B]), gundem, duzelt=duzelt, ben=BEN, davet=davet, **kw)
    m.konusmaci = SahteKonusmaci()
    return m


def _konus(m, saat, satirlar):
    """satirlar: [(metin, konusmaci)] -> klipler 3 sn arayla, kararlar hemen."""
    for i, (metin, kim) in enumerate(satirlar, 1):
        m.ses_olayi(klip(i, i * 3, i * 3 + 2, metin))
        m.konusmaci.kararlar = [karar(i, kim)]
        saat.t = T0 + i * 3 + 3
        m.kontrol()


@pytest.fixture(autouse=True)
def hizli_parca(monkeypatch):
    monkeypatch.setattr(motor_mod, "DUZELT_MIN_TOKEN", 0)
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: llm.normalize_ozet(
        {"ozet": "Kubernetes geçişi konuşuldu.", "konular": ["Geçiş"]}))


# ---------------------------------------------------------------- 1) parca basina duzeltme

def test_parca_kapaninca_duzelt_calisir_raw_korunur_gozlem_yazilir(tmp_path, saat):
    havuz = _havuz(tmp_path)
    havuz.ekle("haftalik-mimari", ["Kubernetes", "Jenkins", "commit"], "davet")
    m = _toplanti(tmp_path, havuz=havuz)
    sor = _oneri_sor({"kubernetis": ("Kubernetes", "terim"), "cenkinse": ("Jenkins'e", "terim"),
                      "komite": ("commit", "terim")})              # komite -> commit: edit ~0.5, kapi reddeder
    m.duzelt_sor_fn = sor
    _konus(m, saat, [("kubernetis kümesi düştü", A), ("cenkinse bakarız", B), ("komite toplandı", A)])
    m.parca_kapat("token", zorla=True)
    for i in m.isler:
        i.result(timeout=10)
    p = m.parca_oku(1)
    metinler = [(s["raw"], s["text"]) for s in p["satirlar"]]
    assert metinler == [("kubernetis kümesi düştü", "Kubernetes kümesi düştü"),
                        ("cenkinse bakarız", "Jenkins'e bakarız"), ("komite toplandı", "komite toplandı")]
    assert p["satirlar"][0]["duzeltmeler"] == [{"eski": "kubernetis", "yeni": "Kubernetes", "neden": "terim"}]
    assert p["duzeltmeler"] == [["kubernetis", "Kubernetes"], ["cenkinse", "Jenkins'e"]]
    assert len(sor.cagrilar) == 1
    # adaylar ve adlar LLM mesajinda: terim.adaylar(seri) + katilimci adlari
    kullanici = sor.cagrilar[0][1]["content"]
    assert "Kubernetes" in kullanici and A in kullanici and "[1] kubernetis" in kullanici
    # kabul edilen terim|ad gozlem olarak havuza (ogrenme toplanti sonunda); red olan yazilmaz
    assert sorted((g["eski"], g["hedef"], g["seri"]) for g in havuz.gozlemler) == [
        ("cenkinse", "Jenkins", "haftalik-mimari"), ("kubernetis", "Kubernetes", "haftalik-mimari")]
    # kurtarma: altyazi.jsonl'e guncelleme kaydi; log: kabul + red nedeni (yaygin kelime)
    kayit = {s["id"]: s for s in motor_mod.altyazi_kayitlari(m.klasor)}
    assert kayit[p["satirlar"][0]["id"]]["text"] == "Kubernetes kümesi düştü"
    log = [json.loads(x) for x in open(os.path.join(m.klasor, "duzelt_log.jsonl"), encoding="utf-8")]
    assert len(log[0]["kabul"]) == 2 and [r["sebep"] for r in log[0]["red"]] in (["uzaklik"], ["yaygin_kelime"])
    # ozet kapi sonrasi metinle
    assert motor_mod.blok_metni(p["satirlar"]).count("Kubernetes kümesi") == 1
    m.kapat()


def test_llm_hatasinda_metin_ve_onceki_duzeltmeler_korunur(tmp_path, saat):
    m = _toplanti(tmp_path)
    m.terimler.ekle("haftalik-mimari", ["Kubernetes"], "davet")

    def patla(mesajlar, sema):
        raise RuntimeError("uydurma LLM hatasi")
    m.duzelt_sor_fn = patla
    _konus(m, saat, [("kubernetis kümesi düştü", A)])
    veri = m.mevcut[0]
    veri["duzeltmeler"] = [{"eski": "x", "yeni": "y", "neden": "terim"}]           # onceki gecisten kalan
    m.parca_kapat("token", zorla=True)
    for i in m.isler:
        i.result(timeout=10)
    s = m.parca_oku(1)["satirlar"][0]
    assert s["text"] == "kubernetis kümesi düştü" and s["duzeltmeler"] == [{"eski": "x", "yeni": "y", "neden": "terim"}]
    assert m.parca_oku(1)["ozet"]["ozet"]                                          # ozet yine de uretildi
    m.kapat()


def test_yazim_onerisi_uygulanmaz_belirsizler_panelinde(tmp_path, saat):
    m = _toplanti(tmp_path)
    m.terimler.ekle("haftalik-mimari", ["Kubernetes"], "davet")
    m.duzelt_sor_fn = _oneri_sor({"deplay": ("deploy", "yazim")})
    _konus(m, saat, [("dün deplay ettik", A)])
    m.parca_kapat("token", zorla=True)
    for i in m.isler:
        i.result(timeout=10)
    s = m.parca_oku(1)["satirlar"][0]
    assert s["text"] == "dün deplay ettik" and s["belirsiz"] == ["deplay"] and s["duzeltmeler"] == []
    b = m.belirsizler()
    kelime = [x for x in b if x["tur"] == "kelime"]
    assert len(kelime) == 1 and kelime[0]["metin"] == "deplay" and kelime[0]["ts"] == s["ts"] and kelime[0]["parca"] == 1
    assert kelime[0]["baglam"].startswith(A) and "dün deplay ettik" in kelime[0]["baglam"]
    assert not m.terimler.gozlemler                                                 # yazim gozlem degildir
    m.kapat()


def test_duzelt_kapali_ya_da_raw_yoksa_llm_cagrilmaz(tmp_path, saat):
    m = _toplanti(tmp_path, duzelt=False)
    sor = _oneri_sor({"kubernetis": ("Kubernetes", "terim")})
    m.duzelt_sor_fn = sor
    _konus(m, saat, [("kubernetis kümesi düştü", A)])
    m.parca_kapat("token", zorla=True)
    for i in m.isler:
        i.result(timeout=10)
    assert sor.cagrilar == [] and m.parca_oku(1)["satirlar"][0]["text"] == "kubernetis kümesi düştü"
    m.kapat()


# ---------------------------------------------------------------- 2) terim havuzu: davet, onceki not, ogrenme

def test_davet_ve_onceki_not_terimleri_havuza_kanit_ve_kayit(tmp_path):
    onceki = tmp_path / "toplantilar" / "2026-09-28_haftalik-mimari"
    onceki.mkdir(parents=True)
    (onceki / "meta.json").write_text(json.dumps({"baslik": "Haftalık Mimari", "tarih": "2026-09-28"}),
                                      encoding="utf-8")
    (onceki / "not.md").write_text("# Haftalık Mimari\n\n## Konu akışı\n- **Mikroservis Geçişi** ele alındı\n",
                                   encoding="utf-8")
    baska = tmp_path / "toplantilar" / "2026-09-29_baska-seri"                      # baska seri: kullanilmaz
    baska.mkdir(parents=True)
    (baska / "meta.json").write_text(json.dumps({"baslik": "Başka", "tarih": "2026-09-29"}), encoding="utf-8")
    (baska / "not.md").write_text("- **Gizli Terim** konuşuldu\n", encoding="utf-8")
    m = _toplanti(tmp_path, davet={"govde": "Bu toplantıda Kampanya Modülü ele alınacak. Teams toplantısına katılın "
                                            "https://example.invalid/x", "ekler": ["Plan_v3.pptx"]})
    assert m.terimleri_hazirla() > 0 and m.terimleri_hazirla() == 0                 # ikinci cagri etkisiz
    terimler = [e["terim"] for e in m.terimler.terimler]
    assert "Kampanya Modülü" in terimler and "Mikroservis Geçişi" in terimler and "Gizli Terim" not in terimler
    assert not any("http" in t or "Teams" in t for t in terimler)                   # kara liste
    assert os.path.exists(m.terimler.yol)                                           # kaydedildi
    davet_meta = json.loads(open(os.path.join(m.klasor, "meta.json"), encoding="utf-8").read())["davet"]
    assert davet_meta["ekler"] == ["Plan_v3.pptx"] and "http" not in davet_meta["govde"]   # meta'ya yalniz temiz metin
    assert len(m._onceki_notlar) == 1
    m.kapat()


def test_bitirde_ogren_cagrilir_kanit_yalniz_bagimsiz_kaynak(tmp_path, saat, monkeypatch):
    havuz = _havuz(tmp_path)
    cagrilar = []
    gercek = havuz.ogren
    monkeypatch.setattr(havuz, "ogren", lambda *a, **k: (cagrilar.append(a), gercek(*a, **k))[1])
    m = _toplanti(tmp_path, havuz=havuz, gundem="Kubernetes geçişi", davet={"govde": "Kubernetes geçişi", "ekler": []})
    m.duzelt_sor_fn = _oneri_sor({"kubernetis": ("Kubernetes", "terim")})
    _konus(m, saat, [("kubernetis kümesi düştü", A)])
    m.bitir()
    assert len(cagrilar) == 1
    seri, toplanti_id, kanit = cagrilar[0]
    assert seri == "haftalik-mimari" and toplanti_id == "2026-10-05_haftalik-mimari"
    assert set(kanit) == {"davet", "onceki_not", "katilimci"}
    assert "Kubernetes" in json.dumps(kanit["davet"], ensure_ascii=False) and A in kanit["katilimci"]
    # duzeltilmis transkript kanit degildir: kanitta duzeltme sonucu cumle de, LLM ciktisi da yok
    assert "kümesi düştü" not in json.dumps(kanit, ensure_ascii=False)
    m.kapat()


def test_iki_toplantida_ogrenir_varyant_olur_geri_alinabilir(tmp_path, saat):
    havuz = _havuz(tmp_path)
    havuz.ekle("haftalik-mimari", ["Kubernetes"], "elle")                          # kapi icin aday listesinde olmali
    davet = {"govde": "Gündem: Kubernetes geçişi", "ekler": []}
    sonuc = []
    for gun in (5, 12):                                                             # ayni seri, iki ayri toplanti
        m = _toplanti(tmp_path, ad=f"2026-10-{gun:02d}_haftalik-mimari", havuz=havuz, davet=davet,
                      tarih=dt.date(2026, 10, gun))
        m.duzelt_sor_fn = _oneri_sor({"kubernetis": ("Kubernetes", "terim")})
        saat.t = T0
        _konus(m, saat, [("kubernetis kümesi düştü", A)])
        m.bitir()
        sonuc.append(list(havuz.varyantlar("Kubernetes")))
        m.kapat()
    assert sonuc[0] == [] and sonuc[1] == ["kubernetis"]                            # ilk toplantida yok, ikincide
    assert terim_mod.TerimHavuzu(havuz.yol).varyantlar("Kubernetes") == ["kubernetis"]   # diske yazildi
    assert havuz.geri_al("Kubernetes") is not None and havuz.varyantlar("Kubernetes") == []


def test_ogrenme_davette_gecmeyen_hedef_ogrenilmez(tmp_path, saat):
    havuz = _havuz(tmp_path)
    for gun in (5, 12):
        m = _toplanti(tmp_path, ad=f"2026-10-{gun:02d}_haftalik-mimari", havuz=havuz, tarih=dt.date(2026, 10, gun))
        havuz.ekle("haftalik-mimari", ["Kubernetes"], "elle")                       # terim var ama davette yok
        m.duzelt_sor_fn = _oneri_sor({"kubernetis": ("Kubernetes", "terim")})
        saat.t = T0
        _konus(m, saat, [("kubernetis kümesi düştü", A)])
        m.bitir()
        m.kapat()
    assert havuz.varyantlar("Kubernetes") == [] and len(havuz.gozlemler[0]["toplantilar"]) == 2


# ---------------------------------------------------------------- 3) halusinasyon cizelge kurali

def test_cizelge_agirligi_stt_ile_ayni():
    metin = "Bütçe tablosunu güncelledim."
    fark = halusinasyon_puani(metin, 3.0, cizelge_etkin=False) - halusinasyon_puani(metin, 3.0, cizelge_etkin=None)
    assert round(fark, 4) == motor_mod.CIZELGE_PUANI


def test_gercek_konusmaciyla_etkinliksiz_klip_supheli_nota_girmez(tmp_path, saat):
    """Altyazi saglikli (Ayse 1-4 sn konusuyor), 25-28 sn'de kimse yok: klip supheli (+0.6), soluk, nota girmez."""
    olaylar = []
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date.fromtimestamp(T0), duzelt=False, ben=BEN,
                        katilimcilar=[A, B, BEN], olay=lambda t, v: olaylar.append((t, v)))
    m._isle = lambda veri, duzelt: None
    akit(m, saat, [(A, 1, 4)], [(1, "loopback", 1, 4, "Bütçe tablosunu güncelledim."),
                               (2, "loopback", 25, 28, "Kampanya görsellerini de ekledim.")], 45)
    s1, s2 = m.mevcut
    assert not s1["supheli"] and s1["speaker"] == A
    assert s2["supheli"] and s2["hal_puani"] == 0.6 and s2["cizelge_etkin"] is False and s2["speaker"] == "?"
    g = [v for t, v in olaylar if t == "satir_guncelle" and v["id"] == s2["id"]]
    assert g and g[-1]["supheli"] is True                                           # canli ekran soluk gosterir
    assert [r for r in motor_mod.altyazi_kayitlari(m.klasor) if r["id"] == s2["id"]][0]["supheli"] is True
    assert motor_mod.blok_metni(m.mevcut) == f"{A}?: Bütçe tablosunu güncelledim."      # capa az: 'Ad?' 
    assert "Kampanya görsellerini" not in m.transkript_metni()
    kayit = m.belirsizler()
    assert [(x["tur"], x["ts"]) for x in kayit if x["tur"] == "supheli"] == [("supheli", s2["ts"])]
    m.kapat()


def test_kapsama_yoksa_cizelge_kurali_uygulanmaz(tmp_path, saat):
    m = _toplanti(tmp_path, duzelt=False)
    m.ses_olayi(klip(1, 0, 2))
    m.konusmaci.kararlar = [karar(1, "?", 0.0, adaylar=[], kapsama=False, neden="kapsama_yok")]
    m.kontrol()
    assert not m.mevcut[0]["supheli"] and "cizelge_etkin" not in m.mevcut[0]
    m.kapat()


@pytest.mark.parametrize("akis,adaylar,beklenen", [
    ("loopback", [], False), ("loopback", [[A, 0.1]], True), ("loopback", [[A, 0.0]], False),
    ("mikrofon", [[BEN, 0.0], [A, 0.4]], False), ("mikrofon", [[BEN, 0.5]], True)])
def test_cizelge_etkin_karardan(akis, adaylar, beklenen):
    assert motor_mod.Motor.cizelge_etkin(karar(1, "?", adaylar=adaylar, akis=akis)) is beklenen
    assert motor_mod.Motor.cizelge_etkin(karar(1, "?", adaylar=adaylar, akis=akis, kapsama=False)) is None


def test_yuksek_taban_puan_cizelgeyle_atilir_ikinci_karar_tekrar_saymaz(tmp_path, saat):
    olaylar = []
    m = _toplanti(tmp_path, duzelt=False)
    m._olay = lambda t, v: olaylar.append((t, v))
    ev = klip(1, 0, 2)
    ev["hal_puani"] = 0.4                                         # ses.py puani (kr/sn yumusak kural gibi)
    s = m.ses_olayi(ev)
    m.konusmaci.kararlar = [karar(1, "?", 0.0, adaylar=[], neden="kimse")]
    m.kontrol()                                                   # 0.4 + 0.6 = 1.0 >= hal_at (0.9): atilir
    assert ("satir_sil", {"id": s["id"], "neden": "halusinasyon"}) in olaylar and m.mevcut == []
    # ayni anda 0.2 tabanli klip: 0.8 -> supheli; sonraki 'bolunemedi' karari puani ikinci kez artirmaz
    ev2 = klip(2, 3, 5)
    ev2["hal_puani"] = 0.2
    s2 = m.ses_olayi(ev2)
    m.konusmaci.kararlar = [karar(2, "?", 0.0, adaylar=[], neden="kimse")]
    m.kontrol()
    m.konusmaci.kararlar = [karar(2, "?", 0.0, adaylar=[], neden="kimse")]
    m.kontrol()
    assert s2["supheli"] and s2["hal_puani"] == 0.8
    m.kapat()


def test_hal_esikleri_config_esikler_den(tmp_path, saat):
    m = _toplanti(tmp_path, duzelt=False, esikler={"esikler": {"hal_supheli": 0.7}})
    m.ses_olayi(klip(1, 0, 2))
    m.konusmaci.kararlar = [karar(1, "?", 0.0, adaylar=[], neden="kimse")]
    m.kontrol()
    assert not m.mevcut[0]["supheli"] and m.mevcut[0]["hal_puani"] == 0.6           # 0.6 < 0.7
    m.kapat()


def test_supheli_satir_nota_ve_sorumluya_girmez_yedek_girer(tmp_path, saat, monkeypatch):
    monkeypatch.setattr(llm, "birlestir", lambda bolumler, baglam, log, **k: "## Özet\nx\n")
    m = _toplanti(tmp_path, duzelt=False)
    _konus(m, saat, [("Raporu ben hazırlarım.", A)])
    s = m.mevcut[0]
    sup = dict(s, id=99, supheli=True, text="Abone olmayı unutmayın.")
    yedek = dict(s, id=100, supheli=True, kaynak="altyazi-yedek", text="Yedek satır.")
    assert motor_mod.blok_metni([s, sup, yedek]).count("Abone") == 0 and "Yedek satır" in motor_mod.blok_metni(
        [s, sup, yedek])
    assert motor_mod.nota_girer(s) and not motor_mod.nota_girer(sup) and motor_mod.nota_girer(yedek)
    m.kapat()


# ---------------------------------------------------------------- 4) silinenler

def test_sozluk_ve_onay_akisi_izi_kalmadi():
    for ad in ("sozluk.py", "sozluk.example.json", os.path.join("tools", "sozluk_temizle.py")):
        assert not os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ad)), ad
    assert not hasattr(llm, "satirlari_duzelt") and not hasattr(llm, "DUZELT_SISTEM")
    # LLM gunlugu: duzeltme istegi 'duzeltme' gorevi olarak taninir (llm_log.jsonl)
    msj = duzelt_mod.mesajlar([(1, "metin")], ["Jira"], [])
    assert duzelt_mod.DUZELT_SISTEM.startswith(llm.DUZELT_ONEK) and llm._gorev_adi(msj) == "duzeltme"
    baglam = llm.baglam_metni("Baslik", dt.date(2026, 10, 5), [A], "Jira; commit", "")
    assert "Terimler (doğru yazımlar): Jira; commit" in baglam and "Sözlük" not in baglam


def test_baglam_terimleri_havuzdan_metinde_gecenler(tmp_path):
    m = _toplanti(tmp_path, duzelt=False)
    m.terimler.ekle("haftalik-mimari", ["Kubernetes", "Jenkins"], "davet")
    assert "Kubernetes; Jenkins" in m.baglam() or "Jenkins; Kubernetes" in m.baglam()
    b = m.baglam("kubernetes kümesi düştü")                        # yalniz metinde gecen
    assert "Kubernetes" in b and "Jenkins" not in b
    m.kapat()


def test_saklama_duzelt_logunu_da_siler(tmp_path):
    k = tmp_path / "toplantilar" / "2026-01-01_x"
    (k / "parcalar").mkdir(parents=True)
    (k / "meta.json").write_text(json.dumps({"baslik": "X", "tarih": "2026-01-01", "durum": "tamam", "parca": 0}),
                                 encoding="utf-8")
    for ad in ("altyazi.jsonl", "oneriler.json", "duzelt_log.jsonl"):
        (k / ad).write_text("x", encoding="utf-8")
    assert motor_mod.saklama_uygula(30, kok=str(tmp_path / "toplantilar"), bugun=dt.date(2026, 6, 1)) == 1
    assert not any((k / ad).exists() for ad in ("altyazi.jsonl", "oneriler.json", "duzelt_log.jsonl"))


def test_yeni_motor_varsayilan_havuz_toplanti_klasorunde(tmp_path):
    """terimler verilmezse (replay/test) uygulama klasorundeki terimler/ dosyasina dokunulmaz."""
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date(2026, 10, 5), duzelt=False)
    assert m.terimler.yol == str(tmp_path / "t" / "terimler" / "terimler.json")
    m.kapat()


# ---------------------------------------------------------------- 5) toplanti_bitti akisi (offscreen)

QtCore = pytest.importorskip("PyQt5.QtCore")
QtWidgets = pytest.importorskip("PyQt5.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bekle(qapp, kosul, sn=15.0):
    import time
    son = time.time() + sn
    while time.time() < son:
        qapp.processEvents()
        if kosul():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return kosul()


class SahteYakalamaIsi(QtCore.QThread):
    olay = QtCore.pyqtSignal(str, object)
    durum = QtCore.pyqtSignal(str)
    toplanti_bitti = QtCore.pyqtSignal(list)
    hata = QtCore.pyqtSignal(str)

    def __init__(self, *a, **k):
        super().__init__()
        self.motor = None

    def durdur(self):
        pass

    def run(self):
        pass


@pytest.fixture
def pencere(tmp_path, monkeypatch, qapp):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)                # hata.log gercek kullanici profiline yazilmaz
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    import app
    monkeypatch.setattr(app.terim_mod, "uygulama_klasoru", lambda: str(tmp_path))
    monkeypatch.setattr(app.Pencere, "toplantilari_yenile", lambda self, elle=False: None)
    monkeypatch.setattr(app.Pencere, "saklama_uygula", lambda self: None)
    monkeypatch.setattr(app.Pencere, "llm_tani", lambda self, test=False: None)
    monkeypatch.setattr(app, "YakalamaIsi", SahteYakalamaIsi)
    monkeypatch.setattr(app.outlook, "kullanici_adi", lambda: BEN)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    p = app.Pencere()
    p.a_outlook.setChecked(False)
    yield p
    if p.tamamla is not None:
        p.tamamla.wait(5000)
    qapp.processEvents()
    p.close()


def test_arayuzde_sozluk_ve_inceleme_sekmesi_yok(pencere):
    p = pencere
    adlar = [p.nav.item(i).text() for i in range(p.nav.count())]
    assert adlar == ["Canlı", "Not", "Geçmiş", "Ayarlar"] and p.sayfalar.count() == 4
    for ad in ("tablo", "b_uygula", "s_tablo", "sozluk", "inc_bilgi", "a_otomatik_not", "k_oneri", "kararlar", "uygula",
               "oneri_satiri", "inceleme_hazir", "sozluk_ekle"):
        assert not hasattr(p, ad), ad
    assert p.belirsiz_kutu.title().startswith("Belirsizler") and p.belirsiz_goster.isReadOnly()
    assert not any(isinstance(w, QtWidgets.QPushButton) and ("Onayla" in w.text() or "Uygula" in w.text())
                   for w in p.findChildren(QtWidgets.QPushButton))


def test_toplanti_bitti_motoru_baglar_baslati_acar_notu_uretir_belirsizleri_gosterir(pencere, qapp, tmp_path,
                                                                                     monkeypatch, saat):
    p = pencere
    monkeypatch.setattr(llm, "birlestir", lambda bolumler, baglam, log, **k: "## Özet\nToplantı konuşuldu.\n")
    m = _toplanti(tmp_path, havuz=p.terimler)
    m.terimler.ekle("haftalik-mimari", ["Kubernetes"], "davet")
    m.duzelt_sor_fn = _oneri_sor({"deplay": ("deploy", "yazim")})
    _konus(m, saat, [("dün deplay ettik", A), ("kubernetis kümesi düştü", B)])
    m.konusmaci.kararlar = []
    belirsizler = m.bitir()                                       # YakalamaIsi._kapat'in yaptigi
    assert any(x["tur"] == "kelime" and x["metin"] == "deplay" for x in belirsizler)
    is_ = SahteYakalamaIsi()
    is_.motor = m
    p.is_ = is_
    p.motor = None
    p.b_ana.setEnabled(False)
    is_.toplanti_bitti.connect(p.toplanti_bitti)
    is_.toplanti_bitti.emit(belirsizler)                          # sinyal sozlesmesi: list
    assert p.motor is m                                           # motor baglandi
    assert p.tamamla is not None                                  # not uretimi onay beklemeden basladi
    assert _bekle(qapp, lambda: not p._not_kilit and "Toplantı konuşuldu" in p.not_goster.toPlainText())
    assert p.b_ana.isEnabled() and p.b_ana.text() == "Başlat" and p.nav.currentRow() == p.SAYFA_NOT
    assert os.path.exists(os.path.join(m.klasor, "not.md"))
    assert p.k_belirsiz.deger.text() == str(len([x for x in m.belirsizler()]))
    html = p.belirsiz_goster.toHtml()
    assert "deplay" in p.belirsiz_goster.toPlainText() and "ts:" in html           # kaynak baglantisi
    assert p.b_not_uret.isEnabled()
    m.kapat()


def test_toplanti_bitti_motorsuzsa_arayuz_sifirlanir(pencere):
    p = pencere
    p.motor = None
    p.is_ = SahteYakalamaIsi()
    p.b_ana.setEnabled(False)
    p.toplanti_bitti([])
    assert p.b_ana.isEnabled() and p.tamamla is None


def test_gecmis_notunu_acinca_belirsizler_panele_yuklenir_eski_kayit_cokmez(pencere, tmp_path):
    p = pencere
    k = tmp_path / "toplantilar" / "2026-09-23_eski"
    (k / "parcalar").mkdir(parents=True)
    (k / "meta.json").write_text(json.dumps({"baslik": "Eski", "tarih": "2026-09-23", "durum": "tamam",
                                             "katilimcilar": [A], "parca": 1, "kaynak": "ses"}), encoding="utf-8")
    (k / "not.md").write_text("# Eski\n\nx\n", encoding="utf-8")
    (k / "parcalar" / "parca_001.json").write_text(json.dumps(       # v2 oncesi satir: raw/surum yok
        {"sira": 1, "neden": "token", "token": 10, "baslangic": "10:00:00", "bitis": "10:00:05",
         "satirlar": [{"id": 1, "ts": "10:00:00", "speaker": A, "text": "Eski satır"}],
         "ozet": {"ozet": "x", "konular": [], "kararlar": [], "aksiyonlar": [], "acik_sorular": [],
                  "belirsiz_terimler": ["Eskit"]}, "duzeltmeler": []}), encoding="utf-8")
    p.gecmis_yenile()
    p.g_liste.setCurrentRow(0)
    p.gecmis_not()
    assert p.motor is not None and "Eskit" in p.belirsiz_goster.toPlainText()
    ozet = [x for x in p.motor.belirsizler() if x["tur"] == "konusmaci"]
    assert len(ozet) == 1 and ozet[0]["satir_sayisi"] == 1 and ozet[0]["ts"] == "10:00:00"   # eski kayit: tek ozet kaydi
    p.motor.kapat()


def test_satir_html_supheli_soluk(pencere):
    h = pencere._satir_html({"ts": "10:00:00", "speaker": A, "spk_guven": 0.9, "text": "Abone ol", "kaynak": "ses",
                             "durum": "karar", "supheli": True})
    assert "#94A3B8\">Abone ol" in h and "nota girmez" in h
    n = pencere._satir_html({"ts": "10:00:00", "speaker": A, "spk_guven": 0.9, "text": "Merhaba", "kaynak": "ses",
                             "durum": "karar", "supheli": False})
    assert "nota girmez" not in n and gorunen_ad({"speaker": A, "spk_guven": 0.9}) in n


def test_belirsiz_konusmaci_ardisik_satirlar_tek_kayit(tmp_path, saat):
    m = _toplanti(tmp_path, duzelt=False)
    for i in range(1, 5):
        m.ses_olayi(klip(i, i * 3, i * 3 + 2, f"Satır {i} burada."))
    m.konusmaci.kararlar = [karar(1, "?", 0.0, adaylar=[[A, 0.2]]), karar(2, "?", 0.0, adaylar=[[A, 0.2]]),
                            karar(3, A, 0.9), karar(4, A, 0.5)]
    m.kontrol()
    k = [(x["metin"], x["satir_sayisi"]) for x in m.belirsizler() if x["tur"] == "konusmaci"]
    assert k == [("Bilinmeyen", 2), (A + "?", 1)]
    m.kapat()


class _Yakalayici:
    isi = None

    def __init__(self, katilimcilar=(), okuma_fn=None):
        self.hazir = False

    def oku(self):
        _Yakalayici.isi.durdur()
        return []

    def kayip_saniye(self):
        return 0

    def onerilen_aralik(self):
        return 0

    def bitir(self):
        return []


def test_yakalama_isi_gercek_motorla_davet_terimleri_ve_toplanti_bitti(qapp, tmp_path, monkeypatch):
    """YakalamaIsi + gercek Motor: ctx['davet'] motora gider, terimler toplanti basinda havuza yazilir, ses kurulamasa
    da motor.bitir bir kez cagrilir ve toplanti_bitti (list) yayilir; ipucu_fn terim.prompt'tur."""
    import isler
    import ses
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(isler, "Yakalayici", _Yakalayici)
    monkeypatch.setattr(isler, "ekran_okuyucu", lambda acik: None)
    monkeypatch.setattr(isler.motor_mod, "yeni_klasor", lambda baslik, tarih: str(tmp_path / "kayit"))
    kurulan = {}

    class SahteSes:
        def __init__(self, *a, **kw):
            kurulan.update(kw)

        def baslat(self):
            return True

        def durdur(self):
            pass

        def yeniden_kes(self, no, an):
            return False
        stt_durumu = {"durum": "ok", "bas": None, "bekleyen": 0, "kayit": False, "eksik": [], "hata": None}
    monkeypatch.setattr(ses, "SesServisi", SahteSes)
    havuz = _havuz(tmp_path)
    ctx = {"katilimcilar": [A, B], "gundem": "Bu toplantıda Kampanya Modülü ele alınacak.",
           "davet": {"govde": "Bu toplantıda Kampanya Modülü ele alınacak.", "ekler": []}}
    isi = isler.YakalamaIsi("Haftalık Mimari", ctx, havuz, False, 30, {"kaynak": "ses"})
    _Yakalayici.isi = isi
    sonuc = []
    isi.toplanti_bitti.connect(sonuc.append)
    isi.start()
    assert isi.wait(15000)
    _bekle(qapp, lambda: bool(sonuc))
    assert len(sonuc) == 1 and isinstance(sonuc[0], list)
    assert isi.motor.terimler is havuz and "Kampanya Modülü" in [e["terim"] for e in havuz.terimler]
    assert kurulan["ipucu_fn"]("loopback").startswith("Katılımcılar: " + A) and "Kampanya Modülü" in kurulan["ipucu_fn"]()
    meta = json.loads((tmp_path / "kayit" / "meta.json").read_text(encoding="utf-8"))
    assert meta["durum"] == "bitti" and meta["davet"]["govde"].startswith("Bu toplantıda")
    isi.motor.kapat()


def test_slug_turkce_buyuk_i_ve_prompt_serisi():
    assert motor_mod.slug("İstanbul Çarşı IFRS Haftalık") == "istanbul-carsi-ifrs-haftalik"
    assert motor_mod.slug("Haftalık Mimari") == "haftalik-mimari" and motor_mod.slug("") == "toplanti"


def test_gecmis_yeniden_ozetle_eski_kayitta_calisir_belirsizler_gosterilir(pencere, qapp, tmp_path, monkeypatch):
    """Geçmiş -> 'Yeniden özetle' (eski, raw'siz kayit): duzeltme istegi gitmez, not uretilir, Belirsizler paneli dolar,
    Baslat acik kalir; onay/inceleme adimi yoktur."""
    p = pencere
    monkeypatch.setattr(llm, "bolum_ozetle", lambda *a, **k: llm.normalize_ozet(
        {"ozet": "Eski toplantı konuşuldu.", "belirsiz_terimler": ["Eskit"]}))
    monkeypatch.setattr(llm, "birlestir", lambda bolumler, baglam, log, **k: "## Özet\nEski toplantı konuşuldu.\n")
    sorulan = []
    monkeypatch.setattr(llm, "sor", lambda mesajlar, *a, **k: sorulan.append(llm._gorev_adi(mesajlar)))
    k = tmp_path / "toplantilar" / "2026-09-23_eski"
    (k / "parcalar").mkdir(parents=True)
    (k / "meta.json").write_text(json.dumps({"baslik": "Eski", "tarih": "2026-09-23", "durum": "devam",
                                             "katilimcilar": [A], "parca": 1, "kaynak": "ses"}), encoding="utf-8")
    (k / "parcalar" / "parca_001.json").write_text(json.dumps(
        {"sira": 1, "neden": "token", "token": 900, "baslangic": "10:00:00", "bitis": "10:00:05",
         "satirlar": [{"id": 1, "ts": "10:00:00", "speaker": A, "text": "kubernetis kümesi"}], "ozet": None,
         "duzeltmeler": []}), encoding="utf-8")
    p.gecmis_yenile()
    p.g_liste.setCurrentRow(0)
    p.gecmis_yeniden_ozetle()
    assert _bekle(qapp, lambda: "Eski toplantı konuşuldu" in p.not_goster.toPlainText() and not p._yeniden_kilit)
    assert "duzeltme" not in sorulan and (k / "not.md").exists()
    assert p.b_ana.isEnabled() and "Eskit" in p.belirsiz_goster.toPlainText() and p.nav.currentRow() == p.SAYFA_NOT
    assert json.loads((k / "parcalar" / "parca_001.json").read_text(encoding="utf-8"))["satirlar"][0]["text"] \
        == "kubernetis kümesi"
    p.yeniden.wait(5000)


def test_dogru_ad_baska_katilimciya_cevrilmez_ve_ogrenilmez(tmp_path, saat, monkeypatch):
    """Bulgu regresyonu: 'Sercan'ın' (dogru, katilimci) iki toplantida 'Serkan'ın'a cevrilip Serkan'in varyanti
    olarak ogreniliyordu. Kapi eski'si zaten listedeki baska bir ad olan oneriyi reddeder; metin raw kalir.
    Yaygin liste tesadufune dayanmasin diye ad yaygin sayilmaz."""
    import kelime
    monkeypatch.setattr(kelime, "yaygin_mi", lambda *a, **k: False)
    havuz = _havuz(tmp_path)
    adlar = ["Serkan Ak", "Sercan Öz"]
    for gun in (5, 12):
        m = _toplanti(tmp_path, ad=f"2026-10-{gun:02d}_haftalik-mimari", havuz=havuz, tarih=dt.date(2026, 10, gun),
                      katilimcilar=adlar)
        m.duzelt_sor_fn = _oneri_sor({"Sercan'ın": ("Serkan'ın", "ad")})
        saat.t = T0
        _konus(m, saat, [("Sercan'ın önerisi bence mantıklı, devam edelim", adlar[0])])
        m.parca_kapat("token", zorla=True)
        for i in m.isler:
            i.result(timeout=10)
        s = m.parca_oku(1)["satirlar"][0]
        assert s["text"] == s["raw"] == "Sercan'ın önerisi bence mantıklı, devam edelim"
        m.bitir()
        m.kapat()
    assert havuz.gozlemler == [] and havuz._bul("Serkan") is None
