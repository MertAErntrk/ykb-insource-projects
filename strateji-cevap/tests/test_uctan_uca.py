"""15 sentetik Çözüm Merkezi kaydı: motor + ekran uçtan uca (LLM ve ağ yok)."""
import json
import os

import pytest

import ekran
import kayitlar
import motor

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KAYITLAR = kayitlar.yukle()


@pytest.fixture(scope="module")
def m():
    ayar = motor.ayar_oku(os.path.join(KOK, "config.example.json"))
    return motor.Motor.kur(ayar)


@pytest.fixture(scope="module")
def taslaklar(m):
    return {k["Kayıt Numarası"]: m.cevapla(kayitlar.cagri_yap(k)) for k in KAYITLAR}


@pytest.mark.parametrize("kayit", KAYITLAR, ids=[k["Kayıt Numarası"] for k in KAYITLAR])
def test_beklenen_sinif_ve_durum(m, kayit):
    t = m.cevapla(kayitlar.cagri_yap(kayit))
    b = kayit["_beklenen"]
    assert (t.sinif, t.durum) == (b["sinif"], b["durum"])


def test_on_bes_kayit():
    assert len(KAYITLAR) == 15


def test_h221_otomatik(taslaklar):
    t = taslaklar["I1000001"]
    assert t.durum == "otomatik"
    assert t.metin.strip()


def test_yetki_olgulari(taslaklar):
    sayac = 0
    for no, t in taslaklar.items():
        if "YETKI" in t.kurallar and "YETKI" in t.olgular:
            o = t.olgular["YETKI"]
            if "segment" in o:  # veri_eksik dalında matris olguları olmayabilir
                sayac += 1
                assert "tavan" in o, no
    assert sayac >= 8


def test_yetki_senaryolari(taslaklar):
    assert taslaklar["I1000006"].olgular["YETKI"]["tavan"] is True
    assert taslaklar["I1000008"].olgular["YETKI"]["segment"] == "KURUMSAL"
    assert taslaklar["I1000007"].olgular["YETKI"]["hesap_matris"] == 0


def test_ekran_akisi(m, tmp_path):
    yol = str(tmp_path / "onaylar.json")
    uyg = ekran.Uygulama(m, KAYITLAR, yol)
    liste = uyg.liste()
    assert len(liste) == 15
    sayim = {}
    for s in liste:
        sayim[s["durum"]] = sayim.get(s["durum"], 0) + 1
    assert sayim == {"otomatik": 1, "onaya_gitti": 14}

    sonuc = uyg.onayla("I1000004")
    assert sonuc["durum"] == "gonderildi"
    with open(yol, encoding="utf-8") as f:
        assert json.load(f)["I1000004"]["durum"] == "gonderildi"

    yeni = ekran.Uygulama(m, KAYITLAR, yol)
    assert yeni.durum("I1000004") == "gonderildi"
    assert yeni.durum("I1000001") == "otomatik"
