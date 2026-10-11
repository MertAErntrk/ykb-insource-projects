"""Saat adimi senaryosu (Faz 2 ek): duvar saati toplanti ortasinda +3 / -6 / +3 sn sicrar (200, 400, 650. sn).

Eskiden ses ankrajlari en gec 60 sn sonra, altyazi okumalari hemen yeni duvar saatine tasindigindan iki zaman cizelgesi
uyusmuyor, bagimsiz dogrulayici 20 tohumda 11 kesin yanlis konusmaci uretiyordu. Artik tum zaman karsilastirmalari
saat.simdi() (monotonik tabanli) uzerindedir. Duzenek ses sayaci ankrajlarini (ses.AkisYakalayici) ve altyazi
okumasini (Yakalayici.oku) gercek kod yollarindan damgalar, time.time'i adimli duvar saatine baglar.

Kapilar: kesin gosterilen yanlis ad 0; sonuc adimsiz kosumla ayni; 3 adim algilanir; ts yerel saat dogru;
negatif kontrol: simdi() duvar saatine baglanirsa (eski davranis) kapi bozulur."""
import socket

import pytest

import duzenek as D

SEEDLER = D.SEDLER + D.SEDLER_GENIS
SC = D.SENARYOLAR_EK["saat_adimi"]
ADIMSIZ = {k: v for k, v in SC.items() if k != "saat_adimlari"}
KARSILASTIR = ("klip", "dogru", "yanlis", "kirli", "soru", "adayli", "devir", "ayni", "cift_ut", "yanki_kalan", "satir",
               "bekleyen_kalan", "capa")


@pytest.fixture(autouse=True)
def _agsiz(monkeypatch):
    def yasak(*_a, **_k):
        raise RuntimeError("duzenek ag erisimi yok")
    monkeypatch.setattr(socket.socket, "connect", yasak)
    monkeypatch.setattr(socket, "create_connection", yasak)


_onbellek = {}


def olc(seed, **ek):
    anahtar = (seed, tuple(sorted(ek.items())))
    if anahtar not in _onbellek:
        _onbellek[anahtar] = D.calistir(seed=seed, **dict(SC, **ek))
    return _onbellek[anahtar]


def adimsiz(seed):
    if ("adimsiz", seed) not in _onbellek:
        _onbellek[("adimsiz", seed)] = D.calistir(seed=seed, **ADIMSIZ)
    return _onbellek[("adimsiz", seed)]


@pytest.fixture(scope="module", autouse=True)
def _onisitma(request):
    """Tam kosumda adimli / adimsiz / eski-davranis kosumlari paralel (fork) onceden hesaplanir (test_e2e ile ayni)."""
    kosan = [i for i in request.session.items if i.module is request.module]
    if len(kosan) > 30 and D.paralel_sayisi() > 1:
        isler = [((s, ()), dict(SC, seed=s)) for s in SEEDLER]
        isler += [(("adimsiz", s), dict(ADIMSIZ, seed=s)) for s in SEEDLER]
        isler += [((s, (("saat_eski", True),)), dict(SC, seed=s, saat_eski=True)) for s in D.SEDLER_GENIS + (7,)]
        for anahtar, (sonuc, hata) in D.toplu(isler).items():
            if hata is None:
                _onbellek[anahtar] = sonuc


@pytest.mark.parametrize("seed", SEEDLER)
def test_saat_adiminda_kesin_yanlis_sifir(seed):
    s = olc(seed)
    assert s.get("yanlis", 0) == 0, [a for a in s["ayrinti"] if a[0] == "YANLIS"]
    assert s.get("cift_ut", 0) == 0 and s.get("yanki_kalan", 0) == 0
    assert s.get("sizinti", 0) == 0


@pytest.mark.parametrize("seed", SEEDLER)
def test_saat_adimi_sonucu_adimsiz_kosumla_ayni(seed):
    """Zaman cizelgeleri adimdan etkilenmez: kararlarin sayilari adimsiz ayni senaryoyla birebir."""
    a, b = olc(seed), adimsiz(seed)
    assert {k: a.get(k) for k in KARSILASTIR} == {k: b.get(k) for k in KARSILASTIR}


@pytest.mark.parametrize("seed", D.SEDLER)
def test_adimlar_algilanir_ts_yerel_saat_dogru(seed):
    s = olc(seed)
    assert s["saat_adim_algilanan"] == 3                      # her adim bir kez
    assert s["ts_olculen"] >= 50                              # kontrol bos degil
    assert s["ts_hatali"] == 0 and s["ts_yakin_hatali"] == 0


def test_negatif_kontrol_eski_davranis_kapiyi_bozar():
    """simdi() = duvar saati (eski): ses ankrajlari 60 sn gec kayar -> dogru atama belirgin duser, kesin yanlis olusur."""
    yanlis = kayip = 0
    for seed in D.SEDLER_GENIS:
        yeni, eski = olc(seed), olc(seed, saat_eski=True)
        yanlis += eski.get("yanlis", 0)
        kayip += yeni.get("dogru", 0) - eski.get("dogru", 0)
        assert yeni.get("yanlis", 0) == 0
    assert yanlis > 0 and kayip > 100, (yanlis, kayip)


def test_duzenek_modulu_geri_yukler():
    import saat
    import time
    g = time.time
    olc(7, saat_eski=True)
    assert time.time is g and abs(saat.simdi() - time.time()) < 2
