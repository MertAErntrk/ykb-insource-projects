"""Informatica whitelist kontrol API'si.

GET /api/v1/infa-whitelist/check?folder=<FOLDER>&workflow=<WF>      başlık: X-API-Key
  200 {"folder": ..., "workflow": ..., "is_unauthorized": 0 | 1}     1 = uyarı, 0 = devam
  400 parametre eksik/geçersiz              401 anahtar yok/yanlış
  502 fonksiyon beklenmeyen değer döndürdü  503 veritabanına ulaşılamadı ya da zaman aşımı
Hata hiçbir zaman 0 olarak dönmez: çağıran 200 dışındaki her yanıtı "kontrol yapılamadı" saymalıdır.
"""

import hmac
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.db import CheckUnavailable, UnexpectedResult, WhitelistDb, init_driver

log = logging.getLogger("iwc.api")
MAX_NAME_LEN = 255


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


def match_key(presented: str | None, keys: list[SecretStr]) -> int | None:
    """Eşleşen anahtarın sıra numarası (1'den başlar); yoksa None. Karşılaştırma sabit zamanlıdır."""
    if not presented:
        return None
    given = presented.encode("utf-8")
    found = None
    for no, key in enumerate(keys, 1):
        if hmac.compare_digest(given, key.get_secret_value().encode("utf-8")):
            found = no
    return found


def clean_name(name: str, value: str | None) -> str:
    """Baş/son boşluğu kırpar. Boş değer fonksiyona gitmez: fonksiyon boşa 0 döner, bu 'devam' demektir."""
    text = (value or "").strip()
    if not text:
        raise ApiError(400, "invalid_parameter", f"'{name}' parametresi zorunlu.")
    if len(text) > MAX_NAME_LEN or not text.isprintable():
        raise ApiError(400, "invalid_parameter", f"'{name}' parametresi geçersiz.")
    return text


def create_app(settings: Settings | None = None, db: WhitelistDb | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    injected = db is not None
    db = db or WhitelistDb(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not injected:
            log.info("Oracle sürücü kipi: %s", init_driver(settings))
        yield
        db.close()

    # Belge uçları kapalı: kimlik doğrulamasız yüzey yalnız /api/health.
    app = FastAPI(title="Infa Whitelist API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "message": exc.message})

    def require_key(request: Request, x_api_key: str | None = Header(default=None)) -> int:
        key_no = match_key(x_api_key, settings.api_keys)
        if key_no is None:
            log.warning("yetkisiz çağrı ip=%s", request.client.host if request.client else "-")
            raise ApiError(401, "unauthorized", "API anahtarı eksik ya da geçersiz.")
        return key_no

    # async: iş parçacığı havuzunu kullanmaz; veritabanı çağrıları beklerken de probe yanıt alır.
    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/v1/infa-whitelist/check")
    def check(folder: str | None = None, workflow: str | None = None,
              key_no: int = Depends(require_key)) -> JSONResponse:
        folder = clean_name("folder", folder)
        workflow = clean_name("workflow", workflow)
        started = time.perf_counter()
        try:
            flag = db.check(folder, workflow)
        except CheckUnavailable:
            log.error("kontrol yapılamadı (503) folder=%r workflow=%r anahtar=%d", folder, workflow, key_no)
            raise ApiError(503, "check_unavailable", "Kontrol yapılamadı: veritabanına ulaşılamadı.") from None
        except UnexpectedResult:
            log.error("kontrol yapılamadı (502) folder=%r workflow=%r anahtar=%d", folder, workflow, key_no)
            raise ApiError(502, "unexpected_result", "Kontrol yapılamadı: fonksiyon beklenmeyen değer döndürdü.") from None
        log.info("kontrol folder=%r workflow=%r sonuc=%d sure_ms=%d anahtar=%d",
                 folder, workflow, flag, (time.perf_counter() - started) * 1000, key_no)
        return JSONResponse(
            {"folder": folder, "workflow": workflow, "is_unauthorized": flag},
            headers={"Cache-Control": "no-store"},  # ara katman eski bir 0'ı önbellekten vermesin
        )

    return app
