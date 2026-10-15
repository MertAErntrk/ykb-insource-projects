"""Uygulamanın çalıştırabileceği tüm SQL'in tek kaynağı.

Her şablon metni üretmeden önce guard.assert_allowed'dan geçirir ve modül-içi sentinel ile
işaretli bir Stmt döner; dışarıda kurulan Stmt is_trusted() ile reddedilir. Metinde literal
yoktur: sabit değerler ('N', 'ENABLED', 'TABLE' ...) dahil her değer bind ile verilir.
Uygulama bind adları "tt_" önekiyle başlar; filtre bind'leri (:f0, :f1 ...) ile çakışmaz.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.oracle.guard import ForbiddenSQL, assert_allowed
from app.oracle.identifiers import is_valid_ident, qualified, quote_ident

_SENTINEL = object()

Kind = Literal["select", "insert", "create"]
_FIRST_WORD = {"select": ("SELECT", "WITH"), "insert": ("INSERT",), "create": ("CREATE",)}


@dataclass(frozen=True)
class Stmt:
    text: str
    kind: Kind
    _token: object


def is_trusted(stmt: object) -> bool:
    """Stmt bu modülde üretildiyse ve metni hâlâ guard'dan geçiyorsa True."""
    if type(stmt) is not Stmt or stmt._token is not _SENTINEL:
        return False
    if stmt.kind not in _FIRST_WORD or not stmt.text.lstrip().upper().startswith(_FIRST_WORD[stmt.kind]):
        return False
    try:
        assert_allowed(stmt.text)
    except ForbiddenSQL:
        return False
    return True


def _make(text: str, kind: Kind) -> Stmt:
    assert_allowed(text)
    return Stmt(text=text, kind=kind, _token=_SENTINEL)


def _check_owner(schema: object) -> str:
    if not is_valid_ident(schema):
        raise ValueError("Geçersiz tanımlayıcı.")
    return schema  # type: ignore[return-value]


def _check_int(value: object, name: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"Geçersiz {name}.")
    return value


def _q_bind(q: object) -> str | None:
    if q is None or q == "":
        return None
    if not isinstance(q, str) or len(q) > 128:
        raise ValueError("Geçersiz arama metni.")
    return q


def _merge_binds(binds: dict | None) -> dict:
    out = dict(binds or {})
    if any(not isinstance(k, str) or k.lower().startswith("tt_") for k in out):
        raise ValueError("Geçersiz bind adı.")
    return out


def _where(where_sql: str) -> str:
    if not isinstance(where_sql, str):
        raise ValueError("Geçersiz filtre.")
    return f" WHERE {where_sql}" if where_sql.strip() else ""


# --- Sözlük sorguları -----------------------------------------------------------------------


def dict_schemas(q: str | None, limit: int, offset: int) -> tuple[Stmt, dict]:
    """(Stmt, binds) döner. Kolonlar: OWNER. Yalnız oturum kullanıcısının ALL_TABLES'ta gördüğü
    (kalıcı) tabloların sahipleri; Oracle'ın kendi şemaları (ORACLE_MAINTAINED, 12.1+) hariç."""
    stmt = _make(
        "SELECT DISTINCT OWNER FROM ALL_TABLES"
        " WHERE TEMPORARY = :tt_n"
        " AND OWNER NOT IN (SELECT USERNAME FROM ALL_USERS WHERE ORACLE_MAINTAINED = :tt_y)"
        " AND (:tt_q IS NULL OR INSTR(UPPER(OWNER), UPPER(:tt_q)) > 0)"
        " ORDER BY OWNER"
        " OFFSET :tt_offset ROWS FETCH NEXT :tt_limit ROWS ONLY",
        "select",
    )
    binds = {
        "tt_n": "N",
        "tt_y": "Y",
        "tt_q": _q_bind(q),
        "tt_offset": _check_int(offset, "offset", 0),
        "tt_limit": _check_int(limit, "limit", 1),
    }
    return stmt, binds


