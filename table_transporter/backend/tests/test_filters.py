from datetime import datetime
from decimal import Decimal

import pytest

from app.errors import AppError, ErrorCode
from app.oracle import sql_templates as t
from app.oracle.guard import assert_allowed
from app.transfer.filters import ColumnMeta, build_where

COLS = {
    "BOLGE": ColumnMeta("BOLGE", "VARCHAR2"),
    "AD": ColumnMeta("AD", "NVARCHAR2"),
    "ID": ColumnMeta("ID", "NUMBER"),
    "TUTAR": ColumnMeta("TUTAR", "NUMBER"),
    "TARIH": ColumnMeta("TARIH", "DATE"),
    "ZAMAN": ColumnMeta("ZAMAN", "TIMESTAMP(6)"),
    "NOT_ALANI": ColumnMeta("NOT_ALANI", "CLOB"),
    "ZORUNLU": ColumnMeta("ZORUNLU", "VARCHAR2", nullable=False),
    "N5": ColumnMeta("N5", "NUMBER", precision=5, scale=0),
    "N52": ColumnMeta("N52", "NUMBER", precision=5, scale=2),
    "NB": ColumnMeta("NB", "FLOAT", precision=5, scale=0),
}


def _invalid(filters, columns=COLS):
    with pytest.raises(AppError) as exc:
        build_where(filters, columns)
    assert exc.value.code == ErrorCode.VALIDATION
    return exc.value


def _guarded(where, binds):
    stmt, _ = t.select_rows("DEMO", "MUSTERI", ["ID"], where, binds, 10)
    assert_allowed(stmt.text)


def test_empty_rejected():
    err = _invalid([])
    assert err.detail == "Taşınacak kümeyi belirlemek için en az bir filtre gerekli."


def test_full_table():
    assert build_where([], COLS, full_table=True) == ("", {})
    with pytest.raises(AppError) as exc:
        build_where([{"column": "BOLGE", "op": "eq", "value": "IST"}], COLS, full_table=True)
    assert exc.value.code == ErrorCode.VALIDATION
    assert exc.value.detail == "Tablonun tamamı seçiliyken filtre verilemez."
    with pytest.raises(AppError) as exc:
        build_where([], COLS, full_table=False)
    assert exc.value.detail == "Taşınacak kümeyi belirlemek için en az bir filtre gerekli."


@pytest.mark.parametrize(
    "op,sql", [("eq", "="), ("ne", "<>"), ("lt", "<"), ("le", "<="), ("gt", ">"), ("ge", ">=")]
)
def test_compare_ops(op, sql):
    where, binds = build_where([{"column": "BOLGE", "op": op, "value": "IST"}], COLS)
    assert where == f'"BOLGE" {sql} :f0'
    assert binds == {"f0": "IST"}
    _guarded(where, binds)


def test_like_in_between_null_and_combined():
    where, binds = build_where(
        [
            {"column": "AD", "op": "like", "value": "AL%"},
            {"column": "ID", "op": "in", "values": [1, "2", 3]},
            {"column": "TARIH", "op": "between", "values": ["2024-01-01", "2024-12-31T23:59:59"]},
            {"column": "TUTAR", "op": "is_null"},
            {"column": "ZAMAN", "op": "is_not_null"},
        ],
        COLS,
    )
    assert where == (
        '"AD" LIKE :f0 AND "ID" IN (:f1, :f2, :f3) AND "TARIH" BETWEEN :f4 AND :f5'
        ' AND "TUTAR" IS NULL AND "ZAMAN" IS NOT NULL'
    )
    assert binds == {
        "f0": "AL%",
        "f1": 1,
        "f2": 2,
        "f3": 3,
        "f4": datetime(2024, 1, 1),
        "f5": datetime(2024, 12, 31, 23, 59, 59),
    }
    _guarded(where, binds)


