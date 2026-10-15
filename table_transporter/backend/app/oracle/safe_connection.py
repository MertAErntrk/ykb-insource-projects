"""Güvenli bağlantı sarmalayıcısı (silmeme garantisi, katman 2).

SafeConnection yalnız sql_templates'in ürettiği güvenilir Stmt'yi çalıştırır; ham str, ham
connection/cursor ya da execute/callproc benzeri bir üye dışarı açılmaz. Nesneyi yalnız
pools kurar (`_wrap`); bağlantı havuza dönünce nesne kullanılamaz hale gelir.

Arrow tip eşlemesi (kayıpsız; float64'e düşme yok), ColumnInfo'dan belirlenir:
- NUMBER(p<=18, 0)                → int64
- NUMBER(p, s), 0<=s<=p<=38       → decimal128(p, s)
- NUMBER(*, s), 0<=s<=38          → decimal128(38, s)
- precision'sız NUMBER, FLOAT(n) ve diğer NUMBER biçimleri (s<0, s>p)
                                  → string, alan metadata'sı {tt_number: "1"}; değer Decimal'in
                                    tam ondalık metnidir (str(Decimal)). Ölçek sabit olmadığından
                                    decimal128(38, s) her değere sığmaz; metin her değeri taşır.
                                    to_db_rows bu alanları yeniden Decimal'e çevirir.
- BINARY_FLOAT / BINARY_DOUBLE    → float32 / float64
- CHAR, VARCHAR2, NCHAR, NVARCHAR2 → string
- DATE                            → timestamp('s')
- TIMESTAMP(n)                    → timestamp('us')
Okuma yolu: metin-sayı kolonu yoksa fetch_df_batches(requested_schema=..., fetch_decimals=True);
varsa cursor + outputtypehandler (NUMBER → Decimal) + fetchmany. Her iki yolda batch şeması
arrow_schema_for ile birebirdir; güvensiz dönüşüm AppError(INTERNAL) verir (sessiz yuvarlama yok).
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

import oracledb
import pyarrow as pa

from app.anon.rules import ColumnInfo
from app.errors import AppError, ErrorCode, ora_error_to_app_error
from app.oracle.guard import assert_allowed
from app.oracle.identifiers import is_valid_ident
from app.oracle.sql_templates import Stmt, is_trusted

NUMBER_TEXT_META = b"tt_number"
_TIMEOUT_CODES = frozenset({"DPY-4024", "ORA-03156", "ORA-01013"})
_DISCONNECT_CODES = frozenset({"DPY-1001", "DPY-4011"})
_TEXT_TYPES = frozenset({"CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2"})
_TIMESTAMP_RE = re.compile(r"TIMESTAMP(\([0-9]\))?")
_ARROW_ERRORS = (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError, OverflowError, ValueError, TypeError)
_WRAP_TOKEN = object()


# --- Tip eşlemesi ----------------------------------------------------------------------------


def _int_or_none(value: object) -> int | None:
    return None if value is None else int(value)  # sözlükten Decimal/float gelebilir


def _arrow_type(c: ColumnInfo) -> tuple[pa.DataType, bool]:
    """(Arrow tipi, metin-sayı mı) döner."""
    t = c.data_type
    if t == "NUMBER":
        p, s = _int_or_none(c.precision), _int_or_none(c.scale)
        if p is None:
            if s is not None and 0 <= s <= 38:
                return pa.decimal128(38, s), False
            return pa.string(), True
        if 1 <= p <= 38:
            s = s or 0
            if s == 0 and p <= 18:
                return pa.int64(), False
            if 0 <= s <= p:
                return pa.decimal128(p, s), False
        return pa.string(), True
    if t == "FLOAT":
        return pa.string(), True
    if t == "BINARY_FLOAT":
        return pa.float32(), False
    if t == "BINARY_DOUBLE":
        return pa.float64(), False
    if t in _TEXT_TYPES:
        return pa.string(), False
    if t == "DATE":
        return pa.timestamp("s"), False
    if isinstance(t, str) and _TIMESTAMP_RE.fullmatch(t):
        return pa.timestamp("us"), False
    raise AppError(ErrorCode.VALIDATION, f"{c.name}: desteklenmeyen kolon tipi ({t}).")


def arrow_schema_for(columns: list[ColumnInfo]) -> pa.Schema:
    fields = []
    for c in columns:
        typ, is_text_number = _arrow_type(c)
        meta = {NUMBER_TEXT_META: b"1"} if is_text_number else None
        fields.append(pa.field(c.name, typ, nullable=True, metadata=meta))
    return pa.schema(fields)


def _is_text_number(field: pa.Field) -> bool:
    return bool(field.metadata) and field.metadata.get(NUMBER_TEXT_META) == b"1"


def to_db_rows(batch: pa.RecordBatch) -> list[tuple]:
    """Batch'i executemany/direct_path_load için satır listesine çevirir: decimal → Decimal,
    int64 → int, timestamp → naive datetime, string → str (metin-sayı alanı → Decimal), null → None."""
    cols = []
    for i, field in enumerate(batch.schema):
        values = batch.column(i).to_pylist()
        if _is_text_number(field):
            values = [None if v is None else Decimal(v) for v in values]
        cols.append(values)
    return list(zip(*cols)) if cols else []


def _py_value(value: object, typ: pa.DataType, text_number: bool) -> object:
    """fetchmany değerini Arrow'a kayıpsız hazırlar; sığmayan değer ValueError."""
    if value is None:
        return None
    if text_number:
        if not isinstance(value, Decimal):
            raise ValueError("Sayı Decimal olarak okunmadı.")
        return str(value)
    if pa.types.is_int64(typ) and isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError("Tam sayı kolonunda kesirli değer.")
        return int(value)
    return value


