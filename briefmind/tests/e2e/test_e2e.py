"""Uctan uca duzenek kapilari (MIMARI_v3 §8). Sentetik, ag yok, repo'ya dosya yazmaz; tum senaryolar ~3 sn.

(a) K5 kapali: transkriptte altyazi kaynakli satir yok
(b) altyazi kapali (2 dk, 300-420 sn): yanlis konusmaci <= 5 (Faz0 oncesi 9-14)
(c) ikisi modu disinda cift satir yok (olculen ihlaller xfail(strict=True); Faz1'de duser)
(d) her senaryo icin olculen metrikler ust sinir (Faz0 tabani, Faz1'de sikilasir)
"""
import os
import socket

import pytest

import duzenek as D

SEEDLER = D.SEDLER

# Faz0 tabani: her senaryonun (seed'ler uzerinden) en kotu olculen degeri. Deterministik; artarsa kapi kirilir.
TABAN = {
    "gecikme0": dict(yanlis=0, soru=0, eksik=2, cift=0, bozulan=13, SER=0.0, son=18, altyazi=10),
    "gecikme2": dict(yanlis=0, soru=0, eksik=3, cift=2, bozulan=15, SER=0.0, son=21, altyazi=5),
    "altyazi_kapali": dict(yanlis=2, soru=10, eksik=4, cift=1, bozulan=15, SER=0.169, son=21, altyazi=4),
    "altyazi_kapali_3_5": dict(yanlis=4, soru=11, eksik=4, cift=1, bozulan=15, SER=0.2, son=21, altyazi=4),
    "ikisi": dict(yanlis=2, soru=10, eksik=3, cift=5, bozulan=15, SER=0.164, son=21, altyazi=10),
    "cakisma_yok": dict(yanlis=2, soru=0, eksik=1, cift=1, bozulan=16, SER=0.027, son=21, altyazi=1),
    "sozluksuz": dict(yanlis=0, soru=0, eksik=3, cift=2, bozulan=0, SER=0.0, son=13, altyazi=5),
    "k5_kapali": dict(yanlis=0, soru=0, eksik=7, cift=0, bozulan=15, SER=0.0, son=21, altyazi=0),
    "k5_kapali_altyazi_kapali": dict(yanlis=2, soru=10, eksik=7, cift=0, bozulan=15, SER=0.174, son=21, altyazi=0),
    "k5_kapali_tekrar": dict(yanlis=10, soru=1, eksik=8, cift=0, bozulan=14, SER=0.145, son=30, altyazi=0),
    "tekrar": dict(yanlis=9, soru=1, eksik=3, cift=4, bozulan=14, SER=0.129, son=30, altyazi=7),
}
SOZLUK_MIKRO_ZARAR_TABAN = 2       # komite->commit, kampanyacı->kampanya (Faz2'de 0)

# (c) kapisi: Faz0'da duplicate olcumu (seed 7, 11, 23 sirasiyla) -- kapi kirik, xfail
CIFT_BILINEN = {
    "gecikme2": "cift (7,11,23) = 0,0,2",
    "altyazi_kapali": "cift (7,11,23) = 0,0,1",
    "altyazi_kapali_3_5": "cift (7,11,23) = 0,0,1",
    "cakisma_yok": "cift (7,11,23) = 0,0,1",
    "sozluksuz": "cift (7,11,23) = 0,0,2",
    "tekrar": "cift (7,11,23) = 1,2,4",
}
CIFT_SENARYO = [ad for ad, sc in D.SENARYOLAR.items() if sc["kaynak"] != "ikisi"]
K5_KAPALI = ["k5_kapali", "k5_kapali_altyazi_kapali", "k5_kapali_tekrar"]


@pytest.fixture(autouse=True)
def _agsiz(monkeypatch):
    """Duzenek ag acamaz: baglanti denemesi testi hemen kirar (sessiz yutulmaz)."""
    def yasak(*_a, **_k):
        raise RuntimeError("duzenek ag erisimi yok")
    monkeypatch.setattr(socket.socket, "connect", yasak)
    monkeypatch.setattr(socket, "create_connection", yasak)


_onbellek = {}


def olc(ad, seed):
    """Senaryo sonucu (ayni (ad, seed) bir kez kosar; testler ayni veriyi paylasir)."""
    if (ad, seed) not in _onbellek:
        _onbellek[(ad, seed)] = D.calistir(seed=seed, **D.SENARYOLAR[ad])
    return _onbellek[(ad, seed)]


# ---------------- (a) K5 kapali: altyazi metni transkripte girmez ----------------

@pytest.mark.parametrize("seed", SEEDLER)
@pytest.mark.parametrize("ad", K5_KAPALI)
def test_a_k5_kapali_altyazi_satiri_yok(ad, seed):
    assert olc(ad, seed)["altyazi_metni"] == 0


# ---------------- (b) altyazi kapali: yanlis konusmaci sayisi ----------------

@pytest.mark.parametrize("seed", SEEDLER)
def test_b_altyazi_kapali_yanlis_konusmaci(seed):
    s = olc("altyazi_kapali", seed)
    assert s["yanlis"] <= 5, f"seed={seed} yanlis={s['yanlis']} (sinir 5)"


# ---------------- (c) ikisi disinda cift satir yok ----------------

def _cift_parametreleri():
    for ad in CIFT_SENARYO:
        if ad in CIFT_BILINEN:
            yield pytest.param(ad, marks=pytest.mark.xfail(
                strict=True, reason=f"Faz0 olcumu {CIFT_BILINEN[ad]}; Faz1 konusmaci kapisi ile duser"))
        else:
            yield ad


@pytest.mark.parametrize("ad", list(_cift_parametreleri()))
def test_c_cift_satir_yok(ad):
    for seed in SEEDLER:
        cift = olc(ad, seed)["cift_utterance"]
        assert cift == [], f"seed={seed} cift={cift}"


# ---------------- (d) diger metrikler: Faz0 tabani ust sinir ----------------

@pytest.mark.parametrize("ad", list(D.SENARYOLAR))
def test_d_regresyon_ust_sinir(ad):
    # Faz0 tabani, Faz1'de sikilasir: sinirlar senaryonun seed'ler uzerindeki en kotu olculen degeridir
    sinir = TABAN[ad]
    for seed in SEEDLER:
        s = olc(ad, seed)
        olculen = dict(yanlis=s["yanlis"], soru=s["soru"], eksik=len(s["eksik_utterance"]),
                       cift=len(s["cift_utterance"]), bozulan=s["satir_metni_bozulan"],
                       SER=s["konusmaci_hata_orani"], son=s["son_kelime_hatasi"], altyazi=s["altyazi_metni"])
        for metrik, ust in sinir.items():
            assert olculen[metrik] <= ust + 1e-9, f"seed={seed} {metrik}={olculen[metrik]} > {ust}"


def test_sozluk_mikro_zarar_ust_sinir():
    # Faz0 tabani (komite->commit, kampanyacı->kampanya); Faz2 terim kapisi ile 0 olur
    s = D.sozluk_mikro()
    assert s["toplam"] == 18
    assert len(s["zarar"]) <= SOZLUK_MIKRO_ZARAR_TABAN, s["zarar"]


# ---------------- hijyen: cagri repo'ya / cwd'ye dosya yazmaz ----------------

def test_dosya_yazmaz(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    D.calistir(seed=7, **D.SENARYOLAR["gecikme2"])
    assert os.listdir(str(tmp_path)) == []
