"""FastAPI uygulama fabrikası. Başlatma: `uvicorn app.main:create_app --factory` (tek worker)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.anon.routes import router as anon_router
from app.auth.routes import router as auth_router
from app.cleanup.checker import scheduler_loop, verify_visible
from app.cleanup.routes import router as cleanup_router
from app.config import Settings, get_settings
from app.errors import AppError, ErrorCode, ERROR_TABLE
from app.log.db import open_database
from app.log.repository import Repository
from app.oracle.pools import init_driver
from app.oracle.tns import load_tns
from app.oracle.routes import router as oracle_router
from app.transfer.routes import router as transfer_router
from app.transfer.service import get_service

logger = logging.getLogger("app")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    ),
}


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    headers.setdefault(k, v)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _error_response(code: ErrorCode, detail: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code.value, "detail": detail},
        headers=SECURITY_HEADERS,
    )


SCHEDULER_START_DELAY_SEC = 30


async def _delayed_scheduler(app: FastAPI) -> None:
    await asyncio.sleep(SCHEDULER_START_DELAY_SEC)
    await scheduler_loop(app)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info("Oracle sürücü kipi: %s", init_driver(settings))
    known = {a.upper() for a in load_tns(settings)}
    unknown = [a for a in settings.tt_target_deny_aliases if known and a not in known]
    if unknown:
        logger.warning("TT_TARGET_DENY_ALIASES tnsnames.ora'da olmayan ad içeriyor (yazım?): %s", ", ".join(unknown))

    # Testler kendi deposunu önceden verebilir; yoksa ayarlardaki SQLite açılır.
    owned_db = None
    repo = getattr(app.state, "repository", None)
    if repo is None:
        owned_db = open_database(settings)
        repo = app.state.repository = Repository(owned_db)
    recovered = repo.recover_on_startup()
    if recovered:
        logger.warning("Yeniden başlatmada yarım kalan %d iş işaretlendi: %s", len(recovered), ", ".join(recovered))

    if getattr(app.state, "after_create", None) is None:
        app.state.after_create = lambda tracked_id: verify_visible(settings, repo, tracked_id)
    get_service(app)  # app.state.cancel_job logout'tan önce kayıtlı olsun

    scheduler = asyncio.create_task(_delayed_scheduler(app))
    try:
        yield
    finally:
        scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler
        svc = getattr(app.state, "transfer_service", None)
        if svc is not None:
            # Çalışan işler iptal edilir; append geri alınır, create partial kalır.
            await asyncio.to_thread(svc.shutdown, True)
        if owned_db is not None:
            owned_db.close()


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        logger.warning("TT_FRONTEND_DIST'te index.html yok; statik sunum kapalı")
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise AppError(ErrorCode.NOT_FOUND)
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="VeriKöprü",
        lifespan=lifespan,
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.dependency_overrides[get_settings] = lambda: settings

    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return _error_response(exc.code, exc.detail, exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Yalnız alan yolları; input / ctx / msg döndürülmez (şifre sızmasın).
        paths = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
        return _error_response(ErrorCode.VALIDATION, ", ".join(paths) or "istek", 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return _error_response(ErrorCode.NOT_FOUND, ERROR_TABLE[ErrorCode.NOT_FOUND][1], 404)
        return _error_response(ErrorCode.VALIDATION, "Desteklenmeyen istek.", exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.error("İşlenmeyen hata: %s", type(exc).__name__, exc_info=exc)
        return _error_response(ErrorCode.INTERNAL, ERROR_TABLE[ErrorCode.INTERNAL][1], 500)

    for router in (auth_router, oracle_router, transfer_router, anon_router, cleanup_router):
        app.include_router(router)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    if settings.tt_frontend_dist:
        dist = Path(settings.tt_frontend_dist)
        if dist.is_dir():
            _mount_frontend(app, dist)
        else:
            logger.warning("TT_FRONTEND_DIST dizini yok; statik sunum kapalı")

    return app
