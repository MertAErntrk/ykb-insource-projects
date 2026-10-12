import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from riskmatch.catalog_cache import cache_key
from riskmatch.resources import BackgroundIndexer, EmbeddingGate, Resources
from test_retrieval import hash_vector, record, snapshot


class FakeEmbedding:
    """Deterministic embedding adapter; ``flip`` reverses vectors (a different model)."""

    def __init__(self, identity="model-a", flip=False):
        self.cache_identity = identity
        self.flip = flip
        self.calls = []
        self.hook = None

    def embed(self, texts, kind="document"):
        self.calls.append((list(texts), kind))
        if self.hook is not None:
            self.hook(texts, kind)
        vectors = [hash_vector(text) for text in texts]
        return [v[::-1] for v in vectors] if self.flip else vectors


class FakeOracle:
    def __init__(self, sig="s1", complete=True):
        self.sig = sig
        self.complete = complete
        self.scans = 0
        self.signature_error = False

    def signature(self):
        if self.signature_error:
            raise RuntimeError("ORA-12541 secret-host")
        return self.sig

    def scan(self, progress=None):
        self.scans += 1
        snap = snapshot([record("c1", "MUS_YAS", "Müşteri yaşı")])
        snap["complete"] = self.complete
        snap["version"] = "v-%s-%d" % (self.sig, self.scans)
        return snap


def catalog_snapshot(count=50, version="v1"):
    snap = snapshot([record("id%02d" % i, "KOLON_%02d_TUTAR" % i, "Açıklama numara %d" % i)
                     for i in range(count)])
    snap["version"] = version
    return snap


def wait_pending(test, gate, lane, count):
    with gate._cond:
        test.assertTrue(gate._cond.wait_for(lambda: len(gate._queues[lane]) == count, timeout=5))


