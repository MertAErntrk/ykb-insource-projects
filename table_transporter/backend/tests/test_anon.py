from datetime import date, datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from app.anon.preview import build_preview
from app.anon.rules import ColumnInfo, compile_plan, is_supported_type
from app.errors import AppError, ErrorCode
from app.oracle.sql_templates import ColumnDef, create_table

KEY = b"s" * 32


def col(name, data_type="VARCHAR2", length=50, *, char_used="B", precision=None, scale=None,
        nullable=True, pk=False, supported=True):
    text = data_type in ("CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2")
    return ColumnInfo(
        name=name, data_type=data_type,
        data_length=length if text else 22, char_length=length if text else 0,
        char_used=char_used if data_type in ("CHAR", "VARCHAR2") else None,
        precision=precision, scale=scale, nullable=nullable, is_pk_or_unique=pk,
        supported=supported,
    )


def rule(column, type_, **params):
    return {"column": column, "type": type_, "params": params}


def plan(rules, cols, mode="create", target=None):
    return compile_plan(rules, cols, mode=mode, target_cols=target, key=KEY)


def err(code, rules, cols, mode="create", target=None):
    with pytest.raises(AppError) as e:
        plan(rules, cols, mode, target)
    assert e.value.code == code
    return e.value.detail


# --- tip desteği ve kapsam --------------------------------------------------------------


def test_supported_types():
    for t in ("NUMBER", "FLOAT", "BINARY_DOUBLE", "CHAR", "NVARCHAR2", "DATE", "TIMESTAMP(6)"):
        assert is_supported_type(t)
    for t in ("CLOB", "BLOB", "RAW", "LONG", "XMLTYPE", "TIMESTAMP(6) WITH TIME ZONE",
              "TIMESTAMP(6) WITH LOCAL TIME ZONE"):
        assert not is_supported_type(t)


def test_rules_must_cover_all_columns_once():
    cols = [col("A"), col("B")]
    assert "B" in err(ErrorCode.VALIDATION, [rule("A", "keep")], cols)
    assert "C" in err(ErrorCode.VALIDATION, [rule("A", "keep"), rule("B", "keep"), rule("C", "keep")], cols)
    assert "A" in err(ErrorCode.VALIDATION, [rule("A", "keep"), rule("A", "null"), rule("B", "keep")], cols)


def test_unknown_type_and_params():
    cols = [col("A")]
    assert "A" in err(ErrorCode.RULE_INVALID, [rule("A", "scramble")], cols)
    assert "A" in err(ErrorCode.RULE_INVALID, [rule("A", "mask", foo=1)], cols)
    assert "A" in err(ErrorCode.RULE_INVALID, [rule("A", "mask", show_last=11)], cols)


def test_unsupported_column_only_exclude():
    cols = [col("A"), ColumnInfo("DOC", "CLOB", 4000, 0, None, None, None, True, False, False)]
    err(ErrorCode.RULE_INVALID, [rule("A", "keep"), rule("DOC", "keep")], cols)
    p = plan([rule("A", "keep"), rule("DOC", "exclude")], cols)
    assert p.output_columns == ["A"]


def test_append_requires_target_column():
    cols = [col("A"), col("B")]
    rules = [rule("A", "keep"), rule("B", "keep")]
    assert "B" in err(ErrorCode.VALIDATION, rules, cols, "append", {"A": col("A")})
    err(ErrorCode.VALIDATION, rules, cols, "append", None)
    p = plan([rule("A", "keep"), rule("B", "exclude")], cols, "append", {"A": col("A")})
    assert p.output_columns == ["A"]


# --- PK/UNIQUE ve NOT NULL --------------------------------------------------------------


@pytest.mark.parametrize("t,params", [("mask", {"show_last": 2}), ("random", {}), ("constant", {"value": "X"}),
                                      ("null", {})])
