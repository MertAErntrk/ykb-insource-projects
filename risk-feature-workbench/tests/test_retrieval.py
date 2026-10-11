import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riskmatch.errors import IntegrationError, StaleIndexError
from riskmatch.retrieval import HybridIndex, _tokens


SCOPE = {"source_database": "risk", "environment": "dev", "container": "PDB1",
         "schemas": ["A", "B"]}


def record(identifier, column, comment, schema="A"):
    return {"catalog_id": identifier, "source_database": "risk", "environment": "dev",
            "container": "PDB1", "schema": schema, "object": "FEATURES", "column": column,
            "column_comment": comment, "table_comment": "Risk features", "metadata_version": identifier + "-v1"}


def snapshot(records):
    return {"scope": copy.deepcopy(SCOPE), "records": records, "version": "v1", "complete": True}


class FakeEmbedding:
    model = "synthetic-test-vectorizer"
    model_version = "v1"
    dimensions = 2

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


class RetrievalTests(unittest.TestCase):
    def test_turkish_tokens_and_exact(self):
        self.assertEqual(_tokens("BaşvuruKanal_L3M"), ["basvuru", "kanal", "l", "3", "m"])
        data = snapshot([record("a", "BaşvuruKanal", "başvuru kanalı"),
                         record("b", "Faiz", "faiz türü")])
        hits = HybridIndex(data).search({"name": "BaşvuruKanal"}, strategy="exact")
        self.assertEqual([item["catalog_id"] for item in hits], ["a"])
        self.assertEqual(HybridIndex(data).search({"name": "BasvuruKanal"}, strategy="exact"), [])

    def test_no_forced_candidate_or_embedding_without_scope(self):
        embedding = FakeEmbedding()
        data = snapshot([record("a", "HAV_LM", "Havale son ay"),
                         record("b", "EFT_LM", "EFT son ay", schema="B")])
        index = HybridIndex(data, embedding=embedding)
        self.assertEqual(index.search({"name": "unknown"}, strategy="exact"), [])
        self.assertEqual(index.search({"name": "HAV", "scope": {"schemas": ["C"]}}), [])
        self.assertEqual(embedding.calls, [])
        index.search({"name": "HAV", "scope": {"schemas": ["A"]}}, strategy="embedding")
        self.assertEqual(len(embedding.calls), 2)
        self.assertEqual(len(embedding.calls[0]), 1)
        self.assertIn("HAV_LM", embedding.calls[0][0])

    def test_bm25_and_hybrid_use_related_terms(self):
        data = snapshot([record("a", "FIELD_1", "son hafta farklı kişi sayısı"),
                         record("b", "FIELD_2", "gelen toplam tutar")])
        index = HybridIndex(data)
        self.assertEqual(index.search({"name": "kişi sayısı"}, strategy="bm25")[0]["catalog_id"], "a")
        self.assertIn("bm25", index.search({"name": "kişi sayısı"})[0]["_retrieval"])

    def test_stale_index_detects_deletion_even_if_version_not_bumped(self):
        data = snapshot([record("a", "A", ""), record("b", "B", "")])
        index = HybridIndex(data)
        updated = copy.deepcopy(data)
        updated["records"].pop()
        with self.assertRaises(StaleIndexError):
            index.assert_fresh(updated)
        changed = copy.deepcopy(data)
        changed["records"][0]["column_comment"] = "changed without version bump"
        with self.assertRaises(StaleIndexError):
            index.assert_fresh(changed)

    def test_cache_invalidated_for_deleted_record(self):
        data = snapshot([record("a", "A", ""), record("b", "B", "")])
        embedding = FakeEmbedding()
        with tempfile.TemporaryDirectory() as directory:
            HybridIndex(data, embedding=embedding, cache_dir=directory).search({"name": "A"})
            self.assertEqual(len(list(Path(directory).glob("vectors-*.json"))), 1)
            updated = snapshot([record("a", "A", "")])
            HybridIndex(updated, embedding=embedding, cache_dir=directory).search({"name": "A"})
            self.assertEqual(len(list(Path(directory).glob("vectors-*.json"))), 1)
            self.assertEqual(len(embedding.calls[-2]), 1)

    def test_embedding_requires_adapter(self):
        with self.assertRaises(IntegrationError):
            HybridIndex(snapshot([record("a", "A", "")])).search({"name": "A"}, strategy="embedding")


if __name__ == "__main__":
    unittest.main()
