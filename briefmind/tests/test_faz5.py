"""Faz 5 kabuk saglamligi (MIMARI_v3 §7 Faz5): tek mesgul durum makinesi (bos | yakalaniyor | not_uretiliyor |
yeniden_ozetleniyor | soru), toplanti_bitti'nin thread GERCEKTEN bittikten sonra islenmesi, Outlook COM / UIA / STT testinin
arayuz thread'inde calismamasi, closeEvent'in iscileri durdurup finished'lerini beklemesi (acik parca <=30 sn'de diske),
ayar.py (BOM, atomik yazim, sema + bir kez uyari, goc), hata.log yolu, onceki oturumun kesinti kuyrugu, --duman.
Ses cihazi, ag, Outlook gerekmez (sahte COM); adlar uydurma."""
import json
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # PyQt5 import edilmeden ONCE

import pytest

import ayar as ayar_mod
import outlook
import saat

QtCore = pytest.importorskip("PyQt5.QtCore")
QtWidgets = pytest.importorskip("PyQt5.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bekle(qapp, kosul, sn=10.0):
    son = time.time() + sn
    while time.time() < son:
        qapp.processEvents()
        if kosul():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return kosul()


def _guvenli_kapat(qapp, p):
    """Test sonu: pencerenin butun iscilerini bekler (QThread calisirken yok edilirse Qt cokertir), sonra kapatir."""
    KAPI.hepsini_birak()
    for _ in range(100):
        qapp.processEvents()
        if not p._calisan_isler():
            break
        time.sleep(0.05)
    p._kapanis_tamam = True
    p.close()
    qapp.processEvents()


@pytest.fixture
def ortam(tmp_path, monkeypatch):
    """Calisma klasoru tmp (config.json, hata.log, toplantilar/ oraya); gercek kullanici profiline yazilmaz."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    ayar_mod.uyarilari_sifirla()
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    yield tmp_path
    ayar_mod.uyarilari_sifirla()


# =============================================================== ayar.py: BOM, atomik yazim, sema, goc

def test_bomlu_config_okunur_ve_yazilan_dosya_bomsuzdur(ortam):
    (ortam / "config.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"stt_url": "http://stt.ornek", "otobitir": 90,
                                                                     "ben": "Deniz Kaya"}).encode("utf-8"))
    v = ayar_mod.ayar_oku()
    assert ayar_mod.AYAR_HATASI is None
    assert v["stt_url"] == "http://stt.ornek" and v["otobitir"] == 90 and v["ben"] == "Deniz Kaya"
    assert ayar_mod.ham_oku()["stt_url"] == "http://stt.ornek"
    ayar_mod.ayar_yaz(v)
    assert not (ortam / "config.json").read_bytes().startswith(b"\xef\xbb\xbf")
    assert ayar_mod.ayar_oku()["otobitir"] == 90


def test_tls_dogrulama_bomlu_config_ve_ca_bundle(ortam):
    (ortam / "kok.cer").write_text("x", encoding="utf-8")
    (ortam / "config.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"ca_bundle": "kok.cer"}).encode("utf-8"))
    assert ayar_mod.tls_dogrulama() == "kok.cer"
    assert ayar_mod.tls_dogrulama({"ca_bundle": ""}) is False


def test_bozuk_json_ve_nesne_olmayan_json_hata_verir_varsayilanla_acar(ortam):
    (ortam / "config.json").write_text("{bozuk", encoding="utf-8")
    v = ayar_mod.ayar_oku()
    assert v["stt_ipucu"] is True and ayar_mod.AYAR_HATASI
    (ortam / "config.json").write_text("[1, 2]", encoding="utf-8")
    v = ayar_mod.ayar_oku()
    assert v["otobitir"] == 180 and "nesne" in ayar_mod.AYAR_HATASI
    (ortam / "config.json").write_text("{}", encoding="utf-8")
    ayar_mod.ayar_oku()
    assert ayar_mod.AYAR_HATASI is None


def test_atomik_yazim_hata_olursa_eski_dosya_bozulmaz_gecici_dosya_kalmaz(ortam, monkeypatch):
    ayar_mod.ayar_yaz({"stt_url": "eski"})
    eski = (ortam / "config.json").read_text(encoding="utf-8")

    def patla(*a, **k):
        raise PermissionError("uydurma: dosya acik")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", patla)
        m.setattr(ayar_mod.time, "sleep", lambda s: None)
        with pytest.raises(PermissionError):
            ayar_mod.ayar_yaz({"stt_url": "yeni"})
    assert (ortam / "config.json").read_text(encoding="utf-8") == eski          # eski dosya sapasaglam
    assert sorted(os.listdir(ortam)) == ["config.json"]                         # .tmp artigi yok
    ayar_mod.ayar_yaz({"stt_url": "yeni"})
    assert json.loads((ortam / "config.json").read_text(encoding="utf-8"))["stt_url"] == "yeni"


def test_atomik_yazim_gecici_hata_yeniden_denenir(ortam, monkeypatch):
    gercek = os.replace
    cagri = []

    def bir_kez_patla(a, b):
        cagri.append(1)
        if len(cagri) == 1:
            raise PermissionError("uydurma")
        return gercek(a, b)
    monkeypatch.setattr(os, "replace", bir_kez_patla)
    monkeypatch.setattr(ayar_mod.time, "sleep", lambda s: None)
    ayar_mod.ayar_yaz({"ben": "Ayşe Demir"})
    assert len(cagri) == 2 and json.loads((ortam / "config.json").read_text(encoding="utf-8"))["ben"] == "Ayşe Demir"


def test_sema_yanlis_tip_varsayilana_doner_ve_uyari_bir_kez(ortam):
    (ortam / "config.json").write_text(json.dumps({
        "otobitir": "bir saat", "duzelt": "evet", "stt_url": 5, "context": "4096", "saklama_gun": 99999,
        "stt_repetition_penalty": "cok", "esikler": [1], "mikrofon_cihaz": None, "bilinmeyen_anahtar": {"a": 1}}),
        encoding="utf-8")
    v = ayar_mod.ayar_oku()
    assert v["otobitir"] == 180 and v["stt_url"] == "" and v["stt_repetition_penalty"] is None
    assert v["duzelt"] is True                                   # "evet" -> true (belirsiz olmayan donusum)
    assert v["context"] == 4096 and v["saklama_gun"] == 3650     # metin sayi + sinira cekme
    assert "esikler" not in v                                    # varsayilani yok: yok sayilir, tuketici kendi varsayilanini kullanir
    assert v["bilinmeyen_anahtar"] == {"a": 1} and v["mikrofon_cihaz"] is None
    uyarilar = ayar_mod.ayar_uyarilari_al()
    anahtarlar = " ".join(uyarilar)
    for k in ("otobitir", "stt_url", "stt_repetition_penalty", "esikler", "saklama_gun", "duzelt", "context"):
        assert f"'{k}'" in anahtarlar, k
    assert ayar_mod.ayar_uyarilari_al() == []                    # bosaltildi
    ayar_mod.ayar_oku()                                          # ayni dosya tekrar okunur: ayni uyari yeniden uretilmez
    assert ayar_mod.ayar_uyarilari_al() == []


def test_goc_kaynak_ikisi_altyazi_ses_ve_altyazi_yedek_sozluk_anahtarlari_atilir(ortam):
    (ortam / "config.json").write_text(json.dumps({
        "kaynak": "ikisi", "stt_url": "http://stt.ornek", "otomatik_not": True, "sozluk_yolu": "sozluk.json",
        "sozluk": {"x": 1}, "mikrofon_modu": "cift", "inceleme": False}), encoding="utf-8")
    v = ayar_mod.ayar_oku()
    assert v["kaynak"] == "ses" and v["altyazi_yedek"] is True and v["mikrofon_modu"] == "otomatik"
    for k in ("otomatik_not", "sozluk_yolu", "sozluk", "inceleme"):
        assert k not in v
    uyarilar = " ".join(ayar_mod.ayar_uyarilari_al())
    assert "göçü" in uyarilar and "otomatik_not" in uyarilar and "kaynak 'ikisi'" in uyarilar
    # dosyanin kendisi de duzeltildi (varsayilanlar eklenmeden), eskisi yedeklendi
    ham = json.loads((ortam / "config.json").read_text(encoding="utf-8"))
    assert ham == {"kaynak": "ses", "stt_url": "http://stt.ornek", "mikrofon_modu": "otomatik", "altyazi_yedek": True}
    assert json.loads((ortam / "config.json.yedek-goc").read_text(encoding="utf-8"))["kaynak"] == "ikisi"
    ayar_mod.uyarilari_sifirla()
    ayar_mod.ayar_oku()
    assert ayar_mod.ayar_uyarilari_al() == []                    # artik goc edilecek bir sey yok


def test_goc_altyazi_acikca_yazilan_altyazi_yedek_degerini_ezmez(ortam):
    (ortam / "config.json").write_text(json.dumps({"kaynak": "altyazi", "altyazi_yedek": False}), encoding="utf-8")
    v = ayar_mod.ayar_oku()
    assert v["kaynak"] == "ses" and v["altyazi_yedek"] is False


def test_goc_yazilamazsa_okuma_yine_calisir(ortam, monkeypatch):
    (ortam / "config.json").write_text(json.dumps({"kaynak": "ikisi"}), encoding="utf-8")
    monkeypatch.setattr(ayar_mod, "ayar_yaz", lambda v: (_ for _ in ()).throw(OSError("uydurma salt okunur")))
    v = ayar_mod.ayar_oku()
    assert v["kaynak"] == "ses" and any("dosyaya yazılamadı" in u for u in ayar_mod.ayar_uyarilari_al())


# =============================================================== hata.log yolu

def test_hata_log_localappdata_klasorune_yazilir_yoksa_uygulama_klasorune(ortam, monkeypatch):
    import isler
    assert ayar_mod.hata_log_yolu() == "hata.log"                # LOCALAPPDATA yok: uygulama klasoru
    isler.hata_logla("birinci")
    assert "birinci" in (ortam / "hata.log").read_text(encoding="utf-8")
    yerel = ortam / "yerel"
    monkeypatch.setenv("LOCALAPPDATA", str(yerel))
    yol = ayar_mod.hata_log_yolu()
    assert os.path.normpath(yol) == os.path.normpath(str(yerel / "BriefMind" / "hata.log"))
    isler.hata_logla("ikinci")
    assert "ikinci" in (yerel / "BriefMind" / "hata.log").read_text(encoding="utf-8")
    assert "ikinci" not in (ortam / "hata.log").read_text(encoding="utf-8")


def test_hata_log_localappdata_yazilamazsa_uygulama_klasorune_duser_ve_asla_firlatmaz(ortam, monkeypatch):
    dosya = ortam / "dosya"
    dosya.write_text("x", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(dosya))               # klasor olusturulamaz (bir dosya)
    assert ayar_mod.hata_log_yolu() == "hata.log"
    assert ayar_mod.hata_yaz("kurtarildi") == "hata.log"
    assert "kurtarildi" in (ortam / "hata.log").read_text(encoding="utf-8")
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk dolu")))
    assert ayar_mod.hata_yaz("hicbir yere yazilamaz") is None


def test_hata_log_boyut_tavani_asilinca_doner(ortam, monkeypatch):
    monkeypatch.setattr(ayar_mod, "LOG_BOYUT_TAVAN", 50)
    (ortam / "hata.log").write_text("x" * 100, encoding="utf-8")
    ayar_mod.hata_yaz("yeni")
    assert (ortam / "hata.log.1").read_text(encoding="utf-8") == "x" * 100
    assert (ortam / "hata.log").read_text(encoding="utf-8") == "yeni"


# =============================================================== Outlook COM arka plan thread'i (sahte COM)

class SahtePythoncom:
    def __init__(self):
        self.olaylar = []

    def CoInitialize(self):
        self.olaylar.append(("init", threading.get_ident()))

    def CoUninitialize(self):
        self.olaylar.append(("uninit", threading.get_ident()))


class SahteTarih:
    def __init__(self, d):
        self.year, self.month, self.day, self.hour, self.minute = d.year, d.month, d.day, d.hour, d.minute


class SahteKayit:
    Subject = "Haftalık Mimari"
    Organizer = "Ayşe Demir"
    RequiredAttendees = "Kisi B; Kisi C"
    OptionalAttendees = ""
    Body = "Gündem: Kampanya Modülü"
    Attachments = []

    def __init__(self):
        import datetime as dt
        simdi = dt.datetime.now()
        self.Start = SahteTarih(simdi - dt.timedelta(minutes=5))
        self.End = SahteTarih(simdi + dt.timedelta(minutes=25))


class SahteKayitlar(list):
    @property
    def Count(self):
        return len(self)

    def Restrict(self, filtre):
        return self

    def Sort(self, alan):
        pass

    IncludeRecurrences = True


class SahteOutlook:
    def __init__(self, bekle=None):
        self.bekle, self.thread = bekle, []

    def GetNamespace(self, ad):
        self.thread.append(threading.get_ident())
        if self.bekle is not None:
            self.bekle.wait(10)                                  # COM cagrisi uzun surer (Outlook acilisi)
        class Ns:
            def GetDefaultFolder(self, n):
                return types_ns(Items=SahteKayitlar([SahteKayit()]))
        return Ns()


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


def test_outlook_com_cagrisi_kendi_threadinde_coinitialize_ile_ve_cifte_eslesir(ortam, monkeypatch, qapp):
    import isler
    com = SahtePythoncom()
    ol = SahteOutlook()
    monkeypatch.setattr(outlook, "_pythoncom", lambda: com)
    monkeypatch.setattr(outlook, "_outlook", lambda: ol)
    sonuc = []
    isi = isler.ArkaIs(outlook.toplanti_esle, "Haftalık Mimari", ad="test")
    isi.bitti.connect(sonuc.append)
    isi.start()
    assert _bekle(qapp, lambda: bool(sonuc))
    secilen, adaylar, skor = sonuc[0]
    assert secilen is not None and secilen["baslik"] == "Haftalık Mimari" and len(adaylar) == 1
    ana = threading.get_ident()
    assert ol.thread and ol.thread[0] != ana                       # COM arayuz thread'inde calismadi
    inits = [t for o, t in com.olaylar if o == "init"]
    uninits = [t for o, t in com.olaylar if o == "uninit"]
    assert inits and sorted(inits) == sorted(uninits)              # her CoInitialize'in eslesen CoUninitialize'i
    assert set(inits) == {ol.thread[0]}                            # COM'u kullanan thread'de baslatildi
    isi.wait(3000)


def test_outlook_com_hata_verirse_sonuc_bos_gelir_ve_com_yine_kapanir(ortam, monkeypatch, qapp):
    import isler
    com = SahtePythoncom()
    monkeypatch.setattr(outlook, "_pythoncom", lambda: com)
    monkeypatch.setattr(outlook, "_outlook", lambda: (_ for _ in ()).throw(OSError("uydurma: Outlook yok")))
    sonuc = []
    isi = isler.ArkaIs(isler.takvim_sorgula, uia=True)
    isi.bitti.connect(sonuc.append)
    isi.start()
    assert _bekle(qapp, lambda: bool(sonuc))
    assert sonuc[0]["adaylar"] == [] and sonuc[0]["secilen"] is None
    assert [o for o, _ in com.olaylar].count("init") == [o for o, _ in com.olaylar].count("uninit") >= 1
    isi.wait(3000)


def test_arka_is_istisnasi_hata_sinyali_ve_hata_logu(ortam, qapp):
    import isler

    def patla():
        raise RuntimeError("uydurma arka is hatasi")
    hatalar = []
    isi = isler.ArkaIs(patla)
    isi.hata.connect(hatalar.append)
    isi.start()
    assert _bekle(qapp, lambda: bool(hatalar))
    assert "uydurma arka is hatasi" in hatalar[0] and "uydurma arka is hatasi" in (ortam / "hata.log").read_text(encoding="utf-8")
    isi.wait(3000)


# =============================================================== pencere: sahte iscilerle durum makinesi

class Kapi:
    """Sahte iscilerin serbest birakilmasi (test kontrol eder). hepsini_birak sonrasi acilan olaylar da serbesttir."""
    def __init__(self):
        self.olaylar = {}
        self.serbest = False

    def olay(self, ad):
        if ad not in self.olaylar:
            self.olaylar[ad] = threading.Event()
            if self.serbest:
                self.olaylar[ad].set()
        return self.olaylar[ad]

    def birak(self, ad):
        self.olay(ad).set()

    def hepsini_birak(self):
        self.serbest = True
        for e in list(self.olaylar.values()):
            e.set()

    def sifirla(self):
        self.hepsini_birak()                                     # onceki testten kalan iscileri serbest birak
        self.olaylar.clear()
        self.serbest = False


KAPI = Kapi()


class SahteMotor:
    def __init__(self, klasor):
        self.klasor, self.baslik, self.parca_no, self.isler = klasor, "Deneme", 0, []
        self.tarih = __import__("datetime").date(2026, 10, 5)
        self.katilimcilar, self.ben, self.sablon = [], "Kisi", "genel"

    def belirsizler(self):
        return []


class SahteYakalamaIsi(QtCore.QThread):
    """Gercek YakalamaIsi sozlesmesi: durdur(hizli) -> run biter; toplanti_bitti run SONUNDA (thread bitmeden) yayilir."""
    olay = QtCore.pyqtSignal(str, object)
    durum = QtCore.pyqtSignal(str)
    toplanti_bitti = QtCore.pyqtSignal(list)
    hata = QtCore.pyqtSignal(str)
    olusturulan = []

    def __init__(self, baslik, ctx, terimler, duzelt, otobitir, ayar=None):
        super().__init__()
        self.baslik, self.motor = baslik, None
        self.durdurma = []
        self._dur = KAPI.olay(f"dur-{id(self)}")                # KAPI.hepsini_birak() ile serbest kalir
        self.bitis_sirasinda_calisiyor = None
        SahteYakalamaIsi.olusturulan.append(self)

    def durdur(self, hizli=False):
        self.durdurma.append(hizli)
        self._dur.set()

    def run(self):
        self._dur.wait(30)
        KAPI.olay("yakalama-kapanis").wait(30)                   # test 'kapanis suruyor' ani yakalasin
        self.toplanti_bitti.emit([{"tur": "kelime"}])


class SahteTamamlama(QtCore.QThread):
    olay = QtCore.pyqtSignal(str, object)
    bitti = QtCore.pyqtSignal(str)
    hata = QtCore.pyqtSignal(str)

    def __init__(self, m):
        super().__init__()

    def run(self):
        KAPI.olay("not").wait(30)
        self.bitti.emit("## Not\n\n- madde")


class SahteYeniden(QtCore.QThread):
    """Yeniden ozetleme: hata sinyalini verir ama thread'i serbest birakilana kadar canli tutar (eski hata: Baslat
    hata sinyalinde aciliyordu)."""
    olay = QtCore.pyqtSignal(str, object)
    durum = QtCore.pyqtSignal(str)
    bitti = QtCore.pyqtSignal(str)
    hata = QtCore.pyqtSignal(str)
    hatali = False

    def __init__(self, klasor, terimler, duzelt=True, ben=None, sablon="genel"):
        super().__init__()
        self.motor = SahteMotor(klasor)

    def run(self):
        KAPI.olay("yeniden-sinyal").wait(30)
        if SahteYeniden.hatali:
            self.hata.emit("uydurma yeniden ozetleme hatasi")
        else:
            self.bitti.emit("## Not\n\n- yeniden")
        KAPI.olay("yeniden").wait(30)


class SahteSoru(QtCore.QThread):
    bitti = QtCore.pyqtSignal(str)

    def __init__(self, m, soru):
        super().__init__()

    def run(self):
        KAPI.olay("soru").wait(30)
        self.bitti.emit("Cevap")


@pytest.fixture
def pencere(ortam, monkeypatch, qapp):
    import app
    KAPI.sifirla()
    SahteYakalamaIsi.olusturulan = []
    SahteYeniden.hatali = False
    monkeypatch.setattr(app.terim_mod, "uygulama_klasoru", lambda: str(ortam))
    monkeypatch.setattr(app.Pencere, "toplantilari_yenile", lambda self, elle=False: None)
    monkeypatch.setattr(app.Pencere, "saklama_uygula", lambda self: None)
    monkeypatch.setattr(app.Pencere, "llm_tani", lambda self, test=False: None)
    monkeypatch.setattr(app.Pencere, "kesinti_devam", lambda self: None)
    monkeypatch.setattr(app, "YakalamaIsi", SahteYakalamaIsi)
    monkeypatch.setattr(app, "TamamlamaIsi", SahteTamamlama)
    monkeypatch.setattr(app, "YenidenOzetlemeIsi", SahteYeniden)
    monkeypatch.setattr(app, "SoruIsi", SahteSoru)
    monkeypatch.setattr(app.outlook, "kullanici_adi", lambda: "Deneme Kisi")
    bilgi, uyari, soru = [], [], []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: uyari.append(a))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: bilgi.append(a))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: (soru.append(a), QtWidgets.QMessageBox.Yes)[1])
    p = app.Pencere()
    p.a_outlook.setChecked(False)
    p.bilgi, p.uyari, p.soru_kutu = bilgi, uyari, soru
    p.klasor_yolu = str(ortam / "kayit")
    p.motor = SahteMotor(p.klasor_yolu)
    os.makedirs(p.klasor_yolu, exist_ok=True)
    p._not_uret_aday = True
    _bekle(qapp, lambda: not p._arka_isler)                  # acilistaki kullanici adi sorgusu bitsin
    p._durum_uygula()
    yield p
    KAPI.hepsini_birak()
    for ad in ("yakalama-kapanis", "not", "yeniden-sinyal", "yeniden", "soru"):
        KAPI.birak(ad)
    for i in SahteYakalamaIsi.olusturulan:
        i._dur.set()
    for i in list(SahteYakalamaIsi.olusturulan) + [p.is_, p.tamamla, p.yeniden, p._soru_isi, p.kesinti_isi]:
        if i is not None:
            i.wait(5000)
    _guvenli_kapat(qapp, p)


def _butonlar(p):
    return {"baslat_bitir": p.b_ana.isEnabled(), "ana_yazi": p.b_ana.text(), "yeniden": p.b_g_yeniden.isEnabled(),
            "parca": p.b_g_parca.isEnabled(), "uygula": p.b_not_uret.isEnabled(), "sor": p.b_sor.isEnabled(),
            "kaydet": p.b_kaydet.isEnabled()}


def _baslat(p, qapp):
    p.toplanti_secici.setEditText("Deneme Toplantisi")
    p.a_ben.setText("Deneme Kisi")
    p.baslat()
    assert len(SahteYakalamaIsi.olusturulan) >= 1


def test_izin_tablosu_bes_durumu_kapsar():
    import app
    assert set(app.IZINLER) == set(app.DURUMLAR) == {"bos", "yakalaniyor", "not_uretiliyor", "yeniden_ozetleniyor",
                                                    "soru"}
    for iz in app.IZINLER.values():
        assert set(iz) == {"baslat", "bitir", "yeniden", "uygula", "sor", "kaydet"}


def test_durum_bos_dugmeler(pencere):
    p = pencere
    assert p._durum() == "bos"
    b = _butonlar(p)
    assert b == {"baslat_bitir": True, "ana_yazi": "Başlat", "yeniden": True, "parca": True, "uygula": True, "sor": True,
                 "kaydet": True}


def test_gecis_bos_yakalaniyor_bitiriliyor_not_uretiliyor_bos(pencere, qapp):
    p = pencere
    _baslat(p, qapp)
    assert p._durum() == "yakalaniyor"
    b = _butonlar(p)
    assert b["ana_yazi"] == "Bitir" and b["baslat_bitir"] is True            # yakalamada Bitir acik
    assert not b["yeniden"] and not b["parca"] and not b["uygula"] and not b["sor"] and b["kaydet"]
    adet = len(SahteYakalamaIsi.olusturulan)
    p.baslat()                                                               # cift Baslat: engellendi
    assert len(SahteYakalamaIsi.olusturulan) == adet
    p.is_.motor = SahteMotor(p.klasor_yolu)                            # yakalama motoru
    p.bitir()
    assert p._durum() == "yakalaniyor" and p.b_ana.isEnabled() is False      # Bitir'e basildi: tekrar Bitir yok
    assert SahteYakalamaIsi.olusturulan[0].durdurma == [False]               # normal (hizli degil) durdurma
    # thread bitis sinyalini verecek ama HENUZ calisiyor: toplanti_bitti islenmemeli
    cagrilar = []
    gercek = p.toplanti_bitti
    p.toplanti_bitti = lambda b, kaynak="canli": cagrilar.append((b, p.is_.isRunning())) or gercek(b, kaynak)
    KAPI.birak("yakalama-kapanis")
    assert _bekle(qapp, lambda: bool(cagrilar))
    assert cagrilar[0][0] == [{"tur": "kelime"}] and cagrilar[0][1] is False   # thread GERCEKTEN bittikten sonra
    assert p._durum() == "not_uretiliyor" and p.tamamla is not None
    b = _butonlar(p)
    assert not b["baslat_bitir"] and b["ana_yazi"] == "Başlat" and not b["yeniden"] and not b["uygula"] \
        and not b["sor"] and not b["kaydet"]
    adet = len(SahteYakalamaIsi.olusturulan)
    p.baslat()
    p.ana_dugme()
    assert len(SahteYakalamaIsi.olusturulan) == adet                          # not uretilirken Baslat acilmaz
    KAPI.birak("not")
    assert _bekle(qapp, lambda: p._durum() == "bos")
    assert p.b_ana.isEnabled() and p.not_md.startswith("## Not") and p._not_kilit is False
    assert p.b_not_uret.isEnabled()                                          # yeniden uretilebilir


def test_toplanti_bitti_sinyali_thread_bitmeden_notu_baslatmaz(pencere, qapp):
    p = pencere
    _baslat(p, qapp)
    p.is_.motor = SahteMotor(p.klasor_yolu)
    p.bitir()
    SahteYakalamaIsi.olusturulan[0]._dur.set()
    # sinyal yayildi ama finished'den once islenemez: _bitis_sinyali yalniz saklar
    p._bitis_sinyali([{"tur": "kelime"}])
    assert p.tamamla is None and p._durum() == "yakalaniyor"
    KAPI.birak("yakalama-kapanis")
    assert _bekle(qapp, lambda: p.tamamla is not None)


def test_yakalama_motorsuz_biterse_yeniden_baslatilabilir(pencere, qapp):
    p = pencere
    _baslat(p, qapp)
    KAPI.birak("yakalama-kapanis")
    p.is_._dur.set()
    assert _bekle(qapp, lambda: p._durum() == "bos")                          # motor=None: not uretilmez, Baslat acik
    assert p.b_ana.isEnabled() and p.tamamla is None


def test_yeniden_ozetleme_surerken_baslat_kapali_hata_sinyalinde_bile(pencere, qapp):
    p = pencere
    SahteYeniden.hatali = True
    k = os.path.join("toplantilar", "2026-10-05_eski")
    os.makedirs(k)
    with open(os.path.join(k, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"baslik": "Eski", "tarih": "2026-10-05", "durum": "tamam", "katilimcilar": [], "parca": 0}, f)
    p.gecmis_yenile()
    p.g_liste.setCurrentRow(0)
    p.gecmis_yeniden_ozetle()
    assert p._durum() == "yeniden_ozetleniyor" and p._yeniden_kilit
    b = _butonlar(p)
    assert not b["baslat_bitir"] and not b["yeniden"] and not b["uygula"] and not b["sor"] and not b["kaydet"]
    adet = len(SahteYakalamaIsi.olusturulan)
    p.baslat()
    assert len(SahteYakalamaIsi.olusturulan) == adet
    KAPI.birak("yeniden-sinyal")                                             # thread hata sinyali verdi ama CANLI
    assert _bekle(qapp, lambda: len(p.uyari) == 1)
    assert p.yeniden.isRunning() and p.b_ana.isEnabled() is False            # eski hata: burada Baslat aciliyordu
    p.baslat()
    assert len(SahteYakalamaIsi.olusturulan) == adet
    KAPI.birak("yeniden")                                                    # thread gercekten bitti
    assert _bekle(qapp, lambda: p.b_ana.isEnabled())
    assert p._yeniden_kilit is False and p._durum() == "bos"
    assert not p.yeniden.isRunning()


def test_yeniden_ozetleme_basarili_not_uretir_ve_thread_bitince_acar(pencere, qapp):
    p = pencere
    k = os.path.join("toplantilar", "2026-10-05_eski")
    os.makedirs(k)
    with open(os.path.join(k, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"baslik": "Eski", "tarih": "2026-10-05", "durum": "tamam", "katilimcilar": [], "parca": 0}, f)
    p.gecmis_yenile()
    p.g_liste.setCurrentRow(0)
    p.gecmis_yeniden_ozetle()
    KAPI.birak("yeniden-sinyal")
    assert _bekle(qapp, lambda: "yeniden" in p.not_goster.toPlainText())
    assert p.b_ana.isEnabled() is False                                      # not goruldu ama thread suruyor
    KAPI.birak("yeniden")
    assert _bekle(qapp, lambda: p.b_ana.isEnabled())
    assert p.b_not_uret.isEnabled()


def test_yeniden_ozetleme_baska_is_surerken_baslamaz(pencere, qapp):
    p = pencere
    _baslat(p, qapp)
    p.g_liste.clear()
    p.gecmis_yeniden_ozetle()
    assert p.yeniden is None                                                 # secili kayit yok / yakalama suruyor
    p.gecmis_not()
    p.parca_yeniden()
    assert p._yeniden_kilit is False


def test_soru_durumu_baslat_acik_digerleri_kapali_soru_bitince_bos(pencere, qapp):
    p = pencere
    p.soru.setText("Ne karar verildi?")
    p.soru_sor()
    assert p._durum() == "soru"
    b = _butonlar(p)
    assert b["baslat_bitir"] and b["ana_yazi"] == "Başlat" and not b["yeniden"] and not b["sor"] and not b["uygula"]
    p.soru_sor()                                                             # soru surerken ikinci soru yok
    KAPI.birak("soru")
    assert _bekle(qapp, lambda: p._durum() == "bos")
    assert "Cevap" in p.cevap.toPlainText() and p.b_sor.isEnabled()


def test_yakalama_surerken_ayarlar_kaydedilir_not_uretilirken_kaydedilmez(pencere, qapp, ortam):
    p = pencere
    _baslat(p, qapp)
    assert p.b_kaydet.isEnabled()
    p.is_.motor = SahteMotor(p.klasor_yolu)
    p.bitir()
    KAPI.birak("yakalama-kapanis")
    assert _bekle(qapp, lambda: p._durum() == "not_uretiliyor")
    assert not p.b_kaydet.isEnabled()
    p.ayar_kaydet()                                                          # elle cagrilsa da reddedilir
    assert len(p.bilgi) == 1 and not (ortam / "config.json").exists()
    KAPI.birak("not")
    assert _bekle(qapp, lambda: p._durum() == "bos")
    p.ayar_kaydet()
    assert (ortam / "config.json").exists()
    assert sorted(f for f in os.listdir(ortam) if f.startswith(".config")) == []      # atomik yazim artigi yok


# =============================================================== takvim / Outlook / STT testi arayuz thread'ini bloklamaz

def test_takvim_sorgusu_arka_planda_arayuz_donmaz(ortam, monkeypatch, qapp):
    import app
    monkeypatch.setattr(app.terim_mod, "uygulama_klasoru", lambda: str(ortam))
    monkeypatch.setattr(app.Pencere, "saklama_uygula", lambda self: None)
    monkeypatch.setattr(app.Pencere, "llm_tani", lambda self, test=False: None)
    monkeypatch.setattr(app.Pencere, "kesinti_devam", lambda self: None)
    monkeypatch.setattr(app.outlook, "kullanici_adi", lambda: "Deneme Kisi")
    import datetime as dt
    kapi, thread = threading.Event(), []

    def yavas_takvim():
        thread.append(threading.get_ident())
        kapi.wait(10)
        bas = dt.datetime.now()
        a = {"baslik": "Haftalık Mimari", "katilimcilar": ["Ayşe Demir"], "gundem": "", "baslangic": bas,
             "bitis": bas + dt.timedelta(minutes=30), "davet": {"govde": "", "ekler": []}}
        return {"teams_adi": "", "secilen": a, "adaylar": [a], "skor": 0.9}
    monkeypatch.setattr(app, "takvim_sorgula", yavas_takvim)
    p = app.Pencere()
    try:
        t0 = time.time()
        p.toplantilari_yenile(True)
        assert time.time() - t0 < 1.0                                        # COM/UIA beklenmedi
        qapp.processEvents()
        assert p.toplanti_secici.count() == 0                                # sonuc henuz yok, arayuz yanit veriyor
        kapi.set()
        assert _bekle(qapp, lambda: p.toplanti_secici.count() == 1)
        assert thread and thread[0] != threading.get_ident()
        assert p.toplanti_secici.currentText() == "Haftalık Mimari"
    finally:
        kapi.set()
        if p._takvim_isi is not None:
            p._takvim_isi.wait(5000)
        _guvenli_kapat(qapp, p)


def test_baslat_toplanti_adi_yoksa_takvimi_arka_planda_bekler_ve_devam_eder(pencere, qapp, monkeypatch):
    import app
    import datetime as dt
    p = pencere
    kapi, thread = threading.Event(), []
    bas = dt.datetime.now()
    a = {"baslik": "Haftalık Mimari", "katilimcilar": ["Ayşe Demir"], "gundem": "", "baslangic": bas,
         "bitis": bas + dt.timedelta(minutes=30), "davet": {"govde": "", "ekler": []}}

    def takvim():
        thread.append(threading.get_ident())
        kapi.wait(10)
        return {"teams_adi": "Haftalık Mimari", "secilen": a, "adaylar": [a], "skor": 0.9}
    monkeypatch.setattr(app, "takvim_sorgula", takvim)
    p.toplanti_secici.setEditText("")
    t0 = time.time()
    p.baslat()
    assert time.time() - t0 < 1.0 and p._baslat_bekliyor and p._durum() == "yakalaniyor"
    assert p.b_ana.isEnabled() is False and not SahteYakalamaIsi.olusturulan  # takvim gelene kadar yakalama yok
    p.baslat()                                                                # ikinci tiklama etkisiz
    kapi.set()
    assert _bekle(qapp, lambda: len(SahteYakalamaIsi.olusturulan) == 1)
    assert SahteYakalamaIsi.olusturulan[0].baslik == "Haftalık Mimari" and thread[0] != threading.get_ident()
    assert p._baslat_bekliyor is False and p._durum() == "yakalaniyor"
    if p._takvim_isi is not None:
        p._takvim_isi.wait(3000)


def test_baslat_takvim_sorgusu_takilirsa_zaman_asimiyla_devam_eder(pencere, qapp, monkeypatch):
    import app
    p = pencere
    kapi = threading.Event()
    monkeypatch.setattr(app, "takvim_sorgula", lambda: (kapi.wait(10), {})[1])
    monkeypatch.setattr(p, "TAKVIM_ZAMAN_ASIMI_MS", 100)
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Elle Ad", True)))
    p.toplanti_secici.setEditText("")
    p.baslat()
    assert _bekle(qapp, lambda: len(SahteYakalamaIsi.olusturulan) == 1)
    assert SahteYakalamaIsi.olusturulan[0].baslik == "Elle Ad"
    kapi.set()
    if p._takvim_isi is not None:
        p._takvim_isi.wait(3000)


def test_stt_testi_arka_planda_calisir(pencere, qapp, monkeypatch):
    import app
    p = pencere
    kapi, thread = threading.Event(), []

    def saglik(adres, model, anahtar, verify):
        thread.append(threading.get_ident())
        kapi.wait(10)
        return True, None
    monkeypatch.setattr(app, "stt_saglik_testi", saglik)
    t0 = time.time()
    p.stt_test()
    assert time.time() - t0 < 1.0 and not p.b_stt_test.isEnabled()
    p.stt_test()                                                             # test surerken ikinci tiklama etkisiz
    kapi.set()
    assert _bekle(qapp, lambda: len(p.bilgi) == 1 and p.b_stt_test.isEnabled())
    assert thread and len(thread) == 1 and thread[0] != threading.get_ident()
    assert "ulaşılabilir" in p.bilgi[0][2]


def test_outlook_taslagi_arka_planda_acilir(pencere, qapp, monkeypatch):
    p = pencere
    kapi, thread, alinan = threading.Event(), [], []

    def taslak(konu, govde, alicilar=None):
        thread.append(threading.get_ident())
        kapi.wait(10)
        alinan.append((konu, alicilar))
        return True
    monkeypatch.setattr(p_outlook(), "taslak", taslak)
    p.not_md = "# Not"
    t0 = time.time()
    p.outlook_ac()
    assert time.time() - t0 < 1.0 and not alinan
    kapi.set()
    assert _bekle(qapp, lambda: bool(alinan))
    assert thread[0] != threading.get_ident() and alinan[0][0].startswith("Toplantı notu: Deneme")
    assert _bekle(qapp, lambda: not p._arka_isler)


def p_outlook():
    import app
    return app.outlook


def test_kullanici_adi_arka_planda_alinir_ben_alanina_yazilir(ortam, monkeypatch, qapp):
    import app
    monkeypatch.setattr(app.terim_mod, "uygulama_klasoru", lambda: str(ortam))
    monkeypatch.setattr(app.Pencere, "toplantilari_yenile", lambda self, elle=False: None)
    monkeypatch.setattr(app.Pencere, "saklama_uygula", lambda self: None)
    monkeypatch.setattr(app.Pencere, "llm_tani", lambda self, test=False: None)
    monkeypatch.setattr(app.Pencere, "kesinti_devam", lambda self: None)
    kapi, thread = threading.Event(), []

    def ad():
        thread.append(threading.get_ident())
        kapi.wait(10)
        return "Ayşe Demir"
    monkeypatch.setattr(app.outlook, "kullanici_adi", ad)
    t0 = time.time()
    p = app.Pencere()
    try:
        assert time.time() - t0 < 5.0 and p.a_ben.text() == ""               # kurulum Outlook'u beklemedi
        kapi.set()
        assert _bekle(qapp, lambda: p.a_ben.text() == "Ayşe Demir")
        assert thread[0] != threading.get_ident()
    finally:
        kapi.set()
        _bekle(qapp, lambda: not p._arka_isler)
        _guvenli_kapat(qapp, p)


# =============================================================== kapanis (closeEvent)

def test_kapanis_yakalama_suruyorsa_hizli_durdurur_finished_bekler_not_baslatmaz(pencere, qapp):
    p = pencere
    _baslat(p, qapp)
    isi = p.is_
    p.close()                                                                # closeEvent: onay (Yes) -> iscileri durdur
    assert isi.durdurma == [True]                                            # hizli=True: acik parca <=30 sn'de diske
    assert p._kapaniyor and not p._kapanis_tamam                             # pencere iscinin finished'ini bekliyor
    assert len(p.soru_kutu) == 1
    assert p.b_ana.isEnabled() is False and not p.b_kaydet.isEnabled()      # kapanirken hicbir eylem acik degil
    qapp.processEvents()
    assert p._kapanis_tamam is False                                         # thread hala calisiyor: kapanmadi
    KAPI.birak("yakalama-kapanis")                                           # motor.bitir bitti, thread sonlandi
    assert _bekle(qapp, lambda: p._kapanis_tamam)
    assert not isi.isRunning()
    assert p.tamamla is None                                                 # kapanista not uretimi baslatilmadi
    assert p.yetim_isler == []


def test_kapanis_hayir_denirse_hicbir_sey_durmaz(pencere, qapp, monkeypatch):
    p = pencere
    _baslat(p, qapp)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.No)
    p.close()
    assert p.is_.durdurma == [] and not p._kapaniyor and not p._kapanis_tamam
    assert p.b_ana.isEnabled()                                               # Bitir hala acik


def test_kapanis_not_uretilirken_biteni_bekler_yarida_kesmez(pencere, qapp):
    p = pencere
    p._not_uret()
    assert p._durum() == "not_uretiliyor"
    isi = p.tamamla
    p.close()
    assert len(p.soru_kutu) == 1 and p._kapaniyor and not p._kapanis_tamam
    for _ in range(20):
        qapp.processEvents()
        time.sleep(0.01)
    assert isi.isRunning() and not p._kapanis_tamam                          # LLM cagrisi yarida kesilmedi
    KAPI.birak("not")
    assert _bekle(qapp, lambda: p._kapanis_tamam)
    assert not isi.isRunning() and p.not_md.startswith("## Not")


def test_kapanis_ikinci_kapatma_istegi_yok_sayilir(pencere, qapp):
    p = pencere
    p._not_uret()
    p.close()
    p.close()
    assert len(p.soru_kutu) == 1 and p._kapaniyor
    KAPI.birak("not")
    assert _bekle(qapp, lambda: p._kapanis_tamam)


def test_kapanis_siniri_dolarsa_loglar_yetim_iscileri_isaretler(pencere, qapp, monkeypatch, ortam):
    import app
    p = pencere
    monkeypatch.setattr(app, "KAPANIS_YAKALAMA_SN", 0.3)
    _baslat(p, qapp)
    p.close()
    assert p._kapaniyor
    assert _bekle(qapp, lambda: p._kapanis_tamam, sn=5)
    assert len(p.yetim_isler) == 1 and p.yetim_isler[0] is p.is_
    assert "Kapanış sınırı doldu" in (ortam / "hata.log").read_text(encoding="utf-8")
    KAPI.birak("yakalama-kapanis")
    p.is_.wait(5000)


def test_kapanis_calisan_is_yoksa_hemen_kapanir(pencere, qapp):
    p = pencere
    assert _bekle(qapp, lambda: not p._arka_isler)                           # acilistaki kullanici adi sorgusu bitsin
    p.close()
    assert p._kapanis_tamam and p.soru_kutu == []


def test_kapanis_kisa_isleri_sormadan_bekler(pencere, qapp):
    import isler
    p = pencere
    kapi = threading.Event()
    p._arka(kapi.wait, 10)
    p.close()
    assert p._kapaniyor and p.soru_kutu == []
    kapi.set()
    assert _bekle(qapp, lambda: p._kapanis_tamam)
    assert isler is not None


# =============================================================== gercek YakalamaIsi: hizli kapanista acik parca <=30 sn'de diske

class DiskMotor:
    """motor.bitir: acik parcayi HEMEN diske yazar, sonra LLM bekler gibi uzun bloklanir (gercek Motor.bitir sirasi)."""
    def __init__(self, klasor, baslik, tarih, terimler, katilimcilar, gundem, **kw):
        self.klasor, self.kaynak, self.parca_no, self.mevcut_tok = klasor, "ses", 0, 0
        self.seri, self.katilimcilar, self.kulaklik = "x", [], None
        self.ben, self.sablon, self.ses_olay_sayisi, self.yeniden_kes_fn = None, "genel", 0, None
        self.terimler = SahteTerimler()
        self.bitir_basladi = threading.Event()
        DiskMotor.son = self

    def terimleri_hazirla(self):
        return 0

    def altyazi_satiri(self, s):
        pass

    def ses_olayi(self, o):
        pass

    def okuma_ekle(self, o):
        pass

    def stt_durumu_ayarla(self, d):
        pass

    def kontrol(self):
        pass

    def belirsizler(self):
        return []

    def bitir(self):
        self.bitir_basladi.set()
        os.makedirs(os.path.join(self.klasor, "parcalar"), exist_ok=True)
        with open(os.path.join(self.klasor, "parcalar", "parca_001.json"), "w", encoding="utf-8") as f:
            json.dump({"sira": 1, "satirlar": [{"id": 1, "text": "açık parça"}]}, f)
        KAPI.olay("llm").wait(60)                                            # LLM ozetini bekler (bitiste cf.wait)
        return [{"tur": "kelime"}]


class SahteTerimler:
    def prompt(self, seri, adlar=(), onceki=""):
        return ""


class SahteYakalayici:
    isi = None

    def __init__(self, katilimcilar=(), okuma_fn=None):
        self.hazir = False

    def oku(self):
        return []

    def kayip_saniye(self):
        return 0

    def onerilen_aralik(self):
        return 0.01

    def bitir(self):
        return []


class YavasSes:
    """Durmasi dakikalar suren ses servisi (STT kuyrugu bosaltma): kapanis siniri bunu beklememeli."""
    def __init__(self, *a, **k):
        pass

    def baslat(self):
        return True

    def durdur(self):
        KAPI.olay("ses").wait(60)

    def yeniden_kes(self, no, an):
        return False
    stt_durumu = {"durum": "ok", "bas": None, "bekleyen": 0, "kayit": False, "eksik": [], "hata": None}


def test_yakalama_isi_hizli_kapanista_30_sn_sinirinda_acik_parca_diskte(ortam, qapp, monkeypatch):
    import isler
    import ses
    KAPI.sifirla()
    monkeypatch.setattr(isler, "Yakalayici", SahteYakalayici)
    monkeypatch.setattr(isler, "Motor", DiskMotor)
    monkeypatch.setattr(isler, "ekran_okuyucu", lambda acik: None)
    monkeypatch.setattr(isler.motor_mod, "yeni_klasor", lambda baslik, tarih: str(ortam / "kayit"))
    monkeypatch.setattr(ses, "SesServisi", YavasSes)
    monkeypatch.setattr(isler.YakalamaIsi, "KAPANIS_SN", 2.0)               # 30 sn yerine 2 sn (ayni mantik)
    isi = isler.YakalamaIsi("Deneme", {}, SahteTerimler(), False, 30, {"kaynak": "ses"})
    sonuc, loglar, bitenler = [], [], []
    isi.toplanti_bitti.connect(sonuc.append)
    isi.olay.connect(lambda t, v: loglar.append((t, v)))
    isi.finished.connect(lambda: bitenler.append(time.time()))
    isi.start()
    time.sleep(0.3)                                                          # yakalama dongusu calisiyor
    t0 = time.time()
    isi.durdur(hizli=True)
    assert _bekle(qapp, lambda: bool(bitenler), sn=10)                       # thread KAPANIS_SN icinde bitti
    gecen = bitenler[0] - t0
    assert gecen < 4.0, gecen                                                # ses 60 sn, LLM 60 sn bloklu iken
    assert os.path.exists(ortam / "kayit" / "parcalar" / "parca_001.json")   # acik parca diskte
    assert sonuc == [[]]                                                     # bitir sinirda donmedi: belirsizler yok
    metin = " ".join(str(v) for t, v in loglar if t == "log")
    assert "sn sınırında durmadı" in metin and "kapanış sınırı doldu" in metin
    KAPI.birak("ses")
    KAPI.birak("llm")
    time.sleep(0.2)


def test_yakalama_isi_normal_durdurmada_bitir_beklenir_ve_belirsizler_gelir(ortam, qapp, monkeypatch):
    import isler
    import ses
    KAPI.sifirla()
    KAPI.birak("llm")
    KAPI.birak("ses")
    monkeypatch.setattr(isler, "Yakalayici", SahteYakalayici)
    monkeypatch.setattr(isler, "Motor", DiskMotor)
    monkeypatch.setattr(isler, "ekran_okuyucu", lambda acik: None)
    monkeypatch.setattr(isler.motor_mod, "yeni_klasor", lambda baslik, tarih: str(ortam / "kayit"))
    monkeypatch.setattr(ses, "SesServisi", YavasSes)
    isi = isler.YakalamaIsi("Deneme", {}, SahteTerimler(), False, 30, {"kaynak": "ses"})
    sonuc = []
    isi.toplanti_bitti.connect(sonuc.append)
    isi.start()
    time.sleep(0.3)
    isi.durdur()
    assert isi.wait(10000)
    _bekle(qapp, lambda: bool(sonuc))
    assert sonuc == [[{"tur": "kelime"}]]


def test_stt_yok_ve_stt_serit_saat_simdi_kullanir(ortam, qapp, monkeypatch):
    """Zaman cizelgesi alanlari (stt_durum 'bas') duvar saati degil saat.simdi() (adima dayanikli)."""
    import isler
    geri = saat.sanal_kur(lambda: 7777.5)
    try:
        isi = isler.YakalamaIsi("Deneme", {}, SahteTerimler(), False, 30, {})
        gelen = []

        class M:
            def stt_durumu_ayarla(self, d):
                gelen.append(d)
        isi.motor = M()
        isi._stt_yok("uydurma")
        assert gelen[0]["bas"] == 7777.5
    finally:
        geri()


def test_stt_serit_bas_yoksa_saat_simdi_kullanir(pencere, monkeypatch):
    import motor as motor_mod
    geri = saat.sanal_kur(lambda: 1700000000.0)
    try:
        pencere.olay("stt_durum", {"durum": "kesinti", "bas": None, "eksik": [], "kayit": False})
        assert pencere.stt_serit.text().startswith(motor_mod.stt_eksik_metni([[1700000000.0, None]]))
    finally:
        geri()


# =============================================================== onceki oturumun kesinti kuyrugu

def _kesinti_klasoru(ortam, ad="2026-10-05_eski", n=2):
    k = ortam / "toplantilar" / ad
    (k / "olcum" / "kesinti").mkdir(parents=True)
    (k / "parcalar").mkdir()
    (k / "meta.json").write_text(json.dumps({"baslik": "Eski", "tarih": "2026-10-05", "durum": "devam",
                                             "katilimcilar": [], "parca": 0, "kaynak": "ses"}), encoding="utf-8")
    for i in range(1, n + 1):
        (k / "olcum" / "kesinti" / f"{i:06d}.json").write_text(json.dumps({"klip_no": i}), encoding="utf-8")
        (k / "olcum" / "kesinti" / f"{i:06d}.wav").write_bytes(b"RIFF")
    return k


class SahteSttIstemci:
    def __init__(self, saglikli=True):
        self.saglikli = saglikli

    def saglik(self, deneme=2):
        return self.saglikli


class SahteSes:
    """SesServisi'nin kesinti kuyrugu yuzeyi: kesinti_bosalt, duraklat (public API); _coz, _bekci_baslat dahili."""
    def __init__(self, klasor, saglikli=True):
        self.klasor = os.path.join(klasor, "olcum", "kesinti")
        self.stt = SahteSttIstemci(saglikli)
        self._dur = threading.Event()
        self._bekci = None
        self.cozulen = []
        self.kuyruk = self
        self._kesinti_kuyrugu = self

    def sayi(self):
        return len([f for f in os.listdir(self.klasor) if f.endswith(".json")])

    def _coz(self, p):
        self.cozulen.append(p["klip_no"])

    def duraklat(self):
        self._dur.set()

    def kesinti_bosalt(self, duraklatildi=None, kesinti_bekle_sn=None, kesinti_asildi=None, duraklat_hatasi=RuntimeError):
        orijinal = self._coz
        if duraklatildi is not None:
            def coz(p):
                if duraklatildi():
                    raise duraklat_hatasi("kesinti kuyruğu duraklatıldı")
                return orijinal(p)
            self._coz = coz
        self._bekci_baslat()
        self._bekci.join(10)
        return self.sayi()

    def _bekci_baslat(self):
        def dongu():
            for ad in sorted(os.listdir(self.klasor)):
                if not ad.endswith(".json"):
                    continue
                no = int(ad.split(".")[0])
                try:
                    self._coz({"klip_no": no})
                except Exception:
                    return                                                   # kesinti hatasi: klip diskte kalir
                os.remove(os.path.join(self.klasor, ad))
                os.remove(os.path.join(self.klasor, ad.replace(".json", ".wav")))
        self._bekci = threading.Thread(target=dongu, daemon=True)
        self._bekci.start()


