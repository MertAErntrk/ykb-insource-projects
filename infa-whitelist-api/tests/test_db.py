import logging
import threading
from decimal import Decimal

import oracledb
import pytest

import app.db
from app.db import (ACQUIRE_WAIT_MS, CHECK_SQL, CONNECT_TIMEOUT_SEC, CheckUnavailable, UnexpectedResult,
                    WhitelistDb, init_driver, to_flag)
from conftest import make_settings


class FakeCursor:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.driver.executed.append((sql, params))
        if self.driver.execute_error:
            raise self.driver.execute_error

    def fetchone(self):
        return self.driver.row


class FakeConn:
    call_timeout = None

    def __init__(self, pool):
        self.pool = pool

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.pool.released += 1
        return False

    def cursor(self):
        return FakeCursor(self.pool.driver)


class FakePool:
    def __init__(self, driver):
        self.driver = driver
        self.conns = []
        self.released = 0
        self.closed = False

    def acquire(self):
        if self.driver.acquire_error:
            raise self.driver.acquire_error
        conn = FakeConn(self)
        self.conns.append(conn)
        return conn

    def close(self, force=False):
        self.closed = True


class FakeDriver:
    def __init__(self, row=(0,)):
        self.row = row
        self.executed = []
        self.pools = []
        self.pool_kwargs = []
        self.create_error = self.acquire_error = self.execute_error = None
        self.thin = True
        self.init_calls = []

    def create_pool(self, **kw):
        self.pool_kwargs.append(kw)
        if self.create_error:
            raise self.create_error
        pool = FakePool(self)
        self.pools.append(pool)
        return pool

    def is_thin_mode(self):
        return self.thin

    def init_oracle_client(self, lib_dir=None):
        self.init_calls.append(lib_dir)
        self.thin = False


def make_db(driver, **settings):
    return WhitelistDb(make_settings(**settings), driver=driver)


def test_values_go_only_as_binds():
    driver = FakeDriver((1,))
    hostile = "x') OR 1=1 --"
    assert make_db(driver).check(hostile, "wf_m_X") == 1
    assert driver.executed == [(CHECK_SQL, {"p_folder": hostile, "p_workflow": "wf_m_X"})]
    assert "EDWPARAM.FN_CHECK_INFA_WHITELIST(:p_folder, :p_workflow)" in CHECK_SQL
    assert hostile not in CHECK_SQL


@pytest.mark.parametrize("value, expected", [
    (0, 0), (1, 1), (0.0, 0), (1.0, 1), (Decimal("0"), 0), (Decimal("1"), 1), ("0", 0), ("1", 1), (" 1 ", 1),
])
def test_flag_values(value, expected):
    assert to_flag(value) == expected
    assert make_db(FakeDriver((value,))).check("F", "W") == expected


@pytest.mark.parametrize("value", [None, 2, -1, 0.5, float("nan"), Decimal("2"), "", "x", "01", "1.0", True, False, b"1"])
def test_anything_else_is_an_error_not_zero(value):
    with pytest.raises(UnexpectedResult):
        to_flag(value)
    with pytest.raises(UnexpectedResult):
        make_db(FakeDriver((value,))).check("F", "W")


def test_no_row_is_an_error():
    with pytest.raises(UnexpectedResult):
        make_db(FakeDriver(None)).check("F", "W")


def test_pool_is_created_once_with_timeouts():
    driver = FakeDriver((0,))
    db = make_db(driver, db_user="ETL", db_password="s3cret", db_dsn="h:1521/svc", db_pool_max=3)
    db.check("F", "W")
    db.check("F", "W")
    assert len(driver.pools) == 1
    assert driver.pool_kwargs == [dict(
        user="ETL", password="s3cret", dsn="h:1521/svc", min=1, max=3, increment=1,
        getmode=oracledb.POOL_GETMODE_TIMEDWAIT, wait_timeout=ACQUIRE_WAIT_MS,
        tcp_connect_timeout=CONNECT_TIMEOUT_SEC,
    )]


def test_call_timeout_is_set_and_connection_released():
    driver = FakeDriver((0,))
    make_db(driver, db_call_timeout_ms=1234).check("F", "W")
    pool = driver.pools[0]
    assert pool.conns[0].call_timeout == 1234
    assert pool.released == 1


