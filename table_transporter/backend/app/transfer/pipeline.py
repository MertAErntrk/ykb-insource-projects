"""Taşıma pipeline'ı: okuyucu thread → sınırlı kuyruk → anonimleştirme → yazıcı (ana thread).

Atomiklik (plan Karar 2):
- append: tüm yükleme hedefte tek transaction() içinde; hata/iptal → rollback, commit yok,
  rows_written=0 raporlanır → hedef ya hiç değişmez ya tam yazılır.
- create: CREATE TABLE → HEMEN OBJECT_ID + CREATED ile repo.track_table (yüklemeden önce) →
  direct path (batch başına örtük commit) ya da batch başına ayrı transaction() ile executemany.
  Tablo oluştuktan sonraki hata → partial (tablo izlemede kalır). ORA-00955 → failed, izlenmez.
Sert tavan (Karar 7): SELECT'te FETCH FIRST :limit + yazıcı sayacı; kaynak fazla dönse de
row_limit'ten fazla satır yazılmaz. SQL yalnız sql_templates şablonları ve SafeConnection ile.
"""

import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime

import pyarrow as pa

from app.anon.rules import ColumnInfo, RulePlan
from app.errors import AppError, ErrorCode
from app.log.repository import Repository
from app.oracle import sql_templates as T
from app.oracle.pools import DbHandle
from app.oracle.safe_connection import NUMBER_TEXT_META, to_db_rows

log = logging.getLogger(__name__)

_QUEUE_SIZE = 4
_POLL_SEC = 0.2
_PROGRESS_INTERVAL = 1.0
_JOIN_TIMEOUT = 30.0


@dataclass
class JobSpec:
    job_id: str
    owner: str
    mode: str  # 'create' | 'append'
    source_alias: str
    source_schema: str
    source_table: str
    target_alias: str
    target_schema: str
    target_table: str
    where_sql: str
    binds: dict
    row_limit: int
    plan: RulePlan
    read_columns: list[ColumnInfo]  # output + date_shift çapası; kaynak sırası
    use_direct_path: bool
    batch_rows: int = 100_000
    insert_batch: int = 50_000
    retention_days: int = 7


class CancelToken:
    """İş başına iptal bayrağı (servis/route set eder, pipeline okur)."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


class _Cancelled(Exception):
    """İptal algılandı; transaction() bunu görünce rollback yapar."""


# --- Okuyucu -----------------------------------------------------------------------------------


class _Reader(threading.Thread):
    """Kaynaktan Arrow batch'leri okur ve kuyruğa koyar: ("batch", rb) | ("end", None) | ("error", exc)."""

    def __init__(self, lease, stmt: T.Stmt, binds: dict, columns: list[ColumnInfo], batch_rows: int) -> None:
        super().__init__(name="tt-reader", daemon=True)
        self.queue: queue.Queue = queue.Queue(maxsize=_QUEUE_SIZE)
        self._lease = lease
        self._stmt = stmt
        self._binds = binds
        self._columns = columns
        self._batch_rows = batch_rows
        self._halt = threading.Event()
        self._conn = None
        self._conn_lock = threading.Lock()

    def run(self) -> None:
        try:
            with self._lease.connection() as conn:
                with self._conn_lock:
                    self._conn = conn
                try:
                    batches = conn.fetch_arrow(
                        self._stmt, self._binds, columns=self._columns, batch_rows=self._batch_rows
                    )
                    with closing(batches):
                        for batch in batches:
                            if not self._put(("batch", batch)):
                                return
                finally:
                    with self._conn_lock:
                        self._conn = None
            self._put(("end", None))
        except Exception as exc:  # hata yazıcıya iletilir
            self._put(("error", exc))

    def _put(self, item: tuple) -> bool:
        while not self._halt.is_set():
            try:
                self.queue.put(item, timeout=_POLL_SEC)
                return True
            except queue.Full:
                continue
        return False

    def stop(self, *, cancel_query: bool) -> None:
        """Okumayı durdurur; cancel_query ise sürmekte olan kaynak sorgusu kesilir."""
        self._halt.set()
        if not cancel_query:
            return
        with self._conn_lock:
            conn = self._conn
        if conn is not None:
            try:
                conn.cancel()
            except Exception:  # bağlantı zaten kapanmış olabilir
                log.warning("Kaynak sorgusu iptal edilemedi.", exc_info=True)


# --- Yardımcılar -------------------------------------------------------------------------------


def _with_number_meta(out: pa.RecordBatch, src_schema: pa.Schema, plan: RulePlan) -> pa.RecordBatch:
    """apply_batch metadata'yı düşürür; metin-sayı (precision'sız NUMBER) alanlarına geri eklenir
    ki to_db_rows Decimal yazsın. hmac çıktısı hex metindir, metadata eklenmez."""
    fields = []
    changed = False
    for f in out.schema:
        meta = None
        if f.name in src_schema.names:
            src = src_schema.field(f.name)
            if (
                src.metadata
                and src.metadata.get(NUMBER_TEXT_META) == b"1"
                and plan.rule_type(f.name) != "hmac"
                and pa.types.is_string(f.type)
            ):
                meta = {NUMBER_TEXT_META: b"1"}
                changed = True
        fields.append(pa.field(f.name, f.type, nullable=True, metadata=meta))
    if not changed:
        return out
    return pa.RecordBatch.from_arrays(out.columns, schema=pa.schema(fields))


