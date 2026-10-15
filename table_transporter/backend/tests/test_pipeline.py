import re
from datetime import datetime
from decimal import Decimal

import pytest

from app.anon.rules import ColumnInfo, compile_plan
from app.config import Settings
from app.log.db import Database
from app.log.repository import Repository
from app.oracle import pools
from app.oracle.guard import assert_allowed
from app.transfer.pipeline import CancelToken, JobSpec, run_transfer
from tests.fakes.fake_oracle import FakeOracle, make_ora_error

KEY = b"k" * 32
TNS = """
DEMO_TEST = (DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db-test.example.invalid)(PORT=1453))
  (CONNECT_DATA=(SERVICE_NAME=DEMOTEST)))
DEMO_DEV = (DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db-dev.example.invalid)(PORT=1453))
  (CONNECT_DATA=(SERVICE_NAME=DEMODEV)))
"""
SRC_RE = r"FETCH FIRST"
OBJ_ROW = [(4242, datetime(2026, 10, 15, 10, 30, 0), "TABLE")]


def col(name, data_type="VARCHAR2", length=50, *, precision=None, scale=None, nullable=True):
    text = data_type in ("CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2")
    return ColumnInfo(
        name=name, data_type=data_type,
        data_length=length if text else 22, char_length=length if text else 0,
        char_used="B" if text else None, precision=precision, scale=scale,
        nullable=nullable, is_pk_or_unique=False, supported=True,
    )


ID = col("ID", "NUMBER", precision=10, scale=0)
NAME = col("NAME")


class TrackingRepo(Repository):
    """track_table anındaki direct path çağrı sayısını kaydeder (write-ahead sırası)."""

    def __init__(self, db, fake):
        super().__init__(db)
        self.fake = fake
        self.track_order: list[int] = []

    def track_table(self, *a, **kw):
        self.track_order.append(len(self.fake.direct_path_calls))
        return super().track_table(*a, **kw)


@pytest.fixture
def fake():
    f = FakeOracle()
    f.on(r"FROM ALL_OBJECTS", OBJ_ROW, columns=["OBJECT_ID", "CREATED", "OBJECT_TYPE"])
    pools.set_driver(f)
    yield f
    pools.set_driver(None)


@pytest.fixture
def cfg(tmp_path) -> Settings:
    (tmp_path / "tnsnames.ora").write_text(TNS, encoding="utf-8")
    return Settings(_env_file=None, tt_tns_admin=str(tmp_path))


@pytest.fixture
def handles(cfg, fake):
    src = pools.open_db(cfg, role="source", alias="DEMO_TEST", user="SRC", password="p")
    tgt = pools.open_db(cfg, role="target", alias="DEMO_DEV", user="TGT", password="p")
    yield src, tgt
    src.close()
    tgt.close()


@pytest.fixture
def repo(tmp_path, fake):
    db = Database(tmp_path / "log.db")
    db.init_schema()
    yield TrackingRepo(db, fake)
    db.close()


def make_job(repo, mode, total=None, job_id="j1"):
    repo.create_job({
        "id": job_id, "owner": "mert", "source_alias": "DEMO_TEST", "source_schema": "S",
        "source_table": "SRC", "target_alias": "DEMO_DEV", "target_schema": "TGT",
        "target_table": "DST", "mode": mode, "filters": [], "total_rows": total,
    }, [])


def make_spec(mode, cols, rules, *, row_limit=1000, direct=True, batch_rows=100, target_cols=None, **kw):
    plan = compile_plan(rules, cols, mode=mode, target_cols=target_cols, key=KEY)
    return JobSpec(
        job_id="j1", owner="mert", mode=mode, source_alias="DEMO_TEST", source_schema="S",
        source_table="SRC", target_alias="DEMO_DEV", target_schema="TGT", target_table="DST",
        where_sql="", binds={}, row_limit=row_limit, plan=plan, read_columns=list(cols),
        use_direct_path=direct, batch_rows=batch_rows, **kw,
    )