def test_type_conversion():
    _, b = build_where([{"column": "TUTAR", "op": "eq", "value": "12.50"}], COLS)
    assert b["f0"] == Decimal("12.50") and isinstance(b["f0"], Decimal)
    _, b = build_where([{"column": "TUTAR", "op": "eq", "value": 1.5}], COLS)
    assert b["f0"] == Decimal("1.5")
    _, b = build_where([{"column": "ID", "op": "eq", "value": " -42 "}], COLS)
    assert b["f0"] == -42 and isinstance(b["f0"], int)
    _, b = build_where([{"column": "BOLGE", "op": "eq", "value": 34}], COLS)
    assert b["f0"] == "34"
    _, b = build_where([{"column": "ZAMAN", "op": "ge", "value": "2024-05-06T07:08:09.123456"}], COLS)
    assert b["f0"] == datetime(2024, 5, 6, 7, 8, 9, 123456)


@pytest.mark.parametrize(
    "f",
    [
        {"column": "YOK", "op": "eq", "value": 1},
        {"column": "bolge", "op": "eq", "value": "x"},
        {"column": "BOLGE", "op": "regexp", "value": "x"},
        {"column": "BOLGE", "op": "EQ", "value": "x"},
        {"column": "BOLGE", "op": "eq"},
        {"column": "BOLGE", "op": "eq", "value": None},
        {"column": "BOLGE", "op": "eq", "value": True},
        {"column": "BOLGE", "op": "eq", "value": ["a"]},
        {"column": "BOLGE", "op": "eq", "values": ["a"]},
        {"column": "ID", "op": "eq", "value": "abc"},
        {"column": "ID", "op": "eq", "value": "NaN"},
        {"column": "ID", "op": "eq", "value": "1; DROP TABLE X"},
        {"column": "TARIH", "op": "eq", "value": "31.12.2024"},
        {"column": "TARIH", "op": "eq", "value": 20240101},
        {"column": "TARIH", "op": "eq", "value": "2024-01-01T00:00:00+03:00"},
        {"column": "ID", "op": "like", "value": "1%"},
        {"column": "ID", "op": "between", "values": [1]},
        {"column": "ID", "op": "between", "values": [1, 2, 3]},
        {"column": "ID", "op": "in", "values": []},
        {"column": "ID", "op": "in", "value": 1},
        {"column": "ID", "op": "is_null", "value": 1},
        {"column": "NOT_ALANI", "op": "eq", "value": "x"},
        {"op": "eq", "value": 1},
        "BOLGE = 1",
    ],
)
def test_invalid_filters(f):
    err = _invalid([f])
    assert err.detail


def test_in_limit():
    where, binds = build_where([{"column": "ID", "op": "in", "values": list(range(1000))}], COLS)
    assert len(binds) == 1000 and where.endswith(":f999)")
    _invalid([{"column": "ID", "op": "in", "values": list(range(1001))}])


def test_column_meta_name_must_match_key_and_be_valid():
    _invalid([{"column": "X", "op": "eq", "value": 1}], {"X": ColumnMeta("Y", "NUMBER")})
    _invalid([{"column": "x", "op": "eq", "value": 1}], {"x": ColumnMeta("x", "NUMBER")})


@pytest.mark.parametrize(
    "payload",
    [
        "IST' OR '1'='1",
        "x'); DROP TABLE MUSTERI; --",
        "/* */ DELETE FROM T",
        '" OR 1=1 --',
    ],
)
def test_injection_values_stay_in_binds(payload):
    where, binds = build_where(
        [
            {"column": "BOLGE", "op": "eq", "value": payload},
            {"column": "AD", "op": "in", "values": [payload, "B"]},
        ],
        COLS,
    )
    assert where == '"BOLGE" = :f0 AND "AD" IN (:f1, :f2)'
    assert binds["f0"] == payload and binds["f1"] == payload
    assert payload not in where
    _guarded(where, binds)


def test_injection_in_column_name_rejected():
    _invalid([{"column": 'BOLGE" = 1 OR "A', "op": "eq", "value": 1}])


