import pytest
from pydantic import ValidationError

from app.config import Settings
from conftest import KEY_1, KEY_2, make_settings


def test_keys_are_split_from_csv():
    s = make_settings(api_keys=f" {KEY_1} , {KEY_2},")
    assert [k.get_secret_value() for k in s.api_keys] == [KEY_1, KEY_2]


def test_reads_prefixed_env_vars(monkeypatch):
    monkeypatch.setenv("IWC_DB_USER", "ETL")
    monkeypatch.setenv("IWC_DB_PASSWORD", "p#1'x")
    monkeypatch.setenv("IWC_DB_DSN", "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=h)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=s)))")
    monkeypatch.setenv("IWC_API_KEYS", KEY_1)
    monkeypatch.setenv("IWC_ORACLE_THICK", "true")
    monkeypatch.setenv("IWC_ORACLE_THICK_LIB_DIR", " ")
    s = Settings(_env_file=None)
    assert s.db_user == "ETL"
    assert s.db_password.get_secret_value() == "p#1'x"
    assert s.db_dsn.startswith("(DESCRIPTION=")
    assert s.oracle_thick is True
    assert s.oracle_thick_lib_dir is None
    assert s.db_call_timeout_ms == 5000 and s.db_pool_max == 4


@pytest.mark.parametrize("over", [
    {"api_keys": ""},
    {"api_keys": " , "},
    {"api_keys": []},
    {"api_keys": "kisa"},
    {"api_keys": "a" * 31},
    {"api_keys": "a" * 129},
    {"api_keys": "a" * 40 + "!"},
    {"api_keys": "a" * 20 + " " + "a" * 20},
    {"api_keys": f"{KEY_1},kisa"},
    {"db_user": ""},
    {"db_password": ""},
    {"db_dsn": ""},
    {"db_call_timeout_ms": 0},
    {"db_pool_max": 0},
])
def test_invalid_settings_are_rejected(over):
    with pytest.raises(ValidationError):
        make_settings(**over)


def test_missing_required_settings_are_rejected(monkeypatch):
    for name in ("IWC_DB_USER", "IWC_DB_PASSWORD", "IWC_DB_DSN", "IWC_API_KEYS"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secrets_do_not_appear_in_errors_or_repr():
    with pytest.raises(ValidationError) as err:
        make_settings(api_keys=f"{KEY_1},cok-gizli-ama-kisa", db_password="parola-gizli", db_pool_max=0)
    assert "cok-gizli-ama-kisa" not in str(err.value) and "parola-gizli" not in str(err.value)
    assert KEY_1 not in repr(make_settings()) and "'p'" not in repr(make_settings().db_password)
