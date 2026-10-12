"""Tam indeks kapısı: gerçek modda eşleştirme, bütün belgeler vektör deposuna yazılmadan başlamaz."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from riskmatch import pipeline
from riskmatch.errors import AnalysisCancelled, IntegrationError
from riskmatch.pipeline import _await_full_index, _wait_stage, analyze


class FakeIndex:
    def __init__(self, missing, total=10):
        self.missing = missing
        self.total = total

    def queue_counts(self):
        return self.missing, self.total


class FakeIndexer:
    """Her start() bir koşu açar; status() running iken `ticks` kez döner, sonra `outcome` ile biter."""

    def __init__(self, index, *, state="idle", ticks=0, outcome="done", fill=None):
        self.index = index
        self.state = state
        self.ticks = ticks
        self.outcome = outcome
        self.fill = fill if fill is not None else [index.missing]
        self.starts = 0
        self.remaining = ticks

    def start(self):
        self.starts += 1
        self.state = "running"
        self.remaining = self.ticks
        return self

    def wait(self, timeout=None):
        return True

    def status(self):
        if self.state == "running":
            if self.remaining > 0:
                self.remaining -= 1
                return {"state": "running", "done": 3, "total": 10, "eta_s": 600.0, "message": ""}
            self.state = self.outcome
            self.index.missing = max(0, self.index.missing - self.fill[min(self.starts, len(self.fill)) - 1])
        return {"state": self.state, "done": 0, "total": 0, "eta_s": None,
                "message": "Arka plan indeksleme art arda 5 hata sonrası durdu." if self.state == "failed" else ""}


class GateTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(pipeline, "_sleep", lambda seconds: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.notices = []

    def notify(self, stage, completed=0, phase="analysis"):
        self.notices.append((stage, phase))

    def test_no_missing_documents_continues_without_waiting(self):
        indexer = FakeIndexer(FakeIndex(0))
        _await_full_index(indexer, self.notify, lambda: None)
        self.assertEqual(indexer.starts, 0)
        self.assertEqual(self.notices, [])

    def test_running_indexer_is_awaited_with_index_wait_notices(self):
        index = FakeIndex(7)
        indexer = FakeIndexer(index, state="running", ticks=3)
        indexer.remaining = 3
        indexer.starts = 1
        _await_full_index(indexer, self.notify, lambda: None, poll=0.0)
        self.assertEqual(index.missing, 0)
        self.assertTrue(self.notices)
        self.assertTrue(all(phase == "index_wait" for _stage, phase in self.notices))
        self.assertEqual(self.notices[0][0], "İndeks tamamlanması bekleniyor · %30 · 3/10 · tahmini kalan 10 dk")

    def test_wait_percent_counts_documents_stored_before_restart(self):
        status = {"state": "running", "done": 1, "total": 4}
        self.assertEqual(_wait_stage(status, 10), "İndeks tamamlanması bekleniyor · %70 · 7/10")

    def test_done_with_missing_restarts_until_complete(self):
        index = FakeIndex(10)
        indexer = FakeIndexer(index, state="done", ticks=1, fill=[6, 4])
        _await_full_index(indexer, self.notify, lambda: None)
        self.assertEqual(indexer.starts, 2)
        self.assertEqual(index.missing, 0)

    def test_permanent_missing_raises_after_three_starts(self):
        index = FakeIndex(4, total=10)
        indexer = FakeIndexer(index, state="idle", fill=[0])
        with self.assertRaises(IntegrationError) as caught:
            _await_full_index(indexer, self.notify, lambda: None)
        self.assertEqual(indexer.starts, 3)
        self.assertIn("%60'de kaldı (4 belge eklenemedi)", str(caught.exception))

    def test_second_failure_raises(self):
        indexer = FakeIndexer(FakeIndex(5), state="failed", outcome="failed", fill=[0])
        with self.assertRaises(IntegrationError) as caught:
            _await_full_index(indexer, self.notify, lambda: None)
        self.assertEqual(indexer.starts, 1)
        self.assertIn("arama yapılmadı", str(caught.exception))

    def test_wait_is_cancellable(self):
        indexer = FakeIndexer(FakeIndex(5), state="running", ticks=1000)
        indexer.remaining = 1000
        calls = []

        def cancelled():
            calls.append(1)
            if len(calls) > 3:
                raise AnalysisCancelled("durduruldu")

        with self.assertRaises(AnalysisCancelled):
            _await_full_index(indexer, self.notify, cancelled)


EMBEDDING = {
    "endpoint": "https://embedding.internal.example/v1/embeddings",
    "allowed_hosts": ["embedding.internal.example"],
    "model_selection": "explicit", "model": "embed", "model_version": "v1",
    "dimensions": 8, "request_template": {"model": "{model}", "input": "{texts}"},
    "response_path": "$", "max_retries": 0,
}
SCOPE = {"source_database": "test", "environment": "test", "container": "PDB1", "schemas": ["RISK"]}
CONFIG = {"mode": "real", "allow_real_connections": True, "use_models": True, "llm_review": False,
          "oracle": {**SCOPE, "user_env": "TEST_USER", "password_env": "TEST_PASSWORD", "dsn_env": "TEST_DSN"},
          "embedding": EMBEDDING}
REQUESTS = [{"row_id": "r1", "name": "FIELD", "description": "Field value", "data_type": "Numeric",
             "category": "Risk", "original": {}, "sheet": "Requests", "excel_row": 2}]


def snapshot():
    records = []
    for number, (column, comment) in enumerate([("FIELD", "Field value"), ("LIMIT_AMT", "Credit limit amount"),
                                                ("CUSTOMER_ID", "Customer identifier")]):
        record = {key: SCOPE[key] for key in ("source_database", "environment", "container")}
        record.update({"catalog_id": "c%d" % number, "schema": "RISK", "object": "FEATURES", "column": column,
                       "data_type": "NUMBER", "column_comment": comment, "table_comment": "Risk features",
                       "metadata_version": "1"})
        records.append(record)
    return {"records": records, "scope": copy.deepcopy(SCOPE), "version": "stable", "complete": True,
            "errors": [], "captured_at": "2026-10-11T00:00:00Z"}


class EndToEndGateTests(unittest.TestCase):
    def run_analysis(self, response, rows=None, **options):
        rows = [] if rows is None else rows
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "input.xlsx"
            source.write_bytes(b"fixed source")
            with patch("riskmatch.pipeline.read_requests", side_effect=lambda *_: copy.deepcopy(REQUESTS)), \
                 patch("riskmatch.pipeline.write_results", return_value=str(folder / "output.xlsx")), \
                 patch("riskmatch.adapters.oracle.OracleAdapter.scan", side_effect=lambda *_, **kw: snapshot()), \
                 patch("riskmatch.adapters.qwen.post_json", side_effect=response), \
                 patch.object(pipeline, "_sleep", lambda seconds: None):
                result = analyze(source, folder / "output.xlsx", mode="real", config_data=copy.deepcopy(CONFIG),
                                 state_dir=folder / "runtime", result_callback=rows.append, **options)
        return result, rows

    @staticmethod
    def embed(config, payload, **kwargs):
        return [[1.0] + [float(len(text) % 5)] + [0.0] * 6 for text in payload["input"]]

    def test_full_index_gate_fills_store_before_matching(self):
        stages = []
        result, rows = self.run_analysis(self.embed, require_full_index=True, progress=stages.append)
        self.assertEqual(result["run_info"]["Hatalar"], [])
        self.assertEqual(len(rows), 1)
        self.assertTrue(any(s.get("phase") == "index" for s in stages))
        self.assertEqual(result["run_info"]["Anlamsal kapsam"]["index_coverage"], 1.0)

    def test_unreachable_embedding_stops_only_when_full_index_required(self):
        def broken(config, payload, **kwargs):
            raise IntegrationError("bağlantı yok")

        with self.assertRaises(IntegrationError) as caught:
            self.run_analysis(broken, require_full_index=True)
        self.assertIn("Embedding servisine ulaşılamadı", str(caught.exception))
        result, rows = self.run_analysis(broken)
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("ad ve metin aramasıyla devam edildi" in w for w in result["run_info"]["Uyarılar"]))

    def test_permanent_missing_matches_no_row(self):
        index = FakeIndex(2, total=4)
        fake = FakeIndexer(index, fill=[0])
        rows = []
        with patch("riskmatch.resources.Resources.indexer", return_value=fake), \
             self.assertRaises(IntegrationError) as caught:
            self.run_analysis(self.embed, rows, require_full_index=True)
        self.assertEqual(fake.starts, 3)
        self.assertEqual(rows, [])
        self.assertIn("%50'de kaldı (2 belge eklenemedi)", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
