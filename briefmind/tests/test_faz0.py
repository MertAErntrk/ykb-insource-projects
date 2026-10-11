"""Faz 0 acil duzeltmeler (MIMARI_v3 §7), Faz 1 davranisina guncellendi: metin benzerligiyle atama, K5 ve
_tek_konusmaci kaldirildi (altyazisiz aralikta klip 'Bilinmeyen', altyazi metni transkripte hic girmez), mikrofon
klibi uzak kisi konusurken yalniz kulaklik VARSA kullanicinin (yoksa yanki: atilir); worker istisnasi
(threading.excepthook + Qt sinyal koprusu), YakalamaIsi/ParcaIsi her yolda motor.bitir + bitis sinyali, not
uretilirken Baslat kilidi. Ses cihazi, ag, Outlook gerekmez; adlar uydurma."""
import datetime as dt
import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # PyQt5 import edilmeden ONCE

import pytest

import motor as motor_mod
from metin import gorunen_ad
from test_motor_faz1 import BEN, T0, akit, saat_kur


@pytest.fixture
def saat(monkeypatch):
    return saat_kur(monkeypatch)


A_, B_ = "Ayşe Demir", "Kisi B"


def _motor(tmp_path, ben=BEN):
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date.fromtimestamp(T0), duzelt=False, ben=ben,
                        katilimcilar=[A_, B_, ben])
    m._isle = lambda veri, duzelt: None
    return m


# ---------------------------------------------------------------- 1) altyazisiz aralik: tahmin yok

def test_tek_konusmaci_ve_metin_benzerligi_kaldirildi():
    for ad in ("_tek_konusmaci", "_konusmaci_bul", "_cakisanlar", "_bekleyen_altyazilar", "bekleyen_ses"):
        assert not hasattr(motor_mod.Motor, ad), ad
    assert not hasattr(motor_mod, "K5_UST") and not hasattr(motor_mod, "IKISI_BEKLE_SN")


def test_altyazisiz_aralikta_klip_son_konusmaciya_yazilmaz(tmp_path, saat):
    m = _motor(tmp_path)
    # Kisi A 1-4 sn konusur; 20. sn'den sonra altyazi kapali; 25. sn'deki klip 'Bilinmeyen' (eskiden Kisi A)
    akit(m, saat, [(A_, 1, 4)], [(1, "loopback", 1, 4, "Bütçe tablosunu güncelledim."),
                                (2, "loopback", 25, 28, "Kampanya görsellerini de ekledim.")], 30,
         kapali=((20, 60),))
    assert [s["speaker"] for s in m.mevcut] == [A_, "?"] and gorunen_ad(m.mevcut[1]) == "Bilinmeyen"
    m.kapat()


# ---------------------------------------------------------------- 2) altyazi metni transkripte girmez

def test_altyazi_metni_transkripte_hic_girmez(tmp_path, saat):
    m = _motor(tmp_path)
    for i, kim in enumerate((A_, B_, "Kisi C")):
        m.altyazi_satiri({"ts": "10:00:0%d" % i, "speaker": kim, "text": "Test ortamı perşembe hazır olacak"})
    s = m.ses_olayi({"tur": "klip", "klip_no": 1, "akis": "loopback", "bas": T0, "bit": T0 + 2, "sure": 2.0,
                     "text": "Test ortamı perşembe hazır olacak.", "ts": "10:00:02", "rms": 0.1, "zarf": [],
                     "ust_klip": None, "supheli": False})
    assert [x["kaynak"] for x in m.mevcut] == ["ses"] and m.mevcut[0] is s
    m.kapat()


# ---------------------------------------------------------------- 3) mikrofon klibi: ben / yanki

