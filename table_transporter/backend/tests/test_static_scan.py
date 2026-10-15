"""backend/app altındaki kaynak kodun AST taraması (silmeme garantisi, katman 5)."""

import ast
import re
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"

ORACLEDB_ALLOWED = {"oracle/safe_connection.py", "oracle/pools.py"}
SQL_WORDS_ALLOWED = {"oracle/guard.py", "cleanup/drop_script.py"}
DROP_SCRIPT = "cleanup/drop_script.py"
# Yalnız SQLite log deposu UPDATE ... SET kullanabilir (Oracle'a erişemez: aşağıdaki import testi).
SQLITE_UPDATE_ALLOWED = {"log/repository.py"}
SQLITE_UPDATE_RE = re.compile(r"\bUPDATE\s+\S+\s+SET\b", re.IGNORECASE)

# SQL bağlamı: sözcük tek başına değil, ardından SQL yapısı gelirse yakalanır
# (Türkçe metindeki "DELETE" ya da "drop-down" gibi sözcükler yanlış pozitif vermez).
FORBIDDEN_SQL_RE = re.compile(
    r"\bDROP\s+(TABLE|VIEW|USER|INDEX|SYNONYM|SEQUENCE|PROCEDURE|FUNCTION|PACKAGE|TRIGGER"
    r"|TABLESPACE|DATABASE|SCHEMA|PUBLIC|TYPE|MATERIALIZED|CONSTRAINT|COLUMN|PARTITION)\b"
    r"|\bTRUNCATE\s+(TABLE|CLUSTER)\b"
    r"|\bDELETE\s+FROM\b"
    r"|\bALTER\s+(TABLE|USER|SYSTEM|SESSION|DATABASE|INDEX|VIEW|TRIGGER)\b"
    r"|\bUPDATE\s+\S+\s+SET\b"
    r"|\bMERGE\s+INTO\b"
    r"|\bPURGE\s+(TABLE|INDEX|RECYCLEBIN|DBA_RECYCLEBIN|TABLESPACE|USER_RECYCLEBIN)\b"
    r"|\bEXECUTE\s+IMMEDIATE\b",
    re.IGNORECASE,
)