def test_kesinti_bekleyenleri_diskteki_kuyruklari_bulur(ortam):
    import isler
    assert isler.kesinti_bekleyenleri() == []
    k = _kesinti_klasoru(ortam)
    _kesinti_klasoru(ortam, "2026-10-06_bos", n=0)
    (ortam / "toplantilar" / "2026-10-07_metasiz" / "olcum" / "kesinti").mkdir(parents=True)
    bulunan = isler.kesinti_bekleyenleri()
    assert [(os.path.basename(a), n) for a, n in bulunan] == [(k.name, 2)]


def test_kesinti_devam_isi_klipleri_cozer_loglar_ve_motoru_bitirir(ortam, qapp):
    import isler
    import terim
    k = _kesinti_klasoru(ortam)
    sesler = []

    def fabrika(m, klasor):
        sesler.append(SahteSes(klasor))
        return sesler[-1]
    havuz = terim.TerimHavuzu(str(ortam / "terimler" / "terimler.json"))
    isi = isler.KesintiDevamIsi(isler.kesinti_bekleyenleri(), havuz, {"stt_url": "http://stt.ornek"}, duzelt=False,
                                servis_fabrikasi=fabrika)
    loglar, sonuc = [], []
    isi.olay.connect(lambda t, v: loglar.append(v) if t == "log" else None)
    isi.bitti.connect(lambda c, kalan: sonuc.append((c, kalan)))
    isi.start()
    assert isi.wait(15000)
    _bekle(qapp, lambda: bool(sonuc))
    assert sonuc == [(2, 0)] and sesler[0].cozulen == [1, 2]
    assert os.listdir(k / "olcum" / "kesinti") == []
    metin = " ".join(loglar)
    assert "2 ses klibi çözülüyor" in metin and "2 klip çözüldü" in metin and "Yeniden özetle" in metin
    assert json.loads((k / "meta.json").read_text(encoding="utf-8"))["durum"] == "bitti"


