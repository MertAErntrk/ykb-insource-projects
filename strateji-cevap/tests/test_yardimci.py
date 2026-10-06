import datetime as dt

from yardimci import ay_yil, tarih_ayristir, tl_bicim, tr_buyuk_ilk, tr_kucuk, yil_once, yol_al


def test_tarih_ayristir_bicimler():
    for s in ("2025-04-12", "2025-04-12T10:00:00", "2025-04-12 10:00:00.123", "12.04.2025", "12/04/2025", "20250412"):
        assert tarih_ayristir(s) == dt.date(2025, 4, 12), s
    assert tarih_ayristir(dt.datetime(2025, 4, 12, 9)) == dt.date(2025, 4, 12)
    assert tarih_ayristir(None) is None and tarih_ayristir("") is None and tarih_ayristir("abc") is None


def test_ay_yil_ve_yil_once():
    assert ay_yil(dt.date(2025, 4, 12)) == "Nisan 2025"
    assert ay_yil(dt.date(2024, 1, 1)) == "Ocak 2024"
    assert yil_once(dt.date(2025, 7, 10), 5) == dt.date(2020, 7, 10)
    assert yil_once(dt.date(2024, 2, 29), 1) == dt.date(2023, 2, 28)


def test_tl_bicim():
    assert tl_bicim(1234567.89) == "1.234.567,89"
    assert tl_bicim(800000) == "800.000,00"
    assert tl_bicim("x") == "x" and tl_bicim(None) == "None"


def test_tr_kucuk_ve_buyuk():
    assert tr_kucuk("ISTANBUL İzmir") == "ıstanbul izmir"
    assert tr_buyuk_ilk("iki limit") == "İki limit"
    assert tr_buyuk_ilk("") == ""


def test_yol_al():
    veri = {"Outdata": {"CORPCOMMMATRIXOUTPUTS": {"NOMINALLIMIT": 5.0}, "HRules": [{"Code": "H221"}]}}
    assert yol_al(veri, "Outdata.CORPCOMMMATRIXOUTPUTS.NOMINALLIMIT") == 5.0
    assert yol_al(veri, "outdata.corpcommmatrixoutputs.nominallimit") == 5.0      # büyük/küçük harf duyarsız
    assert yol_al(veri, "Outdata.HRules.0.Code") == "H221"
    assert yol_al(veri, "Outdata.Yok", "v") == "v" and yol_al(veri, "", "v") == "v"
    assert yol_al(veri, "Outdata.HRules.5.Code") is None
