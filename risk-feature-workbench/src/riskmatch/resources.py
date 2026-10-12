"""Process-wide shared resources reused across small analysis jobs.

- EmbeddingGate: at most one embedding call in flight (the bank service gives
  no parallelism guarantee); waiting search calls go before waiting index calls.
- BackgroundIndexer: embeds the missing catalog documents once, in a daemon
  thread, through the gate's low-priority lane.
- Resources: catalog snapshot cache (validated by a cheap Oracle signature),
  one vector store, fingerprint-validated namespaces and a small index LRU.

Stdlib only. Long work (scan, fingerprint, index build) runs outside the lock.
"""

import json
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

from . import lexicon as _lexicon
from .catalog_cache import CatalogCache
from .retrieval import HybridIndex
from .vectorstore import VectorStore


_PRIORITIES = ("search", "index")
_MAX_INDEXES = 2


class EmbeddingGate:
    """Single-flight embedding gate with a priority lane for searches."""

    def __init__(self, embed_fn):
        self._embed_fn = embed_fn
        self._cond = threading.Condition()
        self._busy = False
        self._queues = {name: deque() for name in _PRIORITIES}
        self._stats = {"calls": 0, "failures": 0, "search_calls": 0, "index_calls": 0,
                       "wait_seconds_total": 0.0}
        self.search_fn = lambda texts, kind: self.embed(texts, kind, priority="search")  # noqa: E731
        self.index_fn = lambda texts, kind: self.embed(texts, kind, priority="index")  # noqa: E731

    def _turn(self, ticket, priority):
        if self._busy or self._queues[priority][0] is not ticket:
            return False
        return priority == "search" or not self._queues["search"]

    def embed(self, texts, kind, priority="search"):
        if priority not in _PRIORITIES:
            raise ValueError("priority must be 'search' or 'index'")
        ticket = object()
        started = time.monotonic()
        with self._cond:
            self._queues[priority].append(ticket)
            self._cond.notify_all()
            try:
                self._cond.wait_for(lambda: self._turn(ticket, priority))
            except BaseException:
                self._queues[priority].remove(ticket)
                self._cond.notify_all()
                raise
            self._queues[priority].popleft()
            self._busy = True
            self._stats["calls"] += 1
            self._stats[priority + "_calls"] += 1
            self._stats["wait_seconds_total"] += time.monotonic() - started
        failed = True
        try:
            result = self._embed_fn(texts, kind)
            failed = False
            return result
        finally:
            with self._cond:
                self._busy = False
                if failed:
                    self._stats["failures"] += 1
                self._cond.notify_all()

    def pending(self):
        """Waiting calls per lane (diagnostics/tests)."""
        with self._cond:
            return {name: len(queue) for name, queue in self._queues.items()}

    def stats(self):
        with self._cond:
            return dict(self._stats)


