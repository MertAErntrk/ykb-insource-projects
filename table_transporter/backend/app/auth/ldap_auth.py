"""LDAP (Active Directory) kimlik doğrulama + süreç içi başarısız giriş kısması.

Şifre hiçbir log / hata mesajına girmez; log yalnız kullanıcı adı + sonuç kodu.
"""

import logging
import re
import socket
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from ldap3 import NONE, SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException, LDAPPasswordIsMandatoryError
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn

from app.auth.session import User
from app.config import Settings
from app.errors import AppError, ErrorCode

logger = logging.getLogger("app.auth")

_USERNAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_ATTRS = ["displayName", "memberOf", "sAMAccountName"]

ClientFactory = Callable[[Settings], Any]


def default_client_factory(settings: Settings) -> Connection:
    """LDAPS bağlantısı (bağlanmamış). Kimlik bilgisi authenticate içinde atanır."""
    if settings.ldap_ca_file:
        tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=settings.ldap_ca_file, ssl_options=_tls_options())
    else:
        # LDAP_TLS_VERIFY=true ama CA dosyası yoksa sistem deposu ile doğrula.
        validate = ssl.CERT_REQUIRED if settings.ldap_tls_verify else ssl.CERT_NONE
        tls = Tls(validate=validate, ssl_options=_tls_options())
    server = Server(
        settings.ldap_server_uri,
        use_ssl=True,
        tls=tls,
        connect_timeout=settings.ldap_timeout_sec,
        get_info=NONE,
    )
    return Connection(
        server,
        authentication=SIMPLE,
        receive_timeout=settings.ldap_timeout_sec,
        raise_exceptions=False,
    )


def _tls_options() -> list[int]:
    # En az TLS 1.2.
    return [ssl.OP_NO_SSLv2, ssl.OP_NO_SSLv3, ssl.OP_NO_TLSv1, ssl.OP_NO_TLSv1_1]


def _values(attrs: dict[str, Any], name: str) -> list[str]:
    for key, value in attrs.items():
        if key.lower() == name.lower():
            if value is None:
                return []
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
            return [str(value)]
    return []


def _cn(dn: str) -> str | None:
    try:
        parts = parse_dn(dn)
    except Exception:  # noqa: BLE001 - bozuk DN: yalnız tam DN eşleşmesi
        return None
    if parts and parts[0][0].lower() == "cn":
        return parts[0][1]
    return None


def _in_groups(member_of: list[str], wanted: list[str]) -> bool:
    wanted_l = {w.lower() for w in wanted}
    for dn in member_of:
        if dn.lower() in wanted_l:
            return True
        cn = _cn(dn)
        if cn is not None and cn.lower() in wanted_l:
            return True
    return False


def authenticate(
    username: str,
    password: str,
    settings: Settings,
    client_factory: ClientFactory | None = None,
) -> User:
    """AD'ye NT-stil (DOMAIN\\kullanıcı) SIMPLE bind + Base DN altında arama + grup kontrolü."""
    if not isinstance(username, str) or not _USERNAME_RE.fullmatch(username):
        logger.info("login user=<geçersiz> result=%s", ErrorCode.AUTH_INVALID.value)
        raise AppError(ErrorCode.AUTH_INVALID)
    if not isinstance(password, str) or not password.strip():
        # AD kimliksiz (boş şifreli) bind'i başarılı sayar: bind denenmez.
        _fail(username, ErrorCode.AUTH_INVALID)

    conn = (client_factory or default_client_factory)(settings)
    try:
        conn.user = f"{settings.ldap_domain}\\{username}"
        conn.password = password
        try:
            bound = conn.bind()
        except LDAPPasswordIsMandatoryError:
            bound = False
        if not bound:
            _fail(username, ErrorCode.AUTH_INVALID)

        conn.search(
            settings.ldap_base_dn,
            f"(sAMAccountName={escape_filter_chars(username)})",
            search_scope=SUBTREE,
            attributes=_ATTRS,
        )
        entries = [r for r in (conn.response or []) if r.get("type") == "searchResEntry"]
        if len(entries) != 1:
            _fail(username, ErrorCode.AUTH_FORBIDDEN)

        attrs = entries[0].get("attributes") or {}
        member_of = _values(attrs, "memberOf")
        if settings.ldap_allowed_groups and not _in_groups(member_of, settings.ldap_allowed_groups):
            _fail(username, ErrorCode.AUTH_FORBIDDEN)
        is_admin = bool(settings.ldap_admin_groups) and _in_groups(member_of, settings.ldap_admin_groups)

        sam = (_values(attrs, "sAMAccountName") or [username])[0]
        display = (_values(attrs, "displayName") or [sam])[0]
        logger.info("login user=%s result=OK", username)
        return User(username=sam, display_name=display, groups=member_of, is_admin=is_admin)
    except AppError:
        raise
    except (LDAPException, socket.timeout, OSError) as exc:
        logger.warning("login user=%s result=%s (%s)", username, ErrorCode.LDAP_UNAVAILABLE.value, type(exc).__name__)
        raise AppError(ErrorCode.LDAP_UNAVAILABLE) from None
    finally:
        try:
            conn.unbind()
        except Exception:  # noqa: BLE001 - kapanış hatası sonucu değiştirmez
            pass


def _fail(username: str, code: ErrorCode) -> None:
    logger.info("login user=%s result=%s", username, code.value)
    raise AppError(code)


class LoginThrottle:
    """Aynı anahtar (kullanıcı adı / IP) için `window_sec` içinde `max_failures` başarısız giriş → kısma."""

    def __init__(self, max_failures: int = 5, window_sec: float = 300.0, clock: Callable[[], float] = time.monotonic):
        self.max_failures = max_failures
        self.window_sec = window_sec
        self.clock = clock
        self._lock = threading.Lock()
        self._fails: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float] | None:
        q = self._fails.get(key)
        if q is None:
            return None
        while q and now - q[0] >= self.window_sec:
            q.popleft()
        if not q:
            del self._fails[key]
            return None
        return q

    def is_blocked(self, *keys: str) -> bool:
        with self._lock:
            now = self.clock()
            return any((q := self._prune(k, now)) is not None and len(q) >= self.max_failures for k in keys)

    def record_failure(self, *keys: str) -> None:
        with self._lock:
            now = self.clock()
            if len(self._fails) > 10_000:  # bellek sınırı: eski kayıtları süpür
                for k in list(self._fails):
                    self._prune(k, now)
            for k in keys:
                self._fails.setdefault(k, deque()).append(now)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)
