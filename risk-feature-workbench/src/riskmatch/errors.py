class ConfigError(ValueError):
    """Eksik ya da geçersiz yapılandırma."""

class IntegrationError(RuntimeError):
    """Güvenli mesajla dış entegrasyon hatası."""

class StaleIndexError(IntegrationError):
    """Katalog ve arama indeksi sürümleri farklı."""

class InputError(ValueError):
    """Geçersiz kullanıcı dosyası."""
