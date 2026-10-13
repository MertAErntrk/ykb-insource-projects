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

    def __init__(self, katilimcilar=(), okuma_fn=None, ad_esigi=3, ses_var_fn=None, log_fn=None, ses_sure_fn=None):
        self.hazir = False
        self.katilimcilar, self.okuma_fn = katilimcilar, okuma_fn
        self.ses_var_fn, self.log_fn, self.ses_sure_fn = ses_var_fn, log_fn, ses_sure_fn
        SahteYakalayici.son = self

    def yeniden_bul(self):
        pass

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
        self.yeniden_bul_fn, self.olaylar = None, []
        SahteMotor.son = self

    def son_loopback_ses(self):
        return None

    def olay_kaydet(self, mesaj, **ek):
        self.olaylar.append((mesaj, ek))

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
    # K1/K2: panel canliligi icin son loopback konusma bitisi, olay gunlugu; atama basarisizsa motor paneli yeniden aratir
    assert y.ses_var_fn == m.son_loopback_ses and y.log_fn == m.olay_kaydet
    assert m.yeniden_bul_fn is None                                  # bitiste koparilir (yakalayici durdu)
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


# ================================================================ B0: okuma dongusu UIA yuruyusleriyle bloke olmaz

import time

import saat as saat_modulu


class DonguYakalayici(SahteYakalayici):
    """oku() zamanlarini kaydeder; hazir, hazir_ol(n) -> n. okumadan sonra hazir (None: hep False); sure sonra durdurur."""
    sure = 3.0
    hazir_tur = 0
    hazir_kayip = None                              # bu okumadan sonra hazir False (panel kayboldu)
    uyku = 0.1

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.zamanlar, self.bas = [], time.monotonic()

    def oku(self):
        t = time.monotonic()
        self.zamanlar.append(t)
        n = len(self.zamanlar)
        self.hazir = n > self.hazir_tur and (self.hazir_kayip is None or n <= self.hazir_kayip)
        if t - self.bas > self.sure:
            SahteYakalayici.isi.durdur()
        return []

    def onerilen_aralik(self):
        return self.uyku


def _dongu(qapp, tmp_path, monkeypatch, yak_ozellik=None, isi_ozellik=None, ayar=None):
    """Sahte yakalayici + sahte ses ile gercek YakalamaIsi dongusu; (loglar, yakalayici, isi)."""
    import isler
    import ses
    SahteYakalayici.cagrilar, SahteSes.cagrilar, SahteSes.kesinti = [], [], []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(isler, "Yakalayici", DonguYakalayici)
    monkeypatch.setattr(isler, "Motor", SahteMotor)
    monkeypatch.setattr(isler, "ekran_okuyucu", lambda acik: None)
    monkeypatch.setattr(isler.motor_mod, "yeni_klasor", lambda baslik, tarih: str(tmp_path / "kayit"))
    monkeypatch.setattr(ses, "SesServisi", SahteSes)
    for k, v in (yak_ozellik or {}).items():
        monkeypatch.setattr(DonguYakalayici, k, v)
    isi = isler.YakalamaIsi("Deneme", {}, SahteTerimler(), False, 30, ayar or {"kaynak": "ses"})
    for k, v in (isi_ozellik or {}).items():
        setattr(isi, k, v)
    SahteYakalayici.isi = isi
    loglar = []
    isi.olay.connect(lambda t, v: loglar.append((t, v)))
    isi.start()
    assert isi.wait(30000)
    qapp.processEvents()
    return [v for t, v in loglar if t == "log"], SahteYakalayici.son, isi


def _bosluklar(zamanlar):
    return [b - a for a, b in zip(zamanlar, zamanlar[1:])]


def test_yavas_pencere_denetimi_okuma_boslugu_acmaz(qapp, tmp_path, monkeypatch):
    """toplanti_penceresi_var 2,5 sn surer (Teams buyukken UIA yuruyusu): okuma olaylari arasi bosluk < 1 sn kalmali;
    panel okunurken en az PENCERE_YAVAS_SN'de bir calisir."""
    import yakalayici as yak
    cagrilar = []

    def yavas_pencere():
        cagrilar.append(time.monotonic())
        time.sleep(2.5)
        return True
    monkeypatch.setattr(yak, "toplanti_penceresi_var", yavas_pencere)
    loglar, y, isi = _dongu(qapp, tmp_path, monkeypatch, {"sure": 6.0},
                            {"PENCERE_HIZLI_SN": 0.2, "PENCERE_YAVAS_SN": 1.5})
    assert len(y.zamanlar) > 20
    assert max(_bosluklar(y.zamanlar)) < 1.0
    assert 1 <= len(cagrilar) <= 2                           # 6 sn'de 2,5 sn'lik is + 1,5 sn aralik
    assert all(b - a >= 2.5 + 1.5 - 0.1 for a, b in zip(cagrilar, cagrilar[1:]))     # tek is, bitince aralik
    assert not any("kapandı" in m for m in loglar)