def test_pk_rejects_non_bijective(t, params):
    cols = [col("ID", pk=True)]
    assert "ID" in err(ErrorCode.RULE_INVALID, [rule("ID", t, **params)], cols)


@pytest.mark.parametrize("t", ["keep", "exclude", "text_token", "hmac"])
def test_pk_allows_bijective(t):
    plan([rule("ID", t)], [col("ID", length=64, pk=True)])
    plan([rule("N", "num_token")], [col("N", "NUMBER", precision=10, scale=0, pk=True)])
    plan([rule("D", "date_token")], [col("D", "DATE", pk=True)])


def test_pk_in_target_counts_in_append():
    cols = [col("ID")]
    err(ErrorCode.RULE_INVALID, [rule("ID", "random")], cols, "append", {"ID": col("ID", pk=True)})


def test_not_null_rejects_null_and_empty_constant():
    cols = [col("A", nullable=False)]
    err(ErrorCode.RULE_INVALID, [rule("A", "null")], cols)
    err(ErrorCode.RULE_INVALID, [rule("A", "constant", value="")], cols)
    plan([rule("A", "constant", value="X")], cols)


# --- hmac uzunluk -----------------------------------------------------------------------


def _hmac_plan(length, pk=False):
    return plan([rule("A", "hmac")], [col("A")], "append", {"A": col("A", length=length, pk=pk)})


def test_hmac_length_behaviour():
    p64 = _hmac_plan(64, pk=True)
    out64 = p64.apply_batch(pa.record_batch({"A": ["X1"]})).column("A")[0].as_py()
    assert len(out64) == 64 and out64 == out64.lower()
    p32 = _hmac_plan(32)
    out32 = p32.apply_batch(pa.record_batch({"A": ["X1"]})).column("A")[0].as_py()
    assert out32 == out64[:32]
    err(ErrorCode.RULE_INVALID, [rule("A", "hmac")], [col("A")], "append", {"A": col("A", length=32, pk=True)})
    err(ErrorCode.RULE_INVALID, [rule("A", "hmac")], [col("A")], "append", {"A": col("A", length=10)})


def test_hmac_append_needs_text_target():
    err(ErrorCode.RULE_INVALID, [rule("A", "hmac")], [col("A")], "append",
        {"A": col("A", "NUMBER", precision=10, scale=0)})


def test_hmac_join_stable_across_types_and_char_rstrip():
    p1 = plan([rule("A", "hmac")], [col("A", "CHAR", 4)])
    p2 = plan([rule("A", "hmac")], [col("A", "VARCHAR2", 10)])
    a = p1.apply_batch(pa.record_batch({"A": ["AB  "]})).column("A")[0].as_py()
    b = p2.apply_batch(pa.record_batch({"A": ["AB"]})).column("A")[0].as_py()
    assert a == b
    pi = plan([rule("ID", "hmac")], [col("ID", "NUMBER", precision=10, scale=0)])
    pd_ = plan([rule("ID", "hmac")], [col("ID", "NUMBER", precision=30, scale=0)])
    x = pi.apply_batch(pa.record_batch({"ID": pa.array([100], pa.int64())})).column("ID")[0].as_py()
    y = pd_.apply_batch(pa.record_batch({"ID": pa.array([Decimal(100)], pa.decimal128(30, 0))})).column("ID")[0].as_py()
    assert x == y


def test_target_column_defs_hmac_varchar64():
    cols = [col("ID", "NUMBER", precision=10, scale=0, nullable=False), col("AD", "CHAR", 20, char_used="C"),
            col("X")]
    p = plan([rule("ID", "hmac"), rule("AD", "keep"), rule("X", "exclude")], cols)
    defs = p.target_column_defs()
    assert defs[0] == ColumnDef("ID", "VARCHAR2", 64, 64, "B", None, None, "N")
    assert defs[1] == ColumnDef("AD", "CHAR", 20, 20, "C", None, None, "Y")
    assert len(defs) == 2
    assert "VARCHAR2(64 BYTE)" in create_table("TT", "T1", defs).text


