"""Bounded semantic reranking for large real catalogs, without network access."""

import copy
import sys
import tempfile
import unittest
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riskmatch.errors import AnalysisCancelled, InputError
from riskmatch.retrieval import HybridIndex


SCOPE = {"source_database": "EDWLIVE", "environment": "live",
         "container": "EDWLIVE", "schemas": ["DM_CAD", "OTHER"]}


def record(identifier, column, comment="", schema="DM_CAD"):
    return {"catalog_id": str(identifier), "source_database": "EDWLIVE",
            "environment": "live", "container": "EDWLIVE", "schema": schema,
            "object": "FACT", "column": column, "column_comment": comment,
            "table_comment": "", "metadata_version": "v1"}


def snapshot(records):
    return {"scope": copy.deepcopy(SCOPE), "records": records, "version": "v1", "complete": True}


class CountingEmbedding:
    model = "controlled-test"
    model_version = "v1"
    dimensions = 4096
    max_batch = 1
    persistent_cache_safe = False

    def __init__(self, fail=False, invalid=False):
        self.calls = []
        self.fail = fail
        self.invalid = invalid

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("provider-secret-that-must-not-leak")
        if self.invalid:
            return [[0.0] * self.dimensions for _ in texts]
        return [[1.0] + [0.0] * (self.dimensions - 1) for _ in texts]


