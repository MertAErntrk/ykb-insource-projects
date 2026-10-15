from pydantic import BaseModel, ConfigDict, SecretStr

SECRET = "GizliSifre123!"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: str
    password: SecretStr


def _add_test_routes(app):
    @app.post("/api/_t/echo")
    async def echo(body: _Body):
        return {"ok": True}

    @app.get("/api/_t/boom")
    async def boom():
        raise RuntimeError("ic-ayrinti")


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_unknown_path_is_json_404(client):
    r = client.get("/api/yok")
    assert r.status_code == 404
    assert r.json()["error"] == "NOT_FOUND"


def test_validation_does_not_leak_values(app, client):
    _add_test_routes(app)
    for payload in (
        {"user": "u", "password": SECRET, "fazla": 1},
        {"user": 5, "password": SECRET},
        {"user": "u", "password": {"x": SECRET}},
    ):
        r = client.post("/api/_t/echo", json=payload)
        assert r.status_code == 422
        body = r.json()
        assert body["error"] == "VALIDATION"
        assert set(body) == {"error", "detail"}
        assert SECRET not in r.text
        assert "input" not in r.text and "ctx" not in r.text
    assert "body.fazla" in client.post("/api/_t/echo", json={"user": "u", "password": "x", "fazla": 1}).json()["detail"]


def test_unhandled_error_is_500_without_traceback(app, client):
    _add_test_routes(app)
    r = client.get("/api/_t/boom")
    assert r.status_code == 500
    assert r.json()["error"] == "INTERNAL"
    assert "Traceback" not in r.text and "ic-ayrinti" not in r.text and "RuntimeError" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"


def test_require_user_stub_gives_session_expired(app, client):
    from app.deps import CurrentUser

    @app.get("/api/_t/me")
    async def me(user: CurrentUser):
        return {}

    r = client.get("/api/_t/me")
    assert r.status_code == 401
    assert r.json()["error"] == "SESSION_EXPIRED"


def test_spa_fallback(tmp_path, settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (tmp_path / "a.js").write_text("x", encoding="utf-8")
    s = settings.model_copy(update={"tt_frontend_dist": str(tmp_path)})
    with TestClient(create_app(s)) as c:
        assert "spa" in c.get("/connect").text
        assert c.get("/a.js").text == "x"
        assert "spa" in c.get("/..%2f..%2fetc/passwd").text
        r = c.get("/api/yok")
        assert r.status_code == 404 and r.json()["error"] == "NOT_FOUND"
        assert c.get("/api/health").json() == {"status": "ok"}
