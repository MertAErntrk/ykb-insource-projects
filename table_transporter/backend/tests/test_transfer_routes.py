import threading
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.auth.session import COOKIE_NAME, User, get_store
from app.log.db import Database
from app.log.repository import Repository
from app.main import create_app
from app.oracle import pools
from app.oracle.guard import assert_allowed
from tests.fakes.fake_oracle import FakeOracle
from tests.test_auth import PASSWORD, FakeLdap

TNS = """
DEMO_TEST = (DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db-test.example.invalid)(PORT=1453))
  (CONNECT_DATA=(SERVICE_NAME=DEMOTEST)))
DEMO_DEV = (DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db-dev.example.invalid)(PORT=1453))
  (CONNECT_DATA=(SERVICE_NAME=DEMODEV)))
DEMOLIVE = (DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db-live.example.invalid)(PORT=1521))
  (CONNECT_DATA=(SERVICE_NAME=DEMOLIVE)))
"""
COLS = ["COLUMN_NAME", "DATA_TYPE", "DATA_LENGTH", "CHAR_LENGTH", "CHAR_USED", "DATA_PRECISION",
        "DATA_SCALE", "NULLABLE", "COLUMN_ID", "HIDDEN_COLUMN", "VIRTUAL_COLUMN", "IDENTITY_COLUMN",
        "DEFAULT_LENGTH"]
SRC_COLS = [
    ("PARTY_ID", "NUMBER", 22, 0, None, 12, 0, "N", 1, "NO", "NO", "NO", None),
    ("AD", "VARCHAR2", 80, 80, "B", None, None, "Y", 2, "NO", "NO", "NO", None),
    ("DOGUM", "DATE", 7, 0, None, None, None, "Y", 3, "NO", "NO", "NO", None),
]
SHORT_COLS = [SRC_COLS[0], ("AD", "VARCHAR2", 50, 50, "B", None, None, "Y", 2, "NO", "NO", "NO", None),
              SRC_COLS[2]]
TABLES = {
    ("DEMO", "MUSTERI"): SRC_COLS,
    ("APP_USER", "HEDEF"): SRC_COLS,
    ("APP_USER", "KISA"): SHORT_COLS,
    ("APP_USER", "TRG"): SRC_COLS,
    ("APP_USER", "V_HEDEF"): SRC_COLS,
    ("APP_USER", "DOLU"): SRC_COLS,
}
DATA = [(1, "Ali", datetime(1990, 1, 1)), (2, "Ayşe", None), (3, None, datetime(1985, 5, 5))]
RULES = [
    {"column": "PARTY_ID", "type": "num_token", "params": {}},
    {"column": "AD", "type": "mask", "params": {"show_last": 1}},
    {"column": "DOGUM", "type": "keep", "params": {}},
]
KEEP = [{"column": c[0], "type": "keep", "params": {}} for c in SRC_COLS]
SOURCE = {"schema": "DEMO", "table": "MUSTERI"}
FLT = [{"column": "PARTY_ID", "op": "gt", "value": 0}]
TERMINAL = {"done", "failed", "partial", "cancelled"}


