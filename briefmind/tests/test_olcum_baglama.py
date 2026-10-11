"""Faz 0 olcum kaydi baglantisi: ayar olcum_kaydi acikken YakalamaIsi klip kaydini (SesServisi.olcum_kaydi_ac) ve
altyazi okuma kaydini (Yakalayici.okuma_kaydi_ac) toplanti klasoruyle acar, Olaylar'a bir satir yazar; kapaliyken
hicbirini cagirmaz. Sahte sinif/motor kullanilir; ses cihazi, ag gerekmez."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # PyQt5 import edilmeden ONCE

import pytest
from PyQt5 import QtWidgets

import ayar as ayar_mod


class SahteYakalayici:
    cagrilar = []
    isi = None

    def __init__(self):
        self.hazir = False

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

    def altyazi_satiri(self, s):
        pass

    def kontrol(self):
        pass

    def bitir(self):
        return []


class SahteSes:
    cagrilar = []
    cozulen = 0
    kulaklik = None

    def __init__(self, *a, **kw):
        pass

    def olcum_kaydi_ac(self, klasor):
        SahteSes.cagrilar.append(klasor)

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
    SahteYakalayici.cagrilar, SahteSes.cagrilar = [], []
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
    assert SahteSes.cagrilar == [] and SahteYakalayici.cagrilar == []
    assert not any("Ölçüm kaydı" in m for m in loglar)