def _batch_from_rows(rows: list[tuple], schema: pa.Schema) -> pa.RecordBatch:
    try:
        arrays = []
        for i, field in enumerate(schema):
            text_number = _is_text_number(field)
            values = [_py_value(r[i], field.type, text_number) for r in rows]
            arrays.append(pa.array(values, type=field.type))
        return pa.RecordBatch.from_arrays(arrays, schema=schema)
    except _ARROW_ERRORS as exc:
        raise AppError(ErrorCode.INTERNAL, "Okunan değer hedef tipe kayıpsız dönüştürülemedi.") from exc


def _cast_table(table: pa.Table, schema: pa.Schema) -> pa.Table:
    if table.schema.names != schema.names:
        raise AppError(ErrorCode.INTERNAL, "Okunan kolonlar beklenen kolonlarla uyuşmuyor.")
    try:
        return table.cast(schema, safe=True)
    except _ARROW_ERRORS as exc:
        raise AppError(ErrorCode.INTERNAL, "Okunan değer hedef tipe kayıpsız dönüştürülemedi.") from exc


def _decimal_handler(cursor, metadata):
    if metadata.type_code is oracledb.DB_TYPE_NUMBER:
        return cursor.var(Decimal, arraysize=cursor.arraysize)
    return None


# --- Hata eşlemesi ---------------------------------------------------------------------------


def _full_code(exc: oracledb.Error) -> str:
    info = exc.args[0] if exc.args else None
    return str(getattr(info, "full_code", "") or "")


def _to_app_error(exc: oracledb.Error) -> AppError:
    info = exc.args[0] if exc.args else None
    full_code = _full_code(exc)
    if full_code in _DISCONNECT_CODES:
        return AppError(ErrorCode.DB_DISCONNECTED)
    if full_code.startswith("ORA-"):
        return ora_error_to_app_error(int(getattr(info, "code", 0) or 0), "")
    return AppError(ErrorCode.ORA_ERROR, full_code or None)


@contextmanager
def _ora_errors() -> Iterator[None]:
    try:
        yield
    except oracledb.Error as exc:
        raise _to_app_error(exc) from None


# --- SafeConnection --------------------------------------------------------------------------


