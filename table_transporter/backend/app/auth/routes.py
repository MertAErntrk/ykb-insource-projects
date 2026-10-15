import inspect
import logging
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr
from starlette.concurrency import run_in_threadpool

from app.auth.ldap_auth import LoginThrottle, authenticate
from app.auth.session import COOKIE_NAME, Session, get_store, require_session
from app.deps import SettingsDep
from app.errors import AppError, ErrorCode

logger = logging.getLogger("app.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

CurrentSession = Annotated[Session, Depends(require_session)]


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: SecretStr


class LogoutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancel_running: bool = False


def _throttle(request: Request) -> LoginThrottle:
    t = getattr(request.app.state, "login_throttle", None)
    if t is None:
        t = LoginThrottle()
        request.app.state.login_throttle = t
    return t


def _session_body(s: Session) -> dict:
    return {"user": asdict(s.user), "csrf_token": s.csrf_token}


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response, settings: SettingsDep):
    ip = request.client.host if request.client else "unknown"
    keys = (f"user:{body.username[:64].lower()}", f"ip:{ip}")
    throttle = _throttle(request)
    if throttle.is_blocked(*keys):
        logger.info("login result=%s", ErrorCode.AUTH_THROTTLED.value)
        raise AppError(ErrorCode.AUTH_THROTTLED)

    factory = getattr(request.app.state, "ldap_client_factory", None)
    try:
        user = await run_in_threadpool(authenticate, body.username, body.password.get_secret_value(), settings, factory)
    except AppError as exc:
        if exc.code in (ErrorCode.AUTH_INVALID, ErrorCode.AUTH_FORBIDDEN):
            throttle.record_failure(*keys)
        raise
    throttle.reset(*keys)

    s = get_store(request.app).create(user)
    response.set_cookie(
        COOKIE_NAME,
        s.id,
        httponly=True,
        samesite="strict",
        secure=settings.tt_cookie_secure,
        path="/",
    )
    return _session_body(s)


@router.post("/logout", status_code=204)
async def logout(request: Request, session: CurrentSession, settings: SettingsDep, body: LogoutBody | None = None):
    jobs = session.resources.get("active_jobs")
    if jobs:
        if not (body and body.cancel_running):
            raise AppError(ErrorCode.JOB_RUNNING, "İşiniz sürüyor. Çıkış için çalışan işin iptal edilmesi gerekir.")
        cancel_job = getattr(request.app.state, "cancel_job", None)
        if callable(cancel_job):
            for job_id in list(jobs):
                result = cancel_job(job_id)
                if inspect.isawaitable(result):
                    await result
    get_store(request.app).delete(session.id)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/", secure=settings.tt_cookie_secure, httponly=True, samesite="strict")
    return response


@router.get("/me")
async def me(session: CurrentSession):
    return _session_body(session)
