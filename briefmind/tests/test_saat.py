"""saat.py: adima dayanikli zaman kaynagi. Duvar saati (NTP/w32time adimi, elle degisiklik) sicrayinca simdi() sicramaz;
ses ankrajlari, altyazi okumasi, motor son tarihleri ayni zaman tabaninda kalir; ekrandaki HH:MM:SS yerel saat dogru kalir.
Ses cihazi, ag yok; sanal 'duvar' ve 'monotonik' saatler saat.time uzerinden verilir."""
import datetime as dt
import queue
import time
import types

import pytest

import motor as motor_mod
import saat
import ses
import yakalayici

T0 = 1760170000.0


class Sanal:
    """Sanal sistem saatleri: `mono` ilerler, `ofset` duvar saatini sicratir (adim)."""

    def __init__(self):
        self.mono_t = 5000.0
        self.ofset = 0.0

    def duvar(self):
        return T0 + (self.mono_t - 5000.0) + self.ofset

    def mono(self):
        return self.mono_t

    def ilerle(self, sn):
        self.mono_t += sn

    def adim(self, sn):
        self.ofset += sn


@pytest.fixture
def sistem():
    """saat.py'nin time basvurusu sanal saatlere baglanir; ankrajlar o anda kurulur."""
    s = Sanal()
    eski = saat.time, saat.LOG_FN
    saat.time = types.SimpleNamespace(time=s.duvar, monotonic=s.mono)
    saat.sifirla()
    yield s
    saat.time, saat.LOG_FN = eski
    saat.sifirla()


# ---------------------------------------------------------------- saat.py

def test_simdi_gercek_saate_yakin_ve_monoton():
    a = saat.simdi()
    time.sleep(0.02)
    b = saat.simdi()
    assert 0 < b - a < 2 and abs(a - time.time()) < 2


def test_simdi_duvar_adimindan_etkilenmez(sistem):
    t = saat.simdi()
    assert t == T0
    sistem.ilerle(10)
    sistem.adim(+3)                                    # w32time ileri adim
    assert saat.simdi() == T0 + 10
    sistem.adim(-6)
    sistem.ilerle(5)
    assert saat.simdi() == T0 + 15                     # geri adim da etkilemez


def test_duvar_gosterim_icin_guncel_ofseti_uygular(sistem):
    sistem.ilerle(20)
    e = saat.simdi()
    assert saat.duvar(e) == e
    sistem.adim(+3)
    assert saat.duvar(e) == pytest.approx(e + 3)
    assert saat.duvardan(saat.duvar(e)) == pytest.approx(e)
    sistem.adim(-5)
    assert saat.duvar(e) == pytest.approx(e - 2)


def test_ts_yerel_saati_dogru_gosterir_adimdan_sonra(sistem):
    e = saat.simdi() + 10                               # simdi epoch'u
    sistem.adim(+3)
    beklenen = dt.datetime.fromtimestamp(e + 3).strftime("%H:%M:%S")
    assert saat.ts(e) == beklenen
    assert saat.yerel(e) == dt.datetime.fromtimestamp(e + 3)
    d = dt.datetime.fromtimestamp(e + 3)
    assert saat.epoch(d) == pytest.approx(e)           # yerel datetime -> simdi epoch'u


def test_mikro_kayma_ekran_saniyesini_oynatmaz(sistem):
    sistem.adim(0.004)                                  # NTP mikro kaymasi
    e = T0 + 100.0
    assert saat.duvar(e) == e
    sistem.adim(-0.008)
    assert saat.duvar(e) == e


def test_adim_algilanir_bir_kez_loglanir(sistem):
    log = []
    saat.LOG_FN = log.append
    sistem.ilerle(30)
    assert saat.adim_denetle() == 0.0 and log == []
    sistem.adim(+3)
    assert saat.adim_denetle() == pytest.approx(3.0)
    assert saat.adim_denetle() == 0.0                   # ayni adim ikinci kez bildirilmez
    assert len(log) == 1 and "+3.0" in log[0] and saat.adim_sayisi() == 1
    sistem.ilerle(60)
    sistem.adim(-3)
    assert saat.adim_denetle() == pytest.approx(-3.0) and len(log) == 2