@pytest.mark.parametrize("kulaklik,beklenen", [(None, None), (True, BEN), (False, None)])
def test_mikrofon_klibi_uzak_konusurken_yalniz_kulaklikla_ben(tmp_path, saat, kulaklik, beklenen):
    """Ayse konusurken mikrofona ayni ses geldi, kullanicinin altyazisi yok: kulaklik varsa kullanicinin sozu
    (dusuk guven), yoksa/bilinmiyorsa yanki -> satir atilir (loopback kopyasi zaten var). Kimseye yazilmaz."""
    m = _motor(tmp_path)
    m.ses_olayi({"tur": "kulaklik", "durum": kulaklik, "guven": 0.9})
    akit(m, saat, [(A_, 1, 5)], [(1, "loopback", 1, 5, "Test ortamı perşembe hazır olacak."),
                                (2, "mikrofon", 1.2, 4.8, "Test ortamı perşembe hazır olacak.")], 8)
    mik = [s for s in m.mevcut if s["akis"] == "mikrofon"]
    if beklenen is None:
        assert mik == []
    else:
        assert [s["speaker"] for s in mik] == [beklenen] and gorunen_ad(mik[0]) == beklenen + "?"
    assert [s["speaker"] for s in m.mevcut if s["akis"] == "loopback"] == [A_]
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

    def __init__(self, katilimcilar=(), okuma_fn=None):
        self.hazir = False
        self.okuma_fn = okuma_fn

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
        self.ses_olay_sayisi, self.yeniden_kes_fn, self.stt = 0, None, []

    def altyazi_satiri(self, s):
        pass

    def ses_olayi(self, olay):
        pass

    def okuma_ekle(self, okuma):
        pass

    def stt_durumu_ayarla(self, durum):
        self.stt.append(durum)

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
    import ses

    def ses_yok(*a, **k):
        raise OSError("uydurma: ses cihazi yok")
    monkeypatch.setattr(ses, "SesServisi", ses_yok)                 # ses kurulamaz: STT yok durumu, altyazi moduna dusmez

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


def test_yakalama_ses_kurulamazsa_altyazi_moduna_dusmez_stt_yok_bildirir(yakalama, qapp):
    isi, olaylar = yakalama()
    _calistir(qapp, isi, olaylar)
    assert isi.motor.kaynak == "ses" and isi.ses_servisi is None
    assert isi.motor.stt and isi.motor.stt[0]["durum"] == "kesinti"
    tipler = [t for t, _ in olaylar["log"]]
    assert "stt_durum" in tipler and "kaynak_degisti" not in tipler
    assert any("artık yok" in str(v) for t, v in olaylar["log"] if t == "log")   # eski 'altyazi' ayari bildirilir


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


# ---------------------------------------------------------------- 7) canli gorunum: Faz 1 olaylari

def test_canli_gorunum_beklemede_karar_sil_degistir_seritler(pencere, qapp):
    p = pencere
    p.ayar["altyazi_goster"] = True
    s1 = {"id": 1, "klip_no": 1, "ts": "10:00:01", "speaker": "?", "spk_guven": 0.0, "text": "Birinci satır",
          "kaynak": "ses", "durum": "beklemede"}
    s2 = dict(s1, id=2, klip_no=2, ts="10:00:05", text="İkinci satır")
    p.olay("satir", s1)
    p.olay("satir", s2)
    p.olay("altyazi_izi", {"ts": "10:00:02", "speaker": A_, "text": "bozuk altyazı", "guncelle": False})
    assert "beklemede…" in p.akis.toPlainText() and "[altyazı]" in p.akis.toPlainText()
    p.olay("satir_guncelle", dict(s1, speaker=A_, spk_guven=0.6, durum="karar"))
    assert f"{A_}?: Birinci satır" in p.akis.toPlainText()
    p.olay("satir_degistir", {"eski_id": 2, "yeni": [dict(s2, id=3, text="Parça bir", durum="karar", speaker=B_,
                                                          spk_guven=0.9),
                                                     dict(s2, id=4, text="Parça iki")]})
    metin = p.akis.toPlainText()
    assert "İkinci satır" not in metin and f"{B_}: Parça bir" in metin and metin.index("Parça bir") < \
        metin.index("Parça iki")
    p.olay("satir_sil", {"id": 1, "neden": "yanki"})
    assert "Birinci satır" not in p.akis.toPlainText() and "bozuk altyazı" in p.akis.toPlainText()
    p.olay("stt_durum", {"durum": "kesinti", "bas": T0, "eksik": [], "kayit": False})
    assert p.stt_serit.isVisibleTo(p) and p.stt_serit.text().startswith("STT yok — transkript eksik (")
    p.olay("stt_durum", {"durum": "ok", "bas": None, "eksik": [], "kayit": False})
    assert not p.stt_serit.isVisibleTo(p)
    p.olay("kapsama", {"var": False, "bas": T0})
    assert p.kapsama_serit.isVisibleTo(p) and "Konuşmacı bilgisi yok" in p.kapsama_serit.text()
    p.olay("kapsama", {"var": True, "bas": None})
    assert not p.kapsama_serit.isVisibleTo(p)
    assert p.a_kaynak.count() == 1 and p.a_kaynak.currentData() == "ses"
    assert not p.a_altyazi_yedek.isChecked()
