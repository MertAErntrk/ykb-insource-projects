class ConfigError(ValueError):
    """Eksik ya da geçersiz yapılandırma."""

class IntegrationError(RuntimeError):
    """Güvenli mesajla dış entegrasyon hatası."""

class AnalysisCancelled(Exception):
    """Cooperative user cancellation; never a row or integration failure."""

class OracleOperationError(IntegrationError):
    """Only a controlled phase, error code and guidance; never raw driver text."""

class CatalogScanError(IntegrationError):
    """No usable catalog was read; analysis must not pretend to process rows."""

    def __init__(self, message, diagnostics, code="CATALOG_UNAVAILABLE"):
        super().__init__(message)
        self.diagnostics = diagnostics
        self.code = code

class StaleIndexError(IntegrationError):
    """Katalog ve arama indeksi sürümleri farklı."""

class InputError(ValueError):
    """Geçersiz kullanıcı dosyası."""

class ReportWriteError(IntegrationError):
    """Rapor yazılamadı; kind: locked | permission | disk_full | invalid | other. Mesaj yol/sır içermez."""

    MESSAGES = {
        "locked": "Rapor dosyası başka bir programda açık. Excel'de kapatıp yeniden deneyin.",
        "permission": "Rapor klasörüne yazma izni yok. Başka bir klasör seçin.",
        "disk_full": "Diskte yeterli boş alan yok. Yer açıp yeniden deneyin.",
        "invalid": "Oluşturulan rapor doğrulanamadı; mevcut dosya değiştirilmedi.",
        "other": "Rapor yazılamadı; mevcut dosya değiştirilmedi.",
    }

    def __init__(self, kind="other"):
        kind = kind if kind in self.MESSAGES else "other"
        super().__init__(self.MESSAGES[kind])
        self.kind = kind
