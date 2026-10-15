"""Uçtan uca (P12): sahte Oracle + gerçek uygulama (lifespan dahil) HTTP API üzerinden.

Kapsam: 1M satır create (anonimleştirilmiş), append, append hatasında geri alma, iptal, 5M+1 reddi,
temizlik (görülme → süresi dolma → DROP script), açılış kurtarma, şifre sızıntısı ve silmeme denetimi.
Satır sayısı TT_E2E_ROWS ile küçültülebilir (varsayılan 1.000.000).
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.log.db import Database
from app.log.repository import Repository
from app.main import create_app
from app.oracle import pools
from app.oracle.guard import assert_allowed
from tests.fakes.fake_oracle import FakeOracle, make_ora_error
from tests.test_auth import PASSWORD, FakeLdap
from tests.test_static_scan import FORBIDDEN_SQL_RE
from tests.test_transfer_routes import COLS, SRC_COLS, TNS

N_ROWS = int(os.environ.get("TT_E2E_ROWS", "1000000"))
ORA_PASSWORD = "Ora-Gizli-Sifre-7731!"
CREATED_AT = datetime(2026, 10, 1, 9, 30, 0)
TERMINAL = {"done", "failed", "partial", "cancelled"}
FLT = [{"column": "PARTY_ID", "op": "gt", "value": 0}]
SOURCE = {"schema": "DEMO", "table": "MUSTERI"}
ANON_RULES = [
    {"column": "PARTY_ID", "type": "num_token", "params": {}},
    {"column": "AD", "type": "mask", "params": {"show_last": 1}},
    {"column": "DOGUM", "type": "keep", "params": {}},
]
KEEP = [{"column": c[0], "type": "keep", "params": {}} for c in SRC_COLS]
TABLES = {("DEMO", "MUSTERI"): SRC_COLS, ("APP_USER", "HEDEF"): SRC_COLS}


def _rows(n: int) -> list[tuple]:
    base = datetime(1980, 1, 1)
    return [(100_000 + i, f"Ad{i % 9973}", base + timedelta(days=i % 15000)) for i in range(n)]


@pytest.fixture
def world(tmp_path, settings, caplog):
    caplog.set_level(logging.INFO)
    (tmp_path / "tnsnames.ora").write_text(TNS, encoding="utf-8")
    accounts = tmp_path / "service_accounts.toml"
    accounts.write_text('[default]\nuser = "SVC_CHECK"\npassword = "svc-test-only"\n', encoding="utf-8")

    fake = FakeOracle()
    state = {"count": N_ROWS, "data": [], "gate": None}

    def created(name):
        return any(sql.upper().startswith("CREATE TABLE") and f'"{name}"' in sql for sql, _ in fake.executed)

    def objects(sql, p):
        key = (p["tt_owner"], p["tt_name"])
        if key in TABLES or created(p["tt_name"]):
            return [(4242, CREATED_AT, "TABLE")]
        return []

    def rows(sql, p):
        gate = state["gate"]
        if gate is not None:
            start = fake.cancels
            while not gate.wait(0.02):
                if fake.cancels > start:
                    return []
        return state["data"][: p.get("tt_limit") or None]

    fake.on(r"FROM ALL_TAB_COLS", lambda sql, p: TABLES.get((p["tt_owner"], p["tt_table"]), []), columns=COLS)
    fake.on(r"ALL_CONSTRAINTS", [], columns=["COLUMN_NAME"])
    fake.on(r"FROM (ALL|DBA)_OBJECTS", objects, columns=["OBJECT_ID", "CREATED", "OBJECT_TYPE"])
    fake.on(r"FROM ALL_TRIGGERS", [], columns=["OWNER", "TRIGGER_NAME", "BASE_OBJECT_TYPE"])
    fake.on(r"FETCH FIRST", rows, columns=["PARTY_ID", "AD", "DOGUM"])
    # Gerçek Oracle gibi tavanlı: FETCH FIRST :tt_cap en çok cap satır sayar
    # (okuma handler'ından sonra kayıtlı: son kayıt öncelikli, sayım SQL'i de FETCH FIRST içerir).
    fake.on(r"SELECT COUNT\(\*\) FROM \(SELECT 1 FROM", lambda sql, p: [(min(state["count"], p["tt_cap"]),)],
            columns=["N"])
    pools.set_driver(fake)

    # Saat 8 gün geride: iş kaydı ve son kullanma geçmişte kalır, kontrol gerçek saatle "süresi doldu" der.
    clock = {"now": datetime.now(timezone.utc) - timedelta(days=8)}
    db_path = tmp_path / "e2e.db"
    db = Database(db_path)
    db.init_schema()
    repo = Repository(db, now=lambda: clock["now"])
    cfg = settings.model_copy(update={
        "tt_tns_admin": str(tmp_path), "tt_service_accounts_file": str(accounts), "tt_sqlite_path": str(db_path),
    })
    app = create_app(cfg)
    app.state.ldap_client_factory = FakeLdap()
    app.state.repository = repo
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/auth/login", json={"username": "mert", "password": PASSWORD})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        for role, alias in (("source", "DEMO_TEST"), ("target", "DEMO_DEV")):
            r = c.post("/api/db/connect", json={"role": role, "alias": alias, "user": "APP_USER", "password": ORA_PASSWORD})
            assert r.status_code == 200, r.text
        yield SimpleNamespace(c=c, fake=fake, repo=repo, state=state, db_path=db_path,
                              caplog=caplog)
        if state["gate"] is not None:
            state["gate"].set()
    pools.set_driver(None)
    db.close()

    # Silmeme: sahte DB'ye giden her SQL izin listesinde ve yasak kalıp içermiyor.
    for sql, _ in fake.executed:
        assert_allowed(sql)
        assert not FORBIDDEN_SQL_RE.search(sql), sql
    # Oracle ve LDAP şifresi hiçbir yerde kalıcı değil.
    blob = db_path.read_bytes() + b"".join(
        p.read_bytes() for p in tmp_path.glob("e2e.db-*") if p.is_file())
    for secret in (ORA_PASSWORD, PASSWORD):
        assert secret.encode() not in blob
        assert secret not in caplog.text


def body(table, mode="create", rules=ANON_RULES, filters=tuple(FLT), schema="APP_USER"):
    return {"source": SOURCE, "target": {"schema": schema, "table": table, "mode": mode}, "rules": rules,
            "filters": list(filters), "confirm_target": f"{schema}.{table}", "confirm_unmasked_live": False}


def wait_job(c, job_id, statuses=TERMINAL, timeout=120.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = c.get(f"/api/transfers/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"iş beklenen duruma gelmedi: {c.get(f'/api/transfers/{job_id}').json()}")


def test_create_anonymized_then_cleanup_drop_script(world):
    c, fake = world.c, world.fake
    world.state["data"] = _rows(N_ROWS)
    count = c.post("/api/transfers/count", json={"source": SOURCE, "filters": FLT}).json()
    assert count == {"count": N_ROWS, "limit": 5_000_000, "over_limit": False, "timed_out": False}

    preview = c.post("/api/transfers/preview", json={"source": SOURCE, "filters": FLT, "rules": ANON_RULES})
    assert preview.status_code == 200, preview.text

    t0 = time.monotonic()
    r = c.post("/api/transfers", json=body("MUSTERI_ANON"))
    assert r.status_code == 202, r.text
    job = wait_job(c, r.json()["job_id"])
    elapsed = time.monotonic() - t0
    assert job["status"] == "done", job
    assert job["rows_written"] == N_ROWS and job["total_rows"] == N_ROWS
    logging.getLogger("e2e").info("create %d satır: %.1f sn", N_ROWS, elapsed)

    written = fake.tables["MUSTERI_ANON"]["rows"]
    assert len(written) == N_ROWS
    src_ids = [row[0] for row in world.state["data"]]
    out_ids = [row[0] for row in written]
    assert len(set(out_ids)) == N_ROWS  # num_token birebir
    assert sum(a == b for a, b in zip(src_ids, out_ids)) < N_ROWS // 100  # kimlikler değişti
    assert all(len(str(a)) == len(str(b)) for a, b in zip(src_ids, out_ids))  # basamak sayısı korunur
    assert written[0][1].endswith(world.state["data"][0][1][-1]) and written[0][1].startswith("*")
    create_sql = [s for s, _ in fake.executed if s.upper().startswith("CREATE TABLE")]
    assert len(create_sql) == 1 and '"APP_USER"."MUSTERI_ANON"' in create_sql[0]

    # Temizlik: iş bitince servis hesabıyla görüldü; saat 8 gün geride olduğundan süresi doldu.
    items = c.get("/api/cleanup").json()["items"]
    assert [i["table"] for i in items] == ["MUSTERI_ANON"]
    assert items[0]["verified_visible_at"] is not None
    checked = c.post(f"/api/cleanup/check/{job['id']}")
    assert checked.status_code == 200, checked.text
    assert checked.json()["table_status"] == "expired_present"

    script = c.get("/api/cleanup/drop-script", params={"job_ids": job["id"]})
    assert script.status_code == 200
    assert 'attachment; filename="drop_script.sql"' in script.headers["content-disposition"]
    drops = [line for line in script.text.splitlines() if line.startswith("DROP")]
    assert drops == ['DROP TABLE "APP_USER"."MUSTERI_ANON";']
    assert "PURGE" not in script.text.upper()

    # Uygulama yine de hiçbir şey silmedi: tablo sahte DB'de duruyor.
    assert len(fake.tables["MUSTERI_ANON"]["rows"]) == N_ROWS


def test_append_commits_once_and_rolls_back_on_error(world):
    c, fake = world.c, world.fake
    n = 120_000  # insert_batch 50k → 3 executemany
    world.state["count"] = n
    world.state["data"] = _rows(n)

    commits = fake.commits
    r = c.post("/api/transfers", json=body("HEDEF", mode="append", rules=KEEP))
    assert r.status_code == 202, r.text
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done" and job["rows_written"] == n
    inserts = [p for s, p in fake.executed if s.upper().startswith("INSERT")]
    assert sum(len(p) for p in inserts) == n
    assert fake.commits == commits + 1  # tek transaction

    # İkinci executemany'de ORA hatası: hiç commit yok, rollback var → hedef değişmez.
    orig = fake._record
    seen = {"n": 0}

    def failing(sql, params):
        orig(sql, params)
        if sql.upper().startswith("INSERT"):
            seen["n"] += 1
            if seen["n"] == 2:
                raise make_ora_error(1653, "unable to extend table")

    fake._record = failing
    commits, rollbacks = fake.commits, fake.rollbacks
    r = c.post("/api/transfers", json=body("HEDEF", mode="append", rules=KEEP))
    job = wait_job(c, r.json()["job_id"])
    fake._record = orig
    assert job["status"] == "failed" and job["error_code"]
    assert fake.commits == commits and fake.rollbacks > rollbacks
    assert c.get("/api/cleanup").json()["items"] == []  # append tabloları izlenmez


def test_cancel_running_append(world):
    import threading

    c, fake = world.c, world.fake
    world.state["count"] = 10
    world.state["data"] = _rows(10)
    world.state["gate"] = threading.Event()
    commits = fake.commits
    r = c.post("/api/transfers", json=body("HEDEF", mode="append", rules=KEEP))
    job_id = r.json()["job_id"]
    wait_job(c, job_id, statuses={"running"})
    assert c.post(f"/api/transfers/{job_id}/cancel").status_code == 202
    job = wait_job(c, job_id)
    assert job["status"] == "cancelled" and job["rows_written"] == 0
    assert fake.commits == commits


def test_over_limit_never_starts(world):
    c, fake = world.c, world.fake
    world.state["count"] = 5_000_001
    count = c.post("/api/transfers/count", json={"source": SOURCE, "filters": FLT}).json()
    assert count["over_limit"] is True and count["limit"] == 5_000_000 and count["count"] is None
    r = c.post("/api/transfers", json=body("BUYUK_ANON"))
    assert r.json()["error"] == "LIMIT_EXCEEDED"
    full = c.post("/api/transfers/count", json={"source": SOURCE, "filters": [], "full_table": True}).json()
    assert full == {"count": None, "limit": 5_000_000, "over_limit": True, "timed_out": False}
    r = c.post("/api/transfers", json={**body("BUYUK_ANON", filters=()), "full_table": True})
    assert r.json()["error"] == "LIMIT_EXCEEDED" and r.json()["detail"].startswith("Tablo 5.000.000 satırın üzerinde")
    assert not any(s.upper().startswith("CREATE TABLE") for s, _ in fake.executed)
    assert c.get("/api/transfers").json()["items"] == []


def test_restart_marks_running_jobs(tmp_path, settings):
    db = Database(tmp_path / "r.db")
    db.init_schema()
    repo = Repository(db)
    job = {"id": "j-restart", "owner": "mert", "source_alias": "DEMO_TEST", "source_schema": "DEMO",
           "source_table": "MUSTERI", "target_alias": "DEMO_DEV", "target_schema": "APP_USER",
           "target_table": "X", "mode": "append", "filters": [], "total_rows": 5}
    repo.create_job(job, [])
    repo.set_status("j-restart", "running")
    app = create_app(settings)
    app.state.repository = repo
    with TestClient(app):
        assert repo.get_job("j-restart")["status"] == "failed"
    db.close()


def test_full_table_transfer(world):
    c, fake = world.c, world.fake
    world.state["data"] = _rows(500)
    world.state["count"] = 500
    count = c.post("/api/transfers/count", json={"source": SOURCE, "filters": [], "full_table": True}).json()
    assert count == {"count": 500, "limit": 5_000_000, "over_limit": False, "timed_out": False}
    r = c.post("/api/transfers", json={**body("TAM_ANON", filters=()), "full_table": True})
    assert r.status_code == 202, r.text
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done" and job["rows_written"] == 500 and job["filters"] == []
    assert len(fake.tables["TAM_ANON"]["rows"]) == 500
    reads = [s for s, _ in fake.executed if s.upper().startswith("SELECT") and '"DEMO"."MUSTERI"' in s]
    assert reads and not any(" WHERE " in s for s in reads)
