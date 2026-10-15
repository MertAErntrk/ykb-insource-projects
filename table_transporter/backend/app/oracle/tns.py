"""tnsnames.ora ayrıştırıcı (salt okunur; oracledb import edilmez).

IFILE satırları güvenlik gereği izlenmez. Bozuk giriş yalnız kendi alias'ını düşürür.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, project_path
from app.errors import AppError, ErrorCode

log = logging.getLogger(__name__)

_ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.$#-]*$")
# Satır başındaki yeni giriş başlığı (dengesiz parantez sonrası toparlanma için).
_HEADER_AT_COL0 = re.compile(r"[A-Za-z][^\s=()]*(?:\s*,\s*[^\s=()]+)*\s*=")
_KEY_RE = re.compile(r"\((\w+)=")


@dataclass(frozen=True)
class TnsEntry:
    alias: str
    host: str | None
    port: int | None
    service_name: str | None
    sid: str | None
    descriptor: str


def _strip_comments(text: str) -> str:
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = re.search(r"(?:^|\s)#", line)
        lines.append(line[: m.start()] if m else line)
    return "\n".join(lines)


def _skip_line(text: str, pos: int) -> int:
    nl = text.find("\n", pos)
    return len(text) if nl < 0 else nl + 1


def _first(pattern: str, descriptor: str) -> str | None:
    m = re.search(pattern, descriptor)
    return m.group(1) if m and m.group(1) else None


def _build(alias: str, block: str) -> TnsEntry | None:
    descriptor = _KEY_RE.sub(lambda m: f"({m.group(1).upper()}=", re.sub(r"\s+", "", block))
    if not _KEY_RE.match(descriptor):
        return None
    port_s = _first(r"\(PORT=([^()]*)\)", descriptor)
    return TnsEntry(
        alias=alias.upper(),
        host=_first(r"\(HOST=([^()]*)\)", descriptor),
        port=int(port_s) if port_s and port_s.isdigit() else None,
        service_name=_first(r"\(SERVICE_NAME=([^()]*)\)", descriptor),
        sid=_first(r"\(SID=([^()]*)\)", descriptor),
        descriptor=descriptor,
    )


def parse_tnsnames(text: str) -> dict[str, TnsEntry]:
    text = _strip_comments(text.lstrip("﻿"))
    result: dict[str, TnsEntry] = {}
    n = len(text)
    pos = 0
    while pos < n:
        if text[pos].isspace():
            pos += 1
            continue
        eq = text.find("=", pos)
        nl = text.find("\n", pos)
        par = text.find("(", pos)
        if eq < 0 or (0 <= nl < eq) or (0 <= par < eq):
            log.warning("tnsnames: '=' eksik ya da geçersiz satır atlandı")
            pos = _skip_line(text, pos)
            continue
        header = text[pos:eq].strip()
        pos = eq + 1
        if header.upper() == "IFILE":
            log.warning("tnsnames: IFILE izlenmiyor (güvenlik), atlandı")
            pos = _skip_line(text, pos)
            continue
        while pos < n and text[pos].isspace():
            pos += 1
        if pos >= n or text[pos] != "(":
            log.warning("tnsnames: '%s' için tanım parantezle başlamıyor, atlandı", header)
            pos = _skip_line(text, pos)
            continue
        start, depth, ok = pos, 0, False
        while pos < n:
            ch = text[pos]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    pos += 1
                    ok = True
                    break
            elif ch == "\n" and _HEADER_AT_COL0.match(text, pos + 1):
                break  # sonraki giriş başladı; bu giriş dengesiz
            pos += 1
        if not ok:
            log.warning("tnsnames: '%s' için parantezler dengesiz, atlandı", header)
            continue
        block = text[start:pos]
        rest_end = text.find("\n", pos)
        rest = text[pos : n if rest_end < 0 else rest_end]
        if rest.strip():
            log.warning("tnsnames: '%s' tanımından sonra fazlalık var, atlandı", header)
            pos = _skip_line(text, pos)
            continue
        aliases = [a.strip() for a in header.split(",")]
        for alias in aliases:
            if not _ALIAS_RE.match(alias):
                log.warning("tnsnames: geçersiz alias adı atlandı: %r", alias)
                continue
            entry = _build(alias, block)
            if entry is None:
                log.warning("tnsnames: '%s' tanımı çözümlenemedi, atlandı", alias)
                continue
            if entry.alias in result:
                log.warning("tnsnames: '%s' birden çok tanımlı, sonuncu kullanılacak", entry.alias)
            result[entry.alias] = entry
    return result


_cache: dict[Path, tuple[int, dict[str, TnsEntry]]] = {}


def load_tns(settings: Settings) -> dict[str, TnsEntry]:
    path = project_path(settings.tt_tns_admin) / "tnsnames.ora"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        log.warning("tnsnames.ora bulunamadı: %s", path)
        _cache.pop(path, None)
        return {}
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    entries = parse_tnsnames(path.read_text(encoding="utf-8-sig"))
    _cache[path] = (mtime, entries)
    return entries


def is_target_allowed(settings: Settings, alias: str) -> bool:
    return alias.upper() not in {a.upper() for a in settings.tt_target_deny_aliases}


def list_aliases(settings: Settings, q: str | None = None) -> list[dict]:
    needle = q.strip().upper() if q else ""
    return [
        {
            "alias": e.alias,
            "host": e.host,
            "port": e.port,
            "service_name": e.service_name,
            "target_allowed": is_target_allowed(settings, e.alias),
        }
        for _, e in sorted(load_tns(settings).items())
        if needle in e.alias
    ]


def get_entry(settings: Settings, alias: str) -> TnsEntry:
    entry = load_tns(settings).get(alias.strip().upper())
    if entry is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Bağlantı adı tnsnames.ora'da bulunamadı: {alias}")
    return entry
