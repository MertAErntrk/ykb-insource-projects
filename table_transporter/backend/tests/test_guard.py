import pytest

from app.oracle import sql_templates as t
from app.oracle.guard import FORBIDDEN_WORDS, ForbiddenSQL, assert_allowed


def _rejected(sql):
    with pytest.raises(ForbiddenSQL):
        assert_allowed(sql)


@pytest.mark.parametrize("word", sorted(FORBIDDEN_WORDS))
def test_each_forbidden_word_rejected_anywhere(word):
    _rejected(f"SELECT A FROM {word}")
    _rejected(f"SELECT A FROM T WHERE B = :b AND {word.lower()} = :c")
    _rejected(f"{word} TABLE X")


@pytest.mark.parametrize(
    "word",
    [
        "DROP", "TRUNCATE", "DELETE", "ALTER", "UPDATE", "MERGE", "RENAME", "PURGE", "GRANT",
        "REVOKE", "BEGIN", "DECLARE", "CALL", "EXEC", "EXECUTE", "IMMEDIATE", "FLASHBACK",
        "COMMENT", "LOCK", "AUDIT", "NOAUDIT", "ANALYZE", "REPLACE", "UPSERT", "SHUTDOWN",
    ],
)
def test_contract_words_present(word):
    assert word in FORBIDDEN_WORDS


@pytest.mark.parametrize("name", ["DBMS_LOCK", "dbms_sql", "UTL_HTTP", "SYS.DBMS_RANDOM", '"DBMS_LOCK"'])
def test_package_prefixes_rejected(name):
    _rejected(f"SELECT {name} FROM DUAL")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE X",
        "SELECT 1 FROM DUAL;",
        "/* x */ DROP TABLE X",
        "SELECT 1 FROM DUAL /* x */",
        "SELECT 1 FROM DUAL */",
        "--\nDROP TABLE X",
        "SELECT 1 FROM DUAL -- x",
        " DROP TABLE X",
        " DROP TABLE X",
        "　  DROP TABLE X",
        "SELECT 1 FROM DUAL ; DROP TABLE X",
        "drop table x",
        "Drop Table X",
        "WITH x AS (SELECT 1 FROM DUAL) DELETE FROM T",
        "WITH x AS (SELECT 1 FROM DUAL) INSERT INTO \"S\".\"T\" (\"A\") VALUES (:1)",
        "CREATE TABLE \"S\".\"T\" AS SELECT * FROM \"S\".\"U\"",
        "CREATE TABLE \"S\".\"T\" (\"A\") AS SELECT A FROM U",
        "CREATE OR REPLACE VIEW V AS SELECT 1 FROM DUAL",
        "CREATE OR REPLACE TRIGGER X",
        "CREATE USER X",
        "CREATE INDEX I ON T (A)",
        "INSERT ALL INTO \"S\".\"T\" (\"A\") VALUES (:1) SELECT 1 FROM DUAL",
        "INSERT FIRST INTO \"S\".\"T\" (\"A\") VALUES (:1)",
        "INSERT INTO \"S\".\"T\" (\"A\") SELECT A FROM U",
        "INSERT INTO \"S\".\"T\" (\"A\", \"B\") VALUES (:1)",
        "INSERT INTO \"T\" (\"A\") VALUES (:1)",
        "INSERT INTO \"S\".\"T\" (\"A\") VALUES (:1) RETURNING A INTO :2",
        "SELECT A FROM T FOR UPDATE",
        "SELECT A FROM T FOR UPDATE NOWAIT",
        "SELECT A FROM T WHERE B = 'literal'",
        "SELECT q'[x]' FROM DUAL",
        "SELECT N'x' FROM DUAL",
        "SELECT A FROM T@REMOTE",
        "SELECT EVIL() FROM DUAL",
        "SELECT SYS.EVIL(1) FROM DUAL",
        "SELECT \"EVIL\"(1) FROM DUAL",
        "WITH FUNCTION F RETURN NUMBER IS BEGIN RETURN 1 END SELECT F FROM DUAL",
        "SELECT 1 FROM DUAL\x00",
        "SELECT 1 FROM\x0bDUAL",
        "DR​OP TABLE X",
        "SELECT \"lower\" FROM DUAL",
        "SELECT \"A B\" FROM DUAL",
        "SELECT \"A\"\"B\" FROM DUAL",
        "SELECT \"A FROM DUAL",
        "SELECT (1 FROM DUAL",
        "SELECT 1) FROM (DUAL",
        "SELECT ； FROM DUAL",
        "SELECT ı FROM DUAL",
        "",
        "   ",
        "EXPLAIN PLAN FOR SELECT 1 FROM DUAL",
        ":1",
        "(SELECT 1 FROM DUAL)",
    ],
)
def test_rejected(sql):
    _rejected(sql)


def test_non_str_rejected():
    _rejected(b"SELECT 1 FROM DUAL")
    _rejected(None)


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT "DROP_FLAG", "UPDATED_AT" FROM "S"."T" WHERE "DELETE_TS" IS NULL',
        "SELECT 1 FROM DUAL",
        "select username from all_users",
        "WITH X AS (SELECT 1 AS A FROM DUAL), Y AS (SELECT A FROM X) SELECT * FROM Y",
        'INSERT INTO "S"."T" ("A", "B") VALUES (:1, :2)',
        'CREATE TABLE "S"."T" ("A" VARCHAR2(10 BYTE) NOT NULL, "B" NUMBER(*,0))',
        "SELECT 1 FROM\tDUAL\r\n",
        "SELECT DROPPED FROM ALL_TABLES",
    ],
)
def test_allowed(sql):
    assert_allowed(sql)


def _all_template_outputs():
    cols = [
        t.ColumnDef("A", "VARCHAR2", 40, 10, "C", None, None, "N"),
        t.ColumnDef("B", "NUMBER", 22, 0, None, 10, 2, "Y"),
        t.ColumnDef("C", "TIMESTAMP(6)", 11, 0, None, None, 6, "Y"),
    ]
    out = [
        t.dict_schemas("ar", 50, 0)[0],
        t.dict_tables("DEMO", None, 50, 10)[0],
        t.dict_columns("DEMO", "MUSTERI")[0],
        t.dict_unique_columns("DEMO", "MUSTERI")[0],
        t.dict_triggers("DEMO", "MUSTERI")[0],
        t.dict_schema_triggers("DEMO")[0],
        t.dict_object("DEMO", "MUSTERI")[0],
        t.dba_object("DEMO", "MUSTERI")[0],
        t.count_rows("DEMO", "MUSTERI", "", {}, 5_000_001)[0],
        t.count_rows("DEMO", "MUSTERI", '"DROP_FLAG" = :f0', {"f0": 1}, 11)[0],
        t.select_rows("DEMO", "MUSTERI", ["A", "DROP_FLAG"], '"A" IN (:f0, :f1)', {"f0": 1, "f1": 2}, 10)[0],
        t.create_table("TT", "MUSTERI", cols),
        t.insert_rows("TT", "MUSTERI", ["A", "B", "C"]),
    ]
    return out


def test_all_template_outputs_pass_guard():
    for stmt in _all_template_outputs():
        assert_allowed(stmt.text)
        assert t.is_trusted(stmt)
