"""Verified no-model embedding requests must remain usable without invented identity."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from riskmatch.adapters.qwen import EmbeddingAdapter, LLMAdapter
from riskmatch.errors import ConfigError, IntegrationError
from riskmatch.pipeline import analyze
from riskmatch.retrieval import HybridIndex


EMBEDDING = {
    "endpoint": "https://embedding.internal.example/v1/embeddings",
    "allowed_hosts": ["embedding.internal.example"],
    "model_selection": "server_default", "model": "", "model_version": "",
    "dimensions": 4096, "request_template": {"input": "{texts}"},
    "response_path": "$", "max_retries": 0,
}
LLM = {
    "endpoint": "https://llm.internal.example/v1/chat/completions",
    "allowed_hosts": ["llm.internal.example"], "model": "explicit-llm",
    "model_version": "v1", "contract": "openai_chat", "max_retries": 0,
}
SCOPE = {"source_database": "test", "environment": "test", "container": "PDB1",
         "schemas": ["RISK"]}


def snapshot():
    record = {key: SCOPE[key] for key in ("source_database", "environment", "container")}
    record.update({"catalog_id": "field-1", "schema": "RISK", "object": "FEATURES",
                   "column": "FIELD", "data_type": "NUMBER", "column_comment": "Field value",
                   "table_comment": "Metadata for test", "metadata_version": "1"})
    return {"records": [record], "scope": copy.deepcopy(SCOPE), "version": "stable",
            "complete": True, "errors": [], "captured_at": "2026-10-11T00:00:00Z"}


class DefaultEmbeddingRuntimeTests(unittest.TestCase):
    def test_exact_no_model_payload_and_conservative_single_item_batches(self):
        calls = []

        def response(config, payload, **kwargs):
            calls.append(payload)
            return [[1.0] * 4096]

        adapter = EmbeddingAdapter(EMBEDDING)
        with patch("riskmatch.adapters.qwen.post_json", side_effect=response):
            result = adapter.embed(["first", "second"])
        self.assertEqual(calls, [{"input": ["first"]}, {"input": ["second"]}])
        self.assertEqual([len(vector) for vector in result], [4096, 4096])
        self.assertEqual((adapter.model, adapter.model_version), ("", ""))
        self.assertFalse(adapter.persistent_cache_safe)

    def test_verified_openai_response_path_is_supported_without_model_identity(self):
        config = {**EMBEDDING, "response_path": "data[*].embedding"}
        with patch("riskmatch.adapters.qwen.post_json",
                   return_value={"data": [{"embedding": [0.5] * 4096}]}):
            self.assertEqual(EmbeddingAdapter(config).embed(["text"]), [[0.5] * 4096])

    def test_no_model_contract_still_requires_exact_dimensions_and_finite_numbers(self):
        invalid = [[], [[1] * 4095], [[1] * 4097], [[True] * 4096],
                   [["1"] * 4096], [[float("nan")] * 4096], [[float("inf")] * 4096],
                   [[1] * 4096, [1] * 4096]]
        for response in invalid:
            with self.subTest(response_shape=(len(response), len(response[0]) if response else 0)), \
                 patch("riskmatch.adapters.qwen.post_json", return_value=response):
                with self.assertRaises(IntegrationError):
                    EmbeddingAdapter(EMBEDDING).embed(["text"])

    def test_default_contract_requires_explicit_choice_and_rejects_identity_conflicts(self):
        variants = [
            {"model_selection": "explicit"}, {"model_selection": "automatic"},
            {"model": "guessed"}, {"model_version": "deployment:guessed"},
            {"request_template": {"input": "{texts}", "model": "invented"}},
            {"request_template": {"input": "{texts}", "model_version": "v1"}},
            {"request_template": {"input": "{texts}", "metadata": ["{model}"]}},
            {"request_template": {"input": "{texts}", "metadata": {"id": "{model_version}"}}},
        ]
        no_choice = dict(EMBEDDING)
        del no_choice["model_selection"]
        with self.assertRaises(ConfigError):
            EmbeddingAdapter(no_choice)
        for override in variants:
            with self.subTest(override=override), self.assertRaises(ConfigError):
                EmbeddingAdapter({**EMBEDDING, **override})

    def test_explicit_embedding_and_llm_still_require_model_and_version(self):
        for missing in ("model", "model_version"):
            explicit = {**EMBEDDING, "model_selection": "explicit", "model": "known", "model_version": "v1"}
            explicit[missing] = ""
            with self.subTest(adapter="embedding", missing=missing), self.assertRaises(ConfigError):
                EmbeddingAdapter(explicit)
            with self.subTest(adapter="llm", missing=missing), self.assertRaises(ConfigError):
                LLMAdapter({**LLM, missing: "", "model_selection": "server_default"})

    def test_named_deployment_label_is_unverified_and_cannot_enable_persistent_cache(self):
        config = {**EMBEDDING, "model_selection": "explicit", "model": "named-model",
                  "model_version": "deployment:named-model",
                  "request_template": {"model": "{model}", "input": "{texts}"}}
        adapter = EmbeddingAdapter(config)
        self.assertEqual(adapter.identity_status, "unverified")
        self.assertFalse(adapter.persistent_cache_safe)
        versioned = EmbeddingAdapter({**config, "model_version": "approved-v1"})
        self.assertEqual(versioned.identity_status, "configured")
        self.assertTrue(versioned.persistent_cache_safe)

    def test_default_embedding_reuses_vectors_only_under_its_fingerprint(self):
        # v0.5: persisted vectors of an unknown model are reused only when the canary fingerprint matches.
        from riskmatch.resources import Resources
        current = []

        def response(config, payload, **kwargs):
            return copy.deepcopy(current)

        namespaces = []
        with tempfile.TemporaryDirectory() as directory, \
             patch("riskmatch.adapters.qwen.post_json", side_effect=response):
            for value in ([1.0] * 4096, [1.0] * 4096, [0.0, 1.0] + [0.0] * 4094):
                current[:] = [value]
                resources = Resources(Path(directory))
                try:
                    namespaces.append(resources.embedding(EmbeddingAdapter(EMBEDDING)).namespace.id)
                finally:
                    resources.close()
        self.assertEqual(namespaces[0], namespaces[1])
        self.assertNotEqual(namespaces[0], namespaces[2])

    def _assert_repeated_analysis_is_fresh(self, embedding, identity_status):
        config = {"mode": "real", "allow_real_connections": True, "use_models": True,
                  "oracle": {**SCOPE, "user_env": "TEST_USER", "password_env": "TEST_PASSWORD",
                             "dsn_env": "TEST_DSN"},
                  "embedding": copy.deepcopy(embedding), "llm": copy.deepcopy(LLM)}
        requests = [{"row_id": "r1", "name": "FIELD", "description": "Field value",
                     "data_type": "Numeric", "category": "Risk", "original": {},
                     "sheet": "Requests", "excel_row": 2}]
        calls = []

        def response(config, payload, **kwargs):
            calls.append((config["endpoint"], payload))
            if config["endpoint"] == EMBEDDING["endpoint"]:
                return [[1.0] * 4096]
            return {"choices": [{"message": {"content": '{"assessments": []}'}}]}

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "input.xlsx"
            source.write_bytes(b"fixed source hash; Excel parser separately tested")
            with patch("riskmatch.pipeline.read_requests", side_effect=lambda *_: copy.deepcopy(requests)), \
                 patch("riskmatch.pipeline.write_results", return_value=str(folder / "output.xlsx")), \
                 patch("riskmatch.pipeline._source_version", return_value="fixed-test-source"), \
                 patch("riskmatch.adapters.oracle.OracleAdapter.scan", side_effect=lambda *_, **kwargs: snapshot()), \
                 patch("riskmatch.adapters.qwen.post_json", side_effect=response):
                first = analyze(source, folder / "output.xlsx", mode="real", config_data=config,
                                state_dir=folder / "runtime")
                self.assertEqual(first["run_info"]["Tamamlanma"], "Tamamlandı")
                self.assertEqual(len(list((folder / "runtime" / "checkpoints").glob("*.json"))), 1)
                self.assertTrue(calls)
                calls.clear()
                second = analyze(source, folder / "output.xlsx", mode="real", config_data=config,
                                 state_dir=folder / "runtime", resume=True)
            self.assertEqual(second["run_info"]["Devam edilen satır"], 0)
            self.assertEqual(second["run_info"]["Tamamlanma"], "Tamamlandı")
            self.assertTrue(any(endpoint == EMBEDDING["endpoint"] for endpoint, _ in calls))
            self.assertTrue(any(endpoint == LLM["endpoint"] for endpoint, _ in calls))
            self.assertFalse(list((folder / "runtime").glob("index/vectors-*.json")))
            metadata = second["run_info"]["Model doğrulaması"]["embedding"]
            self.assertEqual(metadata["model_selection"], embedding["model_selection"])
            self.assertEqual(metadata["identity_status"], identity_status)
            self.assertEqual((metadata["model"], metadata["model_version"]),
                             (embedding["model"], embedding["model_version"]))
            # v0.5: resume is allowed; the checkpoint key contains the fingerprint namespace.
            self.assertTrue(second["run_info"]["Önbellek ve devam politikası"]["checkpoint_resume"])
            self.assertEqual(second["run_info"]["Hatalar"], [])

    def test_repeated_real_analysis_unknown_model_checkpoint_is_namespace_scoped(self):
        self._assert_repeated_analysis_is_fresh(EMBEDDING, "unknown")

    def test_repeated_real_analysis_named_deployment_checkpoint_is_namespace_scoped(self):
        config = {**EMBEDDING, "model_selection": "explicit", "model": "named-model",
                  "model_version": "deployment:named-model", "max_batch": 1,
                  "request_template": {"model": "{model}", "input": "{texts}"}}
        self._assert_repeated_analysis_is_fresh(config, "unverified")


if __name__ == "__main__":
    unittest.main()