def dict_tables(schema: str, q: str | None, limit: int, offset: int) -> tuple[Stmt, dict]:
    """(Stmt, binds) döner. Kolonlar: OWNER, TABLE_NAME. Geçici tablolar hariç; tablolara sorgu atılmaz."""
    stmt = _make(
        "SELECT OWNER, TABLE_NAME FROM ALL_TABLES"
        " WHERE OWNER = :tt_owner AND TEMPORARY = :tt_n"
        " AND (:tt_q IS NULL OR INSTR(UPPER(TABLE_NAME), UPPER(:tt_q)) > 0)"
        " ORDER BY TABLE_NAME"
        " OFFSET :tt_offset ROWS FETCH NEXT :tt_limit ROWS ONLY",
        "select",
    )
    binds = {
        "tt_owner": _check_owner(schema),
        "tt_n": "N",
        "tt_q": _q_bind(q),
        "tt_offset": _check_int(offset, "offset", 0),
        "tt_limit": _check_int(limit, "limit", 1),
    }
    return stmt, binds


def dict_columns(schema: str, table: str) -> tuple[Stmt, dict]:
    """(Stmt, binds) döner. Kolonlar: COLUMN_NAME, DATA_TYPE, DATA_LENGTH, CHAR_LENGTH, CHAR_USED,
    DATA_PRECISION, DATA_SCALE, NULLABLE, COLUMN_ID, HIDDEN_COLUMN, VIRTUAL_COLUMN,
    IDENTITY_COLUMN, DEFAULT_LENGTH (COLUMN_ID sırasıyla; gizli kolonlarda COLUMN_ID NULL olabilir)."""
    stmt = _make(
        "SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, CHAR_LENGTH, CHAR_USED, DATA_PRECISION,"
        " DATA_SCALE, NULLABLE, COLUMN_ID, HIDDEN_COLUMN, VIRTUAL_COLUMN, IDENTITY_COLUMN,"
        " DEFAULT_LENGTH FROM ALL_TAB_COLS"
        " WHERE OWNER = :tt_owner AND TABLE_NAME = :tt_table"
        " ORDER BY COLUMN_ID NULLS LAST, COLUMN_NAME",
        "select",
    )
    return stmt, {"tt_owner": _check_owner(schema), "tt_table": _check_owner(table)}


def dict_unique_columns(schema: str, table: str) -> tuple[Stmt, dict]:
    """(Stmt, binds) döner. Kolonlar: COLUMN_NAME (PK/UNIQUE kısıt kolonları UNION benzersiz
    index kolonları; tekrarsız)."""
    stmt = _make(
        "SELECT CC.COLUMN_NAME FROM ALL_CONSTRAINTS C"
        " JOIN ALL_CONS_COLUMNS CC ON CC.OWNER = C.OWNER"
        " AND CC.CONSTRAINT_NAME = C.CONSTRAINT_NAME AND CC.TABLE_NAME = C.TABLE_NAME"
        " WHERE C.OWNER = :tt_owner AND C.TABLE_NAME = :tt_table"
        " AND C.CONSTRAINT_TYPE IN (:tt_p, :tt_u)"
        " UNION"
        " SELECT IC.COLUMN_NAME FROM ALL_INDEXES I"
        " JOIN ALL_IND_COLUMNS IC ON IC.INDEX_OWNER = I.OWNER AND IC.INDEX_NAME = I.INDEX_NAME"
        " WHERE I.TABLE_OWNER = :tt_owner AND I.TABLE_NAME = :tt_table"
        " AND I.UNIQUENESS = :tt_unique",
        "select",
    )
    binds = {
        "tt_owner": _check_owner(schema),
        "tt_table": _check_owner(table),
        "tt_p": "P",
        "tt_u": "U",
        "tt_unique": "UNIQUE",
    }
    return stmt, binds


