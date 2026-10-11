"""Persistent vector store: fingerprinting, quantized search, concurrency, no raw text."""

import hashlib
import math
import random
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riskmatch.vectorstore import DEFAULT_CANARIES, VectorStore, VectorStoreError, text_hash


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def clustered(n, dims, clusters, seed, spread=0.6):
    rng = random.Random(seed)
    r = rng.random
    centers = [[rng.gauss(0, 1) for _ in range(dims)] for _ in range(clusters)]
    items = []
    for i in range(n):
        c = centers[i % clusters]
        items.append((text_hash("item-%d-%d" % (seed, i)), [x + spread * (r() - 0.5) * 3.4 for x in c]))
    queries = [[x + spread * (r() - 0.5) * 3.4 for x in centers[j]] for j in (0, clusters // 3, clusters // 2, clusters - 1)]
    return items, queries


def brute(items, query, k):
    q = _unit(query)
    scored = [(sum(a * b for a, b in zip(q, _unit(v))), h) for h, v in items]
    scored.sort(reverse=True)
    return [h for _s, h in scored[:k]]


def projection(seed, dims, src=64):
    rng = random.Random(seed)
    return [[rng.gauss(0, 1) for _ in range(src)] for _ in range(dims)]


def make_embed(matrix, noise=0.0, noise_seed=0, src=64):
    noise_rng = random.Random(noise_seed)

    def embed(text):
        rng = random.Random(hashlib.sha256(text.encode("utf-8")).digest())
        base = [rng.gauss(0, 1) for _ in range(src)]
        out = [sum(a * b for a, b in zip(row, base)) for row in matrix]
        if noise:
            norm = math.sqrt(sum(x * x for x in out))
            out = [x / norm + noise_rng.gauss(0, noise) for x in out]
        return out
    return embed


class VectorStoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "sub" / "vectors.sqlite"
        self.store = VectorStore(self.path)
        self.model_a = make_embed(projection(1, 256))

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def new_ns(self, embed=None):
        fp = self.store.fingerprint(embed or self.model_a)
        return self.store.namespace(fp["namespace"])

    def assert_recall(self, ns, items, queries, label):
        hits = []
        for q in queries:
            got = [h for h, _s in ns.search(q, k=10, coarse_k=300)]
            want = brute(items, q, 10)
            hits.append(len(set(got) & set(want)))
        avg = sum(hits) / len(hits)
        print("\n[%s] recall@10 per query=%s avg=%.2f" % (label, hits, avg))
        self.assertGreaterEqual(avg, 9.0)

    # (1) recall
    def test_recall_256(self):
        ns = self.new_ns()
        items, queries = clustered(5000, 256, 50, seed=7)
        self.assertEqual(ns.put_many(items), 5000)
        self.assertEqual(ns.count(), 5000)
        self.assert_recall(ns, items, queries, "dims=256 n=5000")

    # (1) + (5) recall and timing at 4096 dims
    def test_recall_and_timing_4096(self):
        embed = make_embed(projection(2, 4096))
        ns = self.new_ns(embed)
        items, queries = clustered(2000, 4096, 40, seed=11)
        t0 = time.perf_counter()
        ns.put_many(items)
        put_s = time.perf_counter() - t0
        self.assert_recall(ns, items, queries[:3], "dims=4096 n=2000")
        extra, _q = clustered(8000, 4096, 40, seed=12)
        t0 = time.perf_counter()
        ns.put_many(extra)
        put_s += time.perf_counter() - t0
        self.assertEqual(ns.count(), 10000)
        t0 = time.perf_counter()
        cands = [h for h, _d in ns.coarse(queries[0], k=300)]
        t1 = time.perf_counter()
        ns.rescore(queries[0], cands)
        t2 = time.perf_counter()
        print("[timing dims=4096 n=10000] put_many=%.2fs coarse=%.1fms rescore=%.1fms total=%.1fms"
              % (put_s, (t1 - t0) * 1e3, (t2 - t1) * 1e3, (t2 - t0) * 1e3))

    # (2) fingerprint
    def test_fingerprint_same_function(self):
        first = self.store.fingerprint(self.model_a)
        self.assertEqual(first["status"], "new")
        self.assertEqual(first["dims"], 256)
        self.assertIsNone(first["detail"]["min_cos"])
        again = self.store.fingerprint(self.model_a)
        self.assertEqual(again["status"], "same")
        self.assertEqual(again["namespace"], first["namespace"])
        self.assertGreaterEqual(again["detail"]["min_cos"], 0.9999)
        self.assertTrue(self.store.verify(first["namespace"], self.model_a))

    def test_fingerprint_small_noise_is_same(self):
        first = self.store.fingerprint(self.model_a)
        noisy = make_embed(projection(1, 256), noise=1e-4, noise_seed=5)
        again = self.store.fingerprint(noisy)
        self.assertEqual(again["status"], "same")
        self.assertEqual(again["namespace"], first["namespace"])

    def test_fingerprint_other_projection_is_new(self):
        first = self.store.fingerprint(self.model_a)
        other = make_embed(projection(99, 256))
        fp = self.store.fingerprint(other)
        self.assertEqual(fp["status"], "new")
        self.assertNotEqual(fp["namespace"], first["namespace"])
        self.assertLess(fp["detail"]["min_cos"], 0.995)
        self.assertFalse(self.store.verify(first["namespace"], other))

    def test_fingerprint_other_dims_is_new(self):
        first = self.store.fingerprint(self.model_a)
        fp = self.store.fingerprint(make_embed(projection(1, 128)))
        self.assertEqual(fp["status"], "new")
        self.assertEqual(fp["dims"], 128)
        self.assertNotEqual(fp["namespace"], first["namespace"])
        self.assertFalse(self.store.verify(first["namespace"], make_embed(projection(1, 128))))

    def test_fingerprint_tainted_is_new(self):
        first = self.store.fingerprint(self.model_a)
        ns = self.store.namespace(first["namespace"])
        ns.mark_tainted()
        self.assertTrue(ns.tainted)
        fp = self.store.fingerprint(self.model_a)
        self.assertEqual(fp["status"], "new")
        self.assertNotEqual(fp["namespace"], first["namespace"])

    def test_fingerprint_skips_damaged_canary_record(self):
        first = self.store.fingerprint(self.model_a)
        self.store.close()
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE namespaces SET canaries = ? WHERE id = ?", (b"\x01\x02", first["namespace"]))
        self.store = VectorStore(self.path)
        fp = self.store.fingerprint(self.model_a)
        self.assertEqual(fp["status"], "new")
        self.assertNotEqual(fp["namespace"], first["namespace"])

    def test_fingerprint_persists_across_reopen(self):
        first = self.store.fingerprint(self.model_a)
        self.store.close()
        self.store = VectorStore(self.path)
        self.assertEqual(self.store.fingerprint(self.model_a)["namespace"], first["namespace"])

    def test_unknown_namespace(self):
        with self.assertRaises(KeyError):
            self.store.namespace("0" * 16)

    # (3) round trip
    def test_put_get_roundtrip(self):
        ns = self.new_ns()
        items, _q = clustered(50, 256, 5, seed=3)
        ns.put(items[0][0], items[0][1])
        ns.put_many(items[1:])
        got = ns.get_many([h for h, _v in items] + [text_hash("missing")])
        self.assertEqual(len(got), 50)
        for h, v in items:
            g = got[h]
            self.assertEqual(g.typecode, "f")
            self.assertAlmostEqual(math.sqrt(sum(x * x for x in g)), 1.0, places=4)
            self.assertGreaterEqual(sum(a * b for a, b in zip(_unit(v), g)), 0.999)
        self.assertEqual(ns.has([items[0][0], text_hash("missing")]), {items[0][0]})
        ns.put(items[0][0], items[1][1])  # overwrite keeps count
        self.assertEqual(ns.count(), 50)
        sample = ns.sample(10, seed="s1")
        self.assertEqual(sample, ns.sample(10, seed="s1"))
        self.assertEqual(len(set(sample)), 10)
        self.assertNotEqual(sample, ns.sample(10, seed="s2"))
        restricted = ns.coarse(items[3][1], k=5, restrict={items[3][0], items[4][0]})
        self.assertEqual(restricted[0][0], items[3][0])
        self.assertEqual(len(restricted), 2)

    # (4) concurrency
    def test_concurrent_put_and_search(self):
        ns = self.new_ns()
        items, queries = clustered(1200, 256, 20, seed=5)
        ns.put_many(items[:200])
        errors = []

        def writer():
            try:
                for i in range(200, 1200, 50):
                    ns.put_many(items[i:i + 50])
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        def reader():
            try:
                for i in range(30):
                    self.assertTrue(ns.search(queries[i % len(queries)], k=10))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(ns.count(), 1200)
        self.assertEqual(len(ns.coarse(queries[0], k=5000)), 1200)

    # (6) prune
    def test_prune_keeps_most_recent(self):
        ids = [self.store.fingerprint(make_embed(projection(s, 64)))["namespace"] for s in (21, 22, 23)]
        ns_old = self.store.namespace(ids[0])
        ns_old.put(text_hash("x"), [1.0] * 64)
        self.store.fingerprint(make_embed(projection(21, 64)))  # touch the oldest
        removed = self.store.prune(keep=2)
        self.assertEqual(removed, [ids[1]])
        with self.assertRaises(KeyError):
            self.store.namespace(ids[1])
        self.assertEqual(self.store.namespace(ids[0]).count(), 1)
        self.assertEqual(sorted(self.store.prune(keep=0)), sorted([ids[0], ids[2]]))
        self.assertEqual(self.store._read("t", "SELECT COUNT(*) FROM vectors")[0][0], 0)

    # (7) invalid vectors
    def test_invalid_vectors_raise(self):
        ns = self.new_ns()
        h = text_hash("a")
        for bad in ([1.0] * 255, [0.0] * 256, [float("nan")] + [1.0] * 255,
                    [float("inf")] + [1.0] * 255, ["x"] * 256, None):
            with self.assertRaises(ValueError):
                ns.put(h, bad)
        with self.assertRaises(ValueError):
            ns.put("Son 3 ay ortalama mevduat bakiyesi", [1.0] * 256)
        with self.assertRaises(ValueError):
            ns.put_many([(h, [1.0] * 256), (text_hash("b"), [0.0] * 256)])
        self.assertEqual(ns.count(), 0)
        with self.assertRaises(ValueError):
            self.store.fingerprint(lambda t: [float("nan")] * 8)
        with self.assertRaises(ValueError):
            self.store.fingerprint(lambda t: [1.0] * (len(t) % 3 + 4))
        with self.assertRaises(ValueError):
            self.store.fingerprint(lambda t: [0.0] * 8)

    def test_sqlite_errors_wrapped(self):
        ns = self.new_ns()
        self.store._conn().execute("DROP TABLE vectors")
        with self.assertRaises(VectorStoreError) as ctx:
            ns.count()
        self.assertNotIn(str(self.path.parent), str(ctx.exception))
        self.assertNotIn("vectors.sqlite", str(ctx.exception))

    # (8) no raw text on disk
    def test_no_raw_text_in_files(self):
        texts = ["Son 3 ay ortalama mevduat bakiyesi", "Gizli kolon açıklaması XYZ", "MUSTERI_GELIR_SEVIYESI"]
        ns = self.new_ns()
        for i, t in enumerate(texts):
            ns.put(text_hash(t), self.model_a(t))
        self.store.close()
        blob = b"".join(p.read_bytes() for p in self.path.parent.iterdir() if p.is_file())
        self.assertTrue(blob)
        for t in list(texts) + list(DEFAULT_CANARIES):
            self.assertNotIn(t.encode("utf-8"), blob)


if __name__ == "__main__":
    unittest.main()
