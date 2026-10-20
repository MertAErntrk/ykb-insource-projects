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


# --- DbKaynagi / kaynak_kur ---
import pytest

import veri
from veri import DbKaynagi, kaynak_kur


class _Imlec:
    def __init__(self, baglanti):
        self.b = baglanti

    def execute(self, sql, params=None):
        self.b.cagrilar.append((sql, params))

    def fetchone(self):
        return self.b.satir


class _Baglanti:
    def __init__(self, satir):
        self.satir = satir
        self.cagrilar = []

    def cursor(self):
        return _Imlec(self)


class _Clob:
    def __init__(self, s):
        self.s = s

    def read(self):
        return self.s


def _cfg(**sut):
    sutunlar = {"cif": "MUSTERI", "basvuru_no": "BNO", "indata": "INDATA",
                "outdata": "OUTDATA", "action_code": "ACTION_CODE", "tarih": "TRH"}
    sutunlar.update(sut)
    return {"tablolar": {"io_log": "LOG_T", "identity": "", "evaluation": "", "pc_log": "PC_T",
                         "proposals": "PRO_T", "rule_lkp": ""}, "sutunlar": sutunlar}


_SATIR = (_Clob('{"a": 1}'), '{"b": 2}', "RED", dt.date(2025, 5, 4))


def test_db_bul_dict_ve_bind():
    b = _Baglanti(_SATIR)
    r = DbKaynagi(_cfg(), b).bul("987", dt.date(2025, 6, 1))
    assert r == {"CIF": "987", "BasvuruTarihi": "2025-05-04", "Indata": {"a": 1},
                 "Outdata": {"b": 2}, "ActionCode": "RED"}
    sql, params = b.cagrilar[0]
    assert ":cif" in sql and ":tarih" in sql and "987" not in sql
    assert params["cif"] == "987"


def test_db_identity_ve_smile():
    cfg = _cfg()
    cfg["tablolar"]["identity"] = "KIM_T"
    b = _Baglanti(_SATIR)
    DbKaynagi(cfg, b).bul("1")
    assert "KIM_T" in b.cagrilar[0][0]
    DbKaynagi(cfg, b).bul("1", kanal="smile_plus")
    assert "GROUP_PROPOSAL_ID" in b.cagrilar[1][0] and "PC_T" in b.cagrilar[1][0]


def test_db_gecersiz_ad_ve_bos_sutun():
    cfg = _cfg()
    cfg["tablolar"]["io_log"] = "T; DROP TABLE X"
    with pytest.raises(ValueError):
        DbKaynagi(cfg, _Baglanti(_SATIR)).bul("1")
    with pytest.raises(ValueError, match="db.sutunlar.cif boş"):
        DbKaynagi(_cfg(cif=""), _Baglanti(_SATIR)).bul("1")


def test_db_kayit_yok():
    assert DbKaynagi(_cfg(), _Baglanti(None)).bul("1") is None


def _ayar(tmp_path, **ek):
    a = {"basvurular": str(tmp_path), "cif_yolu": "CIF", "tarih_yolu": "BasvuruTarihi"}
    a.update(ek)
    return a


def test_kaynak_kur(tmp_path, monkeypatch):
    k = kaynak_kur(_ayar(tmp_path, kaynak="dosya"))
    assert isinstance(k, DosyaKaynagi) and k.etiket == "dosya"
    bos = _ayar(tmp_path, kaynak="db", db={"hizli_tahsis": {"dsn": ""}})
    assert kaynak_kur(bos).etiket == "dosya (db: dsn boş)"
    dolu = _ayar(tmp_path, kaynak="db", db={"hizli_tahsis": {"dsn": "x"}})
    monkeypatch.setattr(veri, "_surucu", lambda: None)
    assert kaynak_kur(dolu).etiket == "dosya (db: sürücü yok)"
    monkeypatch.setattr(veri, "_surucu", lambda: object())
    assert kaynak_kur(dolu).etiket.startswith("dosya (db: ayar: ")        # tablo/sütun adları boş
    tam = _ayar(tmp_path, kaynak="db", db=dict(_cfg(), hizli_tahsis={"dsn": "x"}))
    assert isinstance(kaynak_kur(tam), DbKaynagi)
