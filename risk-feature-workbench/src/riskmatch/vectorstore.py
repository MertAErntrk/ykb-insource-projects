"""Persistent local vector cache keyed by model fingerprint.

The bank embedding model has no reliable identity/version, so vectors are
grouped into namespaces identified by how the model embeds a fixed set of
canary texts. A changed model yields a new namespace and old vectors are never
mixed with new queries. Only text hashes are stored, never raw text.

Storage: int8 quantized vectors + scale, plus a dims-bit sign signature used as
an in-memory coarse (Hamming) index; candidates are rescored with int8 cosine.
Stdlib only (sqlite3, array, hashlib).
"""

import hashlib
import math
import operator
import re
import sqlite3
import threading
import time
from array import array
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


DEFAULT_CANARIES = (
    "Son 3 ay ortalama mevduat bakiyesi",
    "Average deposit balance last 3 months",
    "Son 12 ay ortalama mevduat bakiyesi",
    "Kredi kartı gecikme gün sayısı",
    "Şube personelinin yıllık izin planı",
    "Weather forecast for the coastal region",
    "MUS_YAS",
    "Müşterinin rapor tarihindeki yaşı, yıl cinsinden",
)

SAME_MIN_COS = 0.995
SAME_MAX_GRAM_DELTA = 0.01
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_CHUNK = 500


class VectorStoreError(Exception):
    """sqlite/disk/lock failure. Message never contains paths, texts or secrets."""


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _wrap(op: str, exc: Exception) -> VectorStoreError:
    msg = str(exc).lower()
    if "locked" in msg or "busy" in msg:
        kind = "database locked"
    elif "full" in msg:
        kind = "disk full"
    elif "readonly" in msg or "read-only" in msg:
        kind = "read-only database"
    else:
        kind = type(exc).__name__
    return VectorStoreError("vector store %s failed: %s" % (op, kind))


def _normalized(vector, dims: Optional[int] = None) -> List[float]:
    try:
        values = [float(x) for x in vector]
    except (TypeError, ValueError):
        raise ValueError("vector must be a sequence of numbers") from None
    if dims is not None and len(values) != dims:
        raise ValueError("vector has %d dimensions, expected %d" % (len(values), dims))
    if not values:
        raise ValueError("vector is empty")
    if not all(math.isfinite(x) for x in values):
        raise ValueError("vector contains non-finite values")
    norm = math.sqrt(math.fsum(x * x for x in values))
    if norm == 0.0 or not math.isfinite(norm):
        raise ValueError("vector norm is zero")
    return [x / norm for x in values]


def _dot(a, b) -> float:
    return sum(map(operator.mul, a, b))


def _quantize(unit: List[float]) -> Tuple[float, bytes, int]:
    peak = max(abs(x) for x in unit)
    scale = peak / 127.0
    inv = 127.0 / peak
    q = array("b", [int(round(x * inv)) for x in unit])
    sig = int("".join("1" if x >= 0 else "0" for x in unit), 2)
    return scale, q.tobytes(), sig


