import logging

import pytest
from fastapi.testclient import TestClient

from app.oracle import pools
from app.oracle.guard import assert_allowed
from tests.fakes.fake_oracle import FakeOracle, make_ora_error
from tests.test_auth import FakeLdap, PASSWORD
from app.main import create_app

SECRET = "S3cr3t-Parola-xyz"
TNS = """
DEMO_TEST = (DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db-test.example.invalid)(PORT=1453))
  (CONNECT_DATA=(SERVICE_NAME=DEMOTEST)))
DEMOLIVE = (DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db-live.example.invalid)(PORT=1521))
  (CONNECT_DATA=(SERVICE_NAME=DEMOLIVE)))
"""
COLS = ["COLUMN_NAME", "DATA_TYPE", "DATA_LENGTH", "CHAR_LENGTH", "CHAR_USED", "DATA_PRECISION",
        "DATA_SCALE", "NULLABLE", "COLUMN_ID", "HIDDEN_COLUMN", "VIRTUAL_COLUMN", "IDENTITY_COLUMN",
        "DEFAULT_LENGTH"]
COL_ROWS = [
    ("PARTY_ID", "NUMBER", 22, 0, None, 12, 0, "N", 1, "NO", "NO", "NO", None),
    ("EMAIL", "VARCHAR2", 100, 100, "C", None, None, "Y", 2, "NO", "NO", "NO", 5),
    ("NOTLAR", "CLOB", 4000, 0, None, None, None, "Y", 3, "NO", "NO", "NO", None),
    ("SYS_NC00004$", "RAW", 8, 0, None, None, None, "Y", None, "YES", "YES", "NO", None),
]


@pytest.fixture
def fake():
    f = FakeOracle()
    f.on(r"FROM ALL_TABLES",
         lambda sql, p: [("DEMO", "BUYUK"), ("DEMO", "KUCUK")][p["tt_offset"]:][:p["tt_limit"]],
         columns=["OWNER", "TABLE_NAME"])
    f.on(r"SELECT DISTINCT OWNER FROM ALL_TABLES", [("DEMO",), ("HR",)], columns=["OWNER"])  # sonra kayıt öncelikli
    f.on(r"FROM ALL_TAB_COLS", COL_ROWS, columns=COLS)
    f.on(r"ALL_CONSTRAINTS", [("PARTY_ID",)], columns=["COLUMN_NAME"])
    pools.set_driver(f)
    yield f
    pools.set_driver(None)


@pytest.fixture
def client(settings, tmp_path, fake):
    (tmp_path / "tnsnames.ora").write_text(TNS, encoding="utf-8")
    app = create_app(settings.model_copy(update={"tt_tns_admin": str(tmp_path)}))
    app.state.ldap_client_factory = FakeLdap()
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/auth/login", json={"username": "mert", "password": PASSWORD})
        assert r.status_code == 200
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        yield c


def _connect(c, role="source", alias="DEMO_TEST", password=SECRET):
    return c.post("/api/db/connect", json={"role": role, "alias": alias, "user": "APP_USER", "password": password})


def test_aliases(client):
    r = client.get("/api/tns/aliases")
    by = {a["alias"]: a for a in r.json()["aliases"]}
    assert by["DEMO_TEST"]["target_allowed"] is True and by["DEMOLIVE"]["target_allowed"] is False
    assert [a["alias"] for a in client.get("/api/tns/aliases?q=live").json()["aliases"]] == ["DEMOLIVE"]


def test_connect_status_disconnect(client):
    assert client.get("/api/db/status").json() == {"source": None, "target": None}
    r = _connect(client)
    assert r.status_code == 200
    assert r.json() == {"role": "source", "alias": "DEMO_TEST", "db_version": "19.0.0.0", "user": "APP_USER",
                        "mode": "thin", "direct_path": True, "max_ident_len": 128}
    st = client.get("/api/db/status").json()
    assert st["source"]["alias"] == "DEMO_TEST" and st["target"] is None
    assert client.delete("/api/db/connect/source").status_code == 204
    assert client.get("/api/db/status").json() == {"source": None, "target": None}
    r = client.get("/api/db/source/tables", params={"schema": "DEMO"})
    assert (r.status_code, r.json()["error"]) == (409, "DB_DISCONNECTED")


def test_target_denied_and_unknown_alias(client):
    r = _connect(client, role="target", alias="DEMOLIVE")
    assert (r.status_code, r.json()["error"]) == (403, "TARGET_DENIED")
    assert _connect(client, role="source", alias="DEMOLIVE").status_code == 200
    assert _connect(client, alias="YOK").json()["error"] == "NOT_FOUND"
    assert _connect(client, role="x").json()["error"] == "VALIDATION"


def test_reconnect_closes_previous(client):
    _connect(client)
    first = client.app.state.session_store._sessions
    handle = next(iter(first.values())).resources["source"]
    _connect(client)
    assert handle.closed


