from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.cleanup import checker
from app.cleanup.drop_script import build_drop_script
from app.log.db import Database
from app.log.repository import Repository
from app.main import create_app
from app.oracle import pools
from app.oracle.guard import assert_allowed
from tests.fakes.fake_oracle import FakeOracle, make_ora_error
from tests.test_auth import FakeLdap, PASSWORD

TNS = """
DEMO_TEST = (DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db-test.example.invalid)(PORT=1453))
  (CONNECT_DATA=(SERVICE_NAME=DEMOTEST)))
"""
T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
LATER = T0 + timedelta(days=8)
CREATED = datetime(2026, 10, 1, 9, 0, 0)
OBJ_COLS = ["OBJECT_ID", "CREATED", "OBJECT_TYPE"]


@pytest.fixture
def fake():
    f = FakeOracle()
    pools.set_driver(f)
    yield f
    pools.set_driver(None)


@pytest.fixture
def cfg(settings, tmp_path):
    (tmp_path / "tnsnames.ora").write_text(TNS, encoding="utf-8")
    acc = tmp_path / "service_accounts.toml"
    acc.write_text('[default]\nuser = "SVC_CHECK"\npassword = "svc-pass"\n', encoding="utf-8")
    return settings.model_copy(update={"tt_tns_admin": str(tmp_path), "tt_service_accounts_file": str(acc)})


@pytest.fixture
def repo(tmp_path):
    db = Database(tmp_path / "log.db")
    db.init_schema()
    yield Repository(db, now=lambda: T0)
    db.close()


def add(repo, job_id, owner="mert", table="T_ANON", object_id=111, created="2026-10-01T09:00:00", verified=True):
    repo.create_job({"id": job_id, "owner": owner, "target_alias": "DEMO_TEST", "target_schema": "TEST",
                     "target_table": table, "mode": "create"}, [])
    tid = repo.track_table(job_id, owner, "DEMO_TEST", "TEST", table, object_id, created, 7)
    if verified:
        repo.mark_verified_visible(tid)
    return tid


def present(fake, object_id=111, created=CREATED, view="DBA_OBJECTS"):
    fake.on(rf"FROM {view}", [(object_id, created, "TABLE")], columns=OBJ_COLS)


def check(cfg, repo, tid, now=LATER, **kw):
    return checker.check_table(cfg, repo, repo.get_tracked(tid), now=now, **kw)


def assert_selects_only(fake):
    assert fake.executed
    for sql, _ in fake.executed:
        assert_allowed(sql)
        assert sql.lstrip().upper().startswith("SELECT"), sql


# --- checker ---

def test_match_expired_is_expired_present(cfg, repo, fake):
    tid = add(repo, "j1", verified=False)
    present(fake)
    t = check(cfg, repo, tid)
    assert t["table_status"] == "expired_present" and t["verified_visible_at"]
    assert_selects_only(fake)


def test_match_not_yet_expired_is_active(cfg, repo, fake):
    tid = add(repo, "j1")
    present(fake)
    assert check(cfg, repo, tid, now=T0 + timedelta(days=1))["table_status"] == "active"


def test_different_object_id_verified_is_deleted(cfg, repo, fake):
    tid = add(repo, "j1", verified=True)
    present(fake, object_id=999)
    assert check(cfg, repo, tid)["table_status"] == "deleted_verified"
    assert repo.get_tracked(tid)["table_status"] == "deleted_verified"


def test_never_seen_and_missing_is_unverifiable(cfg, repo, fake):
    tid = add(repo, "j1", verified=False)
    fake.on(r"FROM DBA_OBJECTS", [], columns=OBJ_COLS)
    assert check(cfg, repo, tid)["table_status"] == "unverifiable"


def test_different_created_means_recreated(cfg, repo, fake):
    tid = add(repo, "j1", verified=True)
    present(fake, created=datetime(2026, 10, 5, 1, 2, 3))
    assert check(cfg, repo, tid)["table_status"] == "deleted_verified"


def test_falls_back_to_all_objects_without_dba_privilege(cfg, repo, fake):
    tid = add(repo, "j1", verified=False)
    present(fake, view="ALL_OBJECTS")
    fake.fail_next(r"FROM DBA_OBJECTS", make_ora_error(942, "table or view does not exist"))
    assert check(cfg, repo, tid)["table_status"] == "expired_present"
    assert any("ALL_OBJECTS" in s for s, _ in fake.executed)
    assert_selects_only(fake)


def test_connection_error_keeps_status(cfg, repo, fake):
    tid = add(repo, "j1")
    fake.fail_connect = make_ora_error(12541, "no listener")
    t = check(cfg, repo, tid)
    assert t["table_status"] == "active" and t["last_checked_at"]
    errs = repo.db.query("SELECT error FROM table_checks WHERE tracked_table_id=?", (tid,))
    assert errs[0]["error"]


def test_missing_service_account_strict(cfg, repo, fake, tmp_path):
    tid = add(repo, "j1")
    bad = cfg.model_copy(update={"tt_service_accounts_file": str(tmp_path / "yok.toml")})
    from app.errors import AppError, ErrorCode
    with pytest.raises(AppError) as ei:
        check(bad, repo, tid, strict=True)
    assert ei.value.code is ErrorCode.DB_CONNECT_FAILED
    assert check(bad, repo, tid)["table_status"] == "active"