def test_pencere_denetimi_panel_yokken_sik_calisir(qapp, tmp_path, monkeypatch):
    import yakalayici as yak
    cagrilar = []
    monkeypatch.setattr(yak, "toplanti_penceresi_var", lambda: cagrilar.append(time.monotonic()) or True)
    # panel hep okunuyor: PENCERE_YAVAS_SN dongu suresinden uzun -> hic denetim yok
    _dongu(qapp, tmp_path, monkeypatch, {"sure": 2.0}, {"PENCERE_HIZLI_SN": 0.3, "PENCERE_YAVAS_SN": 100.0})
    assert cagrilar == []
    # 3 okuma panel var (yakalama basladi), sonra panel kayip: hizli aralikla denetim
    _, y, _ = _dongu(qapp, tmp_path, monkeypatch, {"sure": 3.0, "hazir_kayip": 3},
                     {"PENCERE_HIZLI_SN": 0.3, "PENCERE_YAVAS_SN": 100.0})
    assert len(cagrilar) >= 4                                # ~0.3-0.4 sn'de bir
    assert cagrilar[0] >= y.zamanlar[2]                      # panel varken calismadi


def test_pencere_kapaninca_toplanti_biter(qapp, tmp_path, monkeypatch):
    import yakalayici as yak
    monkeypatch.setattr(yak, "toplanti_penceresi_var", lambda: False)
    loglar, y, isi = _dongu(qapp, tmp_path, monkeypatch, {"sure": 20.0, "hazir_tur": 0},
                            {"PENCERE_HIZLI_SN": 0.1, "PENCERE_YAVAS_SN": 0.1, "PENCERE_YOK_SN": 0.5})
    assert any("Teams toplantı penceresi kapandı" in m for m in loglar)
    assert y.zamanlar[-1] - y.bas < 10.0                     # sure dolmadan pencere yokluguyla bitti


def test_altyazi_acma_denemesi_okuma_boslugu_acmaz(qapp, tmp_path, monkeypatch):
    import yakalayici as yak
    cagrilar = []

    def yavas_ac(log=print):
        cagrilar.append(time.monotonic())
        time.sleep(2.5)
        return False
    monkeypatch.setattr(yak, "altyazi_ac", yavas_ac)
    # altyazi hic gelmez: hazir hep False
    loglar, y, isi = _dongu(qapp, tmp_path, monkeypatch, {"sure": 4.0, "hazir_tur": 10 ** 9},
                            {"ALTYAZI_DENEME_SN": 0.3})
    assert len(cagrilar) >= 1
    assert max(_bosluklar(y.zamanlar)) < 1.0
    assert any("açmayı deniyorum (1/2)" in m for m in loglar)


def test_altyazi_dili_ayri_thread_okuma_boslugu_acmaz(qapp, tmp_path, monkeypatch):
    import yakalayici as yak
    cagrilar = []

    def yavas_dil(log=print):
        cagrilar.append(time.monotonic())
        time.sleep(2.5)
    monkeypatch.setattr(yak, "altyazi_dili_turkce", yavas_dil)
    loglar, y, isi = _dongu(qapp, tmp_path, monkeypatch, {"sure": 3.5}, ayar={"kaynak": "ses", "altyazi_turkce": True})
    assert len(cagrilar) == 1 and max(_bosluklar(y.zamanlar)) < 1.0
    assert any("altyazı bulundu, yakalama başladı" in m for m in loglar)


