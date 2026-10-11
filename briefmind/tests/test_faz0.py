"""Faz 0 acil duzeltmeler (MIMARI_v3 §7): bos altyazi penceresi tek konusmaci sayilmaz, K5 varsayilan kapali
ve K5_UST=0 gercekten kapatir, mikrofon->baskasi yalniz kulaklik yokken, worker istisnasi (threading.excepthook +
Qt sinyal koprusu), YakalamaIsi/ParcaIsi her yolda motor.bitir + bitis sinyali, not uretilirken Baslat kilidi.
Ses cihazi, ag, Outlook gerekmez; adlar uydurma."""
import datetime as dt
import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # PyQt5 import edilmeden ONCE

import pytest

import motor as motor_mod


def _motor(tmp_path, kaynak="ses", ben="Zeynep Örnek"):
    return motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date(2026, 9, 30), duzelt=False, kaynak=kaynak, ben=ben)


def _transkript(m):
    return [(s["speaker"], s["kaynak"], s["text"]) for s in m.mevcut]


# ---------------------------------------------------------------- 1) bos pencere tek konusmaci degil

def test_tek_konusmaci_bos_pencere_false(tmp_path):
    m = _motor(tmp_path)
    assert m._tek_konusmaci("10:00:05", "Kisi A") is False        # iz hic yok
    m.altyazi_satiri({"ts": "10:00:00", "speaker": "Kisi A", "text": "Bütçe tablosunu güncelledim"})
    assert m._tek_konusmaci("10:00:05", "Kisi A") is True         # pencerede yalniz Kisi A
    assert m._tek_konusmaci("10:00:05", "Kisi B") is False
    assert m._tek_konusmaci("10:00:25", "Kisi A") is False        # 25 sn sonra: pencere (±10) bos
    m.kapat()


def test_altyazisiz_pencerede_whisper_satiri_son_konusmaciya_yazilmaz(tmp_path):
    m = _motor(tmp_path)
    m.altyazi_satiri({"ts": "10:00:00", "speaker": "Kisi A", "text": "Bütçe tablosunu güncelledim"})
    # 25 sn sonra altyazi yok (kapali/kayip): _konusmaci_bul (±30) Kisi A'yi bulur ama ±10 pencere bos
    s = m.ses_satiri("10:00:25", None, "Kampanya görsellerini de ekledim", "loopback")
    assert s["speaker"] == "?"
    m.kapat()


# ---------------------------------------------------------------- 2) K5 varsayilan kapali, K5_UST=0 kapatir

def _k5_senaryosu(m):
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Kisi A", "text": "Kampanya bütçesini yarın onaylarım"})
    m.altyazi_satiri({"ts": "10:00:02", "speaker": "Kisi B", "text": "Test ortamı perşembe hazır olacak"})
    m.altyazi_satiri({"ts": "10:00:02", "speaker": "Kisi C", "text": "Yük testini pazartesi koşarız"})
    return m.ses_satiri("10:00:02", None, "Test ortamı perşembe hazır olacak.", "loopback")


def test_k5_varsayilan_kapali_altyazi_metni_girmez(tmp_path):
    assert motor_mod.K5_UST == 0
    m = _motor(tmp_path)
    s = _k5_senaryosu(m)
    assert s["speaker"] == "Kisi B"
    assert [k for _, k, _ in _transkript(m)] == ["ses"]
    assert not any(k.startswith("altyazi") for _, k, _ in _transkript(m))
    m.kapat()


def test_k5_ust_0_gercekten_kapatir_ust_n_tam_n_ekler(tmp_path, monkeypatch):
    monkeypatch.setattr(motor_mod, "K5_UST", 0)
    m = _motor(tmp_path)
    _k5_senaryosu(m)
    assert not any(k == "altyazi-cakisma" for _, k, _ in _transkript(m))
    m.kapat()
    # sinir EKLEMEDEN once denetlenir: UST=1 iken iki aday olsa da yalniz bir satir girer
    monkeypatch.setattr(motor_mod, "K5_UST", 1)
    m2 = motor_mod.Motor(str(tmp_path / "t2"), "Test", dt.date(2026, 9, 30), duzelt=False, kaynak="ses")
    _k5_senaryosu(m2)
    assert sum(1 for _, k, _ in _transkript(m2) if k == "altyazi-cakisma") == 1
    m2.kapat()


# ---------------------------------------------------------------- 3) mikrofon->baskasi yalniz kulaklik yokken

def _yanki(m):
    m.altyazi_satiri({"ts": "10:00:01", "speaker": "Ayşe Demir", "text": "Test ortamı perşembe hazır olacak"})
    return m.ses_satiri("10:00:02", "ben", "Test ortamı perşembe hazır olacak.", "mikrofon")


