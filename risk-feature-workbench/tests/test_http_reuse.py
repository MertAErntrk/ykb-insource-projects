"""Repeated inference reuses verified TLS setup without sharing credentials."""

import json
import ssl
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from riskmatch.adapters._http import _NoRedirect, post_json
from riskmatch.adapters.qwen import EmbeddingAdapter, LLMAdapter
from riskmatch.errors import ConfigError, IntegrationError


EMBED = {
    "endpoint": "https://embedding.internal.example/v1/embeddings",
    "allowed_hosts": ["embedding.internal.example"],
    "model_selection": "server_default", "dimensions": 2,
    "request_template": {"input": "{texts}"},
    "response_path": "data[*].embedding", "max_retries": 0,
}
LLM = {
    "endpoint": "https://llm.internal.example/v1/chat/completions",
    "allowed_hosts": ["llm.internal.example"], "contract": "openai_chat",
    "model": "test-model", "model_version": "v1", "max_retries": 0,
}


class Response:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return self.body[:limit]


class Service:
    def __init__(self):
        self.requests = []
        self.failures = []
        self.response = {"data": [{"embedding": [1, 0]}]}

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.failures:
            raise self.failures.pop(0)
        return Response(self.response)


class ReusableHTTPTests(unittest.TestCase):
    def test_500_single_text_requests_build_one_verified_opener_lazily(self):
        service = Service()
        # Real local SSL context; every network operation remains a test double.
        context = ssl.create_default_context()
        with patch("riskmatch.adapters._http.ssl.create_default_context", return_value=context) as create, \
                patch("urllib.request.build_opener", return_value=service) as build:
            adapter = EmbeddingAdapter(EMBED)
            create.assert_not_called()
            build.assert_not_called()
            self.assertEqual(adapter.embed([]), [])
            self.assertEqual(adapter.embed([str(i) for i in range(500)]), [[1.0, 0.0]] * 500)
            create.assert_called_once_with(cafile=None)
            build.assert_called_once()
        self.assertEqual(len(service.requests), 500)
        self.assertTrue(all(request.get_method() == "POST" for request, _ in service.requests))
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        handlers = build.call_args.args
        self.assertIsInstance(handlers[0], _NoRedirect)
        self.assertIsNone(handlers[0].redirect_request(None, None, 302, None, None, "https://other.example"))
        self.assertIsInstance(handlers[1], urllib.request.HTTPSHandler)
        self.assertIs(handlers[1]._context, context)

    def test_llm_reuses_setup_and_empty_candidates_do_not_create_it(self):
        service = Service()
        service.response = {"choices": [{"message": {"content": '{"assessments":[]}'}}]}
        with patch("riskmatch.adapters._http.ssl.create_default_context") as create, \
                patch("urllib.request.build_opener", return_value=service) as build:
            adapter = LLMAdapter(LLM)
            adapter.evaluate({"name": "X"}, [])
            create.assert_not_called()
            candidates = [{"catalog_id": "1", "column": "X"}]
            for _ in range(3):
                self.assertEqual(adapter.evaluate({"name": "X"}, candidates)["assessments"], [])
            self.assertEqual(create.call_count, 1)
            self.assertEqual(build.call_count, 1)
        self.assertEqual(len(service.requests), 3)

    def test_separate_adapters_never_share_opener_or_credentials(self):
        first, second = Service(), Service()
        with patch("urllib.request.build_opener", side_effect=[first, second]) as build:
            a = EmbeddingAdapter(EMBED, api_key="first-key")
            b = EmbeddingAdapter(EMBED, api_key="second-key")
            a.embed(["a"])
            b.embed(["b"])
            a.api_key = ""
            a.embed(["c"])
        self.assertEqual(build.call_count, 2)
        self.assertEqual(first.requests[0][0].get_header("Authorization"), "Bearer first-key")
        self.assertIsNone(first.requests[1][0].get_header("Authorization"))
        self.assertEqual(second.requests[0][0].get_header("Authorization"), "Bearer second-key")

    def test_changed_ca_path_rebuilds_and_bad_ca_cannot_fall_back(self):
        service = Service()
        with patch("riskmatch.adapters._http.ssl.create_default_context") as create, \
                patch("urllib.request.build_opener", return_value=service) as build:
            adapter = EmbeddingAdapter({**EMBED, "ca_file": "first.pem"})
            adapter.embed(["a"])
            adapter.config["ca_file"] = "second.pem"
            adapter.embed(["b"])
            self.assertEqual([call.kwargs for call in create.call_args_list],
                             [{"cafile": "first.pem"}, {"cafile": "second.pem"}])
            adapter.config["ca_file"] = "bad.pem"
            create.side_effect = OSError("private path detail")
            with self.assertRaisesRegex(ConfigError, "Kurum CA dosyası") as error:
                adapter.embed(["c"])
            self.assertNotIn("private path detail", str(error.exception))
            self.assertEqual(len(service.requests), 2)
            self.assertEqual(build.call_count, 2)

    def test_endpoint_allowlist_and_request_limits_remain_checked_after_reuse(self):
        service = Service()
        adapter = EmbeddingAdapter(EMBED)
        with patch("urllib.request.build_opener", return_value=service):
            adapter.embed(["a"])
            for endpoint in ("http://embedding.internal.example", "https://other.example/v1/embeddings",
                             "https://username:password@embedding.internal.example/v1/embeddings"):
                adapter.config["endpoint"] = endpoint
                with self.assertRaises(ConfigError):
                    adapter.embed(["b"])
            adapter.config["endpoint"] = EMBED["endpoint"]
            with self.assertRaisesRegex(ConfigError, "boyut sınırını"):
                post_json(adapter.config, {"value": "x" * 512_001}, transport=adapter.transport)
            with self.assertRaises(ConfigError):
                post_json(adapter.config, {"value": float("nan")}, transport=adapter.transport)
        self.assertEqual(len(service.requests), 1)

    def test_retry_and_response_limits_are_preserved(self):
        service = Service()
        service.failures.append(urllib.error.HTTPError(EMBED["endpoint"], 503, "unavailable", {}, None))
        adapter = EmbeddingAdapter({**EMBED, "max_retries": 1})
        with patch("urllib.request.build_opener", return_value=service) as build, \
                patch("riskmatch.adapters._http.time.sleep") as sleep:
            self.assertEqual(adapter.embed(["a"]), [[1.0, 0.0]])
            self.assertEqual(len(service.requests), 2)
            sleep.assert_called_once_with(0.2)
            service.response = b"x" * 1_000_001
            with self.assertRaisesRegex(IntegrationError, "yanıtı boyut sınırını"):
                adapter.embed(["b"])
            build.assert_called_once()

    def test_injected_transport_keeps_explicit_ca_validation(self):
        service = Service()
        adapter = EmbeddingAdapter({**EMBED, "ca_file": "bank.pem"}, transport=service)
        with patch("riskmatch.adapters._http.ssl.create_default_context") as create:
            adapter.embed(["a", "b"])
            self.assertEqual(create.call_count, 2)
            create.assert_called_with(cafile="bank.pem")
            create.side_effect = OSError("unreadable CA")
            with self.assertRaises(ConfigError):
                adapter.embed(["c"])
        self.assertEqual(len(service.requests), 2)


if __name__ == "__main__":
    unittest.main()
