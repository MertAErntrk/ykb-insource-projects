"""Order-independent candidate pool: exact, lexicon-normalized, BM25 and frozen semantic channels.

Each row's pool and features depend only on (row, snapshot, frozen vector set).
The frozen set is fixed at construction and on refresh_index(); documents
embedded later for a row's shortlist never widen another row's search.
"""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter

from riskmatch import config as _config
from riskmatch import lexicon as _lexicon
from riskmatch.errors import AnalysisCancelled, InputError, IntegrationError, StaleIndexError
from riskmatch.vectorstore import text_hash


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


def _signature(snapshot):
    return hashlib.sha256(json.dumps({
        "version": snapshot.get("version"), "scope": snapshot.get("scope"),
        "records": sorted(({key: value for key, value in r.items()
                            if key not in ("captured_at", "_score", "_retrieval", "_features", "_locations")}
                           for r in snapshot.get("records", [])),
                          key=lambda r: str(r.get("catalog_id"))),
    }, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _unit(vector, dims=None):
    if isinstance(vector, (str, bytes)) or not hasattr(vector, "__len__"):
        raise ValueError("vector must be a sequence")
    if dims is not None and len(vector) != dims:
        raise ValueError("vector dimension mismatch")
    if not len(vector) or any(isinstance(x, bool) or not isinstance(x, (int, float))
                              or not math.isfinite(x) for x in vector):
        raise ValueError("vector values invalid")
    norm = math.sqrt(math.fsum(x * x for x in vector))
    if not norm or not math.isfinite(norm):
        raise ValueError("vector norm is zero")
    return [x / norm for x in vector]


def _dot(left, right):
    return sum(x * y for x, y in zip(left, right))


class MemoryNamespace:
    """In-memory Namespace equivalent, lives only as long as its owner."""
    tainted = False

    def __init__(self, dims=None):
        self.dims = dims
        self._vectors = {}
        self._signatures = {}

    @staticmethod
    def _signature(unit):
        return int("".join("1" if x >= 0 else "0" for x in unit), 2)

    def has(self, hashes):
        return {h for h in hashes if h in self._vectors}

    def get_many(self, hashes):
        return {h: self._vectors[h] for h in hashes if h in self._vectors}

    def put_many(self, items):
        count = 0
        for h, vector in items:
            unit = _unit(vector, self.dims)
            self.dims = len(unit)
            self._vectors[h] = unit
            self._signatures[h] = self._signature(unit)
            count += 1
        return count

    def count(self):
        return len(self._vectors)

    def coarse(self, query, k=300, restrict=None):
        """Sign-bit Hamming prefilter, same contract as vectorstore.Namespace.coarse."""
        signature = self._signature(_unit(query, self.dims))
        scored = sorted((bin(sig ^ signature).count("1"), h) for h, sig in self._signatures.items()
                        if restrict is None or h in restrict)
        return [(h, distance) for distance, h in scored[:max(0, k)]]

    def rescore(self, query, hashes):
        unit = _unit(query, self.dims)
        out = [(h, _dot(unit, self._vectors[h])) for h in set(hashes) if h in self._vectors]
        out.sort(key=lambda item: (-item[1], item[0]))
        return out

    def search(self, query, k=50, coarse_k=300):
        return self.rescore(query, [h for h, _ in self.coarse(query, max(k, coarse_k))])[:k]


class HybridIndex:
    """Builds a per-row candidate pool with measurable features.

    cache_dir, candidate_limit and embedding_budget are accepted for backward
    compatibility but have no effect (deprecated): there is no job-level budget,
    vectors live in ``namespace`` (or an in-memory one for this object).
    """

    _CIRCUIT_LIMIT = 3

    def __init__(self, snapshot, *, embed=None, namespace=None, lexicon=None, progress=None,
                 cancelled=None, pool_size=30, lexical_k=40, semantic_k=30, shortlist_embed=10,
                 background_size=128, embedding=None, cache_dir=None, candidate_limit=None,
                 embedding_budget=None, table_k=10, table_embed=40, exclude_objects=None):
        for value in (pool_size, lexical_k, semantic_k, shortlist_embed, background_size, table_k, table_embed):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InputError("Aday havuzu ayarları negatif olmayan tam sayı olmalı.")
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
        # Taramanın uyguladığı kural snapshot kapsamında taşınır; yoksa varsayılan kural.
        excluded = self._exclusion_rules(exclude_objects if exclude_objects is not None else scope.get("exclude_objects"))
        self.records = []
        seen = set()
        for record in snapshot["records"]:
            if any(record.get(key) != scope[key] for key in ("source_database", "environment", "container")):
                continue
            if record.get("schema") not in allowed:
                continue
            if excluded and self._excluded(record, excluded):
                continue
            catalog_id = record.get("catalog_id")
            if not isinstance(catalog_id, str) or not catalog_id or catalog_id in seen:
                raise InputError("Katalog kimliği geçersiz veya yinelenmiş.")
            seen.add(catalog_id)
            self.records.append(record)
        self.pool_size, self.lexical_k, self.semantic_k = pool_size, lexical_k, semantic_k
        self.shortlist_embed, self.background_size = shortlist_embed, background_size
        self.table_k, self.table_embed = table_k, table_embed
        self.lexicon = lexicon or _lexicon.load()
        self.embedding = embedding
        if embed is None and embedding is not None:
            embed = lambda texts, kind: embedding.embed(texts, kind=kind)  # noqa: E731
        self.embed = embed
        self._batch = max(1, min(64, int(getattr(embedding, "max_batch", 64) or 64)))
        if namespace is None and embed is not None:
            namespace = MemoryNamespace()
        self.namespace = namespace
        self._token_cache = {}
        self._build_lexical()
        self._doc_texts, self._doc_hashes, self._hash_indices = [], [], {}
        for index, record in enumerate(self.records):
            text = self.document_text(record)
            digest = text_hash(text)
            self._doc_texts.append(text)
            self._doc_hashes.append(digest)
            self._hash_indices.setdefault(digest, []).append(index)
        self._unique_hashes = sorted(self._hash_indices)
        # Aynı (kolon, yorum) kopyaları tek grup: havuz grup başına kesilir, diğer üyeler konum olur.
        self._group_keys, self._group_members = [], {}
        for index, record in enumerate(self.records):
            key = tuple(" ".join(_fold(record.get(k)).split()) for k in ("column", "column_comment"))
            self._group_keys.append(key)
            self._group_members.setdefault(key, []).append(index)
        # Kapsam filtresi yokken her aramada yeniden kurulmasın.
        self._all_indices = list(range(len(self.records)))
        self._all_set = frozenset(self._all_indices)
        self._all_hashes = frozenset(self._doc_hashes)
        self._build_tables()
        self._background = self._unique_hashes[:background_size]
        self._background_ready = False
        self._query_vectors = {}
        self._query_background = {}
        self._consecutive_failures = 0
        self.stats = {"searches": 0, "lexical_searches": 0,
                      "catalog_vectors_embedded": 0, "query_vectors_embedded": 0,
                      "embedding_calls": 0, "embedding_failures": 0,
                      "catalog_cache_hits": 0, "query_cache_hits": 0,
                      "vector_comparisons": 0, "embedding_budget_exhausted": False,
                      "circuit_open": False, "document_calls": 0}
        self.last_search_info = {}
        self.refresh_index()

    # -- text and token helpers -------------------------------------------------

    def _lex(self, text):
        text = str(text or "")
        cached = self._token_cache.get(text)
        if cached is None:
            cached = self._token_cache[text] = tuple(self.lexicon.tokens(text)) if text.strip() else ()
        return cached

    def _distinctive(self, token):
        return not self.lexicon.is_generic(token) and not token.isdigit()

    def _query_tokens(self, token):
        return not self.lexicon.is_generic(token)

    def document_text(self, record):
        column = str(record.get("column") or "").strip()
        parts = [column, self.lexicon.describe(column) if column else "",
                 str(record.get("column_comment") or "").strip()]
        return " | ".join(part for part in parts if part)[:8000]

    def query_text(self, request):
        name = str(request.get("name") or "").strip()
        parts = [name, self.lexicon.describe(name) if name else "",
                 str(request.get("description") or "").strip(), str(request.get("category") or "").strip()]
        return " | ".join(part for part in parts if part)[:8000]

    def table_document_text(self, schema, obj, table_comment):
        obj = str(obj or "").strip()
        parts = [obj, self.lexicon.describe(obj) if obj else "", str(table_comment or "").strip()]
        return " | ".join(part for part in parts if part)[:8000]

    @staticmethod
    def _exclusion_rules(rules):
        if rules is None:
            rules = _config.DEFAULT_EXCLUDE_OBJECTS
        if not isinstance(rules, list) or not all(
                isinstance(rule, dict) and isinstance(rule.get("schema"), str)
                and isinstance(rule.get("prefixes"), list) and all(isinstance(p, str) for p in rule["prefixes"])
                for rule in rules):
            raise InputError("Dışlama kuralları şema ve önek listesi olmalı.")
        return [(rule["schema"], tuple(p.upper() for p in rule["prefixes"] if p)) for rule in rules]

    @staticmethod
    def _excluded(record, rules):
        obj = str(record.get("object") or "").upper()
        return any(record.get("schema") == schema and obj.startswith(prefixes)
                   for schema, prefixes in rules if prefixes)

    def document_hash(self, record):
        return text_hash(self.document_text(record))

    def _build_lexical(self):
        self._exact_indices, self._normalized_indices = {}, {}
        self._postings, self._lengths, self._column_tokens, self._content_tokens = {}, [], [], []
        for index, record in enumerate(self.records):
            if index % 500 == 0:
                self._check_cancelled()
            column = self._lex(record.get("column"))
            comment = self._lex(record.get("column_comment"))
            other = self._lex(record.get("object")) + self._lex(record.get("table_comment"))
            weights = Counter()
            for tokens, weight in ((column, 3), (comment, 2), (other, 1)):
                for token in tokens:
                    weights[token] += weight
            for token, tf in weights.items():
                self._postings.setdefault(token, []).append((index, tf))
            self._lengths.append(sum(weights.values()))
            self._column_tokens.append(frozenset(column))
            self._content_tokens.append(frozenset(t for t in column + comment if self._distinctive(t)))
            self._exact_indices.setdefault(_identifier_key(record.get("column")), []).append(index)
            if column:
                self._normalized_indices.setdefault(tuple(sorted(column)), []).append(index)
        self._average_length = sum(self._lengths) / len(self._lengths) if self._lengths else 1.0

    def _build_tables(self):
        """Tablo belgeleri (ad + açıklama) ve tablo düzeyi BM25; kolon indeksinden ayrı."""
        self._table_keys, self._table_members, self._record_table, ids = [], [], [], {}
        comments = []
        for index, record in enumerate(self.records):
            key = (record.get("schema"), record.get("object"))
            table = ids.get(key)
            if table is None:
                table = ids[key] = len(self._table_keys)
                self._table_keys.append(key)
                self._table_members.append([])
                comments.append("")
            self._table_members[table].append(index)
            self._record_table.append(table)
            if not comments[table] and str(record.get("table_comment") or "").strip():
                comments[table] = record.get("table_comment")
        self._table_hashes, self._table_texts, self._table_hash_tables = [], {}, {}
        self._table_postings, self._table_lengths = {}, []
        for table, (schema, obj) in enumerate(self._table_keys):
            text = self.table_document_text(schema, obj, comments[table])
            digest = text_hash(text)
            self._table_hashes.append(digest)
            self._table_texts[digest] = text
            self._table_hash_tables.setdefault(digest, []).append(table)
            weights = Counter()
            for tokens, weight in ((self._lex(obj), 2), (self._lex(comments[table]), 1)):
                for token in tokens:
                    weights[token] += weight
            for token, tf in weights.items():
                self._table_postings.setdefault(token, []).append((table, tf))
            self._table_lengths.append(sum(weights.values()))
        self._table_average = (sum(self._table_lengths) / len(self._table_lengths)
                               if self._table_lengths else 1.0)
        self._table_unique = sorted(self._table_hash_tables)
        self._all_tables = frozenset(range(len(self._table_keys)))

    def _text(self, digest):
        indices = self._hash_indices.get(digest)
        return self._doc_texts[indices[0]] if indices else self._table_texts[digest]

    # -- frozen semantic set ----------------------------------------------------

    def refresh_index(self):
        self._frozen = (frozenset(self.namespace.has(self._unique_hashes))
                        if self.namespace is not None and self._unique_hashes else frozenset())
        self._table_frozen = (frozenset(self.namespace.has(self._table_unique))
                              if self.namespace is not None and self._table_unique else frozenset())
        self._all_covered = self._frozen >= self._all_hashes
        return len(self._frozen)

    def begin_job(self, progress=None, cancelled=None, reset_circuit=True):
        """Bind per-job callbacks to a cached index; returns a stats copy so callers can report per-job deltas."""
        self.progress, self.cancelled = progress, cancelled
        if reset_circuit:
            self._consecutive_failures = 0
            self.stats["circuit_open"] = False
        return dict(self.stats)

    def embed_all(self):
        """Embed every catalog text not yet stored, then freeze; for small catalogs (demo, tests)."""
        if self.embed is not None and self._unique_hashes:
            self._store_documents(self._table_unique + self._unique_hashes, "Katalog metinleri anlamsal arama için hazırlanıyor")
            self.refresh_index()
        return self.index_coverage

    def stored_coverage(self):
        """Share of unique catalog texts already in the vector store (grows while the indexer runs)."""
        if self.namespace is None or not self._unique_hashes:
            return 0.0
        return len(self.namespace.has(self._unique_hashes)) / len(self._unique_hashes)

    @property
    def index_coverage(self):
        return len(self._frozen) / len(self._unique_hashes) if self._unique_hashes else 0.0

    def missing_documents(self, limit=None):
        present = self.namespace.has(self._unique_hashes) if self.namespace is not None else set()
        missing = [(h, self._doc_texts[self._hash_indices[h][0]])
                   for h in self._unique_hashes if h not in present]
        return missing if limit is None else missing[:max(0, limit)]

    def queue_counts(self):
        """(eksik, toplam) benzersiz tablo + kolon belgesi; index_queue() sıralaması yapmadan sayar."""
        hashes = set(self._table_unique) | set(self._unique_hashes)
        present = self.namespace.has(list(hashes)) if self.namespace is not None and hashes else set()
        return len(hashes) - len(present & hashes), len(hashes)

    def index_queue(self):
        """Arka plan sırası: önce eksik tablo belgeleri, sonra kolonlar şema katmanı önceliğiyle."""
        hashes = self._table_unique + self._unique_hashes
        present = self.namespace.has(hashes) if self.namespace is not None else set()
        tiers = {}
        for rank, tier in enumerate(_config.DEFAULT_SCHEMA_TIERS):
            for schema in tier.get("schemas") or ():
                tiers.setdefault(schema, rank)
        last = len(_config.DEFAULT_SCHEMA_TIERS)
        tables = [(h, self._table_texts[h]) for h in self._table_unique if h not in present]
        tables.sort(key=lambda item: (min(tiers.get(self._table_keys[t][0], last)
                                          for t in self._table_hash_tables[item[0]]), item[0]))
        columns = [(h, self._doc_texts[self._hash_indices[h][0]]) for h in self._unique_hashes if h not in present]
        columns.sort(key=lambda item: (min(tiers.get(self.records[i].get("schema"), last)
                                           for i in self._hash_indices[item[0]]), item[0]))
        return tables + columns

    # -- infrastructure ---------------------------------------------------------

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
        previous = self.last_search_info.get("warning") or ""
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
        return self._all_indices

    def _call_embed(self, texts, kind, stage):
        vectors = []
        for start in range(0, len(texts), self._batch):
            batch = texts[start:start + self._batch]
            self._progress(stage, start, len(texts))
            self.stats["embedding_calls"] += 1
            if kind == "document":
                self.stats["document_calls"] += 1
                self.last_search_info["document_calls"] = self.last_search_info.get("document_calls", 0) + 1
            output = self.embed(batch, kind)
            self._check_cancelled()
            if not isinstance(output, list) or len(output) != len(batch):
                raise IntegrationError("Embedding yanıtındaki vektör sayısı istekle uyuşmuyor.")
            vectors.extend(_unit(vector, getattr(self.namespace, "dims", None)) for vector in output)
            self._progress(stage, start + len(batch), len(texts))
        return vectors

    def _store_documents(self, hashes, stage):
        missing = sorted(set(hashes) - self.namespace.has(hashes))
        self.stats["catalog_cache_hits"] += len(set(hashes)) - len(missing)
        if missing:
            texts = [self._text(h) for h in missing]
            vectors = self._call_embed(texts, "document", stage)
            self.namespace.put_many(list(zip(missing, vectors)))
            self.stats["catalog_vectors_embedded"] += len(missing)

    def _query_vector(self, text):
        key = text_hash(text)
        if key in self._query_vectors:
            self.stats["query_cache_hits"] += 1
        else:
            self._query_vectors[key] = self._call_embed(
                [text], "query", "Değişken açıklaması anlam bakımından karşılaştırılıyor")[0]
            self.stats["query_vectors_embedded"] += 1
        return key, self._query_vectors[key]

    def _background_stats(self, key, vector):
        if key not in self._query_background:
            if not self._background_ready:
                self._store_documents(self._background, "Anlamsal karşılaştırma için referans dağılım hazırlanıyor")
                self._background_ready = True
            values = self.namespace.rescore(vector, self._background)
            self.stats["vector_comparisons"] += len(values)
            self._query_background[key] = values or None
        return self._query_background[key]

    @staticmethod
    def _z(value, digest, background):
        """Leave-one-out z: the candidate's own text is never part of its background.

        Returns (z, small); small means the background has < 16 texts, so the scale is limited.
        """
        values = [cos for h, cos in background if h != digest]
        if not values:
            return None, True
        mean = math.fsum(values) / len(values)
        spread = math.sqrt(math.fsum((v - mean) ** 2 for v in values) / len(values))
        return (value - mean) / max(spread, 1e-3), len(values) < 16

    # -- search -----------------------------------------------------------------

    def _bm25(self, tokens, eligible_set):
        n = len(self.records)
        scores, matched = {}, {}
        for token in tokens:
            postings = self._postings.get(token)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            distinctive = self._distinctive(token)
            for index, tf in postings:
                if index not in eligible_set:
                    continue
                norm = 1.2 * (0.25 + 0.75 * self._lengths[index] / (self._average_length or 1))
                scores[index] = scores.get(index, 0.0) + idf * (tf * 2.2) / (tf + norm)
                if distinctive:
                    matched[index] = matched.get(index, 0) + 1
        ranked = sorted((i for i in scores if matched.get(i)),
                        key=lambda i: (-scores[i], self.records[i]["catalog_id"]))
        # lexical_k farklı (kolon, yorum) grubu sayar; kopyalar ikinci ilgili kolonu dışarıda bırakmaz.
        out, groups = [], set()
        for index in ranked:
            key = self._group_keys[index]
            if key not in groups:
                if len(groups) >= self.lexical_k:
                    continue
                groups.add(key)
            out.append(index)
        return out, matched

    def _table_bm25(self, tokens, scoped):
        n = len(self._table_keys)
        scores, matched = {}, set()
        for token in tokens:
            postings = self._table_postings.get(token)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            distinctive = self._distinctive(token)
            for table, tf in postings:
                if table not in scoped:
                    continue
                norm = 1.2 * (0.25 + 0.75 * self._table_lengths[table] / (self._table_average or 1))
                scores[table] = scores.get(table, 0.0) + idf * (tf * 2.2) / (tf + norm)
                if distinctive:
                    matched.add(table)
        return sorted((t for t in scores if t in matched), key=lambda t: (-scores[t], self._table_keys[t]))

    def _rank_tables(self, tokens, scoped, vector):
        """İlk table_k tablo: tablo BM25'i ve (vektör varsa) tablo cos sırasının RRF'i."""
        lists, mode = [self._table_bm25(tokens, scoped)], "lexical"
        if vector is not None:
            hashes = {self._table_hashes[t] for t in scoped} & self._table_frozen
            if hashes:
                scored = self.namespace.rescore(vector, hashes)
                self.stats["vector_comparisons"] += len(scored)
                lists.append([t for h, _ in scored for t in sorted(self._table_hash_tables[h]) if t in scoped])
                mode = "semantic"
        scores = {}
        for ranked in lists:
            for rank, table in enumerate(ranked, 1):
                scores[table] = scores.get(table, 0.0) + 1.0 / (60 + rank)
        top = sorted(scores, key=lambda t: (-scores[t], self._table_keys[t]))[:self.table_k]
        return top, {t: scores[t] for t in top}, mode

    def _table_columns(self, tables, eligible_set, query_set):
        """Seçilen tabloların kapsamdaki kolonları; tablo başına sorgu tokenı eşleşmesine göre sıralı."""
        out = []
        for table in tables:
            members = [i for i in self._table_members[table] if i in eligible_set]
            hits = {i: len(query_set & self._content_tokens[i]) for i in members}
            out.append(sorted(members, key=lambda i: (-hits[i], self.records[i]["catalog_id"])))
        return out

    def _table_embed_hashes(self, tables, eligible_set, query_set):
        """Seçilen tabloların vektörsüz kolonlarından en çok table_embed tanesi (tablolar arasında sırayla)."""
        per_table = self._table_columns(tables, eligible_set, query_set)
        chosen, seen, depth = [], set(), 0
        while len(chosen) < self.table_embed and any(depth < len(c) for c in per_table):
            for columns in per_table:
                if depth < len(columns):
                    digest = self._doc_hashes[columns[depth]]
                    if digest not in self._frozen and digest not in seen:
                        seen.add(digest)
                        chosen.append(digest)
                        if len(chosen) >= self.table_embed:
                            break
            depth += 1
        return chosen

    def _semantic(self, query, eligible, shortlist, info, extra=None):
        """Return (semantic ranked indices, cos-by-hash, background) or raise on provider failure.

        extra(vector) satırın ek anında-embed hash'lerini döndürür; kısa listeyle tek çağrıda saklanır.
        """
        key, vector = self._query_vector(query)
        info["query_embedded"] = True
        background = self._background_stats(key, vector)
        eligible_hashes = (self._all_hashes if eligible is self._all_indices
                           else {self._doc_hashes[i] for i in eligible})
        searchable = self._frozen & eligible_hashes
        ranked = []
        if searchable and self.semantic_k:
            coarse = [h for h, _ in self.namespace.coarse(vector, max(self.semantic_k * 4, 100),
                                                          restrict=set(searchable))]
            scored = self.namespace.rescore(vector, coarse)
            self.stats["vector_comparisons"] += len(scored)
            ranked = [h for h, _ in scored[:self.semantic_k]]
        short_hashes = list(dict.fromkeys(self._doc_hashes[i] for i in shortlist))[:self.shortlist_embed]
        extra_hashes = extra(vector) if extra is not None else []
        stored = list(dict.fromkeys(short_hashes + extra_hashes))
        if stored:
            self._store_documents(stored, "Kısa listedeki kolonlar anlam bakımından karşılaştırılıyor")
        return vector, ranked, set(searchable) | set(stored), background

    def search(self, request, limit=None, strategy="hybrid", expanded=False):
        self._check_cancelled()
        self.stats["searches"] += 1
        info = self.last_search_info = {
            "semantic_scope": "none", "index_coverage": self.index_coverage, "pool_size": 0,
            "query_embedded": False, "document_calls": 0, "warning": None, "eligible_count": 0,
            "exact_match_count": 0, "candidate_limited": False, "candidate_count": 0,
            "embedding_skipped": False, "table_channel": {"tables": [], "mode": "off"}}
        if not isinstance(request, dict):
            raise InputError("Talep nesne olmalı.")
        if strategy == "bm25":
            strategy = "lexical"
        if strategy not in ("hybrid", "lexical", "exact", "embedding"):
            raise InputError("Arama stratejisi geçersiz.")
        if limit is None:
            limit = self.pool_size
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise InputError("Arama limiti 1–100 aralığında olmalı.")
        if strategy == "embedding" and self.embed is None:
            raise IntegrationError("Embedding stratejisi için embedding adapter gerekli.")
        eligible = self._scoped(request)
        info["eligible_count"] = len(eligible)
        name = str(request.get("name") or "").strip()
        description = str(request.get("description") or "").strip()
        if not eligible or not (name or description):
            return []
        eligible_set = self._all_set if eligible is self._all_indices else set(eligible)
        name_tokens = self._lex(name)
        query_tokens = list(dict.fromkeys(
            t for t in name_tokens + self._lex(description) + self._lex(request.get("category"))
            if self._query_tokens(t)))
        exact, normalized, lexical, matched = [], [], [], {}
        # "lexical" is the name-and-text fallback; it keeps the exact/normalized name channels.
        if strategy in ("hybrid", "lexical", "exact") and name:
            exact = sorted((i for i in self._exact_indices.get(_identifier_key(name), ()) if i in eligible_set),
                           key=lambda i: self.records[i]["catalog_id"])
        if strategy in ("hybrid", "lexical") and name_tokens:
            normalized = sorted((i for i in self._normalized_indices.get(tuple(sorted(name_tokens)), ())
                                 if i in eligible_set), key=lambda i: self.records[i]["catalog_id"])
        if strategy in ("hybrid", "lexical") and query_tokens:
            self.stats["lexical_searches"] += 1
            lexical, matched = self._bm25(query_tokens, eligible_set)
        info["exact_match_count"] = len(exact)
        # Önce-tablo kanalı: talep tablo adı/açıklamasıyla eşlenir, ilk table_k tablonun kolonları ayrı liste olur.
        use_tables = strategy in ("hybrid", "lexical") and self.table_k > 0 and bool(self._table_keys)
        scoped_tables = ((self._all_tables if eligible is self._all_indices
                          else {self._record_table[i] for i in eligible}) if use_tables else None)
        query_set = {t for t in query_tokens if self._distinctive(t)}
        table_state = {}

        def table_extra(query_vector):
            tables, table_scores, mode = self._rank_tables(query_tokens, scoped_tables, query_vector)
            table_state.update(tables=tables, scores=table_scores, mode=mode)
            return self._table_embed_hashes(tables, eligible_set, query_set) if self.table_embed else []

        semantic_hashes, cos_allowed, vector, background = [], set(), None, None
        if strategy in ("hybrid", "embedding") and self.embed is not None:
            if self.stats["circuit_open"]:
                info["embedding_skipped"] = True
                self._warn("Embedding servisi art arda hata verdi; ad ve metin aramasıyla devam edildi.")
            else:
                shortlist = list(dict.fromkeys(exact + normalized + lexical)) if strategy == "hybrid" else []
                try:
                    vector, semantic_hashes, cos_allowed, background = self._semantic(
                        self.query_text(request), eligible, shortlist, info,
                        extra=table_extra if use_tables and strategy == "hybrid" else None)
                    self._consecutive_failures = 0
                    covered = (self._all_covered if eligible is self._all_indices
                               else self._frozen >= {self._doc_hashes[i] for i in eligible})
                    info["semantic_scope"] = "full" if covered else "partial"
                except AnalysisCancelled:
                    raise
                except Exception:
                    # Controlled degradation; raw provider errors never reach the result.
                    vector, semantic_hashes, cos_allowed, background = None, [], set(), None
                    self.stats["embedding_failures"] += 1
                    self._consecutive_failures += 1
                    info["embedding_skipped"] = True
                    if self._consecutive_failures >= self._CIRCUIT_LIMIT:
                        self.stats["circuit_open"] = True
                    self._warn("Embedding servisi hata verdi; bu değişken ad ve metin aramasıyla değerlendirildi.")
        table_list, table_rank = [], {}
        if use_tables:
            if vector is None or "tables" not in table_state:
                tables, table_scores, mode = self._rank_tables(query_tokens, scoped_tables, None)
                table_state = {"tables": tables, "scores": table_scores, "mode": mode}
            table_rank = {t: rank for rank, t in enumerate(table_state["tables"], 1)}
            per_table = self._table_columns(table_state["tables"], eligible_set, query_set)
            table_cos = {}
            if vector is not None:
                wanted = {self._doc_hashes[i] for columns in per_table for i in columns} & cos_allowed
                if wanted:
                    table_cos = dict(self.namespace.rescore(vector, wanted))
                    self.stats["vector_comparisons"] += len(table_cos)
            ordered_tables = []
            for columns in per_table:
                with_cos = sorted((i for i in columns if self._doc_hashes[i] in table_cos),
                                  key=lambda i: (-table_cos[self._doc_hashes[i]], self.records[i]["catalog_id"]))
                rest = [i for i in columns if self._doc_hashes[i] not in table_cos and query_set & self._content_tokens[i]]
                ordered_tables.append(with_cos + rest)
            depth = 0
            while len(table_list) < self.lexical_k and any(depth < len(c) for c in ordered_tables):
                for columns in ordered_tables:
                    if depth < len(columns) and len(table_list) < self.lexical_k:
                        table_list.append(columns[depth])
                depth += 1
            info["table_channel"] = {
                "tables": [str(self._table_keys[t][0] or "?") + "." + str(self._table_keys[t][1] or "?")
                           for t in table_state["tables"]],
                "mode": table_state["mode"]}
        semantic = []
        for h in semantic_hashes:
            semantic.extend(sorted((i for i in self._hash_indices[h] if i in eligible_set),
                                   key=lambda i: self.records[i]["catalog_id"]))
        semantic_rank = {}
        for rank, h in enumerate(semantic_hashes, 1):
            semantic_rank.setdefault(h, rank)
        lists = {"exact": exact, "normalized": normalized, "lexical": lexical, "table": table_list}
        scores, sources = {}, {}
        for source, ranked in lists.items():
            for rank, index in enumerate(ranked, 1):
                scores[index] = scores.get(index, 0.0) + 1.0 / (60 + rank)
                sources.setdefault(index, []).append(source)
        for index in semantic:
            scores[index] = scores.get(index, 0.0) + 1.0 / (60 + semantic_rank[self._doc_hashes[index]])
            sources.setdefault(index, []).append("semantic")
        exact_set, normalized_set = set(exact), set(normalized)
        ordered = sorted(scores, key=lambda i: (0 if i in exact_set else 1 if i in normalized_set else 2,
                                                -scores[i], self.records[i]["catalog_id"]))
        # Havuz farklı (kolon, yorum) grubu başına kesilir; grup en iyi üyesinin sırasını alır,
        # temsilcisi (schema, object, catalog_id) sırasında ilk bulunan üyedir.
        scored_groups = {}
        for index in ordered:
            scored_groups.setdefault(self._group_keys[index], []).append(index)
        pool, locations = [], {}
        for key in list(scored_groups)[:self.pool_size]:
            representative = min(scored_groups[key], key=lambda i: (
                str(self.records[i].get("schema") or ""), str(self.records[i].get("object") or ""),
                str(self.records[i]["catalog_id"])))
            pool.append(representative)
            locations[representative] = sorted(
                str(self.records[i].get("schema") or "?") + "." + str(self.records[i].get("object") or "?")
                for i in self._group_members[key] if i != representative and i in eligible_set)
        info["pool_size"] = info["candidate_count"] = len(pool)
        cos = {}
        if vector is not None:
            wanted = {self._doc_hashes[i] for i in pool} & cos_allowed
            if wanted:
                cos = dict(self.namespace.rescore(vector, wanted))
                self.stats["vector_comparisons"] += len(cos)
        lexical_rank = {index: rank for rank, index in enumerate(lexical, 1)}
        name_distinctive = {t for t in name_tokens if self._distinctive(t)}
        required = name_distinctive
        if len(required) < 2:
            required = required | {t for t in self._lex(description) if self._distinctive(t)}
        # Konu kelimeleri: süre/ölçü/yön/birleştirme dışındaki ayırt edici tokenlar; addan yoksa açıklamadan.
        subject = {t for t in name_distinctive if not self.lexicon.is_structural(t)}
        if not subject:
            subject = {t for t in self._lex(description)
                       if self._distinctive(t) and not self.lexicon.is_structural(t)}
        # Tek konu tokenlı talep (ör. "Bakiye") kelime kuralını 1 tokenla geçebilir, ama yalnız token
        # adayın kolon adında varsa. Yapısal tokenlar (ort/maks, gelen/giden, süre) kapsamda kalır:
        # embedding yokken ORT ile MAX'ı, GELEN ile GIDEN'i ayıran tek kanıt onlar.
        query_plain = {t for t in query_tokens if self._distinctive(t) and not self.lexicon.is_structural(t)}
        legacy = {"exact": "exact", "normalized": "normalized", "lexical": "bm25", "semantic": "embedding",
                  "table": "table"}
        results = []
        for index in pool[:limit]:
            digest = self._doc_hashes[index]
            value = cos.get(digest)
            z, small = (self._z(value, digest, background)
                        if value is not None and background is not None else (None, False))
            column_tokens = self._column_tokens[index]
            missing = sorted(subject - self._content_tokens[index])
            level = ("exact" if index in exact_set else "normalized" if index in normalized_set
                     else "partial" if name_distinctive & column_tokens else "none")
            results.append({**self.records[index], "_score": scores[index],
                            "_retrieval": [legacy[s] for s in sources[index]],
                            "_locations": locations[index],
                            "_features": {
                                "cos": value, "z": z, "background_small": small,
                                "query_tokens": (1 if len(query_plain) == 1 and query_plain <= column_tokens
                                                 else None),
                                "coverage": (len(required & self._content_tokens[index]) / len(required)
                                             if required else 0.0),
                                "subject_coverage": ((len(subject) - len(missing)) / len(subject)
                                                     if subject else None),
                                "subject_missing": missing,
                                "name_level": level, "channels": list(sources[index]),
                                "lexical_rank": lexical_rank.get(index),
                                "semantic_rank": semantic_rank.get(digest) if index in semantic else None,
                                "distinctive_matched": matched.get(index, 0), "doc_hash": digest,
                                "table_rank": table_rank.get(self._record_table[index]),
                                "table_score": (table_state.get("scores") or {}).get(self._record_table[index])}})
        return results