def _created_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    return str(value)


class _Run:
    """Tek işin durum ve olay yönetimi (yalnız ana thread)."""

    def __init__(self, spec: JobSpec, repo: Repository, on_event: Callable[[str, dict], None]) -> None:
        self.spec = spec
        self.repo = repo
        self._on_event = on_event
        self.rows_read = 0
        self.rows_loaded = 0  # hedefe gönderilen (append'te commit'e kadar kesin değil)
        self.rows_committed = 0
        self.created = False
        self.cancelling = False
        self.started = time.monotonic()
        self._last_emit: float | None = None
        self._dirty = False
        try:
            job = repo.get_job(spec.job_id, with_columns=False) or {}
        except Exception:
            log.warning("İş kaydı okunamadı: %s", spec.job_id, exc_info=True)
            job = {}
        self.total_rows = job.get("total_rows")

    def emit(self, name: str, data: dict) -> None:
        try:
            self._on_event(name, {"job_id": self.spec.job_id, **data})
        except Exception:  # olay tüketicisinin hatası işi bozmaz
            log.warning("Olay iletilemedi: %s", name, exc_info=True)

    def status(self, status: str, **kw) -> None:
        try:
            job = self.repo.get_job(self.spec.job_id, with_columns=False)
            if job is not None and job["status"] == status:
                return  # ör. servis cancelling'i önceden yazmış olabilir
            self.repo.set_status(self.spec.job_id, status, **kw)
        except ValueError:
            log.warning("Durum geçişi yapılamadı: %s -> %s", self.spec.job_id, status, exc_info=True)

    def check_cancel(self, cancel: CancelToken) -> None:
        if cancel.is_set():
            if not self.cancelling:
                self.cancelling = True
                self.status("cancelling")
                self.emit("cancelling", {"status": "cancelling"})
            raise _Cancelled()

    def rate(self) -> float:
        elapsed = time.monotonic() - self.started
        return round(self.rows_loaded / elapsed, 1) if elapsed > 0 else 0.0

    def progress(self, *, force: bool = False) -> None:
        rps = self.rate()
        self.repo.update_progress(self.spec.job_id, self.rows_read, self.rows_loaded, rps)
        self._dirty = True
        now = time.monotonic()
        if not force and self._last_emit is not None and now - self._last_emit < _PROGRESS_INTERVAL:
            return
        self._last_emit = now
        self._dirty = False
        eta = None
        if rps > 0 and self.total_rows is not None:
            eta = math.ceil(max(self.total_rows - self.rows_loaded, 0) / rps)
        self.emit("progress", {
            "status": "running", "rows_read": self.rows_read, "rows_written": self.rows_loaded,
            "total_rows": self.total_rows, "rows_per_sec": rps, "eta_sec": eta,
        })

    def final_progress(self) -> None:
        if self._dirty or self._last_emit is None:
            self.progress(force=True)

    def written(self) -> int:
        return self.rows_committed


def _next_item(reader: _Reader, cancel: CancelToken, run: _Run) -> tuple:
    while True:
        run.check_cancel(cancel)
        try:
            return reader.queue.get(timeout=_POLL_SEC)
        except queue.Empty:
            if not reader.is_alive() and reader.queue.empty():
                raise AppError(ErrorCode.INTERNAL, "Okuyucu beklenmedik biçimde durdu.") from None


def _load(spec: JobSpec, tconn, reader: _Reader, cancel: CancelToken, run: _Run, insert_stmt: T.Stmt) -> None:
    """Kuyruktan batch alır, anonimleştirir ve yazar. Sert tavan: row_limit'ten fazla yazılmaz."""
    plan = spec.plan
    columns = plan.output_columns
    while True:
        kind, item = _next_item(reader, cancel, run)
        if kind == "end":
            break
        if kind == "error":
            raise item
        batch: pa.RecordBatch = item
        run.rows_read += batch.num_rows
        remaining = spec.row_limit - run.rows_loaded
        capped = batch.num_rows > remaining
        if capped:
            log.warning(
                "Sert satır tavanı: iş %s kaynağı %d satırı aştı; okuma durduruluyor.",
                spec.job_id, spec.row_limit,
            )
            batch = batch.slice(0, max(remaining, 0))
        if batch.num_rows:
            out = _with_number_meta(plan.apply_batch(batch), batch.schema, plan)
            rows = to_db_rows(out)
            if spec.mode == "append":
                tconn.insert_many(insert_stmt, rows, spec.insert_batch)
            elif spec.use_direct_path:
                tconn.direct_path_load(spec.target_schema, spec.target_table, columns, rows, spec.batch_rows)
                run.rows_committed += len(rows)
            else:
                with tconn.transaction():
                    tconn.insert_many(insert_stmt, rows, spec.insert_batch)
                run.rows_committed += len(rows)
            run.rows_loaded += len(rows)
            run.progress()
        if capped:
            reader.stop(cancel_query=True)
            break
    run.final_progress()


