import logging
from datetime import datetime
from decimal import Decimal

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from app.anon.fpe import FeistelFPE
from app.anon.reveal import reveal_values
from app.anon.rules import ColumnInfo, compile_plan, derive_key
from app.log.db import Database
from app.log.repository import Repository
from app.main import create_app
from app.transfer.service import hmac_key
from tests.test_auth import PASSWORD, FakeLdap
from tests.test_transfer_routes import err

KEY = b"reveal-test-key-0123456789abcdefgh"
NUM = ColumnInfo("PARTY_ID", "NUMBER", 22, 0, None, 12, 0, False, True, True)
TXT = ColumnInfo("REF", "VARCHAR2", 40, 40, "B", None, None, True, False, True)
DT = ColumnInfo("DOGUM", "DATE", 7, 0, None, None, None, True, False, True)


def _plan(key: bytes):
    rules = [{"column": "PARTY_ID", "type": "num_token", "params": {}},
             {"column": "REF", "type": "text_token", "params": {}},
             {"column": "DOGUM", "type": "date_token", "params": {}}]
    return compile_plan(rules, [NUM, TXT, DT], mode="create", target_cols=None, key=key)


def _forward(key: bytes, ids: list[int], refs: list[str], dates: list[datetime] = ()) -> tuple[list, list, list]:
    """Beklenen token'lar RulePlan'ın ileri yönüyle (int64 + metin + tarih kolon)."""
    n = max(len(ids), len(refs), len(dates))
    pad = lambda xs: list(xs) + [None] * (n - len(xs))  # noqa: E731
    batch = pa.RecordBatch.from_arrays(
        [pa.array(pad(ids), pa.int64()), pa.array(pad(refs), pa.string()), pa.array(pad(dates), pa.timestamp("s"))],
        names=["PARTY_ID", "REF", "DOGUM"])
    out = _plan(key).apply_batch(batch)
    return (out.column(0).to_pylist()[: len(ids)], out.column(1).to_pylist()[: len(refs)],
            out.column(2).to_pylist()[: len(dates)])


def test_reveal_values_number_text_date():
    ids, refs = [0, 7, -3, 123456789012, -42], ["TR-0012-AB", "Çağrı Öz", "x" + "1" * 45, "100234", "0012"]
    dates = [datetime(2024, 2, 29), datetime(1999, 12, 31, 23, 59, 59)]
    tid, tref, tdt = _forward(KEY, ids, refs, dates)
    got = reveal_values(KEY, "num_token", [str(t) for t in tid])
    assert [g["original"] for g in got] == [str(i) for i in ids]
    assert all(g["error"] is None for g in got) and [g["value"] for g in got] == [str(t) for t in tid]
    assert [g["original"] for g in reveal_values(KEY, "text_token", tref)] == refs
    iso = [f"{tdt[0]:%Y-%m-%d}", f"{tdt[1]:%Y-%m-%d %H:%M:%S}"]
    assert [g["original"] for g in reveal_values(KEY, "date_token", iso)] == ["2024-02-29", "1999-12-31 23:59:59"]
    tr = [f"{tdt[0]:%d.%m.%Y}", f"{tdt[1]:%d/%m/%Y %H:%M:%S}"]
    assert [g["original"] for g in reveal_values(KEY, "date_token", tr)] == ["29.02.2024", "31/12/1999 23:59:59"]


def test_reveal_date_fraction_and_errors():
    f = FeistelFPE(derive_key(KEY, b"tt-fpe"))
    v = datetime(2020, 5, 6, 7, 8, 9, 123456)
    t = f.token_date(v)
    got = reveal_values(KEY, "date_token", [f"{t:%Y-%m-%d %H:%M:%S}.123456", "2023-02-30", "abc", "1.2.2023"])
    assert got[0]["original"] == "2020-05-06 07:08:09.123456"
    assert [g["original"] is None for g in got[1:]] == [True, True, True]
    assert got[1]["error"] == "Geçersiz tarih." and got[2]["error"].startswith("Tarih bekleniyordu")