class OptimizedRetrievalTests(unittest.TestCase):
    def test_10000_columns_273_queries_have_bounded_vectors_and_comparisons(self):
        data = snapshot([record(i, "TARGET_%05d" % i, "application amount") for i in range(10000)])
        embedding = CountingEmbedding()
        progress = []
        index = HybridIndex(data, embedding, candidate_limit=24, embedding_budget=256,
                            progress=progress.append)
        self.assertEqual(embedding.calls, [])
        for number in range(273):
            hits = index.search({"name": "TARGET_%05d" % (9999 - number)})
            self.assertEqual(hits[0]["catalog_id"], str(9999 - number))
            self.assertLessEqual(index.last_search_info["candidate_count"], 24)
            self.assertTrue(index.last_search_info["candidate_limited"])
        self.assertEqual(index.stats["catalog_vectors_embedded"], 256)
        self.assertLessEqual(index.stats["query_vectors_embedded"], 273)
        self.assertLessEqual(len(embedding.calls), 256 + 273)
        self.assertLessEqual(index.stats["vector_comparisons"], 273 * 24)
        self.assertTrue(index.stats["embedding_budget_exhausted"])
        self.assertEqual(progress[0]["items_completed"], 0)
        self.assertLessEqual(progress[0]["items_total"], 24)
        self.assertTrue(all(item["phase"] == "embedding" for item in progress))

    def test_no_lexical_match_never_embeds_arbitrary_catalog_prefix(self):
        embedding = CountingEmbedding()
        index = HybridIndex(snapshot([record(i, "TOTAL", "amount") for i in range(100)]),
                            embedding, candidate_limit=24, embedding_budget=256)
        self.assertEqual(index.search({"name": "unrelatedfoobar"}), [])
        self.assertEqual(embedding.calls, [])
        self.assertTrue(index.last_search_info["candidate_limited"])
        self.assertTrue(index.last_search_info["embedding_skipped"])
        self.assertIn("kısa liste", index.last_search_info["warning"])

    def test_scope_is_applied_before_shortlist_and_provider_calls(self):
        embedding = CountingEmbedding()
        index = HybridIndex(snapshot([record("a", "AMOUNT", "allowed"),
                                      record("b", "AMOUNT", "FORBIDDEN", schema="OTHER")]),
                            embedding, candidate_limit=24, embedding_budget=256)
        hits = index.search({"name": "AMOUNT", "scope": {"schemas": ["DM_CAD"]}})
        self.assertEqual([hit["catalog_id"] for hit in hits], ["a"])
        self.assertNotIn("FORBIDDEN", str(embedding.calls))
        previous = len(embedding.calls)
        self.assertEqual(index.search({"name": "AMOUNT", "scope": {"environment": "elsewhere"}}), [])
        self.assertEqual(len(embedding.calls), previous)

    def test_exact_physical_match_reserves_slot_even_at_end_of_catalog(self):
        embedding = CountingEmbedding()
        data = snapshot([record(i, "OTHER", "target target target") for i in range(100)] +
                        [record("last", "TARGET", "")])
        index = HybridIndex(data, embedding, candidate_limit=1, embedding_budget=1)
        hits = index.search({"name": "TARGET"})
        self.assertEqual(hits[0]["catalog_id"], "last")
        self.assertIn("exact", hits[0]["_retrieval"])
        self.assertTrue(embedding.calls[0][0].startswith("TARGET "))

    def test_identical_catalog_text_deduplicates_and_expansion_reuses_query(self):
        embedding = CountingEmbedding()
        index = HybridIndex(snapshot([record("a", "LIMIT", "credit limit"),
                                      record("b", "LIMIT", "credit limit")]),
                            embedding, candidate_limit=24, embedding_budget=10)
        request = {"name": "LIMIT", "description": "credit limit"}
        index.search(request, limit=1)
        calls = len(embedding.calls)
        expanded = index.search(request, limit=24, expanded=True)
        self.assertEqual(len(expanded), 2)
        self.assertEqual(len(embedding.calls), calls)
        self.assertEqual(index.stats["catalog_vectors_embedded"], 1)
        self.assertEqual(index.stats["query_vectors_embedded"], 1)
        self.assertEqual(index.stats["query_cache_hits"], 1)
        self.assertIsInstance(next(iter(index._text_vectors.values())), array)

    def test_exhausted_budget_preserves_lexical_candidates_and_discloses_skip(self):
        embedding = CountingEmbedding()
        index = HybridIndex(snapshot([record("a", "ALPHA"), record("b", "OMEGA")]),
                            embedding, candidate_limit=24, embedding_budget=1)
        index.search({"name": "ALPHA"})
        previous = len(embedding.calls)
        result = index.search({"name": "OMEGA"})
        self.assertEqual(result[0]["catalog_id"], "b")
        self.assertNotIn("embedding", result[0]["_retrieval"])
        self.assertEqual(len(embedding.calls), previous)
        self.assertTrue(index.last_search_info["embedding_skipped"])
        self.assertEqual(index.last_search_info["embedding_skipped_count"], 1)
        self.assertIn("bütçesine", index.last_search_info["warning"])

    def test_provider_failure_opens_circuit_and_never_leaks_raw_error(self):
        embedding = CountingEmbedding(fail=True)
        index = HybridIndex(snapshot([record("a", "LIMIT", "credit limit")]),
                            embedding, candidate_limit=24, embedding_budget=256)
        for number in range(273):
            hits = index.search({"name": "LIMIT", "description": "item %d" % number})
            self.assertEqual(hits[0]["catalog_id"], "a")
        self.assertEqual(len(embedding.calls), 1)
        self.assertEqual(index.stats["embedding_failures"], 1)
        self.assertTrue(index.stats["circuit_open"])
        self.assertTrue(index.last_search_info["embedding_skipped"])
        self.assertNotIn("provider-secret", index.last_search_info["warning"])

    def test_invalid_vector_degrades_to_lexical_and_opens_circuit(self):
        index = HybridIndex(snapshot([record("a", "LIMIT")]), CountingEmbedding(invalid=True),
                            candidate_limit=24, embedding_budget=256)
        self.assertEqual(index.search({"name": "LIMIT"})[0]["catalog_id"], "a")
        self.assertTrue(index.stats["circuit_open"])
        self.assertEqual(index.stats["catalog_vectors_embedded"], 0)

    def test_cancellation_before_first_network_call_and_between_batches(self):
        for stop_at in (0, 1):
            embedding = CountingEmbedding()
            state = {"stop": False}

            def update(event):
                if event["items_completed"] >= stop_at:
                    state["stop"] = True

            index = HybridIndex(snapshot([record("a", "LIMIT"), record("b", "LIMIT")]),
                                embedding, candidate_limit=24, embedding_budget=256,
                                cancelled=lambda: state["stop"], progress=update)
            with self.assertRaises(AnalysisCancelled):
                index.search({"name": "LIMIT"})
            self.assertEqual(len(embedding.calls), stop_at)
            self.assertFalse(index.stats["circuit_open"])

    def test_unversioned_cache_is_local_to_index_and_ignores_disk(self):
        embedding = CountingEmbedding()
        data = snapshot([record("a", "LIMIT")])
        with tempfile.TemporaryDirectory() as directory:
            for _ in range(2):
                index = HybridIndex(data, embedding, cache_dir=directory,
                                    candidate_limit=24, embedding_budget=256)
                index.search({"name": "LIMIT"})
            self.assertEqual(len(embedding.calls), 4)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_full_mode_keeps_all_candidates_and_emits_per_batch_progress(self):
        embedding = CountingEmbedding()
        progress = []
        index = HybridIndex(snapshot([record("a", "ALPHA"), record("b", "OMEGA")]),
                            embedding, progress=progress.append)
        hits = index.search({"name": "unrelated"}, strategy="embedding")
        self.assertEqual(len(hits), 2)
        self.assertEqual(len(embedding.calls), 3)
        self.assertIn((0, 2), [(item["items_completed"], item["items_total"]) for item in progress])
        self.assertIn((2, 2), [(item["items_completed"], item["items_total"]) for item in progress])

    def test_configuration_bounds(self):
        for options in ({"candidate_limit": 0}, {"candidate_limit": True},
                        {"embedding_budget": -1}, {"embedding_budget": False}):
            with self.assertRaises(InputError):
                HybridIndex(snapshot([]), **options)


if __name__ == "__main__":
    unittest.main()