class GateTests(unittest.TestCase):
    def test_single_flight_under_concurrency(self):
        lock, state = threading.Lock(), {"now": 0, "max": 0}

        def embed_fn(texts, kind):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            sum(range(2000))
            with lock:
                state["now"] -= 1
            return [[1.0] for _ in texts]

        gate = EmbeddingGate(embed_fn)
        barrier = threading.Barrier(4)

        def worker(priority):
            barrier.wait()
            for _ in range(25):
                gate.embed(["x"], "document", priority=priority)

        threads = [threading.Thread(target=worker, args=(p,)) for p in ("search", "index", "search", "index")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(state["max"], 1)
        stats = gate.stats()
        self.assertEqual(stats["calls"], 100)
        self.assertEqual((stats["search_calls"], stats["index_calls"], stats["failures"]), (50, 50, 0))

    def test_waiting_search_goes_before_waiting_index(self):
        order, started, release = [], threading.Event(), threading.Event()

        def embed_fn(texts, kind):
            order.append(texts[0])
            if texts[0] == "i1":
                started.set()
                release.wait(5)
            return [[1.0]]

        gate = EmbeddingGate(embed_fn)
        first = threading.Thread(target=gate.index_fn, args=(["i1"], "document"))
        first.start()
        self.assertTrue(started.wait(5))
        second = threading.Thread(target=gate.index_fn, args=(["i2"], "document"))
        second.start()
        wait_pending(self, gate, "index", 1)
        search = threading.Thread(target=gate.search_fn, args=(["s1"], "query"))
        search.start()
        wait_pending(self, gate, "search", 1)
        release.set()
        for thread in (first, second, search):
            thread.join(5)
        self.assertEqual(order, ["i1", "s1", "i2"])

    def test_failure_is_counted_and_gate_released(self):
        def embed_fn(texts, kind):
            raise RuntimeError("boom")

        gate = EmbeddingGate(embed_fn)
        with self.assertRaises(RuntimeError):
            gate.embed(["x"], "query")
        with self.assertRaises(RuntimeError):
            gate.embed(["x"], "query", priority="index")
        self.assertEqual(gate.stats()["failures"], 2)
        with self.assertRaises(ValueError):
            gate.embed(["x"], "query", priority="urgent")


class ResourcesTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.now = [1000.0]
        self.res = Resources(self.root, clock=lambda: self.now[0])

    def tearDown(self):
        self.res.close()
        alive = [t for t in threading.enumerate() if t.name == "riskmatch-indexer" and t.is_alive()]
        self.assertEqual(alive, [])
        shutil.rmtree(self.root, ignore_errors=True)


class CatalogTests(ResourcesTestCase):
    def test_signature_validated_cache_and_refresh(self):
        oracle = FakeOracle()
        key = cache_key("user", "dsn", "PDB1", ["A", "B"])
        snap, info = self.res.catalog(oracle, key)
        self.assertEqual((info["source"], oracle.scans), ("scan", 1))
        again, info = self.res.catalog(oracle, key)
        self.assertEqual((info["source"], oracle.scans), ("cache", 1))
        self.assertTrue(info["signature_checked"])
        self.assertEqual(again["version"], snap["version"])
        oracle.sig = "s2"
        _snap, info = self.res.catalog(oracle, key)
        self.assertEqual((info["source"], oracle.scans), ("scan", 2))
        _snap, info = self.res.catalog(oracle, key, refresh=True)
        self.assertEqual((info["source"], oracle.scans), ("scan", 3))
        oracle.signature_error = True
        _snap, info = self.res.catalog(oracle, key)
        self.assertEqual((info["source"], oracle.scans), ("scan", 4))
        self.assertFalse(info["signature_checked"])

    def test_incomplete_snapshot_is_not_cached(self):
        oracle = FakeOracle(complete=False)
        key = cache_key("user", "dsn", "PDB1", ["A"])
        self.res.catalog(oracle, key)
        _snap, info = self.res.catalog(oracle, key)
        self.assertEqual((info["source"], oracle.scans), ("scan", 2))


class EmbeddingTests(ResourcesTestCase):
    def test_fingerprint_cached_within_ttl(self):
        adapter = FakeEmbedding()
        ctx = self.res.embedding(adapter)
        self.assertEqual(len(adapter.calls), 8)
        self.assertIs(self.res.embedding(adapter), ctx)
        self.assertEqual(len(adapter.calls), 8)
        self.now[0] += 601
        renewed = self.res.embedding(adapter)
        self.assertEqual(len(adapter.calls), 16)
        self.assertEqual(renewed.namespace.id, ctx.namespace.id)
        self.assertEqual(renewed.fingerprint["status"], "same")
        self.assertIs(renewed.gate, ctx.gate)

    def test_different_vectors_get_different_namespace(self):
        first = self.res.embedding(FakeEmbedding("model-a"))
        second = self.res.embedding(FakeEmbedding("model-b", flip=True))
        self.assertNotEqual(first.namespace.id, second.namespace.id)
        self.assertIsNot(first.gate, second.gate)

    def test_failed_verify_taints_and_next_context_is_new(self):
        adapter = FakeEmbedding()
        ctx = self.res.embedding(adapter)
        self.assertTrue(self.res.verify(ctx))
        adapter.flip = True
        self.assertFalse(self.res.verify(ctx))
        self.assertTrue(ctx.namespace.tainted)
        adapter.flip = False
        fresh = self.res.embedding(adapter)
        self.assertNotEqual(fresh.namespace.id, ctx.namespace.id)
        self.assertEqual(fresh.fingerprint["status"], "new")


class IndexTests(ResourcesTestCase):
    def test_index_reused_and_refreshed(self):
        ctx = self.res.embedding(FakeEmbedding())
        snap = catalog_snapshot(5)
        index = self.res.index(snap, ctx, {"pool_size": 10})
        index.refresh_index = mock.Mock(wraps=index.refresh_index)
        self.assertIs(self.res.index(snap, ctx, {"pool_size": 10}), index)
        index.refresh_index.assert_called_once_with()
        other = self.res.index(catalog_snapshot(5, version="v2"), ctx, {"pool_size": 10})
        self.assertIsNot(other, index)
        self.res.index(catalog_snapshot(5, version="v3"), None, {})
        self.assertEqual(len(self.res._indexes), 2)

    def test_lexical_only_index_without_context(self):
        index = self.res.index(catalog_snapshot(3), None, {})
        self.assertIsNone(index.namespace)


class IndexerTests(ResourcesTestCase):
    def setUp(self):
        super().setUp()
        self.adapter = FakeEmbedding()
        self.ctx = self.res.embedding(self.adapter)
        self.adapter.calls.clear()
        self.snap = catalog_snapshot(50)

    def test_indexes_all_missing_documents(self):
        indexer = self.res.indexer(self.snap, self.ctx, {})
        self.assertIs(self.res.indexer(self.snap, self.ctx, {}), indexer)
        indexer.start()
        indexer.start()
        self.assertTrue(indexer.wait(10))
        status = indexer.status()
        # 50 kolon belgesi + 1 tablo belgesi (A.FEATURES); kapsam yüzdesi yalnız kolonları sayar.
        self.assertEqual((status["state"], status["done"], status["total"]), ("done", 51, 51))
        self.assertEqual(self.ctx.namespace.count(), 51)
        index = self.res.index(self.snap, self.ctx, {})
        self.assertEqual(index.index_coverage, 1.0)
        self.assertEqual(index.missing_documents(), [])

    def test_indexer_sends_adapter_batch_per_request(self):
        self.adapter.max_batch = 16
        indexer = self.res.indexer(self.snap, self.ctx, {})
        self.assertEqual(indexer.batch, 16)
        indexer.start()
        self.assertTrue(indexer.wait(10))
        # 51 belge: 16 + 16 + 16 + 3; belge başına ayrı istek yok.
        self.assertEqual([len(texts) for texts, _kind in self.adapter.calls], [16, 16, 16, 3])
        self.assertEqual(indexer.status()["done"], 51)

    def test_queue_starts_with_table_documents(self):
        index = self.res.index(self.snap, self.ctx, {})
        queue = index.index_queue()
        self.assertEqual(len(queue), 51)
        self.assertTrue(queue[0][1].startswith("FEATURES"))
        self.assertEqual(index.stored_coverage(), 0.0)

    def test_stop_midway(self):
        reached, release, count = threading.Event(), threading.Event(), [0]

        def hook(texts, kind):
            count[0] += 1
            if count[0] == 10:
                reached.set()
                release.wait(5)

        self.adapter.hook = hook
        indexer = self.res.indexer(self.snap, self.ctx, {}).start()
        self.assertTrue(reached.wait(5))
        indexer.stop(wait=False)
        release.set()
        self.assertTrue(indexer.wait(10))
        status = indexer.status()
        self.assertEqual(status["state"], "stopped")
        self.assertLess(status["done"], 50)

    def test_consecutive_failures_fail_safely(self):
        def hook(texts, kind):
            raise RuntimeError("ORA-01017 user/secret_password@prod-host")

        self.adapter.hook = hook
        index = self.res.index(self.snap, self.ctx, {})
        indexer = BackgroundIndexer(index, self.ctx.gate, self.ctx.namespace, max_failures=3).start()
        self.assertTrue(indexer.wait(10))
        status = indexer.status()
        self.assertEqual((status["state"], status["failures"], status["done"]), ("failed", 3, 0))
        self.assertNotIn("secret", status["message"])
        self.assertNotIn("ORA-", status["message"])

    def test_search_calls_jump_ahead_of_indexing(self):
        started, release = threading.Event(), threading.Event()

        def hook(texts, kind):
            if kind == "document" and not started.is_set():
                started.set()
                release.wait(5)

        self.adapter.hook = hook
        indexer = self.res.indexer(self.snap, self.ctx, {}).start()
        self.assertTrue(started.wait(5))
        result = []
        search = threading.Thread(target=lambda: result.append(self.ctx.gate.search_fn(["sorgu"], "query")))
        search.start()
        wait_pending(self, self.ctx.gate, "search", 1)
        release.set()
        search.join(5)
        self.assertEqual(len(result), 1)
        self.assertTrue(indexer.wait(10))
        self.assertEqual(self.adapter.calls[1], (["sorgu"], "query"))
        self.assertEqual(indexer.status()["state"], "done")


if __name__ == "__main__":
    unittest.main()
