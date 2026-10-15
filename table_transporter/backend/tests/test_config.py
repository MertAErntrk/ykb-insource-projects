import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings

K1 = "a" * 32
K2 = "b" * 32


def make(**kw):
    base = dict(tt_anon_hmac_key=K1, tt_session_secret=K2)
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_defaults():
    s = make()
    assert s.tt_row_limit == 5_000_000
    assert s.tt_retention_days == 7
    assert s.ldap_server_uri == "ldaps://ldaps.kfs.local:636"
    assert s.ldap_timeout_sec == 10
    assert s.ldap_tls_verify is False
    assert s.tt_env == "local"


def test_short_key_rejected():
    with pytest.raises(ValidationError) as ei:
        make(tt_anon_hmac_key="kisa")
    assert "kisa" not in str(ei.value)
    with pytest.raises(ValidationError):
        make(tt_session_secret="b" * 31)


def test_equal_keys_rejected():
    with pytest.raises(ValidationError):
        make(tt_session_secret=K1)


def test_missing_keys_rejected(monkeypatch):
    monkeypatch.delenv("TT_ANON_HMAC_KEY")
    monkeypatch.delenv("TT_SESSION_SECRET")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_row_limit_bounds():
    assert make(tt_row_limit=5_000_000).tt_row_limit == 5_000_000
    with pytest.raises(ValidationError):
        make(tt_row_limit=5_000_001)
    with pytest.raises(ValidationError):
        make(tt_row_limit=0)


@pytest.mark.parametrize("value", [None, "", " , "])
def test_deny_aliases_required(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TT_TARGET_DENY_ALIASES", raising=False)
    else:
        monkeypatch.setenv("TT_TARGET_DENY_ALIASES", value)
    with pytest.raises(ValidationError) as ei:
        make()
    assert "TT_TARGET_DENY_ALIASES" in str(ei.value)


def test_lists_from_env(monkeypatch):
    monkeypatch.setenv("TT_TARGET_DENY_ALIASES", "demolive, prod2 ,")
    monkeypatch.setenv("LDAP_ALLOWED_GROUPS", "GrpA, GrpB")
    monkeypatch.setenv("LDAP_ADMIN_GROUPS", "")
    s = Settings(_env_file=None)
    assert s.tt_target_deny_aliases == ["DEMOLIVE", "PROD2"]
    assert s.ldap_allowed_groups == ["GrpA", "GrpB"]
    assert s.ldap_admin_groups == []


def test_secrets_are_masked():
    s = make()
    assert K1 not in repr(s)
    assert s.tt_anon_hmac_key.get_secret_value() == K1


def test_get_settings_cached():
    get_settings.cache_clear()
    assert get_settings() is get_settings()