@pytest.fixture
def fake():
    f = FakeOracle()
    state = {"count": 3, "gate": None, "schema_triggers": []}
    f.state = state

    def created(name):
        return any("CREATE TABLE" in sql.upper() and f'"{name}"' in sql for sql, _ in f.executed)

    def objects(sql, p):
        key = (p["tt_owner"], p["tt_name"])
        if key in TABLES or created(p["tt_name"]):
            return [(4242, datetime(2026, 10, 15, 10, 0, 0), "VIEW" if key[1].startswith("V_") else "TABLE")]
        return []

    def rows(sql, p):
        gate = state["gate"]
        if gate is not None:
            start = f.cancels
            while not gate.wait(0.02):
                if f.cancels > start:
                    return []
        return DATA

    f.on(r"FROM ALL_TAB_COLS", lambda sql, p: TABLES.get((p["tt_owner"], p["tt_table"]), []), columns=COLS)
    f.on(r"ALL_CONSTRAINTS", [], columns=["COLUMN_NAME"])
    f.on(r"FROM ALL_OBJECTS", objects, columns=["OBJECT_ID", "CREATED", "OBJECT_TYPE"])
    f.on(r"FROM ALL_TRIGGERS\s+WHERE TABLE_OWNER",
         lambda sql, p: [("APP_USER", "TRG_BI")] if p["tt_table"] == "TRG" else [],
         columns=["OWNER", "TRIGGER_NAME"])
    f.on(r"FROM ALL_TRIGGERS\s+WHERE OWNER", lambda sql, p: state["schema_triggers"],
         columns=["OWNER", "TRIGGER_NAME", "BASE_OBJECT_TYPE"])
    f.on(r"FETCH FIRST", rows, columns=["PARTY_ID", "AD", "DOGUM"])
    # Gerçek Oracle gibi tavanlı: FETCH FIRST :tt_cap en çok cap satır sayar
    # (okuma handler'ından sonra kayıtlı: son kayıt öncelikli, sayım SQL'i de FETCH FIRST içerir).
    f.on(r"SELECT COUNT\(\*\) FROM \(SELECT 1 FROM", lambda sql, p: [(min(state["count"], p["tt_cap"]),)],
         columns=["N"])
    pools.set_driver(f)
    yield f
    pools.set_driver(None)
    for sql, _ in f.executed:  # fake'in kaydettiği tüm SQL guard'dan geçer
        assert_allowed(sql)


@pytest.fixture
def client(settings, tmp_path, fake):
    (tmp_path / "tnsnames.ora").write_text(TNS, encoding="utf-8")
    app = create_app(settings.model_copy(update={"tt_tns_admin": str(tmp_path), "tt_row_limit": 1000}))
    app.state.ldap_client_factory = FakeLdap()
    db = Database(tmp_path / "log.db")
    db.init_schema()
    app.state.repository = Repository(db)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/auth/login", json={"username": "mert", "password": PASSWORD})
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        connect(c, "source", "DEMO_TEST")
        connect(c, "target", "DEMO_DEV")
        yield c
        if fake.state["gate"] is not None:
            fake.state["gate"].set()
        svc = getattr(app.state, "transfer_service", None)
        if svc is not None:
            svc.shutdown(wait=True)
    db.close()


def connect(c, role, alias):
    r = c.post("/api/db/connect", json={"role": role, "alias": alias, "user": "APP_USER", "password": "x"})
    assert r.status_code == 200, r.text


def other_client(c, username="ayse", admin=False):
    s = get_store(c.app).create(User(username, username, [], admin))
    o = TestClient(c.app, raise_server_exceptions=False)
    o.cookies.set(COOKIE_NAME, s.id)
    o.headers["X-CSRF-Token"] = s.csrf_token
    return o


def body(table="MUSTERI_ANON", mode="create", schema="APP_USER", rules=RULES, **kw):
    out = {"source": SOURCE, "target": {"schema": schema, "table": table, "mode": mode},
           "rules": rules, "filters": FLT, "confirm_target": f"{schema}.{table}", "confirm_unmasked_live": False}
    out.update(kw)
    return out


def err(r):
    return r.status_code, r.json()["error"]


def wait_job(c, job_id, statuses=TERMINAL, timeout=10.0):
    repo = c.app.state.repository
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = repo.get_job(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"iş beklenen duruma gelmedi: {repo.get_job(job_id)['status']}")