# --- constant ---------------------------------------------------------------------------


def test_constant_byte_semantics_turkish_overflow():
    err(ErrorCode.RULE_INVALID, [rule("A", "constant", value="ÇĞŞ")], [col("A", length=5, char_used="B")])
    plan([rule("A", "constant", value="ÇĞŞ")], [col("A", length=6, char_used="B")])
    plan([rule("A", "constant", value="ÇĞŞ")], [col("A", length=3, char_used="C")])


def test_constant_number_and_date():
    cols = [col("N", "NUMBER", precision=5, scale=2), col("D", "DATE"), col("I", "NUMBER", precision=9, scale=0)]
    err(ErrorCode.RULE_INVALID, [rule("N", "constant", value="1234.5"), rule("D", "keep"), rule("I", "keep")], cols)
    err(ErrorCode.RULE_INVALID, [rule("N", "constant", value="1.234"), rule("D", "keep"), rule("I", "keep")], cols)
    err(ErrorCode.RULE_INVALID, [rule("N", "keep"), rule("D", "constant", value="31.12.2020"), rule("I", "keep")], cols)
    p = plan([rule("N", "constant", value="123.4"), rule("D", "constant", value="2020-12-31"),
              rule("I", "constant", value="42")], cols)
    b = pa.record_batch({"N": pa.array([None, Decimal("1.00")], pa.decimal128(5, 2)),
                         "D": pa.array([None, datetime(2001, 1, 1)], pa.timestamp("s")),
                         "I": pa.array([1, None], pa.int64())})
    out = p.apply_batch(b)
    assert out.column("N").to_pylist() == [Decimal("123.40")] * 2
    assert out.column("N").type == pa.decimal128(5, 2)
    assert out.column("D").to_pylist() == [datetime(2020, 12, 31)] * 2
    assert out.column("I").to_pylist() == [42, 42] and out.column("I").type == pa.int64()


# --- kural × tip ------------------------------------------------------------------------


def test_type_compatibility():
    err(ErrorCode.RULE_INVALID, [rule("N", "mask", show_last=2)], [col("N", "NUMBER")])
    err(ErrorCode.RULE_INVALID, [rule("D", "num_token")], [col("D", "DATE")])
    err(ErrorCode.RULE_INVALID, [rule("F", "num_token")], [col("F", "BINARY_DOUBLE")])
    err(ErrorCode.RULE_INVALID, [rule("A", "date_shift", anchor="A", max_days=5)], [col("A")])


def test_mask_rule():
    p = plan([rule("A", "mask", show_last=4)], [col("A")])
    out = p.apply_batch(pa.record_batch({"A": ["TR123456789", "AB", None]})).column("A").to_pylist()
    assert out == ["*******6789", "**", None]
    p0 = plan([rule("A", "mask", show_last=0)], [col("A")])
    assert p0.apply_batch(pa.record_batch({"A": ["ABC"]})).column("A").to_pylist() == ["***"]


def test_null_keep_exclude_order():
    cols = [col("A"), col("B"), col("C")]
    p = plan([rule("C", "keep"), rule("A", "null"), rule("B", "exclude")], cols)
    out = p.apply_batch(pa.record_batch({"A": ["x"], "B": ["y"], "C": ["z"]}))
    assert out.schema.names == ["A", "C"]
    assert out.column("A").to_pylist() == [None] and out.column("A").type == pa.string()
    assert out.column("C").to_pylist() == ["z"]


