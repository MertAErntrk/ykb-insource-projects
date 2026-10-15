"""SQLite (WAL) log veritabanı: tek bağlantı, RLock altında erişim, şema kurulumu."""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import Settings, project_path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    source_alias TEXT, source_schema TEXT, source_table TEXT,
    target_alias TEXT, target_schema TEXT, target_table TEXT,
    mode TEXT,
    filters_json TEXT,
    total_rows INTEGER,
    rows_read INTEGER NOT NULL DEFAULT 0,
    rows_written INTEGER NOT NULL DEFAULT 0,
    rows_per_sec REAL,
    error_code TEXT,
    error_detail TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS job_columns (
    id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    position INTEGER,
    column_name TEXT,
    source_type TEXT,
    rule_type TEXT,
    params_json TEXT
);
CREATE TABLE IF NOT EXISTS tracked_tables (
    id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    owner TEXT NOT NULL,
    alias TEXT,
    schema_name TEXT,
    table_name TEXT,
    object_id INTEGER,
    object_created TEXT,
    verified_visible_at TEXT,
    table_status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_checked_at TEXT
);
CREATE TABLE IF NOT EXISTS table_checks (
    id INTEGER PRIMARY KEY,
    tracked_table_id INTEGER NOT NULL REFERENCES tracked_tables(id),
    checked_at TEXT NOT NULL,
    visible INTEGER NOT NULL,
    object_id INTEGER,
    object_created TEXT,
    result_status TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    username TEXT,
    action TEXT NOT NULL,
    job_id TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_owner_created ON jobs(owner, created_at);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_tracked_status_expires ON tracked_tables(table_status, expires_at);
CREATE INDEX IF NOT EXISTS ix_tracked_job ON tracked_tables(job_id);
CREATE INDEX IF NOT EXISTS ix_job_columns_job ON job_columns(job_id);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        """Dış transaction BEGIN/COMMIT yapar; iç içe çağrılar dışa katılır. Hata → rollback."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def open_database(settings: Settings) -> Database:
    path = project_path(settings.tt_sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path)
    db.init_schema()
    return db
