class ConfigError(ValueError):
    """Eksik ya da geçersiz yapılandırma."""

class IntegrationError(RuntimeError):
    """Güvenli mesajla dış entegrasyon hatası."""

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