def test_yavas_ntp_kaymasi_adim_sayilmaz(sistem):
    saat.LOG_FN = lambda m: pytest.fail("adim degil")
    for _ in range(100):
        sistem.ilerle(1)
        sistem.adim(0.0005)                             # 500 ppm kayma
        assert saat.adim_denetle() == 0.0
    assert saat.adim_sayisi() == 0


def test_log_hatasi_denetimi_bozmaz(sistem):
    def bozuk(m):
        raise RuntimeError("log")
    saat.LOG_FN = bozuk
    sistem.adim(+1)
    assert saat.adim_denetle() == pytest.approx(1.0)


def test_sanal_kur_dikisi_ve_geri_alma():
    gercek = (saat.simdi, saat.duvar, saat.duvardan)
    sn = [123.0]
    geri = saat.sanal_kur(lambda: sn[0])
    try:
        assert saat.simdi() == 123.0 and saat.duvar(5.0) == 5.0 and saat.duvardan(5.0) == 5.0
        assert saat.ts(T0) == dt.datetime.fromtimestamp(T0).strftime("%H:%M:%S")
        assert saat.adim_denetle() == 0.0               # sanal saatte gercek saat denetlenmez
    finally:
        geri()
    assert (saat.simdi, saat.duvar, saat.duvardan) == gercek


def test_sifirla_ankrajlari_yeniler(sistem):
    sistem.ilerle(40)
    sistem.adim(+9)
    saat.sifirla()
    assert saat.simdi() == pytest.approx(T0 + 40 + 9)  # yeni ankraj o anki duvar saati
    assert saat.duvar(saat.simdi()) == pytest.approx(saat.simdi())


# ---------------------------------------------------------------- ses.py: ankrajlar

def _akis(**kw):
    ak = ses.AkisYakalayici("loopback", queue.Queue(), loopback=True, **kw)
    ak._capa_kur(int(0.1 * ses.ORNEK))
    return ak


def test_ses_ankraji_duvar_adiminda_yeniden_capalanmaz(sistem):
    sistem.ilerle(0.1)                                 # ilk blok geldi
    ak = _akis()                                       # varsayilan saat = saat.simdi
    e0 = ak.ornek_epoch(0)
    ak.toplam_ornek = int(0.1 * ses.ORNEK)
    sistem.ilerle(70)
    sistem.adim(+3)                                    # 60 sn denetiminden ONCE duvar atladi
    ak.toplam_ornek = int(70.1 * ses.ORNEK)
    ak._capa_kontrol()
    assert ak.capa_sayisi == 0 and ak.ornek_epoch(0) == e0
    assert ak.ornek_epoch(ak.toplam_ornek) == pytest.approx(saat.simdi(), abs=1e-6)


def test_ses_ankrajinin_eski_duvar_davranisi_kayardi(sistem):
    """Karsilastirma: ankraj duvar saatinden okunsaydi 60 sn denetiminde +3 sn'lik capa olusurdu (eski sorun)."""
    sistem.ilerle(0.1)
    ak = _akis(saat=sistem.duvar)
    sistem.ilerle(70)
    sistem.adim(+3)
    ak.toplam_ornek = int(70.1 * ses.ORNEK)
    ak._capa_kontrol()
    assert ak.capa_sayisi == 1


def test_ses_ankraji_sanal_saat_dikisine_baglanir():
    sn = [1000.0]
    geri = saat.sanal_kur(lambda: sn[0])
    try:
        ak = _akis()
        assert ak.ornek_epoch(0) == pytest.approx(1000.0 - 0.1)
    finally:
        geri()


def test_klip_ts_ve_epoch_adimdan_sonra(sistem):
    """Klip olayi: bas/bit simdi epoch'u, ts yerel duvar saati."""
    s = ses.SesServisi("", olay_fn=lambda ev: None)
    sistem.ilerle(50)
    bas = saat.simdi()
    sistem.adim(+3)
    ev = s.klip_olayi_uret({"kaynak": "loopback", "klip_no": 1, "bas_epoch": bas, "bit_epoch": bas + 2.0},
                           [{"id": 0, "text": "Merhaba dunya.", "tokens": [], "temperature": 0.0}])
    assert ev["bas"] == bas and ev["bit"] == bas + 2.0
    assert ev["ts"] == dt.datetime.fromtimestamp(bas + 3).strftime("%H:%M:%S")


# ---------------------------------------------------------------- yakalayici.py: okuma damgalari