class SafeConnection:
    __slots__ = ("__conn", "__closed", "__in_tx")

    def __init__(self, raw: object, _token: object = None) -> None:
        if _token is not _WRAP_TOKEN:
            raise TypeError("SafeConnection yalnız pools tarafından kurulur.")
        self.__conn = raw
        self.__closed = False
        self.__in_tx = False

    def __repr__(self) -> str:
        return "<SafeConnection>"

    def _detach(self) -> None:
        """Bağlantı havuza dönerken pools çağırır; sonrasında her çağrı DB_DISCONNECTED."""
        self.__closed = True
        self.__conn = None

    def __raw(self, stmt: object, kind: str):
        if not isinstance(stmt, Stmt) or not is_trusted(stmt):
            raise TypeError("Yalnız sql_templates'in ürettiği Stmt çalıştırılabilir.")
        if stmt.kind != kind:
            raise ValueError(f"Bu yöntem yalnız '{kind}' türünde Stmt kabul eder.")
        assert_allowed(stmt.text)
        return self.__live()

    def __live(self):
        if self.__closed:
            raise AppError(ErrorCode.DB_DISCONNECTED)
        return self.__conn

    def query(self, stmt: Stmt, binds: dict | None = None, *, max_rows: int | None = None) -> list[dict]:
        conn = self.__raw(stmt, "select")
        with _ora_errors():
            cur = conn.cursor()
            try:
                cur.execute(stmt.text, binds or {})
                names = [d[0] for d in cur.description or []]
                rows = cur.fetchmany(max_rows) if max_rows is not None else cur.fetchall()
            finally:
                cur.close()
        return [dict(zip(names, r)) for r in rows]

    def query_one(self, stmt: Stmt, binds: dict | None = None) -> dict | None:
        rows = self.query(stmt, binds, max_rows=1)
        return rows[0] if rows else None

    def count(self, stmt: Stmt, binds: dict | None, *, timeout_sec: int) -> int | None:
        """İlk satırın ilk kolonu. Zaman aşımı ya da cancel() → None."""
        conn = self.__raw(stmt, "select")
        previous = conn.call_timeout
        conn.call_timeout = int(timeout_sec * 1000)
        try:
            cur = conn.cursor()
            try:
                cur.execute(stmt.text, binds or {})
                row = cur.fetchone()
            finally:
                cur.close()
        except oracledb.Error as exc:
            if _full_code(exc) in _TIMEOUT_CODES:
                return None
            raise _to_app_error(exc) from None
        finally:
            if not self.__closed:
                conn.call_timeout = previous
        return None if row is None or row[0] is None else int(row[0])

    def cancel(self) -> None:
        """Sürmekte olan sorguyu başka bir thread'den keser (ORA-01013)."""
        if self.__closed:
            return
        with _ora_errors():
            self.__conn.cancel()

    def fetch_arrow(
        self, stmt: Stmt, binds: dict | None, *, columns: list[ColumnInfo], batch_rows: int
    ) -> Iterator[pa.RecordBatch]:
        """Doğrulama çağrı anında yapılır; batch'ler tüketildikçe okunur."""
        conn = self.__raw(stmt, "select")
        schema = arrow_schema_for(columns)
        if type(batch_rows) is not int or batch_rows < 1:
            raise ValueError("Geçersiz batch_rows.")
        if any(_is_text_number(f) for f in schema):
            return self.__fetch_rows(conn, stmt, binds, schema, batch_rows)
        return self.__fetch_frames(conn, stmt, binds, schema, batch_rows)

    def __fetch_frames(self, conn, stmt: Stmt, binds: dict | None, schema: pa.Schema, batch_rows: int):
        with _ora_errors():
            frames = conn.fetch_df_batches(
                stmt.text, binds or {}, size=batch_rows, fetch_decimals=True, requested_schema=schema
            )
            for df in frames:
                table = df if isinstance(df, pa.Table) else pa.table(df)
                table = _cast_table(table, schema)
                for batch in table.combine_chunks().to_batches(max_chunksize=batch_rows):
                    yield batch

    def __fetch_rows(self, conn, stmt: Stmt, binds: dict | None, schema: pa.Schema, batch_rows: int):
        with _ora_errors():
            cur = conn.cursor()
            try:
                cur.arraysize = batch_rows
                cur.outputtypehandler = _decimal_handler
                cur.execute(stmt.text, binds or {})
                names = [d[0] for d in cur.description or []]
                if names != schema.names:
                    raise AppError(ErrorCode.INTERNAL, "Okunan kolonlar beklenen kolonlarla uyuşmuyor.")
                while True:
                    rows = cur.fetchmany(batch_rows)
                    if not rows:
                        break
                    yield _batch_from_rows(rows, schema)
            finally:
                cur.close()

    def execute_create(self, stmt: Stmt) -> None:
        conn = self.__raw(stmt, "create")
        with _ora_errors():
            cur = conn.cursor()
            try:
                cur.execute(stmt.text)
            finally:
                cur.close()

    def insert_many(self, stmt: Stmt, rows: list[tuple], batch_size: int) -> None:
        """Commit etmez; atomiklik için transaction() içinde çağrılır."""
        conn = self.__raw(stmt, "insert")
        if not rows:
            return
        with _ora_errors():
            cur = conn.cursor()
            try:
                cur.executemany(stmt.text, rows, batch_size=batch_size)
            finally:
                cur.close()

    def direct_path_load(
        self, schema: str, table: str, column_names: list[str], rows: list[tuple], batch_size: int
    ) -> None:
        """Direct path yükleme (SQL yok; batch başına örtük commit)."""
        if not is_valid_ident(schema) or not is_valid_ident(table):
            raise ValueError("Geçersiz tanımlayıcı.")
        if not column_names or len(set(column_names)) != len(column_names) or not all(
            is_valid_ident(c) for c in column_names
        ):
            raise ValueError("Geçersiz kolon listesi.")
        conn = self.__live()
        if not rows:
            return
        with _ora_errors():
            conn.direct_path_load(
                schema_name=schema,
                table_name=table,
                column_names=list(column_names),
                data=rows,
                batch_size=batch_size,
            )

    @contextmanager
    def transaction(self) -> Iterator["SafeConnection"]:
        """Autocommit kapalı; BaseException (iptal dahil) → rollback + yeniden fırlat; başarı → commit."""
        conn = self.__live()
        if self.__in_tx:
            raise RuntimeError("İç içe transaction desteklenmez.")
        conn.autocommit = False
        self.__in_tx = True
        try:
            yield self
        except BaseException:
            try:
                if not self.__closed:
                    conn.rollback()
            except oracledb.Error:
                pass  # asıl hata yeniden fırlatılır; kapanan bağlantıda sunucu zaten geri alır
            raise
        else:
            try:
                conn.commit()
            except oracledb.Error as exc:
                try:
                    conn.rollback()
                except oracledb.Error:
                    pass
                raise _to_app_error(exc) from None
        finally:
            self.__in_tx = False


def _wrap(raw: object) -> SafeConnection:
    """Yalnız pools kullanır."""
    return SafeConnection(raw, _WRAP_TOKEN)