def keep_all(cols):
    return [{"column": c.name, "type": "keep", "params": {}} for c in cols]


def run(spec, handles, repo, cancel=None, on_event=None):
    events: list[tuple[str, dict]] = []

    def record(name, data):
        events.append((name, data))
        if on_event:
            on_event(name, data)

    src, tgt = handles
    status = run_transfer(spec, source=src, target=tgt, repo=repo, cancel=cancel or CancelToken(),
                          on_event=record)
    return status, events


def inserted_rows(fake):
    return [r for sql, params in fake.executed if sql.startswith("INSERT") for r in params]


def assert_sql_safe(fake):
    assert fake.executed
    for sql, _ in fake.executed:
        assert_allowed(sql)
        assert re.match(r"(SELECT|INSERT|CREATE TABLE)\b", sql), sql


def names(events):
    return [n for n, _ in events]


def test_create_direct_path_million_rows(fake, handles, repo):
    n = 1_000_000
    fake.on(SRC_RE, [(i, f"N{i}") for i in range(n)], columns=["ID", "NAME"])
    make_job(repo, "create", total=n)
    spec = make_spec("create", [ID, NAME], keep_all([ID, NAME]), row_limit=5_000_000, batch_rows=250_000)
    status, events = run(spec, handles, repo)
    assert status == "done"
    assert len(fake.direct_path_calls) == 4
    assert all(c["column_names"] == ["ID", "NAME"] and c["batch_size"] == 250_000
               for c in fake.direct_path_calls)
    rows = fake.tables["DST"]["rows"]
    assert len(rows) == n and rows[0] == (0, "N0") and rows[-1] == (n - 1, f"N{n - 1}")
    assert repo.track_order == [0]  # izleme ilk yüklemeden ÖNCE
    tracked = repo.tracked_for_job("j1")
    assert tracked["object_id"] == 4242 and tracked["object_created"] == "2026-10-15T10:30:00"
    job = repo.get_job("j1")
    assert job["status"] == "done" and job["rows_written"] == n and job["rows_read"] == n
    assert names(events)[-1] == "done" and "progress" in names(events)
    assert names(events).index("progress") < names(events).index("done")
    done = events[-1][1]
    assert done["rows_written"] == n and done["target"] == {"schema": "TGT", "table": "DST"}
    assert done["finished_at"] == job["finished_at"]
    last_progress = [d for e, d in events if e == "progress"][-1]
    assert set(last_progress) == {"job_id", "status", "rows_read", "rows_written", "total_rows",
                                  "rows_per_sec", "eta_sec"}
    assert last_progress["rows_written"] == n and last_progress["total_rows"] == n
    assert_sql_safe(fake)
    create_sql = [s for s, _ in fake.executed if s.startswith("CREATE")]
    assert len(create_sql) == 1 and '"TGT"."DST"' in create_sql[0]


def test_append_single_commit(fake, handles, repo):
    fake.on(SRC_RE, [(i, f"N{i}") for i in range(250)], columns=["ID", "NAME"])
    make_job(repo, "append")
    spec = make_spec("append", [ID, NAME], keep_all([ID, NAME]), target_cols={"ID": ID, "NAME": NAME},
                     insert_batch=40)
    status, events = run(spec, handles, repo)
    assert status == "done"
    assert fake.commits == 1 and fake.direct_path_calls == []
    assert len(inserted_rows(fake)) == 250
    assert repo.tracked_for_job("j1") is None
    assert repo.get_job("j1")["rows_written"] == 250
    assert not any(s.startswith("CREATE") for s, _ in fake.executed)
    assert_sql_safe(fake)