class BackgroundIndexer:
    """Embeds index.index_queue() (table documents first, then columns by schema tier) via the index lane.

    Never calls index.refresh_index(): a running job keeps its frozen semantic
    set; the next job picks the new vectors up through Resources.index().
    """

    def __init__(self, index, gate, namespace, *, max_failures=5, batch=1):
        self.index = index
        self.gate = gate
        self.namespace = namespace
        self.max_failures = max(1, int(max_failures))
        self.batch = max(1, int(batch))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._state = "idle"
        self._done = 0
        self._total = 0
        self._failures = 0
        self._message = ""
        self._started = None
        self._finished = None

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            self._stop.clear()
            self._state, self._done, self._total, self._failures = "running", 0, 0, 0
            self._message, self._started, self._finished = "", time.monotonic(), None
            self._thread = threading.Thread(target=self._run, name="riskmatch-indexer", daemon=True)
            self._thread.start()
        return self

    def stop(self, wait=True):
        self._stop.set()
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                if self._state in ("idle", "running"):
                    self._state = "stopped"
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join()

    def wait(self, timeout=None):
        """Block until the worker exits; True if it is no longer running."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def _finish(self, state, message=""):
        with self._lock:
            self._state, self._message, self._finished = state, message, time.monotonic()

    def _run(self):
        try:
            missing = self.index.index_queue()
        except Exception as error:
            self._finish("failed", "Eksik belge listesi alınamadı (%s)." % type(error).__name__)
            return
        with self._lock:
            self._total = len(missing)
        consecutive = 0
        for start in range(0, len(missing), self.batch):
            if self._stop.is_set():
                self._finish("stopped", "Arka plan indeksleme durduruldu.")
                return
            chunk = missing[start:start + self.batch]
            try:
                vectors = self.gate.index_fn([text for _h, text in chunk], "document")
                if len(vectors) != len(chunk):
                    raise ValueError("vector count mismatch")
                self.namespace.put_many(zip([h for h, _text in chunk], vectors))
            except Exception as error:
                consecutive += 1
                with self._lock:
                    self._failures += 1
                if consecutive >= self.max_failures:
                    self._finish("failed", "Arka plan indeksleme art arda %d hata sonrası durdu (%s)."
                                 % (consecutive, type(error).__name__))
                    return
                continue
            consecutive = 0
            with self._lock:
                self._done += len(chunk)
        if self._stop.is_set():
            self._finish("stopped", "Arka plan indeksleme durduruldu.")
        else:
            self._finish("done", "")

    def status(self):
        with self._lock:
            end = self._finished if self._finished is not None else time.monotonic()
            elapsed = (end - self._started) if self._started is not None else 0.0
            rate = self._done / elapsed if elapsed > 0 and self._done else 0.0
            remaining = max(0, self._total - self._done)
            eta = remaining / rate if rate > 0 else None
            return {"state": self._state, "done": self._done, "total": self._total,
                    "failures": self._failures, "rate_per_s": rate,
                    "eta_s": eta if self._state == "running" else (0.0 if self._state == "done" else None),
                    "message": self._message}


class EmbeddingContext:
    def __init__(self, gate, store, namespace, fingerprint, adapter_identity):
        self.gate = gate
        self.store = store
        self.namespace = namespace
        self.fingerprint = fingerprint
        self.adapter_identity = adapter_identity


def _canary_embed(gate):
    # VectorStore.fingerprint/verify call embed(text) once per canary.
    return lambda text: gate.search_fn([text], "document")[0]


class Resources:
    def __init__(self, root_dir, *, catalog_ttl_seconds=28800, fingerprint_ttl_seconds=600,
                 clock=time.monotonic):
        self.root = Path(root_dir)
        self.clock = clock
        self.fingerprint_ttl_seconds = fingerprint_ttl_seconds
        # Catalog entries persist on disk, so they need wall-clock time unless a test clock is injected.
        self._catalog = CatalogCache(self.root / "catalog", catalog_ttl_seconds,
                                     clock=time.time if clock is time.monotonic else clock)
        self._lock = threading.Lock()
        self._store = None
        self._gates = {}
        self._adapters = {}
        self._contexts = {}
        self._indexes = OrderedDict()
        self._indexers = {}
        self._lexicon = None
        self._closed = False

    # -- catalog ----------------------------------------------------------------

    def catalog(self, oracle_adapter, key, *, refresh=False, progress=None):
        with self._lock:
            if refresh:
                self._catalog.invalidate(key)
                cached = None
            else:
                cached = self._catalog.info(key)
        try:
            signature = oracle_adapter.signature()
        except Exception:
            signature = None
        if cached is not None and signature is not None and cached.get("signature") == signature:
            with self._lock:
                snapshot = self._catalog.get(key, signature=signature)
            if snapshot is not None:
                return snapshot, {"source": "cache", "age_seconds": cached["age_s"], "signature_checked": True}
        snapshot = oracle_adapter.scan(progress=progress)
        if signature is not None:
            with self._lock:
                self._catalog.put(key, snapshot, signature=signature)
        return snapshot, {"source": "scan", "age_seconds": 0.0, "signature_checked": signature is not None}

    # -- embedding --------------------------------------------------------------

    def _store_locked(self):
        if self._closed:
            raise RuntimeError("resources are closed")
        if self._store is None:
            self._store = VectorStore(self.root / "vectors.sqlite")
        return self._store

    def embedding(self, adapter):
        identity = getattr(adapter, "cache_identity", None) or "adapter:%x" % id(adapter)
        with self._lock:
            store = self._store_locked()
            self._adapters[identity] = adapter
            gate = self._gates.get(identity)
            if gate is None:
                gate = self._gates[identity] = EmbeddingGate(
                    lambda texts, kind, _id=identity: self._adapters[_id].embed(texts, kind=kind))
            cached = self._contexts.get(identity)
            if (cached is not None and self.clock() - cached[0] <= self.fingerprint_ttl_seconds
                    and not cached[1].namespace.tainted):
                return cached[1]
        fingerprint = store.fingerprint(_canary_embed(gate))
        namespace = store.namespace(fingerprint["namespace"])
        context = EmbeddingContext(gate, store, namespace, fingerprint, identity)
        with self._lock:
            self._contexts[identity] = (self.clock(), context)
        return context

    def verify(self, ctx, sample=None):
        if ctx.store.verify(ctx.namespace.id, _canary_embed(ctx.gate), sample=sample):
            return True
        ctx.namespace.mark_tainted()
        ns_id = ctx.namespace.id
        with self._lock:
            for identity, (_at, context) in list(self._contexts.items()):
                if context.namespace.id == ns_id:
                    del self._contexts[identity]
            for key in [k for k in self._indexes if k[1] == ns_id]:
                del self._indexes[key]
            doomed = [self._indexers.pop(k) for k in list(self._indexers) if k[1] == ns_id]
        for indexer in doomed:
            indexer.stop(wait=False)
        return False

    # -- indexes ----------------------------------------------------------------

    @staticmethod
    def _key(snapshot, ctx, settings):
        return (snapshot.get("version"), ctx.namespace.id if ctx is not None else None,
                json.dumps(settings or {}, sort_keys=True, default=str))

    def index(self, snapshot, ctx, settings, *, refresh=True):
        key = self._key(snapshot, ctx, settings)
        with self._lock:
            index = self._indexes.get(key)
            if index is not None:
                self._indexes.move_to_end(key)
            elif self._lexicon is None:
                self._lexicon = _lexicon.load()
            lexicon = self._lexicon
        if index is not None:
            if refresh:
                index.refresh_index()
            return index
        built = HybridIndex(snapshot, embed=ctx.gate.search_fn if ctx is not None else None,
                            namespace=ctx.namespace if ctx is not None else None,
                            lexicon=lexicon, **(settings or {}))
        with self._lock:
            index = self._indexes.get(key)
            if index is None:
                index = self._indexes[key] = built
            self._indexes.move_to_end(key)
            while len(self._indexes) > _MAX_INDEXES:
                self._indexes.popitem(last=False)
        return index

    def indexer(self, snapshot, ctx, settings):
        key = self._key(snapshot, ctx, settings)
        with self._lock:
            existing = self._indexers.get(key)
        if existing is not None:
            return existing
        # No refresh: a running job's frozen semantic set must not change under it.
        index = self.index(snapshot, ctx, settings, refresh=False)
        # Adaptörün tek istekte kabul ettiği kadar metin: her belge ayrı istek olursa büyük katalog saatler sürer.
        # Kapı tek uçuşludur; arama bekleyişi en çok bir istek kadardır.
        with self._lock:
            adapter = self._adapters.get(ctx.adapter_identity)
        batch = getattr(adapter, "max_batch", 1)
        created = BackgroundIndexer(index, ctx.gate, ctx.namespace,
                                    batch=batch if isinstance(batch, int) and batch > 0 else 1)
        with self._lock:
            if self._closed:
                raise RuntimeError("resources are closed")
            return self._indexers.setdefault(key, created)

    def close(self):
        with self._lock:
            self._closed = True
            indexers = list(self._indexers.values())
            self._indexers.clear()
            self._indexes.clear()
            self._contexts.clear()
            store, self._store = self._store, None
        for indexer in indexers:
            indexer.stop(wait=True)
        if store is not None:
            store.close()