def test_random_rule_types():
    cols = [col("S", length=10), col("I", "NUMBER", precision=9, scale=0),
            col("M", "NUMBER", precision=10, scale=2), col("D", "DATE")]
    p = plan([rule(c.name, "random") for c in cols], cols)
    b = pa.record_batch({
        "S": ["Çağrı12", None], "I": pa.array([-12345, 7], pa.int64()),
        "M": pa.array([Decimal("123.45"), None], pa.decimal128(10, 2)),
        "D": pa.array([datetime(1900, 1, 1), None], pa.timestamp("s")),
    })
    out = p.apply_batch(b)
    s = out.column("S").to_pylist()
    assert len(s[0]) == 7 and s[0].isalnum() and s[0].isascii() and s[1] is None
    i = out.column("I").to_pylist()
    assert i[0] < 0 and len(str(abs(i[0]))) == 5 and 0 <= i[1] <= 9
    m = out.column("M").to_pylist()
    assert out.column("M").type == pa.decimal128(10, 2) and len(str(int(m[0]))) == 3 and m[1] is None
    d = out.column("D").to_pylist()[0]
    assert datetime(1950, 1, 1) <= d <= datetime(2030, 12, 31, 23, 59, 59)


def test_num_token_types():
    cols = [col("I", "NUMBER", precision=18, scale=0), col("T", length=20),
            col("M", "NUMBER", precision=7, scale=2)]
    p = plan([rule("I", "num_token"), rule("T", "text_token"), rule("M", "num_token")], cols)
    b = pa.record_batch({
        "I": pa.array([12345678901, -5, None], pa.int64()),
        "T": ["TR00-1234", "ABC", None],
        "M": pa.array([Decimal("12345.67"), Decimal("-0.50"), None], pa.decimal128(7, 2)),
    })
    out = p.apply_batch(b)
    i = out.column("I").to_pylist()
    assert out.column("I").type == pa.int64()
    assert len(str(i[0])) == 11 and -9 <= i[1] <= -1 and i[2] is None
    t = out.column("T").to_pylist()
    assert t[0][:2].isupper() and t[0][2:4].isdigit() and t[0][5:].isdigit()
    assert len(t[0]) == 9 and t[0][4] == "-" and len(t[1]) == 3 and t[1].isupper() and t[2] is None
    m = out.column("M").to_pylist()
    assert str(m[0]).endswith(".67") and len(str(int(m[0]))) == 5
    assert m[1] < 0 and str(m[1]).endswith(".50")
    # determinizm + aynı değer aynı sonuç
    assert p.apply_batch(b).column("I").to_pylist() == i


def test_num_token_number38_decimal():
    v = Decimal("12345678901234567890123456789012345678")
    p = plan([rule("N", "num_token")], [col("N", "NUMBER", precision=38, scale=0, pk=True)])
    arr = pa.array([v], pa.decimal128(38, 0))
    out = p.apply_batch(pa.record_batch({"N": arr})).column("N")
    assert out.type == pa.decimal128(38, 0)
    y = out[0].as_py()
    assert y != v and len(str(y)) == 38


def test_num_token_int64_beyond_2_53_lossless():
    v = 2**53 + 1
    p = plan([rule("N", "num_token"), rule("K", "keep")],
             [col("N", "NUMBER", precision=18, scale=0), col("K", "NUMBER", precision=18, scale=0)])
    out = p.apply_batch(pa.record_batch({"N": pa.array([v], pa.int64()), "K": pa.array([v], pa.int64())}))
    assert out.column("K")[0].as_py() == v
    y = out.column("N")[0].as_py()
    assert isinstance(y, int) and len(str(y)) == len(str(v))
    assert p._fpe.decrypt_int(y, len(str(v))) == v


def test_num_token_negative_scale_rejected():
    err(ErrorCode.RULE_INVALID, [rule("N", "num_token")], [col("N", "NUMBER", precision=5, scale=-2)])


# --- date_shift -------------------------------------------------------------------------


def _shift_cols():
    return [col("PARTY_ID", "NUMBER", precision=10, scale=0), col("BAS", "DATE"), col("BIT", "TIMESTAMP(6)")]