def test_kesinti_devam_isi_stt_yoksa_klipleri_diskte_birakir(ortam, qapp):
    import isler
    import terim
    k = _kesinti_klasoru(ortam)
    havuz = terim.TerimHavuzu(str(ortam / "terimler" / "terimler.json"))
    isi = isler.KesintiDevamIsi(isler.kesinti_bekleyenleri(), havuz, {"stt_url": "http://stt.ornek"}, duzelt=False,
                                servis_fabrikasi=lambda m, klasor: SahteSes(klasor, saglikli=False))
    loglar, sonuc = [], []
    isi.olay.connect(lambda t, v: loglar.append(v) if t == "log" else None)
    isi.bitti.connect(lambda c, kalan: sonuc.append((c, kalan)))
    isi.start()
    assert isi.wait(15000)
    _bekle(qapp, lambda: bool(sonuc))
    assert sonuc == [(0, 2)] and len(os.listdir(k / "olcum" / "kesinti")) == 4
    assert "STT şu an ulaşılamıyor" in " ".join(loglar)


def test_kesinti_devam_isi_duraklatilinca_klipler_diskte_kalir(ortam, qapp):
    import isler
    import terim
    k = _kesinti_klasoru(ortam)
    havuz = terim.TerimHavuzu(str(ortam / "terimler" / "terimler.json"))
    isi = isler.KesintiDevamIsi(isler.kesinti_bekleyenleri(), havuz, {}, duzelt=False,
                                servis_fabrikasi=lambda m, klasor: SahteSes(klasor))
    isi.durdur()                                                             # baslamadan once duraklatildi
    sonuc = []
    isi.bitti.connect(lambda c, kalan: sonuc.append((c, kalan)))
    isi.start()
    assert isi.wait(15000)
    _bekle(qapp, lambda: bool(sonuc))
    assert sonuc == [(0, 2)] and len(os.listdir(k / "olcum" / "kesinti")) == 4


