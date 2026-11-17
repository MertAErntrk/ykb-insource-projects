"""Ayarlar: ortam değişkenleri ya da çalışma dizinindeki .env (IWC_ önekli).

Doğrulama başarısızsa uygulama açılmaz. Gizli değerler SecretStr'dir; hata iletisinde görünmez.
"""

import re
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# secrets.token_urlsafe(32) çıktısı bu biçime uyar. Virgül ayırıcı olduğu için anahtarda olamaz.
_KEY_RE = re.compile(r"[A-Za-z0-9_\-]{32,128}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="IWC_",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,  # doğrulama hatasında gizli değer görünmesin
    )

    db_user: str = Field(min_length=1)
    db_password: SecretStr = Field(min_length=1)
    # host:port/servis_adı ya da tam TNS tanımı.
    db_dsn: str = Field(min_length=1)
    # Çağıran anahtarları, virgülle. Yenilemede eski ve yeni anahtar bir süre birlikte geçerli olur.
    api_keys: Annotated[list[SecretStr], NoDecode]
    # Thick kip: veritabanı Native Network Encryption istiyorsa gerekir (Thin kipte DPY-3001 hatası).
    oracle_thick: bool = False
    oracle_thick_lib_dir: str | None = None
    db_call_timeout_ms: int = Field(5000, ge=100, le=60_000)
    db_pool_max: int = Field(4, ge=1, le=32)

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_keys(cls, v: object) -> object:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("api_keys")
    @classmethod
    def _check_keys(cls, v: list[SecretStr]) -> list[SecretStr]:
        if not v:
            raise ValueError("en az bir API anahtarı gerekli")
        for key in v:
            if not _KEY_RE.fullmatch(key.get_secret_value()):
                raise ValueError("her anahtar 32-128 karakter olmalı; yalnız harf, rakam, '-' ve '_'")
        return v

    @field_validator("oracle_thick_lib_dir", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
