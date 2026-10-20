"""matris_excel.py testleri: sentetik çalışma kitabı (genel adlar, uydurma sayılar)."""
import json
import os
import sys

import pytest

openpyxl = pytest.importorskip("openpyxl")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matris  # noqa: E402
import matris_excel  # noqa: E402


@pytest.fixture
def excel(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ST X1 TR"
    ws["B3"], ws["C3"] = "Segment", "Unvan"
    ws["B4"] = "TİCARİ"
    ws.merge_cells("B4:B8")
    ws["B9"] = "KOBİ/422 TARIM"
    ws.merge_cells("B9:B11")
    satirlar = [
        (4, "Otomatik Onay", None, 100000, 250000),
        (5, "Şube Müdürü", None, "500.000", "1.000.000"),
        (6, "Bölge Direktörü", "Nominal < 3.000.000 USD", "-", 3000000),
        (7, None, "Nominal > 3.000.000 USD", 1000000, 3000000),
        (8, "Yönetim Kurulu", None, None, None),
        (9, "Otomatik Onay", None, 50000, "abc"),
        (10, "Şube Müdürü", None, 1000, 2000),
        (11, "Yönetim Kurulu", None, None, None),
    ]
    for r, u, d, e, f in satirlar:
        ws["C{}".format(r)], ws["D{}".format(r)] = u, d
        ws["E{}".format(r)], ws["F{}".format(r)] = e, f
    yol = tmp_path / "m.xlsx"
    wb.save(str(yol))
    return str(yol)


def test_oku_yapi(excel):
    m, gecersiz = matris_excel._oku_ic(excel)
    assert m["kaynak"] == "excel" and m["surum"].startswith("excel-")
    assert list(m["segmentler"]) == ["TICARI", "KOBI_422_TARIM"]
    assert [len(v) for v in m["segmentler"].values()] == [5, 3]
    t = m["segmentler"]["TICARI"]
    assert [s["seviye"] for s in t] == [0, 1, 2, 2, 3]
    assert t[1]["nominal_ust"] == 1000000 and t[1]["agirlikli_ust"] == 500000
    assert t[2]["kosul"] == {"alan": "nominal", "op": "<", "deger": 3000000} and t[2]["tavan"] is True
    assert t[2]["agirlikli_ust"] is None
    assert t[3]["kosul"]["op"] == ">=" and "tavan" not in t[3]
    assert t[4]["nominal_ust"] is None and t[4]["agirlikli_ust"] is None and "kosul" not in t[4]
    assert gecersiz == ["F9"]
    assert matris_excel.oku(excel)["segmentler"].keys() == m["segmentler"].keys()


def test_seviye_bul(excel):
    m = matris_excel.oku(excel)
    s, bilgi = matris.seviye_bul(m, 2000000, 5000000, "TICARI")
    assert s == 2 and bilgi["tavan_uygulandi"] is True
    s, bilgi = matris.seviye_bul(m, 500000, 100000, "TICARI")
    assert s == 1 and bilgi["tavan_uygulandi"] is False


def test_unvan_seviye_override(excel):
    m = matris_excel.oku(excel, unvan_seviye={"Yönetim Kurulu": 8})
    assert m["segmentler"]["TICARI"][-1]["seviye"] == 8


def test_cli(excel, tmp_path, capsys):
    cikti = tmp_path / "alt" / "m.json"
    ortak = ["--excel", excel, "--cikti", str(cikti), "--config", str(tmp_path / "yok.json")]
    # config yok dosyası hata verir; geçerli boş config kullan
    cfg = tmp_path / "c.json"
    cfg.write_text("{}", encoding="utf-8")
    ortak[-1] = str(cfg)
    dog = ["--dogrula", "nominal=2000000", "agirlikli=5000000", "segment=TICARI"]
    assert matris_excel.main(ortak + dog + ["beklenen=2"]) == 0
    out = capsys.readouterr().out
    assert "segment: 2, satır: 8, çevrilemeyen: F9" in out and "doğrulama geçti" in out
    assert "Şube" not in out and "3000000" not in out
    assert matris_excel.main(ortak + dog + ["beklenen=3"]) == 1
    assert "doğrulama kalmadı: hesap 2, beklenen 3" in capsys.readouterr().out
    with open(str(cikti), encoding="utf-8") as f:
        v = json.load(f)
    for k in ("surum", "kaynak", "para_birimi", "agirlikli_alan", "otomatik_onay_seviyesi",
              "unvan_seviye", "segmentler"):
        assert k in v
    assert v["kaynak"] == "excel"
