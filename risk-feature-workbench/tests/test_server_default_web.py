"""Probe-to-analysis regressions for embedding services without model metadata.

Only Oracle and HTTP I/O are substituted. The web API, model discovery,
configuration, adapters, retrieval and two-row analysis all run normally.
"""
from copy import deepcopy
import io
import json
import unittest
import urllib.error
from urllib.parse import urlsplit
from unittest.mock import patch

import test_web
from test_adapters import FakeConnection
from riskmatch.adapters.oracle import OracleAdapter


class ModelService:
    """The observed API shape: metadata is absent, inference is available."""

    def __init__(self, response_shape):
        self.response_shape = response_shape
        self.phase = "probe"
        self.calls = []

    def open(self, request, timeout):
        address = urlsplit(request.full_url)
        method = request.get_method()
        body = json.loads(request.data) if request.data else None
        self.calls.append({"host": address.hostname, "path": address.path,
                           "method": method, "body": body, "phase": self.phase,
                           "authorization": request.get_header("Authorization")})
        if address.hostname == "embedding.invalid":
            if method == "GET" and address.path == "/openapi.json":
                response = {"openapi": "3.0.0", "paths": {
                    "/health": {"get": {}}, "/v1/embeddings": {"post": {}}}}
            elif method == "GET" or (method == "POST" and address.path == "/embed"):
                raise urllib.error.HTTPError(request.full_url, 404,
                                             "PRIVATE_UPSTREAM_ERROR", {}, None)
            elif method == "POST" and address.path == "/v1/embeddings":
                # An invented model or expanded batch would reproduce the bug.
                if set(body) != {"input"} or not isinstance(body["input"], list) or len(body["input"]) != 1:
                    raise AssertionError("Only the verified single-input, model-free contract is supported")
                vector = [1.0] + [0.0] * 4095
                response = ([vector] if self.response_shape == "$" else
                            {"data": [{"embedding": vector, "index": 0}]})
            else:
                raise AssertionError("Unexpected embedding route: " + request.full_url)
        elif address.hostname == "llm.invalid":
            if method == "GET" and address.path == "/v1/models":
                response = {"data": [{"id": "qwen38-27b"}]}
            elif method == "POST" and address.path == "/v1/chat/completions":
                if body.get("model") != "qwen38-27b":
                    raise AssertionError("The named LLM contract must be preserved")
                context = json.loads(body["messages"][-1]["content"])
                assessments = [{"catalog_id": item["catalog_id"],
                                "evidence": item["column"],
                                "note": "Checked source evidence"}
                               for item in context["candidates"]]
                response = {"choices": [{"message": {
                    "content": json.dumps({"assessments": assessments})}}]}
            else:
                raise AssertionError("Unexpected LLM route: " + request.full_url)
        else:
            raise AssertionError("Unexpected service host")
        return io.BytesIO(json.dumps(response).encode("utf-8"))


class ServerDefaultWebTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown
    connect = test_web.WebTests.connect
    finish = test_web.WebTests.finish

    def test_verified_model_free_probe_settings_run_two_rows_with_named_llm(self):
        for response_shape in ("data[*].embedding", "$"):
            with self.subTest(response_shape=response_shape):
                service = ModelService(response_shape)
                raw = {"use_models": True, "llm_review": True, "search_mode": "full",
                       "embedding": {"base_url": "https://embedding.invalid",
                                     "api_key": "EMBED_SECRET_SENTINEL"},
                       "llm": {"base_url": "https://llm.invalid/v1",
                               "model": "qwen38-27b", "api_key": "LLM_SECRET_SENTINEL"}}
                with patch("urllib.request.build_opener", return_value=service), \
                        patch.object(OracleAdapter, "_connect", return_value=FakeConnection()):
                    for name in ("embedding", "llm"):
                        probe = self.client.post("/api/probe/" + name, json=raw, headers=self.headers)
                        self.assertEqual(probe.status_code, 200, probe.json)
                        self.assertTrue(probe.json["ok"], probe.json)
                        self.assertNotIn("SECRET_SENTINEL", probe.text)
                        self.assertNotIn("PRIVATE_UPSTREAM_ERROR", probe.text)
                        raw[name].update(probe.json["settings"])

                    embedding = raw["embedding"]
                    self.assertEqual(embedding["model_selection"], "server_default")
                    self.assertEqual(embedding.get("model", ""), "")
                    self.assertEqual(embedding.get("model_version", ""), "")
                    self.assertEqual(embedding["dimensions"], 4096)
                    self.assertEqual(embedding["max_batch"], 1)
                    self.assertEqual(embedding["response_path"], response_shape)
                    self.assertEqual(embedding["request_template"], {"input": "{texts}"})

                    # Settings exported by the UI must preserve the verified contract.
                    profile = self.client.post("/api/profile", json=raw, headers=self.headers)
                    self.assertEqual(profile.status_code, 200, profile.json)
                    self.assertEqual(profile.json["embedding"]["model_selection"], "server_default")
                    self.assertEqual(profile.json["embedding"]["max_batch"], 1)
                    self.assertNotIn("SECRET_SENTINEL", profile.text)

                    service.phase = "analysis"
                    response = self.client.post("/api/analyze", json={
                        "schemas": ["RISK_DWH"], "input_type": "manual",
                        "variables": [
                            {"name": "HAV_LM", "description": "Son ay havale kisi sayisi"},
                            {"name": "HAV_AY", "description": "Aylik havale kisi sayisi"}],
                        "settings": deepcopy(raw)}, headers=self.headers)
                    self.assertEqual(response.status_code, 202, response.json)
                    result = self.finish(response.json["id"])

                self.assertEqual(result["status"], "complete", result)
                self.assertEqual(len(result["results"]), 2)
                self.assertTrue(result["run_info"]["Tarama tamamlandı"])
                self.assertEqual(result["run_info"]["Arama yaklaşımı"], "hybrid")
                identity = result["run_info"]["Model doğrulaması"]["embedding"]
                self.assertEqual(identity["model_selection"], "server_default")
                self.assertEqual(identity["dimensions"], 4096)
                self.assertEqual(identity["model"], "")
                self.assertEqual(identity["model_version"], "")
                # v0.5: vector reuse is gated by the canary fingerprint, not by a persistent_cache_safe flag.
                self.assertIsNotNone(identity["fingerprint"])
                self.assertEqual(list(self.root.rglob("vectors-*.json")), [])
                # v0.5: the LLM is consulted only for rows whose shown candidates are not strong; the exact
                # HAV_LM match needs no review and weak candidates are no longer shown.
                self.assertTrue(result["results"][0]["candidates"])
                for row in result["results"]:
                    self.assertEqual(row["errors"], [])
                    if row.get("llm_requested"):
                        self.assertEqual(row["model_review"]["model"], "qwen38-27b")
                        self.assertTrue(row["model_review"]["assessments"])
                    else:
                        self.assertNotIn("model_review", row)

                analysis_calls = [call for call in service.calls if call["phase"] == "analysis"]
                embeddings = [call for call in analysis_calls if call["host"] == "embedding.invalid"]
                reviews = [call for call in analysis_calls if call["host"] == "llm.invalid"]
                self.assertGreaterEqual(len(embeddings), 3)  # Catalog plus both variable queries.
                self.assertEqual(len(reviews), sum(1 for row in result["results"] if row.get("llm_requested")))
                for call in embeddings:
                    self.assertEqual(call["path"], "/v1/embeddings")
                    self.assertEqual(set(call["body"]), {"input"})
                    self.assertEqual(len(call["body"]["input"]), 1)
                    self.assertEqual(call["authorization"], "Bearer EMBED_SECRET_SENTINEL")
                for call in reviews:
                    self.assertEqual(call["authorization"], "Bearer LLM_SECRET_SENTINEL")
                self.assertNotIn("SECRET_SENTINEL", json.dumps(result))
                for path in self.root.rglob("*.json"):
                    self.assertNotIn("SECRET_SENTINEL", path.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
