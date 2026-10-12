"""KVKK saklama suresi: konusma iceren her kalinti, notsuz toplantilar, aktif klasor, sinir gunleri, idempotans."""
import datetime as dt
import json
import os
import sys
import threading

import pytest

import motor as motor_mod

BUGUN = dt.date(2026, 10, 11)
SATIR = {"ts": "10:02:00", "speaker": "Selin Kara", "text": "Gizli toplanti cumlesi"}


def _tarih(gun_once):
    return (BUGUN - dt.timedelta(days=gun_once)).isoformat()


def _toplanti(kok, gun_once, durum="tamam", ad="strateji", tam=True):
    """Sentetik toplanti klasoru: tum konusma tasiyan kalintilar + korunacak dosyalar."""
    t = _tarih(gun_once)
    k = kok / f"{t}_{ad}"
    (k / "parcalar").mkdir(parents=True)
    (k / "meta.json").write_text(json.dumps({"baslik": "Strateji", "tarih": t, "durum": durum, "parca": 1}),
                                 encoding="utf-8")
    (k / "parcalar" / "parca_001.json").write_text(json.dumps(
        {"sira": 1, "baslangic": "10:00:00", "bitis": "10:09:00", "satirlar": [SATIR],
         "duzeltmeler": [{"eski": "a", "yeni": "b"}], "ozet": {"ozet": "MVP konusuldu."}}, ensure_ascii=False),
        encoding="utf-8")
    (k / "not.md").write_text("# Not", encoding="utf-8")
    (k / "olaylar.jsonl").write_text('{"tur": "konusmaci"}\n', encoding="utf-8")
    (k / "llm_log.jsonl").write_text(
        json.dumps({"gorev": "bolum", "finish": "stop", "sure_sn": 1.2}) + "\n"
        + json.dumps({"olay": "liste_geri_ekle", "madde": 3, "ornek": ["Gizli karar metni"]}) + "\n"
        + json.dumps({"gorev": "duzeltme", "http": 400, "hata": "istek: Gizli toplanti cumlesi"}) + "\n"
        + json.dumps({"olay": "liste_yedek", "neden": "hata: Gizli toplanti cumlesi", "madde": 2}) + "\n",
        encoding="utf-8")
    if not tam:
        return k
    for ad_ in ("altyazi.jsonl", "oneriler.json", "duzelt_log.jsonl"):
        (k / ad_).write_text(json.dumps(SATIR) + "\n", encoding="utf-8")
    (k / "olcum" / "klipler").mkdir(parents=True)
    (k / "olcum" / "kesinti").mkdir()
    (k / "olcum" / "klipler" / "000001.wav").write_bytes(b"RIFF....WAVE")
    (k / "olcum" / "kesinti" / "000002.wav").write_bytes(b"RIFF....WAVE")
    (k / "olcum" / "kesinti" / "000002.json").write_text("{}", encoding="utf-8")
    (k / "olcum" / "klipler.jsonl").write_text(json.dumps({"no": 1, "raw": "ham stt metni"}) + "\n", encoding="utf-8")
    (k / "olcum" / "okumalar.jsonl").write_text(json.dumps({"okuma_no": 1, "satirlar": ["x"]}) + "\n", encoding="utf-8")
    (k / "olcum" / "gercek.jsonl").write_text(json.dumps({"klip_no": 1, "text": "gercek"}) + "\n", encoding="utf-8")
    (k / "kesinti").mkdir()
    (k / "kesinti" / "000003.wav").write_bytes(b"RIFF....WAVE")
    (k / "rag_dizin.npz").write_bytes(b"npz")
    (k / "uzun_pencere").mkdir()
    (k / "uzun_pencere" / "onbellek.json").write_text('{"metin": "x"}', encoding="utf-8")
    (k / "replay_1").mkdir()
    (k / "replay_1" / "satirlar.jsonl").write_text(json.dumps(SATIR) + "\n", encoding="utf-8")
    (k / "cikti").mkdir()                                              # --cikti ile verilen ad
    (k / "cikti" / "satirlar.jsonl").write_text(json.dumps(SATIR) + "\n", encoding="utf-8")
    (k / "cikti" / "olcum.json").write_text("{}", encoding="utf-8")
    (k / "satirlar.jsonl").write_text(json.dumps(SATIR) + "\n", encoding="utf-8")
    (k / "kayit.wav").write_bytes(b"RIFF....WAVE")
    (k / "parcalar" / "ek.wav").write_bytes(b"RIFF....WAVE")
    (k / "altyazi.jsonl.yedek-20260101-1000").write_text(json.dumps(SATIR) + "\n", encoding="utf-8")
    (k / "duzelt_log.jsonl.yedek-20260101-1000").write_text("x", encoding="utf-8")
    (k / "parcalar.yedek-20260101-1000").mkdir()
    (k / "parcalar.yedek-20260101-1000" / "parca_001.json").write_text(json.dumps({"satirlar": [SATIR]}),
                                                                       encoding="utf-8")
    (k / "not.md.yedek-20260101-1000").write_text("# eski not", encoding="utf-8")
    return k


