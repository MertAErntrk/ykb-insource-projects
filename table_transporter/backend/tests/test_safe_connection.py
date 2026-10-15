from datetime import datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from app.anon.rules import ColumnInfo
from app.errors import AppError, ErrorCode
from app.oracle import sql_templates as T
from app.oracle.guard import assert_allowed
from app.oracle.safe_connection import SafeConnection, _wrap, arrow_schema_for, to_db_rows
from tests.fakes.fake_oracle import FakeConnection, FakeOracle, make_dpy_error, make_ora_error


def col(name, data_type, precision=None, scale=None):
    return ColumnInfo(name=name, data_type=data_type, data_length=None, char_length=None,
                      char_used=None, precision=precision, scale=scale, nullable=True,
                      is_pk_or_unique=False, supported=True)


@pytest.fixture
def fake():
    return FakeOracle()


@pytest.fixture
def conn(fake):
    return _wrap(FakeConnection(fake))


def _select(columns):
    return T.select_rows("S", "T", columns, "", {}, 10)


# --- Stmt zorunluluğu ----------------------------------------------------------------------


def test_raw_string_rejected(conn):
    with pytest.raises(TypeError):
        conn.query("SELECT 1 FROM DUAL")
    with pytest.raises(TypeError):
        conn.count("SELECT COUNT(*) FROM T", None, timeout_sec=1)
    with pytest.raises(TypeError):
        conn.execute_create("CREATE TABLE S.T (A NUMBER)")
    with pytest.raises(TypeError):
        conn.insert_many("INSERT INTO S.T (A) VALUES (:1)", [(1,)], 10)
    with pytest.raises(TypeError):
        conn.fetch_arrow("SELECT A FROM T", None, columns=[col("A", "NUMBER", 5, 0)], batch_rows=10)


def test_forged_stmt_rejected(conn, fake):
    forged = T.Stmt(text='SELECT "A" FROM "S"."T"', kind="select", _token=object())
    with pytest.raises(TypeError):
        conn.query(forged)
    assert fake.executed == []


def test_kind_mismatch(conn):
    stmt, binds = _select(["A"])
    with pytest.raises(ValueError):
        conn.execute_create(stmt)
    with pytest.raises(ValueError):
        conn.insert_many(stmt, [(1,)], 10)
    with pytest.raises(ValueError):
        conn.query(T.insert_rows("S", "T", ["A"]))


def test_no_raw_members(conn):
    public = {n for n in dir(SafeConnection) if not n.startswith("_")}
    assert public == {"query", "query_one", "count", "cancel", "fetch_arrow", "execute_create",
                      "insert_many", "direct_path_load", "transaction"}
    for banned in ("cursor", "execute", "executemany", "callproc", "callfunc", "raw", "connection"):
        assert not hasattr(SafeConnection, banned)
    assert not hasattr(SafeConnection, "__getattr__")
    assert not hasattr(conn, "__dict__")
    for banned in ("cursor", "execute", "callproc", "raw"):
        assert not hasattr(conn, banned)


def test_cannot_construct_directly(fake):
    with pytest.raises(TypeError):
        SafeConnection(FakeConnection(fake))


def test_detached_connection_unusable(conn):
    conn._detach()
    stmt, binds = _select(["A"])
    with pytest.raises(AppError) as e:
        conn.query(stmt, binds)
    assert e.value.code is ErrorCode.DB_DISCONNECTED


# --- Sorgu, sayım, iptal -------------------------------------------------------------------


def test_query_returns_dicts(conn, fake):
    fake.on(r"FROM ALL_USERS", [("HR",), ("DEMO",)], columns=["USERNAME"])
    stmt, binds = T.dict_schemas(None, 50, 0)
    assert conn.query(stmt, binds) == [{"USERNAME": "HR"}, {"USERNAME": "DEMO"}]
    assert conn.query(stmt, binds, max_rows=1) == [{"USERNAME": "HR"}]
    assert conn.query_one(stmt, binds) == {"USERNAME": "HR"}


def test_count_ok_and_timeout(conn, fake):
    fake.on(r"COUNT\(\*\)", [(42,)], columns=["COUNT(*)"])
    stmt, binds = T.count_rows("S", "T", "", {}, 10)
    assert conn.count(stmt, binds, timeout_sec=5) == 42
    for exc in (make_dpy_error(4024, "call timeout"), make_ora_error(1013, "user cancel"),
                make_ora_error(3156, "timeout")):
        fake.fail_next(r"COUNT\(\*\)", exc)
        assert conn.count(stmt, binds, timeout_sec=1) is None


def test_count_restores_call_timeout(fake):
    raw = FakeConnection(fake)
    raw.call_timeout = 7
    c = _wrap(raw)
    fake.on(r"COUNT\(\*\)", [(1,)], columns=["COUNT(*)"])
    stmt, binds = T.count_rows("S", "T", "", {}, 10)
    c.count(stmt, binds, timeout_sec=3)
    assert raw.call_timeout == 7