def test_kesinti_devam_coz_sirasinda_duraklatma_klip_atmaz(ortam, qapp):
    """Gercek ses.py bekcisinin yolu: _coz duraklatinca firlattigi Duraklatildi kesinti hatasidir (klip silinmez)."""
    import isler
    import stt
    assert stt.kesinti_hatasi_mi(isler.Duraklatildi("x")) is True


def test_pencere_acilista_kesinti_kuyrugunu_otomatik_baslatir_ve_loglar(ortam, qapp, monkeypatch):
    import app
    _kesinti_klasoru(ortam)
    monkeypatch.setattr(app.terim_mod, "uygulama_klasoru", lambda: str(ortam))
    monkeypatch.setattr(app.Pencere, "toplantilari_yenile", lambda self, elle=False: None)
    monkeypatch.setattr(app.Pencere, "saklama_uygula", lambda self: None)
    monkeypatch.setattr(app.Pencere, "llm_tani", lambda self, test=False: None)
    monkeypatch.setattr(app.outlook, "kullanici_adi", lambda: "Deneme Kisi")
    kuruldu = []

    class SahteKesinti(QtCore.QThread):
        olay = QtCore.pyqtSignal(str, object)
        bitti = QtCore.pyqtSignal(int, int)

        def __init__(self, klasorler, terimler, ayar=None, duzelt=True):
            super().__init__()
            kuruldu.append((klasorler, dict(ayar)))
            self.durdurma = False

        def durdur(self):
            self.durdurma = True

        def run(self):
            self.olay.emit("log", "çözülüyor")
            self.olay.emit("satir", {"id": 1})                                # satir olaylari canli goruntuye karismaz
            KAPI.olay("kesinti").wait(10)
            self.bitti.emit(2, 0)
    monkeypatch.setattr(app, "KesintiDevamIsi", SahteKesinti)
    KAPI.sifirla()
    p = app.Pencere()
    p.ayar["stt_url"] = "http://stt.ornek"
    try:
        p.kesinti_devam()
        assert len(kuruldu) == 1 and kuruldu[0][0][0][1] == 2
        assert p._durum() == "yeniden_ozetleniyor"                           # kuyruk suruyor: Yeniden ozetle/Sor kapali
        assert p.b_g_yeniden.isEnabled() is False and p.b_ana.isEnabled() is True   # Baslat acik (duraklatir)
        assert _bekle(qapp, lambda: "çözülüyor" in p.gunluk.toPlainText())
        assert "önceki oturumdan 2 ses klibi" in p.gunluk.toPlainText() and p.akis.toPlainText() == ""
        # Baslat kuyrugu duraklatir, thread bitince baslar
        p.toplanti_secici.setEditText("Deneme Toplantisi")
        p.baslat()
        assert p.kesinti_isi.durdurma and p._baslat_bekliyor and p.b_ana.isEnabled() is False
        monkeypatch.setattr(app, "YakalamaIsi", SahteYakalamaIsi)
        SahteYakalamaIsi.olusturulan = []
        KAPI.birak("kesinti")
        assert _bekle(qapp, lambda: len(SahteYakalamaIsi.olusturulan) == 1)
        assert "2 klip çözüldü" in p.gunluk.toPlainText()
    finally:
        KAPI.hepsini_birak()
        for i in (p.kesinti_isi, p.is_):
            if i is not None:
                i.wait(5000)
        _guvenli_kapat(qapp, p)


