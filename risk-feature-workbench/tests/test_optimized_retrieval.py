"""Large-catalog retrieval: bounded work per row, scope first, controlled degradation."""

import copy
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from riskmatch.errors import AnalysisCancelled
from riskmatch.retrieval import HybridIndex
from test_retrieval import HashEmbed, frozen_namespace


SCOPE = {"source_database": "EDWLIVE", "environment": "live",
         "container": "EDWLIVE", "schemas": ["DM_CAD", "OTHER"]}
WORDS = ["kredi", "kart", "mevduat", "bakiye", "limit", "gecikme", "tutar", "faiz", "odeme", "basvuru"]


def record(identifier, column, comment="", schema="DM_CAD"):
    return {"catalog_id": str(identifier), "source_database": "EDWLIVE",
            "environment": "live", "container": "EDWLIVE", "schema": schema,
            "object": "FACT", "column": column, "column_comment": comment,
            "table_comment": "", "metadata_version": "v1"}


def snapshot(records):
    return {"scope": copy.deepcopy(SCOPE), "records": records, "version": "v1", "complete": True}


def catalog(size):
    return snapshot([record(i, "%s_%s_%05d" % (WORDS[i % 10].upper(), WORDS[i // 10 % 10].upper(), i),
                            "%s %s son %d ay" % (WORDS[i % 7], WORDS[i % 3], i % 12 + 1)) for i in range(size)])


def requests(count):
    return [{"name": "%s_%s_%05d" % (WORDS[n % 10].upper(), WORDS[n % 9].upper(), 9999 - n),
             "description": "%s son %d ay %s" % (WORDS[n % 4], n % 12 + 1, WORDS[n % 6])} for n in range(count)]


class OptimizedRetrievalTests(unittest.TestCase):
    def test_10000_columns_273_lexical_searches_under_5_seconds(self):
        data = catalog(10000)
        start = time.perf_counter()
        index = HybridIndex(data)
        built = time.perf_counter() - start
        start = time.perf_counter()
        for request in requests(273):
            self.assertLessEqual(len(index.search(request)), 30)
        hit = index.search({"name": data["records"][4321]["column"]})[0]
        self.assertEqual((hit["catalog_id"], hit["_features"]["name_level"]), ("4321", "exact"))
        elapsed = time.perf_counter() - start
        print("\n10k lexical: build %.2fs, 273 searches %.2fs" % (built, elapsed))
        self.assertLess(elapsed, 5.0)
        self.assertEqual(index.last_search_info["semantic_scope"], "none")

    def test_3000_columns_273_hybrid_searches_with_frozen_index_under_15_seconds(self):
        data = catalog(3000)
        embed = HashEmbed()
        index = HybridIndex(data, embed=embed)
        frozen_namespace(index, embed)
        self.assertEqual(index.index_coverage, 1.0)
        start = time.perf_counter()
        for request in requests(273):
            hits = index.search(request)
            self.assertEqual(index.last_search_info["semantic_scope"], "full")
            self.assertLessEqual(len(hits), 30)
        elapsed = time.perf_counter() - start
        print("\n3k hybrid (frozen full): 273 searches %.2fs, stats %s" % (elapsed, index.stats))
        self.assertLess(elapsed, 15.0)
        self.assertEqual(index.stats["document_calls"], 0)  # frozen fill happened outside the index

    def test_no_lexical_match_never_embeds_arbitrary_catalog_prefix(self):
        embed = HashEmbed()
        index = HybridIndex(snapshot([record(i, "TOTAL", "amount") for i in range(100)]),
                            embed=embed, background_size=0)
        self.assertEqual(index.search({"name": "unrelatedfoobar"}), [])
        self.assertEqual([kind for kind, _ in embed.calls], ["query"])

    def test_scope_is_applied_before_shortlist_and_provider_calls(self):
        embed = HashEmbed()
        index = HybridIndex(snapshot([record("a", "AMOUNT", "allowed"),
                                      record("b", "AMOUNT", "FORBIDDEN", schema="OTHER")]),
                            embed=embed, background_size=0)
        hits = index.search({"name": "AMOUNT", "scope": {"schemas": ["DM_CAD"]}})
        self.assertEqual([hit["catalog_id"] for hit in hits], ["a"])
        self.assertNotIn("FORBIDDEN", str(embed.calls))
        previous = len(embed.calls)
        self.assertEqual(index.search({"name": "AMOUNT", "scope": {"environment": "elsewhere"}}), [])
        self.assertEqual(len(embed.calls), previous)

    def test_exact_physical_match_ranks_first_even_at_end_of_catalog(self):
        data = snapshot([record(i, "OTHER_%d" % i, "target target target") for i in range(100)] +
                        [record("last", "TARGET", "")])
        hits = HybridIndex(data, pool_size=5).search({"name": "TARGET"})
        self.assertEqual(hits[0]["catalog_id"], "last")
        self.assertIn("exact", hits[0]["_retrieval"])
        self.assertEqual(hits[0]["_features"]["name_level"], "exact")
        self.assertEqual(len(hits), 5)

    def test_query_vector_is_cached_per_job(self):
        embed = HashEmbed()
        index = HybridIndex(snapshot([record("a", "LIMIT", "credit limit"),
                                      record("b", "LIMIT", "credit limit")]), embed=embed)
        request = {"name": "LIMIT", "description": "credit limit"}
        index.search(request, limit=1)
        calls = len(embed.calls)
        # v0.5: identical (column, comment) copies collapse into one record; the copy moves to _locations.
        hits = index.search(request, limit=24, expanded=True)
        self.assertEqual(len(hits), 1)
        self.assertEqual(len(hits[0]["_locations"]), 1)
        self.assertEqual(len(embed.calls), calls)
        self.assertEqual(index.stats["query_vectors_embedded"], 1)
        self.assertEqual(index.stats["query_cache_hits"], 1)

    def test_invalid_vector_degrades_to_lexical(self):
        index = HybridIndex(snapshot([record("a", "LIMIT")]),
                            embed=lambda texts, kind: [[0.0, 0.0] for _ in texts])
        hit = index.search({"name": "LIMIT"})[0]
        self.assertEqual(hit["catalog_id"], "a")
        self.assertIsNone(hit["_features"]["cos"])
        self.assertEqual(index.stats["embedding_failures"], 1)
        self.assertEqual(index.stats["catalog_vectors_embedded"], 0)

    def test_cancellation_before_first_network_call_and_between_batches(self):
        for stop_at in (0, 1):
            embed = HashEmbed()
            state = {"stop": False}

            def update(event):
                if event["items_completed"] >= stop_at:
                    state["stop"] = True

            index = HybridIndex(snapshot([record("a", "LIMIT"), record("b", "LIMIT")]),
                                embed=embed, cancelled=lambda: state["stop"], progress=update)
            with self.assertRaises(AnalysisCancelled):
                index.search({"name": "LIMIT"})
            self.assertEqual(len(embed.calls), stop_at)
            self.assertFalse(index.stats["circuit_open"])

    def test_legacy_embedding_object_and_deprecated_options_are_accepted(self):
        class Legacy:
            def __init__(self):
                self.kinds = []

            def embed(self, texts, kind="document"):
                self.kinds.append(kind)
                return HashEmbed()(texts, kind)

        legacy = Legacy()
        with tempfile.TemporaryDirectory() as directory:
            index = HybridIndex(snapshot([record("a", "LIMIT")]), embedding=legacy, cache_dir=directory,
                                candidate_limit=24, embedding_budget=0)
            self.assertEqual(index.search({"name": "LIMIT"})[0]["catalog_id"], "a")
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertIn("query", legacy.kinds)
        self.assertIn("document", legacy.kinds)
        self.assertFalse(index.last_search_info["candidate_limited"])


if __name__ == "__main__":
    unittest.main()