def _sources() -> list[tuple[str, ast.Module]]:
    out = []
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = path.relative_to(APP_DIR).as_posix()
        out.append((rel, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    return out


def _module_of(rel: str) -> list[str]:
    parts = ["app", *rel[:-3].split("/")]
    if parts[-1] == "__init__":
        parts.pop()
    return parts


def _imported_modules(rel: str, tree: ast.AST) -> list[str]:
    """Mutlak modül adları (göreli importlar çözülür) + import edilen adlar."""
    names = []
    pkg = _module_of(rel)
    if not rel.endswith("__init__.py"):
        pkg = pkg[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                prefix = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                base = ".".join([*prefix, *([base] if base else [])])
            names.append(base)
            names.extend(f"{base}.{a.name}" for a in node.names)
            names.extend(a.name for a in node.names)
    return names


def _string_constants(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            # f-string: sabit parçaları yer tutucuyla birleştirilmiş hali de taranır
            out.append(
                "".join(v.value if isinstance(v, ast.Constant) else " X " for v in node.values)
            )
    return out


def _sql_violations(tree: ast.AST, allow_update: bool = False) -> list[str]:
    out = []
    for s in _string_constants(tree):
        if allow_update:
            s = SQLITE_UPDATE_RE.sub(" ", s)
        if FORBIDDEN_SQL_RE.search(s):
            out.append(s)
    return out


def _call_violations(tree: ast.AST) -> list[str]:
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("callproc", "callfunc"):
            bad.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in ("__import__", "importlib"):
            bad.append(node.id)
        elif isinstance(node, ast.Import) and any(a.name.split(".")[0] == "importlib" for a in node.names):
            bad.append("import importlib")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "importlib":
            bad.append("from importlib")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if len(node.args) < 2 or not (
                isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
            ):
                bad.append("getattr(değişken)")
        elif isinstance(node, ast.Attribute) and node.attr == "__import__":
            bad.append("__import__")
    return bad


def _imports_oracledb(rel: str, tree: ast.AST) -> bool:
    return any(m == "oracledb" or m.startswith("oracledb.") for m in _imported_modules(rel, tree))


SOURCES = _sources()


def test_app_sources_found():
    rels = {rel for rel, _ in SOURCES}
    assert "oracle/guard.py" in rels and "oracle/sql_templates.py" in rels


@pytest.mark.parametrize("rel,tree", SOURCES, ids=[r for r, _ in SOURCES])
def test_oracledb_import_only_in_driver_modules(rel, tree):
    if rel not in ORACLEDB_ALLOWED:
        assert not _imports_oracledb(rel, tree), f"{rel} oracledb import ediyor"


@pytest.mark.parametrize("rel,tree", SOURCES, ids=[r for r, _ in SOURCES])
def test_no_dynamic_calls(rel, tree):
    assert _call_violations(tree) == [], rel


@pytest.mark.parametrize("rel,tree", SOURCES, ids=[r for r, _ in SOURCES])
def test_forbidden_sql_words_only_in_allowed_files(rel, tree):
    if rel not in SQL_WORDS_ALLOWED:
        assert _sql_violations(tree, allow_update=rel in SQLITE_UPDATE_ALLOWED) == [], rel


def test_sqlite_log_modules_never_touch_oracle():
    trees = dict(SOURCES)
    for rel in [r for r in trees if r.startswith("log/")]:
        for m in _imported_modules(rel, trees[rel]):
            assert not (m == "app.oracle" or m.startswith("app.oracle.")), (rel, m)
            assert not (m == "oracledb" or m.startswith("oracledb.")), (rel, m)


def test_sqlite_update_exception_still_blocks_delete():
    tree = ast.parse('s = "DELETE FROM jobs WHERE id=?"\nt = "UPDATE jobs SET a=?"')
    assert _sql_violations(tree, allow_update=True) == ["DELETE FROM jobs WHERE id=?"]


def test_drop_script_imports_no_db_module():
    trees = dict(SOURCES)
    if DROP_SCRIPT not in trees:
        pytest.skip("cleanup/drop_script.py henüz yok")
    mods = _imported_modules(DROP_SCRIPT, trees[DROP_SCRIPT])
    for m in mods:
        assert not (m == "app.oracle" or m.startswith("app.oracle.")), m
        assert not (m == "oracledb" or m.startswith("oracledb.")), m
        assert "SafeConnection" not in m, m


# --- Tarayıcının kendisi: yakalaması gereken ve yanlış pozitif vermemesi gereken örnekler ---


@pytest.mark.parametrize(
    "src",
    [
        's = "DROP TABLE X"',
        's = "drop  table x"',
        's = f"DROP TABLE {name}"',
        's = f"TRUNCATE TABLE {s}.{t}"',
        's = "DELETE FROM T"',
        's = f"ALTER TABLE {t} ADD X"',
        's = "UPDATE T SET A = 1"',
        's = f"UPDATE {t} SET A = :1"',
        's = "MERGE INTO T"',
        's = "PURGE RECYCLEBIN"',
        's = "BEGIN EXECUTE IMMEDIATE x; END;"',
        's = ("DROP " "TABLE X")',
    ],
)
def test_scanner_catches_sql(src):
    assert _sql_violations(ast.parse(src))


@pytest.mark.parametrize(
    "src",
    [
        's = "Kayıt silinemez (DELETE yetkisi yok)."',
        's = "Açılır menü (drop-down) seçimi"',
        's = "Son güncelleme: UPDATED_AT"',
        's = "Tabloyu DROP etmek yasaktır."',
        's = "ALTER ego"',
        's = "DROPPED kolonu"',
    ],
)
def test_scanner_no_false_positive(src):
    assert not _sql_violations(ast.parse(src))


@pytest.mark.parametrize(
    "src",
    [
        "cur.callproc('x')",
        "cur.callfunc('x', int)",
        "getattr(obj, name)",
        "import importlib",
        "from importlib import import_module",
        "__import__('os')",
        "builtins.__import__('os')",
    ],
)
def test_scanner_catches_dynamic_calls(src):
    assert _call_violations(ast.parse(src))


def test_scanner_allows_constant_getattr():
    assert not _call_violations(ast.parse("getattr(obj, 'x', None)"))


def test_import_resolution():
    tree = ast.parse("from ..oracle import guard\nimport oracledb\nfrom oracledb import x")
    mods = _imported_modules(DROP_SCRIPT, tree)
    assert "app.oracle" in mods and "oracledb" in mods
    assert _imports_oracledb("transfer/x.py", tree)
