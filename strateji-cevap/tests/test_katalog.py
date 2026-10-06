import datetime as dt

import pytest

import katalog as katalog_mod
from katalog import Katalog, KatalogHatasi


def test_gercek_katalog_yuklenir():
    k = Katalog.yukle()
    assert "H221" in k.kodlar() and "YETKI" in k.kodlar()
    assert k.bul("h221")["kod"] == "H221"
    assert k.varsayilan_kapanis.startswith("Ek sorularınız")


def test_gecerlilik_surumu():
    k = Katalog({"kurallar": [
        {"kod": "H1", "ad": "a", "sinif": "cikti", "cozum": {"tur": "sabit"},
         "gecerlilik": {"baslangic": "2020-01-01", "bitis": "2025-12-31"}, "sablonlar": {"aciklama": {"metin": "eski"}}},
        {"kod": "H1", "ad": "a", "sinif": "cikti", "cozum": {"tur": "sabit"},
         "gecerlilik": {"baslangic": "2026-01-01", "bitis": None}, "sablonlar": {"aciklama": {"metin": "yeni"}}},
    ]})
    assert k.bul("H1", dt.date(2025, 6, 1))["sablonlar"]["aciklama"]["metin"] == "eski"
    assert k.bul("H1", dt.date(2026, 6, 1))["sablonlar"]["aciklama"]["metin"] == "yeni"
    assert k.bul("H1")["sablonlar"]["aciklama"]["metin"] == "yeni"          # tarihsiz: en yeni
    assert k.bul("H1", dt.date(2019, 1, 1)) is None
    assert k.bul("H2") is None


def test_anahtar_kelime():
    k = Katalog.yukle()
    assert k.anahtar_kelime_eslestir("Onay Yetki Seviyesi neden 8?") == ["YETKI"]
    assert k.anahtar_kelime_eslestir("Bağımsız denetim raporu isteniyor") == ["H221"]
    assert k.anahtar_kelime_eslestir("başka bir şey") == []


@pytest.mark.parametrize("kural", [
    {"kod": "X", "ad": "a", "sinif": "cikti", "cozum": {"tur": "sabit"}},                       # sablonlar yok
    {"kod": "X", "ad": "a", "sinif": "baska", "cozum": {"tur": "sabit"}, "sablonlar": {}},      # sınıf tanımsız
    {"kod": "X", "ad": "a", "sinif": "cikti", "cozum": {"tur": "yok"}, "sablonlar": {}},        # tür tanımsız
    {"kod": "X", "ad": "a", "sinif": "cikti", "cozum": {"tur": "liste_kosul"}, "sablonlar": {}},  # liste alanları yok
    {"kod": "X", "ad": "a", "sinif": "cikti", "cozum": {"tur": "sabit"}, "sablonlar": {"a": "düz metin"}},
])
def test_bozuk_katalog_reddedilir(kural):
    with pytest.raises(KatalogHatasi):
        Katalog({"kurallar": [kural]})