def _meta(k):
    return json.loads((k / "meta.json").read_text(encoding="utf-8"))


def _konusma_kaldi_mi(k):
    """Klasorde (herhangi bir dosyada, yedek dahil) transkript cumlesi gecen dosya var mi?"""
    for kok, _, dosyalar in os.walk(str(k)):
        for d in dosyalar:
            if d == "not.md" or d.startswith("not.md."):
                continue
            with open(os.path.join(kok, d), "rb") as f:
                if b"Gizli" in f.read():
                    return True
    return False


# ---------------------------------------------------------------- tum veri turleri

def test_her_kalinti_silinir_not_ve_ozet_kalir(tmp_path):
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 40)
    sonuc = motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    assert sonuc["toplanti"] == 1 and sonuc["tamam"] == 1 and sonuc["yarim"] == 0 and sonuc["hata"] == 0
    for ad in ("altyazi.jsonl", "oneriler.json", "duzelt_log.jsonl", "olcum", "kesinti", "rag_dizin.npz", "uzun_pencere",
               "replay_1", "cikti", "satirlar.jsonl", "kayit.wav", "altyazi.jsonl.yedek-20260101-1000",
               "duzelt_log.jsonl.yedek-20260101-1000", "parcalar.yedek-20260101-1000"):
        assert not (k / ad).exists(), ad
    assert not (k / "parcalar" / "ek.wav").exists()
    assert not any(f.endswith(".wav") for _, _, fs in os.walk(str(k)) for f in fs)
    # korunanlar
    assert (k / "not.md").read_text(encoding="utf-8") == "# Not"
    assert (k / "not.md.yedek-20260101-1000").exists()                 # not yedegi konusma tasimaz
    assert (k / "olaylar.jsonl").exists() and (k / "meta.json").exists()
    p = json.loads((k / "parcalar" / "parca_001.json").read_text(encoding="utf-8"))
    assert p["satirlar"] == [] and p["duzeltmeler"] == [] and p["ozet"] == {"ozet": "MVP konusuldu."}
    assert not _konusma_kaldi_mi(k)
    assert _meta(k)["transkript_silindi"] == BUGUN.isoformat() and _meta(k)["durum"] == "tamam"
    # her tur sayildi
    assert set(sonuc["tur"]) == {"transkript", "olcum", "ses", "dizin", "replay", "yedek", "llm_log"}
    assert all(v == 1 for v in sonuc["tur"].values())


def test_llm_log_icerik_alanlari_ayiklanir_sayilar_kalir(tmp_path):
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 40, tam=False)                                  # yalniz llm_log + parca satiri
    motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    satirlar = [json.loads(x) for x in (k / "llm_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(satirlar) == 4
    assert satirlar[0] == {"gorev": "bolum", "finish": "stop", "sure_sn": 1.2}
    assert satirlar[1] == {"olay": "liste_geri_ekle", "madde": 3}
    assert satirlar[2] == {"gorev": "duzeltme", "http": 400} and satirlar[3] == {"olay": "liste_yedek", "madde": 2}
    assert "Gizli" not in (k / "llm_log.jsonl").read_text(encoding="utf-8")


def test_llm_log_temizse_dosyaya_dokunulmaz(tmp_path):
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 40, tam=False)
    motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    yol = k / "llm_log.jsonl"
    once = os.stat(str(yol)).st_mtime_ns
    metin = yol.read_text(encoding="utf-8")
    assert motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])["toplanti"] == 0
    assert os.stat(str(yol)).st_mtime_ns == once and yol.read_text(encoding="utf-8") == metin


