import logging
import socket
import ssl
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from ldap3 import SUBTREE
from ldap3.core.exceptions import LDAPSocketOpenError

from app.auth import ldap_auth
from app.auth.session import get_store
from app.main import create_app

PASSWORD = "Dogru.Sifre-9431"
WRONG = "Yanlis.Sifre-7777"
BASE_DN = "OU=BTUsers,OU=GMUsers,OU=KFS,OU=Users,OU=KFS,DC=kfs,DC=local"
GROUP_APP = "CN=TT_Users,OU=Groups,DC=kfs,DC=local"
GROUP_ADMIN = "CN=TT_Admins,OU=Groups,DC=kfs,DC=local"


def _entry(sam="mert", groups=(GROUP_APP,)):
    return {
        "dn": f"CN={sam},{BASE_DN}",
        "attributes": {"sAMAccountName": sam, "displayName": "Mert Ali", "memberOf": list(groups)},
    }


class FakeConn:
    """ldap3.Connection benzeri: NT-stil bind'i ve aramayı taklit eder, çağrıları sayar."""

    def __init__(self, ldap):
        self.ldap = ldap
        self.user = None
        self.password = None
        self.response = []

    def bind(self):
        self.ldap.bind_calls += 1
        self.ldap.bound_users.append(self.user)
        if self.ldap.bind_exc is not None:
            raise self.ldap.bind_exc
        return self.user == "KFS\\mert" and self.password == PASSWORD

    def search(self, base, flt, search_scope=None, attributes=None):
        self.ldap.searches.append((base, flt, search_scope, attributes))
        self.response = [{"type": "searchResEntry", **e} for e in self.ldap.entries]
        self.response.append({"type": "searchResRef", "uri": ["ldaps://other/"]})  # AD referansı sayılmaz
        return bool(self.ldap.entries)

    def unbind(self):
        self.ldap.unbind_calls += 1


class FakeLdap:
    def __init__(self):
        self.bind_calls = 0
        self.unbind_calls = 0
        self.bound_users = []
        self.searches = []
        self.entries = [_entry()]
        self.bind_exc = None

    def __call__(self, settings):
        return FakeConn(self)


@pytest.fixture
def ldap():
    return FakeLdap()


def _make(settings, ldap, **update):
    app = create_app(settings.model_copy(update=update) if update else settings)
    app.state.ldap_client_factory = ldap
    return app


@pytest.fixture
def app(settings, ldap):
    return _make(settings, ldap)


