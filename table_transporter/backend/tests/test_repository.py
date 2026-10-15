import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.log.db import Database
from app.log.repository import Repository


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def repo(db, clock):
    return Repository(db, now=clock)


def mkjob(i="j1", owner="mert", target="T1"):
    return {
        "id": i, "owner": owner, "source_alias": "A", "source_schema": "S", "source_table": "X",
        "target_alias": "B", "target_schema": "TEST", "target_table": target, "mode": "create",
        "filters": [{"column": "C", "op": "eq", "value": 1}], "total_rows": 1000,
    }


COLS = [
    {"position": 2, "column_name": "B", "source_type": "VARCHAR2", "rule_type": "mask", "params": {"show_last": 4}},
    {"position": 1, "column_name": "A", "source_type": "NUMBER", "rule_type": "keep", "params": {}},
]


def test_schema_wal_and_version(db):
    assert db.query_one("PRAGMA journal_mode")[0] == "wal"
    assert db.query_one("PRAGMA user_version")[0] == 1
    assert db.query_one("PRAGMA foreign_keys")[0] == 1
    names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "job_columns", "tracked_tables", "table_checks", "audit_log"} <= names


def test_create_get_list(repo):
    repo.create_job(mkjob(), COLS)
    j = repo.get_job("j1")
    assert j["status"] == "queued" and j["created_at"] == "2026-10-15T09:00:00Z"
    assert j["filters"] == [{"column": "C", "op": "eq", "value": 1}]
    assert [c["column_name"] for c in j["columns"]] == ["A", "B"]
    assert j["columns"][1]["params"] == {"show_last": 4}
    assert j["eta_sec"] is None and "filters_json" not in j
    assert "columns" not in repo.get_job("j1", with_columns=False)
    assert repo.get_job("yok") is None


def test_list_order_and_owner(repo, clock):
    repo.create_job(mkjob("a"), [])
    clock.t += timedelta(seconds=5)
    repo.create_job(mkjob("b", owner="x"), [])
    assert [j["id"] for j in repo.list_jobs(None)] == ["b", "a"]
    assert [j["id"] for j in repo.list_jobs("mert")] == ["a"]
    assert "columns" not in repo.list_jobs("mert")[0]


def test_transitions_and_timestamps(repo, clock):
    repo.create_job(mkjob(), [])
    repo.set_status("j1", "running")
    j = repo.get_job("j1")
    assert j["started_at"] == "2026-10-15T09:00:00Z" and j["finished_at"] is None
    clock.t += timedelta(seconds=30)
    repo.set_status("j1", "failed", error_code="ORA_ERROR", error_detail="x")
    j = repo.get_job("j1")
    assert j["finished_at"] == "2026-10-15T09:00:30Z" and j["error_code"] == "ORA_ERROR"
    with pytest.raises(ValueError):
        repo.set_status("j1", "running")
    repo.create_job(mkjob("j2", target="T2"), [])
    with pytest.raises(ValueError):
        repo.set_status("j2", "done")
    repo.set_status("j2", "running")
    repo.set_status("j2", "cancelling")
    repo.set_status("j2", "cancelled")
    with pytest.raises(ValueError):
        repo.set_status("yok", "running")


def test_eta(repo):
    repo.create_job(mkjob(), [])
    repo.update_progress("j1", 100, 100, 50.0)
    assert repo.get_job("j1")["eta_sec"] is None  # queued
    repo.set_status("j1", "running")
    repo.update_progress("j1", 300, 300, 100.0, total_rows=1000)
    assert repo.get_job("j1")["eta_sec"] == 7
    repo.update_progress("j1", 300, 300, 0.0)
    assert repo.get_job("j1")["eta_sec"] is None


def test_target_lock_and_active_count(repo):
    repo.create_job(mkjob(), [])
    assert repo.active_job_for_target("B", "TEST", "T1") == "j1"
    assert repo.active_job_for_target("B", "TEST", "OTHER") is None
    assert repo.count_active_jobs() == 1
    repo.set_status("j1", "failed")
    assert repo.active_job_for_target("B", "TEST", "T1") is None
    assert repo.count_active_jobs() == 0


