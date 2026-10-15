"""TABAN sahte Oracle sürücüsü (P4a genişletir). oracledb ile aynı anahtar adları.

Kullanım:
    fake = FakeOracle()
    fake.on(r"FROM ALL_TABLES", lambda sql, params: [("T1", 10)], columns=["TABLE_NAME", "NUM_ROWS"])
    fake.fail_next(r"CREATE TABLE", make_ora_error(955, "name is already used"))
    pool = fake.create_pool(user="u", password="p", dsn="d", min=1, max=2, increment=1)
Her execute/executemany/fetch_df_batches çağrısı `fake.executed`'a (sql, params) olarak girer.

P4a genişletmesi: `version` (bağlantı sürümü), `thin`, `cancels`, `pools`, `df_calls`
(fetch_df_batches anahtar argümanları), `fail_connect` (create_pool/acquire'da bir kez),
`simulate_number_fetch` (True ise outputtypehandler Decimal istemeyen NUMBER kolonu gerçek
sürücü gibi int/float döner: kayıplı yolun yakalanması için), direct_path_load gerçek imza.
"""

import re
from datetime import datetime
from decimal import Decimal
from collections.abc import Callable, Iterable
from typing import Any

import oracledb
import pyarrow as pa


class _FakeErrorInfo:
    def __init__(self, code: int, message: str):
        self.code = code
        self.full_code = f"ORA-{code:05d}"
        self.message = f"{self.full_code}: {message}"
        self.isrecoverable = False

    def __str__(self) -> str:
        return self.message


class FakeDatabaseError(oracledb.DatabaseError):
    pass


def make_ora_error(code: int, msg: str) -> oracledb.DatabaseError:
    """oracledb.DatabaseError alt sınıfı; args[0] .code/.full_code/.message taşır."""
    return FakeDatabaseError(_FakeErrorInfo(code, msg))


def make_dpy_error(num: int, msg: str) -> oracledb.DatabaseError:
    """Sürücü (DPY-xxxx) hatası; .code 0, .full_code 'DPY-4024' gibi."""
    info = _FakeErrorInfo(0, msg)
    info.full_code = f"DPY-{num:04d}"
    info.message = f"{info.full_code}: {msg}"
    return FakeDatabaseError(info)


Handler = Callable[[str, Any], Iterable[tuple] | None] | Iterable[tuple]


class FakeOracle:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.direct_path_calls: list[dict[str, Any]] = []
        self.tables: dict[str, dict[str, Any]] = {}  # ad -> {"columns": [...], "rows": [...]}
        self.connect_calls: list[dict[str, Any]] = []  # şifre KAYDEDİLMEZ
        self._handlers: list[tuple[re.Pattern[str], Handler, list[str] | None]] = []
        self._failures: list[tuple[re.Pattern[str], BaseException]] = []
        self.version = "19.0.0.0"
        self.thin = True
        self.cancels = 0
        self.pools: list[FakePool] = []
        self.df_calls: list[dict[str, Any]] = []
        self.fail_connect: BaseException | None = None
        self.simulate_number_fetch = False
        self.init_client_calls: list[dict[str, Any]] = []

    # --- kayıt ---
    def on(self, pattern_regex: str, handler: Handler, columns: list[str] | None = None) -> None:
        """SQL'e (re.search, büyük/küçük harf duyarsız) uyan çağrıya yanıt. handler: satır listesi
        ya da (sql, params) -> satır listesi. columns: cursor.description adları."""
        self._handlers.append((re.compile(pattern_regex, re.I | re.S), handler, columns))

    def fail_next(self, pattern: str, exc: BaseException) -> None:
        """Uyan bir sonraki çağrıda exc fırlatılır (bir kez)."""
        self._failures.append((re.compile(pattern, re.I | re.S), exc))

    def _record(self, sql: str, params: Any) -> None:
        self.executed.append((sql, params))
        for i, (pat, exc) in enumerate(self._failures):
            if pat.search(sql):
                del self._failures[i]
                raise exc

    def _lookup(self, sql: str, params: Any) -> tuple[list[tuple], list[str] | None]:
        for pat, handler, columns in reversed(self._handlers):  # son kayıt öncelikli
            if pat.search(sql):
                rows = handler(sql, params) if callable(handler) else handler
                return list(rows or []), columns
        return [], None

    def _take_connect_failure(self) -> None:
        if self.fail_connect is not None:
            exc, self.fail_connect = self.fail_connect, None
            raise exc

    # --- sürücü yüzeyi ---
    def is_thin_mode(self) -> bool:
        return self.thin

    def init_oracle_client(self, lib_dir: str | None = None, **kw: Any) -> None:
        self.init_client_calls.append({"lib_dir": lib_dir, **kw})
        self.thin = False

    def connect(self, user: str, password: str, dsn: str, **kw: Any) -> "FakeConnection":
        self.connect_calls.append({"user": user, "dsn": dsn, **{k: v for k, v in kw.items()}})
        return FakeConnection(self)

    def create_pool(self, user: str, password: str, dsn: str, min: int = 1, max: int = 2,
                    increment: int = 1, **kw: Any) -> "FakePool":
        self.connect_calls.append({"user": user, "dsn": dsn, "min": min, "max": max,
                                   "increment": increment, **kw})
        self._take_connect_failure()
        pool = FakePool(self)
        self.pools.append(pool)
        return pool


