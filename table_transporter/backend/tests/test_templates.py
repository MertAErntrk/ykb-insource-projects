from decimal import Decimal

import pytest

from app.oracle import sql_templates as t
from app.oracle.guard import assert_allowed
from app.oracle.identifiers import IDENT_RE, is_valid_ident, qualified, quote_ident


def col(name, data_type, *, data_length=None, char_length=None, char_used=None,
        precision=None, scale=None, nullable="Y"):
    return t.ColumnDef(name, data_type, data_length, char_length, char_used, precision, scale, nullable)


def _select_templates():
    return [
        t.dict_schemas("ar", 50, 0),
        t.dict_schemas(None, 1, 100),
        t.dict_tables("DEMO", "mus", 50, 0),
        t.dict_columns("DEMO", "MUSTERI"),
        t.dict_unique_columns("DEMO", "MUSTERI"),
        t.dict_triggers("DEMO", "MUSTERI"),
        t.dict_schema_triggers("DEMO"),
        t.dict_object("DEMO", "MUSTERI"),
        t.dba_object("DEMO", "MUSTERI"),
        t.count_rows("DEMO", "MUSTERI", '"BOLGE" = :f0', {"f0": "IST"}, 5_000_001),
        t.select_rows("DEMO", "MUSTERI", ["ID", "BOLGE"], "", {}, 5_000_001),
    ]


def _all_stmts():
    stmts = [s for s, _ in _select_templates()]
    stmts.append(t.create_table("TT", "T1", [col("A", "DATE", nullable="N")]))
    stmts.append(t.insert_rows("TT", "T1", ["A", "B"]))
    return stmts


def test_every_template_passes_guard_and_has_no_literal():
    for stmt in _all_stmts():
        assert_allowed(stmt.text)
        assert "'" not in stmt.text
        assert t.is_trusted(stmt)


def test_select_kinds_and_binds_are_dicts():
    for stmt, binds in _select_templates():
        assert stmt.kind == "select"
        assert isinstance(binds, dict)
        for name in binds:
            assert f":{name}" in stmt.text


def test_constant_values_are_bound():
    stmt, binds = t.dict_tables("DEMO", None, 10, 0)
    assert binds["tt_n"] == "N" and binds["tt_owner"] == "DEMO" and binds["tt_q"] is None
    _, binds = t.dict_schemas(None, 10, 0)
    assert (binds["tt_n"], binds["tt_y"]) == ("N", "Y")
    _, binds = t.dict_triggers("DEMO", "MUSTERI")
    assert binds["tt_enabled"] == "ENABLED" and binds["tt_type"] == "TABLE"
    _, binds = t.dict_unique_columns("DEMO", "MUSTERI")
    assert (binds["tt_p"], binds["tt_u"]) == ("P", "U")
    _, binds = t.dict_schema_triggers("DEMO")
    assert {binds["tt_schema"], binds["tt_database"]} == {"SCHEMA", "DATABASE"}


def test_select_rows_always_fetch_first():
    stmt, binds = t.select_rows("DEMO", "MUSTERI", ["ID"], '"ID" > :f0', {"f0": 3}, 100)
    assert stmt.text.endswith("FETCH FIRST :tt_limit ROWS ONLY")
    assert stmt.text.startswith('SELECT "ID" FROM "DEMO"."MUSTERI" WHERE "ID" > :f0')
    assert binds == {"f0": 3, "tt_limit": 100}


def test_count_rows_without_filter_is_capped():
    stmt, binds = t.count_rows("DEMO", "MUSTERI", "", {}, 5_000_001)
    assert stmt.text == 'SELECT COUNT(*) FROM (SELECT 1 FROM "DEMO"."MUSTERI" FETCH FIRST :tt_cap ROWS ONLY)'
    assert binds == {"tt_cap": 5_000_001}
    assert "5000001" not in stmt.text  # tavan literal değil, bind
    assert_allowed(stmt.text)


def test_count_rows_with_filter_is_capped():
    stmt, binds = t.count_rows("DEMO", "MUSTERI", '"BOLGE" = :f0', {"f0": "IST"}, 1001)
    assert stmt.text == ('SELECT COUNT(*) FROM (SELECT 1 FROM "DEMO"."MUSTERI" WHERE "BOLGE" = :f0'
                         " FETCH FIRST :tt_cap ROWS ONLY)")
    assert binds == {"f0": "IST", "tt_cap": 1001}
    for bad in (0, -1, True, "10"):
        with pytest.raises(ValueError):
            t.count_rows("DEMO", "MUSTERI", "", {}, bad)


def test_reserved_bind_names_rejected():
    with pytest.raises(ValueError):
        t.select_rows("DEMO", "MUSTERI", ["ID"], "", {"tt_limit": 1}, 10)


@pytest.mark.parametrize("bad", ["t", "T;", 'T"', "T\n", "A" * 129, "", "İ", "1T", "T T", None, 5])
def test_identifier_rejected(bad):
    assert not is_valid_ident(bad)
    with pytest.raises(ValueError):
        quote_ident(bad)
    with pytest.raises(ValueError):
        t.select_rows("DEMO", bad, ["ID"], "", {}, 10)
    with pytest.raises(ValueError):
        t.select_rows("DEMO", "MUSTERI", [bad], "", {}, 10)
    with pytest.raises(ValueError):
        t.dict_columns(bad, "MUSTERI")
    with pytest.raises(ValueError):
        t.insert_rows(bad, "T", ["A"])
    with pytest.raises(ValueError):
        t.create_table("TT", "T", [col(bad, "DATE")])


