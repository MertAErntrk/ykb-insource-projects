"""Taşıma geçmişi, temizlik takibi ve denetim kaydı (SQLite). Şifre/veri satırı tutulmaz."""

import json
import math
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Database

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "failed", "cancelled"},
    "running": {"cancelling", "done", "failed", "partial"},
    "cancelling": {"cancelled", "failed", "partial", "done"},
    "done": set(),
    "failed": set(),
    "partial": set(),
    "cancelled": set(),
}
_ACTIVE = ("queued", "running", "cancelling")
_CHECK_STATUSES = {"active", "expired_present", "deleted_verified", "unverifiable"}
_AUDIT_ACTIONS = {"login", "logout", "connect", "transfer_start", "cancel", "drop_script", "check", "reveal"}
_RESTART_DETAIL = "Sunucu yeniden başladı; iş yarıda kaldı."


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


class Repository:
    def __init__(self, db: Database, now: Callable[[], datetime] = _utc_now) -> None:
        self.db = db
        self._now = now

    def _ts(self) -> str:
        return _fmt(self._now())

    # --- işler ---
    def create_job(self, job: dict, columns: list[dict]) -> None:
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO jobs (id, owner, status, source_alias, source_schema, source_table,"
                " target_alias, target_schema, target_table, mode, filters_json, total_rows,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job["id"], job["owner"], "queued",
                    job.get("source_alias"), job.get("source_schema"), job.get("source_table"),
                    job.get("target_alias"), job.get("target_schema"), job.get("target_table"),
                    job.get("mode"), json.dumps(job.get("filters") or []), job.get("total_rows"),
                    self._ts(),
                ),
            )
            for c in columns:
                self.db.execute(
                    "INSERT INTO job_columns (job_id, position, column_name, source_type,"
                    " rule_type, params_json) VALUES (?,?,?,?,?,?)",
                    (
                        job["id"], c.get("position"), c["column_name"], c.get("source_type"),
                        c["rule_type"], json.dumps(c.get("params") or {}),
                    ),
                )

    def _job_dict(self, row: Any) -> dict:
        d = {k: row[k] for k in row.keys()}
        d["filters"] = json.loads(d.pop("filters_json") or "[]")
        rps, total = d["rows_per_sec"], d["total_rows"]
        d["eta_sec"] = (
            math.ceil(max(total - d["rows_written"], 0) / rps)
            if d["status"] == "running" and rps and rps > 0 and total is not None
            else None
        )
        return d

    def get_job(self, job_id: str, with_columns: bool = True) -> dict | None:
        row = self.db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row is None:
            return None
        job = self._job_dict(row)
        if with_columns:
            job["columns"] = [
                {
                    "column_name": c["column_name"],
                    "rule_type": c["rule_type"],
                    "params": json.loads(c["params_json"] or "{}"),
                    "source_type": c["source_type"],
                    "position": c["position"],
                }
                for c in self.db.query(
                    "SELECT * FROM job_columns WHERE job_id=? ORDER BY position, id", (job_id,)
                )
            ]
        return job

    def list_jobs(self, owner: str | None, limit: int = 200) -> list[dict]:
        if owner is None:
            rows = self.db.query(
                "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            )
        else:
            rows = self.db.query(
                "SELECT * FROM jobs WHERE owner=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (owner, limit),
            )
        return [self._job_dict(r) for r in rows]

    def set_status(
        self, job_id: str, status: str, *, error_code: str | None = None, error_detail: str | None = None
    ) -> None:
        with self.db.transaction():
            row = self.db.query_one("SELECT status FROM jobs WHERE id=?", (job_id,))
            if row is None:
                raise ValueError(f"İş bulunamadı: {job_id}")
            if status not in TRANSITIONS.get(row["status"], set()):
                raise ValueError(f"Geçersiz durum geçişi: {row['status']} -> {status}")
            now = self._ts()
            started = now if status == "running" else None
            finished = now if not TRANSITIONS[status] else None
            self.db.execute(
                "UPDATE jobs SET status=?, started_at=COALESCE(?, started_at),"
                " finished_at=COALESCE(?, finished_at), error_code=COALESCE(?, error_code),"
                " error_detail=COALESCE(?, error_detail) WHERE id=?",
                (status, started, finished, error_code, error_detail, job_id),
            )

    def update_progress(
        self, job_id: str, rows_read: int, rows_written: int, rows_per_sec: float,
        total_rows: int | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE jobs SET rows_read=?, rows_written=?, rows_per_sec=?,"
            " total_rows=COALESCE(?, total_rows) WHERE id=?",
            (rows_read, rows_written, rows_per_sec, total_rows, job_id),
        )

    def active_job_for_target(self, alias: str, schema: str, table: str) -> str | None:
        row = self.db.query_one(
            "SELECT id FROM jobs WHERE target_alias=? AND target_schema=? AND target_table=?"
            " AND status IN (?,?,?) ORDER BY created_at LIMIT 1",
            (alias, schema, table, *_ACTIVE),
        )
        return row["id"] if row else None

    def count_active_jobs(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE status IN (?,?,?)", _ACTIVE)
        return int(row["n"])

    # --- temizlik takibi ---
    def track_table(
        self, job_id: str, owner: str, alias: str, schema: str, table: str,
        object_id: int | None, object_created: str | None, retention_days: int,
    ) -> int:
        expires = _fmt(self._now() + timedelta(days=retention_days))
        cur = self.db.execute(
            "INSERT INTO tracked_tables (job_id, owner, alias, schema_name, table_name, object_id,"
            " object_created, table_status, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, owner, alias, schema, table, object_id, object_created, "active", expires),
        )
        return int(cur.lastrowid)

    def mark_verified_visible(self, tracked_id: int) -> None:
        self.db.execute(
            "UPDATE tracked_tables SET verified_visible_at=COALESCE(verified_visible_at, ?)"
            " WHERE id=?",
            (self._ts(), tracked_id),
        )

    @staticmethod
    def _tracked_dict(r: Any) -> dict:
        return {
            "id": r["id"], "job_id": r["job_id"], "owner": r["owner"], "alias": r["alias"],
            "schema": r["schema_name"], "table": r["table_name"],
            "object_id": r["object_id"], "object_created": r["object_created"],
            "verified_visible_at": r["verified_visible_at"], "table_status": r["table_status"],
            "expires_at": r["expires_at"], "last_checked_at": r["last_checked_at"],
        }

    def get_tracked(self, tracked_id: int) -> dict | None:
        r = self.db.query_one("SELECT * FROM tracked_tables WHERE id=?", (tracked_id,))
        return self._tracked_dict(r) if r else None

    def tracked_for_job(self, job_id: str) -> dict | None:
        r = self.db.query_one("SELECT * FROM tracked_tables WHERE job_id=? ORDER BY id LIMIT 1", (job_id,))
        return self._tracked_dict(r) if r else None

    def list_tracked(self, owner: str | None = None, status: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM tracked_tables WHERE 1=1", []
        if owner is not None:
            sql += " AND owner=?"
            params.append(owner)
        if status is not None:
            sql += " AND table_status=?"
            params.append(status)
        rows = self.db.query(sql + " ORDER BY id DESC", tuple(params))
        return [self._tracked_dict(r) for r in rows]

    def due_for_check(self, now: datetime | str | None = None) -> list[dict]:
        if now is None:
            now = self._now()
        ts = now if isinstance(now, str) else _fmt(now)
        rows = self.db.query(
            "SELECT * FROM tracked_tables WHERE (table_status='active' AND expires_at<=?)"
            " OR table_status='expired_present' ORDER BY expires_at, id",
            (ts,),
        )
        return [self._tracked_dict(r) for r in rows]

    def record_check(
        self, tracked_id: int, *, visible: bool, object_id: int | None,
        object_created: str | None, result_status: str, error: str | None = None,
    ) -> dict:
        if result_status not in _CHECK_STATUSES:
            raise ValueError(f"Geçersiz result_status: {result_status}")
        with self.db.transaction():
            t = self.db.query_one(
                "SELECT table_status, verified_visible_at FROM tracked_tables WHERE id=?", (tracked_id,)
            )
            if t is None:
                raise ValueError(f"Takip kaydı bulunamadı: {tracked_id}")
            effective = result_status
            if effective == "deleted_verified" and t["verified_visible_at"] is None:
                effective = "unverifiable"
            if t["table_status"] == "deleted_verified":
                effective = "deleted_verified"  # geri dönüş yok
            now = self._ts()
            cur = self.db.execute(
                "INSERT INTO table_checks (tracked_table_id, checked_at, visible, object_id,"
                " object_created, result_status, error) VALUES (?,?,?,?,?,?,?)",
                (tracked_id, now, 1 if visible else 0, object_id, object_created, effective, error),
            )
            self.db.execute(
                "UPDATE tracked_tables SET table_status=?, last_checked_at=? WHERE id=?",
                (effective, now, tracked_id),
            )
            return {
                "id": cur.lastrowid, "tracked_table_id": tracked_id, "checked_at": now,
                "visible": bool(visible), "object_id": object_id, "object_created": object_created,
                "result_status": effective, "error": error,
            }

    # --- denetim ---
    def audit(self, username: str, action: str, job_id: str | None = None, detail: str | None = None) -> None:
        if action not in _AUDIT_ACTIONS:
            raise ValueError(f"Geçersiz denetim eylemi: {action}")
        if detail is not None:
            detail = detail[:500]
        self.db.execute(
            "INSERT INTO audit_log (at, username, action, job_id, detail) VALUES (?,?,?,?,?)",
            (self._ts(), username, action, job_id, detail),
        )

    # --- açılış kurtarma ---
    def recover_on_startup(self) -> list[str]:
        with self.db.transaction():
            rows = self.db.query(
                "SELECT j.id AS id, (SELECT COUNT(*) FROM tracked_tables t WHERE t.job_id=j.id) AS n"
                " FROM jobs j WHERE j.status IN (?,?,?)",
                _ACTIVE,
            )
            now = self._ts()
            for r in rows:
                self.db.execute(
                    "UPDATE jobs SET status=?, error_code='INTERNAL', error_detail=?,"
                    " finished_at=? WHERE id=?",
                    ("partial" if r["n"] else "failed", _RESTART_DETAIL, now, r["id"]),
                )
            return [r["id"] for r in rows]
