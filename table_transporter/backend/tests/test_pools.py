import pytest

from app.config import Settings
from app.errors import AppError, ErrorCode
from app.oracle import pools
from app.oracle import sql_templates as T
from app.oracle.safe_connection import SafeConnection
from tests.fakes.fake_oracle import FakeOracle, make_dpy_error, make_ora_error

SECRET = "S3cr3t-Parola-xyz"
TNS = """
DEMO_TEST = (DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db-test.example.invalid)(PORT=1453))
  (CONNECT_DATA=(SERVICE_NAME=DEMOTEST)))
DEMOLIVE = (DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db-live.example.invalid)(PORT=1521))
  (CONNECT_DATA=(SERVICE_NAME=DEMOLIVE)))
"""


@pytest.fixture
def cfg(tmp_path) -> Settings:
    (tmp_path / "tnsnames.ora").write_text(TNS, encoding="utf-8")
    return Settings(_env_file=None, tt_tns_admin=str(tmp_path),
                    tt_service_accounts_file=str(tmp_path / "service_accounts.toml"))


@pytest.fixture
def fake():
    f = FakeOracle()
    pools.set_driver(f)
    yield f
    pools.set_driver(None)


def _open(cfg, role="source", alias="DEMO_TEST", user="APP_USER", password=SECRET):
    return pools.open_db(cfg, role=role, alias=alias, user=user, password=password)


def _all_strings(obj) -> str:
    parts = [repr(obj)]
    cls = type(obj)
    for name in getattr(cls, "__slots__", ()):
        if name.startswith("__") and not name.endswith("__"):
            name = f"_{cls.__name__}{name}"  # isim karıştırma (name mangling)
        parts.append(repr(getattr(obj, name, None)))
    if hasattr(obj, "__dict__"):
        parts.append(repr(vars(obj)))
    return " ".join(parts)


def test_driver_default_and_override(fake):
    assert pools.get_driver() is fake
    pools.set_driver(None)
    import oracledb

    assert pools.get_driver() is oracledb


def test_init_driver_thin_and_thick(cfg, fake, monkeypatch):
    assert pools.init_driver(cfg) == "thin"
    assert fake.init_client_calls == []
    monkeypatch.setattr(pools, "_thick_initialized", False)
    thick = cfg.model_copy(update={"tt_oracle_thick_lib_dir": "C:/oracle/instantclient"})
    assert pools.init_driver(thick) == "thick"
    assert pools.init_driver(thick) == "thick"
    assert len(fake.init_client_calls) == 1


def test_open_db_success(cfg, fake):
    h = _open(cfg)
    assert h.info() == {"role": "source", "alias": "DEMO_TEST", "db_version": "19.0.0.0",
                        "user": "APP_USER", "mode": "thin", "direct_path": True, "max_ident_len": 128}
    call = fake.connect_calls[-1]
    assert call["min"] == 1 and call["max"] == cfg.tt_max_concurrent_jobs + 2 and call["increment"] == 1
    assert call["tcp_connect_timeout"] == 10 and "db-test.example.invalid" in call["dsn"]
    assert "password" not in call
    with h.connection() as c:
        assert isinstance(c, SafeConnection)


def test_open_db_thick_has_no_direct_path(cfg, fake):
    fake.thin = False
    h = _open(cfg)
    assert h.mode == "thick" and h.direct_path is False


def test_password_not_kept(cfg, fake):
    h = _open(cfg)
    assert SECRET not in _all_strings(h)
    with h.job_lease() as lease:
        assert SECRET not in _all_strings(lease)
        with lease.connection() as c:
            assert SECRET not in _all_strings(c)
    assert SECRET not in repr(fake.connect_calls)


def test_target_denied(cfg, fake):
    with pytest.raises(AppError) as e:
        _open(cfg, role="target", alias="DEMOLIVE")
    assert e.value.code is ErrorCode.TARGET_DENIED
    assert fake.connect_calls == []
    assert _open(cfg, role="source", alias="demolive").alias == "DEMOLIVE"


def test_unknown_alias_not_found(cfg, fake):
    with pytest.raises(AppError) as e:
        _open(cfg, alias="NOPE")
    assert e.value.code is ErrorCode.NOT_FOUND


@pytest.mark.parametrize("user,password", [
    ("bad user", SECRET), ("", SECRET), ("A" * 129, SECRET), ("U;X", SECRET), ("APP", ""), ("APP", None),
])
def test_validation_before_create_pool(cfg, fake, user, password):
    with pytest.raises(AppError) as e:
        _open(cfg, user=user, password=password)
    assert e.value.code is ErrorCode.VALIDATION
    assert fake.connect_calls == []