def dict_triggers(schema: str, table: str) -> tuple[Stmt, dict]:
    """(Stmt, binds) döner. Kolonlar: OWNER, TRIGGER_NAME (tablodaki ENABLED trigger'lar)."""
    stmt = _make(
        "SELECT OWNER, TRIGGER_NAME FROM ALL_TRIGGERS"
        " WHERE TABLE_OWNER = :tt_owner AND TABLE_NAME = :tt_table"
        " AND TRIM(BASE_OBJECT_TYPE) = :tt_type AND STATUS = :tt_enabled",
        "select",
    )
    binds = {
        "tt_owner": _check_owner(schema),
        "tt_table": _check_owner(table),
        "tt_type": "TABLE",
        "tt_enabled": "ENABLED",
    }
    return stmt, binds


def dict_schema_triggers(schema: str) -> tuple[Stmt, dict]:
    """(Stmt, binds) döner. Kolonlar: OWNER, TRIGGER_NAME, BASE_OBJECT_TYPE (sahibi schema olan,
    ENABLED, SCHEMA/DATABASE seviyesi trigger'lar)."""
    stmt = _make(
        "SELECT OWNER, TRIGGER_NAME, TRIM(BASE_OBJECT_TYPE) AS BASE_OBJECT_TYPE FROM ALL_TRIGGERS"
        " WHERE OWNER = :tt_owner AND TRIM(BASE_OBJECT_TYPE) IN (:tt_schema, :tt_database)"
        " AND STATUS = :tt_enabled",
        "select",
    )
    binds = {
        "tt_owner": _check_owner(schema),
        "tt_schema": "SCHEMA",
        "tt_database": "DATABASE",
        "tt_enabled": "ENABLED",
    }
    return stmt, binds


_OBJECT_SQL = (
    "SELECT OBJECT_ID, CREATED, OBJECT_TYPE FROM {view}"
    " WHERE OWNER = :tt_owner AND OBJECT_NAME = :tt_name AND SUBOBJECT_NAME IS NULL"
)


def dict_object(schema: str, name: str) -> tuple[Stmt, dict]:
    """(Stmt, binds) döner. ALL_OBJECTS'ten OBJECT_ID, CREATED, OBJECT_TYPE (bölüm satırları hariç)."""
    stmt = _make(_OBJECT_SQL.format(view="ALL_OBJECTS"), "select")
    return stmt, {"tt_owner": _check_owner(schema), "tt_name": _check_owner(name)}


def dba_object(schema: str, name: str) -> tuple[Stmt, dict]:
    """(Stmt, binds) döner. DBA_OBJECTS'ten OBJECT_ID, CREATED, OBJECT_TYPE (bölüm satırları hariç)."""
    stmt = _make(_OBJECT_SQL.format(view="DBA_OBJECTS"), "select")
    return stmt, {"tt_owner": _check_owner(schema), "tt_name": _check_owner(name)}


# --- Veri sorguları -------------------------------------------------------------------------


def count_rows(schema: str, table: str, where_sql: str, binds: dict, cap: int) -> tuple[Stmt, dict]:
    """(Stmt, binds) döner. Tek kolon: COUNT(*), en çok `cap` (tavanlı; tipik cap = sınır + 1,
    sonuç cap ise sınır aşılmıştır). where_sql filters.build_where çıktısıdır
    ("WHERE" içermez; boşsa koşulsuz)."""
    stmt = _make(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {qualified(schema, table)}{_where(where_sql)}"
        " FETCH FIRST :tt_cap ROWS ONLY)",
        "select",
    )
    out = _merge_binds(binds)
    out["tt_cap"] = _check_int(cap, "cap", 1)
    return stmt, out


def select_rows(
    schema: str, table: str, columns: list[str], where_sql: str, binds: dict, limit: int
) -> tuple[Stmt, dict]:
    """(Stmt, binds) döner. Kolonlar `columns` sırasıyla; sonda her zaman
    FETCH FIRST :tt_limit ROWS ONLY vardır."""
    if not columns or len(set(columns)) != len(columns):
        raise ValueError("Kolon listesi boş ya da yinelenen kolon var.")
    cols = ", ".join(quote_ident(c) for c in columns)
    stmt = _make(
        f"SELECT {cols} FROM {qualified(schema, table)}{_where(where_sql)}"
        " FETCH FIRST :tt_limit ROWS ONLY",
        "select",
    )
    out = _merge_binds(binds)
    out["tt_limit"] = _check_int(limit, "limit", 1)
    return stmt, out