def test_password_never_leaks(client, caplog, fake):
    caplog.set_level(logging.DEBUG)
    r = _connect(client)
    assert SECRET not in r.text
    bad = client.post("/api/db/connect", json={"role": "source", "alias": "DEMO_TEST", "user": "U",
                                               "password": 12345})
    assert bad.status_code == 422 and "12345" not in bad.text
    miss = client.post("/api/db/connect", json={"role": "source", "alias": "DEMO_TEST", "user": "U",
                                                "password": SECRET + "x", "extra": [SECRET]})
    assert SECRET not in miss.text
    fake.fail_connect = make_ora_error(1017, "invalid username/password")
    fail = _connect(client)
    assert fail.json()["error"] == "DB_CONNECT_FAILED" and SECRET not in fail.text
    assert SECRET not in caplog.text


def test_csrf_required(client):
    del client.headers["X-CSRF-Token"]
    r = _connect(client)
    assert (r.status_code, r.json()["error"]) == (403, "CSRF_INVALID")
    assert client.delete("/api/db/connect/source").status_code == 403


def test_requires_login(settings, tmp_path, fake):
    (tmp_path / "tnsnames.ora").write_text(TNS, encoding="utf-8")
    app = create_app(settings.model_copy(update={"tt_tns_admin": str(tmp_path)}))
    with TestClient(app) as c:
        assert c.get("/api/tns/aliases").status_code == 401
        assert c.get("/api/db/status").status_code == 401


def test_schemas(client):
    _connect(client)
    assert client.get("/api/db/source/schemas?q=ar").json() == {"schemas": ["DEMO", "HR"]}
    assert client.get("/api/db/bad/schemas").json()["error"] == "VALIDATION"


def test_schemas_come_from_accessible_tables(client, fake):
    _connect(client)
    client.get("/api/db/source/schemas", params={"q": "ar"})
    sql, params = next((q, p) for q, p in reversed(fake.executed) if "DISTINCT OWNER" in q)
    assert "FROM ALL_TABLES" in sql and "ORACLE_MAINTAINED = :tt_y" in sql and "NUM_ROWS" not in sql
    assert (params["tt_n"], params["tt_y"], params["tt_q"]) == ("N", "Y", "ar")


def test_tables_paging(client, fake):
    _connect(client)
    r = client.get("/api/db/source/tables", params={"schema": "DEMO"})
    body = r.json()
    assert body["limit"] == 50 and body["offset"] == 0 and body["total"] == 2
    assert body["items"] == [{"schema": "DEMO", "name": "BUYUK"}, {"schema": "DEMO", "name": "KUCUK"}]
    # Tablo listesi yalnız sözlükten gelir; hiçbir tabloya SELECT/COUNT atılmaz.
    assert not any("BUYUK" in q or "KUCUK" in q or "COUNT(" in q.upper() for q, _ in fake.executed)
    page = client.get("/api/db/source/tables", params={"schema": "DEMO", "limit": 1, "offset": 1}).json()
    assert [i["name"] for i in page["items"]] == ["KUCUK"] and page["limit"] == 1 and page["offset"] == 1
    assert client.get("/api/db/source/tables", params={"schema": "DEMO", "limit": 201}).status_code == 422
    assert client.get("/api/db/source/tables").status_code == 422  # schema zorunlu


def test_columns(client):
    _connect(client)
    r = client.get("/api/db/source/tables/DEMO/MUSTERI/columns")
    cols = r.json()["columns"]
    assert [c["name"] for c in cols] == ["PARTY_ID", "EMAIL", "NOTLAR"]  # SYS_ gizli kolon yok
    pid, email, notlar = cols
    assert pid["is_pk_or_unique"] is True and pid["supported"] is True and pid["nullable"] is False
    assert pid["position"] == 1 and pid["precision"] == 12 and pid["unsupported_reason"] is None
    assert email["char_used"] == "C" and email["default"] is not None and email["is_pk_or_unique"] is False
    assert notlar["supported"] is False and notlar["unsupported_reason"] == "CLOB taşınamaz"


def test_columns_not_found(client, fake):
    _connect(client)
    fake.on(r"FROM ALL_TAB_COLS", [], columns=COLS)
    r = client.get("/api/db/source/tables/DEMO/YOK/columns")
    assert (r.status_code, r.json()["error"]) == (404, "NOT_FOUND")


def test_invalid_identifiers(client):
    _connect(client)
    assert client.get("/api/db/source/tables", params={"schema": "ar'im"}).json()["error"] == "VALIDATION"
    assert client.get("/api/db/source/tables/DEMO/mus-teri/columns").json()["error"] == "VALIDATION"
    assert client.get("/api/db/source/tables/1X/T/columns").status_code == 422


def test_ora_permission_mapping(client, fake):
    _connect(client)
    fake.fail_next(r"FROM ALL_TABLES", make_ora_error(1031, "insufficient privileges"))
    r = client.get("/api/db/source/schemas")
    assert (r.status_code, r.json()["error"]) == (403, "DB_PERMISSION")


def test_all_recorded_sql_passes_guard(client, fake):
    _connect(client)
    client.get("/api/db/source/schemas")
    client.get("/api/db/source/tables", params={"schema": "DEMO"})
    client.get("/api/db/source/tables/DEMO/MUSTERI/columns")
    assert len(fake.executed) >= 4
    for sql, _ in fake.executed:
        assert_allowed(sql)
