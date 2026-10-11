class ConfigError(ValueError):
    """Eksik ya da geçersiz yapılandırma."""

class IntegrationError(RuntimeError):
    """Güvenli mesajla dış entegrasyon hatası."""

class OracleOperationError(IntegrationError):
    """Only a controlled phase, error code and guidance; never raw driver text."""

class StaleIndexError(IntegrationError):
    """Katalog ve arama indeksi sürümleri farklı."""

class InputError(ValueError):
    """Geçersiz kullanıcı dosyası."""
