"""Uygulama ayarları (pydantic-settings). Tek kaynak: ortam değişkenleri / .env.

LDAP_* anahtarları öneksiz, diğerleri TT_* önekli (alan adı = anahtar adı, büyük/küçük harf duyarsız).
Başlangıçta doğrulama başarısızsa uygulama açılmaz.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MIN_SECRET_BYTES = 32
MAX_ROW_LIMIT = 5_000_000
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_path(value: str | Path) -> Path:
    """Göreli yolu proje köküne göre çözer; mutlak yol olduğu gibi döner."""
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _split_csv(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return [str(p).strip() for p in value if str(p).strip()]  # type: ignore[attr-defined]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,  # doğrulama hatasında gizli değer görünmesin
    )

    # --- LDAP (AD) ---
    ldap_server_uri: str = "ldaps://ldaps.kfs.local:636"
    ldap_base_dn: str = "OU=BTUsers,OU=GMUsers,OU=KFS,OU=Users,OU=KFS,DC=kfs,DC=local"
    ldap_domain: str = "KFS"
    ldap_tls_verify: bool = False
    ldap_ca_file: str | None = None
    ldap_timeout_sec: int = Field(10, ge=1, le=120)
    ldap_allowed_groups: Annotated[list[str], NoDecode] = Field(default_factory=list)
    ldap_admin_groups: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Uygulama ---
    tt_env: Literal["local", "openshift"] = "local"
    tt_frontend_dist: str | None = None
    tt_tns_admin: str = "config"
    tt_oracle_thick_lib_dir: str | None = None
    tt_session_secret: SecretStr
    tt_session_ttl_min: int = Field(30, ge=1, le=1440)
    tt_cookie_secure: bool = True
    tt_anon_hmac_key: SecretStr
    tt_row_limit: int = Field(MAX_ROW_LIMIT, ge=1, le=MAX_ROW_LIMIT)
    tt_retention_days: int = Field(7, ge=1, le=365)
    tt_check_interval_min: int = Field(60, ge=1)
    tt_count_timeout_sec: int = Field(60, ge=1)
    tt_batch_rows: int = Field(100_000, ge=1_000)
    tt_max_concurrent_jobs: int = Field(2, ge=1, le=32)
    # Zorunlu: hedef olamayacak (canlı) alias'lar. Ortama özgüdür, koda yazılmaz; boşsa uygulama açılmaz.
    tt_target_deny_aliases: Annotated[list[str], NoDecode] = Field(default_factory=list)
    tt_sqlite_path: str = "data/verikopru.db"
    tt_service_accounts_file: str = "config/service_accounts.toml"

    @field_validator("ldap_allowed_groups", "ldap_admin_groups", mode="before")
    @classmethod
    def _groups(cls, v: object) -> list[str]:
        return _split_csv(v)

    @field_validator("tt_target_deny_aliases", mode="before")
    @classmethod
    def _deny_aliases(cls, v: object) -> list[str]:
        return [a.upper() for a in _split_csv(v)]

    @field_validator("ldap_ca_file", "tt_frontend_dist", "tt_oracle_thick_lib_dir", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("tt_anon_hmac_key", "tt_session_secret")
    @classmethod
    def _min_bytes(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value().encode("utf-8")) < MIN_SECRET_BYTES:
            raise ValueError(f"en az {MIN_SECRET_BYTES} bayt olmalı")
        return v

    @model_validator(mode="after")
    def _keys_differ(self) -> "Settings":
        if self.tt_anon_hmac_key.get_secret_value() == self.tt_session_secret.get_secret_value():
            raise ValueError("TT_ANON_HMAC_KEY ve TT_SESSION_SECRET birbirinden farklı olmalı")
        if not self.tt_target_deny_aliases:
            raise ValueError("TT_TARGET_DENY_ALIASES boş olamaz: hedef olamayacak canlı alias'ları virgülle yazın")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
