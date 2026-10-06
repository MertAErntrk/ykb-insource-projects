import datetime as dt
import json
import os

from veri import DosyaKaynagi


def _yaz(klasor, ad, veri):
    with open(os.path.join(klasor, ad), "w", encoding="utf-8") as f:
        json.dump(veri, f)


def test_tarihe_gore_secim(tmp_path):
    k = str(tmp_path)
    _yaz(k, "a.json", {"CIF": "1", "BasvuruTarihi": "2025-01-10", "x": "eski"})
    _yaz(k, "b.json", {"CIF": "1", "BasvuruTarihi": "2025-06-10", "x": "yeni"})
    _yaz(k, "c.json", {"CIF": "2", "BasvuruTarihi": "2025-03-01"})
    _yaz(k, "bozuk.json", "düz metin")
    with open(os.path.join(k, "d.json"), "w") as f:
        f.write("{bozuk")
    kaynak = DosyaKaynagi(k)
    assert kaynak.bul("1")["x"] == "yeni"
    assert kaynak.bul("1", dt.date(2025, 3, 1))["x"] == "eski"        # çağrı tarihinden önceki son başvuru
    assert kaynak.bul("1", dt.date(2024, 1, 1))["x"] == "yeni"        # öncesi yoksa en yeni
    assert kaynak.bul("2")["CIF"] == "2" and kaynak.bul("3") is None