def start(c, **kw):
    r = c.post("/api/transfers", json=body(**kw))
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def test_count_and_over_limit(client, fake):
    flt = [{"column": "AD", "op": "eq", "value": "Ali"}]
    r = client.post("/api/transfers/count", json={"source": SOURCE, "filters": flt})
    assert r.json() == {"count": 3, "limit": 1000, "over_limit": False, "timed_out": False}
    sql, binds = next((s, b) for s, b in fake.executed if "COUNT(*)" in s)
    assert binds["f0"] == "Ali" and ":f0" in sql
    assert binds["tt_cap"] == 1001
    fake.state["count"] = 5000
    r = client.post("/api/transfers/count", json={"source": SOURCE, "filters": FLT})
    assert r.json() == {"count": None, "limit": 1000, "over_limit": True, "timed_out": False}
    bad = client.post("/api/transfers/count", json={"source": SOURCE, "filters": [{"column": "YOK", "op": "eq", "value": 1}]})
    assert err(bad) == (422, "VALIDATION")


def test_scope_required_on_all_endpoints(client):
    msg = "Taşınacak kümeyi belirlemek için en az bir filtre gerekli."
    for flt in ([], None):
        extra = {} if flt is None else {"filters": flt}
        for path, payload in (("count", {"source": SOURCE}), ("preview", {"source": SOURCE, "rules": RULES})):
            r = client.post(f"/api/transfers/{path}", json={**payload, **extra})
            assert err(r) == (422, "VALIDATION") and r.json()["detail"] == msg, (path, r.text)
        r = client.post("/api/transfers", json={k: v for k, v in body().items() if k != "filters"} | extra)
        assert err(r) == (422, "VALIDATION")
    tauto = [{"column": "PARTY_ID", "op": "gt", "value": -10**12}]
    r = client.post("/api/transfers/count", json={"source": SOURCE, "filters": tauto})
    assert err(r) == (422, "VALIDATION") and "'PARTY_ID' koşulu tüm satırları seçer" in r.json()["detail"]
    assert client.post("/api/transfers", json=body(filters=tauto)).status_code == 422


def test_preview_shape(client):
    r = client.post("/api/transfers/preview", json={"source": SOURCE, "filters": FLT, "rules": RULES})
    assert r.status_code == 200, r.text
    cols = {c["column"]: c for c in r.json()["columns"]}
    assert list(cols) == ["PARTY_ID", "AD", "DOGUM"]
    assert cols["PARTY_ID"]["type"] == "num_token" and cols["PARTY_ID"]["before"] == [1, 2, 3]
    assert len(cols["PARTY_ID"]["after"]) == 3
    assert cols["AD"]["after"][0] == "**i" and cols["AD"]["null_before"] == 1
    bad = client.post("/api/transfers/preview", json={
        "source": SOURCE, "filters": FLT, "rules": [*RULES[:2], {"column": "DOGUM", "type": "mask", "params": {}}]})
    assert err(bad) == (422, "RULE_INVALID")


def test_compat(client):
    def compat(table):
        return client.post("/api/transfers/compat", json={
            "source": SOURCE, "target": {"schema": "APP_USER", "table": table}, "rules": KEEP}).json()

    assert compat("HEDEF")["status"] == "ok"
    trg = compat("TRG")
    assert trg["status"] == "error" and trg["has_enabled_trigger"] is True
    view = compat("V_HEDEF")
    assert view["status"] == "error" and view["target_is_table"] is False
    short = compat("KISA")
    assert short["status"] == "warn" and short["items"][0]["message"] == "Hedef kolon daha kısa (50 < 80)."
    r = client.post("/api/transfers/compat", json={
        "source": SOURCE, "target": {"schema": "APP_USER", "table": "YOK"}, "rules": KEEP})
    assert err(r) == (404, "NOT_FOUND")


