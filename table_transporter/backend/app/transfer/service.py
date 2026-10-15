"""Taşıma servisi: başlatma doğrulaması, hedef kilidi, iş thread havuzu, iptal ve olaylar.

Kilit kontrolü + create_job tek threading.Lock altında yapılır (aynı hedefe iki iş yarışamaz).
İşin kaynak/hedef lease'leri istek thread'inde alınır: oturum (logout) iş kuyruktayken kapansa da
havuz iş bitene kadar açık kalır.
"""

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any

from app.anon.rules import ColumnInfo, RulePlan, compile_plan
from app.auth.session import Session, User
from app.config import Settings
from app.errors import AppError, ErrorCode
from app.log.repository import Repository
from app.oracle import dictionary, tns
from app.oracle import sql_templates as T
from app.oracle.identifiers import is_valid_ident
from app.oracle.pools import DbHandle
from app.oracle.safe_connection import SafeConnection
from app.transfer import compat, filters
from app.transfer.events import EventBroker
from app.transfer.pipeline import CancelToken, JobSpec, run_transfer

log = logging.getLogger(__name__)

_service_lock = threading.Lock()


@dataclass
class _Job:
    token: CancelToken
    broker: EventBroker
    future: Future | None = None


class _HeldHandle:
    """run_transfer'a verilen vekil: job_lease() önceden alınmış lease'i döner."""

    def __init__(self, lease) -> None:
        self._lease = lease

    @contextmanager
    def job_lease(self):
        yield self._lease


def get_repo(app: Any) -> Repository:
    repo = getattr(app.state, "repository", None)
    if repo is None:
        raise AppError(ErrorCode.INTERNAL, "Kayıt deposu hazır değil.")
    return repo


def get_service(app: Any) -> "TransferService":
    svc = getattr(app.state, "transfer_service", None)
    if svc is None:
        with _service_lock:
            svc = getattr(app.state, "transfer_service", None)
            if svc is None:
                svc = TransferService(app)
                app.state.transfer_service = svc
                app.state.cancel_job = svc.cancel
    return svc


def hmac_key(settings: Settings) -> bytes:
    return settings.tt_anon_hmac_key.get_secret_value().encode("utf-8")


def check_ident(value: object, what: str, max_len: int = 128) -> str:
    if not isinstance(value, str) or not is_valid_ident(value, max_len):
        raise AppError(ErrorCode.VALIDATION, f"Geçersiz {what} adı.")
    return value


def session_handle(session: Session, role: str) -> DbHandle:
    handle = session.resources.get(role)
    if handle is None or handle.closed:
        raise AppError(ErrorCode.DB_DISCONNECTED)
    return handle


def source_where(conn: SafeConnection, schema: str, table: str,
                 flt: list[dict], *, full_table: bool = False) -> tuple[list[ColumnInfo], str, dict]:
    """Kaynak kolonları + doğrulanmış WHERE (bind'li). full_table: filtresiz, tablonun tamamı."""
    cols, _ = dictionary.read_columns(conn, schema, table)
    where, binds = filters.build_where(flt, dictionary.filter_columns(cols), full_table=full_table)
    return cols, where, binds


def format_rows(n: int) -> str:
    """5000000 -> "5.000.000" (nokta binlik ayırıcı)."""
    return f"{n:,}".replace(",", ".")


def limit_message(limit: int, full_table: bool) -> str:
    if full_table:
        return (f"Tablo {format_rows(limit)} satırın üzerinde; tamamı taşınamaz. "
                "Filtre girerek kümeyi daraltın.")
    return f"Seçiminiz {format_rows(limit)} satırı aşıyor; lütfen filtreyi daraltın."


def target_info(conn: SafeConnection, schema: str, table: str) -> tuple[list[ColumnInfo], dict[str, dict]]:
    try:
        return dictionary.read_columns(conn, schema, table)
    except AppError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return [], {}
        raise


def read_columns_for(plan: RulePlan, cols: list[ColumnInfo], rules: list[dict]) -> list[ColumnInfo]:
    """Çıktı kolonları + date_shift çapaları, kaynak sırasıyla."""
    names = set(plan.output_columns)
    for r in rules:
        if r.get("type") == "date_shift" and isinstance(r.get("params"), dict):
            anchor = r["params"].get("anchor")
            if isinstance(anchor, str):
                names.add(anchor)
    return [c for c in cols if c.name in names]