# --- Hedef DDL/DML --------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnDef:
    name: str
    data_type: str
    data_length: int | None
    char_length: int | None
    char_used: str | None
    precision: int | None
    scale: int | None
    nullable: str


_TIMESTAMP_RE = re.compile(r"TIMESTAMP\(([0-9])\)")


def _as_int(value: object, lo: int, hi: int) -> int:
    """Sözlükten int/Decimal/float gelebilir; yalnız tam sayı ve aralık içi kabul edilir."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("Geçersiz kolon boyutu.")
    if value != int(value) or not lo <= int(value) <= hi:
        raise ValueError("Geçersiz kolon boyutu.")
    return int(value)


def _type_sql(c: ColumnDef) -> str:
    t = c.data_type
    if t in ("VARCHAR2", "CHAR"):
        hi = 32767 if t == "VARCHAR2" else 2000
        if c.char_used == "C":
            return f"{t}({_as_int(c.char_length, 1, hi)} CHAR)"
        if c.char_used == "B":
            return f"{t}({_as_int(c.data_length, 1, hi)} BYTE)"
        raise ValueError("Geçersiz CHAR_USED.")
    if t in ("NVARCHAR2", "NCHAR"):
        hi = 32767 if t == "NVARCHAR2" else 2000
        return f"{t}({_as_int(c.char_length, 1, hi)})"
    if t == "NUMBER":
        if c.precision is None:
            if c.scale is None:
                return "NUMBER"
            return f"NUMBER(*,{_as_int(c.scale, -84, 127)})"
        p = _as_int(c.precision, 1, 38)
        if c.scale is None or c.scale == 0:
            return f"NUMBER({p})"
        return f"NUMBER({p},{_as_int(c.scale, -84, 127)})"
    if t == "FLOAT":
        return "FLOAT" if c.precision is None else f"FLOAT({_as_int(c.precision, 1, 126)})"
    if t in ("BINARY_FLOAT", "BINARY_DOUBLE", "DATE"):
        return t
    m = _TIMESTAMP_RE.fullmatch(t) if isinstance(t, str) else None
    if m:
        return f"TIMESTAMP({m.group(1)})"
    if t == "TIMESTAMP" and c.scale is not None:
        return f"TIMESTAMP({_as_int(c.scale, 0, 9)})"
    raise ValueError("Desteklenmeyen kolon tipi.")


def create_table(schema: str, table: str, cols: list[ColumnDef]) -> Stmt:
    """Stmt döner (DDL bind almaz). Yalnız kolon adı, tip ve NOT NULL yazılır;
    DEFAULT/IDENTITY/virtual kolon tanımı asla yazılmaz. Desteklenmeyen tip → ValueError."""
    if not cols or len({c.name for c in cols}) != len(cols):
        raise ValueError("Kolon listesi boş ya da yinelenen kolon var.")
    parts = []
    for c in cols:
        if c.nullable not in ("Y", "N"):
            raise ValueError("Geçersiz NULLABLE.")
        not_null = " NOT NULL" if c.nullable == "N" else ""
        parts.append(f"{quote_ident(c.name)} {_type_sql(c)}{not_null}")
    return _make(f"CREATE TABLE {qualified(schema, table)} ({', '.join(parts)})", "create")


def insert_rows(schema: str, table: str, columns: list[str]) -> Stmt:
    """Stmt döner. Değerler konumsal bind :1..:n (columns sırasıyla, executemany için)."""
    if not columns or len(set(columns)) != len(columns):
        raise ValueError("Kolon listesi boş ya da yinelenen kolon var.")
    cols = ", ".join(quote_ident(c) for c in columns)
    values = ", ".join(f":{i}" for i in range(1, len(columns) + 1))
    return _make(f"INSERT INTO {qualified(schema, table)} ({cols}) VALUES ({values})", "insert")
