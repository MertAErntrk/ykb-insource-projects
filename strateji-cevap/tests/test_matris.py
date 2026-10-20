import pytest

import katalog as katalog_mod
from matris import MatrisHatasi, nihai_seviye, seviye_bul

# Eski "seviyeler" biçimi (sentetik); geriye uyumluluk için okunmaya devam eder.
ESKI = {"seviyeler": [
    {"seviye": 1, "nominal_ust": 1000000, "agirlikli_ust": 500000},
    {"seviye": 2, "nominal_ust": 3000000, "agirlikli_ust": 1000000},
    {"seviye": 3, "nominal_ust": 10000000, "agirlikli_ust": 5000000},
    {"seviye": 4, "nominal_ust": 25000000, "agirlikli_ust": 12500000},
    {"seviye": 5, "nominal_ust": 50000000, "agirlikli_ust": 25000000},
    {"seviye": 6, "nominal_ust": 100000000, "agirlikli_ust": 50000000},
    {"seviye": 7, "nominal_ust": 250000000, "agirlikli_ust": 125000000},
    {"seviye": 8, "nominal_ust": None, "agirlikli_ust": None},
]}
M = katalog_mod.matris_yukle()


def test_sentetik_ornek_matriste_3():
    seviye, ayrinti = seviye_bul(ESKI, 4250000.00, 1375000.00)
    assert seviye == 3
    assert ayrinti == {"nominal": 3, "agirlikli": 3, "segment": None, "tavan_uygulandi": False}


def test_buyuk_olan_kazanir():
    assert seviye_bul(ESKI, 2400000, 800000)[0] == 2
    assert seviye_bul(ESKI, 500000, 4000000)[0] == 3        # ağırlıklı limit seviyeyi yukarı çeker
    assert seviye_bul(ESKI, 10 ** 12, 1)[0] == 8            # sınırsız son satır
    assert seviye_bul(ESKI, 2400000, 800000, "OBI")[1]["segment"] is None   # eski biçimde segment yok sayılır


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


def test_v2_ust_alanlar():
    assert M["kaynak"] == "sentetik" and M["para_birimi"] == "USD" and M["otomatik_onay_seviyesi"] == 0
    assert M["unvan_seviye"]["Otomatik Onay"] == 0 and M["unvan_seviye"]["Yönetim Kurulu"] == 8
    assert set(M["segmentler"]) == {"TICARI", "KURUMSAL", "OBI"}


@pytest.mark.parametrize("segment, nominal, agirlikli, beklenen, tavan", [
    ("TICARI", 4250000, 1375000, 3, False),
    ("TICARI", 2400000, 800000, 2, True),        # ağırlıklı 3 verir, nominal<3M tavanı 2'de keser
    ("TICARI", 200000, 100000, 0, False),        # Otomatik Onay; tavan koşulu sağlanır ama düşürmez
    ("TICARI", 800000, 450000, 1, False),
    ("TICARI", 2000000, 12000000, 2, True),      # tavansız 4 olurdu
    ("TICARI", 10 ** 12, 1, 8, False),           # sınırsız son satır
    ("KURUMSAL", 4250000, 1375000, 1, False),
    ("OBI", 1200000, 9000000, 2, True),
])
def test_v2_segment_seviyeleri(segment, nominal, agirlikli, beklenen, tavan):
    seviye, ayrinti = seviye_bul(M, nominal, agirlikli, segment)
    assert seviye == beklenen
    assert ayrinti["segment"] == segment and ayrinti["tavan_uygulandi"] is tavan


def test_v2_tavan_ayrintisi():
    seviye, ayrinti = seviye_bul(M, 2000000, 12000000, "TICARI")
    assert (seviye, ayrinti["nominal"], ayrinti["agirlikli"]) == (2, 2, 4)


def test_v2_segment_zorunlu():
    with pytest.raises(MatrisHatasi):
        seviye_bul(M, 4250000, 1375000)
    with pytest.raises(MatrisHatasi):
        seviye_bul(M, 4250000, 1375000, "BILINMEYEN")


def test_v2_gecersiz_kosul_hata():
    m = {"segmentler": {"X": [{"sira": 1, "seviye": 1, "nominal_ust": 10, "agirlikli_ust": 10,
                               "kosul": {"alan": "nominal", "op": "==", "deger": 5}}]}}
    with pytest.raises(MatrisHatasi):
        seviye_bul(m, 1, 1, "X")