def test_reveal_values_decimal_and_row_errors():
    f = FeistelFPE(derive_key(KEY, b"tt-fpe"))
    vals = [Decimal("12.50"), Decimal("-0.75"), Decimal("-8")]
    toks = [format(f.token_decimal(v), "f") for v in vals]
    assert [g["original"] for g in reveal_values(KEY, "num_token", toks)] == ["12.50", "-0.75", "-8"]
    got = reveal_values(KEY, "num_token", ["12a", "1e5", "-", "1" * 39, "5"])
    assert [g["original"] is None for g in got] == [True, True, True, True, False]
    assert got[0]["error"] == "Sayı bekleniyordu." and "38 basamak" in got[3]["error"]
    with pytest.raises(ValueError):
        reveal_values(KEY, "hmac", ["1"])


# --- uç --------------------------------------------------------------------------------------


@pytest.fixture
def client(settings, tmp_path):
    app = create_app(settings)
    app.state.ldap_client_factory = FakeLdap()
    db = Database(tmp_path / "log.db")
    db.init_schema()
    app.state.repository = Repository(db)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/auth/login", json={"username": "mert", "password": PASSWORD})
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        yield c
    db.close()


def _audits(c):
    return [dict(r) for r in c.app.state.repository.db.query("SELECT * FROM audit_log WHERE action='reveal'")]


def test_reveal_endpoint_and_audit(client, settings, caplog):
    caplog.set_level(logging.DEBUG)
    ids, refs, dates = [918273645501, -6], ["ali.veli@ornek.com"], [datetime(2001, 3, 4, 5, 6, 7)]
    tid, tref, tdt = _forward(hmac_key(settings), ids, refs, dates)
    values = [f" {tid[0]} ", "", str(tid[1]), "abc"]
    r = client.post("/api/anon/reveal", json={"rule": "num_token", "values": values})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rule"] == "num_token"
    assert [i["value"] for i in body["items"]] == [str(tid[0]), str(tid[1]), "abc"]
    assert [i["original"] for i in body["items"]] == [str(ids[0]), str(ids[1]), None]
    assert body["items"][2]["error"] == "Sayı bekleniyordu."

    r = client.post("/api/anon/reveal", json={"rule": "text_token", "values": tref})
    assert r.json()["items"][0]["original"] == refs[0]
    tdate = f"{tdt[0]:%d.%m.%Y %H:%M:%S}"
    r = client.post("/api/anon/reveal", json={"rule": "date_token", "values": [tdate]})
    assert r.json()["items"][0]["original"] == "04.03.2001 05:06:07"

    audits = _audits(client)
    assert [(a["username"], a["job_id"], a["detail"]) for a in audits] == [
        ("mert", None, "num_token: 3 değer"), ("mert", None, "text_token: 1 değer"),
        ("mert", None, "date_token: 1 değer")]
    secrets = [str(x) for x in (*ids, *tid)] + refs + tref + [tdate, "04.03.2001"]
    for s in secrets:
        assert all(s not in (a["detail"] or "") for a in audits)
        assert len(s) < 4 or s not in caplog.text  # kısa değerler log biçimindeki sayılarla çakışabilir


def test_reveal_endpoint_errors(client, settings):
    anon = TestClient(client.app, raise_server_exceptions=False)
    assert anon.post("/api/anon/reveal", json={"rule": "num_token", "values": ["1"]}).status_code == 401
    for body in ({"rule": "hmac", "values": ["1"]}, {"rule": "num_token", "values": []},
                 {"rule": "num_token", "values": ["  ", ""]}, {"rule": "num_token", "values": ["1"] * 101},
                 {"rule": "text_token", "values": ["a" * 4001]}):
        assert err(client.post("/api/anon/reveal", json=body)) == (422, "VALIDATION"), body
    assert _audits(client) == []
    ok = client.post("/api/anon/reveal", json={"rule": "text_token", "values": ["a" * 4000] + ["1"] * 99})
    assert ok.status_code == 200 and len(ok.json()["items"]) == 100
    assert _audits(client)[-1]["detail"] == "text_token: 100 değer"
    # eski iş tabanlı uç kalktı
    assert client.post("/api/transfers/j1/reveal", json={"column": "A", "values": ["1"]}).status_code in (404, 405)


def test_reveal_text_token_keeps_surrounding_spaces(client, settings):
    """VARCHAR2 değerindeki baş/son boşluk tweak'e girer; kırpılırsa sessizce yanlış çözülürdü."""
    f = FeistelFPE(derive_key(hmac_key(settings), b"tt-fpe"))
    original = " x "
    token = f.token_text_std(original)
    r = client.post("/api/anon/reveal", json={"rule": "text_token", "values": [token, "   "]})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["value"] for i in items] == [token]
    assert items[0]["original"] == original