def test_date_shift_row_consistency():
    rules = [rule("PARTY_ID", "exclude"), rule("BAS", "date_shift", anchor="PARTY_ID", max_days=30),
             rule("BIT", "date_shift", anchor="PARTY_ID", max_days=30)]
    p = plan(rules, _shift_cols())
    bas = [datetime(2020, 1, 1), datetime(2021, 6, 1), datetime(2022, 3, 3), None]
    bit = [datetime(2020, 1, 2), datetime(2021, 7, 1), None, datetime(2022, 1, 1)]
    b = pa.record_batch({"PARTY_ID": pa.array([1, 2, 3, None], pa.int64()),
                         "BAS": pa.array(bas, pa.timestamp("s")), "BIT": pa.array(bit, pa.timestamp("us"))})
    out = p.apply_batch(b)
    assert out.schema.names == ["BAS", "BIT"]
    assert out.column("BIT").type == pa.timestamp("us")
    ob, oi = out.column("BAS").to_pylist(), out.column("BIT").to_pylist()
    for i in range(2):
        assert ob[i] < oi[i]
        assert oi[i] - bit[i] == ob[i] - bas[i]
        assert 1 <= abs((ob[i] - bas[i]).days) <= 30
    assert ob[3] is None and oi[2] is None
    # aynı anchor → aynı ofset (deterministik)
    assert p.apply_batch(b).column("BAS").to_pylist() == ob


def test_date_shift_clamps_at_bounds():
    p = plan([rule("D", "date_shift", anchor="D", max_days=3650)], [col("D", "DATE")])
    out = p.apply_batch(pa.record_batch({"D": pa.array([date(1, 1, 1), date(9999, 12, 31)], pa.date32())}))
    vals = out.column("D").to_pylist()
    assert all(date(1, 1, 1) <= v <= date(9999, 12, 31) for v in vals)


def test_date_shift_requires_same_anchor_and_valid_params():
    cols = _shift_cols()
    err(ErrorCode.RULE_INVALID, [rule("PARTY_ID", "keep"), rule("BAS", "date_shift", anchor="PARTY_ID", max_days=30),
                                 rule("BIT", "date_shift", anchor="BAS", max_days=30)], cols)
    err(ErrorCode.RULE_INVALID, [rule("PARTY_ID", "keep"), rule("BAS", "date_shift", anchor="PARTY_ID", max_days=30),
                                 rule("BIT", "date_shift", anchor="PARTY_ID", max_days=31)], cols)
    err(ErrorCode.RULE_INVALID, [rule("PARTY_ID", "keep"), rule("BAS", "date_shift", anchor="YOK", max_days=30),
                                 rule("BIT", "keep")], cols)
    err(ErrorCode.RULE_INVALID, [rule("PARTY_ID", "keep"), rule("BAS", "date_shift", anchor="PARTY_ID", max_days=0),
                                 rule("BIT", "keep")], cols)


# --- önizleme ---------------------------------------------------------------------------


def test_build_preview_format():
    cols = [col("ID", "NUMBER", precision=18, scale=0), col("AD"), col("TUTAR", "NUMBER", precision=10, scale=2),
            col("D", "DATE")]
    p = plan([rule("ID", "keep"), rule("AD", "exclude"), rule("TUTAR", "null"), rule("D", "keep")], cols)
    b = pa.record_batch({
        "ID": pa.array([2**53 + 1, 5] + [1] * 6, pa.int64()), "AD": ["a", None] + ["x"] * 6,
        "TUTAR": pa.array([Decimal("1.50")] * 8, pa.decimal128(10, 2)),
        "D": pa.array([datetime(2020, 1, 2)] * 8, pa.timestamp("s")),
    })
    pv = build_preview(b, p)
    assert [c["column"] for c in pv] == ["ID", "AD", "TUTAR", "D"]
    idc, ad, tutar, d = pv
    assert set(idc) == {"column", "type", "before", "after", "null_before", "null_after"}
    assert idc["before"][:2] == [str(2**53 + 1), 5] and len(idc["before"]) == 5
    assert ad["type"] == "exclude" and ad["after"] == [] and ad["null_after"] == 0 and ad["null_before"] == 1
    assert tutar["before"][0] == "1.50" and tutar["after"] == [None] * 5 and tutar["null_after"] == 5
    assert d["before"][0] == "2020-01-02T00:00:00"