def test_bozuk_parca_dosyasi_silinir(tmp_path):
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 40, tam=False)
    (k / "parcalar" / "parca_002.json").write_text('{"satirlar": [{"text": "Gizli', encoding="utf-8")   # yarim yazim
    motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    assert not (k / "parcalar" / "parca_002.json").exists() and (k / "parcalar" / "parca_001.json").exists()
    assert not _konusma_kaldi_mi(k)


# ---------------------------------------------------------------- tamam / yarim

@pytest.mark.parametrize("durum", ["devam", "hata", "yarim", "bilinmeyen"])
def test_notsuz_toplanti_tarihe_gore_temizlenir(tmp_path, durum):
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 40, durum=durum)
    sonuc = motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    assert sonuc["toplanti"] == 1 and sonuc["yarim"] == 1 and sonuc["tamam"] == 0
    assert not (k / "altyazi.jsonl").exists() and not (k / "olcum").exists() and not _konusma_kaldi_mi(k)
    assert _meta(k)["durum"] == durum                                  # durum degismez: not uretilmedi, yalan soylenmez


def test_aktif_set_bilinmiyorsa_notsuz_toplanti_korunur(tmp_path):
    """Eski cagri bicimi (aktif verilmez): yalniz 'tamam' temizlenir, notsuz toplantiya dokunulmaz."""
    kok = tmp_path / "toplantilar"
    tamam = _toplanti(kok, 40, ad="a")
    devam = _toplanti(kok, 40, durum="devam", ad="b")
    assert motor_mod.saklama_uygula(30, str(kok), BUGUN) == 1
    assert not (tamam / "altyazi.jsonl").exists() and (devam / "altyazi.jsonl").exists()
    assert (devam / "olcum" / "klipler" / "000001.wav").exists()


def test_meta_bozuk_ya_da_yoksa_klasor_adindaki_tarih_kullanilir(tmp_path):
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 40)
    (k / "meta.json").write_text("{bozuk", encoding="utf-8")
    assert motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])["toplanti"] == 1
    assert not (k / "altyazi.jsonl").exists()
    assert (k / "meta.json").read_text(encoding="utf-8") == "{bozuk"   # bozuk meta uzerine yazilmaz
    # tarihi hic cikmayan klasor (toplanti degil) dokunulmaz
    yabanci = kok / "terimler"
    yabanci.mkdir()
    (yabanci / "terimler.json").write_text("{}", encoding="utf-8")
    (yabanci / "k.wav").write_bytes(b"x")
    motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    assert (yabanci / "k.wav").exists()


# ---------------------------------------------------------------- sinir gunleri

def test_sinir_gunleri(tmp_path):
    kok = tmp_path / "toplantilar"
    onceki = _toplanti(kok, 29, ad="n-1")
    tam = _toplanti(kok, 30, ad="n")
    sonraki = _toplanti(kok, 31, ad="n+1")
    notsuz_onceki = _toplanti(kok, 29, durum="devam", ad="d-1")
    notsuz_tam = _toplanti(kok, 30, durum="devam", ad="d")
    sonuc = motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    assert sonuc["toplanti"] == 3
    for k in (onceki, notsuz_onceki):
        assert (k / "altyazi.jsonl").exists() and (k / "olcum").exists() and "transkript_silindi" not in _meta(k)
    for k in (tam, sonraki, notsuz_tam):
        assert not (k / "altyazi.jsonl").exists() and not (k / "olcum").exists()
    gelecek = _toplanti(kok, -2, ad="gelecek")                         # saat/tarih kaymasi: gelecek tarih silinmez
    assert motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])["toplanti"] == 0 and (gelecek / "altyazi.jsonl").exists()