def test_append_error_midway_rolls_back(fake, handles, repo):
    fake.on(SRC_RE, [(i, f"N{i}") for i in range(500)], columns=["ID", "NAME"])
    make_job(repo, "append")
    spec = make_spec("append", [ID, NAME], keep_all([ID, NAME]), target_cols={"ID": ID, "NAME": NAME})

    def inject(name, data):
        if name == "progress" and not fake._failures:
            fake.fail_next(r"^INSERT", make_ora_error(1, "unique constraint violated"))

    status, events = run(spec, handles, repo, on_event=inject)
    assert status == "failed"
    assert fake.commits == 0 and fake.rollbacks >= 1
    job = repo.get_job("j1")
    assert job["status"] == "failed" and job["rows_written"] == 0 and job["error_code"] == "ORA_ERROR"
    failed = events[-1]
    assert failed[0] == "failed" and failed[1]["status"] == "failed" and failed[1]["rows_written"] == 0
    assert "ORA-00001" in failed[1]["detail"]


def test_cancel_append_rolls_back(fake, handles, repo):
    fake.on(SRC_RE, [(i, f"N{i}") for i in range(500)], columns=["ID", "NAME"])
    make_job(repo, "append")
    spec = make_spec("append", [ID, NAME], keep_all([ID, NAME]), target_cols={"ID": ID, "NAME": NAME})
    cancel = CancelToken()
    status, events = run(spec, handles, repo, cancel=cancel,
                         on_event=lambda n, d: n == "progress" and cancel.cancel())
    assert status == "cancelled"
    assert fake.commits == 0 and fake.rollbacks >= 1
    assert names(events)[-2:] == ["cancelling", "cancelled"]
    assert events[-1][1] == {"job_id": "j1", "status": "cancelled", "rows_written": 0}
    job = repo.get_job("j1")
    assert job["status"] == "cancelled" and job["rows_written"] == 0


def test_cancel_create_keeps_tracking(fake, handles, repo):
    fake.on(SRC_RE, [(i, f"N{i}") for i in range(500)], columns=["ID", "NAME"])
    make_job(repo, "create")
    spec = make_spec("create", [ID, NAME], keep_all([ID, NAME]))
    cancel = CancelToken()
    status, events = run(spec, handles, repo, cancel=cancel,
                         on_event=lambda n, d: n == "progress" and cancel.cancel())
    assert status == "cancelled"
    assert repo.tracked_for_job("j1") is not None
    assert names(events)[-2:] == ["cancelling", "cancelled"]
    written = events[-1][1]["rows_written"]
    assert 0 < written < 500 and written == len(fake.tables["DST"]["rows"])
    assert repo.get_job("j1")["status"] == "cancelled"


def test_cancel_before_start_creates_nothing(fake, handles, repo):
    make_job(repo, "create")
    cancel = CancelToken()
    cancel.cancel()
    status, _ = run(make_spec("create", [ID, NAME], keep_all([ID, NAME])), handles, repo, cancel=cancel)
    assert status == "cancelled" and fake.executed == []
    assert repo.tracked_for_job("j1") is None


def test_hard_cap_source_returns_extra_rows(fake, handles, repo):
    fake.on(SRC_RE, [(i, f"N{i}") for i in range(110)], columns=["ID", "NAME"])
    make_job(repo, "create")
    spec = make_spec("create", [ID, NAME], keep_all([ID, NAME]), row_limit=100, batch_rows=30)
    status, events = run(spec, handles, repo)
    assert status == "done"
    assert len(fake.tables["DST"]["rows"]) == 100
    assert events[-1][1]["rows_written"] == 100
    select = [(s, p) for s, p in fake.executed if "FETCH FIRST" in s]
    assert select[0][1]["tt_limit"] == 100


def test_hard_cap_append(fake, handles, repo):
    fake.on(SRC_RE, [(i, f"N{i}") for i in range(110)], columns=["ID", "NAME"])
    make_job(repo, "append")
    spec = make_spec("append", [ID, NAME], keep_all([ID, NAME]), target_cols={"ID": ID, "NAME": NAME},
                     row_limit=100, batch_rows=30)
    assert run(spec, handles, repo)[0] == "done"
    assert len(inserted_rows(fake)) == 100 and fake.commits == 1


