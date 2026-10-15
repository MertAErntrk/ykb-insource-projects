"""SQL izin listesi denetimi (silmeme garantisinin son kapısı).

Yalnız tek bir SELECT, WITH ... SELECT, kolon tanımlı CREATE TABLE ya da
INSERT INTO "S"."T" (...) VALUES (:1, ...) kabul edilir. Literal ve yorum yoktur:
tüm değerler bind ile gelir. Bilinmeyen her karakter/yapı reddedilir (izin listesi).
"""

import re
import unicodedata

from app.oracle.identifiers import IDENT_RE


class ForbiddenSQL(Exception):
    pass


FORBIDDEN_WORDS = frozenset(
    {
        "DROP", "TRUNCATE", "DELETE", "ALTER", "UPDATE", "MERGE", "RENAME", "PURGE",
        "GRANT", "REVOKE", "BEGIN", "DECLARE", "CALL", "EXEC", "EXECUTE", "IMMEDIATE",
        "FLASHBACK", "COMMENT", "LOCK", "AUDIT", "NOAUDIT", "ANALYZE", "REPLACE",
        "UPSERT", "SHUTDOWN",
        # Sözleşmedekilere ek: satır içi PL/SQL (WITH FUNCTION ...) kapısı.
        "FUNCTION", "PROCEDURE", "PACKAGE",
    }
)
FORBIDDEN_PREFIXES = ("DBMS_", "UTL_")
FORBIDDEN_SEQUENCES = ("--", "/*", "*/", ";", "'")
FIRST_WORDS = frozenset({"SELECT", "WITH", "INSERT", "CREATE"})
# Sorguda '(' öncesinde yalnız bunlar gelebilir: kullanıcı/paket fonksiyonu çağrısı yok.
CALL_WORDS = frozenset(
    {
        "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "EXISTS", "AS", "ON", "JOIN",
        "UNION", "COUNT", "UPPER", "INSTR", "TRIM", "NVL", "COALESCE", "LENGTH",
    }
)

_IDENT = re.compile(IDENT_RE)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_$#]*")
_BIND = re.compile(r":[A-Za-z0-9_]+")
_NUM = re.compile(r"[0-9]+")
_QUOTED = re.compile(r'"([^"]*)"')
_PUNCT = frozenset("(),.*=<>!+-/")

# Token türleri: W kelime (büyük harfe çevrilmiş), Q tırnaklı tanımlayıcı, B bind, N sayı, P noktalama
Token = tuple[str, str]


def _normalize(sql: str) -> str:
    out = []
    for ch in sql:
        cat = unicodedata.category(ch)
        if ch in "\t\n\r" or (ch.isspace() and cat != "Cc"):
            out.append(" ")
        elif cat[0] == "C":
            raise ForbiddenSQL("SQL kontrol/biçim karakteri içeriyor.")
        else:
            out.append(ch)
    return "".join(out)


def _tokenize(text: str) -> list[Token]:
    toks: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == " ":
            i += 1
            continue
        if ch == '"':
            m = _QUOTED.match(text, i)
            # "A""B" Oracle'da tek tanımlayıcıdır (kaçışlı tırnak): bitişik tırnak reddedilir.
            if not m or not _IDENT.fullmatch(m.group(1)) or text.startswith('"', m.end()):
                raise ForbiddenSQL("Geçersiz tırnaklı tanımlayıcı.")
            toks.append(("Q", m.group(1)))
            i = m.end()
            continue
        for kind, rx in (("W", _WORD), ("B", _BIND), ("N", _NUM)):
            m = rx.match(text, i)
            if m:
                val = m.group(0)
                toks.append((kind, val.upper() if kind == "W" else val))
                i = m.end()
                break
        else:
            if ch not in _PUNCT:
                raise ForbiddenSQL("SQL izin verilmeyen karakter içeriyor.")
            toks.append(("P", ch))
            i += 1
    return toks


def _skip_parens(toks: list[Token], i: int) -> int:
    """toks[i] '(' olmalı; eşleşen ')' sonrasındaki indeksi döner."""
    if i >= len(toks) or toks[i] != ("P", "("):
        raise ForbiddenSQL("Parantez bekleniyordu.")
    depth = 0
    for j in range(i, len(toks)):
        if toks[j] == ("P", "("):
            depth += 1
        elif toks[j] == ("P", ")"):
            depth -= 1
            if depth == 0:
                return j + 1
    raise ForbiddenSQL("Parantezler dengesiz.")


def _is_ident(tok: Token) -> bool:
    return tok[0] in ("Q", "W")


