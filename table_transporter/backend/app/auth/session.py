"""Sunucu oturumu (süreç içi), CSRF ve require_user.

Sözleşme (değişmez): User, Session ve require_user(request) -> User imzaları.
`Session.resources`: P4 "source" / "target" pool tutucusunu buraya koyar (Oracle şifresi saklanmaz).
`resources["active_jobs"]` boş olmayan bir küme iken oturumun süresi dolmaz.
"""

import hmac
import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request

from app.errors import AppError, ErrorCode

logger = logging.getLogger("app.auth")

COOKIE_NAME = "tt_session"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass
class User:
    username: str
    display_name: str
    groups: list[str]
    is_admin: bool


@dataclass
class Session:
    id: str
    user: User
    csrf_token: str
    created_at: datetime
    last_seen: datetime
    resources: dict[str, Any] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _close_resources(session: Session) -> None:
    for name, res in list(session.resources.items()):
        close = getattr(res, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - kapanış hatası diğerlerini engellemesin
                logger.warning("oturum kaynağı kapatılamadı: %s (%s)", name, type(exc).__name__)


class SessionStore:
    def __init__(self, ttl_min: int, clock: Callable[[], datetime] = _utcnow):
        self.ttl = timedelta(minutes=ttl_min)
        self.clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    def _expired(self, s: Session, now: datetime) -> bool:
        if s.resources.get("active_jobs"):
            return False
        return now - s.last_seen > self.ttl

    def _pop_expired(self, now: datetime) -> list[Session]:
        # Kilit altında çağrılır.
        dead = [s for s in self._sessions.values() if self._expired(s, now)]
        for s in dead:
            del self._sessions[s.id]
        return dead

    def create(self, user: User) -> Session:
        now = self.clock()
        s = Session(
            id=secrets.token_urlsafe(32),
            user=user,
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            last_seen=now,
        )
        with self._lock:
            dead = self._pop_expired(now)
            self._sessions[s.id] = s
        for d in dead:
            _close_resources(d)
        return s

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        dead: Session | None = None
        with self._lock:
            s = self._sessions.get(session_id)
            if s is not None and self._expired(s, self.clock()):
                dead = self._sessions.pop(session_id)
                s = None
        if dead is not None:
            _close_resources(dead)
        return s

    def touch(self, session: Session) -> None:
        with self._lock:
            session.last_seen = self.clock()

    def delete(self, session_id: str) -> None:
        with self._lock:
            s = self._sessions.pop(session_id, None)
        if s is not None:
            _close_resources(s)

    def purge_expired(self) -> int:
        with self._lock:
            dead = self._pop_expired(self.clock())
        for d in dead:
            _close_resources(d)
        return len(dead)


_store_lock = threading.Lock()


def get_store(app: Any) -> SessionStore:
    store = getattr(app.state, "session_store", None)
    if store is None:
        with _store_lock:
            store = getattr(app.state, "session_store", None)
            if store is None:
                settings = getattr(app.state, "settings", None)
                ttl = settings.tt_session_ttl_min if settings is not None else 30
                store = SessionStore(ttl)
                app.state.session_store = store
    return store


def require_session(request: Request) -> Session:
    store = get_store(request.app)
    s = store.get(request.cookies.get(COOKIE_NAME))
    if s is None:
        raise AppError(ErrorCode.SESSION_EXPIRED)
    if request.method.upper() in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent.encode("utf-8"), s.csrf_token.encode("utf-8")):
            raise AppError(ErrorCode.CSRF_INVALID)
    store.touch(s)
    request.state.session = s
    return s


def require_user(request: Request) -> User:
    return require_session(request).user


def require_admin(request: Request) -> User:
    user = require_user(request)
    if not user.is_admin:
        raise AppError(ErrorCode.AUTH_FORBIDDEN)
    return user
