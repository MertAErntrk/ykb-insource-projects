"""Cross-module checks without bank connections or installed Oracle driver."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riskmatch.adapters.qwen import EmbeddingAdapter, LLMAdapter
from riskmatch.cli import main
from riskmatch.config import load_config
from riskmatch.errors import ConfigError, IntegrationError, CatalogScanError
from riskmatch.retrieval import HybridIndex


BASE = {
    "mode": "real", "allow_real_connections": True,
    "oracle": {"source_database": "risk", "environment": "dev", "container": "PDB1",
               "schemas": ["RISK_DWH"], "user_env": "TEST_ORA_U", "password_env": "TEST_ORA_P",
               "dsn_env": "TEST_ORA_D"},
    "embedding": {"endpoint": "https://embed.internal.example/vectors", "allowed_hosts": ["embed.internal.example"],
                  "model": "explicit", "model_version": "v1", "dimensions": 2,
                  "request_template": {"texts": "{texts}"}, "response_path": "vectors[*].value"},
    "llm": {"endpoint": "https://llm.internal.example/chat", "allowed_hosts": ["llm.internal.example"],
            "model": "explicit", "model_version": "v1", "contract": "template",
            "request_template": {"request": "{context}"}, "response_path": "result"},
}


class Response:
    def __init__(self, value):
        self.body = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _):
        return self.body


class SpyTransport:
    def __init__(self, response):
        self.response = response
        self.sent = []

    def open(self, request, timeout):
        self.sent.append(json.loads(request.data))
        return Response(self.response)


class IntegrationTests(unittest.TestCase):
    def test_check_config_never_calls_database_or_service(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.json"
            path.write_text(json.dumps(BASE), encoding="utf-8")
            with patch("riskmatch.adapters.oracle.OracleAdapter.scan", side_effect=AssertionError("DB called")), \
                 patch("riskmatch.adapters.qwen.post_json", side_effect=AssertionError("HTTP called")):
                self.assertEqual(main(["check-config", str(path)]), 0)
                self.assertEqual(load_config(path, "real")["mode"], "real")

    def test_real_config_requires_explicit_flag_and_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.json"
            invalid = json.loads(json.dumps(BASE))
            invalid["allow_real_connections"] = False
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path, "real")
            invalid = json.loads(json.dumps(BASE))
            invalid["embedding"]["request_template"] = {"fixed": "missing input"}
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path, "real")

    def test_llm_rejects_injected_schema_and_omits_unknown_fields(self):
        secret = "SENTINEL_DO_NOT_SEND"
        bad = {"result": {"assessments": [{"catalog_id": "c1", "evidence": "real comment",
                                           "status": "supported"}]}}
        spy = SpyTransport(bad)
        adapter = LLMAdapter(BASE["llm"], transport=spy)
        request = {"name": "FIELD", "description": "Request", "password": secret}
        candidate = {"catalog_id": "c1", "column": "FIELD", "column_comment": "real comment",
                     "schema": "RISK_DWH", "private_value": secret}
        with self.assertRaises(IntegrationError):
            adapter.evaluate(request, [candidate])
        self.assertNotIn(secret, json.dumps(spy.sent))
        self.assertEqual(spy.sent[0]["request"]["candidates"][0]["catalog_id"], "c1")

    def test_embedding_contract_changes_cache_identity(self):
        first = EmbeddingAdapter(BASE["embedding"])
        changed = json.loads(json.dumps(BASE["embedding"]))
        changed["request_template"] = {"inputs": "{texts}"}
        second = EmbeddingAdapter(changed)
        self.assertNotEqual(first.cache_identity, second.cache_identity)
        # v0.5: HybridIndex has no per-adapter vector file; cache_identity selects the embedding gate and
        # stored vectors are keyed by the canary fingerprint namespace (test_vectorstore/test_resources).

    def test_real_oracle_failure_stops_without_output_or_demo_fallback(self):
        from riskmatch.pipeline import analyze

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.xlsx"
            source.write_bytes(b"synthetic test placeholder")
            config = folder / "config.json"
            config.write_text(json.dumps(BASE), encoding="utf-8")
            requests = [{"row_id": "r1", "name": "FIELD", "description": "A field",
                         "data_type": "Numeric", "category": "Risk", "original": {},
                         "sheet": "Requests", "excel_row": 2}]
            with patch("riskmatch.pipeline.read_requests", return_value=requests), \
                 patch("riskmatch.pipeline.write_results", return_value=str(folder / "out.xlsx")) as write_results, \
                 patch("riskmatch.adapters.oracle.OracleAdapter.scan", side_effect=IntegrationError("scan failed")), \
                 patch("riskmatch.demo.demo_snapshot", side_effect=AssertionError("demo fallback")) as demo:
                with self.assertRaises(CatalogScanError) as caught:
                    analyze(source, folder / "out.xlsx", mode="real", config_path=config,
                            state_dir=folder / "state")
            demo.assert_not_called()
            write_results.assert_not_called()
            self.assertEqual(caught.exception.code, "CATALOG_UNAVAILABLE")
            self.assertFalse(caught.exception.diagnostics["ok"])
            self.assertEqual(caught.exception.diagnostics["record_count"], 0)
            self.assertTrue((folder / "state" / "catalog-diagnostics.json").exists())
            self.assertFalse((folder / "out.xlsx").exists())


if __name__ == "__main__":
    unittest.main()