def test_pool_open_failure_is_retried_after_cooldown(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(app.db, "monotonic", lambda: now[0])
    driver = FakeDriver((1,))
    db = make_db(driver)
    driver.create_error = oracledb.OperationalError("DPY-6005: cannot connect")
    with pytest.raises(CheckUnavailable):
        db.check("F", "W")
    driver.create_error = None
    with pytest.raises(CheckUnavailable):  # soğuma süresinde yeniden denenmez
        db.check("F", "W")
    assert len(driver.pool_kwargs) == 1
    now[0] += app.db.POOL_RETRY_SEC
    assert db.check("F", "W") == 1
    assert len(driver.pool_kwargs) == 2


def test_concurrent_calls_do_not_each_retry_a_failing_pool_open():
    driver = FakeDriver((0,))
    driver.create_error = oracledb.OperationalError("DPY-6005: cannot connect")
    db = make_db(driver)
    errors = []

    def call():
        try:
            db.check("F", "W")
        except CheckUnavailable:
            errors.append(1)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(errors) == 8
    assert len(driver.pool_kwargs) == 1


def test_call_does_not_wait_long_behind_a_slow_pool_open(monkeypatch):
    monkeypatch.setattr(app.db, "LOCK_WAIT_SEC", 0.05)
    entered, release = threading.Event(), threading.Event()
    driver = FakeDriver((1,))
    real_create = driver.create_pool

    def slow_create(**kw):
        entered.set()
        release.wait(timeout=10)
        return real_create(**kw)

    driver.create_pool = slow_create
    db = make_db(driver)
    results = []
    first = threading.Thread(target=lambda: results.append(db.check("F", "W")))
    first.start()
    assert entered.wait(timeout=10)
    with pytest.raises(CheckUnavailable):  # kilidi bekleyen çağrı süre dolunca hata alır
        db.check("F", "W")
    release.set()
    first.join(timeout=10)
    assert results == [1]
    assert db.check("F", "W") == 1


@pytest.mark.parametrize("stage", ["acquire_error", "execute_error"])
def test_db_errors_become_unavailable(stage):
    driver = FakeDriver((0,))
    setattr(driver, stage, oracledb.DatabaseError("ORA-03113"))
    with pytest.raises(CheckUnavailable):
        make_db(driver).check("F", "W")
    if stage == "execute_error":
        assert driver.pools[0].released == 1


def test_non_db_errors_are_not_swallowed():
    driver = FakeDriver((0,))
    driver.execute_error = RuntimeError("hata")
    with pytest.raises(RuntimeError):
        make_db(driver).check("F", "W")


def test_close():
    driver = FakeDriver((0,))
    db = make_db(driver)
    db.close()  # havuz yokken hata vermez
    db.check("F", "W")
    db.close()
    db.close()
    assert driver.pools[0].closed


def test_init_driver_thin_by_default():
    driver = FakeDriver()
    assert init_driver(make_settings(), driver) == "thin"
    assert driver.init_calls == []


@pytest.mark.parametrize("lib_dir", [None, r"C:\oracle\instantclient"])
def test_init_driver_thick(lib_dir):
    driver = FakeDriver()
    settings = make_settings(oracle_thick=True, oracle_thick_lib_dir=lib_dir)
    assert init_driver(settings, driver) == "thick"
    assert init_driver(settings, driver) == "thick"  # ikinci çağrı istemciyi yeniden yüklemez
    assert driver.init_calls == [lib_dir]


@pytest.mark.parametrize("dsn, warns", [
    ("h:1521/s", True),
    ("(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=h)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=s)))", True),
    ("(DESCRIPTION=(TRANSPORT_CONNECT_TIMEOUT=5)(ADDRESS=(PROTOCOL=TCP)(HOST=h)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=s)))", False),
    ("h:1521/s?transport_connect_timeout=5", False),
])
def test_thick_mode_warns_when_dsn_has_no_connect_timeout(dsn, warns, caplog):
    with caplog.at_level(logging.WARNING, logger="iwc.db"):
        init_driver(make_settings(oracle_thick=True, db_dsn=dsn), FakeDriver())
    assert ("TRANSPORT_CONNECT_TIMEOUT" in caplog.text) is warns


def test_thin_mode_does_not_warn_about_connect_timeout(caplog):
    with caplog.at_level(logging.WARNING, logger="iwc.db"):
        init_driver(make_settings(), FakeDriver())
    assert caplog.text == ""
