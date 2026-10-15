from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request, Response

from app.cleanup import checker
from app.cleanup.drop_script import build_drop_script
from app.deps import CurrentUser, SettingsDep
from app.errors import AppError, ErrorCode

router = APIRouter(prefix="/api/cleanup", tags=["cleanup"])

_STATUSES = {"active", "expired_present", "deleted_verified", "unverifiable"}


def _repo(request: Request):
    repo = getattr(request.app.state, "repository", None)
    if repo is None:
        raise AppError(ErrorCode.INTERNAL)
    return repo


def _own_tracked(repo, user, job_id: str) -> dict | None:
    """İş yoksa ya da başkasınınsa NOT_FOUND; izlenmeyen işte None."""
    job = repo.get_job(job_id, with_columns=False)
    if job is None or (job["owner"] != user.username and not user.is_admin):
        raise AppError(ErrorCode.NOT_FOUND, "İş bulunamadı.")
    return repo.tracked_for_job(job_id)


@router.get("")
def list_cleanup(request: Request, user: CurrentUser, status: str | None = Query(None)):
    if status is not None and status not in _STATUSES:
        raise AppError(ErrorCode.VALIDATION, "Geçersiz durum filtresi.")
    owner = None if user.is_admin else user.username
    return {"items": _repo(request).list_tracked(owner=owner, status=status)}


@router.get("/drop-script")
def drop_script(request: Request, user: CurrentUser, job_ids: str = Query("")):
    repo = _repo(request)
    ids = list(dict.fromkeys(j.strip() for j in job_ids.split(",") if j.strip()))
    if not ids:
        raise AppError(ErrorCode.VALIDATION, "job_ids gerekli.")
    tracked = [t for t in (_own_tracked(repo, user, j) for j in ids) if t is not None]
    eligible = [t for t in tracked if t["table_status"] == "expired_present" and t["object_id"]]
    if not eligible:
        raise AppError(ErrorCode.NOT_FOUND, "DROP script için uygun tablo yok.")
    try:
        text = build_drop_script(eligible, generated_by=user.username, generated_at=datetime.now(timezone.utc))
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION, str(exc)) from None
    repo.audit(user.username, "drop_script", detail=f"{len(eligible)} tablo: " + ",".join(ids))
    return Response(
        text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="drop_script.sql"'},
    )


@router.post("/check/{job_id}")
def check_now(job_id: str, request: Request, user: CurrentUser, settings: SettingsDep):
    repo = _repo(request)
    tracked = _own_tracked(repo, user, job_id)
    if tracked is None:
        raise AppError(ErrorCode.NOT_FOUND, "Bu iş için izlenen tablo yok.")
    try:
        result = checker.check_table(settings, repo, tracked, strict=True)
    except AppError as exc:
        if exc.code in (ErrorCode.DB_CONNECT_FAILED, ErrorCode.NOT_FOUND):
            raise AppError(ErrorCode.DB_CONNECT_FAILED, exc.detail) from None
        raise
    repo.audit(user.username, "check", job_id=job_id, detail=result["table_status"])
    return result
