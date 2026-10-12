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
    def __init__(self, klasor, baslik, tarih, terimler, katilimcilar, gundem, **kw):
        self.klasor, self.kaynak, self.parca_no, self.mevcut_tok = klasor, kw.get("kaynak"), 0, 0
        self.terimler = SahteTerimler()
        self.seri, self.katilimcilar, self.kulaklik = "x", [], None
        self.ben, self.sablon = "Deneme Kisi", "genel"
        self.ses_olay_sayisi, self.yeniden_kes_fn, self.stt = 0, None, []
        SahteMotor.son = self

    def terimleri_hazirla(self):
        self.hazirlandi = getattr(self, "hazirlandi", 0) + 1
        return 0

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

    def ses_izi_kur(self, ayar, ses_al_fn, cikarici=None):
        self.ses_izi_kuruldu = ses_al_fn
        return False


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

    def ses_al(self, akis, bas, bit):
        return None

    def baslat(self):
        return True

    def durdur(self):
        pass


class SahteTerimler:
    def prompt(self, seri, adlar=(), onceki=""):
        return f"prompt:{seri}:{','.join(adlar)}"


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
    isi = isler.YakalamaIsi("Deneme", {}, SahteTerimler(), False, 30, ayar)
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


def test_faz2_ipucu_terim_prompt_ve_stt_ayarlari_baglanir(qapp, tmp_path, monkeypatch):
    """ipucu_fn = motor.terimler.prompt(seri, katilimci adlari) (eski sozluk metni gitmez); stt_ipucu / esikler /
    stt_repetition_penalty config'ten SesServisi'ne; cizelge_fn verilmez (altyazi STT aninda henuz gelmemistir)."""
    _calistir(qapp, tmp_path, monkeypatch, {"kaynak": "ses", "stt_ipucu": False, "stt_repetition_penalty": 1.1,
                                            "esikler": {"hal_supheli": 0.4}})
    m = SahteMotor.son
    m.katilimcilar.extend(["Ayşe Demir", "Burak Şahin"])
    assert SahteSes.kw["ipucu_fn"]("loopback") == "prompt:x:Ayşe Demir,Burak Şahin"
    assert SahteSes.kw["ipucu_fn"]() == "prompt:x:Ayşe Demir,Burak Şahin"               # sifir argumanli cagri da calisir
    assert SahteSes.kw["stt_ipucu"] is False and SahteSes.kw["stt_repetition_penalty"] == 1.1
    assert SahteSes.kw["esikler"] == {"hal_supheli": 0.4} and "cizelge_fn" not in SahteSes.kw


def test_faz2_ipucu_varsayilan_acik_ve_davet_terimleri_hazirlanir(qapp, tmp_path, monkeypatch):
    _calistir(qapp, tmp_path, monkeypatch, {"kaynak": "ses"})
    assert SahteSes.kw["stt_ipucu"] is True and SahteSes.kw["stt_repetition_penalty"] is None
    assert SahteMotor.son.hazirlandi == 1                       # davet / onceki not terimleri toplanti basinda havuza


def test_faz6_ses_izi_bayrakla_baglanir(qapp, tmp_path, monkeypatch):
    _calistir(qapp, tmp_path, monkeypatch, {"kaynak": "ses"})
    assert not hasattr(SahteMotor.son, "ses_izi_kuruldu")
    _calistir(qapp, tmp_path, monkeypatch, {"kaynak": "ses", "ses_izi": True})
    assert getattr(SahteMotor.son.ses_izi_kuruldu, "__name__", "") == "ses_al"