def test_kesinti_devam_stt_url_yoksa_baslatmaz(ortam, qapp, monkeypatch):
    import app
    _kesinti_klasoru(ortam)
    monkeypatch.setattr(app.terim_mod, "uygulama_klasoru", lambda: str(ortam))
    monkeypatch.setattr(app.Pencere, "toplantilari_yenile", lambda self, elle=False: None)
    monkeypatch.setattr(app.Pencere, "saklama_uygula", lambda self: None)
    monkeypatch.setattr(app.Pencere, "llm_tani", lambda self, test=False: None)
    gercek_kesinti = app.Pencere.kesinti_devam
    monkeypatch.setattr(app.Pencere, "kesinti_devam", lambda self: None)       # acilis zamanlayicisi karismasin
    monkeypatch.setattr(app.outlook, "kullanici_adi", lambda: "Deneme Kisi")
    p = app.Pencere()
    try:
        p.ayar["stt_url"] = ""
        gercek_kesinti(p)
        assert p.kesinti_isi is None and "stt_url ayarlı değil" in p.gunluk.toPlainText()
        p.ayar["kesinti_devam"] = False
        p.gunluk.clear()
        gercek_kesinti(p)
        assert p.gunluk.toPlainText() == ""
    finally:
        _bekle(qapp, lambda: not p._arka_isler)
        _guvenli_kapat(qapp, p)


