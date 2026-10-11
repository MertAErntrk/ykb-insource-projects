"""Scope-first exact, BM25 and embedding retrieval with reciprocal rank fusion."""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

from riskmatch.errors import InputError, IntegrationError, StaleIndexError


def _fold(value):
    value = str(value or "").replace("İ", "I").replace("ı", "i")
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _identifier_key(value):
    """Case-insensitive physical identifier check without accent conflation."""
    return unicodedata.normalize("NFC", str(value or "")).casefold()


def _tokens(value):
    value = re.sub(r"(?<=[a-zçğıöşü])(?=[A-ZÇĞİÖŞÜ])", " ", str(value or ""))
    value = re.sub(r"(?<=[A-Za-zÇĞİÖŞÜçğıöşü])(?=\d)|(?<=\d)(?=[A-Za-zÇĞİÖŞÜçğıöşü])", " ", value)
    return re.findall(r"[a-z0-9]+", _fold(value))


def _text(record):
    return " ".join(str(record.get(field) or "") for field in
                    ("column", "object", "column_comment", "table_comment"))


def _signature(snapshot):
    return hashlib.sha256(json.dumps({
        "version": snapshot.get("version"), "scope": snapshot.get("scope"),
        "records": sorted(({key: value for key, value in r.items()
                            if key not in ("captured_at", "_score", "_retrieval")}
                           for r in snapshot.get("records", [])),
                          key=lambda r: str(r.get("catalog_id"))),
    }, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _cosine(left, right):
    a = math.sqrt(sum(value * value for value in left))
    b = math.sqrt(sum(value * value for value in right))
    return sum(x * y for x, y in zip(left, right)) / (a * b) if a and b else 0.0


class HybridIndex:
    def __init__(self, snapshot, embedding=None, cache_dir=None):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("records"), list):
            raise InputError("Katalog snapshot kaydı gerekli.")
        scope = snapshot.get("scope")
        if not isinstance(scope, dict) or not all(scope.get(k) for k in
                                                 ("source_database", "environment", "container", "schemas")):
            raise InputError("Katalog kaynak, ortam, container ve şema kapsamı gerekli.")
        self.scope = {key: scope[key] for key in ("source_database", "environment", "container", "schemas")}
        self.version = snapshot.get("version")
        if not isinstance(self.version, str) or not self.version:
            raise InputError("Katalog sürümü gerekli.")
        self.signature = _signature(snapshot)
        allowed = set(scope["schemas"])
        self.records = []
        seen = set()
        for record in snapshot["records"]:
            if any(record.get(key) != scope[key] for key in ("source_database", "environment", "container")):
                continue
            if record.get("schema") not in allowed:
                continue
            catalog_id = record.get("catalog_id")
            if not isinstance(catalog_id, str) or not catalog_id or catalog_id in seen:
                raise InputError("Katalog kimliği geçersiz veya yinelenmiş.")
            seen.add(catalog_id)
            self.records.append(record)
        self.embedding = embedding
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.documents = [Counter(_tokens(_text(record))) for record in self.records]
        self.lengths = [sum(counts.values()) for counts in self.documents]
        self.average_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0
        self.document_frequency = Counter()
        for counts in self.documents:
            self.document_frequency.update(counts.keys())
        self._vectors = {}

    def assert_fresh(self, snapshot):
        if (not isinstance(snapshot, dict) or not isinstance(snapshot.get("records"), list)
                or _signature(snapshot) != self.signature):
            raise StaleIndexError("Arama indeksi katalog kapsamı veya sürümüyle uyuşmuyor.")

    def _scoped(self, request):
        requested = request.get("scope") or {}
        if not isinstance(requested, dict):
            raise InputError("Talep kapsamı nesne olmalı.")
        for key in ("source_database", "environment", "container"):
            if requested.get(key) and requested[key] != self.scope[key]:
                return []
        schemas = requested.get("schemas")
        if schemas is None and request.get("schema"):
            schemas = [request["schema"]]
        if schemas is not None:
            if not isinstance(schemas, list):
                raise InputError("Şema filtresi liste olmalı.")
            allowed = set(schemas) & set(self.scope["schemas"])
            return [index for index, record in enumerate(self.records) if record["schema"] in allowed]
        return list(range(len(self.records)))

    def _bm25(self, query_tokens, eligible):
        n = len(self.documents)
        results = []
        for index in eligible:
            frequencies = self.documents[index]
            score = 0.0
            for token in set(query_tokens):
                tf = frequencies[token]
                if not tf:
                    continue
                df = self.document_frequency[token]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * self.lengths[index] / (self.average_length or 1)))
            if score > 0:
                results.append((index, score))
        return sorted(results, key=lambda item: (-item[1], self.records[item[0]]["catalog_id"]))

    def _cache_path(self, eligible):
        if not self.cache_dir or not self.embedding:
            return None
        scope_key = hashlib.sha256(json.dumps({"scope": self.scope,
            "model": getattr(self.embedding, "model", ""),
            "model_version": getattr(self.embedding, "model_version", ""),
            "cache_identity": getattr(self.embedding, "cache_identity", "")},
            sort_keys=True).encode()).hexdigest()[:16]
        identity = {"signature": self.signature, "model": getattr(self.embedding, "model", ""),
                    "model_version": getattr(self.embedding, "model_version", ""),
                    "dimensions": getattr(self.embedding, "dimensions", ""),
                    "cache_identity": getattr(self.embedding, "cache_identity", ""),
                    "ids": [self.records[index]["catalog_id"] for index in eligible]}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        return self.cache_dir / f"vectors-{scope_key}-{key}.json"

    def _record_vectors(self, eligible):
        subset = tuple(eligible)
        if subset in self._vectors:
            return self._vectors[subset]
        if not self.embedding:
            raise IntegrationError("Embedding stratejisi için embedding adapter gerekli.")
        if len(eligible) > 10000:
            raise IntegrationError("Embedding indeksi 10000 kayıt sınırını aşıyor.")
        path = self._cache_path(eligible)
        ids = [self.records[index]["catalog_id"] for index in eligible]
        if path and path.exists() and path.stat().st_size <= 10_000_000:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached.get("ids") == ids and cached.get("signature") == self.signature:
                    vectors = cached["vectors"]
                    if len(vectors) == len(ids) and all(
                        isinstance(v, list) and len(v) == self.embedding.dimensions
                        and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                and math.isfinite(x) for x in v) for v in vectors
                    ):
                        self._vectors[subset] = vectors
                        return vectors
            except (OSError, ValueError, KeyError, TypeError):
                pass
        vectors = self.embedding.embed([_text(self.records[index])[:8000] for index in eligible])
        if len(vectors) != len(ids):
            raise IntegrationError("Embedding indeksi kayıt sayısı uyuşmuyor.")
        self._vectors[subset] = vectors
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"signature": self.signature, "ids": ids, "vectors": vectors}, separators=(",", ":"))
            if len(payload) <= 10_000_000:
                temporary = path.with_suffix(".tmp")
                temporary.write_text(payload, encoding="utf-8")
                temporary.replace(path)
                # Version/model changes and deleted records cannot reuse old vectors.
                prefix = "-".join(path.name.split("-")[:2])
                for old in path.parent.glob(f"{prefix}-*.json"):
                    if old != path:
                        old.unlink(missing_ok=True)
        return vectors

    def _embedding(self, query, eligible):
        vectors = self._record_vectors(eligible)
        query_vectors = self.embedding.embed([query[:8000]])
        if len(query_vectors) != 1:
            raise IntegrationError("Embedding sorgu vektörü eksik.")
        results = [(index, _cosine(query_vectors[0], vector)) for index, vector in zip(eligible, vectors)]
        return sorted(((i, score) for i, score in results if score > 0),
                      key=lambda item: (-item[1], self.records[item[0]]["catalog_id"]))

    def search(self, request, limit=8, strategy="hybrid", expanded=False):
        if not isinstance(request, dict):
            raise InputError("Talep nesne olmalı.")
        if strategy not in ("hybrid", "exact", "bm25", "embedding"):
            raise InputError("Arama stratejisi geçersiz.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise InputError("Arama limiti 1–100 aralığında olmalı.")
        eligible = self._scoped(request)
        if not eligible:
            return []
        name = str(request.get("name") or "").strip()
        query = " ".join(str(request.get(key) or "") for key in ("name", "description")).strip()
        if not query:
            return []
        query_tokens = _tokens(query)
        lists = {}
        if strategy in ("hybrid", "exact") and name:
            exact = [index for index in eligible
                     if _identifier_key(self.records[index].get("column")) == _identifier_key(name)]
            lists["exact"] = sorted(exact, key=lambda i: self.records[i]["catalog_id"])
        if strategy in ("hybrid", "bm25") and query_tokens:
            lists["bm25"] = [index for index, _ in self._bm25(query_tokens, eligible)]
        if strategy in ("hybrid", "embedding") and self.embedding:
            lists["embedding"] = [index for index, _ in self._embedding(query, eligible)]
        elif strategy == "embedding":
            raise IntegrationError("Embedding stratejisi için embedding adapter gerekli.")
        scores = {}
        sources = {}
        for source, ranked in lists.items():
            for rank, index in enumerate(ranked, 1):
                scores[index] = scores.get(index, 0.0) + 1.0 / (60 + rank)
                sources.setdefault(index, []).append(source)
        ranked = sorted(scores, key=lambda i: (-scores[i], self.records[i]["catalog_id"]))
        effective_limit = max(limit, 24) if expanded else limit
        return [{**self.records[index], "_score": scores[index], "_retrieval": sources[index]}
                for index in ranked[:effective_limit]]
