"""Oracle sürücü kipi (Thin/Thick) ve bağlantı havuzları.

Şifre yalnız create_pool çağrısına verilir; hiçbir nesnede tutulmaz. DbHandle dışarıya
yalnız SafeConnection verir (ham pool/connection açılmaz). Çalışan iş job_lease() ile
havuzu tutar: handle kapatılsa (logout/disconnect) bile lease bitene kadar havuz kapanmaz.
"""

import re
import threading
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

import oracledb
from pydantic import SecretStr

from app.config import Settings, project_path
from app.errors import AppError, ErrorCode, ora_error_to_app_error
from app.oracle import tns
from app.oracle.safe_connection import SafeConnection, _wrap

_USER_RE = re.compile(r"[A-Za-z0-9_$#]{1,128}")
_MIN_VERSION = (12, 1)

_driver: object | None = None
_init_lock = threading.Lock()
_thick_initialized = False


def get_driver():
    """Testler set_driver ile sahte sürücü verir; varsayılan oracledb modülü."""
    return _driver if _driver is not None else oracledb


def set_driver(obj: object | None) -> None:
    global _driver
    _driver = obj


def init_driver(settings: Settings) -> Literal["thin", "thick"]:
    """tt_oracle_thick_lib_dir varsa Thick kip bir kez açılır (süreç boyunca geri dönülmez)."""
    global _thick_initialized
    if not settings.tt_oracle_thick_lib_dir:
        return "thick" if _thick_initialized else "thin"
    with _init_lock:
        if not _thick_initialized:
            try:
                get_driver().init_oracle_client(lib_dir=str(project_path(settings.tt_oracle_thick_lib_dir)))
            except oracledb.Error as exc:
                raise AppError(ErrorCode.INTERNAL, "Oracle istemci kitaplığı (Thick) yüklenemedi.") from exc
            _thick_initialized = True
    return "thick"


def _driver_mode(driver) -> Literal["thin", "thick"]:
    return "thin" if driver.is_thin_mode() else "thick"


def _parse_version(text: object) -> tuple[int, ...]:
    parts = re.findall(r"[0-9]+", str(text or ""))
    return tuple(int(p) for p in parts[:2]) if parts else (0,)


def _connect_error(exc: oracledb.Error) -> AppError:
    info = exc.args[0] if exc.args else None
    full_code = str(getattr(info, "full_code", "") or "")
    if full_code.startswith("ORA-"):
        mapped = ora_error_to_app_error(int(getattr(info, "code", 0) or 0), "")
        if mapped.code is ErrorCode.DB_CONNECT_FAILED:
            return mapped
    suffix = f" ({full_code})" if full_code else ""
    return AppError(ErrorCode.DB_CONNECT_FAILED, f"Veritabanına bağlanılamadı{suffix}.")


class _Lease:
    __slots__ = ("_handle", "_active")

    def __init__(self, handle: "DbHandle") -> None:
        self._handle = handle
        self._active = True

    def connection(self):
        """İşin bağlantısı; handle kapatılmış olsa da lease süresince çalışır."""
        if not self._active:
            raise AppError(ErrorCode.DB_DISCONNECTED)
        return self._handle._connection(check_closed=False)