def _login(client, username="mert", password=PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _err(r):
    return r.status_code, r.json()["error"]


def test_login_success_sets_cookie(client, ldap):
    r = _login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["user"] == {"username": "mert", "display_name": "Mert Ali", "groups": [GROUP_APP], "is_admin": False}
    assert len(body["csrf_token"]) >= 40
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("tt_session=")
    low = cookie.lower()
    assert "httponly" in low and "samesite=strict" in low and "path=/" in low
    assert "max-age" not in low and "expires" not in low and "secure" not in low  # TT_COOKIE_SECURE=false (test)
    assert ldap.bound_users == ["KFS\\mert"] and ldap.unbind_calls == 1
    base, flt, scope, attrs = ldap.searches[0]
    assert base == BASE_DN and flt == "(sAMAccountName=mert)" and scope == SUBTREE
    assert set(attrs) == {"displayName", "memberOf", "sAMAccountName"}

    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json() == body


def test_cookie_secure_flag(settings, ldap):
    with TestClient(_make(settings, ldap, tt_cookie_secure=True)) as c:
        assert "secure" in _login(c).headers["set-cookie"].lower()


def test_wrong_password(client, ldap):
    assert _err(_login(client, password=WRONG)) == (401, "AUTH_INVALID")
    assert ldap.unbind_calls == 1 and not ldap.searches


@pytest.mark.parametrize("pw", ["", "   ", "\t\n"])
def test_blank_password_never_binds(client, ldap, pw):
    assert _err(_login(client, password=pw)) == (401, "AUTH_INVALID")
    assert ldap.bind_calls == 0


@pytest.mark.parametrize("name", ["*", "a)(|(x=*", "a\\b", "a" * 65, "", "mert ", "çağrı"])
def test_bad_username_rejected_before_bind(client, ldap, name):
    assert _err(_login(client, username=name)) == (401, "AUTH_INVALID")
    assert ldap.bind_calls == 0


def test_filter_is_escaped(settings, ldap):
    # Regex izinli karakterler zaten güvenli; escape yine de uygulanır.
    ldap.bind_exc = None
    ldap_auth.authenticate("mert", PASSWORD, settings, ldap)
    assert ldap.searches[-1][1] == "(sAMAccountName=mert)"


@pytest.mark.parametrize("entries", [[], [_entry(), _entry("mert2")]])
def test_search_not_exactly_one_is_forbidden(client, ldap, entries):
    ldap.entries = entries
    assert _err(_login(client)) == (403, "AUTH_FORBIDDEN")
    assert ldap.unbind_calls == 1


def test_allowed_groups(settings, ldap):
    with TestClient(_make(settings, ldap, ldap_allowed_groups=["tt_users"])) as c:  # CN, harf duyarsız
        assert _login(c).status_code == 200
    with TestClient(_make(settings, ldap, ldap_allowed_groups=[GROUP_APP.upper()])) as c:  # tam DN
        assert _login(c).status_code == 200
    with TestClient(_make(settings, ldap, ldap_allowed_groups=["Baska_Grup"])) as c:
        assert _err(_login(c)) == (403, "AUTH_FORBIDDEN")


def test_admin_group(settings, ldap):
    ldap.entries = [_entry(groups=(GROUP_APP, GROUP_ADMIN))]
    with TestClient(_make(settings, ldap, ldap_admin_groups=["TT_Admins"])) as c:
        assert _login(c).json()["user"]["is_admin"] is True
    with TestClient(_make(settings, ldap, ldap_admin_groups=["Yok"])) as c:
        assert _login(c).json()["user"]["is_admin"] is False


@pytest.mark.parametrize("exc", [LDAPSocketOpenError("ulasilamadi"), socket.timeout("zaman asimi")])
def test_ldap_unavailable(client, ldap, exc):
    ldap.bind_exc = exc
    r = _login(client)
    assert _err(r) == (503, "LDAP_UNAVAILABLE")
    assert ldap.unbind_calls == 1


def test_throttle_after_five_failures(client, ldap):
    for _ in range(5):
        assert _err(_login(client, password=WRONG)) == (401, "AUTH_INVALID")
    calls = ldap.bind_calls
    assert _err(_login(client)) == (429, "AUTH_THROTTLED")  # doğru şifre bile
    assert ldap.bind_calls == calls


def test_throttle_window_and_reset(app, client, ldap):
    now = [1000.0]
    from app.auth.routes import _throttle  # noqa: PLC0415

    class _Req:
        pass

    req = _Req()
    req.app = app
    _throttle(req).clock = lambda: now[0]
    for _ in range(4):
        _login(client, password=WRONG)
    assert _login(client).status_code == 200  # başarı sayacı sıfırlar
    for _ in range(5):
        _login(client, password=WRONG)
    assert _err(_login(client)) == (429, "AUTH_THROTTLED")
    now[0] += 301
    assert _login(client).status_code == 200


def test_me_without_cookie(client):
    assert _err(client.get("/api/auth/me")) == (401, "SESSION_EXPIRED")
    client.cookies.set("tt_session", "uydurma")
    assert _err(client.get("/api/auth/me")) == (401, "SESSION_EXPIRED")


def test_post_requires_csrf(client):
    token = _login(client).json()["csrf_token"]
    assert _err(client.post("/api/auth/logout")) == (403, "CSRF_INVALID")
    assert _err(client.post("/api/auth/logout", headers={"X-CSRF-Token": token + "x"})) == (403, "CSRF_INVALID")
    r = client.post("/api/auth/logout", headers={"X-CSRF-Token": token})
    assert r.status_code == 204
    assert _err(client.get("/api/auth/me")) == (401, "SESSION_EXPIRED")


def test_logout_without_session(client):
    assert _err(client.post("/api/auth/logout", headers={"X-CSRF-Token": "x"})) == (401, "SESSION_EXPIRED")


class _Res:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def _clocked_store(app):
    store = get_store(app)
    now = [datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)]
    store.clock = lambda: now[0]
    return store, now