def test_full_create_flow_and_sse(client, fake):
    tracked = []
    client.app.state.after_create = tracked.append
    job_id = start(client)
    job = wait_job(client, job_id)
    assert job["status"] == "done", job
    assert job["rows_written"] == 3 and job["total_rows"] == 3 and job["owner"] == "mert"
    assert [c["rule_type"] for c in job["columns"]] == ["num_token", "mask", "keep"]
    assert fake.tables["MUSTERI_ANON"]["rows"][0][1] == "**i"
    detail = client.get(f"/api/transfers/{job_id}").json()
    assert detail["status"] == "done" and detail["columns"][0]["column_name"] == "PARTY_ID"
    with client.stream("GET", f"/api/transfers/{job_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    assert text.startswith("event: done\n") and text.count("event:") == 1
    deadline = time.monotonic() + 5
    while not tracked and time.monotonic() < deadline:
        time.sleep(0.02)
    assert tracked == [client.app.state.repository.tracked_for_job(job_id)["id"]]
    items = client.get("/api/transfers").json()["items"]
    assert [i["id"] for i in items] == [job_id] and "columns" not in items[0]
    sess = next(iter(get_store(client.app)._sessions.values()))
    assert not sess.resources.get("active_jobs")


def test_live_sse_ends_with_done(client, fake):
    gate = fake.state["gate"] = threading.Event()
    job_id = start(client)
    threading.Timer(0.5, gate.set).start()
    with client.stream("GET", f"/api/transfers/{job_id}/events") as r:
        text = "".join(r.iter_text())
    events = [line[7:] for line in text.splitlines() if line.startswith("event: ")]
    assert events[0] == "progress" and events[-1] == "done"
    assert wait_job(client, job_id)["status"] == "done"
    assert client.app.state.transfer_service.broker(job_id) is None


def test_ownership(client):
    job_id = start(client)
    wait_job(client, job_id)
    other = other_client(client)
    assert err(other.get(f"/api/transfers/{job_id}")) == (404, "NOT_FOUND")
    assert err(other.post(f"/api/transfers/{job_id}/cancel")) == (404, "NOT_FOUND")
    assert err(other.get(f"/api/transfers/{job_id}/events")) == (404, "NOT_FOUND")
    assert other.get("/api/transfers").json()["items"] == []
    assert err(other.get("/api/transfers?mine=false")) == (403, "AUTH_FORBIDDEN")
    admin = other_client(client, "yonetici", admin=True)
    assert [j["id"] for j in admin.get("/api/transfers?mine=false").json()["items"]] == [job_id]
    assert admin.get(f"/api/transfers/{job_id}").status_code == 200


def test_limit_exceeded(client, fake):
    fake.state["count"] = 1001
    r = client.post("/api/transfers", json=body())
    assert err(r) == (422, "LIMIT_EXCEEDED")
    assert r.json()["detail"] == "Seçiminiz 1.000 satırı aşıyor; lütfen filtreyi daraltın."
    r = client.post("/api/transfers", json=body(filters=[], full_table=True))
    assert err(r) == (422, "LIMIT_EXCEEDED")
    assert r.json()["detail"] == ("Tablo 1.000 satırın üzerinde; tamamı taşınamaz. "
                                  "Filtre girerek kümeyi daraltın.")
    assert client.app.state.repository.list_jobs(None) == []
    caps = [b["tt_cap"] for s, b in fake.executed if "COUNT(*)" in s]
    assert caps and set(caps) == {1001}


def test_full_table_count_preview_start(client, fake):
    full = {"source": SOURCE, "filters": [], "full_table": True}
    r = client.post("/api/transfers/count", json=full)
    assert r.json() == {"count": 3, "limit": 1000, "over_limit": False, "timed_out": False}
    sql = next(s for s, _ in fake.executed if "COUNT(*)" in s)
    assert " WHERE " not in sql and "FETCH FIRST :tt_cap ROWS ONLY" in sql
    assert client.post("/api/transfers/preview", json={**full, "rules": RULES}).status_code == 200
    fake.state["count"] = 1001
    r = client.post("/api/transfers/count", json=full)
    assert r.json() == {"count": None, "limit": 1000, "over_limit": True, "timed_out": False}
    fake.state["count"] = 3
    bad = client.post("/api/transfers/count", json={**full, "filters": FLT})
    assert err(bad) == (422, "VALIDATION") and bad.json()["detail"] == "Tablonun tamamı seçiliyken filtre verilemez."
    bad = client.post("/api/transfers", json=body(full_table=True))
    assert err(bad) == (422, "VALIDATION") and bad.json()["detail"] == "Tablonun tamamı seçiliyken filtre verilemez."
    nofilter = client.post("/api/transfers/count", json={"source": SOURCE, "filters": [], "full_table": False})
    assert err(nofilter) == (422, "VALIDATION")
    assert nofilter.json()["detail"] == "Taşınacak kümeyi belirlemek için en az bir filtre gerekli."

    job_id = start(client, filters=[], full_table=True)
    job = wait_job(client, job_id)
    assert job["status"] == "done" and job["filters"] == []
    detail = client.app.state.repository.db.query_one(
        "SELECT detail FROM audit_log WHERE action='transfer_start' AND job_id=?", (job_id,))["detail"]
    assert detail.endswith(" [tam tablo]")


def test_target_locked_and_cancel(client, fake):
    gate = fake.state["gate"] = threading.Event()
    job_id = start(client, table="HEDEF", mode="append")
    assert err(client.post("/api/transfers", json=body(table="HEDEF", mode="append"))) == (409, "TARGET_LOCKED")
    wait_job(client, job_id, {"running"})
    r = client.post(f"/api/transfers/{job_id}/cancel")
    assert r.status_code == 202 and r.json()["status"] in ("cancelling", "cancelled")
    job = wait_job(client, job_id)
    assert job["status"] == "cancelled" and job["rows_written"] == 0
    again = client.post(f"/api/transfers/{job_id}/cancel")
    assert again.status_code == 202 and again.json()["status"] == "cancelled"
    gate.set()
    fake.state["gate"] = None
    assert wait_job(client, start(client, table="HEDEF", mode="append"))["status"] == "done"


def test_validation_errors(client):
    def post(**kw):
        return err(client.post("/api/transfers", json=body(**kw)))

    assert post(confirm_target="APP_USER.BASKA") == (422, "VALIDATION")
    assert post(schema="DEMO") == (422, "VALIDATION")
    assert post(table="DOLU") == (409, "TABLE_EXISTS")
    assert post(table="TRG", mode="append") == (409, "TRIGGER_PRESENT")
    assert post(table="V_HEDEF", mode="append") == (422, "INCOMPATIBLE")
    assert post(table="YOK", mode="append") == (404, "NOT_FOUND")
    assert post(rules=RULES[:2]) == (422, "VALIDATION")  # kural eksik
    assert post(table="X" * 129) == (422, "VALIDATION")


def test_schema_trigger_blocks_create(client, fake):
    fake.state["schema_triggers"] = [("APP_USER", "DDL_TRG", "SCHEMA")]
    assert err(client.post("/api/transfers", json=body())) == (409, "TRIGGER_PRESENT")


def test_live_source_keep_requires_confirmation(client):
    connect(client, "source", "DEMOLIVE")
    assert err(client.post("/api/transfers", json=body(rules=KEEP))) == (422, "VALIDATION")
    job_id = start(client, rules=KEEP, confirm_unmasked_live=True)
    assert wait_job(client, job_id)["status"] == "done"
    no_keep = [*RULES[:2], {"column": "DOGUM", "type": "null", "params": {}}]
    assert wait_job(client, start(client, table="T2", rules=no_keep))["status"] == "done"  # keep yok → onay gerekmez


def test_disconnected_target(client):
    client.delete("/api/db/connect/target")
    assert err(client.post("/api/transfers", json=body())) == (409, "DB_DISCONNECTED")


def test_logout_with_running_job(client, fake):
    fake.state["gate"] = threading.Event()
    job_id = start(client)
    r = client.post("/api/auth/logout")
    assert err(r) == (409, "JOB_RUNNING")
    r = client.post("/api/auth/logout", json={"cancel_running": True})
    assert r.status_code == 204
    assert wait_job(client, job_id)["status"] == "cancelled"
