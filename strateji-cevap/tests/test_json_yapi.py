import json

import json_yapi

KAYIT = {"id": "10000001", "tutar": 12.5, "adet": 3, "aktif": True, "not": None,
         "ic": {"a": {"b": {"c": "x"}}}, "kalemler": [{"ad": "Deneme", "n": 1}, {"ad": "Diger"}], "bos": []}


def test_agac_turler_ve_liste():
    a = json_yapi.agac(KAYIT)
    assert a["id"] == "str" and a["tutar"] == "float" and a["adet"] == "int"
    assert a["aktif"] == "bool" and a["not"] == "null" and a["bos"] == {"tur": "list[0]"}
    assert a["kalemler"] == {"tur": "list[2]", "eleman": {"ad": "str", "n": "int"}}
    assert a["ic"]["a"]["b"]["c"] == "str"


def test_deger_sizdirmaz():
    m = json_yapi.metin(json_yapi.agac(KAYIT))
    assert "10000001" not in m and "Deneme" not in m and "12.5" not in m
    assert "kalemler: list[2]" in m and "  ad: str" in m and "id: str" in m


def test_derinlik_siniri():
    a = json_yapi.agac(KAYIT, derinlik=1)
    assert a["ic"] == {"a": "..."}
    assert a["kalemler"]["eleman"] == "..."
    assert "..." in json_yapi.metin(a)


def test_cli_cikti_ve_jsonl(tmp_path):
    g = tmp_path / "k.jsonl"
    g.write_text(json.dumps({"k": "10000001"}) + "\n" + json.dumps({"z": 1}) + "\n", encoding="utf-8")
    c = tmp_path / "agac.txt"
    assert json_yapi.main([str(g), "--cikti", str(c)]) == 0
    t = c.read_text(encoding="utf-8")
    assert "k: str" in t and "10000001" not in t and "z:" not in t
    assert json_yapi.main([str(tmp_path / "yok.json")]) == 2