def test_identifier_ok():
    assert IDENT_RE == r"[A-Z][A-Z0-9_$#]{0,127}"
    assert quote_ident("A" * 128) == '"' + "A" * 128 + '"'
    assert qualified("DEMO", "MUS$T#1") == '"DEMO"."MUS$T#1"'
    assert not is_valid_ident("ABC", max_len=2)


def test_externally_built_stmt_not_trusted():
    fake = t.Stmt(text="SELECT 1 FROM DUAL", kind="select", _token=object())
    assert not t.is_trusted(fake)
    assert not t.is_trusted("SELECT 1 FROM DUAL")
    real = t.dict_columns("DEMO", "MUSTERI")[0]
    # sentinel taşısa bile metni/türü uyumsuz Stmt güvenilmez
    assert not t.is_trusted(t.Stmt(text="DROP TABLE X", kind="select", _token=real._token))
    assert not t.is_trusted(t.Stmt(text=real.text, kind="insert", _token=real._token))


def test_create_table_formats():
    stmt = t.create_table(
        "TT",
        "MUSTERI",
        [
            col("V_B", "VARCHAR2", data_length=40, char_length=40, char_used="B", nullable="N"),
            col("V_C", "VARCHAR2", data_length=40, char_length=10, char_used="C"),
            col("C_B", "CHAR", data_length=2, char_length=2, char_used="B"),
            col("C_C", "CHAR", data_length=8, char_length=2, char_used="C"),
            col("NV", "NVARCHAR2", data_length=20, char_length=10),
            col("NC", "NCHAR", data_length=4, char_length=2),
            col("N0", "NUMBER", data_length=22),
            col("NP", "NUMBER", data_length=22, precision=10, scale=0),
            col("NPS", "NUMBER", data_length=22, precision=Decimal(12), scale=Decimal(2)),
            col("NI", "NUMBER", data_length=22, scale=0),
            col("F", "FLOAT", data_length=22, precision=126),
            col("BF", "BINARY_FLOAT", data_length=4),
            col("BD", "BINARY_DOUBLE", data_length=8),
            col("D", "DATE", data_length=7, nullable="N"),
            col("TS", "TIMESTAMP(6)", data_length=11, scale=6),
            col("TS0", "TIMESTAMP(0)", data_length=7, scale=0),
        ],
    )
    assert stmt.kind == "create"
    assert stmt.text == (
        'CREATE TABLE "TT"."MUSTERI" ('
        '"V_B" VARCHAR2(40 BYTE) NOT NULL, "V_C" VARCHAR2(10 CHAR), "C_B" CHAR(2 BYTE), '
        '"C_C" CHAR(2 CHAR), "NV" NVARCHAR2(10), "NC" NCHAR(2), "N0" NUMBER, "NP" NUMBER(10), '
        '"NPS" NUMBER(12,2), "NI" NUMBER(*,0), "F" FLOAT(126), "BF" BINARY_FLOAT, '
        '"BD" BINARY_DOUBLE, "D" DATE NOT NULL, "TS" TIMESTAMP(6), "TS0" TIMESTAMP(0))'
    )
    assert "DEFAULT" not in stmt.text and "IDENTITY" not in stmt.text
    assert t.is_trusted(stmt)


@pytest.mark.parametrize(
    "bad",
    [
        col("X", "CLOB"),
        col("X", "BLOB"),
        col("X", "LONG"),
        col("X", "RAW", data_length=16),
        col("X", "TIMESTAMP(6) WITH TIME ZONE", scale=6),
        col("X", "XMLTYPE"),
        col("X", "varchar2", data_length=10, char_used="B"),
        col("X", "VARCHAR2", data_length=10, char_used=None),
        col("X", "VARCHAR2", data_length=0, char_used="B"),
        col("X", "VARCHAR2", data_length=1.5, char_used="B"),
        col("X", "NUMBER", precision=39),
        col("X", "DATE", nullable="X"),
    ],
)
def test_create_table_rejects_unsupported(bad):
    with pytest.raises(ValueError):
        t.create_table("TT", "T", [bad])


def test_create_table_rejects_empty_and_duplicates():
    with pytest.raises(ValueError):
        t.create_table("TT", "T", [])
    with pytest.raises(ValueError):
        t.create_table("TT", "T", [col("A", "DATE"), col("A", "DATE")])


def test_insert_rows_positional():
    stmt = t.insert_rows("TT", "T", ["A", "B", "C"])
    assert stmt.kind == "insert"
    assert stmt.text == 'INSERT INTO "TT"."T" ("A", "B", "C") VALUES (:1, :2, :3)'
    with pytest.raises(ValueError):
        t.insert_rows("TT", "T", [])


@pytest.mark.parametrize("limit,offset", [(0, 0), (10, -1), (True, 0), ("10", 0)])
def test_paging_validation(limit, offset):
    with pytest.raises(ValueError):
        t.dict_schemas(None, limit, offset)