def test_count_other_error_mapped(conn, fake):
    fake.fail_next(r"COUNT\(\*\)", make_ora_error(942, "no table"))
    stmt, binds = T.count_rows("S", "T", "", {}, 10)
    with pytest.raises(AppError) as e:
        conn.count(stmt, binds, timeout_sec=1)
    assert e.value.code is ErrorCode.DB_PERMISSION


def test_cancel_recorded(conn, fake):
    conn.cancel()
    assert fake.cancels == 1


# --- Transaction ---------------------------------------------------------------------------


def test_transaction_commit_on_success(conn, fake):
    ins = T.insert_rows("S", "T", ["A", "B"])
    with conn.transaction():
        conn.insert_many(ins, [(1, "x"), (2, "y")], 1000)
    assert (fake.commits, fake.rollbacks) == (1, 0)
    assert fake.executed[-1] == (ins.text, [(1, "x"), (2, "y")])


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt(), SystemExit()])
def test_transaction_rollback_on_error_and_cancel(conn, fake, exc):
    ins = T.insert_rows("S", "T", ["A"])
    with pytest.raises(type(exc)):
        with conn.transaction():
            conn.insert_many(ins, [(1,)], 10)
            raise exc
    assert (fake.commits, fake.rollbacks) == (0, 1)


def test_transaction_ora_error_mapped_no_commit(conn, fake):
    ins = T.insert_rows("S", "T", ["A"])
    fake.fail_next(r"INSERT INTO", make_ora_error(1400, "null"))
    with pytest.raises(AppError) as e:
        with conn.transaction():
            conn.insert_many(ins, [(None,)], 10)
    assert e.value.code is ErrorCode.ORA_ERROR and "ORA-01400" in e.value.detail
    assert (fake.commits, fake.rollbacks) == (0, 1)


def test_execute_create_and_table_exists(conn, fake):
    from app.oracle.sql_templates import ColumnDef

    stmt = T.create_table("S", "T2", [ColumnDef("A", "NUMBER", 22, 0, None, 10, 0, "Y")])
    conn.execute_create(stmt)
    fake.fail_next(r"CREATE TABLE", make_ora_error(955, "exists"))
    with pytest.raises(AppError) as e:
        conn.execute_create(stmt)
    assert e.value.code is ErrorCode.TABLE_EXISTS


def test_direct_path_load(conn, fake):
    conn.direct_path_load("S", "T", ["A", "B"], [(1, "x")], 500)
    assert fake.direct_path_calls == [
        {"schema_name": "S", "table_name": "T", "column_names": ["A", "B"], "batch_size": 500}
    ]
    for bad in (("s", "T", ["A"]), ("S", 'T"', ["A"]), ("S", "T", ["A", "A"]), ("S", "T", [])):
        with pytest.raises(ValueError):
            conn.direct_path_load(*bad, [(1,)], 10)


# --- Tip eşlemesi --------------------------------------------------------------------------


def test_arrow_schema_mapping():
    schema = arrow_schema_for([
        col("I", "NUMBER", 18, 0), col("D", "NUMBER", 38, 0), col("M", "NUMBER", 12, 2),
        col("S", "NUMBER", None, 2), col("U", "NUMBER"), col("F", "FLOAT", 126),
        col("BF", "BINARY_FLOAT"), col("BD", "BINARY_DOUBLE"), col("V", "VARCHAR2"),
        col("N", "NCHAR"), col("DT", "DATE"), col("TS", "TIMESTAMP(6)"), col("NEG", "NUMBER", 5, -2),
    ])
    types = {f.name: f.type for f in schema}
    assert types == {
        "I": pa.int64(), "D": pa.decimal128(38, 0), "M": pa.decimal128(12, 2),
        "S": pa.decimal128(38, 2), "U": pa.string(), "F": pa.string(), "BF": pa.float32(),
        "BD": pa.float64(), "V": pa.string(), "N": pa.string(), "DT": pa.timestamp("s"),
        "TS": pa.timestamp("us"), "NEG": pa.string(),
    }
    assert all(not pa.types.is_floating(types[n]) for n in ("I", "D", "M", "S", "U", "F", "NEG"))
    assert schema.field("U").metadata == {b"tt_number": b"1"}
    assert schema.field("V").metadata is None


def test_arrow_schema_unsupported():
    with pytest.raises(AppError):
        arrow_schema_for([col("L", "CLOB")])


BIG = 2**53 + 1
D38 = Decimal("12345678901234567890123456789012345678")