class DbHandle:
    __slots__ = (
        "role", "alias", "user", "db_version", "mode", "direct_path", "max_ident_len",
        "_pool", "_lock", "_leases", "_closed", "_pool_closed",
    )

    def __init__(self, *, role: str, alias: str, user: str, db_version: str, mode: str,
                 direct_path: bool, max_ident_len: int, pool: object) -> None:
        self.role = role
        self.alias = alias
        self.user = user
        self.db_version = db_version
        self.mode = mode
        self.direct_path = direct_path
        self.max_ident_len = max_ident_len
        self._pool = pool
        self._lock = threading.Lock()
        self._leases = 0
        self._closed = False
        self._pool_closed = False

    def __repr__(self) -> str:
        return f"<DbHandle {self.role} {self.alias} {self.user}>"

    @property
    def closed(self) -> bool:
        return self._closed

    def info(self) -> dict:
        return {
            "role": self.role,
            "alias": self.alias,
            "db_version": self.db_version,
            "user": self.user,
            "mode": self.mode,
            "direct_path": self.direct_path,
            "max_ident_len": self.max_ident_len,
        }

    def connection(self):
        """Bağlam yöneticisi → SafeConnection. Hata → rollback, sonra bağlantı havuza döner."""
        return self._connection(check_closed=True)

    @contextmanager
    def _connection(self, *, check_closed: bool) -> Iterator[SafeConnection]:
        with self._lock:
            if self._pool_closed or (check_closed and self._closed):
                raise AppError(ErrorCode.DB_DISCONNECTED)
        try:
            raw = self._pool.acquire()
        except oracledb.Error as exc:
            raise AppError(ErrorCode.DB_DISCONNECTED) from exc
        safe = _wrap(raw)
        try:
            yield safe
        except BaseException:
            try:
                raw.rollback()
            except oracledb.Error:
                pass
            raise
        finally:
            safe._detach()
            try:
                self._pool.release(raw)
            except oracledb.Error:
                pass

    @contextmanager
    def job_lease(self) -> Iterator[_Lease]:
        with self._lock:
            if self._closed or self._pool_closed:
                raise AppError(ErrorCode.DB_DISCONNECTED)
            self._leases += 1
        lease = _Lease(self)
        try:
            yield lease
        finally:
            lease._active = False
            with self._lock:
                self._leases -= 1
                close_now = self._closed and self._leases == 0
            if close_now:
                self._close_pool()

    def close(self) -> None:
        """İdempotent. Aktif lease varsa havuz, son lease bitince kapanır."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            close_now = self._leases == 0
        if close_now:
            self._close_pool()

    def _close_pool(self) -> None:
        with self._lock:
            if self._pool_closed:
                return
            self._pool_closed = True
        try:
            self._pool.close(force=True)
        except oracledb.Error:
            pass


def _open(settings: Settings, *, role: str, alias: str, dsn: str, user: object, password: object,
          driver) -> DbHandle:
    if isinstance(password, SecretStr):
        password = password.get_secret_value()
    if not isinstance(user, str) or not _USER_RE.fullmatch(user):
        raise AppError(ErrorCode.VALIDATION, "Geçersiz veritabanı kullanıcı adı.")
    if not isinstance(password, str) or not password:
        raise AppError(ErrorCode.VALIDATION, "Şifre boş olamaz.")
    driver = driver or get_driver()
    try:
        pool = driver.create_pool(
            user=user,
            password=password,
            dsn=dsn,
            min=1,
            max=settings.tt_max_concurrent_jobs + 2,
            increment=1,
            tcp_connect_timeout=10,
        )
    except oracledb.Error as exc:
        raise _connect_error(exc) from None
    del password
    try:
        raw = pool.acquire()
        try:
            db_version = str(raw.version)
            direct_path = hasattr(raw, "direct_path_load")
        finally:
            pool.release(raw)
    except oracledb.Error as exc:
        _safe_close(pool)
        raise _connect_error(exc) from None
    version = _parse_version(db_version)
    if version < _MIN_VERSION:
        _safe_close(pool)
        raise AppError(ErrorCode.DB_CONNECT_FAILED, f"Desteklenmeyen Oracle sürümü: {db_version} (en az 12.1).")
    mode = _driver_mode(driver)
    return DbHandle(
        role=role,
        alias=alias,
        user=user,
        db_version=db_version,
        mode=mode,
        direct_path=mode == "thin" and direct_path,
        max_ident_len=30 if version[:2] == (12, 1) else 128,
        pool=pool,
    )


def _safe_close(pool: object) -> None:
    try:
        pool.close(force=True)
    except oracledb.Error:
        pass


def open_db(settings: Settings, *, role: Literal["source", "target"], alias: str, user: str,
            password: str, driver=None) -> DbHandle:
    if role not in ("source", "target"):
        raise AppError(ErrorCode.VALIDATION, "Geçersiz bağlantı rolü.")
    if not isinstance(user, str) or not _USER_RE.fullmatch(user):
        raise AppError(ErrorCode.VALIDATION, "Geçersiz veritabanı kullanıcı adı.")
    if not password or (isinstance(password, SecretStr) and not password.get_secret_value()):
        raise AppError(ErrorCode.VALIDATION, "Şifre boş olamaz.")
    entry = tns.get_entry(settings, alias)
    if role == "target" and not tns.is_target_allowed(settings, entry.alias):
        raise AppError(ErrorCode.TARGET_DENIED)
    return _open(settings, role=role, alias=entry.alias, dsn=entry.descriptor, user=user,
                 password=password, driver=driver)


def open_service_db(settings: Settings, alias: str, driver=None) -> DbHandle:
    """Temizlik denetimi için servis hesabı: [alias.<ALIAS>], yoksa [default]."""
    entry = tns.get_entry(settings, alias)
    missing = AppError(ErrorCode.DB_CONNECT_FAILED, f"Servis hesabı tanımlı değil: {entry.alias}")
    try:
        with project_path(settings.tt_service_accounts_file).open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise missing from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AppError(ErrorCode.DB_CONNECT_FAILED, "Servis hesabı dosyası okunamadı.") from exc
    aliases = data.get("alias") if isinstance(data.get("alias"), dict) else {}
    account = aliases.get(entry.alias) or data.get("default")
    if not isinstance(account, dict):
        raise missing
    try:
        return _open(settings, role="service", alias=entry.alias, dsn=entry.descriptor,
                     user=account.get("user"), password=account.get("password"), driver=driver)
    except AppError as exc:
        if exc.code is ErrorCode.VALIDATION:
            raise AppError(ErrorCode.DB_CONNECT_FAILED, f"Servis hesabı geçersiz: {entry.alias}") from None
        raise
