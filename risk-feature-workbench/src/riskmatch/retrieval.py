"""Scope-first exact, BM25 and embedding retrieval with reciprocal rank fusion."""

import hashlib
import json
import math
import re
import unicodedata
from array import array
from collections import Counter
from pathlib import Path

from riskmatch.errors import AnalysisCancelled, InputError, IntegrationError, StaleIndexError


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
    def __init__(self, snapshot, embedding=None, cache_dir=None, *, candidate_limit=None,
                 embedding_budget=None, progress=None, cancelled=None):
        if candidate_limit is not None and (isinstance(candidate_limit, bool)
                or not isinstance(candidate_limit, int) or not 1 <= candidate_limit <= 100):
            raise InputError("Aday ön eleme sınırı 1–100 aralığında olmalı.")
        if embedding_budget is not None and (isinstance(embedding_budget, bool)
                or not isinstance(embedding_budget, int) or not 0 <= embedding_budget <= 10000):
            raise InputError("Embedding bütçesi 0–10000 aralığında olmalı.")
        self.candidate_limit = candidate_limit
        self.embedding_budget = embedding_budget
        self.progress = progress
        self.cancelled = cancelled
        self._check_cancelled()
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
        self._postings = {}
        self._exact_indices = {}
        for index, counts in enumerate(self.documents):
            self._check_cancelled()
            self.document_frequency.update(counts.keys())
            if candidate_limit is not None:
                for token in counts:
                    self._postings.setdefault(token, set()).add(index)
                key = _identifier_key(self.records[index].get("column"))
                self._exact_indices.setdefault(key, []).append(index)
        self._vectors = {}
        # Fast mode caches only in this job: unknown server-default weights must
        # never share persisted vectors with another analysis or user.
        self._text_vectors = {}
        self._record_vector_keys = {}
        self._query_vectors = {}
        self._embedding_circuit_open = False
        self.stats = {"searches": 0, "lexical_searches": 0,
                      "catalog_vectors_embedded": 0, "query_vectors_embedded": 0,
                      "embedding_calls": 0, "embedding_failures": 0,
                      "catalog_cache_hits": 0, "query_cache_hits": 0,
                      "vector_comparisons": 0, "embedding_budget_exhausted": False,
                      "circuit_open": False}
        self.last_search_info = {}

    def _check_cancelled(self):
        if self.cancelled and self.cancelled():
            raise AnalysisCancelled("Analiz kullanıcı tarafından durduruldu.")

    def _progress(self, stage, completed, total):
        self._check_cancelled()
        if self.progress:
            self.progress({"phase": "embedding", "stage": stage,
                           "items_completed": completed, "items_total": total,
                           "stats": dict(self.stats)})
        self._check_cancelled()

    def _warn(self, message):
        previous = self.last_search_info.get("warning", "")
        if message not in previous:
            self.last_search_info["warning"] = (previous + " " + message).strip()

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
        tokens = set(query_tokens)
        if self.candidate_limit is not None:
            matching = set()
            for token in tokens:
                matching.update(self._postings.get(token, ()))
            eligible = matching.intersection(eligible)
        for index in eligible:
            self._check_cancelled()
            frequencies = self.documents[index]
            score = 0.0
            for token in tokens:
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
        if (not self.cache_dir or not self.embedding
                or getattr(self.embedding, "persistent_cache_safe", True) is False):
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
        self._check_cancelled()
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
        vectors = []
        maximum_batch = max(1, getattr(self.embedding, "max_batch", len(eligible) or 1))
        for start in range(0, len(eligible), maximum_batch):
            batch = eligible[start:start + maximum_batch]
            self._progress("Katalog için anlamsal arama indeksi hazırlanıyor", start, len(eligible))
            self.stats["embedding_calls"] += 1
            output = self.embedding.embed([_text(self.records[index])[:8000] for index in batch])
            self._check_cancelled()
            if len(output) != len(batch):
                raise IntegrationError("Embedding indeksi kayıt sayısı uyuşmuyor.")
            vectors.extend(output)
            self.stats["catalog_vectors_embedded"] += len(batch)
            self._progress("Katalog vektörleri hazırlanıyor", start + len(batch), len(eligible))
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
        self._progress("Değişken açıklaması anlam bakımından karşılaştırılıyor", 0, 1)
        self.stats["embedding_calls"] += 1
        query_vectors = self.embedding.embed([query[:8000]])
        self._check_cancelled()
        if len(query_vectors) != 1:
            raise IntegrationError("Embedding sorgu vektörü eksik.")
        self.stats["query_vectors_embedded"] += 1
        self._progress("Değişken vektörü hazır", 1, 1)
        results = [(index, _cosine(query_vectors[0], vector)) for index, vector in zip(eligible, vectors)]
        return sorted(((i, score) for i, score in results if score > 0),
                      key=lambda item: (-item[1], self.records[item[0]]["catalog_id"]))

    def _vector_key(self, text):
        return (getattr(self.embedding, "model", ""),
                getattr(self.embedding, "model_version", ""),
                getattr(self.embedding, "cache_identity", ""),
                getattr(self.embedding, "dimensions", ""),
                hashlib.sha256(text.encode("utf-8")).hexdigest())

    def _normalize_vector(self, vector):
        if (not isinstance(vector, list) or len(vector) != self.embedding.dimensions
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in vector)):
            raise IntegrationError("Embedding yanıtının boyutu veya sayısal değerleri geçersiz.")
        length = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(length) or not length:
            raise IntegrationError("Embedding yanıtı sıfır veya geçersiz vektör içeriyor.")
        return array("f", (value / length for value in vector))

    def _embed_batch(self, texts, completed, total, stage):
        self._progress(stage, completed, total)
        self.stats["embedding_calls"] += 1
        vectors = self.embedding.embed(texts)
        self._check_cancelled()
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise IntegrationError("Embedding yanıtındaki vektör sayısı istekle uyuşmuyor.")
        return [self._normalize_vector(vector) for vector in vectors]

    def _fast_embedding(self, query, shortlist):
        if self._embedding_circuit_open:
            self.last_search_info["embedding_skipped"] = True
            self.last_search_info["embedding_skipped_count"] = len(shortlist)
            self._warn("Embedding servisi bu analizde hata verdi; tekrar beklememek için ad ve metin aramasıyla devam edildi.")
            return []
        pending = {}
        for index in shortlist:
            text = _text(self.records[index])[:8000]
            key = self._vector_key(text)
            self._record_vector_keys[index] = key
            if key in self._text_vectors:
                self.stats["catalog_cache_hits"] += 1
            else:
                pending.setdefault(key, text)
        budget_left = (len(pending) if self.embedding_budget is None else
                       max(0, self.embedding_budget - self.stats["catalog_vectors_embedded"]))
        selected = list(pending.items())[:budget_left]
        if len(selected) < len(pending):
            self.stats["embedding_budget_exhausted"] = True
            self.last_search_info["embedding_skipped"] = True
            self._warn("Bu analiz için katalog embedding bütçesine ulaşıldı; kalan adaylar ad ve metin eşleşmesiyle değerlendirildi.")
        maximum_batch = max(1, min(64, getattr(self.embedding, "max_batch", 16)))
        try:
            for start in range(0, len(selected), maximum_batch):
                batch = selected[start:start + maximum_batch]
                vectors = self._embed_batch([text for _, text in batch], start,
                                           len(selected), "Kısa listedeki kolonlar anlam bakımından karşılaştırılıyor")
                for (key, _), vector in zip(batch, vectors):
                    self._text_vectors[key] = vector
                self.stats["catalog_vectors_embedded"] += len(batch)
                self._progress("Kısa listedeki kolon vektörleri hazır", start + len(batch), len(selected))
            available = [index for index in shortlist
                         if self._record_vector_keys[index] in self._text_vectors]
            self.last_search_info["embedding_skipped_count"] = len(shortlist) - len(available)
            if not available:
                return []
            query = query[:8000]
            query_key = self._vector_key(query)
            if query_key in self._query_vectors:
                self.stats["query_cache_hits"] += 1
            else:
                vector = self._embed_batch([query], 0, 1, "Değişken açıklaması anlam bakımından karşılaştırılıyor")[0]
                self._query_vectors[query_key] = vector
                self.stats["query_vectors_embedded"] += 1
                self._progress("Değişken vektörü hazır", 1, 1)
            query_vector = self._query_vectors[query_key]
            results = []
            for index in available:
                self._check_cancelled()
                vector = self._text_vectors[self._record_vector_keys[index]]
                score = sum(x * y for x, y in zip(query_vector, vector))
                self.stats["vector_comparisons"] += 1
                if score > 0:
                    results.append((index, score))
            return sorted(results, key=lambda item: (-item[1], self.records[item[0]]["catalog_id"]))
        except AnalysisCancelled:
            raise
        except Exception:
            # Controlled degradation, never persist raw provider exceptions.
            self._embedding_circuit_open = True
            self.stats["embedding_failures"] += 1
            self.stats["circuit_open"] = True
            self.last_search_info["embedding_skipped"] = True
            self.last_search_info["embedding_skipped_count"] = len(shortlist)
            self._warn("Embedding servisi bu analizde hata verdi; tekrar beklememek için ad ve metin aramasıyla devam edildi.")
            self._progress("Embedding kullanılamadı; ad ve metin aramasıyla devam ediliyor", 0, 0)
            return []

    def _fast_search(self, request, eligible, name, query, limit, strategy, expanded):
        self.stats["lexical_searches"] += 1
        eligible_set = set(eligible)
        exact = sorted((i for i in self._exact_indices.get(_identifier_key(name), ())
                        if i in eligible_set), key=lambda i: self.records[i]["catalog_id"]) if name else []
        lexical = [i for i, _ in self._bm25(_tokens(query), eligible)] if strategy != "exact" else []
        # Exact physical identifiers reserve slots before lexical candidates.
        shortlist = list(dict.fromkeys(exact + lexical))[:self.candidate_limit]
        self.last_search_info.update({"candidate_limited": len(shortlist) < len(eligible),
                                      "candidate_count": len(shortlist),
                                      "exact_match_count": len(exact)})
        if len(shortlist) < len(eligible):
            self._warn("Hızlı arama ad ve metin eşleşmesiyle seçilen kısa listeyi değerlendirdi; tüm katalogda anlamsal arama yapılmadı.")
        if not shortlist:
            self.last_search_info["embedding_skipped"] = strategy in ("hybrid", "embedding")
            self._warn("Ad ve açıklamayla kısa liste oluşturulamadı; rastgele kolonlar embedding servisine gönderilmedi.")
            return []
        allowed = set(shortlist)
        lists = {"exact": [i for i in exact if i in allowed]}
        if strategy != "exact":
            lists["bm25"] = [i for i in lexical if i in allowed]
        if strategy in ("hybrid", "embedding"):
            if self.embedding:
                lists["embedding"] = [i for i, _ in self._fast_embedding(query, shortlist)]
            elif strategy == "embedding":
                raise IntegrationError("Embedding stratejisi için embedding adapter gerekli.")
        scores, sources = {}, {}
        for source, ranked in lists.items():
            for rank, index in enumerate(ranked, 1):
                scores[index] = scores.get(index, 0.0) + 1.0 / (60 + rank)
                sources.setdefault(index, []).append(source)
        exact_set = set(exact)
        ranked = sorted(scores, key=lambda i: (i not in exact_set, -scores[i], self.records[i]["catalog_id"]))
        effective_limit = max(limit, 24) if expanded else limit
        return [{**self.records[index], "_score": scores[index], "_retrieval": sources[index]}
                for index in ranked[:effective_limit]]

    def search(self, request, limit=8, strategy="hybrid", expanded=False):
        self._check_cancelled()
        self.stats["searches"] += 1
        self.last_search_info = {"candidate_limited": False, "eligible_count": 0,
                                 "candidate_count": 0, "embedding_skipped": False,
                                 "embedding_skipped_count": 0, "warning": "",
                                 "exact_match_count": 0}
        if not isinstance(request, dict):
            raise InputError("Talep nesne olmalı.")
        if strategy not in ("hybrid", "exact", "bm25", "embedding"):
            raise InputError("Arama stratejisi geçersiz.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise InputError("Arama limiti 1–100 aralığında olmalı.")
        eligible = self._scoped(request)
        self.last_search_info["eligible_count"] = len(eligible)
        if not eligible:
            return []
        name = str(request.get("name") or "").strip()
        query = " ".join(str(request.get(key) or "") for key in ("name", "description")).strip()
        if not query:
            return []
        if self.candidate_limit is not None:
            return self._fast_search(request, eligible, name, query, limit, strategy, expanded)
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