def test_fetch_arrow_fast_path_lossless(conn, fake):
    columns = [col("I", "NUMBER", 18, 0), col("D", "NUMBER", 38, 0), col("M", "NUMBER", 10, 3),
               col("DT", "DATE"), col("V", "VARCHAR2")]
    rows = [(BIG, D38, Decimal("1.125"), datetime(2024, 1, 2, 3, 4, 5), "a"),
            (None, None, None, None, None)]
    fake.on(r'FROM "S"."T"', rows, columns=["I", "D", "M", "DT", "V"])
    stmt, binds = _select(["I", "D", "M", "DT", "V"])
    batches = list(conn.fetch_arrow(stmt, binds, columns=columns, batch_rows=1000))
    assert fake.df_calls[-1]["fetch_decimals"] is True
    assert fake.df_calls[-1]["requested_schema"] == arrow_schema_for(columns)
    assert len(batches) == 1 and batches[0].schema == arrow_schema_for(columns)
    assert batches[0].schema.field("DT").type == pa.timestamp("s")
    out = to_db_rows(batches[0])
    assert out[0] == (BIG, D38, Decimal("1.125"), datetime(2024, 1, 2, 3, 4, 5), "a")
    assert type(out[0][0]) is int and type(out[0][1]) is Decimal
    assert out[1] == (None, None, None, None, None)


def test_fetch_arrow_unbounded_number_lossless(conn, fake):
    fake.simulate_number_fetch = True  # handler olmasa 0.1 float'a düşerdi
    columns = [col("U", "NUMBER"), col("F", "FLOAT", 126), col("I", "NUMBER", 10, 0)]
    values = [Decimal("0.1"), Decimal("12345678901234567890.123"), Decimal("1E+125"), None]
    rows = [(v, v, Decimal(i)) for i, v in enumerate(values)]
    fake.on(r'FROM "S"."T"', rows, columns=["U", "F", "I"])
    stmt, binds = _select(["U", "F", "I"])
    batches = list(conn.fetch_arrow(stmt, binds, columns=columns, batch_rows=3))
    assert [b.num_rows for b in batches] == [3, 1]
    assert all(b.schema == arrow_schema_for(columns) for b in batches)
    assert fake.df_calls == []  # metin-sayı kolonu: fetchmany yolu
    out = [r for b in batches for r in to_db_rows(b)]
    assert [r[0] for r in out] == values
    assert [r[1] for r in out] == values
    assert [r[2] for r in out] == [0, 1, 2, 3] and all(type(r[2]) is int for r in out)
    assert batches[0].column(0).to_pylist()[1] == "12345678901234567890.123"


def test_fetch_arrow_unsafe_cast_is_internal_error(conn, fake):
    fake.on(r'FROM "S"."T"', [(Decimal("1.5"),)], columns=["M"])
    stmt, binds = _select(["M"])
    with pytest.raises(AppError) as e:
        list(conn.fetch_arrow(stmt, binds, columns=[col("M", "NUMBER", 1, 0)], batch_rows=10))
    assert e.value.code is ErrorCode.INTERNAL


def test_fetch_rows_unsafe_value_is_internal_error(conn, fake):
    fake.simulate_number_fetch = True
    fake.on(r'FROM "S"."T"', [(Decimal("1.5"), Decimal("1"))], columns=["I", "U"])
    stmt, binds = _select(["I", "U"])
    columns = [col("I", "NUMBER", 5, 0), col("U", "NUMBER")]
    with pytest.raises(AppError) as e:
        list(conn.fetch_arrow(stmt, binds, columns=columns, batch_rows=10))
    assert e.value.code is ErrorCode.INTERNAL


def test_fetch_arrow_validates_eagerly(conn):
    with pytest.raises(TypeError):
        conn.fetch_arrow("SELECT 1", None, columns=[], batch_rows=10)


def test_to_db_rows_types():
    schema = arrow_schema_for([col("I", "NUMBER", 9, 0), col("M", "NUMBER", 5, 2), col("U", "NUMBER"),
                               col("TS", "TIMESTAMP(6)"), col("V", "VARCHAR2"), col("BD", "BINARY_DOUBLE")])
    batch = pa.RecordBatch.from_arrays([
        pa.array([1, None], pa.int64()), pa.array([Decimal("1.50"), None], pa.decimal128(5, 2)),
        pa.array(["0.1", None]), pa.array([datetime(2024, 5, 6, 7, 8, 9, 123456), None], pa.timestamp("us")),
        pa.array(["x", None]), pa.array([0.5, None]),
    ], schema=schema)
    rows = to_db_rows(batch)
    assert rows[0] == (1, Decimal("1.50"), Decimal("0.1"), datetime(2024, 5, 6, 7, 8, 9, 123456), "x", 0.5)
    assert type(rows[0][2]) is Decimal and rows[0][3].tzinfo is None
    assert rows[1] == (None,) * 6


def test_every_recorded_sql_passes_guard(conn, fake):
    fake.on(r"COUNT\(\*\)", [(1,)], columns=["COUNT(*)"])
    fake.on(r'FROM "S"."T"', [(1,)], columns=["A"])
    stmt, binds = _select(["A"])
    conn.query(stmt, binds)
    list(conn.fetch_arrow(stmt, binds, columns=[col("A", "NUMBER", 5, 0)], batch_rows=10))
    conn.count(*T.count_rows("S", "T", "", {}, 10), timeout_sec=1)
    with conn.transaction():
        conn.insert_many(T.insert_rows("S", "T", ["A"]), [(1,)], 10)
    assert len(fake.executed) == 4
    for sql, _ in fake.executed:
        assert_allowed(sql)