def test_table_exists_failed_not_tracked(fake, handles, repo):
    fake.on(SRC_RE, [(1, "A")], columns=["ID", "NAME"])
    fake.fail_next(r"^CREATE TABLE", make_ora_error(955, "name is already used by an existing object"))
    make_job(repo, "create")
    status, events = run(make_spec("create", [ID, NAME], keep_all([ID, NAME])), handles, repo)
    assert status == "failed"
    job = repo.get_job("j1")
    assert job["status"] == "failed" and job["error_code"] == "TABLE_EXISTS"
    assert repo.tracked_for_job("j1") is None and repo.track_order == []
    assert fake.direct_path_calls == []
    assert events[-1][0] == "failed" and events[-1][1]["error"] == "TABLE_EXISTS"


def test_reader_error_failed(fake, handles, repo):
    fake.fail_next(SRC_RE, make_ora_error(942, "table or view does not exist"))
    make_job(repo, "append")
    spec = make_spec("append", [ID, NAME], keep_all([ID, NAME]), target_cols={"ID": ID, "NAME": NAME})
    status, events = run(spec, handles, repo)
    assert status == "failed" and fake.commits == 0
    assert repo.get_job("j1")["error_code"] == "DB_PERMISSION"
    assert events[-1][0] == "failed"


def test_error_after_create_is_partial(fake, handles, repo):
    fake.fail_next(SRC_RE, make_ora_error(1555, "snapshot too old"))
    make_job(repo, "create")
    status, events = run(make_spec("create", [ID, NAME], keep_all([ID, NAME])), handles, repo)
    assert status == "partial"
    assert repo.get_job("j1")["status"] == "partial"
    assert repo.tracked_for_job("j1") is not None
    assert events[-1][1]["status"] == "partial" and events[-1][1]["error"] == "ORA_ERROR"


def test_unscaled_number_exact_and_hmac_hex(fake, handles, repo):
    amount = col("AMOUNT", "NUMBER")  # precision'sız → metin + metadata
    party = col("PARTY", "NUMBER")
    values = [Decimal("12345678901234567890.123456789"), Decimal("9007199254740993"), None,
              Decimal("-0.000000000000000000000000000001"), Decimal("1E+40")]
    fake.on(SRC_RE, [(v, Decimal(i)) for i, v in enumerate(values)], columns=["AMOUNT", "PARTY"])
    make_job(repo, "create")
    rules = [{"column": "AMOUNT", "type": "keep", "params": {}},
             {"column": "PARTY", "type": "hmac", "params": {}}]
    spec = make_spec("create", [amount, party], rules, direct=False, batch_rows=2)
    status, _ = run(spec, handles, repo)
    assert status == "done"
    rows = inserted_rows(fake)
    assert [r[0] for r in rows] == values
    assert all(isinstance(r[0], Decimal) for r in rows if r[0] is not None)
    assert all(isinstance(r[1], str) and re.fullmatch(r"[0-9a-f]{64}", r[1]) for r in rows)
    assert fake.commits == 3  # create + executemany: batch başına commit
    assert_sql_safe(fake)
    create_sql = next(s for s, _ in fake.executed if s.startswith("CREATE"))
    assert '"PARTY" VARCHAR2(64' in create_sql


def test_on_event_exception_does_not_break_job(fake, handles, repo):
    fake.on(SRC_RE, [(1, "A")], columns=["ID", "NAME"])
    make_job(repo, "create")

    def boom(name, data):
        raise RuntimeError("tüketici hatası")

    status, _ = run(make_spec("create", [ID, NAME], keep_all([ID, NAME])), handles, repo, on_event=boom)
    assert status == "done" and repo.get_job("j1")["status"] == "done"


def test_leases_released_after_run(fake, handles, repo):
    fake.on(SRC_RE, [(1, "A")], columns=["ID", "NAME"])
    make_job(repo, "create")
    src, tgt = handles
    run(make_spec("create", [ID, NAME], keep_all([ID, NAME])), handles, repo)
    assert src._leases == 0 and tgt._leases == 0
    assert all(p.acquired == p.released for p in fake.pools)