# --- standart katalog: text_token / date_token -------------------------------------------


def test_standard_rules_catalog():
    from app.anon.rules import ONE_TO_ONE, RULE_PARAMS, STANDARD_RULES
    assert STANDARD_RULES == {"number": "num_token", "text": "text_token", "date": "date_token"}
    assert {"text_token", "date_token", "num_token"} <= ONE_TO_ONE
    assert RULE_PARAMS["text_token"] == set() and RULE_PARAMS["date_token"] == set()


def test_standard_rule_type_checks():
    err(ErrorCode.RULE_INVALID, [rule("T", "num_token")], [col("T")])
    err(ErrorCode.RULE_INVALID, [rule("T", "num_token")], [col("T", "CHAR", 10)])
    err(ErrorCode.RULE_INVALID, [rule("N", "text_token")], [col("N", "NUMBER", precision=5, scale=0)])
    err(ErrorCode.RULE_INVALID, [rule("D", "text_token")], [col("D", "DATE")])
    err(ErrorCode.RULE_INVALID, [rule("T", "date_token")], [col("T")])
    err(ErrorCode.RULE_INVALID, [rule("F", "date_token")], [col("F", "BINARY_DOUBLE")])
    # append: hedef aynı kategoride olmalı
    err(ErrorCode.RULE_INVALID, [rule("T", "text_token")], [col("T")], "append",
        {"T": col("T", "NUMBER", precision=5, scale=0)})
    err(ErrorCode.RULE_INVALID, [rule("D", "date_token")], [col("D", "DATE")], "append", {"D": col("D")})
    plan([rule("T", "text_token")], [col("T")], "append", {"T": col("T", length=50)})
    plan([rule("D", "date_token")], [col("D", "DATE")], "append", {"D": col("D", "TIMESTAMP(6)")})


def test_text_and_date_token_apply():
    cols = [col("T", "NVARCHAR2", 20), col("C", "CHAR", 8), col("D", "DATE"), col("S", "TIMESTAMP(6)")]
    p = plan([rule("T", "text_token"), rule("C", "text_token"), rule("D", "date_token"),
              rule("S", "date_token")], cols)
    b = pa.record_batch({
        "T": ["Çağrı Öz", "100234", None],
        "C": ["AB12    ", "0012", None],
        "D": pa.array([datetime(2024, 2, 29), datetime(2023, 7, 1, 10, 0, 1), None], pa.timestamp("s")),
        "S": pa.array([datetime(2020, 1, 1, 0, 0, 0, 500), datetime(2021, 12, 31, 23, 59, 59), None],
                      pa.timestamp("us")),
    })
    out = p.apply_batch(b)
    t = out.column("T").to_pylist()
    assert len(t[0]) == 8 and t[0][5] == " " and len(t[0].encode()) == len("Çağrı Öz".encode()) and t[2] is None
    assert t[1] == str(p._fpe.token_int(100234))
    c = out.column("C").to_pylist()
    assert len(c[0]) == 4 and c[1][0] == "0" and len(c[1]) == 4 and c[2] is None
    d = out.column("D").to_pylist()
    assert d[0].year == 2024 and d[0].time() == datetime.min.time() and d[1].year == 2023 and d[2] is None
    s_ = out.column("S").to_pylist()
    assert s_[0].year == 2020 and s_[0].microsecond == 500 and s_[1].year == 2021
    assert out.column("D").type == pa.timestamp("s") and out.column("S").type == pa.timestamp("us")
