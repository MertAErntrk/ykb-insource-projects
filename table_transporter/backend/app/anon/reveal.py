"""Standart kurallarla (num_token, text_token, date_token) anonimleştirilmiş değerlerin gerçek
değere geri çevrilmesi (değer çözme). Kural ileri yöndeki RulePlan dönüşümünün tersidir;
anahtar aynı (derive_key(key, b"tt-fpe")). Değerler ve sonuçlar loglanmaz.
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.anon.fpe import MAX_DIGITS, FeistelFPE
from app.anon.rules import derive_key

REVEAL_RULES = ("num_token", "text_token", "date_token")
_NUMBER_RE = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_TIME = r"(?: ([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]{1,6})?)?"
_ISO_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})" + _TIME)
_TR_RE = re.compile(r"([0-9]{2})([./])([0-9]{2})\2([0-9]{4})" + _TIME)


class _RowError(Exception):
    pass


def _reveal_number(f: FeistelFPE, s: str) -> str:
    if not _NUMBER_RE.fullmatch(s):
        raise _RowError("Sayı bekleniyordu.")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise _RowError("Sayı bekleniyordu.") from None
    if len(str(int(abs(d)))) > MAX_DIGITS:
        raise _RowError(f"Tamsayı kısmı {MAX_DIGITS} basamaktan uzun; çözülemez.")
    return format(f.untoken_decimal(d), "f")


def _reveal_date(f: FeistelFPE, s: str) -> str:
    """Girdi biçimi korunur: YYYY-MM-DD, GG.AA.YYYY ya da GG/AA/YYYY (+ isteğe bağlı saat)."""
    m = _ISO_RE.fullmatch(s)
    if m:
        y, mo, d = m.group(1, 2, 3)
        hh, mi, ss, frac = m.group(4, 5, 6, 7)
        sep = None
    else:
        m = _TR_RE.fullmatch(s)
        if not m:
            raise _RowError("Tarih bekleniyordu (YYYY-AA-GG ya da GG.AA.YYYY).")
        d, sep, mo, y = m.group(1, 2, 3, 4)
        hh, mi, ss, frac = m.group(5, 6, 7, 8)
    try:
        v = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0))
    except ValueError:
        raise _RowError("Geçersiz tarih.") from None
    o = f.untoken_date(v)
    day = o.strftime("%d" + sep + "%m" + sep) + f"{o.year:04d}" if sep else f"{o.year:04d}-{o:%m-%d}"
    if hh is None:
        return day
    return f"{day} {o:%H:%M:%S}{frac or ''}"


def reveal_values(key: bytes, rule: str, values: list[str]) -> list[dict]:
    """Her değer için {"value", "original", "error"}; geçersiz giriş satır hatasıdır."""
    if rule not in REVEAL_RULES:
        raise ValueError("Bu kural geri çevrilemez.")
    f = FeistelFPE(derive_key(key, b"tt-fpe"))
    out = []
    for v in values:
        original = error = None
        try:
            if rule == "num_token":
                original = _reveal_number(f, v)
            elif rule == "text_token":
                original = f.untoken_text_std(v)
            else:
                original = _reveal_date(f, v)
        except _RowError as exc:
            error = str(exc)
        except (ValueError, RuntimeError):
            error = "Değer çözülemedi."
        out.append({"value": v, "original": original, "error": error})
    return out