def _tauto(f):
    err = _invalid([f])
    col = f["column"]
    assert err.detail == f"'{col}' koşulu tüm satırları seçer; kümeyi daraltan bir koşul girin."


def _ok(f):
    build_where([f], COLS)


def test_r1_is_not_null():
    _tauto({"column": "ZORUNLU", "op": "is_not_null"})
    _ok({"column": "BOLGE", "op": "is_not_null"})
    _ok({"column": "ZORUNLU", "op": "is_null"})


@pytest.mark.parametrize("pat", ["%", "%%", "_%", "%_%"])
def test_r2_like_tautology(pat):
    _tauto({"column": "BOLGE", "op": "like", "value": pat})


@pytest.mark.parametrize("pat", ["A%", "_", "__", "%A", ""])
def test_r2_like_ok(pat):
    _ok({"column": "BOLGE", "op": "like", "value": pat})


@pytest.mark.parametrize(
    "f",
    [
        {"column": "N5", "op": "gt", "value": -100000},
        {"column": "N5", "op": "ge", "value": -100001},
        {"column": "N5", "op": "lt", "value": 100000},
        {"column": "N5", "op": "le", "value": "100000"},
        {"column": "N5", "op": "between", "values": [-100000, 100000]},
        {"column": "N5", "op": "ne", "value": 100000},
        {"column": "N5", "op": "ne", "value": 1.5},
        {"column": "N52", "op": "gt", "value": -1000},
        {"column": "N52", "op": "ne", "value": "1.005"},
        {"column": "N52", "op": "lt", "value": 1000},
    ],
)
def test_r3_number_tautology(f):
    _tauto(f)


@pytest.mark.parametrize(
    "f",
    [
        {"column": "N5", "op": "gt", "value": -99999},
        {"column": "N5", "op": "ge", "value": -99999},
        {"column": "N5", "op": "lt", "value": 99999},
        {"column": "N5", "op": "between", "values": [-99999, 100000]},
        {"column": "N5", "op": "between", "values": [-100000, 99999]},
        {"column": "N5", "op": "ne", "value": 99999},
        {"column": "N5", "op": "ne", "value": "3.0"},
        {"column": "N5", "op": "eq", "value": 1000000},
        {"column": "N52", "op": "gt", "value": -999},
        {"column": "N52", "op": "ne", "value": "1.50"},
        {"column": "NB", "op": "gt", "value": -10**9},
        {"column": "ID", "op": "gt", "value": -10**30},
    ],
)
def test_r3_number_ok(f):
    _ok(f)


@pytest.mark.parametrize(
    "f",
    [
        {"column": "TARIH", "op": "gt", "value": "0001-01-01"},
        {"column": "TARIH", "op": "ge", "value": "0001-01-01T00:00:00"},
        {"column": "TARIH", "op": "lt", "value": "9999-12-31"},
        {"column": "ZAMAN", "op": "le", "value": "9999-12-31T23:59:59"},
        {"column": "TARIH", "op": "between", "values": ["0001-01-01", "9999-12-31"]},
    ],
)
def test_r4_date_tautology(f):
    _tauto(f)


@pytest.mark.parametrize(
    "f",
    [
        {"column": "TARIH", "op": "gt", "value": "0001-01-02"},
        {"column": "TARIH", "op": "lt", "value": "9999-12-30T23:59:59"},
        {"column": "TARIH", "op": "between", "values": ["0001-01-02", "9999-12-31"]},
        {"column": "TARIH", "op": "between", "values": ["2024-01-01", "9999-12-31"]},
        {"column": "TARIH", "op": "ge", "value": "2024-01-01"},
    ],
)
def test_r4_date_ok(f):
    _ok(f)


def test_first_tautology_reported():
    err = _invalid([{"column": "BOLGE", "op": "eq", "value": "X"},
                    {"column": "ZORUNLU", "op": "is_not_null"}, {"column": "BOLGE", "op": "like", "value": "%"}])
    assert "'ZORUNLU'" in err.detail
