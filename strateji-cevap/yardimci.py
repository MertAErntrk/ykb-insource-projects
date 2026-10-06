"""
yardimci.py — ortak küçük araçlar: Türkçe küçük harf, tarih ayrıştırma, Türkçe ay adı,
Türkçe sayı biçimi ve JSON içinde noktalı yolla alan okuma (anahtar eşleşmesi büyük/küçük
harf duyarsız; gerçek JSON'daki yazım kesinleşene kadar bu esneklik gerekli).
"""
import datetime as dt
import re
from typing import Any, Optional

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
         "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")


def tr_kucuk(s: str) -> str:
    """Türkçe'ye uygun küçük harf: I→ı, İ→i (str.lower() 'I'yı 'i' yapar)."""
    return (s or "").replace("I", "ı").replace("İ", "i").lower()


def tr_buyuk_ilk(s: str) -> str:
    """İlk harfi Türkçe'ye uygun büyütür (i→İ)."""
    if not s:
        return s
    ilk = "İ" if s[0] == "i" else s[0].upper()
    return ilk + s[1:]


def tarih_ayristir(deger: Any) -> Optional[dt.date]:
    """'2025-04-12', '2025-04-12T10:00:00', '12.06.2025', '12/06/2025', '20250612', date/datetime → date.
    Ayrıştırılamazsa None."""
    if deger is None or deger == "":
        return None
    if isinstance(deger, dt.datetime):
        return deger.date()
    if isinstance(deger, dt.date):
        return deger
    s = str(deger).strip()
    if _ISO.match(s):
        try:
            return dt.date.fromisoformat(s[:10])
        except ValueError:
            return None
    for bicim, uzunluk in (("%d.%m.%Y", 10), ("%d/%m/%Y", 10), ("%d-%m-%Y", 10), ("%Y%m%d", 8)):
        try:
            return dt.datetime.strptime(s[:uzunluk], bicim).date()
        except ValueError:
            continue
    return None


def ay_yil(tarih: dt.date) -> str:
    """date(2025, 6, 12) → 'Nisan 2025' (cevap metinlerindeki biçim)."""
    return "{} {}".format(AYLAR[tarih.month - 1], tarih.year)


def yil_once(tarih: dt.date, yil: int) -> dt.date:
    """tarih'ten yil yıl öncesi; 29 Şubat için 28 Şubat."""
    try:
        return tarih.replace(year=tarih.year - yil)
    except ValueError:
        return tarih.replace(year=tarih.year - yil, day=28)


def tl_bicim(sayi: Any) -> str:
    """1234567.89 → '1.234.567,89' (Türkçe biçim). Sayı değilse olduğu gibi döner."""
    try:
        s = "{:,.2f}".format(float(sayi))          # '5,124,269.86'
    except (TypeError, ValueError):
        return str(sayi)
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def yol_al(obj: Any, yol: str, varsayilan: Any = None) -> Any:
    """'Outdata.CORPCOMMMATRIXOUTPUTS.NOMINALLIMIT' gibi noktalı yolu izler.
    Sözlük anahtarları büyük/küçük harf duyarsız eşleşir, liste için sayı indeks ('HRules.0.Code')."""
    cur = obj
    if not yol:
        return varsayilan
    for parca in yol.split("."):
        if isinstance(cur, dict):
            if parca in cur:
                cur = cur[parca]
                continue
            hedef = parca.lower()
            eslesen = [k for k in cur if str(k).lower() == hedef]
            if not eslesen:
                return varsayilan
            cur = cur[eslesen[0]]
        elif isinstance(cur, list) and parca.isdigit() and int(parca) < len(cur):
            cur = cur[int(parca)]
        else:
            return varsayilan
    return cur