@pytest.mark.parametrize("kulaklik,beklenen", [(None, "Zeynep Örnek"), (True, "Zeynep Örnek"),
                                               (False, "Ayşe Demir")])
def test_mikrofon_satiri_yalniz_kulaklik_yokken_baskasina_gider(tmp_path, kulaklik, beklenen):
    m = _motor(tmp_path)
    assert m.kulaklik is None                                      # varsayilan: bilinmiyor
    m.kulaklik = kulaklik
    assert _yanki(m)["speaker"] == beklenen
    m.kapat()


# ---------------------------------------------------------------- Qt testleri

QtCore = pytest.importorskip("PyQt5.QtCore")
QtWidgets = pytest.importorskip("PyQt5.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bekle(qapp, kosul, sn=10.0):
    import time
    son = time.time() + sn
    while time.time() < son:
        qapp.processEvents()
        if kosul():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return kosul()


@pytest.fixture
def ortam(tmp_path, monkeypatch, qapp):
    """Calisma klasoru tmp (hata.log, config.json oraya), kancalar test sonunda eski haline doner."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    return tmp_path


# ---------------------------------------------------------------- 4) threading.excepthook + sinyal koprusu

def test_worker_istisnasi_loglanir_ana_threadde_gosterilir(ortam, monkeypatch, qapp):
    import app
    monkeypatch.setattr(app, "_KOPRU", None)
    gosterilen = []
    monkeypatch.setattr(app, "_hata_kutusu", lambda b, tb: gosterilen.append((b, tb, threading.current_thread())))
    app.hata_koprusu_kur()
    app.hata_kancalari_kur()
    assert threading.excepthook is app._thread_excepthook and sys.excepthook is app._excepthook

    def is_():
        raise ValueError("uydurma worker hatasi")

    t = threading.Thread(target=is_, name="deneme-isci")
    t.start()
    t.join()
    assert _bekle(qapp, lambda: len(gosterilen) == 1)
    baslik, tb, thread = gosterilen[0]
    assert thread is threading.main_thread()                        # QMessageBox yalniz ana thread
    assert "uydurma worker hatasi" in tb and "deneme-isci" in tb
    assert "uydurma worker hatasi" in open("hata.log", encoding="utf-8").read()

    # sys.excepthook (ana thread) ayni yolu kullanir; SystemExit sessizce gecer
    try:
        raise KeyError("ana")
    except KeyError:
        app._excepthook(*sys.exc_info())
    assert _bekle(qapp, lambda: len(gosterilen) == 2)
    t2 = threading.Thread(target=lambda: sys.exit(0))
    t2.start()
    t2.join()
    qapp.processEvents()
    assert len(gosterilen) == 2


def test_koprusuz_worker_hatasi_yine_loglanir_pencere_acmaz(ortam, monkeypatch, qapp):
    import app
    monkeypatch.setattr(app, "_KOPRU", None)
    gosterilen = []
    monkeypatch.setattr(app, "_hata_kutusu", lambda b, tb: gosterilen.append(tb))
    app.hata_kancalari_kur()

    def is_():
        raise RuntimeError("koprusuz")

    t = threading.Thread(target=is_)
    t.start()
    t.join()
    qapp.processEvents()
    assert gosterilen == [] and "koprusuz" in open("hata.log", encoding="utf-8").read()


# ---------------------------------------------------------------- 5) YakalamaIsi / ParcaIsi korumasi

class SahteYakalayici:
    hata_at = None                                                  # sinif ozelligi: oku() bunu firlatir
    isi = None

    def __init__(self):
        self.hazir = False

    def oku(self):
        if SahteYakalayici.hata_at:
            raise SahteYakalayici.hata_at
        SahteYakalayici.isi.durdur()                                 # normal yol: dongu bir turdan sonra biter
        return []

    def kayip_saniye(self):
        return 0

    def onerilen_aralik(self):
        return 0

    def bitir(self):
        return []


class SahteMotor:
    """isler.Motor yerine: disk/LLM yok; bitir cagrilarini sayar."""
    bitir_hatasi = None

    def __init__(self, klasor, baslik, tarih, sozluk, katilimcilar, gundem, **kw):
        self.klasor, self.kaynak, self.parca_no, self.mevcut_tok = klasor, kw.get("kaynak"), 0, 0
        self.seri, self.katilimcilar, self.bitir_sayisi, self.kulaklik = "x", [], 0, None
        self.ben, self.sablon = "Deneme Kisi", "genel"

    def altyazi_satiri(self, s):
        pass

    def kontrol(self):
        pass

    def bitir(self):
        self.bitir_sayisi += 1
        if SahteMotor.bitir_hatasi:
            raise SahteMotor.bitir_hatasi
        return [{"id": 1}]


class _Sozluk:
    def metin(self, seri):
        return ""


@pytest.fixture
def yakalama(ortam, monkeypatch):
    import isler
    SahteYakalayici.hata_at, SahteMotor.bitir_hatasi = None, None
    monkeypatch.setattr(isler, "Yakalayici", SahteYakalayici)
    monkeypatch.setattr(isler, "Motor", SahteMotor)
    monkeypatch.setattr(isler, "ekran_okuyucu", lambda acik: None)
    monkeypatch.setattr(isler.motor_mod, "yeni_klasor", lambda baslik, tarih: str(ortam / "kayit"))

    def yap():
        isi = isler.YakalamaIsi("Deneme", {}, _Sozluk(), False, 30, {"kaynak": "altyazi"})
        SahteYakalayici.isi = isi
        olaylar = {"hazir": [], "hata": [], "log": []}
        isi.inceleme_hazir.connect(lambda o: olaylar["hazir"].append(o))
        isi.hata.connect(lambda m: olaylar["hata"].append(m))
        isi.olay.connect(lambda t, v: olaylar["log"].append((t, v)))
        return isi, olaylar
    return yap


def _calistir(qapp, isi, olaylar):
    isi.start()
    assert isi.wait(15000)
    _bekle(qapp, lambda: bool(olaylar["hazir"]))


def test_yakalama_normal_yolda_bitir_bir_kez_ve_inceleme_hazir(yakalama, qapp):
    isi, olaylar = yakalama()
    _calistir(qapp, isi, olaylar)
    assert isi.motor.bitir_sayisi == 1
    assert olaylar["hazir"] == [[{"id": 1}]] and olaylar["hata"] == []


def test_yakalama_dongu_istisnasi_cokertmez_bitir_cagrilir_hata_ve_bitis_yayilir(yakalama, qapp):
    SahteYakalayici.hata_at = RuntimeError("uydurma okuma hatasi")
    isi, olaylar = yakalama()
    _calistir(qapp, isi, olaylar)
    assert isi.motor.bitir_sayisi == 1                               # finally: bir kez
    assert len(olaylar["hazir"]) == 1                                # arayuz motoru baglayip Baslat'i acabilir
    assert len(olaylar["hata"]) == 1 and "uydurma okuma hatasi" in olaylar["hata"][0]
    assert "uydurma okuma hatasi" in open("hata.log", encoding="utf-8").read()


def test_yakalama_motor_kurulamazsa_yine_bitis_sinyali(yakalama, qapp, monkeypatch):
    import isler

    def patla(*a, **k):
        raise OSError("uydurma disk hatasi")
    monkeypatch.setattr(isler, "Motor", patla)
    isi, olaylar = yakalama()
    _calistir(qapp, isi, olaylar)
    assert isi.motor is None and olaylar["hazir"] == [[]] and len(olaylar["hata"]) == 1


def test_yakalama_bitir_hatasi_yine_bitis_sinyali_verir(yakalama, qapp):
    SahteMotor.bitir_hatasi = RuntimeError("uydurma bitir hatasi")
    isi, olaylar = yakalama()
    _calistir(qapp, isi, olaylar)
    assert isi.motor.bitir_sayisi == 1                               # tekrar denenmez
    assert olaylar["hazir"] == [[]] and "uydurma bitir hatasi" in olaylar["hata"][0]


def test_parca_isi_istisnasi_hata_sinyali_verir(ortam, qapp):
    import isler

    class Motorcuk:
        def parca_yeniden(self, no):
            raise RuntimeError("uydurma ozetleme hatasi")

    isi = isler.ParcaIsi(Motorcuk(), 3)
    bitti, hata = [], []
    isi.bitti.connect(bitti.append)
    isi.hata.connect(hata.append)
    isi.start()
    assert isi.wait(10000)
    _bekle(qapp, lambda: bool(hata))
    assert bitti == [] and len(hata) == 1 and "uydurma ozetleme hatasi" in hata[0]


def test_yakalama_kulaklik_durumu_motora_gecer(yakalama, qapp):
    isi, _ = yakalama()
    isi.motor = SahteMotor("k", "b", None, None, None, "")
    isi.motor.ses_satiri = lambda *a: None
    isi.ses_servisi = type("S", (), {"kulaklik": False})()
    isi._ses_satiri("10:00:00", "ben", "x", "mikrofon")
    assert isi.motor.kulaklik is False
    isi.ses_servisi = None
    isi._ses_satiri("10:00:00", "ben", "x", "mikrofon")
    assert isi.motor.kulaklik is None


# ---------------------------------------------------------------- 6) not uretilirken Baslat kilidi

class SahteTamamlama(QtCore.QThread):
    """Gercek TamamlamaIsi yerine: serbest birakilana kadar bekler, sonra bitti ya da hata yayar."""
    olay = QtCore.pyqtSignal(str, object)
    bitti = QtCore.pyqtSignal(str)
    hata = QtCore.pyqtSignal(str)
    birakildi = threading.Event()
    basarisiz = False

    def __init__(self, m, kararlar):
        super().__init__()

    def run(self):
        SahteTamamlama.birakildi.wait(15)
        if SahteTamamlama.basarisiz:
            self.hata.emit("uydurma not hatasi")
        else:
            self.bitti.emit("## Not\n\n- madde")


class SahteYakalamaIsi(QtCore.QThread):
    olay = QtCore.pyqtSignal(str, object)
    durum = QtCore.pyqtSignal(str)
    inceleme_hazir = QtCore.pyqtSignal(list)
    hata = QtCore.pyqtSignal(str)
    olusturulan = []

    def __init__(self, *a, **k):
        super().__init__()
        self.motor = None
        SahteYakalamaIsi.olusturulan.append(self)

    def durdur(self):
        pass

    def run(self):
        pass


@pytest.fixture
def pencere(ortam, monkeypatch, qapp):
    import app
    monkeypatch.setattr(app.Pencere, "toplantilari_yenile", lambda self, elle=False: None)
    monkeypatch.setattr(app.Pencere, "saklama_uygula", lambda self: None)
    monkeypatch.setattr(app.Pencere, "llm_tani", lambda self, test=False: None)
    monkeypatch.setattr(app, "TamamlamaIsi", SahteTamamlama)
    monkeypatch.setattr(app, "YakalamaIsi", SahteYakalamaIsi)
    monkeypatch.setattr(app.outlook, "kullanici_adi", lambda: "Deneme Kisi")
    uyarilar = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: uyarilar.append(a))
    SahteTamamlama.birakildi = threading.Event()
    SahteTamamlama.basarisiz = False
    SahteYakalamaIsi.olusturulan = []
    p = app.Pencere()
    p.a_otomatik_not.setChecked(True)
    p.a_outlook.setChecked(False)
    p.uyarilar = uyarilar
    p.motor = SahteMotor(str(ortam / "kayit"), "Deneme", None, None, None, "")
    os.makedirs(p.motor.klasor, exist_ok=True)
    yield p
    SahteTamamlama.birakildi.set()
    if p.tamamla is not None:
        p.tamamla.wait(5000)
    qapp.processEvents()
    p.close()


def test_not_uretilirken_baslat_kapali_ve_cift_baslat_engellenir(pencere, qapp):
    p = pencere
    # gercek akis: yakalama bitti -> inceleme_hazir (Baslat'i acar) -> otomatik not baslar -> Baslat yine kapanmali
    p.is_ = SahteYakalamaIsi()
    p.is_.motor = p.motor
    p.inceleme_hazir([])
    assert p.tamamla is not None and p.tamamla.isRunning()
    assert p.b_ana.isEnabled() is False
    assert p.b_ana.text() == "Başlat"

    SahteYakalamaIsi.olusturulan = []
    p.baslat()                                                       # elle cagrilsa bile (kisayol, otomatik baslat)
    p.ana_dugme()
    assert SahteYakalamaIsi.olusturulan == []                        # yeni toplanti eski nota baglanmadi

    SahteTamamlama.birakildi.set()                                   # not bitti
    assert _bekle(qapp, lambda: p.b_ana.isEnabled())
    assert p._not_kilit is False
    p.a_ben.setText("Deneme Kisi")
    p.toplanti_secici.setEditText("Deneme Toplantisi")
    p.baslat()
    assert len(SahteYakalamaIsi.olusturulan) == 1                    # artik yeni toplanti baslayabilir
    p.is_.wait(3000)


def test_not_hatasinda_baslat_yeniden_acilir(pencere, qapp):
    p = pencere
    SahteTamamlama.basarisiz = True
    p._not_uret({})
    assert p.b_ana.isEnabled() is False and p._not_kilit is True
    SahteTamamlama.birakildi.set()
    assert _bekle(qapp, lambda: p.b_ana.isEnabled())
    assert p._not_kilit is False and len(p.uyarilar) == 1


def test_not_isi_sinyal_vermeden_olurse_kilit_yine_acilir(pencere, qapp, monkeypatch):
    p = pencere

    class SessizOlen(SahteTamamlama):
        def run(self):
            pass                                                     # ne bitti ne hata
    import app
    monkeypatch.setattr(app, "TamamlamaIsi", SessizOlen)
    p._not_uret({})
    assert _bekle(qapp, lambda: p.b_ana.isEnabled())
    assert p._not_kilit is False


def test_yakalama_motorsuz_biterse_arayuz_sifirlanir(pencere, qapp):
    p = pencere
    p.motor = None
    p.is_ = SahteYakalamaIsi()                                       # motor=None: Motor kurulamadi
    p.b_ana.setEnabled(False)
    p.inceleme_hazir([])
    assert p.b_ana.isEnabled() and p.tamamla is None
