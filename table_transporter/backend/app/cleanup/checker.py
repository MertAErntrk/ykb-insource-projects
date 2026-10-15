"""Süresi dolan tabloların varlık kontrolü (servis hesabı). Yalnız sözlük SELECT'i; yazma/DDL yok."""

import asyncio
import logging
from datetime import datetime, timezone

from app.config import Settings
from app.errors import AppError, ErrorCode
from app.log.repository import Repository
from app.oracle import pools, sql_templates as T

log = logging.getLogger(__name__)
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _created_text(value: object) -> str | None:
    """pipeline.track_table ile aynı biçim: YYYY-MM-DDTHH:MM:SS."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    return str(value)


def _lookup(settings: Settings, tracked: dict) -> dict | None:
    """Sözlükten nesne kimliği: önce DBA_OBJECTS, yetki yoksa ALL_OBJECTS."""
    handle = pools.open_service_db(settings, tracked["alias"])
    try:
        with handle.connection() as conn:
            try:
                stmt, binds = T.dba_object(tracked["schema"], tracked["table"])
                return conn.query_one(stmt, binds)
            except AppError as exc:
                if exc.code is not ErrorCode.DB_PERMISSION:
                    raise
            stmt, binds = T.dict_object(tracked["schema"], tracked["table"])
            return conn.query_one(stmt, binds)
    finally:
        handle.close()


def _matches(tracked: dict, row: dict | None) -> bool:
    if row is None or tracked.get("object_id") is None:
        return False
    try:
        same_id = int(row["OBJECT_ID"]) == int(tracked["object_id"])
    except (TypeError, ValueError):
        return False
    created = tracked.get("object_created")
    return same_id and (created is None or _created_text(row.get("CREATED")) == created)


def check_table(
    settings: Settings, repo: Repository, tracked: dict, *, now: datetime | None = None, strict: bool = False
) -> dict:
    """Tek tabloyu denetler, sonucu kaydeder, güncel TrackedTable döner.
    Bağlantı/sorgu hatasında durum korunur; strict=True ise hata kayıttan sonra yeniden fırlatılır."""
    now = now or datetime.now(timezone.utc)
    try:
        row = _lookup(settings, tracked)
    except AppError as exc:
        repo.record_check(tracked["id"], visible=False, object_id=None, object_created=None,
                          result_status=tracked["table_status"], error=exc.detail[:200])
        if strict:
            raise
        return repo.get_tracked(tracked["id"])
    except Exception:
        log.exception("Varlık kontrolü beklenmeyen hata (id=%s)", tracked.get("id"))
        repo.record_check(tracked["id"], visible=False, object_id=None, object_created=None,
                          result_status=tracked["table_status"], error="Beklenmeyen kontrol hatası.")
        if strict:
            raise AppError(ErrorCode.INTERNAL) from None
        return repo.get_tracked(tracked["id"])
    if _matches(tracked, row):
        repo.mark_verified_visible(tracked["id"])
        expires = datetime.strptime(tracked["expires_at"], _TS_FMT).replace(tzinfo=timezone.utc)
        status = "expired_present" if expires <= now else "active"
        repo.record_check(tracked["id"], visible=True, object_id=int(row["OBJECT_ID"]),
                          object_created=_created_text(row.get("CREATED")), result_status=status)
    else:
        # Yok ya da başkası yeniden yaratmış: görülmüş tablo silinmiş sayılır (repo zorlar).
        gone = row is None
        repo.record_check(
            tracked["id"], visible=not gone,
            object_id=None if gone else row.get("OBJECT_ID"),
            object_created=None if gone else _created_text(row.get("CREATED")),
            result_status="deleted_verified" if tracked.get("verified_visible_at") else "unverifiable",
        )
    return repo.get_tracked(tracked["id"])


def verify_visible(settings: Settings, repo: Repository, tracked_id: int) -> None:
    """İş bitince bir kez: tablo sözlükte eşleşiyorsa 'görüldü' işaretlenir. Hata yutulur."""
    try:
        tracked = repo.get_tracked(tracked_id)
        if tracked is not None and _matches(tracked, _lookup(settings, tracked)):
            repo.mark_verified_visible(tracked_id)
    except Exception as exc:  # noqa: BLE001 - arka plan; zamanlayıcı yeniden dener
        log.warning("Görünürlük doğrulaması başarısız (id=%s): %s", tracked_id, type(exc).__name__)


def run_due_checks(settings: Settings, repo: Repository, now: datetime | None = None) -> int:
    """Vadesi gelen ve henüz doğrulanmamış aktif kayıtları denetler; denetlenen sayısı döner."""
    now = now or datetime.now(timezone.utc)
    items = {t["id"]: t for t in repo.due_for_check(now)}
    for t in repo.list_tracked(status="active"):
        if t["verified_visible_at"] is None:
            items.setdefault(t["id"], t)
    done = 0
    for t in items.values():
        try:
            check_table(settings, repo, t, now=now)
            done += 1
        except Exception:  # noqa: BLE001 - her kayıt izole
            log.exception("Kontrol atlandı (id=%s)", t["id"])
    return done


async def scheduler_loop(app) -> None:
    """Aralıklı kontrol; iptal edilince (CancelledError) temiz çıkar."""
    settings: Settings = app.state.settings
    while True:
        repo = getattr(app.state, "repository", None)
        if repo is not None:
            try:
                await asyncio.to_thread(run_due_checks, settings, repo)
            except Exception:  # noqa: BLE001
                log.exception("Zamanlanmış temizlik kontrolü başarısız")
        await asyncio.sleep(settings.tt_check_interval_min * 60)