# ---------------------------------------------------------------- aktif klasor

def test_aktif_klasor_atlanir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kok = tmp_path / "toplantilar"
    aktif = _toplanti(kok, 40, durum="devam", ad="kayitta")
    aktif_tamam = _toplanti(kok, 40, durum="tamam", ad="ozetleniyor")        # 'Yeniden ozetle' calisan eski toplanti
    diger = _toplanti(kok, 40, durum="devam", ad="eski")
    sonuc = motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[str(aktif), str(aktif_tamam)])
    assert sonuc["toplanti"] == 1 and sonuc["aktif_atlanan"] == 2
    for k in (aktif, aktif_tamam):
        assert (k / "altyazi.jsonl").exists() and (k / "olcum" / "klipler" / "000001.wav").exists()
        assert (k / "parcalar" / "parca_001.json").exists() and "Gizli" in (k / "altyazi.jsonl").read_text(encoding="utf-8")
        assert "transkript_silindi" not in _meta(k)
    assert not (diger / "altyazi.jsonl").exists()
    # aktif yol goreli / sonunda ayirac / dolayli yazilsa da ayni klasor tanınır
    kok_goreli = os.path.join("toplantilar", "..", "toplantilar", aktif.name) + os.sep
    s2 = motor_mod.saklama_tara(30, "toplantilar", BUGUN, aktif=[kok_goreli.rstrip(os.sep)])
    assert s2["aktif_atlanan"] == 1 and s2["toplanti"] == 1                # yalniz aktif_tamam artik aktif degil
    assert (aktif / "altyazi.jsonl").exists()
    # aktif olmaktan cikinca temizlenir
    assert motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])["toplanti"] == 1
    assert not (aktif / "altyazi.jsonl").exists() and not (aktif_tamam / "altyazi.jsonl").exists()


# ---------------------------------------------------------------- idempotans ve kapali

def test_ikinci_calistirma_bir_sey_yapmaz(tmp_path):
    kok = tmp_path / "toplantilar"
    k1 = _toplanti(kok, 40, ad="a")
    k2 = _toplanti(kok, 50, durum="devam", ad="b")
    ilk = motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    assert ilk["toplanti"] == 2
    anlik = {str(p): p.read_bytes() for k in (k1, k2) for p in k.rglob("*") if p.is_file()}
    ikinci = motor_mod.saklama_tara(30, str(kok), BUGUN + dt.timedelta(days=3), aktif=[])
    assert ikinci == {"toplanti": 0, "tamam": 0, "yarim": 0, "tur": {}, "aktif_atlanan": 0, "hata": 0}
    assert {str(p): p.read_bytes() for k in (k1, k2) for p in k.rglob("*") if p.is_file()} == anlik
    assert _meta(k1)["transkript_silindi"] == BUGUN.isoformat()          # ilk silme tarihi korunur


def test_sonradan_olusan_kalinti_bir_sonraki_turda_silinir(tmp_path):
    """transkript_silindi isaretli toplantida sonradan olusan artik (or. rag dizini) da temizlenir."""
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 40)
    motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    (k / "rag_dizin.npz").write_bytes(b"npz")
    sonuc = motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    assert sonuc["toplanti"] == 1 and sonuc["tur"] == {"dizin": 1} and not (k / "rag_dizin.npz").exists()
    assert _meta(k)["transkript_silindi"] == BUGUN.isoformat()


@pytest.mark.parametrize("gun", [0, None, -5])
def test_saklama_sifir_kapali(tmp_path, gun):
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 4000)
    d = _toplanti(kok, 4000, durum="devam", ad="b")
    assert motor_mod.saklama_tara(gun, str(kok), BUGUN, aktif=[])["toplanti"] == 0
    assert motor_mod.saklama_uygula(gun, str(kok), BUGUN, aktif=[]) == 0
    for x in (k, d):
        assert (x / "altyazi.jsonl").exists() and (x / "olcum" / "klipler" / "000001.wav").exists()
        assert "Gizli" in (x / "llm_log.jsonl").read_text(encoding="utf-8")


