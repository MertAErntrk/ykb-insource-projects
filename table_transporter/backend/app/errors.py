"""Hata kodları, HTTP durum + Türkçe kullanıcı mesajı tablosu ve ORA eşlemesi.

Mesajlara şifre / bağlantı dizesi / ham sürücü metni asla konmaz.
"""

from enum import Enum


class ErrorCode(str, Enum):
    AUTH_INVALID = "AUTH_INVALID"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    AUTH_THROTTLED = "AUTH_THROTTLED"
    LDAP_UNAVAILABLE = "LDAP_UNAVAILABLE"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    CSRF_INVALID = "CSRF_INVALID"
    VALIDATION = "VALIDATION"
    DB_CONNECT_FAILED = "DB_CONNECT_FAILED"
    DB_DISCONNECTED = "DB_DISCONNECTED"
    DB_PERMISSION = "DB_PERMISSION"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    TABLE_EXISTS = "TABLE_EXISTS"
    TARGET_LOCKED = "TARGET_LOCKED"
    TARGET_DENIED = "TARGET_DENIED"
    INCOMPATIBLE = "INCOMPATIBLE"
    RULE_INVALID = "RULE_INVALID"
    TRIGGER_PRESENT = "TRIGGER_PRESENT"
    JOB_RUNNING = "JOB_RUNNING"
    NOT_FOUND = "NOT_FOUND"
    ORA_ERROR = "ORA_ERROR"
    INTERNAL = "INTERNAL"


# kod -> (HTTP durum, varsayılan Türkçe mesaj)
ERROR_TABLE: dict[ErrorCode, tuple[int, str]] = {
    ErrorCode.AUTH_INVALID: (401, "Kullanıcı adı veya şifre hatalı."),
    ErrorCode.AUTH_FORBIDDEN: (403, "Bu uygulamayı kullanma yetkiniz yok."),
    ErrorCode.AUTH_THROTTLED: (429, "Çok fazla başarısız deneme. Lütfen birkaç dakika sonra tekrar deneyin."),
    ErrorCode.LDAP_UNAVAILABLE: (503, "Kimlik sunucusuna (LDAP) şu an ulaşılamıyor. Lütfen daha sonra tekrar deneyin."),
    ErrorCode.SESSION_EXPIRED: (401, "Oturumunuz sona erdi. Lütfen yeniden giriş yapın."),
    ErrorCode.CSRF_INVALID: (403, "İstek doğrulanamadı. Sayfayı yenileyip tekrar deneyin."),
    ErrorCode.VALIDATION: (422, "İstek geçersiz."),
    ErrorCode.DB_CONNECT_FAILED: (400, "Veritabanına bağlanılamadı."),
    ErrorCode.DB_DISCONNECTED: (409, "Veritabanı bağlantısı koptu. Bağlantıyı yeniden kurun."),
    ErrorCode.DB_PERMISSION: (403, "Bu işlem için veritabanı yetkiniz yok."),
    ErrorCode.LIMIT_EXCEEDED: (422, "Seçiminiz satır sınırını aşıyor. Lütfen filtreyi daraltın."),
    ErrorCode.TABLE_EXISTS: (409, "Hedefte bu adla bir tablo zaten var."),
    ErrorCode.TARGET_LOCKED: (409, "Bu hedef tablo için başka bir taşıma sürüyor."),
    ErrorCode.TARGET_DENIED: (403, "Bu veritabanı hedef olarak kullanılamaz."),
    ErrorCode.INCOMPATIBLE: (422, "Kaynak ve hedef tablo uyumsuz."),
    ErrorCode.RULE_INVALID: (422, "Anonimleştirme kuralı geçersiz."),
    ErrorCode.TRIGGER_PRESENT: (409, "Hedef tabloda etkin trigger var; ekleme yapılamaz."),
    ErrorCode.JOB_RUNNING: (409, "Çalışan bir işiniz var."),
    ErrorCode.NOT_FOUND: (404, "Kayıt bulunamadı."),
    ErrorCode.ORA_ERROR: (400, "Veritabanı hatası."),
    ErrorCode.INTERNAL: (500, "Beklenmeyen bir hata oluştu."),
}


class AppError(Exception):
    def __init__(self, code: ErrorCode, detail: str | None = None, status: int | None = None):
        default_status, default_msg = ERROR_TABLE[code]
        self.code = code
        self.status = status if status is not None else default_status
        self.detail = detail or default_msg
        super().__init__(f"{code.value}: {self.detail}")


_CONNECT_FAILED = {
    12154: "Bağlantı adı (alias) çözümlenemedi.",
    12514: "Dinleyici istenen servisi tanımıyor.",
    12541: "Dinleyiciye ulaşılamadı.",
    12170: "Bağlantı zaman aşımına uğradı.",
}
_DISCONNECTED = {3113, 3114, 3135}
_PERMISSION = {
    1031: "Bu işlem için yetkiniz yok.",
    942: "Tablo veya görünüm yok ya da erişim yetkiniz yok.",
}
_ORA_DETAIL = {
    1: "Benzersiz kısıt ihlali: yinelenen değer yazılamaz.",
    1400: "NOT NULL kolona boş (NULL) değer yazılamaz.",
    12899: "Bir değer hedef kolonun uzunluğuna sığmıyor.",
    30036: "UNDO alanı yetersiz; daha dar bir filtre ile deneyin.",
    1555: "Okuma çok uzun sürdü (snapshot too old); daha dar bir filtre ile deneyin.",
}


def ora_error_to_app_error(code: int, message: str) -> AppError:
    """ORA kodunu AppError'a çevirir. `message` (ham sürücü metni) kasıtlı olarak kullanılmaz:
    bağlantı dizesi / kimlik bilgisi içerebilir."""
    if code == 1017:
        return AppError(ErrorCode.DB_CONNECT_FAILED, "Kullanıcı adı/şifre hatalı.")
    if code in _CONNECT_FAILED:
        return AppError(ErrorCode.DB_CONNECT_FAILED, _CONNECT_FAILED[code])
    if code in _DISCONNECTED:
        return AppError(ErrorCode.DB_DISCONNECTED)
    if code in _PERMISSION:
        return AppError(ErrorCode.DB_PERMISSION, _PERMISSION[code])
    if code == 955:
        return AppError(ErrorCode.TABLE_EXISTS)
    if code in _ORA_DETAIL:
        return AppError(ErrorCode.ORA_ERROR, f"ORA-{code:05d}: {_ORA_DETAIL[code]}")
    return AppError(ErrorCode.ORA_ERROR, f"ORA-{code:05d}")