def test_track_and_expiry(repo):
    repo.create_job(mkjob(), [])
    tid = repo.track_table("j1", "mert", "B", "TEST", "T1", 55, "2026-10-15T09:00:00Z", 7)
    t = repo.get_tracked(tid)
    assert t["expires_at"] == "2026-10-22T09:00:00Z" and t["table_status"] == "active"
    assert t["schema"] == "TEST" and t["table"] == "T1" and t["verified_visible_at"] is None
    assert repo.tracked_for_job("j1")["id"] == tid
    assert repo.list_tracked(owner="x") == []
    assert len(repo.list_tracked(status="active")) == 1
    repo.mark_verified_visible(tid)
    assert repo.get_tracked(tid)["verified_visible_at"] == "2026-10-15T09:00:00Z"


def test_deleted_requires_verified(repo):
    repo.create_job(mkjob(), [])
    tid = repo.track_table("j1", "mert", "B", "TEST", "T1", 55, None, 7)
    r = repo.record_check(tid, visible=False, object_id=None, object_created=None, result_status="deleted_verified")
    assert r["result_status"] == "unverifiable" and r["visible"] is False
    t = repo.get_tracked(tid)
    assert t["table_status"] == "unverifiable" and t["last_checked_at"] is not None
    repo.mark_verified_visible(tid)
    r = repo.record_check(tid, visible=False, object_id=None, object_created=None, result_status="deleted_verified")
    assert r["result_status"] == "deleted_verified"
    repo.record_check(tid, visible=True, object_id=55, object_created=None, result_status="active")
    assert repo.get_tracked(tid)["table_status"] == "deleted_verified"
    with pytest.raises(ValueError):
        repo.record_check(tid, visible=True, object_id=1, object_created=None, result_status="bogus")


def test_due_for_check(repo, clock):
    repo.create_job(mkjob(), [])
    a = repo.track_table("j1", "mert", "B", "TEST", "T1", 1, None, 7)
    assert repo.due_for_check(clock.t) == []
    later = clock.t + timedelta(days=7)
    assert [t["id"] for t in repo.due_for_check(later)] == [a]
    repo.record_check(a, visible=True, object_id=1, object_created=None, result_status="expired_present")
    assert [t["id"] for t in repo.due_for_check(clock.t)] == [a]
    repo.record_check(a, visible=True, object_id=1, object_created=None, result_status="unverifiable")
    assert repo.due_for_check(later) == []


def test_audit(repo, db):
    repo.audit("mert", "login", detail="x" * 600)
    row = db.query_one("SELECT * FROM audit_log")
    assert len(row["detail"]) == 500 and row["action"] == "login"
    with pytest.raises(ValueError):
        repo.audit("mert", "hack")


def test_recover_on_startup(repo):
    repo.create_job(mkjob("p", target="P"), [])
    repo.set_status("p", "running")
    repo.track_table("p", "mert", "B", "TEST", "P", 1, None, 7)
    repo.create_job(mkjob("f", target="F"), [])
    repo.create_job(mkjob("d", target="D"), [])
    repo.set_status("d", "running")
    repo.set_status("d", "done")
    assert sorted(repo.recover_on_startup()) == ["f", "p"]
    p, f = repo.get_job("p"), repo.get_job("f")
    assert p["status"] == "partial" and f["status"] == "failed"
    assert f["error_code"] == "INTERNAL" and f["finished_at"] and "yeniden başladı" in f["error_detail"]
    assert repo.get_job("d")["status"] == "done"
    assert repo.recover_on_startup() == []


def test_concurrent_progress(repo):
    repo.create_job(mkjob("a"), [])
    repo.create_job(mkjob("b", target="T2"), [])
    errs = []

    def work(jid):
        try:
            for i in range(300):
                repo.update_progress(jid, i, i, float(i))
        except Exception as e:  # pragma: no cover
            errs.append(e)

    ts = [threading.Thread(target=work, args=(j,)) for j in ("a", "b", "a", "b")]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert not errs
    assert repo.get_job("a")["rows_written"] == 299