def _check_with(toks: list[Token]) -> None:
    i, n = 1, len(toks)
    while True:
        if i >= n or not _is_ident(toks[i]):
            raise ForbiddenSQL("WITH adı bekleniyordu.")
        i += 1
        if i < n and toks[i] == ("P", "("):
            i = _skip_parens(toks, i)
        if i >= n or toks[i] != ("W", "AS"):
            raise ForbiddenSQL("WITH ... AS bekleniyordu.")
        i = _skip_parens(toks, i + 1)
        if i < n and toks[i] == ("P", ","):
            i += 1
            continue
        break
    if i >= n or toks[i] != ("W", "SELECT"):
        raise ForbiddenSQL("WITH yalnız SELECT ile bitebilir.")


def _check_insert(toks: list[Token]) -> None:
    words = {v for k, v in toks if k == "W"}
    if words & {"ALL", "FIRST", "SELECT"}:
        raise ForbiddenSQL("INSERT ALL/FIRST/SELECT yasak.")
    n = len(toks)

    def expect(i: int, tok: Token) -> int:
        if i >= n or toks[i] != tok:
            raise ForbiddenSQL("INSERT biçimi geçersiz.")
        return i + 1

    def ident_list(i: int, kinds: tuple[str, ...]) -> tuple[int, int]:
        i = expect(i, ("P", "("))
        count = 0
        while True:
            if i >= n or toks[i][0] not in kinds:
                raise ForbiddenSQL("INSERT biçimi geçersiz.")
            i += 1
            count += 1
            if i < n and toks[i] == ("P", ","):
                i += 1
                continue
            return expect(i, ("P", ")")), count

    i = expect(1, ("W", "INTO"))
    if i + 2 >= n or not _is_ident(toks[i]) or toks[i + 1] != ("P", ".") or not _is_ident(toks[i + 2]):
        raise ForbiddenSQL("INSERT hedefi şema.tablo olmalı.")
    i, ncols = ident_list(i + 3, ("Q", "W"))
    i = expect(i, ("W", "VALUES"))
    i, nbinds = ident_list(i, ("B",))
    if i != n or ncols != nbinds:
        raise ForbiddenSQL("INSERT biçimi geçersiz.")


def _check_create(toks: list[Token]) -> None:
    if len(toks) < 2 or toks[1] != ("W", "TABLE"):
        raise ForbiddenSQL("Yalnız CREATE TABLE izinli.")
    if any(k == "W" and v in ("SELECT", "AS") for k, v in toks):
        raise ForbiddenSQL("CREATE TABLE ... AS SELECT yasak.")
    if (
        len(toks) < 6
        or not _is_ident(toks[2])
        or toks[3] != ("P", ".")
        or not _is_ident(toks[4])
        or _skip_parens(toks, 5) != len(toks)
    ):
        raise ForbiddenSQL("CREATE TABLE biçimi geçersiz.")


def assert_allowed(sql: str) -> None:
    """İzin listesinde olmayan her SQL için ForbiddenSQL fırlatır."""
    if not isinstance(sql, str):
        raise ForbiddenSQL("SQL metin olmalı.")
    text = _normalize(sql)
    for seq in FORBIDDEN_SEQUENCES:
        if seq in text:
            raise ForbiddenSQL("SQL yorum, ';' ya da literal içeremez.")
    toks = _tokenize(text)
    if not toks:
        raise ForbiddenSQL("Boş SQL.")

    depth = 0
    for kind, val in toks:
        if (kind == "W" and val in FORBIDDEN_WORDS) or (
            kind in ("W", "Q") and val.upper().startswith(FORBIDDEN_PREFIXES)
        ):
            raise ForbiddenSQL("SQL yasak bir sözcük içeriyor.")
        if (kind, val) == ("P", "("):
            depth += 1
        elif (kind, val) == ("P", ")"):
            depth -= 1
            if depth < 0:
                raise ForbiddenSQL("Parantezler dengesiz.")
    if depth != 0:
        raise ForbiddenSQL("Parantezler dengesiz.")

    kind, first = toks[0]
    if kind != "W" or first not in FIRST_WORDS:
        raise ForbiddenSQL("İlk sözcük SELECT, WITH, INSERT ya da CREATE olmalı.")
    if first in ("SELECT", "WITH"):
        if any(k == "W" and v in ("INSERT", "CREATE") for k, v in toks):
            raise ForbiddenSQL("Sorgu içinde INSERT/CREATE yasak.")
        for prev, nxt in zip(toks, toks[1:]):
            if nxt == ("P", "(") and (prev[0] == "Q" or (prev[0] == "W" and prev[1] not in CALL_WORDS)):
                raise ForbiddenSQL("Sorguda izin verilmeyen fonksiyon çağrısı.")
        if first == "WITH":
            _check_with(toks)
    elif first == "INSERT":
        _check_insert(toks)
    else:
        _check_create(toks)
