"""Oracle tanımlayıcı doğrulama ve tırnaklama.

Yalnız büyük harfli, ASCII, tırnaksız Oracle kuralına uyan adlar kabul edilir.
`upper()` dönüşümü kasıtlı olarak yapılmaz: ad sözlükteki haliyle birebir gelmelidir.
"""

import re

IDENT_RE = r"[A-Z][A-Z0-9_$#]{0,127}"
_IDENT = re.compile(IDENT_RE)


def is_valid_ident(name: object, max_len: int = 128) -> bool:
    if not isinstance(name, str) or not 0 < len(name) <= max_len:
        return False
    return _IDENT.fullmatch(name) is not None


def quote_ident(name: object, max_len: int = 128) -> str:
    if not is_valid_ident(name, max_len):
        raise ValueError("Geçersiz tanımlayıcı.")
    return f'"{name}"'


def qualified(schema: object, table: object) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"