def _sig_bytes(sig: int, dims: int) -> bytes:
    return sig.to_bytes((dims + 7) // 8, "big")


def _check_hash(value: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError("key must be a sha256 hex text hash")
    return value


def _chunks(seq: List, size: int = _CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _gram(vectors: List[Sequence[float]]) -> List[List[float]]:
    return [[_dot(a, b) for b in vectors] for a in vectors]


class Namespace:
    def __init__(self, store: "VectorStore", ns_id: str, dims: int, tainted: bool):
        self._store = store
        self.id = ns_id
        self.dims = dims
        self.tainted = tainted
        self._lock = threading.Lock()
        self._loaded = False
        self._hashes: List[str] = []
        self._sigs: List[int] = []
        self._pos: Dict[str, int] = {}

    # --- index -----------------------------------------------------------
    def _ensure_index(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            rows = self._store._read("load index", "SELECT h, sig FROM vectors WHERE ns = ?", (self.id,))
            for h, sig in rows:
                self._index_add(h, int.from_bytes(sig, "big"))
            self._loaded = True

    def _index_add(self, h: str, sig: int) -> None:
        pos = self._pos.get(h)
        if pos is None:
            self._pos[h] = len(self._hashes)
            self._sigs.append(sig)
            self._hashes.append(h)
        else:
            self._sigs[pos] = sig

    def _snapshot(self) -> Tuple[List[str], List[int]]:
        self._ensure_index()
        with self._lock:
            n = len(self._hashes)
            return self._hashes[:n], self._sigs[:n]

    # --- writes ----------------------------------------------------------
    def _prepare(self, h: str, vector) -> Tuple[str, float, bytes, int]:
        _check_hash(h)
        scale, q, sig = _quantize(_normalized(vector, self.dims))
        return h, scale, q, sig

    def put(self, text_hash: str, vector) -> None:
        self.put_many([(text_hash, vector)])

    def put_many(self, items: Iterable[Tuple[str, Sequence[float]]]) -> int:
        prepared = [self._prepare(h, v) for h, v in items]
        if not prepared:
            return 0
        rows = [(self.id, h, scale, q, _sig_bytes(sig, self.dims)) for h, scale, q, sig in prepared]
        self._store._write_many(
            "put", "INSERT OR REPLACE INTO vectors(ns, h, scale, q, sig) VALUES (?, ?, ?, ?, ?)", rows)
        with self._lock:
            if self._loaded:
                for h, _scale, _q, sig in prepared:
                    self._index_add(h, sig)
        return len(prepared)

    def mark_tainted(self) -> None:
        self._store._write_many("mark tainted", "UPDATE namespaces SET tainted = 1 WHERE id = ?", [(self.id,)])
        self.tainted = True

    # --- reads -----------------------------------------------------------
    def _fetch(self, op: str, hashes: Iterable[str], cols: str) -> List[tuple]:
        keys = list(dict.fromkeys(hashes))
        out: List[tuple] = []
        for chunk in _chunks(keys):
            marks = ",".join("?" * len(chunk))
            out.extend(self._store._read(
                op, "SELECT h, %s FROM vectors WHERE ns = ? AND h IN (%s)" % (cols, marks), [self.id] + chunk))
        return out

    def get_many(self, hashes: Iterable[str]) -> Dict[str, array]:
        result = {}
        for h, scale, q in self._fetch("get", hashes, "scale, q"):
            values = array("b", q)
            norm = math.sqrt(sum(x * x for x in values)) or 1.0
            result[h] = array("f", [x / norm for x in values])
        return result

    def has(self, hashes: Iterable[str]) -> Set[str]:
        return {row[0] for row in self._fetch("has", hashes, "1")}

    def count(self) -> int:
        rows = self._store._read("count", "SELECT COUNT(*) FROM vectors WHERE ns = ?", (self.id,))
        return int(rows[0][0])

    def coarse(self, query, k: int = 300, restrict: Optional[set] = None) -> List[Tuple[str, int]]:
        _scale, _q, qsig = _quantize(_normalized(query, self.dims))
        hashes, sigs = self._snapshot()
        scored = []
        for h, sig in zip(hashes, sigs):
            if restrict is not None and h not in restrict:
                continue
            scored.append((bin(sig ^ qsig).count("1"), h))
        scored.sort()
        return [(h, d) for d, h in scored[:max(0, k)]]

    def rescore(self, query, hashes: Iterable[str]) -> List[Tuple[str, float]]:
        unit = _normalized(query, self.dims)
        out = []
        for h, q in self._fetch("rescore", hashes, "q"):
            values = array("b", q)
            norm = math.sqrt(_dot(values, values))
            if norm:
                out.append((h, _dot(unit, values) / norm))
        out.sort(key=lambda item: (-item[1], item[0]))
        return out

    def search(self, query, k: int = 50, coarse_k: int = 300) -> List[Tuple[str, float]]:
        candidates = [h for h, _d in self.coarse(query, max(k, coarse_k))]
        return self.rescore(query, candidates)[:k]

    def sample(self, n: int, seed: str) -> List[str]:
        hashes, _sigs = self._snapshot()
        salt = str(seed).encode("utf-8")
        ranked = sorted(hashes, key=lambda h: hashlib.sha256(salt + b"|" + h.encode("ascii")).digest())
        return ranked[:max(0, n)]


class VectorStore:
    def __init__(self, path):
        self._path = Path(path)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise VectorStoreError("vector store open failed: %s" % type(exc).__name__) from None
        self._local = threading.local()
        self._conns: List[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._ns_lock = threading.Lock()
        self._namespaces: Dict[str, Namespace] = {}
        try:
            conn = self._conn()
            conn.execute("PRAGMA journal_mode=WAL")
            with self._write_lock, conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS namespaces (id TEXT PRIMARY KEY, dims INTEGER NOT NULL,"
                    " tainted INTEGER NOT NULL DEFAULT 0, canary_key TEXT NOT NULL, canaries BLOB NOT NULL,"
                    " created REAL NOT NULL, last_used REAL NOT NULL, use_seq INTEGER NOT NULL)")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS vectors (ns TEXT NOT NULL, h TEXT NOT NULL, scale REAL NOT NULL,"
                    " q BLOB NOT NULL, sig BLOB NOT NULL, PRIMARY KEY (ns, h)) WITHOUT ROWID")
        except sqlite3.Error as exc:
            raise _wrap("open", exc) from None

    # --- connection helpers ---------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._path), timeout=10.0, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
            with self._conns_lock:
                self._conns.append(conn)
        return conn

    def _read(self, op: str, sql: str, params=()) -> List[tuple]:
        try:
            return self._conn().execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise _wrap(op, exc) from None

    def _write_many(self, op: str, sql: str, rows: List[tuple]) -> None:
        try:
            conn = self._conn()
            with self._write_lock, conn:
                conn.executemany(sql, rows)
        except sqlite3.Error as exc:
            raise _wrap(op, exc) from None

    def close(self) -> None:
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    # --- fingerprint -----------------------------------------------------
    @staticmethod
    def _canary_key(canaries: Sequence[str]) -> str:
        return hashlib.sha256("\n".join(text_hash(c) for c in canaries).encode("ascii")).hexdigest()

    @staticmethod
    def _embed_canaries(embed: Callable[[str], Sequence[float]], canaries: Sequence[str]) -> List[List[float]]:
        if len(canaries) < 2:
            raise ValueError("at least two canaries are required")
        vectors = [_normalized(embed(text)) for text in canaries]
        dims = len(vectors[0])
        if any(len(v) != dims for v in vectors):
            raise ValueError("canary vectors have inconsistent dimensions")
        return vectors

    @staticmethod
    def _compare(new: List[List[float]], old: List[Sequence[float]]) -> Tuple[float, float]:
        min_cos = min(_dot(a, b) for a, b in zip(new, old))
        g_new, g_old = _gram(new), _gram(old)
        delta = max(abs(x - y) for ra, rb in zip(g_new, g_old) for x, y in zip(ra, rb))
        return min_cos, delta

    @staticmethod
    def _is_same(min_cos: float, delta: float) -> bool:
        return min_cos >= SAME_MIN_COS and delta <= SAME_MAX_GRAM_DELTA

    def _stored_canaries(self, blob: bytes, dims: int) -> List[array]:
        flat = array("f")
        flat.frombytes(blob)
        return [flat[i:i + dims] for i in range(0, len(flat), dims)]

    def _touch(self, ns_id: str) -> None:
        self._write_many(
            "touch", "UPDATE namespaces SET last_used = ?, use_seq = (SELECT COALESCE(MAX(use_seq), 0) + 1"
            " FROM namespaces) WHERE id = ?", [(time.time(), ns_id)])

    def fingerprint(self, embed, canaries=DEFAULT_CANARIES) -> dict:
        vectors = self._embed_canaries(embed, canaries)
        dims = len(vectors[0])
        key = self._canary_key(canaries)
        rows = self._read(
            "fingerprint", "SELECT id, canaries FROM namespaces WHERE dims = ? AND tainted = 0 AND canary_key = ?",
            (dims, key))
        best = None
        for ns_id, blob in rows:
            if blob is None or len(blob) != len(vectors) * dims * 4:
                continue  # damaged canary record: never compare, a fresh namespace is created instead
            min_cos, delta = self._compare(vectors, self._stored_canaries(blob, dims))
            rank = (self._is_same(min_cos, delta), min_cos)
            if best is None or rank > best[0]:
                best = (rank, ns_id, min_cos, delta)
        detail = {"min_cos": best[2] if best else None, "gram_delta": best[3] if best else None}
        if best is not None and best[0][0]:
            self._touch(best[1])
            return {"namespace": best[1], "status": "same", "dims": dims, "detail": detail}

        digest_src = ";".join(",".join("%.4f" % x for x in v) for v in vectors)
        ns_id = hashlib.sha256(digest_src.encode("ascii")).hexdigest()[:16]
        salt = 0
        while self._read("fingerprint", "SELECT 1 FROM namespaces WHERE id = ?", (ns_id,)):
            salt += 1
            ns_id = hashlib.sha256(("%s#%d" % (digest_src, salt)).encode("ascii")).hexdigest()[:16]
        flat = array("f", [x for v in vectors for x in v])
        now = time.time()
        self._write_many(
            "fingerprint",
            "INSERT INTO namespaces(id, dims, tainted, canary_key, canaries, created, last_used, use_seq)"
            " VALUES (?, ?, 0, ?, ?, ?, ?, (SELECT COALESCE(MAX(use_seq), 0) + 1 FROM namespaces))",
            [(ns_id, dims, key, flat.tobytes(), now, now)])
        return {"namespace": ns_id, "status": "new", "dims": dims, "detail": detail}

    def verify(self, namespace: str, embed, canaries=DEFAULT_CANARIES, sample=None) -> bool:
        """sample=n re-embeds only the first n (>= 2) canaries: a cheap periodic check of the same namespace."""
        rows = self._read("verify", "SELECT dims, canary_key, canaries FROM namespaces WHERE id = ?", (namespace,))
        if not rows:
            raise KeyError(namespace)
        dims, key, blob = rows[0]
        if key != self._canary_key(canaries):
            raise ValueError("canary set differs from the one used to create the namespace")
        stored = self._stored_canaries(blob, dims)
        if sample is not None:
            count = max(2, min(int(sample), len(canaries)))
            canaries, stored = canaries[:count], stored[:count]
        vectors = self._embed_canaries(embed, canaries)
        if len(vectors[0]) != dims:
            return False
        return self._is_same(*self._compare(vectors, stored))

    def namespace(self, ns_id: str) -> Namespace:
        with self._ns_lock:
            cached = self._namespaces.get(ns_id)
            if cached is not None:
                return cached
            rows = self._read("namespace", "SELECT dims, tainted FROM namespaces WHERE id = ?", (ns_id,))
            if not rows:
                raise KeyError(ns_id)
            ns = Namespace(self, ns_id, int(rows[0][0]), bool(rows[0][1]))
            self._namespaces[ns_id] = ns
            return ns

    def prune(self, keep: int = 2) -> List[str]:
        rows = self._read("prune", "SELECT id FROM namespaces ORDER BY use_seq DESC, last_used DESC")
        doomed = [r[0] for r in rows[max(0, keep):]]
        if not doomed:
            return []
        try:
            conn = self._conn()
            with self._write_lock, conn:
                for ns_id in doomed:
                    conn.execute("DELETE FROM vectors WHERE ns = ?", (ns_id,))
                    conn.execute("DELETE FROM namespaces WHERE id = ?", (ns_id,))
        except sqlite3.Error as exc:
            raise _wrap("prune", exc) from None
        with self._ns_lock:
            for ns_id in doomed:
                self._namespaces.pop(ns_id, None)
        return doomed
