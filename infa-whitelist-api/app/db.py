"""Oracle erişimi: tek sorgu, bind değişkenli.

Havuz ilk çağrıda açılır; açılamazsa hata döner ve sonraki çağrı yeniden dener (uygulama veritabanı
kapalıyken de ayağa kalkar). Şifre yalnız create_pool çağrısına verilir.
"""

import logging
import threading
from decimal import Decimal
from time import monotonic

import oracledb

from app.config import Settings

log = logging.getLogger("iwc.db")

# Fonksiyon adı sabittir; istekten gelen değerler yalnız bind olarak gider, SQL metnine girmez.
CHECK_SQL = "SELECT EDWPARAM.FN_CHECK_INFA_WHITELIST(:p_folder, :p_workflow) FROM DUAL"
CONNECT_TIMEOUT_SEC = 5  # yalnız Thin kipte uygulanır; Thick kipte DSN ya da sqlnet.ora belirler
ACQUIRE_WAIT_MS = 5000
POOL_RETRY_SEC = 5  # havuz açılamadıysa bu süre yeniden denenmez; çağrılar beklemeden hata alır
LOCK_WAIT_SEC = 5  # başka bir çağrı havuzu açarken en çok bu kadar beklenir


class CheckUnavailable(Exception):
    """Veritabanına ulaşılamadı ya da çağrı zaman aşımına uğradı."""


class UnexpectedResult(Exception):
    """Fonksiyon 0/1 dışında bir değer döndürdü."""


def init_driver(settings: Settings, driver=oracledb) -> str:
    """Ayar istiyorsa Thick kipi açar (süreçte bir kez, ilk bağlantıdan önce). Dönen: 'thin' | 'thick'."""
    if settings.oracle_thick and driver.is_thin_mode():
        driver.init_oracle_client(lib_dir=settings.oracle_thick_lib_dir)
    if driver.is_thin_mode():
        return "thin"
    # Thick kipte DSN istemciye olduğu gibi gider; tcp_connect_timeout parametresi uygulanmaz.
    if "CONNECT_TIMEOUT" not in settings.db_dsn.upper():
        log.warning("Thick kipte bağlantı zaman aşımı koddan uygulanmaz: DSN'e TRANSPORT_CONNECT_TIMEOUT ekleyin "
                    "ya da sqlnet.ora'da SQLNET.OUTBOUND_CONNECT_TIMEOUT tanımlayın (README).")
    return "thick"


def to_flag(value: object) -> int:
    """Fonksiyon çıktısını 0/1'e çevirir. NULL ve başka her değer hatadır; sessizce 0 sayılmaz."""
    if isinstance(value, str):
        if value.strip() in ("0", "1"):
            return int(value)
    elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) and value in (0, 1):
        return int(value)
    raise UnexpectedResult(f"{type(value).__name__}: {value!r:.100}")


class WhitelistDb:
    def __init__(self, settings: Settings, driver=oracledb) -> None:
        self._settings = settings
        self._driver = driver
        self._pool = None
        self._retry_at = 0.0
        self._lock = threading.Lock()

    def _get_pool(self):
        # Thick kipte create_pool veritabanına bağlanana kadar bekler. Kilit süreli alınır ve başarısız
        # deneme kısa süre hatırlanır: veritabanı kapalıyken çağrılar sıraya girip birikmez.
        if not self._lock.acquire(timeout=LOCK_WAIT_SEC):
            raise CheckUnavailable("havuz açılışı sürüyor")
        try:
            if self._pool is None:
                if monotonic() < self._retry_at:
                    raise CheckUnavailable("havuz açılamadı; yeniden deneme bekleniyor")
                s = self._settings
                try:
                    self._pool = self._driver.create_pool(
                        user=s.db_user,
                        password=s.db_password.get_secret_value(),
                        dsn=s.db_dsn,
                        min=1,
                        max=s.db_pool_max,
                        increment=1,
                        getmode=oracledb.POOL_GETMODE_TIMEDWAIT,
                        wait_timeout=ACQUIRE_WAIT_MS,
                        tcp_connect_timeout=CONNECT_TIMEOUT_SEC,
                    )
                except oracledb.Error:
                    self._retry_at = monotonic() + POOL_RETRY_SEC
                    raise
            return self._pool
        finally:
            self._lock.release()

    def check(self, folder: str, workflow: str) -> int:
        """1 = whitelist dışı (uyarı), 0 = devam. Hata durumunda istisna fırlatır; asla 0 dönmez."""
        try:
            with self._get_pool().acquire() as conn:
                conn.call_timeout = self._settings.db_call_timeout_ms
                with conn.cursor() as cur:
                    cur.execute(CHECK_SQL, {"p_folder": folder, "p_workflow": workflow})
                    row = cur.fetchone()
        except oracledb.Error as exc:
            log.error("veritabanı hatası: %s", exc)
            raise CheckUnavailable from exc
        try:
            return to_flag(row[0] if row else None)
        except UnexpectedResult as exc:
            log.error("fonksiyon beklenmeyen değer döndürdü: %s", exc)
            raise

    def close(self) -> None:
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            try:
                pool.close(force=True)
            except oracledb.Error:
                pass
