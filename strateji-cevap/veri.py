"""
veri.py — başvuru (strateji girdi/çıktı) JSON'una erişim. Prototipte bir klasördeki dosyalar
okunur; hedefte aynı arayüzü sağlayan veri tabanı/API uyarlayıcısı yazılır
(docs/UYGULAMA_PLANI.md bölüm 5). Arayüz: bul(cif, tarih=None) -> dict | None.
tarih verilirse o tarihe en yakın önceki başvuru, yoksa en yeni başvuru döner.
"""
import datetime as dt
import glob
import json
import os
from typing import List, Optional

from yardimci import tarih_ayristir, yol_al


class DosyaKaynagi:
    def __init__(self, klasor: str, cif_yolu: str = "CIF", tarih_yolu: str = "BasvuruTarihi"):
        self.klasor = klasor
        self.cif_yolu = cif_yolu
        self.tarih_yolu = tarih_yolu
        self._kayitlar: Optional[List[dict]] = None

    def _yukle(self) -> List[dict]:
        if self._kayitlar is None:
            self._kayitlar = []
            for yol in sorted(glob.glob(os.path.join(self.klasor, "*.json"))):
                try:
                    with open(yol, encoding="utf-8") as f:
                        veri = json.load(f)
                except (OSError, ValueError):
                    continue
                if isinstance(veri, dict) and yol_al(veri, self.cif_yolu) is not None:
                    self._kayitlar.append(veri)
        return self._kayitlar

    def bul(self, cif: str, tarih: Optional[dt.date] = None) -> Optional[dict]:
        adaylar = [b for b in self._yukle() if str(yol_al(b, self.cif_yolu)) == str(cif)]
        if not adaylar:
            return None
        adaylar.sort(key=lambda b: tarih_ayristir(yol_al(b, self.tarih_yolu)) or dt.date.min)
        if tarih is not None:
            onceki = [b for b in adaylar if (tarih_ayristir(yol_al(b, self.tarih_yolu)) or dt.date.min) <= tarih]
            if onceki:
                return onceki[-1]
        return adaylar[-1]