def test_kok_yoksa_hata_vermez(tmp_path):
    assert motor_mod.saklama_tara(30, str(tmp_path / "yok"), BUGUN, aktif=[])["toplanti"] == 0


def test_silinemeyen_oge_temizlendi_sayilmaz_sonra_yeniden_denenir(tmp_path, monkeypatch):
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 40)
    gercek = motor_mod._saklama_sil

    def kilitli(yol):
        if yol.endswith("kayit.wav"):
            raise PermissionError("kilitli")
        return gercek(yol)

    monkeypatch.setattr(motor_mod, "_saklama_sil", kilitli)
    sonuc = motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    assert sonuc["hata"] == 1 and (k / "kayit.wav").exists() and not (k / "altyazi.jsonl").exists()
    assert "transkript_silindi" not in _meta(k)                      # tam temizlik yapilmadan 'silindi' denmez
    assert "silinemedi" in motor_mod.saklama_ozeti(sonuc, 30)
    monkeypatch.setattr(motor_mod, "_saklama_sil", gercek)
    sonuc = motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    assert sonuc["hata"] == 0 and not (k / "kayit.wav").exists() and _meta(k)["transkript_silindi"] == BUGUN.isoformat()


@pytest.mark.skipif(not hasattr(os, "symlink") or sys.platform.startswith("win"), reason="symlink")
def test_baglanti_izlenmez(tmp_path):
    """Toplanti klasoru icindeki baglanti hedefi disina cikilmaz."""
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 40)
    dis = tmp_path / "dis"
    dis.mkdir()
    (dis / "x.wav").write_bytes(b"x")
    os.symlink(str(dis), str(k / "baglanti"))
    motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    assert (dis / "x.wav").exists()


# ---------------------------------------------------------------- Olaylar ozeti (yalniz sayilar)

def test_ozet_yalniz_sayi_ve_tur_icerir(tmp_path):
    kok = tmp_path / "toplantilar"
    _toplanti(kok, 40, ad="gizli-proje-adi")
    _toplanti(kok, 40, durum="devam", ad="baska-gizli-ad")
    sonuc = motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[])
    m = motor_mod.saklama_ozeti(sonuc, 30)
    assert "2 toplantı temizlendi" in m and "notu üretilmiş 1" in m and "notsuz 1" in m
    assert "transkript 2" in m and "ölçüm kaydı 2" in m and "ses klibi 2" in m and "LLM günlüğü içeriği 2" in m
    assert "gizli" not in m.lower() and "strateji" not in m.lower()
    assert motor_mod.saklama_ozeti(motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[]), 30) is None


# ---------------------------------------------------------------- uygulama: acilis, saatlik zamanlayici, ayar kaydi

