"""
saat.py — Adima dayanikli zaman kaynagi (MIMARI_v3 Faz 2 ek: saat adimi).

Sorun: duvar saati (time.time) toplanti sirasinda sicrayabilir (NTP/w32time adimi, elle degisiklik). Ses sayaci
epoch'lari ve altyazi okuma epoch'lari farkli anlarda duvar saatine baglanirsa iki zaman cizelgesi 60 sn'ye kadar
birbirini tutmaz (bagimsiz dogrulayici: 20 tohumda 11 kesin yanlis konusmaci).

Cozum: simdi() = surec basindaki duvar saati + time.monotonic() ile gecen sure. Saat adimlari simdi()'yi etkilemez;
ses ankrajlari, okuma bas/bit, konusmaci/motor son tarihleri hep bunu kullanir. Kullaniciya gosterilen HH:MM:SS
yerel saat dogru kalsin diye duvar(t) simdi epoch'unu o anki duvar ofsetiyle duvar epoch'una cevirir; duvardan(t) tersi
(ekrandaki/disaridan gelen HH:MM:SS -> simdi epoch'u).

Test/replay dikisi: modullerde `saat.simdi()` her cagrida modul niteliginden cozulur. sanal_kur(fn) simdi/duvar/
duvardan'i degistirir (duvar ofseti 0) ve geri alma fonksiyonu dondurur.
"""
import datetime as dt
import threading
import time

ADIM_ESIK_SN = 0.5            # duvar - monotonic farki bu kadar sicrarsa 'saat adimi'
OFSET_ADIM_SN = 0.1           # gosterim ofseti bu adimla yuvarlanir (mikro kayma ekran saniyesini oynatmasin)

LOG_FN = None                 # motor ayarlar: saat adimi bir kez uyari olarak Olaylar'a

_kilit = threading.Lock()
_duvar0 = time.time()
_mono0 = time.monotonic()
_son_fark = _duvar0 - _mono0          # son denetimdeki (duvar - monotonic)
_adim_sayisi = 0
_son_adim = 0.0


def _simdi_gercek():
    return _duvar0 + (time.monotonic() - _mono0)


simdi = _simdi_gercek


def _ofset():
    """Su anki (duvar - simdi) ofseti, OFSET_ADIM_SN'ye yuvarlanmis."""
    return round((time.time() - _simdi_gercek()) / OFSET_ADIM_SN) * OFSET_ADIM_SN


def _duvar_gercek(t):
    return t + _ofset()


def _duvardan_gercek(t):
    return t - _ofset()


duvar = _duvar_gercek
duvardan = _duvardan_gercek


def sifirla():
    """Ankrajlari simdiki duvar saatiyle yeniden kurar (testler; sanal time.time/monotonic yamandiktan sonra)."""
    global _duvar0, _mono0, _son_fark, _adim_sayisi, _son_adim, simdi, duvar, duvardan
    with _kilit:
        _duvar0, _mono0 = time.time(), time.monotonic()
        _son_fark = _duvar0 - _mono0
        _adim_sayisi, _son_adim = 0, 0.0
        simdi, duvar, duvardan = _simdi_gercek, _duvar_gercek, _duvardan_gercek


def yerel(t):
    """simdi epoch'u -> yerel datetime (ekran/dosya adi/ts icin)."""
    return dt.datetime.fromtimestamp(duvar(t))


def ts(t):
    """simdi epoch'u -> yerel 'HH:MM:SS'."""
    return yerel(t).strftime("%H:%M:%S")


def epoch(d):
    """yerel datetime (yerel duvar saati) -> simdi epoch'u."""
    return duvardan(d.timestamp())


def adim_denetle():
    """Duvar saati sicradi mi? Son denetimden beri (duvar - monotonic) farki ADIM_ESIK_SN'den fazla degistiyse
    adimi (sn, +ileri) dondurur, bir kez loglar; yoksa 0.0. Yavas NTP kaymasi esigin altinda kalir.
    simdi() adimdan etkilenmez; bu yalniz tani/log icindir. Sanal saatte (sanal_kur) her zaman 0.0."""
    global _son_fark, _adim_sayisi, _son_adim
    if simdi is not _simdi_gercek:
        return 0.0
    fark = time.time() - time.monotonic()
    with _kilit:
        d = fark - _son_fark
        if abs(d) <= ADIM_ESIK_SN:
            return 0.0
        _son_fark = fark
        _adim_sayisi += 1
        _son_adim = d
        log = LOG_FN
    if log:
        try:
            log(f"saat adımı algılandı ({d:+.1f} sn): zaman çizelgesi monotonik saatten sürüyor, etkilenmedi")
        except Exception:
            pass
    return d


def adim_sayisi():
    return _adim_sayisi


def sanal_kur(fn):
    """Test/replay: simdi() -> fn() (duvar ofseti 0: duvar/duvardan kimlik). Dondurur: geri alma fonksiyonu.
    Adim senaryosu icin duvar/duvardan elle de degistirilebilir (modul nitelikleri)."""
    global simdi, duvar, duvardan
    eski = (simdi, duvar, duvardan)
    simdi, duvar, duvardan = fn, (lambda t: t), (lambda t: t)

    def geri():
        global simdi, duvar, duvardan
        simdi, duvar, duvardan = eski
    return geri
