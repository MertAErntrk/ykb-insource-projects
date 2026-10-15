"""Taşıma uçları (API.md "Taşıma — /api/transfers" ve "SSE")."""

import asyncio
import logging

import pyarrow as pa
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.anon.preview import build_preview
from app.anon.rules import compile_plan
from app.auth.session import require_session
from app.deps import CurrentUser, SettingsDep
from app.errors import AppError, ErrorCode
from app.oracle import dictionary
from app.oracle import sql_templates as T
from app.oracle.routes import require_handle
from app.oracle.safe_connection import arrow_schema_for
from app.transfer import compat
from app.transfer.events import sse_stream
from app.transfer.service import check_ident, get_repo, get_service, hmac_key, source_where, target_info

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transfers", tags=["transfers"])

_PREVIEW_ROWS = 5
_DISCONNECT_POLL_SEC = 0.5


class TableRef(BaseModel):
    schema_: str = Field(alias="schema")
    table: str


class TargetRef(TableRef):
    mode: str


class CountBody(BaseModel):
    source: TableRef
    filters: list[dict] = Field(default_factory=list)
    full_table: bool = False


class PreviewBody(CountBody):
    rules: list[dict] = Field(default_factory=list)


class CompatBody(BaseModel):
    source: TableRef
    target: TableRef
    rules: list[dict] = Field(default_factory=list)


class StartBody(BaseModel):
    source: TableRef
    target: TargetRef
    rules: list[dict] = Field(default_factory=list)
    filters: list[dict] = Field(default_factory=list)
    full_table: bool = False
    confirm_target: str
    confirm_unmasked_live: bool = False


def _ref(ref: TableRef) -> tuple[str, str]:
    return check_ident(ref.schema_, "şema"), check_ident(ref.table, "tablo")


def _owned_job(request: Request, user, job_id: str, *, with_columns: bool = True) -> dict:
    job = get_repo(request.app).get_job(job_id, with_columns=with_columns)
    if job is None or (job["owner"] != user.username and not user.is_admin):
        raise AppError(ErrorCode.NOT_FOUND)
    return job


@router.post("/count")
async def count(body: CountBody, request: Request, user: CurrentUser, settings: SettingsDep):
    handle = require_handle(request, "source")
    schema, table = _ref(body.source)
    holder: dict = {}

    def work() -> int | None:
        with handle.connection() as conn:
            _, where, binds = source_where(conn, schema, table, body.filters, full_table=body.full_table)
            # Tavanlı: en çok sınır + 1 satır okunur; sonuç sınırı aşarsa kesin sayı bilinmez.
            stmt, cbinds = T.count_rows(schema, table, where, binds, settings.tt_row_limit + 1)
            holder["conn"] = conn
            if holder.get("aborted"):
                return None
            return conn.count(stmt, cbinds, timeout_sec=settings.tt_count_timeout_sec)

    task = asyncio.ensure_future(run_in_threadpool(work))
    while True:
        done, _ = await asyncio.wait({task}, timeout=_DISCONNECT_POLL_SEC)
        if done:
            break
        if await request.is_disconnected():
            holder["aborted"] = True
            conn = holder.get("conn")
            if conn is not None:
                try:
                    conn.cancel()  # sayım ORA-01013 ile None döner
                except AppError:
                    log.warning("Sayım iptal edilemedi.", exc_info=True)
    n = task.result()
    limit = settings.tt_row_limit
    over = n is not None and n > limit
    return {"count": None if over else n, "limit": limit, "over_limit": over, "timed_out": n is None}


@router.post("/preview")
def preview(body: PreviewBody, request: Request, user: CurrentUser, settings: SettingsDep):
    handle = require_handle(request, "source")
    schema, table = _ref(body.source)
    with handle.connection() as conn:
        cols, where, binds = source_where(conn, schema, table, body.filters, full_table=body.full_table)
        plan = compile_plan(body.rules, cols, mode="create", target_cols=None, key=hmac_key(settings))
        read = [c for c in cols if c.supported]
        if not read:
            return {"columns": build_preview(pa.RecordBatch.from_pylist([]), plan, _PREVIEW_ROWS)}
        try:
            stmt, sbinds = T.select_rows(schema, table, [c.name for c in read], where, binds, _PREVIEW_ROWS)
        except ValueError as exc:
            raise AppError(ErrorCode.VALIDATION, f"Önizleme sorgusu hazırlanamadı: {exc}") from None
        batches = list(conn.fetch_arrow(stmt, sbinds, columns=read, batch_rows=_PREVIEW_ROWS))
    if not batches:
        batch = pa.RecordBatch.from_pylist([], schema=arrow_schema_for(read))
    elif len(batches) == 1:
        batch = batches[0]
    else:
        batch = pa.Table.from_batches(batches).combine_chunks().to_batches()[0]
    return {"columns": build_preview(batch, plan, _PREVIEW_ROWS)}


@router.post("/compat")
def compat_check(body: CompatBody, request: Request, user: CurrentUser, settings: SettingsDep):
    src = require_handle(request, "source")
    tgt = require_handle(request, "target")
    s_schema, s_table = _ref(body.source)
    t_schema, t_table = _ref(body.target)
    with src.connection() as sconn:
        cols, _ = dictionary.read_columns(sconn, s_schema, s_table)
    with tgt.connection() as tconn:
        obj = dictionary.table_object(tconn, t_schema, t_table)
        if obj is None:
            raise AppError(ErrorCode.NOT_FOUND, "Hedef tablo bulunamadı ya da erişim yetkiniz yok.")
        tinfo = target_info(tconn, t_schema, t_table)
        triggers = dictionary.enabled_triggers(tconn, t_schema, t_table)
    try:
        plan_or_error = compile_plan(body.rules, cols, mode="append",
                                     target_cols={c.name: c for c in tinfo[0]}, key=hmac_key(settings))
    except AppError as exc:
        plan_or_error = exc
    return compat.compat_report(cols, tinfo, plan_or_error, obj, triggers, rules=body.rules)


@router.post("", status_code=202)
def start(body: StartBody, request: Request, user: CurrentUser):
    session = require_session(request)
    req = body.model_dump(by_alias=True)
    job_id = get_service(request.app).start(user, session, req)
    return {"job_id": job_id}


@router.get("")
def list_jobs(request: Request, user: CurrentUser, mine: bool = True):
    if not mine and not user.is_admin:
        raise AppError(ErrorCode.AUTH_FORBIDDEN, "Tüm işleri yalnız yönetici görebilir.")
    return {"items": get_repo(request.app).list_jobs(user.username if mine else None, limit=200)}


@router.get("/{job_id}")
def get_job(job_id: str, request: Request, user: CurrentUser):
    return _owned_job(request, user, job_id)


@router.post("/{job_id}/cancel", status_code=202)
def cancel(job_id: str, request: Request, user: CurrentUser):
    _owned_job(request, user, job_id, with_columns=False)
    get_service(request.app).cancel(job_id)
    repo = get_repo(request.app)
    try:
        repo.audit(user.username, "cancel", job_id)
    except Exception:
        log.warning("Denetim kaydı yazılamadı: %s", job_id, exc_info=True)
    return JSONResponse(repo.get_job(job_id), status_code=202)


@router.get("/{job_id}/events")
async def events(job_id: str, request: Request, user: CurrentUser):
    await run_in_threadpool(_owned_job, request, user, job_id, with_columns=False)
    repo = get_repo(request.app)
    broker = get_service(request.app).broker(job_id)

    def load() -> dict:
        return repo.get_job(job_id, with_columns=False)

    return StreamingResponse(
        sse_stream(request, broker, load),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