QtCore = pytest.importorskip("PyQt5.QtCore")
QtWidgets = pytest.importorskip("PyQt5.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def pencere(tmp_path, monkeypatch, qapp):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    import app
    monkeypatch.setattr(app.terim_mod, "uygulama_klasoru", lambda: str(tmp_path))
    monkeypatch.setattr(app.Pencere, "toplantilari_yenile", lambda self, elle=False: None)
    monkeypatch.setattr(app.Pencere, "llm_tani", lambda self, test=False: None)
    monkeypatch.setattr(app.Pencere, "kesinti_devam", lambda self: None)
    monkeypatch.setattr(app.outlook, "kullanici_adi", lambda: "")
    monkeypatch.setattr(app, "ayar_yaz", lambda ayar: None)
    monkeypatch.setattr(motor_mod, "KOK", str(tmp_path / "toplantilar"))
    p = app.Pencere()
    yield p
    qapp.processEvents()
    p.close()


def _log(p):
    return p.gunluk.toPlainText()


def test_app_saatlik_zamanlayici_ve_aktif_klasor(pencere, tmp_path):
    p = pencere
    assert p._saklama_zamanlayici.isActive() and p._saklama_zamanlayici.interval() == 3600 * 1000
    kok = tmp_path / "toplantilar"
    eski = _toplanti(kok, 400, durum="devam", ad="eski")
    aktif = _toplanti(kok, 400, durum="devam", ad="kayitta")
    p.ayar["saklama_gun"] = 30
    p.motor = type("M", (), {"klasor": str(aktif)})()                    # su an kaydedilen toplanti
    p._yakalama_acik = True                                              # yakalama suruyor: motor aktif
    try:
        p.saklama_uygula()
        assert not (eski / "altyazi.jsonl").exists() and (aktif / "altyazi.jsonl").exists()
        assert "1 toplantı temizlendi" in _log(p) and "notsuz 1" in _log(p)
        assert "1 toplantı şu an kullanımda olduğu için atlandı" in _log(p)       # atlanan da Olaylar'da sayiyla
        assert "eski" not in _log(p).replace("temizlendi", "")            # klasor adi Olaylar'a yazilmaz
        n = _log(p).count("temizlendi")
        p.saklama_uygula()                                               # ikinci tur: temizlenecek bir sey yok
        assert _log(p).count("temizlendi") == n and _log(p).count("atlandı") == 2
    finally:
        p._yakalama_acik = False
    p._saklama_zamanlayici.timeout.emit()                                # saatlik tur: motor acik ama is yok
    assert not (aktif / "altyazi.jsonl").exists() and _log(p).count("temizlendi") == n + 1


def test_app_ayar_kaydedilince_hemen_uygulanir_ve_sifir_kapali(pencere, tmp_path):
    p = pencere
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 400, ad="a")
    p.a_saklama.setValue(0)
    p.ayar_kaydet()
    assert (k / "altyazi.jsonl").exists()                                # 0 = kapali
    p.a_saklama.setValue(90)
    p.ayar_kaydet()
    assert p.ayar["saklama_gun"] == 90
    assert not (k / "altyazi.jsonl").exists() and not (k / "olcum").exists()
    assert "saklama süresi (90 gün)" in _log(p)
    assert "transkript" in p.a_saklama.toolTip() and "ses" in p.a_saklama.toolTip()


def test_ozet_aktif_atlanani_sayiyla_soyler(tmp_path):
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 40, ad="kayitta")
    sonuc = motor_mod.saklama_tara(30, str(kok), BUGUN, aktif=[str(k)])
    assert sonuc["toplanti"] == 0 and sonuc["aktif_atlanan"] == 1
    m = motor_mod.saklama_ozeti(sonuc, 30)
    assert m == "saklama süresi (30 gün): 1 toplantı şu an kullanımda olduğu için atlandı, sonraki turda denenecek"
    assert "kayitta" not in m


def test_app_yalniz_goruntulenen_eski_toplanti_korunmaz(pencere, tmp_path):
    """r9: Gecmis -> Notu ac ile acilan (is yok) 400 gunluk toplanti her saatlik turda 'aktif' sayilmaz."""
    p = pencere
    kok = tmp_path / "toplantilar"
    k = _toplanti(kok, 400, ad="acik-not")
    p.ayar["saklama_gun"] = 30
    p.motor = motor_mod.Motor.yukle(str(k))                              # goruntuleniyor; kayit / not / soru isi yok
    try:
        assert p._durum() == "bos" and p._saklama_aktif() == []
        p.saklama_uygula()
        assert not (k / "altyazi.jsonl").exists() and not _konusma_kaldi_mi(k)
        assert (k / "not.md").exists() and _meta(k)["transkript_silindi"] and p.motor.transkript_silindi()
        assert "1 toplantı temizlendi" in _log(p)
        # bir is calisirken (or. not uretimi) ayni motor korunur ve Olaylar'da sayisi yazilir
        k2 = _toplanti(kok, 400, ad="uretiliyor")
        p.motor.kapat()
        p.motor = motor_mod.Motor.yukle(str(k2))
        p._not_kilit = True
        try:
            assert p._saklama_aktif() == [str(k2)]
            p.saklama_uygula()
            assert (k2 / "altyazi.jsonl").exists() and "kullanımda olduğu için atlandı" in _log(p)
        finally:
            p._not_kilit = False
    finally:
        p.motor.kapat()
