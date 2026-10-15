"""Yapılandırılmış filtre → bind'li WHERE koşulu.

Serbest WHERE yoktur: her satır kolon + operatör + değer; kolon adı sözlükten gelen
`columns` içinde birebir olmalı, değerler Python'da türüne çevrilip :f0, :f1 ... ile bind edilir.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.errors import AppError, ErrorCode
from app.oracle.identifiers import quote_ident

MAX_IN_VALUES = 1000

_COMPARE = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
_OPS = frozenset({*_COMPARE, "like", "in", "between", "is_null", "is_not_null"})
_NUMERIC_TYPES = frozenset({"NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"})
_TEXT_TYPES = frozenset({"VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"})
_TIMESTAMP_RE = re.compile(r"TIMESTAMP(\([0-9]\))?")
_INT_RE = re.compile(r"[+-]?[0-9]+")


@dataclass(frozen=True)
class ColumnMeta:
    name: str
    data_type: str
    nullable: bool = True
    precision: int | None = None
    scale: int | None = None


_DATE_MIN = datetime(1, 1, 1)
_DATE_MAX = datetime(9999, 12, 31)
_WILDCARD_ONLY_RE = re.compile(r"[%_]*")


def _decimals(num: object) -> int:
    exp = Decimal(num).normalize().as_tuple().exponent
    return -exp if isinstance(exp, int) and exp < 0 else 0


def _number_tautology(meta: ColumnMeta, op: str, vals: list) -> bool:
    if meta.data_type != "NUMBER" or meta.precision is None:
        return False
    scale = meta.scale or 0
    limit = Decimal(10) ** (meta.precision - scale)
    if op in ("gt", "ge"):
        return vals[0] <= -limit
    if op in ("lt", "le"):
        return vals[0] >= limit
    if op == "between":
        return vals[0] <= -limit and vals[1] >= limit
    if op == "ne":
        return abs(vals[0]) >= limit or _decimals(vals[0]) > max(scale, 0)
    return False


def _date_tautology(op: str, vals: list) -> bool:
    if op in ("gt", "ge"):
        return vals[0] <= _DATE_MIN
    if op in ("lt", "le"):
        return vals[0] >= _DATE_MAX
    if op == "between":
        return vals[0] <= _DATE_MIN and vals[1] >= _DATE_MAX
    return False


def _invalid(detail: str) -> AppError:
    return AppError(ErrorCode.VALIDATION, detail)


def _kind(data_type: str) -> str | None:
    if data_type in _NUMERIC_TYPES:
        return "number"
    if data_type in _TEXT_TYPES:
        return "text"
    if data_type == "DATE" or _TIMESTAMP_RE.fullmatch(data_type or ""):
        return "date"
    return None


def _convert(value: object, kind: str, column: str) -> object:
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise _invalid(f"'{column}' kolonu için filtre değeri geçersiz.")
    if kind == "text":
        return value if isinstance(value, str) else str(value)
    if kind == "number":
        if isinstance(value, int):
            return value
        text = value.strip() if isinstance(value, str) else repr(value)
        if _INT_RE.fullmatch(text):
            return int(text)
        try:
            num = Decimal(text)
        except InvalidOperation:
            num = None
        if num is None or not num.is_finite():
            raise _invalid(f"'{column}' kolonu sayısal; '{value}' sayı değil.")
        return num
    # date
    if not isinstance(value, str):
        raise _invalid(f"'{column}' kolonu tarih; değer ISO-8601 metin olmalı.")
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise _invalid(f"'{column}' kolonu tarih; '{value}' geçerli bir ISO-8601 tarih değil.") from None
    if dt.tzinfo is not None:
        raise _invalid(f"'{column}' kolonu için saat dilimli tarih kullanılamaz.")
    return dt


def build_where(filters: list[dict], columns: dict[str, ColumnMeta], *,
                full_table: bool = False) -> tuple[str, dict]:
    """("koşul1 AND koşul2 ...", {"f0": değer, ...}) döner; "WHERE" eklemez.

    Kapsam baştan belirlenmeli: boş liste ve tüm satırları seçen (totolojik) koşullar VALIDATION.
    full_table=True açık "tablonun tamamı" seçimidir: filtre verilemez, ("", {}) döner
    (satır sınırı sayımda ayrıca uygulanır)."""
    if full_table:
        if filters:
            raise _invalid("Tablonun tamamı seçiliyken filtre verilemez.")
        return "", {}
    if not filters:
        raise _invalid("Taşınacak kümeyi belirlemek için en az bir filtre gerekli.")
    if not isinstance(filters, list):
        raise _invalid("Filtre listesi geçersiz.")
    parts: list[str] = []
    binds: dict = {}

    def bind(value: object) -> str:
        name = f"f{len(binds)}"
        binds[name] = value
        return f":{name}"

    def all_rows(column: str) -> AppError:
        return _invalid(f"'{column}' koşulu tüm satırları seçer; kümeyi daraltan bir koşul girin.")

    for f in filters:
        if not isinstance(f, dict):
            raise _invalid("Filtre satırı geçersiz.")
        column, op = f.get("column"), f.get("op")
        if not isinstance(column, str) or column not in columns:
            raise _invalid("Filtrede bilinmeyen kolon var.")
        if op not in _OPS:
            raise _invalid(f"'{column}' kolonu için bilinmeyen filtre operatörü.")
        meta = columns[column]
        try:
            col_sql = quote_ident(meta.name)
        except ValueError:
            raise _invalid("Filtrede geçersiz kolon adı var.") from None
        if meta.name != column:
            raise _invalid("Filtrede bilinmeyen kolon var.")
        value, values = f.get("value"), f.get("values")

        if op in ("is_null", "is_not_null"):
            if value is not None or values:
                raise _invalid(f"'{column}' için boş/dolu filtresi değer almaz.")
            if op == "is_not_null" and not meta.nullable:
                raise all_rows(column)
            parts.append(f"{col_sql} IS {'NOT ' if op == 'is_not_null' else ''}NULL")
            continue

        kind = _kind(meta.data_type)
        if kind is None:
            raise _invalid(f"'{column}' kolonunun türünde filtre desteklenmiyor.")

        if op in ("in", "between"):
            if value is not None or not isinstance(values, list):
                raise _invalid(f"'{column}' için değer listesi (values) gerekli.")
            if op == "between" and len(values) != 2:
                raise _invalid(f"'{column}' için 'arasında' filtresi tam 2 değer ister.")
            if op == "in" and not 1 <= len(values) <= MAX_IN_VALUES:
                raise _invalid(f"'{column}' için liste filtresi 1 ile {MAX_IN_VALUES} arasında değer ister.")
            converted = [_convert(v, kind, column) for v in values]
            if op == "between" and (
                (kind == "number" and _number_tautology(meta, op, converted))
                or (kind == "date" and _date_tautology(op, converted))
            ):
                raise all_rows(column)
            if op == "between":
                parts.append(f"{col_sql} BETWEEN {bind(converted[0])} AND {bind(converted[1])}")
            else:
                parts.append(f"{col_sql} IN ({', '.join(bind(v) for v in converted)})")
            continue

        if values is not None:
            raise _invalid(f"'{column}' için tek değer (value) gerekli.")
        if op == "like":
            if kind != "text" or not isinstance(value, str):
                raise _invalid(f"'{column}' için 'benzer' filtresi yalnız metin kolonda kullanılabilir.")
            if "%" in value and _WILDCARD_ONLY_RE.fullmatch(value):
                raise all_rows(column)
            parts.append(f"{col_sql} LIKE {bind(value)}")
            continue
        converted_one = _convert(value, kind, column)
        if (kind == "number" and _number_tautology(meta, op, [converted_one])) or (
            kind == "date" and _date_tautology(op, [converted_one])
        ):
            raise all_rows(column)
        parts.append(f"{col_sql} {_COMPARE[op]} {bind(converted_one)}")

    return " AND ".join(parts), binds
