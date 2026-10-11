"""YakalamaIsi baglantisi. Faz 0 olcum kaydi: ayar olcum_kaydi acikken klip kaydi (SesServisi.olcum_kaydi_ac) ve
altyazi okuma kaydi (Yakalayici.okuma_kaydi_ac) toplanti klasoruyle acilir, Olaylar'a bir satir yazilir; kapaliyken
hicbiri cagrilmaz. Faz 1: SesServisi(olay_fn=motor.ses_olayi), Yakalayici(okuma_fn=motor.okuma_ekle,
katilimcilar=...), motor.yeniden_kes_fn = SesServisi.yeniden_kes, config kesinti_kaydi -> kesinti_kaydi_ac, her
turda stt_durumu motora. Sahte sinif/motor kullanilir; ses cihazi, ag gerekmez."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # PyQt5 import edilmeden ONCE

import pytest
from PyQt5 import QtWidgets

import ayar as ayar_mod


class SahteYakalayici:
    cagrilar = []
    isi = None
    son = None

    def __init__(self, katilimcilar=(), okuma_fn=None):
        self.hazir = False
        self.katilimcilar, self.okuma_fn = katilimcilar, okuma_fn
        SahteYakalayici.son = self

    def okuma_kaydi_ac(self, klasor):
        SahteYakalayici.cagrilar.append(klasor)
        return os.path.join(klasor, "olcum", "okumalar.jsonl")

    def oku(self):
        SahteYakalayici.isi.durdur()
        return []

    def kayip_saniye(self):
        return 0

    def onerilen_aralik(self):
        return 0

    def bitir(self):
        return []


class SahteMotor:
    def __init__(self, klasor, baslik, tarih, sozluk, katilimcilar, gundem, **kw):
        self.klasor, self.kaynak, self.parca_no, self.mevcut_tok = klasor, kw.get("kaynak"), 0, 0
        self.seri, self.katilimcilar, self.kulaklik = "x", [], None
        self.ben, self.sablon = "Deneme Kisi", "genel"
        self.ses_olay_sayisi, self.yeniden_kes_fn, self.stt = 0, None, []
        SahteMotor.son = self

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
        return []


class SahteSes:
    cagrilar = []
    kesinti = []
    kw = {}
    cozulen = 0
    kulaklik = None
    stt_durumu = {"durum": "ok", "bas": None, "bekleyen": 0, "kayit": False, "eksik": [], "hata": None}

    def __init__(self, *a, **kw):
        SahteSes.kw = kw

    def olcum_kaydi_ac(self, klasor):
        SahteSes.cagrilar.append(klasor)

    def kesinti_kaydi_ac(self, klasor):
        SahteSes.kesinti.append(klasor)

    def yeniden_kes(self, klip_no, an):
        return False

    def baslat(self):
        return True

    def durdur(self):
        pass


class _Sozluk:
    def metin(self, seri):
        return ""


@pytest.fixture
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _calistir(qapp, tmp_path, monkeypatch, ayar):
    import isler
    import ses
    SahteYakalayici.cagrilar, SahteSes.cagrilar, SahteSes.kesinti = [], [], []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(isler, "Yakalayici", SahteYakalayici)
    monkeypatch.setattr(isler, "Motor", SahteMotor)
    monkeypatch.setattr(isler, "ekran_okuyucu", lambda acik: None)
    monkeypatch.setattr(isler.motor_mod, "yeni_klasor", lambda baslik, tarih: str(tmp_path / "kayit"))
    monkeypatch.setattr(ses, "SesServisi", SahteSes)
    isi = isler.YakalamaIsi("Deneme", {}, _Sozluk(), False, 30, ayar)
    SahteYakalayici.isi = isi
    loglar = []
    isi.olay.connect(lambda t, v: loglar.append((t, v)))
    isi.start()
    assert isi.wait(15000)
    qapp.processEvents()
    return [v for t, v in loglar if t == "log"]


def test_varsayilan_ayar_kapali():
    assert ayar_mod.ayar_oku()["olcum_kaydi"] is False


def test_bayrak_acikken_iki_kayit_acilir_ve_olay_yazilir(qapp, tmp_path, monkeypatch):
    loglar = _calistir(qapp, tmp_path, monkeypatch, {"kaynak": "ses", "olcum_kaydi": True})
    klasor = str(tmp_path / "kayit")
    assert SahteSes.cagrilar == [klasor] and SahteYakalayici.cagrilar == [klasor]
    assert sum("Ölçüm kaydı açık" in m for m in loglar) == 1


def test_bayrak_kapaliyken_hicbir_sey_cagrilmaz(qapp, tmp_path, monkeypatch):
    loglar = _calistir(qapp, tmp_path, monkeypatch, {"kaynak": "ses"})
    assert SahteSes.cagrilar == [] and SahteYakalayici.cagrilar == [] and SahteSes.kesinti == []
    assert not any("Ölçüm kaydı" in m for m in loglar)


def test_faz1_olay_baglantilari(qapp, tmp_path, monkeypatch):
    _calistir(qapp, tmp_path, monkeypatch, {"kaynak": "ses", "kesinti_kaydi": True})
    m, y = SahteMotor.son, SahteYakalayici.son
    assert SahteSes.kw["olay_fn"] == m.ses_olayi and "satir_fn" not in SahteSes.kw
    assert y.okuma_fn == m.okuma_ekle
    assert m.yeniden_kes_fn is None                                  # bitiste koparilir (ses durdu)
    assert SahteSes.kesinti == [str(tmp_path / "kayit")]            # olcum kapali, kesinti_kaydi acik
    assert m.stt and m.stt[0]["durum"] == "ok"                      # her turda stt_durumu motora
