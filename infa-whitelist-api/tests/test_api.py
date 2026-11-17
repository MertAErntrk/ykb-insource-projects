import pytest
from fastapi.testclient import TestClient

from app.db import CheckUnavailable, UnexpectedResult
from app.main import create_app
from conftest import KEY_1, KEY_2, make_settings

URL = "/api/v1/infa-whitelist/check"
PARAMS = {"folder": "ORNEK_FOLDER", "workflow": "wf_m_ORNEK"}


class FakeDb:
    def __init__(self, result=0):
        self.result = result
        self.calls = []
        self.closed = False

    def check(self, folder, workflow):
        self.calls.append((folder, workflow))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def close(self):
        self.closed = True


def client(db, **kw) -> TestClient:
    return TestClient(create_app(make_settings(), db), **kw)


def auth(key=KEY_1) -> dict:
    return {"X-API-Key": key}


def test_health_needs_no_key():
    r = client(FakeDb()).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.parametrize("flag", [0, 1])
def test_returns_flag(flag):
    db = FakeDb(flag)
    r = client(db).get(URL, params=PARAMS, headers=auth())
    assert r.status_code == 200
    assert r.json() == {**PARAMS, "is_unauthorized": flag}
    assert r.headers["cache-control"] == "no-store"
    assert db.calls == [(PARAMS["folder"], PARAMS["workflow"])]


def test_second_key_accepted():
    assert client(FakeDb(1)).get(URL, params=PARAMS, headers=auth(KEY_2)).status_code == 200


@pytest.mark.parametrize("headers", [
    {},
    {"X-API-Key": ""},
    {"X-API-Key": "yanlis-anahtar"},
    {"X-API-Key": KEY_1[:-1]},
    {"X-API-Key": KEY_1 + "x"},
    {"X-API-Key": f"{KEY_1},{KEY_2}"},
    {"Authorization": f"Bearer {KEY_1}"},
])
def test_missing_or_wrong_key_is_401(headers):
    db = FakeDb(0)
    r = client(db).get(URL, params=PARAMS, headers=headers)
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"
    assert "is_unauthorized" not in r.json()
    assert db.calls == []


def test_key_is_not_accepted_as_query_param():
    db = FakeDb(0)
    r = client(db).get(URL, params={**PARAMS, "x_api_key": KEY_1, "X-API-Key": KEY_1})
    assert r.status_code == 401
    assert db.calls == []


def test_auth_is_checked_before_params():
    assert client(FakeDb()).get(URL).status_code == 401


@pytest.mark.parametrize("params", [
    {},
    {"folder": "F"},
    {"workflow": "W"},
    {"folder": "", "workflow": "W"},
    {"folder": "F", "workflow": ""},
    {"folder": "   ", "workflow": "W"},
    {"folder": "F", "workflow": "\t "},
    {"folder": "F" * 256, "workflow": "W"},
    {"folder": "F", "workflow": "W" * 256},
    {"folder": "F\nG", "workflow": "W"},
    {"folder": "F", "workflow": "W\x00"},
])
def test_missing_blank_or_invalid_param_is_400(params):
    db = FakeDb(0)
    r = client(db).get(URL, params=params, headers=auth())
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_parameter"
    assert "is_unauthorized" not in r.json()
    assert db.calls == []


def test_values_are_trimmed_and_otherwise_untouched():
    db = FakeDb(1)
    r = client(db).get(URL, params={"folder": "  Çğİ_Folder ", "workflow": " wf_m_X' OR 1=1 -- "}, headers=auth())
    assert r.status_code == 200
    assert db.calls == [("Çğİ_Folder", "wf_m_X' OR 1=1 --")]
    assert r.json()["folder"] == "Çğİ_Folder"


def test_max_length_is_accepted():
    db = FakeDb(0)
    assert client(db).get(URL, params={"folder": "F" * 255, "workflow": "W" * 255}, headers=auth()).status_code == 200


@pytest.mark.parametrize("error, status, code", [
    (CheckUnavailable(), 503, "check_unavailable"),
    (UnexpectedResult("NoneType: None"), 502, "unexpected_result"),
])
def test_db_failure_is_never_a_zero(error, status, code):
    r = client(FakeDb(error)).get(URL, params=PARAMS, headers=auth())
    assert r.status_code == status
    assert r.json()["error"] == code
    assert "is_unauthorized" not in r.json()


def test_unhandled_error_is_500_not_200():
    r = client(FakeDb(RuntimeError("beklenmeyen")), raise_server_exceptions=False).get(
        URL, params=PARAMS, headers=auth())
    assert r.status_code == 500
    assert "is_unauthorized" not in r.text


def test_error_body_does_not_leak_details():
    r = client(FakeDb(CheckUnavailable("ORA-01017 user=GIZLI"))).get(URL, params=PARAMS, headers=auth())
    assert "GIZLI" not in r.text and "ORA-" not in r.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_disabled(path):
    assert client(FakeDb()).get(path).status_code == 404


def test_only_get_is_allowed():
    assert client(FakeDb()).post(URL, params=PARAMS, headers=auth()).status_code == 405


def test_shutdown_closes_db():
    db = FakeDb()
    with client(db):
        pass
    assert db.closed
