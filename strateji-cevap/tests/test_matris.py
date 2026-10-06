import pytest

import katalog as katalog_mod
from matris import MatrisHatasi, nihai_seviye, seviye_bul


def test_sentetik_ornek_matriste_3():
    m = katalog_mod.matris_yukle()
    seviye, ayrinti = seviye_bul(m, 4250000.00, 1375000.00)
    assert seviye == 3 and ayrinti == {"nominal": 3, "agirlikli": 3}


def test_buyuk_olan_kazanir():
    m = katalog_mod.matris_yukle()
    assert seviye_bul(m, 2400000, 800000)[0] == 2
    assert seviye_bul(m, 500000, 4000000)[0] == 3           # ağırlıklı limit seviyeyi yukarı çeker
    assert seviye_bul(m, 10 ** 12, 1)[0] == 8               # sınırsız son satır


def test_nihai_seviye_max():
    assert nihai_seviye(3, 8) == 8
    assert nihai_seviye(3, None) == 3
    assert nihai_seviye(3, 2) == 3


def test_sinirsiz_satir_yoksa_hata():
    m = {"seviyeler": [{"seviye": 1, "nominal_ust": 10, "agirlikli_ust": 10}]}
    with pytest.raises(MatrisHatasi):
        seviye_bul(m, 11, 1)
    with pytest.raises(MatrisHatasi):
        seviye_bul({"seviyeler": []}, 1, 1)