def test_invalid_role(cfg, fake):
    with pytest.raises(AppError) as e:
        _open(cfg, role="service")
    assert e.value.code is ErrorCode.VALIDATION


def test_old_version_rejected_and_pool_closed(cfg, fake):
    fake.version = "11.2.0.4.0"
    with pytest.raises(AppError) as e:
        _open(cfg)
    assert e.value.code is ErrorCode.DB_CONNECT_FAILED
    assert fake.pools[-1].closed


@pytest.mark.parametrize("version,expected", [("12.1.0.2.0", 30), ("12.2.0.1.0", 128), ("23.4.0.24.5", 128)])
def test_max_ident_len(cfg, fake, version, expected):
    fake.version = version
    assert _open(cfg).max_ident_len == expected


def test_connect_errors_mapped(cfg, fake):
    fake.fail_connect = make_ora_error(1017, "invalid username/password")
    with pytest.raises(AppError) as e:
        _open(cfg)
    assert e.value.code is ErrorCode.DB_CONNECT_FAILED and "şifre" in e.value.detail
    fake.fail_connect = make_dpy_error(6005, "cannot connect")
    with pytest.raises(AppError) as e:
        _open(cfg)
    assert e.value.code is ErrorCode.DB_CONNECT_FAILED and "DPY-6005" in e.value.detail
    assert SECRET not in str(e.value)


def test_connection_error_rolls_back_then_releases(cfg, fake):
    h = _open(cfg)
    pool = fake.pools[-1]
    with pytest.raises(RuntimeError):
        with h.connection():
            raise RuntimeError("x")
    assert fake.rollbacks == 1 and pool.released == pool.acquired


def test_connection_detached_after_release(cfg, fake):
    h = _open(cfg)
    with h.connection() as c:
        pass
    stmt, binds = T.dict_schemas(None, 10, 0)
    with pytest.raises(AppError) as e:
        c.query(stmt, binds)
    assert e.value.code is ErrorCode.DB_DISCONNECTED


def test_close_idempotent_and_disconnects(cfg, fake):
    h = _open(cfg)
    h.close()
    h.close()
    assert fake.pools[-1].closed
    with pytest.raises(AppError) as e:
        with h.connection():
            pass
    assert e.value.code is ErrorCode.DB_DISCONNECTED


def test_close_deferred_while_lease_active(cfg, fake):
    h = _open(cfg)
    pool = fake.pools[-1]
    fake.on(r"FROM ALL_USERS", [("HR",)], columns=["USERNAME"])
    stmt, binds = T.dict_schemas(None, 10, 0)
    with h.job_lease() as lease:
        h.close()
        assert not pool.closed
        with pytest.raises(AppError) as e:
            with h.connection():
                pass
        assert e.value.code is ErrorCode.DB_DISCONNECTED
        with lease.connection() as c:
            assert c.query(stmt, binds) == [{"USERNAME": "HR"}]
    assert pool.closed
    with pytest.raises(AppError):
        with lease.connection():
            pass


def test_lease_on_closed_handle_rejected(cfg, fake):
    h = _open(cfg)
    h.close()
    with pytest.raises(AppError) as e:
        with h.job_lease():
            pass
    assert e.value.code is ErrorCode.DB_DISCONNECTED


# --- Servis hesabı -------------------------------------------------------------------------


def test_service_account_default_and_alias(cfg, fake, tmp_path):
    (tmp_path / "service_accounts.toml").write_text(
        '[default]\nuser = "SVC_DEF"\npassword = "p1"\n\n'
        '[alias.DEMOLIVE]\nuser = "SVC_LIVE"\npassword = "p2"\n', encoding="utf-8")
    h = pools.open_service_db(cfg, "DEMO_TEST")
    assert (h.role, h.user, h.alias) == ("service", "SVC_DEF", "DEMO_TEST")
    h2 = pools.open_service_db(cfg, "DEMOLIVE")
    assert h2.user == "SVC_LIVE"
    assert "p2" not in _all_strings(h2)


def test_service_account_missing(cfg, fake, tmp_path):
    with pytest.raises(AppError) as e:
        pools.open_service_db(cfg, "DEMO_TEST")
    assert e.value.code is ErrorCode.DB_CONNECT_FAILED
    assert e.value.detail == "Servis hesabı tanımlı değil: DEMO_TEST"
    (tmp_path / "service_accounts.toml").write_text('[alias.DEMOLIVE]\nuser = "X"\npassword = "p"\n',
                                                    encoding="utf-8")
    with pytest.raises(AppError) as e:
        pools.open_service_db(cfg, "DEMO_TEST")
    assert e.value.detail == "Servis hesabı tanımlı değil: DEMO_TEST"
    assert fake.connect_calls == []