def test_ttl_expiry_closes_resources(app, client, settings):
    store, now = _clocked_store(app)
    _login(client)
    (s,) = store._sessions.values()
    res = _Res()
    s.resources["source"] = res
    now[0] += timedelta(minutes=settings.tt_session_ttl_min - 1)
    assert client.get("/api/auth/me").status_code == 200  # dokunma süreyi uzatır
    now[0] += timedelta(minutes=settings.tt_session_ttl_min + 1)
    assert _err(client.get("/api/auth/me")) == (401, "SESSION_EXPIRED")
    assert res.closed == 1 and not store._sessions


def test_active_jobs_prevent_expiry(app, client, settings):
    store, now = _clocked_store(app)
    _login(client)
    (s,) = store._sessions.values()
    s.resources["active_jobs"] = {"j1"}
    now[0] += timedelta(minutes=settings.tt_session_ttl_min * 10)
    assert client.get("/api/auth/me").status_code == 200
    s.resources["active_jobs"] = set()
    now[0] += timedelta(minutes=settings.tt_session_ttl_min + 1)
    assert _err(client.get("/api/auth/me")) == (401, "SESSION_EXPIRED")


def test_logout_job_running_and_cancel(app, client):
    token = _login(client).json()["csrf_token"]
    h = {"X-CSRF-Token": token}
    store = get_store(app)
    (s,) = store._sessions.values()
    res = _Res()
    s.resources.update({"active_jobs": {"j1"}, "target": res})
    cancelled = []
    app.state.cancel_job = cancelled.append

    r = client.post("/api/auth/logout", headers=h)
    assert _err(r) == (409, "JOB_RUNNING")
    assert _err(client.post("/api/auth/logout", headers=h, json={"cancel_running": False})) == (409, "JOB_RUNNING")
    assert client.get("/api/auth/me").status_code == 200

    r = client.post("/api/auth/logout", headers=h, json={"cancel_running": True})
    assert r.status_code == 204
    assert cancelled == ["j1"] and res.closed == 1
    assert 'tt_session=""' in r.headers["set-cookie"] or "max-age=0" in r.headers["set-cookie"].lower()
    assert _err(client.get("/api/auth/me")) == (401, "SESSION_EXPIRED")


def test_require_admin(app, client, settings, ldap):
    from app.auth.session import require_admin  # noqa: PLC0415
    from fastapi import Depends  # noqa: PLC0415

    @app.get("/api/_t/admin")
    async def admin(user=Depends(require_admin)):
        return {"ok": True}

    _login(client)
    assert _err(client.get("/api/_t/admin")) == (403, "AUTH_FORBIDDEN")


def test_password_never_leaks(client, ldap, caplog):
    caplog.set_level(logging.DEBUG)
    texts = [_login(client, password=WRONG).text]
    ldap.bind_exc = LDAPSocketOpenError("ulasilamadi")
    texts.append(_login(client).text)
    ldap.bind_exc = None
    ldap.entries = []
    texts.append(_login(client).text)
    texts.append(client.post("/api/auth/login", json={"username": "mert", "password": PASSWORD, "x": 1}).text)
    for t in texts:
        assert PASSWORD not in t and WRONG not in t
    assert PASSWORD not in caplog.text and WRONG not in caplog.text
    assert "mert" in caplog.text


def test_default_client_factory(settings, tmp_path):
    conn = ldap_auth.default_client_factory(settings)
    assert conn.server.ssl is True and conn.server.tls.validate == ssl.CERT_NONE
    assert conn.server.connect_timeout == settings.ldap_timeout_sec
    assert conn.receive_timeout == settings.ldap_timeout_sec
    assert conn.raise_exceptions is False and conn.authentication == "SIMPLE"
    assert ssl.OP_NO_TLSv1_1 in conn.server.tls.ssl_options
    ca = tmp_path / "ca.pem"
    ca.write_text("x", encoding="utf-8")
    conn = ldap_auth.default_client_factory(settings.model_copy(update={"ldap_ca_file": str(ca)}))
    assert conn.server.tls.validate == ssl.CERT_REQUIRED and conn.server.tls.ca_certs_file == str(ca)
