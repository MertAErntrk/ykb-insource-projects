import pytest

pd = pytest.importorskip("pandas")

from analiz_smile import analiz, maskele, rapor, sutun_bul


def test_analiz_sentetik():
    df = pd.DataFrame({
        "Çağrı No": ["SM-1", "SM-2", "SM-3", "SM-4", "SM-5"],
        "Açılış Tarihi": ["2025-01-05", "2025-01-20", "2025-02-03", "03.02.2025", None],
        "Konu": ["H221 kuralı", "Yetki seviyesi", "H221 neden", "Ekran hatası", "Bilanço verisi"],
        "Açıklama": ["10000001 müşterisinde H221 çalıştı", "10000001 onay yetki seviyesi neden 8",
                     "10000003 bağımsız denetim raporu neden isteniyor", "strateji ekranı hata veriyor sonuç gelmedi",
                     "müşterinin bilanço verisi eksik geliyor"],
        "Çözüm Açıklaması": ["cevap 1", "cevap 2", None, "cevap 4", "cevap 5"],
    })
    s = analiz(df)
    assert s["toplam"] == 5 and s["kodlu"] == 3 and s["cevapli"] == 4
    assert s["sutunlar"]["cevap"] == "Çözüm Açıklaması" and s["sutunlar"]["tarih"] == "Açılış Tarihi"
    assert s["sutunlar"]["metin"] == ["Konu", "Açıklama"]
    assert s["aylik"] == {"2025-01": 2, "2025-02": 2, "bilinmiyor": 1}
    assert s["kodlar"][0][:2] == ("H221", 2) and abs(s["kodlar"][0][3] - 40.0) < 0.01
    assert s["siniflar"]["hata"] == 1 and s["siniflar"]["girdi"] == 1
    assert all("10000003" not in o for o in s["kodsuz_ornekler"])
    r = rapor(s)
    assert "| H221 | 2 | 40.0 | 40.0 |" in r and "| 2025-01 | 2 |" in r


def test_sutun_bul_ve_maske():
    assert sutun_bul(["Çağrı No", "Açıklama"], "metin") == "Açıklama"
    assert sutun_bul(["Çağrı No"], "cevap") is None
    assert maskele("CIF 10000004 ve 123") == "CIF ###### ve 123"


def test_metin_sutunu_yoksa_hata():
    with pytest.raises(ValueError):
        analiz(pd.DataFrame({"a": [1]}))