class FakePool:
    def __init__(self, oracle: FakeOracle) -> None:
        self._oracle = oracle
        self.closed = False
        self.acquired = 0
        self.released = 0

    def acquire(self, **kw: Any) -> "FakeConnection":
        if self.closed:
            raise AssertionError("kapalı pool'dan acquire")
        self._oracle._take_connect_failure()
        self.acquired += 1
        return FakeConnection(self._oracle, pool=self)

    def release(self, connection: "FakeConnection") -> None:
        self.released += 1
        connection.close()

    def close(self, force: bool = False) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, oracle: FakeOracle, pool: FakePool | None = None) -> None:
        self._oracle = oracle
        self._pool = pool
        self.autocommit = False
        self.call_timeout = 0
        self.closed = False

    @property
    def version(self) -> str:
        return self._oracle.version

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self._oracle)

    def cancel(self) -> None:
        self._oracle.cancels += 1

    def commit(self) -> None:
        self._oracle.commits += 1

    def rollback(self) -> None:
        self._oracle.rollbacks += 1

    def close(self) -> None:
        self.closed = True

    def direct_path_load(self, schema_name: str, table_name: str, column_names: list[str],
                         data: Any, *, batch_size: int = 4294967295) -> None:
        self._oracle.direct_path_calls.append({
            "schema_name": schema_name, "table_name": table_name,
            "column_names": list(column_names), "batch_size": batch_size,
        })
        store = self._oracle.tables.setdefault(table_name, {"columns": list(column_names), "rows": []})
        store["rows"].extend(data)

    def fetch_df_batches(self, statement: str, parameters: Any = None, size: int = 100_000, **kw: Any):
        self._oracle._record(statement, parameters)
        self._oracle.df_calls.append(dict(kw))
        rows, columns = self._oracle._lookup(statement, parameters)
        names = columns or [f"C{i}" for i in range(len(rows[0]) if rows else 0)]
        schema = kw.get("requested_schema")
        for start in range(0, len(rows), size):
            chunk = rows[start:start + size]
            data = {n: [r[i] for r in chunk] for i, n in enumerate(names)}
            table = pa.table(data)
            if schema is not None:
                try:  # sığmayan değerde ham tablo döner: çağıranın güvenli cast'i yakalamalı
                    table = table.cast(schema)
                except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
                    pass
            yield table


class _FakeVar:
    def __init__(self, typ: Any, arraysize: int = 1) -> None:
        self.type = typ
        self.arraysize = arraysize


class _FakeMeta:
    def __init__(self, name: str, type_code: Any) -> None:
        self.name = name
        self.type_code = type_code


def _type_code(values: list[Any]) -> Any:
    for v in values:
        if v is None:
            continue
        if isinstance(v, (bool, int, Decimal)):
            return oracledb.DB_TYPE_NUMBER
        if isinstance(v, float):
            return oracledb.DB_TYPE_BINARY_DOUBLE
        if isinstance(v, datetime):
            return oracledb.DB_TYPE_DATE
        return oracledb.DB_TYPE_VARCHAR
    return oracledb.DB_TYPE_VARCHAR


def _default_number(v: Any) -> Any:
    """Gerçek sürücünün varsayılanı: tam sayı → int, diğer → float (kayıplı)."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


class FakeCursor:
    def __init__(self, oracle: FakeOracle) -> None:
        self._oracle = oracle
        self._rows: list[tuple] = []
        self._pos = 0
        self.description: list[tuple] | None = None
        self.arraysize = 100
        self.rowcount = -1
        self.outputtypehandler: Callable[..., Any] | None = None

    def var(self, typ: Any, size: int = 0, arraysize: int = 1, **kw: Any) -> _FakeVar:
        return _FakeVar(typ, arraysize)

    def _apply_types(self, columns: list[str]) -> None:
        if not self._oracle.simulate_number_fetch or not self._rows:
            return
        convert = []
        for i, name in enumerate(columns):
            code = _type_code([r[i] for r in self._rows])
            var = self.outputtypehandler(self, _FakeMeta(name, code)) if self.outputtypehandler else None
            convert.append(code is oracledb.DB_TYPE_NUMBER and not (var is not None and var.type is Decimal))
        self._rows = [
            tuple(_default_number(v) if convert[i] else v for i, v in enumerate(r)) for r in self._rows
        ]

    def execute(self, statement: str, parameters: Any = None, **kw: Any) -> "FakeCursor":
        self._oracle._record(statement, parameters if parameters is not None else kw or None)
        self._rows, columns = self._oracle._lookup(statement, parameters)
        self._pos = 0
        self.rowcount = len(self._rows)
        self.description = [(c, None, None, None, None, None, None) for c in columns] if columns else None
        if columns:
            self._apply_types(columns)
        return self

    def executemany(self, statement: str, parameters: Any, batch_size: int | None = None, **kw: Any) -> None:
        self._oracle._record(statement, parameters)
        self.rowcount = len(parameters) if hasattr(parameters, "__len__") else -1

    def fetchone(self) -> tuple | None:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchmany(self, size: int | None = None) -> list[tuple]:
        n = size or self.arraysize
        out = self._rows[self._pos:self._pos + n]
        self._pos += len(out)
        return out

    def fetchall(self) -> list[tuple]:
        out = self._rows[self._pos:]
        self._pos = len(self._rows)
        return out

    def callproc(self, *a: Any, **kw: Any) -> None:
        raise AssertionError("callproc çağrılmamalı")

    def callfunc(self, *a: Any, **kw: Any) -> None:
        raise AssertionError("callfunc çağrılmamalı")

    def close(self) -> None:
        pass
