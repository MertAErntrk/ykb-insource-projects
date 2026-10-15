"""Oracle sözlük okuma: şema, tablo, kolon, nesne ve trigger bilgisi (yalnız sql_templates şablonları).

Fonksiyonlar senkrondur; SafeConnection üzerinden çalışır.
"""

from app.anon.rules import ColumnInfo, is_supported_type
from app.errors import AppError, ErrorCode
from app.oracle import sql_templates as T
from app.oracle.safe_connection import SafeConnection
from app.transfer.filters import ColumnMeta

_UNSUPPORTED_REASONS = {
    "CLOB": "CLOB taşınamaz",
    "NCLOB": "NCLOB taşınamaz",
    "BLOB": "BLOB taşınamaz",
    "BFILE": "BFILE taşınamaz",
    "LONG": "LONG taşınamaz",
    "LONG RAW": "LONG RAW taşınamaz",
    "RAW": "RAW taşınamaz",
    "XMLTYPE": "XML taşınamaz",
    "ROWID": "ROWID taşınamaz",
    "UROWID": "UROWID taşınamaz",
}


def _flag(value: object) -> bool:
    return str(value or "").strip().upper() in ("YES", "Y")


def _int_or_none(value: object) -> int | None:
    return int(value) if value is not None else None


def list_schemas(conn: SafeConnection, q: str | None, limit: int = 200, offset: int = 0) -> list[str]:
    """Kullanıcının erişebildiği tabloların şemaları (ALL_TABLES.OWNER)."""
    stmt, binds = T.dict_schemas(q, limit, offset)
    return [r["OWNER"] for r in conn.query(stmt, binds)]


def list_tables(conn: SafeConnection, schema: str, q: str | None, limit: int, offset: int) -> dict:
    """{items:[{schema,name}], total, limit, offset}. Yalnız sözlük okunur; tablolara sorgu atılmaz.

    Sayım şablonu yok: total en iyi tahmindir. Sayfa dolmadıysa tam değerdir (offset + dönen satır);
    dolduysa sonraki sayfa olabileceğinden offset + dönen satır + 1 verilir.
    """
    stmt, binds = T.dict_tables(schema, q, limit, offset)
    items = [{"schema": r["OWNER"], "name": r["TABLE_NAME"]} for r in conn.query(stmt, binds)]
    total = offset + len(items) + (1 if len(items) >= limit else 0)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def read_columns(conn: SafeConnection, schema: str, table: str) -> tuple[list[ColumnInfo], dict[str, dict]]:
    """(kolonlar, ek bilgi {ad: {position, virtual, identity, hidden, default}}). Tablo yoksa NOT_FOUND."""
    stmt, binds = T.dict_columns(schema, table)
    rows = conn.query(stmt, binds)
    if not rows:
        raise AppError(ErrorCode.NOT_FOUND, "Tablo bulunamadı ya da erişim yetkiniz yok.")
    ustmt, ubinds = T.dict_unique_columns(schema, table)
    unique = {r["COLUMN_NAME"] for r in conn.query(ustmt, ubinds)}
    cols: list[ColumnInfo] = []
    extra: dict[str, dict] = {}
    for r in rows:
        name = r["COLUMN_NAME"]
        hidden, virtual = _flag(r.get("HIDDEN_COLUMN")), _flag(r.get("VIRTUAL_COLUMN"))
        if hidden and virtual and name.startswith("SYS_"):
            continue
        dtype = r["DATA_TYPE"]
        cols.append(ColumnInfo(
            name=name, data_type=dtype, data_length=_int_or_none(r.get("DATA_LENGTH")),
            char_length=_int_or_none(r.get("CHAR_LENGTH")), char_used=r.get("CHAR_USED"),
            precision=_int_or_none(r.get("DATA_PRECISION")), scale=_int_or_none(r.get("DATA_SCALE")),
            nullable=str(r.get("NULLABLE")).upper() == "Y", is_pk_or_unique=name in unique,
            supported=is_supported_type(dtype),
        ))
        extra[name] = {
            "position": _int_or_none(r.get("COLUMN_ID")), "virtual": virtual,
            "identity": _flag(r.get("IDENTITY_COLUMN")), "hidden": hidden,
            "default": "(tanımlı)" if (r.get("DEFAULT_LENGTH") or 0) > 0 else None,
        }
    return cols, extra


def get_columns(conn: SafeConnection, schema: str, table: str) -> list[ColumnInfo]:
    return read_columns(conn, schema, table)[0]


def unsupported_reason(data_type: str) -> str | None:
    if is_supported_type(data_type):
        return None
    if data_type in _UNSUPPORTED_REASONS:
        return _UNSUPPORTED_REASONS[data_type]
    if data_type.startswith("TIMESTAMP") and "TIME ZONE" in data_type:
        return "Saat dilimli TIMESTAMP taşınamaz"
    return f"{data_type} taşınamaz"


def columns_to_api(cols: list[ColumnInfo], extra: dict[str, dict]) -> list[dict]:
    out = []
    for i, c in enumerate(cols, start=1):
        e = extra.get(c.name, {})
        out.append({
            "position": e.get("position") or i, "name": c.name, "data_type": c.data_type,
            "char_length": c.char_length or 0, "char_used": c.char_used if c.char_used in ("B", "C") else None,
            "data_length": c.data_length, "precision": c.precision, "scale": c.scale,
            "nullable": c.nullable, "virtual": e.get("virtual", False), "identity": e.get("identity", False),
            "hidden": e.get("hidden", False), "default": e.get("default"),
            "is_pk_or_unique": c.is_pk_or_unique, "supported": c.supported,
            "unsupported_reason": unsupported_reason(c.data_type),
        })
    return out


def table_object(conn: SafeConnection, schema: str, table: str) -> dict | None:
    stmt, binds = T.dict_object(schema, table)
    row = conn.query_one(stmt, binds)
    if row is None:
        return None
    return {"OBJECT_ID": row["OBJECT_ID"], "CREATED": row["CREATED"], "OBJECT_TYPE": row["OBJECT_TYPE"]}


def enabled_triggers(conn: SafeConnection, schema: str, table: str) -> list[dict]:
    stmt, binds = T.dict_triggers(schema, table)
    return conn.query(stmt, binds)


def schema_triggers(conn: SafeConnection, schema: str) -> list[dict]:
    stmt, binds = T.dict_schema_triggers(schema)
    return conn.query(stmt, binds)


def filter_columns(cols: list[ColumnInfo]) -> dict[str, ColumnMeta]:
    return {c.name: ColumnMeta(c.name, c.data_type, c.nullable, c.precision, c.scale) for c in cols}
