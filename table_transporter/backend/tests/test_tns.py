import logging
import os

import pytest

from app.config import project_path
from app.errors import AppError, ErrorCode
from app.oracle import tns
from app.oracle.tns import get_entry, is_target_allowed, list_aliases, load_tns, parse_tnsnames

SIMPLE = """
# yorum satırı
demo_dev=(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=dbhost1.example)(PORT=1521))
  (CONNECT_DATA=(SERVICE_NAME=DEMODEV)))

DEMO_TEST =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = scan1.example )(PORT = 1453 ) )
    (CONNECT_DATA = (service_name = DEMOTEST ) )
  )
"""


def test_single_and_spacing():
    d = parse_tnsnames(SIMPLE)
    assert set(d) == {"DEMO_DEV", "DEMO_TEST"}
    e = d["DEMO_TEST"]
    assert (e.host, e.port, e.service_name, e.sid) == ("scan1.example", 1453, "DEMOTEST", None)
    assert e.descriptor == (
        "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=scan1.example)(PORT=1453))"
        "(CONNECT_DATA=(SERVICE_NAME=DEMOTEST)))"
    )
    assert d["DEMO_DEV"].port == 1521


def test_multi_alias_indented_and_sid():
    d = parse_tnsnames("  A1, b2 = (DESCRIPTION=(ADDRESS=(HOST=h1.example)(PORT=1))(CONNECT_DATA=(SID=XE)))")
    assert set(d) == {"A1", "B2"}
    assert d["A1"].descriptor == d["B2"].descriptor
    assert d["B2"].sid == "XE" and d["B2"].service_name is None


def test_description_list_and_address_list():
    d = parse_tnsnames(
        "L=(DESCRIPTION_LIST=(DESCRIPTION=(ADDRESS_LIST=(ADDRESS=(HOST=dbhost1.example)(PORT=1))"
        "(ADDRESS=(HOST=dbhost2.example)(PORT=2)))(CONNECT_DATA=(SERVICE_NAME=S))))"
    )
    e = d["L"]
    assert (e.host, e.port, e.service_name) == ("dbhost1.example", 1, "S")
    assert "(ADDRESS_LIST=" in e.descriptor


def test_broken_entries_skipped_only(caplog):
    text = (
        "GOOD1=(DESCRIPTION=(ADDRESS=(HOST=h.example)(PORT=1)))\n"
        "UNBAL=(DESCRIPTION=(ADDRESS=(HOST=h.example)(PORT=1)\n"
        "GOOD2=(DESCRIPTION=(ADDRESS=(HOST=h2.example)(PORT=2)))\n"
        "NOEQ (DESCRIPTION=(HOST=x))\n"
        "1BAD=(DESCRIPTION=(HOST=x))\n"
        "GOOD3=(DESCRIPTION=(ADDRESS=(HOST=h3.example)(PORT=3)))\n"
    )
    with caplog.at_level(logging.WARNING):
        d = parse_tnsnames(text)
    assert set(d) == {"GOOD1", "GOOD2", "GOOD3"}
    assert caplog.records


def test_ifile_not_followed(caplog):
    with caplog.at_level(logging.WARNING):
        d = parse_tnsnames("IFILE=/etc/other.ora\nA=(DESCRIPTION=(ADDRESS=(HOST=h.example)(PORT=1)))")
    assert set(d) == {"A"}
    assert any("IFILE" in r.getMessage() for r in caplog.records)


def test_duplicate_last_wins(caplog):
    with caplog.at_level(logging.WARNING):
        d = parse_tnsnames("A=(D=(HOST=h1.example)(PORT=1))\na=(D=(HOST=h2.example)(PORT=2))")
    assert d["A"].host == "h2.example"
    assert caplog.records


def _settings(settings, tmp_path, text):
    (tmp_path / "tnsnames.ora").write_text(text, encoding="utf-8-sig")
    return settings.model_copy(update={"tt_tns_admin": str(tmp_path)})


def test_list_aliases_and_target_allowed(settings, tmp_path):
    s = _settings(
        settings,
        tmp_path,
        "DEMOLIVE=(D=(HOST=scan1.example)(PORT=1521)(SERVICE_NAME=DEMO))\n"
        "DEMO_TEST=(D=(HOST=dbhost1.example)(PORT=1453)(SERVICE_NAME=DEMOTEST))\n",
    )
    items = list_aliases(s)
    assert [i["alias"] for i in items] == ["DEMOLIVE", "DEMO_TEST"]
    assert items[0] == {
        "alias": "DEMOLIVE", "host": "scan1.example", "port": 1521,
        "service_name": "DEMO", "target_allowed": False,
    }
    assert items[1]["target_allowed"] is True
    assert [i["alias"] for i in list_aliases(s, "live")] == ["DEMOLIVE"]
    assert is_target_allowed(s, "demolive") is False


def test_get_entry(settings, tmp_path):
    s = _settings(settings, tmp_path, "DEMO_TEST=(D=(HOST=dbhost1.example)(PORT=1))\n")
    assert get_entry(s, "demo_test").alias == "DEMO_TEST"
    with pytest.raises(AppError) as ei:
        get_entry(s, "YOK")
    assert ei.value.code == ErrorCode.NOT_FOUND


def test_missing_file_empty(settings, tmp_path):
    s = settings.model_copy(update={"tt_tns_admin": str(tmp_path)})
    assert load_tns(s) == {}


def test_mtime_cache(settings, tmp_path, monkeypatch):
    s = _settings(settings, tmp_path, "A=(D=(HOST=h1.example)(PORT=1))\n")
    calls = []
    real = tns.parse_tnsnames
    monkeypatch.setattr(tns, "parse_tnsnames", lambda t: calls.append(1) or real(t))
    assert load_tns(s)["A"].host == "h1.example"
    load_tns(s)
    assert len(calls) == 1
    p = tmp_path / "tnsnames.ora"
    p.write_text("A=(D=(HOST=h2.example)(PORT=1))\n", encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert load_tns(s)["A"].host == "h2.example"
    assert len(calls) == 2


def test_real_file(settings):
    path = project_path(settings.tt_tns_admin) / "tnsnames.ora"
    if not path.exists():
        pytest.skip("config/tnsnames.ora yok")
    d = load_tns(settings)
    # Gerçek dosya git dışıdır; adlar teste yazılmaz, yalnız ayrıştırma sınanır.
    assert d
    assert all(e.host and e.host == e.host.strip() and " " not in e.descriptor for e in d.values())