def _create_and_track(spec: JobSpec, tconn, run: _Run, create_stmt: T.Stmt) -> None:
    tconn.execute_create(create_stmt)  # ORA-00955 → AppError(TABLE_EXISTS); created False kalır
    run.created = True
    obj_id = obj_created = None
    lookup_error: AppError | None = None
    try:
        stmt, binds = T.dict_object(spec.target_schema, spec.target_table)
        row = tconn.query_one(stmt, binds)
        if row is not None:
            obj_id = None if row.get("OBJECT_ID") is None else int(row["OBJECT_ID"])
            obj_created = _created_text(row.get("CREATED"))
    except AppError as exc:
        lookup_error = exc
    # Write-ahead: tablo yükleme başlamadan izlemeye girer (kimlik okunamasa bile).
    run.repo.track_table(
        spec.job_id, spec.owner, spec.target_alias, spec.target_schema, spec.target_table,
        obj_id, obj_created, spec.retention_days,
    )
    if lookup_error is not None:
        raise lookup_error
    if obj_id is None:
        raise AppError(ErrorCode.INTERNAL, "Oluşturulan tablonun kimliği (OBJECT_ID) okunamadı.")


def _statements(spec: JobSpec) -> tuple[T.Stmt, dict, T.Stmt | None, T.Stmt | None]:
    if spec.mode not in ("create", "append"):
        raise AppError(ErrorCode.VALIDATION, "Geçersiz hedef modu.")
    if type(spec.row_limit) is not int or spec.row_limit < 1:
        raise AppError(ErrorCode.VALIDATION, "Geçersiz satır sınırı.")
    try:
        sel, sel_binds = T.select_rows(
            spec.source_schema, spec.source_table, [c.name for c in spec.read_columns],
            spec.where_sql, spec.binds, spec.row_limit,
        )
        create = (
            T.create_table(spec.target_schema, spec.target_table, spec.plan.target_column_defs())
            if spec.mode == "create" else None
        )
        insert = (
            T.insert_rows(spec.target_schema, spec.target_table, spec.plan.output_columns)
            if spec.mode == "append" or not spec.use_direct_path else None
        )
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION, f"Taşıma sorgusu hazırlanamadı: {exc}") from None
    return sel, sel_binds, create, insert


# --- Giriş noktası -----------------------------------------------------------------------------


def run_transfer(
    spec: JobSpec,
    *,
    source: DbHandle,
    target: DbHandle,
    repo: Repository,
    cancel: CancelToken,
    on_event: Callable[[str, dict], None],
) -> str:
    """İşi sonuna kadar yürütür (çağıran thread'i bloklar) ve son durumu döner:
    done | failed | partial | cancelled."""
    run = _Run(spec, repo, on_event)
    run.status("running")
    error: AppError | None = None
    cancelled = False
    try:
        sel, sel_binds, create_stmt, insert_stmt = _statements(spec)
        run.check_cancel(cancel)
        with source.job_lease() as src_lease, target.job_lease() as tgt_lease:
            with tgt_lease.connection() as tconn:
                if spec.mode == "create":
                    _create_and_track(spec, tconn, run, create_stmt)
                    run.check_cancel(cancel)
                reader = _Reader(src_lease, sel, sel_binds, spec.read_columns, spec.batch_rows)
                reader.start()
                finished = False
                try:
                    if spec.mode == "append":
                        with tconn.transaction():
                            _load(spec, tconn, reader, cancel, run, insert_stmt)
                        run.rows_committed = run.rows_loaded
                    else:
                        _load(spec, tconn, reader, cancel, run, insert_stmt)
                    finished = True
                finally:
                    reader.stop(cancel_query=not finished)
                    reader.join(_JOIN_TIMEOUT)
                    if reader.is_alive():
                        log.warning("Okuyucu thread zamanında durmadı: %s", spec.job_id)
    except _Cancelled:
        cancelled = True
    except AppError as exc:
        error = exc
    except Exception:
        log.exception("Taşıma beklenmedik hatayla durdu: %s", spec.job_id)
        error = AppError(ErrorCode.INTERNAL)

    written = run.written()
    try:
        repo.update_progress(spec.job_id, run.rows_read, written, run.rate())
    except Exception:
        log.warning("Son ilerleme yazılamadı: %s", spec.job_id, exc_info=True)

    if cancelled:
        run.status("cancelled")
        run.emit("cancelled", {"status": "cancelled", "rows_written": written})
        return "cancelled"
    if error is not None:
        final = "partial" if run.created else "failed"
        run.status(final, error_code=error.code.value, error_detail=error.detail)
        run.emit("failed", {
            "status": final, "error": error.code.value, "detail": error.detail, "rows_written": written,
        })
        return final
    run.status("done")
    job = repo.get_job(spec.job_id, with_columns=False) or {}
    run.emit("done", {
        "status": "done", "rows_written": written, "finished_at": job.get("finished_at"),
        "target": {"schema": spec.target_schema, "table": spec.target_table},
    })
    return "done"
