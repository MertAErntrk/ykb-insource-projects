"""DBA için DROP script metni üretir. Uygulama ASLA çalıştırmaz; yalnız metin döner.

Bu modül veritabanı katmanını import etmez (statik tarama bunu zorlar)."""

import re
from datetime import datetime

_IDENT_RE = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_APP_NAME = "VeriKöprü"


def _comment(value: object) -> str:
    """Yorum satırına güvenle girecek tek satırlık metin (satır sonu enjeksiyonu yok)."""
    return _CTRL_RE.sub(" ", "" if value is None else str(value)).strip()


def _ident(value: object, what: str) -> str:
    if not isinstance(value, str) or not _IDENT_RE.fullmatch(value):
        raise ValueError(f"Geçersiz {what} tanımlayıcısı: {_comment(value)[:60]!r}")
    return value


def build_drop_script(tables: list[dict], *, generated_by: str, generated_at: datetime | str) -> str:
    """Yalnız table_status == expired_present ve object_id dolu kayıtlar için script üretir.
    Uygun kayıt yoksa ya da tanımlayıcı geçersizse ValueError."""
    eligible = [t for t in tables if t.get("table_status") == "expired_present" and t.get("object_id")]
    if not eligible:
        raise ValueError("DROP script için uygun tablo yok.")
    stamp = generated_at.strftime("%Y-%m-%d %H:%M:%S") if isinstance(generated_at, datetime) else generated_at
    lines = [
        f"-- Üreten uygulama: {_APP_NAME}",
        f"-- Kullanıcı: {_comment(generated_by)}",
        f"-- Zaman: {_comment(stamp)}",
        "-- Bu script yalnız DBA tarafından gözden geçirilip elle çalıştırılmalıdır."
        " Uygulama tablo silmez.",
        f"-- Tablo sayısı: {len(eligible)}",
        "",
    ]
    for t in eligible:
        schema = _ident(t.get("schema"), "şema")
        table = _ident(t.get("table"), "tablo")
        lines += [
            f"-- job_id: {_comment(t.get('job_id'))}  owner: {_comment(t.get('owner'))}"
            f"  alias: {_comment(t.get('alias'))}",
            f"-- object_id: {_comment(t.get('object_id'))}  object_created: {_comment(t.get('object_created'))}"
            f"  expires_at: {_comment(t.get('expires_at'))}",
            f'DROP TABLE "{schema}"."{table}";',
            "",
        ]
    return "\n".join(lines)
