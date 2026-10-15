import os

# Sahte, ≥32 baytlık ve birbirinden farklı anahtarlar (app import edilmeden önce).
os.environ["TT_ANON_HMAC_KEY"] = "test-hmac-key-0123456789-abcdefghijkl"
os.environ["TT_SESSION_SECRET"] = "test-session-secret-0123456789-abcdefg"
os.environ["TT_COOKIE_SECURE"] = "false"
os.environ["TT_ENV"] = "local"
os.environ["TT_TARGET_DENY_ALIASES"] = "DEMOLIVE"
os.environ.pop("TT_FRONTEND_DIST", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def settings(tmp_path) -> Settings:
    get_settings.cache_clear()
    # Lifespan SQLite'ı proje köküne değil test dizinine açsın.
    return Settings(
        _env_file=None,
        tt_sqlite_path=str(tmp_path / "tt-test.db"),
        tt_service_accounts_file=str(tmp_path / "yok-service_accounts.toml"),
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # raise_server_exceptions=False: 500 yanıtının gövdesini test edebilmek için.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
