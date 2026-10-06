"""
katalog.py — kural kataloğunu (katalog/kurallar.json) ve yetki matrisini (katalog/yetki_matrisi.json)
okur, biçimini doğrular, başvuru tarihinde geçerli kural sürümünü seçer.

Katalog biçimi:
  {"surum": "...", "varsayilan_kapanis": "...",
   "kurallar": [ {"kod", "ad", "sinif", "aciklama", "gecerlilik": {"baslangic", "bitis"},
                  "cozum": {"tur": liste_kosul | yetki_matrisi | sabit, ...},
                  "sablonlar": {"<ad>": {"metin": "...{alan}...", "onay": "taslak" | "<kim, ne zaman>"}},
                  "kapanis": "...", "anahtar_kelimeler": ["..."]} ] }

Kural değişince eski kayıt silinmez: 'bitis' tarihi yazılır, aynı kodla yeni kayıt eklenir.
Böylece eski bir başvuru için cevap, o tarihte geçerli kurala göre üretilir (toplantı: kural seti değişken).
"""
import datetime as dt
import json
import os
from typing import List, Optional

from yardimci import tarih_ayristir, tr_kucuk

KOK = os.path.dirname(os.path.abspath(__file__))
KATALOG_YOLU = os.path.join(KOK, "katalog", "kurallar.json")
MATRIS_YOLU = os.path.join(KOK, "katalog", "yetki_matrisi.json")

COZUM_TURLERI = ("liste_kosul", "yetki_matrisi", "sabit")
SINIFLAR = ("girdi", "cikti", "hata")


class KatalogHatasi(ValueError):
    pass


class Katalog:
    def __init__(self, veri: dict):
        self.surum = veri.get("surum", "")
        self.varsayilan_kapanis = veri.get("varsayilan_kapanis", "")
        self.kurallar: List[dict] = list(veri.get("kurallar", []))
        self._dogrula()

    @classmethod
    def yukle(cls, yol: str = KATALOG_YOLU) -> "Katalog":
        with open(yol, encoding="utf-8") as f:
            return cls(json.load(f))

    def _dogrula(self) -> None:
        for k in self.kurallar:
            for alan in ("kod", "ad", "sinif", "cozum", "sablonlar"):
                if alan not in k:
                    raise KatalogHatasi("{}: '{}' alanı eksik".format(k.get("kod", "?"), alan))
            if k["sinif"] not in SINIFLAR:
                raise KatalogHatasi("{}: sınıf {!r} tanımsız".format(k["kod"], k["sinif"]))
            tur = k["cozum"].get("tur")
            if tur not in COZUM_TURLERI:
                raise KatalogHatasi("{}: bilinmeyen çözüm türü {!r}".format(k["kod"], tur))
            if tur == "liste_kosul":
                for alan in ("liste", "kosul", "tarih_alani"):
                    if alan not in k["cozum"]:
                        raise KatalogHatasi("{}: liste_kosul için '{}' gerekli".format(k["kod"], alan))
            for ad, s in k["sablonlar"].items():
                if not isinstance(s, dict) or "metin" not in s:
                    raise KatalogHatasi("{}: şablon {!r} {{'metin', 'onay'}} sözlüğü olmalı".format(k["kod"], ad))

    def kodlar(self) -> List[str]:
        return sorted({k["kod"].upper() for k in self.kurallar})

    def bul(self, kod: str, tarih: Optional[dt.date] = None) -> Optional[dict]:
        """kod için tarih'te geçerli kaydı döndürür; tarih verilmezse en yeni başlangıçlı kayıt."""
        adaylar = [k for k in self.kurallar if k["kod"].upper() == kod.upper()]
        if not adaylar:
            return None
        if tarih is None:
            return max(adaylar, key=lambda k: tarih_ayristir((k.get("gecerlilik") or {}).get("baslangic")) or dt.date.min)
        for k in adaylar:
            g = k.get("gecerlilik") or {}
            bas = tarih_ayristir(g.get("baslangic")) or dt.date.min
            bit = tarih_ayristir(g.get("bitis")) or dt.date.max
            if bas <= tarih <= bit:
                return k
        return None

    def anahtar_kelime_eslestir(self, metin: str) -> List[str]:
        """Çağrı metninde geçen anahtar kelimelere göre kural kodları (kodu yazılmamış sorular için)."""
        m = tr_kucuk(metin)
        bulunan: List[str] = []
        for k in self.kurallar:
            for kelime in k.get("anahtar_kelimeler", []):
                if tr_kucuk(kelime) in m and k["kod"].upper() not in bulunan:
                    bulunan.append(k["kod"].upper())
        return bulunan

    def kapanis(self, kural: Optional[dict]) -> str:
        if kural and kural.get("kapanis"):
            return kural["kapanis"]
        return self.varsayilan_kapanis


def matris_yukle(yol: str = MATRIS_YOLU) -> dict:
    with open(yol, encoding="utf-8") as f:
        return json.load(f)