class TransferService:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.settings: Settings = app.state.settings
        self._executor = ThreadPoolExecutor(
            max_workers=self.settings.tt_max_concurrent_jobs, thread_name_prefix="tt-job"
        )
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}

    @property
    def repo(self) -> Repository:
        return get_repo(self.app)

    def broker(self, job_id: str) -> EventBroker | None:
        with self._lock:
            entry = self._jobs.get(job_id)
        return entry.broker if entry is not None else None

    # --- başlatma ---
    def start(self, user: User, session: Session, req: dict) -> str:
        settings = self.settings
        repo = self.repo
        src = session_handle(session, "source")
        tgt = session_handle(session, "target")
        if not tns.is_target_allowed(settings, tgt.alias):
            raise AppError(ErrorCode.TARGET_DENIED)
        s_schema = check_ident(req["source"]["schema"], "şema")
        s_table = check_ident(req["source"]["table"], "tablo")
        t_schema = check_ident(req["target"]["schema"], "hedef şema")
        t_table = req["target"]["table"]
        mode = req["target"]["mode"]
        if mode not in ("create", "append"):
            raise AppError(ErrorCode.VALIDATION, "Geçersiz hedef modu.")
        rules, flt = req.get("rules") or [], req.get("filters") or []
        full_table = req.get("full_table") is True

        with src.connection() as sconn:
            cols, where, binds = source_where(sconn, s_schema, s_table, flt, full_table=full_table)

        if req.get("confirm_target") != f"{t_schema}.{t_table}":
            raise AppError(ErrorCode.VALIDATION, "Onay metni hedef tablo adıyla (ŞEMA.TABLO) birebir eşleşmeli.")
        has_keep = any(isinstance(r, dict) and r.get("type") == "keep" for r in rules)
        if not tns.is_target_allowed(settings, src.alias) and has_keep and req.get("confirm_unmasked_live") is not True:
            raise AppError(ErrorCode.VALIDATION,
                           "Canlı kaynaktan maskesiz (keep) kolon taşımak için açık onay gerekli.")

        target_cols: dict[str, ColumnInfo] | None = None
        with tgt.connection() as tconn:
            if mode == "create":
                if t_schema != tgt.user.upper():
                    raise AppError(ErrorCode.VALIDATION,
                                   "Yeni tablo yalnız bağlı kullanıcının kendi şemasında oluşturulabilir.")
                check_ident(t_table, "tablo", tgt.max_ident_len)
                if dictionary.table_object(tconn, t_schema, t_table) is not None:
                    raise AppError(ErrorCode.TABLE_EXISTS)
                if dictionary.schema_triggers(tconn, t_schema):
                    raise AppError(ErrorCode.TRIGGER_PRESENT,
                                   "Hedef şemada etkin şema/veritabanı trigger'ı var; tablo oluşturulamaz.")
            else:
                check_ident(t_table, "tablo")
                obj = dictionary.table_object(tconn, t_schema, t_table)
                if obj is None:
                    raise AppError(ErrorCode.NOT_FOUND, "Hedef tablo bulunamadı ya da erişim yetkiniz yok.")
                if str(obj.get("OBJECT_TYPE") or "").upper() != "TABLE":
                    raise AppError(ErrorCode.INCOMPATIBLE, "Hedef bir tablo değil.")
                triggers = dictionary.enabled_triggers(tconn, t_schema, t_table)
                if triggers:
                    raise AppError(ErrorCode.TRIGGER_PRESENT)
                tinfo = target_info(tconn, t_schema, t_table)
                report = compat.compat_report(cols, tinfo, None, obj, triggers, rules=rules)
                errors = [i for i in report["items"] if i["level"] == "error"]
                if errors:
                    first = errors[0]
                    prefix = f"{first['column']}: " if first["column"] else ""
                    raise AppError(ErrorCode.INCOMPATIBLE, f"{prefix}{first['message']}")
                target_cols = {c.name: c for c in tinfo[0]}

        plan = compile_plan(rules, cols, mode=mode, target_cols=target_cols, key=hmac_key(settings))

        with src.connection() as sconn:
            # Tavanlı sayım: en çok sınır + 1 satır okunur.
            stmt, cbinds = T.count_rows(s_schema, s_table, where, binds, settings.tt_row_limit + 1)
            total = sconn.count(stmt, cbinds, timeout_sec=settings.tt_count_timeout_sec)
        if total is None:
            raise AppError(ErrorCode.VALIDATION, "Sayım zaman aşımı; filtreyi daraltıp tekrar deneyin.")
        if total > settings.tt_row_limit:
            raise AppError(ErrorCode.LIMIT_EXCEEDED, limit_message(settings.tt_row_limit, full_table))

        by_rule = {r["column"]: r for r in rules}
        job_columns = [
            {"column_name": c.name, "rule_type": by_rule[c.name].get("type"),
             "params": by_rule[c.name].get("params") or {}, "source_type": c.data_type, "position": i}
            for i, c in enumerate(cols, start=1)
        ]
        job_id = str(uuid.uuid4())
        spec = JobSpec(
            job_id=job_id, owner=user.username, mode=mode, source_alias=src.alias,
            source_schema=s_schema, source_table=s_table, target_alias=tgt.alias,
            target_schema=t_schema, target_table=t_table, where_sql=where, binds=binds,
            row_limit=settings.tt_row_limit, plan=plan, read_columns=read_columns_for(plan, cols, rules),
            use_direct_path=bool(tgt.direct_path) and mode == "create",
            batch_rows=settings.tt_batch_rows, retention_days=settings.tt_retention_days,
        )

        stack = ExitStack()
        try:
            src_lease = stack.enter_context(src.job_lease())
            tgt_lease = stack.enter_context(tgt.job_lease())
            entry = _Job(CancelToken(), EventBroker())
            with self._lock:
                if repo.active_job_for_target(tgt.alias, t_schema, t_table) is not None:
                    raise AppError(ErrorCode.TARGET_LOCKED)
                repo.create_job({
                    "id": job_id, "owner": user.username, "source_alias": src.alias,
                    "source_schema": s_schema, "source_table": s_table, "target_alias": tgt.alias,
                    "target_schema": t_schema, "target_table": t_table, "mode": mode,
                    "filters": [] if full_table else flt, "total_rows": total,
                }, job_columns)
                self._jobs[job_id] = entry
                session.resources.setdefault("active_jobs", set()).add(job_id)
            try:
                repo.audit(user.username, "transfer_start", job_id,
                           f"{src.alias}:{s_schema}.{s_table} -> {tgt.alias}:{t_schema}.{t_table} ({mode})"
                           + (" [tam tablo]" if full_table else ""))
            except Exception:
                log.warning("Denetim kaydı yazılamadı: %s", job_id, exc_info=True)
            held = stack.pop_all()
        finally:
            stack.close()
        entry.future = self._executor.submit(
            self._run, spec, _HeldHandle(src_lease), _HeldHandle(tgt_lease), entry, session, held
        )
        return job_id

    def _run(self, spec: JobSpec, source: _HeldHandle, target: _HeldHandle, entry: _Job,
             session: Session, held: ExitStack) -> str:
        repo = self.repo
        final = "failed"
        try:
            final = run_transfer(spec, source=source, target=target, repo=repo,  # type: ignore[arg-type]
                                 cancel=entry.token, on_event=entry.broker.publish)
        except Exception:
            log.exception("İş beklenmedik biçimde durdu: %s", spec.job_id)
            try:
                repo.set_status(spec.job_id, "failed", error_code=ErrorCode.INTERNAL.value,
                                error_detail="Beklenmeyen bir hata oluştu.")
            except ValueError:
                pass
            entry.broker.publish("failed", {"job_id": spec.job_id, "status": "failed", "error": "INTERNAL",
                                            "detail": "Beklenmeyen bir hata oluştu.", "rows_written": 0})
        finally:
            held.close()
            with self._lock:
                jobs = session.resources.get("active_jobs")
                if jobs is not None:
                    jobs.discard(spec.job_id)
                self._jobs.pop(spec.job_id, None)
        if final == "done" and spec.mode == "create":
            callback = getattr(self.app.state, "after_create", None)
            if callable(callback):
                try:
                    tracked = repo.tracked_for_job(spec.job_id)
                    if tracked is not None:
                        callback(tracked["id"])
                except Exception:
                    log.warning("after_create çağrısı başarısız: %s", spec.job_id, exc_info=True)
        return final

    # --- iptal ---
    def cancel(self, job_id: str) -> None:
        """İdempotent: bitmiş/bilinmeyen işte bir şey yapmaz."""
        with self._lock:
            entry = self._jobs.get(job_id)
        if entry is None:
            return
        entry.token.cancel()
        try:
            job = self.repo.get_job(job_id, with_columns=False)
            if job is not None and job["status"] == "running":
                self.repo.set_status(job_id, "cancelling")
                entry.broker.publish("cancelling", {"job_id": job_id, "status": "cancelling"})
        except ValueError:
            pass  # bu arada bitti ya da pipeline yazdı

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            entries = list(self._jobs.values())
        for e in entries:
            e.token.cancel()
        self._executor.shutdown(wait=wait)