def test_okuma_damgalari_simdi_epoch_duvar_adiminda_sicramaz(sistem):
    y = yakalayici.Yakalayici()
    y.grup = object()
    okumalar = []
    y.okuma_fn = okumalar.append

    def oku_adimli():
        sistem.ilerle(0.2)
        sistem.adim(+3)                                # okuma sirasinda duvar atladi
        return [("Ayse Demir", "Merhaba")]

    y._ciftler = oku_adimli
    sistem.ilerle(100)
    y.oku()
    o = okumalar[0]
    assert o["bas_epoch"] == pytest.approx(T0 + 100) and o["bit_epoch"] == pytest.approx(T0 + 100.2)
    assert 0 < o["bit_epoch"] - o["bas_epoch"] < 1


def test_okuma_ts_yerel_saat(sistem):
    y = yakalayici.Yakalayici()
    y.grup = object()
    sistem.ilerle(10)
    sistem.adim(+7)
    y._ciftler = lambda: [("Ayse Demir", "Bugun toplanti saat uc.")]
    y._isle = lambda gorunen, ts: ts                   # yayim karari bu testin konusu degil: satir zamani
    assert y.oku() == dt.datetime.fromtimestamp(T0 + 10 + 7).strftime("%H:%M:%S")


def test_kayip_saniye_duvar_adiminda_sicramaz(sistem):
    """Altyazi grubu kayboldu: otobitir sayaci duvar saatine bakmaz."""
    y = yakalayici.Yakalayici()
    y.hazir = True
    yakalayici_grup_bul = yakalayici._grup_bul
    yakalayici._grup_bul = lambda: (None, "")
    try:
        y.oku()
    finally:
        yakalayici._grup_bul = yakalayici_grup_bul
    sistem.ilerle(30)
    sistem.adim(+120)                                  # duvar iki dakika ileri atladi
    assert y.kayip_saniye() == pytest.approx(30, abs=1)


# ---------------------------------------------------------------- motor.py: son tarihler ve gosterim

def test_motor_ts_ve_ts_epoch_yerel_saat_ofsetiyle(sistem):
    sistem.ilerle(10)
    e = saat.simdi()
    sistem.adim(+3)
    hms = dt.datetime.fromtimestamp(e + 3).strftime("%H:%M:%S")
    assert motor_mod._ts(e) == hms
    # ekrandaki HH:MM:SS (yerel duvar) -> simdi epoch'u
    beklenen = saat.simdi() + 0                         # simdi: ekranda gorulen 'simdi' = yerel saat
    simdi_hms = dt.datetime.fromtimestamp(saat.duvar(saat.simdi())).strftime("%H:%M:%S")
    assert motor_mod.Motor._ts_epoch(simdi_hms) == pytest.approx(beklenen, abs=1.0)
    assert motor_mod.stt_eksik_metni([[e, e + 60]]).startswith("STT yok — transkript eksik (" + hms[:5])


def test_motor_son_tarihleri_duvar_adiminda_kaymaz(tmp_path, sistem):
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date.fromtimestamp(T0), duzelt=False, ben="Zeynep Ornek",
                        katilimcilar=["Ayse Demir", "Zeynep Ornek"])
    try:
        sistem.ilerle(5)
        m._kapanis = None
        m._kesimler[9] = {"son": saat.simdi() + motor_mod.KESIM_BEKLE_SN, "gelen": 0, "parcalar": []}
        sistem.adim(+3600)                              # duvar bir saat atladi: kesim zaman asimi erken dolmamali
        m.kontrol()
        assert 9 in m._kesimler
        sistem.ilerle(motor_mod.KESIM_BEKLE_SN + 1)
        m.kontrol()
        assert 9 not in m._kesimler
    finally:
        m.kapat()


def test_motor_kontrol_duvar_adimini_bir_kez_loglar(tmp_path, sistem):
    log = []
    m = motor_mod.Motor(str(tmp_path / "t"), "Test", dt.date.fromtimestamp(T0), duzelt=False, ben="Zeynep Ornek",
                        katilimcilar=["Ayse Demir", "Zeynep Ornek"])
    try:
        m.log = log.append
        m.kontrol()
        sistem.adim(+3)
        m.kontrol()
        m.kontrol()
        adim = [x for x in log if "saat adımı" in x]
        assert len(adim) == 1 and "+3.0" in adim[0]
    finally:
        m.kapat()