# =============================================================== toplanti.py + --duman

def test_toplanti_cli_ayar_uygula_llm_ve_rag_ayarlarini_verir(ortam, monkeypatch, capsys):
    import llm
    import rag
    import toplanti
    gelen = []
    monkeypatch.setattr(llm, "ayarla", lambda cfg: gelen.append(("llm", cfg.get("route"))))
    monkeypatch.setattr(rag, "ayarla", lambda cfg: gelen.append(("rag", cfg.get("embed_url"))))
    (ortam / "config.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"route": "http://llm.ornek:9000/v1", "embed_url": "http://emb.ornek", "kaynak": "ikisi"}).encode("utf-8"))
    toplanti.ayar_uygula(ayar_mod.ayar_oku())
    assert gelen == [("llm", "http://llm.ornek:9000/v1"), ("rag", "http://emb.ornek")]
    cikti = capsys.readouterr().out
    assert "göçü" in cikti
    gelen.clear()
    toplanti.ayar_uygula({"stt_url": ""})                                      # route yok: global ayarlara dokunulmaz
    assert gelen == []


def test_toplanti_cli_stt_yok_durumu_saat_simdi_kullanir(ortam, monkeypatch):
    import toplanti
    geri = saat.sanal_kur(lambda: 4242.0)
    try:
        gelen = []

        class M:
            yeniden_kes_fn = None

            def stt_durumu_ayarla(self, d):
                gelen.append(d)
        import ses as ses_mod
        monkeypatch.setattr(ses_mod, "SesServisi", lambda *a, **k: (_ for _ in ()).throw(OSError("uydurma")))
        assert toplanti.ses_servisi_kur(M(), {"stt_url": ""}, str(ortam / "k")) is None
        assert gelen[0]["bas"] == 4242.0 and gelen[0]["durum"] == "kesinti"
    finally:
        geri()


def test_duman_testi_pencereyi_kurar_ve_modulleri_yoklar(pencere, ortam):
    import app
    tamam, satirlar = app.duman_testi(pencere)
    assert tamam, "\n".join(satirlar)
    metin = "\n".join(satirlar)
    for modul in ("konusmaci", "stt", "saat", "terim", "duzelt", "kelime", "rag"):
        assert f"tamam  modül {modul}" in metin, modul
    assert "veri/yaygin_kelimeler.txt" in metin and "HATA" not in metin
    app._duman_yaz(["BriefMind duman testi: TAMAM"] + satirlar)
    assert "TAMAM" in (ortam / "duman.txt").read_text(encoding="utf-8")


def test_duman_testi_eksik_modulu_basarisizlik_sayar(pencere, monkeypatch):
    import app
    import importlib
    gercek = importlib.import_module

    def eksik(ad, *a, **k):
        if ad == "konusmaci":
            raise ImportError("uydurma: gizli import eksik")
        return gercek(ad, *a, **k)
    monkeypatch.setattr(importlib, "import_module", eksik)
    tamam, satirlar = app.duman_testi(pencere)
    assert tamam is False and any("HATA" in s and "konusmaci" in s for s in satirlar)


def test_duman_modunda_pencere_ag_com_ve_zamanlayici_baslatmaz(ortam, monkeypatch, qapp):
    import app
    monkeypatch.setattr(app.terim_mod, "uygulama_klasoru", lambda: str(ortam))
    cagrilar = []
    monkeypatch.setattr(app.Pencere, "llm_tani", lambda self, test=False: cagrilar.append("llm"))
    monkeypatch.setattr(app.outlook, "kullanici_adi", lambda: cagrilar.append("com") or "x")
    monkeypatch.setattr(app, "takvim_sorgula", lambda: cagrilar.append("takvim") or {})
    p = app.Pencere(duman=True)
    try:
        qapp.processEvents()
        time.sleep(0.2)
        qapp.processEvents()
        assert cagrilar == [] and not p._takvim_zamanlayici.isActive() and not p.zamanlayici.isActive()
        assert p._durum() == "bos"
    finally:
        _guvenli_kapat(qapp, p)


def test_requirements_surumleri_sabit_ve_derle_workflow_gizli_importlari_icerir():
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    satirlar = [s.strip() for s in open(os.path.join(kok, "requirements.txt"), encoding="utf-8")
                if s.strip() and not s.startswith("#")]
    assert satirlar and all("==" in s for s in satirlar), satirlar
    adlar = {s.split("==")[0].lower() for s in satirlar}
    assert {"pyqt5", "numpy", "requests", "httpx", "openai", "pywin32", "uiautomation", "sounddevice", "soundcard",
            "soundfile", "python-docx"} <= adlar
    gerekli = ("konusmaci", "stt", "saat", "terim", "duzelt", "kelime", "rag")
    derle = open(os.path.join(kok, "derle.bat"), encoding="utf-8").read()
    is_akisi = os.path.join(os.path.dirname(kok), ".github", "workflows", "briefmind.yml")
    metinler = [derle]
    if os.path.exists(is_akisi):
        metinler.append(open(is_akisi, encoding="utf-8").read())
    for metin in metinler:
        assert '--add-data "veri;veri"' in metin
        for ad in gerekli:
            assert f"--hidden-import {ad}" in metin, ad
        assert "--duman" in metin


def test_main_yetim_is_kapanisi_pencereli_exede_stdout_none_iken_os_exit_eder(monkeypatch, tmp_path):
    """PyInstaller --windowed: sys.stdout None. Yetim QThread varken main() flush yuzunden AttributeError atip 'acilis
    hatasi' kutusuna dusmemeli; os._exit(kod) her durumda calismali (Qt'nin calisan thread'i yok ederken abort'u)."""
    import app

    class SahteUygulama:
        def __init__(self, argv):
            pass

        def setQuitOnLastWindowClosed(self, _):
            pass

        def setWindowIcon(self, _):
            pass

        def exec_(self):
            return 0

    class SahtePencere:
        def __init__(self, duman=False):
            self.yetim_isler = [object()]

        def show(self):
            pass

    class Cikis(BaseException):                  # os._exit donmez; except Exception yakalamasin
        pass

    cagrilar = []

    def sahte_exit(kod):
        cagrilar.append(kod)
        raise Cikis()

    kutu = []
    monkeypatch.setattr(app.QtWidgets, "QApplication", SahteUygulama)
    monkeypatch.setattr(app.QtWidgets.QMessageBox, "critical", staticmethod(lambda *a, **k: kutu.append(a)))
    monkeypatch.setattr(app, "Pencere", SahtePencere)
    monkeypatch.setattr(app, "hata_kancalari_kur", lambda: None)
    monkeypatch.setattr(app, "hata_koprusu_kur", lambda: None)
    monkeypatch.setattr(app, "logo_ikonu", lambda: None)
    monkeypatch.setattr(app, "uygulama_klasoru", lambda: str(tmp_path))
    monkeypatch.setattr(app, "_hata_kaydet", lambda tb: kutu.append(tb))
    monkeypatch.setattr(app.ayar_mod, "AYAR_HATASI", None)
    monkeypatch.setattr(app.os, "_exit", sahte_exit)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    with pytest.raises(Cikis):
        app.main(["briefmind"])
    assert cagrilar == [0] and kutu == []


# =============================================================== ayar.py: sonlu olmayan sayilar, dusunme butcesi

@pytest.mark.parametrize("ham", ["Infinity", "-Infinity", "NaN", "1e999"])
def test_sonlu_olmayan_sayi_ayar_oku_cokertmez_varsayilana_doner(ortam, ham):
    (ortam / "config.json").write_text(
        '{"otobitir": %s, "context": %s, "stt_repetition_penalty": %s, "llm_dusunme_butcesi": %s, "esikler": {"hal_at": %s}, '
        '"ben": "Deniz Kaya"}' % (ham, ham, ham, ham, ham), encoding="utf-8")
    v = ayar_mod.ayar_oku()
    assert ayar_mod.AYAR_HATASI is None
    assert v["otobitir"] == 180 and v["context"] == 0 and v["stt_repetition_penalty"] is None
    assert "llm_dusunme_butcesi" not in v and "esikler" not in v and v["ben"] == "Deniz Kaya"
    uyarilar = ayar_mod.ayar_uyarilari_al()
    for anahtar in ("otobitir", "context", "stt_repetition_penalty", "llm_dusunme_butcesi", "esikler"):
        assert len([u for u in uyarilar if f"'{anahtar}'" in u]) == 1                 # anahtar basina BIR uyari
    ayar_mod.ayar_oku()
    assert ayar_mod.ayar_uyarilari_al() == []                                          # ikinci okumada tekrar yok


def test_llm_dusunme_butcesi_semada_sinirlanir(ortam):
    (ortam / "config.json").write_text('{"llm_dusunme_butcesi": 99999}', encoding="utf-8")
    assert ayar_mod.ayar_oku()["llm_dusunme_butcesi"] == 65536
    (ortam / "config.json").write_text('{"llm_dusunme_butcesi": "abc"}', encoding="utf-8")
    ayar_mod.uyarilari_sifirla()
    assert "llm_dusunme_butcesi" not in ayar_mod.ayar_oku()
    assert any("llm_dusunme_butcesi" in u for u in ayar_mod.ayar_uyarilari_al())


# =============================================================== ses.py public kesinti API, isler.py

def test_kesinti_devam_isi_yalniz_public_ses_api_kullanir():
    import inspect
    import isler
    kaynak = inspect.getsource(isler.KesintiDevamIsi)
    for ozel in ("._coz", "._bekci", "._kesinti_bas", "._kesinti_kuyrugu", "._dur.set", "servis._", "sv._"):
        assert ozel not in kaynak.replace("self._dur", "").replace("self._servis", ""), ozel


def test_ses_servisi_public_kesinti_api(ortam):
    import ses
    s = ses.SesServisi("http://stt.ornek")
    assert s.kesinti_kalan() == 0                                                      # kuyruk yok
    s.duraklat()
    assert s._dur.is_set()
    for ad in ("kesinti_bosalt", "duraklat", "kesinti_kalan"):
        assert callable(getattr(ses.SesServisi, ad))


def test_ses_servisi_kesinti_bosalt_duraklatilinca_klibi_diskte_birakir(ortam, monkeypatch):
    import ses
    import stt
    s = ses.SesServisi("http://stt.ornek")
    kalan = [2]
    cagrilar = []
    monkeypatch.setattr(s, "_bekci_baslat", lambda: None)
    s._bekci = None
    monkeypatch.setattr(s, "kesinti_kalan", lambda: kalan[0])
    assert s.kesinti_bosalt() == 2                                                     # bekci yok: hemen doner
    pasif = {"v": True}
    s._coz = lambda p: cagrilar.append(p)
    s.kesinti_bosalt(duraklatildi=lambda: pasif["v"], duraklat_hatasi=ValueError)
    with pytest.raises(ValueError):
        s._coz({"klip_no": 1})
    assert cagrilar == []
    pasif["v"] = False
    s._coz({"klip_no": 1})
    assert cagrilar == [{"klip_no": 1}]
    assert stt.kesinti_hatasi_mi(ValueError("x")) is True


def test_ses_servisi_kesinti_bosalt_uzun_kesintide_duraklatir(ortam, monkeypatch):
    import ses
    s = ses.SesServisi("http://stt.ornek")

    class Bekci:
        n = 0

        def is_alive(self):
            self.n += 1
            return self.n < 3

        def join(self, sn):
            pass
    s._bekci = Bekci()
    s._kesinti_bas = saat.simdi() - 100
    monkeypatch.setattr(s, "_bekci_baslat", lambda: None)
    uyarilar = []
    s.kesinti_bosalt(kesinti_bekle_sn=60, kesinti_asildi=lambda: uyarilar.append(1))
    assert s._dur.is_set() and uyarilar == [1]


# =============================================================== hata.log: bat + teshis ayar.hata_log_yolu() kullanir

def test_toplanti_notu_bat_hata_log_yolunu_ayardan_alir():
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bat = open(os.path.join(kok, "ToplantiNotu.bat"), encoding="utf-8").read()
    assert "ayar.hata_log_yolu()" in bat and "Ayrinti: %HATALOG%" in bat


def test_teshis_hata_logu_localappdata_yolunu_okur(ortam, monkeypatch):
    import importlib.util
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monkeypatch.setenv("LOCALAPPDATA", str(ortam / "yerel"))
    ayar_mod.hata_yaz("Traceback ornek\n")
    yol = ayar_mod.hata_log_yolu()
    assert os.path.exists(yol) and "BriefMind" in yol
    cwd = os.getcwd()
    try:
        spec = importlib.util.spec_from_file_location("teshis_modul", os.path.join(kok, "tools", "teshis.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)                                                       # chdir KOK yapar
        m.CIKTI.clear()
        m.hata_logu()
        cikti = "\n".join(m.CIKTI)
    finally:
        os.chdir(cwd)
    assert yol in cikti and "Traceback ornek" in cikti and "hata.log yok" not in cikti


def test_config_example_semadaki_ve_kodun_okudugu_anahtarlari_icerir():
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ornek = json.load(open(os.path.join(kok, "config.example.json"), encoding="utf-8"))
    for k in ("kesinti_devam", "stt_ipucu", "stt_repetition_penalty", "olcum_kaydi", "altyazi_yedek",
              "llm_dusunme_butcesi", "embed_url", "embed_key", "embed_boyut", "embed_proxy", "embed_talimat"):
        assert k in ornek, k
    import konusmaci
    import stt
    assert set(konusmaci.VARSAYILAN_ESIKLER) | set(stt.HAL_ESIKLER) <= set(ornek["esikler"])
    for k, (tur, alt, ust) in ayar_mod.SEMA.items():
        if k in ornek:
            ayar_mod._tip_duzelt(ornek[k], (tur, alt, ust))                              # ornek deger sema ile uyumlu
    metin = json.dumps(ornek)
    assert "://<" in metin and "http://10." not in metin


def test_ayar_faz3_faz6_anahtarlari_varsayilan_kapali(tmp_path, monkeypatch):
    import ayar as ayar_mod
    monkeypatch.chdir(tmp_path)
    v = ayar_mod.ayar_oku()
    assert v["uzun_pencere"] is False and v["ses_izi"] is False