def test_yavas_dongu_bir_kez_loglar_ve_toparlaninca_sifirlar():
    import isler
    loglar = []
    yd = isler._YavasDongu(1.2, 10.0, loglar.append)
    t = 100.0
    for _ in range(30):                                      # periyot 1.5 sn > 1.2, toplam 45 sn
        t += 1.5
        yd.adim(t, 1.5, 0.9, 0.6)
    assert len(loglar) == 1 and loglar[0].startswith("okuma döngüsü yavaş") and "1.5 sn" in loglar[0]
    yd.adim(t + 1.0, 0.7, 0.1, 0.6)                          # toparlandi
    for _ in range(10):
        t += 1.5
        yd.adim(t + 10, 1.5, 0.9, 0.6)
    assert len(loglar) == 2                                  # yine yavaslarsa yeniden


def test_yavas_dongu_kisa_sureli_yavaslik_loglanmaz():
    import isler
    loglar = []
    yd = isler._YavasDongu(1.2, 10.0, loglar.append)
    t = 0.0
    for _ in range(5):
        t += 2.0
        yd.adim(t, 2.0, 1.4, 0.6)
    yd.adim(t + 0.6, 0.6, 0.1, 0.5)
    for _ in range(5):
        t += 2.0
        yd.adim(t + 100, 2.0, 1.4, 0.6)
    assert loglar == [] and yd.adim(0.0, None, 0, 0) is None


def test_pencere_izleyici_onbellekli_sonuc_ve_aralik(monkeypatch):
    import isler
    import yakalayici as yak
    sa = [500.0]
    monkeypatch.setattr(saat_modulu, "simdi", lambda: sa[0])
    sonuclar = []
    monkeypatch.setattr(yak, "toplanti_penceresi_var", lambda: sonuclar.pop(0))
    p = isler._PencereIzleyici(5.0, 30.0, 45.0)

    def adim(hazir, ilerle):
        sa[0] += ilerle
        r = p.adim(hazir)
        while p._is.calisiyor:                               # thread bitsin (sonuc bir sonraki adimda alinir)
            time.sleep(0.005)
        return r

    sonuclar[:] = [True, False, False, False, False, False, False, False, False, False, False, True]
    assert adim(True, 0) is False and p.kontrol_sayisi == 0   # hemen calismaz
    adim(True, 29)
    assert p.kontrol_sayisi == 0                              # panel okunuyor: 30 sn dolmadi
    adim(True, 2)
    assert p.kontrol_sayisi == 1 and p.deger is None          # is basladi, sonuc henuz alinmadi
    adim(True, 0.5)
    assert p.deger is True                                    # onbellekli sonuc
    adim(True, 10)
    assert p.kontrol_sayisi == 1                              # sonuc geldikten sonra yine 30 sn
    adim(True, 21)
    assert p.kontrol_sayisi == 2
    adim(True, 0.5)
    assert p.deger is False
    n = p.kontrol_sayisi
    adim(False, 5.1)                                          # pencere yok: 5 sn'de bir
    adim(False, 0.1)
    assert p.kontrol_sayisi == n + 1
    bitti = False
    for _ in range(12):
        bitti = adim(False, 5.1) or bitti
        if bitti:
            break
    assert bitti is True                                      # 45 sn'dir yok -> toplanti bitti


def test_pencere_izleyici_istisna_degeri_bozmaz(monkeypatch):
    import isler
    import yakalayici as yak

    def bozuk():
        raise RuntimeError("uia")
    monkeypatch.setattr(yak, "toplanti_penceresi_var", bozuk)
    sa = [10.0]
    monkeypatch.setattr(saat_modulu, "simdi", lambda: sa[0])
    p = isler._PencereIzleyici(5.0, 30.0, 45.0)
    sa[0] += 6
    p.adim(False)
    while p._is.calisiyor:
        time.sleep(0.005)
    sa[0] += 6
    assert p.adim(False) is False and p.deger is None        # hata 'pencere yok' sayilmaz


def test_pencere_izleyici_pencere_geri_gelirse_sayac_sifirlanir(monkeypatch):
    import isler
    import yakalayici as yak
    sa = [10.0]
    monkeypatch.setattr(saat_modulu, "simdi", lambda: sa[0])
    sonuclar = [False, False, True]
    monkeypatch.setattr(yak, "toplanti_penceresi_var", lambda: sonuclar.pop(0))
    p = isler._PencereIzleyici(5.0, 30.0, 45.0)

    def adim(ilerle):
        sa[0] += ilerle
        r = p.adim(False)
        while p._is.calisiyor:
            time.sleep(0.005)
        return r

    for _ in range(6):
        assert adim(5.1) is False
    assert p.deger is True and p._yok_bas is None