def test_verify_visible_and_run_due(cfg, repo, fake):
    a = add(repo, "j1", verified=False)
    b = add(repo, "j2", table="T2", object_id=222, verified=True)
    present(fake)
    checker.verify_visible(cfg, repo, a)
    assert repo.get_tracked(a)["verified_visible_at"]
    fake.on(r"FROM DBA_OBJECTS", lambda s, p: [(222 if p["tt_name"] == "T2" else 111, CREATED, "TABLE")],
            columns=OBJ_COLS)
    assert checker.run_due_checks(cfg, repo, LATER) == 2
    assert repo.get_tracked(b)["table_status"] == "expired_present"


# --- drop_script ---

def tr(**kw):
    d = {"job_id": "j1", "owner": "mert", "alias": "DEMO_TEST", "schema": "TEST", "table": "T_ANON",
         "object_id": 111, "object_created": "2026-10-01T09:00:00", "expires_at": "2026-10-08T09:00:00Z",
         "table_status": "expired_present"}
    return d | kw


def test_script_format():
    s = build_drop_script([tr(), tr(table_status="active", table="X")], generated_by="mert", generated_at=T0)
    assert 'DROP TABLE "TEST"."T_ANON";' in s and '"X"' not in s
    assert "PURGE" not in s.upper()
    assert "DBA tarafından gözden geçirilip elle çalıştırılmalıdır. Uygulama tablo silmez." in s
    assert "mert" in s and "job_id: j1" in s and "object_id: 111" in s


def test_script_invalid_ident_and_empty():
    with pytest.raises(ValueError):
        build_drop_script([tr(table='X";DROP')], generated_by="m", generated_at=T0)
    with pytest.raises(ValueError):
        build_drop_script([tr(schema="ab")], generated_by="m", generated_at=T0)
    with pytest.raises(ValueError):
        build_drop_script([], generated_by="m", generated_at=T0)
    with pytest.raises(ValueError):
        build_drop_script([tr(object_id=None)], generated_by="m", generated_at=T0)


def test_script_comment_injection_neutralised():
    s = build_drop_script([tr(job_id="a\nDROP TABLE \"S\".\"EVIL\";")], generated_by="m\nx", generated_at=T0)
    assert s.count("DROP TABLE") == 1 + 1  # gerçek komut + tek satırlık yorum içinde düz metin
    assert all(line.startswith("--") or line.startswith("DROP TABLE") or not line for line in s.splitlines())


# --- uçlar ---

@pytest.fixture
def client(cfg, repo, fake):
    app = create_app(cfg)
    app.state.ldap_client_factory = FakeLdap()
    app.state.repository = repo
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/auth/login", json={"username": "mert", "password": PASSWORD})
        assert r.status_code == 200
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        yield c


def test_list_own_only_and_status_filter(client, repo):
    add(repo, "j1")
    add(repo, "j2", owner="baska", table="T2")
    items = client.get("/api/cleanup").json()["items"]
    assert [i["job_id"] for i in items] == ["j1"]
    assert client.get("/api/cleanup?status=active").json()["items"][0]["table"] == "T_ANON"
    assert client.get("/api/cleanup?status=expired_present").json()["items"] == []
    r = client.get("/api/cleanup?status=zzz")
    assert (r.status_code, r.json()["error"]) == (422, "VALIDATION")


def test_drop_script_endpoint(client, repo, fake):
    tid = add(repo, "j1")
    add(repo, "j3", table="T3", object_id=333)  # active: scriptte yok
    repo.create_job({"id": "j4", "owner": "mert", "mode": "append"}, [])  # izlenmeyen append işi
    present(fake)
    check(client.app.state.settings, repo, tid)
    r = client.get("/api/cleanup/drop-script", params={"job_ids": "j1,j3,j4"})
    assert r.status_code == 200
    assert r.headers["content-type"].lower().startswith("text/plain") and "utf-8" in r.headers["content-type"].lower()
    assert r.headers["content-disposition"] == 'attachment; filename="drop_script.sql"'
    assert 'DROP TABLE "TEST"."T_ANON";' in r.text and "T3" not in r.text
    assert repo.db.query_one("SELECT COUNT(*) AS n FROM audit_log WHERE action='drop_script'")["n"] == 1


def test_drop_script_not_found_cases(client, repo, fake):
    add(repo, "j2", owner="baska", table="T2")
    repo.db.execute("UPDATE tracked_tables SET table_status='expired_present' WHERE job_id='j2'")
    r = client.get("/api/cleanup/drop-script", params={"job_ids": "j2"})
    assert (r.status_code, r.json()["error"]) == (404, "NOT_FOUND")
    add(repo, "j1")
    assert client.get("/api/cleanup/drop-script", params={"job_ids": "j1"}).json()["error"] == "NOT_FOUND"
    assert client.get("/api/cleanup/drop-script", params={"job_ids": "yok"}).json()["error"] == "NOT_FOUND"


def test_check_endpoint(client, repo, fake):
    add(repo, "j1", verified=False)
    add(repo, "j2", owner="baska", table="T2")
    present(fake)
    r = client.post("/api/cleanup/check/j1")
    assert r.status_code == 200 and r.json()["job_id"] == "j1" and r.json()["verified_visible_at"]
    assert repo.db.query_one("SELECT COUNT(*) AS n FROM audit_log WHERE action='check'")["n"] == 1
    assert client.post("/api/cleanup/check/j2").json()["error"] == "NOT_FOUND"


def test_check_endpoint_without_service_account(client, repo, fake, tmp_path):
    add(repo, "j1")
    s = client.app.state.settings
    client.app.dependency_overrides.clear()
    from app.config import get_settings
    client.app.dependency_overrides[get_settings] = lambda: s.model_copy(
        update={"tt_service_accounts_file": str(tmp_path / "yok.toml")})
    r = client.post("/api/cleanup/check/j1")
    assert r.json()["error"] == "DB_CONNECT_FAILED"
