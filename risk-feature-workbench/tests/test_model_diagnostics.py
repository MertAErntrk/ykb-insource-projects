"""Connection diagnostics with controlled HTTP outcomes, never institution calls."""
import io
import json
import socket
import ssl
import unittest
import urllib.error
from unittest.mock import patch

from riskmatch.model_diagnostics import discover_model


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def open(self, request, timeout):
        self.calls.append({"url": request.full_url, "method": request.get_method(), "timeout": timeout,
                           "headers": dict(request.header_items()),
                           "body": json.loads(request.data) if request.data else None})
        value = self.routes[(request.get_method(), request.full_url)]
        if isinstance(value, BaseException):
            raise value
        return Response(value if isinstance(value, bytes) else json.dumps(value).encode("utf-8"))


def http_error(code):
    return urllib.error.HTTPError("https://model.invalid/v1/models?secret=DO_NOT_LEAK", code,
                                  "RAW_SECRET_DETAIL", {}, io.BytesIO(b"SECRET_RESPONSE"))


class ModelDiagnosticsTests(unittest.TestCase):
    def run_report(self, raw, name, routes):
        opener = Opener(routes)
        with patch("riskmatch.model_diagnostics.urllib.request.build_opener", return_value=opener):
            report = discover_model(raw, name)
        self.assertTrue(all("elapsed_ms" in step and step["status"] in {"passed", "failed", "skipped", "unsupported"} for step in report["checks"]))
        return report, opener.calls

    def test_llm_base_discovers_one_model_and_validates_actual_application_contract(self):
        report, calls = self.run_report({"llm": {"base_url": "https://model.invalid/v1", "api_key": "SECRET_KEY"},
                                         "embedding": "deliberately malformed and irrelevant"}, "llm", {
            ("GET", "https://model.invalid/v1/models"): {"data": [{"id": "qwen-chat"}]},
            ("POST", "https://model.invalid/v1/chat/completions"): {"choices": [{"message": {"content": '{"assessments":[]}'}}]},
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["settings"]["endpoint"], "https://model.invalid/v1/chat/completions")
        self.assertEqual(report["settings"]["contract"], "openai_chat")
        self.assertEqual(report["settings"]["model_version"], "deployment:qwen-chat")
        self.assertIn("sürümü bildirmedi", report["message"])
        self.assertEqual(calls[1]["body"]["response_format"], {"type": "json_object"})
        self.assertTrue(all(call["headers"]["Authorization"] == "Bearer SECRET_KEY" for call in calls))
        self.assertNotIn("SECRET_KEY", json.dumps(report))

    def test_embedding_base_root_probes_openai_and_reads_dimension(self):
        report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid"}, "llm": []}, "embedding", {
            ("GET", "https://embed.invalid/v1/models"): {"data": [{"id": "qwen-embed"}]},
            ("POST", "https://embed.invalid/v1/embeddings"): {"data": [{"embedding": [.1, .2, .3]}]},
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["settings"]["dimensions"], 3)
        self.assertEqual(report["settings"]["response_path"], "data[*].embedding")
        self.assertEqual(calls[1]["body"], {"model": "qwen-embed", "input": ["Connection validation"]})
        self.assertEqual(len(calls), 2)

    def test_embedding_tei_fallback_after_missing_openai_list(self):
        report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid"}}, "embedding", {
            ("GET", "https://embed.invalid/v1/models"): http_error(404),
            ("GET", "https://embed.invalid/info"): {"model_id": "qwen-embed", "model_sha": "abcdef123"},
            ("POST", "https://embed.invalid/embed"): [[.1, .3]],
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["settings"]["model_version"], "abcdef123")
        self.assertEqual(report["settings"]["response_path"], "$")
        self.assertEqual(report["settings"]["dimensions"], 2)
        self.assertNotIn("models_endpoint", report["settings"])
        self.assertEqual(calls[-1]["body"], {"inputs": ["Connection validation"]})
        self.assertEqual(report["checks"][1]["code"], "HTTP_404")

    def test_openai_models_but_no_embeddings_route_can_verify_tei(self):
        report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid/v1"}}, "embedding", {
            ("GET", "https://embed.invalid/v1/models"): {"data": [{"id": "qwen-embed"}]},
            ("POST", "https://embed.invalid/v1/embeddings"): http_error(404),
            ("GET", "https://embed.invalid/info"): {"model_id": "qwen-embed", "model_sha": None},
            ("POST", "https://embed.invalid/embed"): [[.2, .4]],
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["settings"]["endpoint"], "https://embed.invalid/embed")
        self.assertEqual(len(calls), 4)

    def test_multiple_models_require_selection_and_do_not_infer_arbitrarily(self):
        raw = {"llm": {"base_url": "https://model.invalid/v1"}}
        routes = {("GET", "https://model.invalid/v1/models"): {"data": [{"id": "first"}, {"id": "second"}]}}
        report, calls = self.run_report(raw, "llm", routes)
        self.assertFalse(report["ok"])
        self.assertEqual(report["code"], "MODEL_SELECTION_REQUIRED")
        self.assertEqual(report["models"], ["first", "second"])
        self.assertEqual(report["settings"], {})
        self.assertEqual(len(calls), 1)
        raw["llm"]["model"] = "second"
        routes[("POST", "https://model.invalid/v1/chat/completions")] = {"choices": [{"message": {"content": '{"assessments":[]}'}}]}
        report, calls = self.run_report(raw, "llm", routes)
        self.assertTrue(report["ok"], report)
        self.assertEqual(calls[-1]["body"]["model"], "second")

    def test_unknown_selected_model_is_not_silently_substituted(self):
        report, calls = self.run_report({"llm": {"base_url": "https://model.invalid", "model": "absent"}}, "llm", {
            ("GET", "https://model.invalid/v1/models"): {"data": [{"id": "available"}]},
        })
        self.assertEqual(report["code"], "MODEL_NOT_LISTED")
        self.assertEqual(len(calls), 1)

    def test_fundamental_network_auth_errors_stop_without_fallback_or_raw_details(self):
        cases = [(http_error(401), "HTTP_401"), (http_error(403), "HTTP_403"), (http_error(407), "HTTP_407"),
                 (http_error(302), "HTTP_302"),
                 (urllib.error.URLError(socket.gaierror(-2, "RAW_SECRET_DETAIL")), "DNS"),
                 (urllib.error.URLError(ssl.SSLCertVerificationError(1, "RAW_SECRET_DETAIL")), "TLS_CERTIFICATE"),
                 (urllib.error.URLError(ssl.SSLError(1, "WRONG_VERSION_NUMBER RAW_SECRET_DETAIL")), "TLS_HANDSHAKE"),
                 (urllib.error.URLError(socket.timeout("RAW_SECRET_DETAIL")), "TIMEOUT"),
                 (urllib.error.URLError(ConnectionRefusedError("RAW_SECRET_DETAIL")), "CONNECTION_REFUSED")]
        for error, code in cases:
            with self.subTest(code=code):
                report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid"}}, "embedding", {
                    ("GET", "https://embed.invalid/v1/models"): error,
                })
                self.assertFalse(report["ok"])
                self.assertEqual(report["code"], code, report)
                self.assertEqual(len(calls), 1)
                self.assertNotIn("RAW_SECRET", json.dumps(report))
                self.assertNotIn("SECRET_RESPONSE", json.dumps(report))
                self.assertNotIn("DO_NOT_LEAK", json.dumps(report))

    def test_list_success_is_not_inference_success(self):
        report, calls = self.run_report({"llm": {"base_url": "https://model.invalid/v1"}}, "llm", {
            ("GET", "https://model.invalid/v1/models"): {"data": [{"id": "qwen"}]},
            ("POST", "https://model.invalid/v1/chat/completions"): {"choices": [{"message": {"content": "Hello"}}]},
        })
        self.assertFalse(report["ok"])
        self.assertEqual(report["code"], "RESPONSE_CONTRACT")
        self.assertEqual(report["settings"], {})
        self.assertEqual(len(calls), 2)

    def test_invalid_embedding_vector_and_dimension_mismatch_fail(self):
        for vector, declared, code in [([True, .2], 0, "INVALID_VECTOR"), ([float("nan")], 0, "INVALID_VECTOR"),
                                        ([], 0, "INVALID_VECTOR"), ([.1, .2], 3, "DIMENSION_MISMATCH")]:
            with self.subTest(code=code):
                raw = {"embedding": {"endpoint": "https://embed.invalid/embed", "model": "embed", "dimensions": declared}}
                report, _ = self.run_report(raw, "embedding", {("POST", "https://embed.invalid/embed"): [vector]})
                self.assertFalse(report["ok"])
                self.assertEqual(report["code"], code, report)

    def test_explicit_template_can_be_verified_without_discovery(self):
        raw = {"llm": {"endpoint": "https://model.invalid/custom", "model": "known-model", "model_version": "known-v1",
                        "contract": "template", "request_template": {"input": "{context}"}, "response_path": "output"}}
        report, calls = self.run_report(raw, "llm", {
            ("POST", "https://model.invalid/custom"): {"output": {"assessments": []}},
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["settings"]["model_version"], "known-v1")
        self.assertEqual(len(calls), 1)

    def test_invalid_urls_foreign_hosts_and_embedded_secret_templates_do_not_connect(self):
        for value in [{"base_url": "http://model.invalid"},
                      {"base_url": "https://model.invalid?api_key=SECRET"},
                      {"base_url": "https://user:SECRET@model.invalid"},
                      {"base_url": "https://model.invalid", "endpoint": "https://other.invalid/chat"},
                      {"base_url": "https://model.invalid", "request_template": {"nested": {"api_key": "SECRET"}}}]:
            with self.subTest(value=value):
                report, calls = self.run_report({"llm": value}, "llm", {})
                self.assertFalse(report["ok"])
                self.assertEqual(calls, [])
                self.assertNotIn("SECRET", json.dumps(report))

    def test_default_built_in_addresses_are_available_without_endpoint_fields(self):
        with patch("riskmatch.model_diagnostics.LLM_BASE_URL", "https://default.invalid/v1"):
            report, calls = self.run_report({}, "llm", {
                ("GET", "https://default.invalid/v1/models"): {"data": [{"id": "model"}]},
                ("POST", "https://default.invalid/v1/chat/completions"): {"choices": [{"message": {"content": '{"assessments":[]}'}}]},
            })
        self.assertTrue(report["ok"], report)
        self.assertEqual(calls[0]["url"], "https://default.invalid/v1/models")

    def test_non_json_or_huge_metadata_rejected_without_inference(self):
        for response, code in [(b"<html>login form</html>", "INVALID_JSON"), (b"x" * 1_000_001, "RESPONSE_TOO_LARGE")]:
            report, calls = self.run_report({"llm": {"base_url": "https://model.invalid"}}, "llm", {
                ("GET", "https://model.invalid/v1/models"): response,
            })
            self.assertEqual(report["code"], code, report)
            self.assertEqual(len(calls), 1)

    def test_bad_ca_file_is_reported_without_network(self):
        with patch("riskmatch.model_diagnostics.ssl.create_default_context", side_effect=OSError("RAW_SECRET_PATH")):
            report = discover_model({"llm": {"base_url": "https://model.invalid", "ca_file": "missing.pem"}}, "llm")
        self.assertEqual(report["code"], "CA_FILE")
        self.assertNotIn("RAW_SECRET", json.dumps(report))

    def test_explicit_endpoint_404_does_not_trigger_alternative_inference(self):
        report, calls = self.run_report({"embedding": {"endpoint": "https://embed.invalid/v1/embeddings", "model": "known"}}, "embedding", {
            ("POST", "https://embed.invalid/v1/embeddings"): http_error(404),
        })
        self.assertEqual(report["code"], "HTTP_404")
        self.assertEqual(len(calls), 1)

    def test_total_deadline_bounds_later_calls(self):
        from riskmatch.model_diagnostics import _Client, _Failure
        opener = Opener({("GET", "https://model.invalid/v1/models"): {"data": []}})
        with patch("riskmatch.model_diagnostics.urllib.request.build_opener", return_value=opener):
            with patch("riskmatch.model_diagnostics.time.monotonic", return_value=100):
                client = _Client("https://model.invalid", "", "")
            with patch("riskmatch.model_diagnostics.time.monotonic", return_value=150):
                client.get("https://model.invalid/v1/models")
            self.assertEqual(opener.calls[0]["timeout"], 5)
            with patch("riskmatch.model_diagnostics.time.monotonic", return_value=156):
                with self.assertRaises(_Failure) as context:
                    client.get("https://model.invalid/v1/models")
            self.assertEqual(context.exception.code, "TIMEOUT")
            self.assertEqual(len(opener.calls), 1)

    def test_embedding_missing_metadata_still_checks_verified_server_default_route(self):
        for body, path in [([[.001] * 4096], "$"),
                           ({"data": [{"embedding": [.001] * 4096}]}, "data[*].embedding")]:
            with self.subTest(path=path):
                report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid"}}, "embedding", {
                    ("GET", "https://embed.invalid/v1/models"): http_error(404),
                    ("GET", "https://embed.invalid/info"): http_error(404),
                    ("POST", "https://embed.invalid/v1/embeddings"): body,
                })
                self.assertTrue(report["ok"], report)
                self.assertEqual(calls[-1]["body"], {"input": ["Connection validation"]})
                self.assertEqual(len(calls), 3)
                settings = report["settings"]
                self.assertEqual(settings["model_selection"], "server_default")
                self.assertEqual(settings["model"], "")
                self.assertEqual(settings["model_version"], "")
                self.assertEqual(settings["max_batch"], 1)
                self.assertEqual(settings["dimensions"], 4096)
                self.assertEqual(settings["response_path"], path)
                self.assertEqual(settings["request_template"], {"input": "{texts}"})
                self.assertNotIn("models_endpoint", settings)
                self.assertIn("varsayılan embedding", report["message"])
                self.assertNotIn("deployment:", json.dumps(settings))

    def test_server_default_explicit_retest_goes_directly_to_inference(self):
        raw = {"embedding": {"base_url": "https://embed.invalid", "endpoint": "https://embed.invalid/v1/embeddings",
                              "model_selection": "server_default", "model": "", "model_version": "", "max_batch": 1,
                              "request_template": {"input": "{texts}"}, "response_path": "$", "dimensions": 3}}
        report, calls = self.run_report(raw, "embedding", {("POST", "https://embed.invalid/v1/embeddings"): [[.1, .2, .3]]})
        self.assertTrue(report["ok"], report)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["body"], {"input": ["Connection validation"]})
        self.assertEqual(report["settings"]["model_selection"], "server_default")

    def test_explicit_empty_model_endpoint_auto_detects_response_path(self):
        report, calls = self.run_report({"embedding": {"endpoint": "https://embed.invalid/v1/embeddings"}}, "embedding", {
            ("POST", "https://embed.invalid/v1/embeddings"): {"data": [{"embedding": [.4, .5]}]},
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["settings"]["response_path"], "data[*].embedding")
        self.assertEqual(report["settings"]["model_selection"], "server_default")
        self.assertEqual(len(calls), 1)

    def test_server_default_custom_template_and_response_path_are_preserved(self):
        raw = {"embedding": {"endpoint": "https://embed.invalid/custom", "model_selection": "server_default",
                              "request_template": {"texts": "{texts}", "normalize": True}, "response_path": "result.vectors"}}
        report, calls = self.run_report(raw, "embedding", {
            ("POST", "https://embed.invalid/custom"): {"result": {"vectors": [[.6, .7]]}},
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(calls[0]["body"], {"texts": ["Connection validation"], "normalize": True})
        self.assertEqual(report["settings"]["response_path"], "result.vectors")
        self.assertEqual(report["settings"]["request_template"], raw["embedding"]["request_template"])

    def test_wrong_explicit_response_path_is_not_silently_overridden(self):
        raw = {"embedding": {"endpoint": "https://embed.invalid/v1/embeddings", "response_path": "data[*].embedding"}}
        report, calls = self.run_report(raw, "embedding", {("POST", "https://embed.invalid/v1/embeddings"): [[.1, .2]]})
        self.assertFalse(report["ok"])
        self.assertEqual(report["code"], "RESPONSE_CONTRACT")
        self.assertEqual(report["settings"], {})
        self.assertEqual(len(calls), 1)

    def test_server_default_cannot_sneak_model_identity_or_model_parameter(self):
        cases = [{"model_selection": "server_default", "model": "invented"},
                 {"model_selection": "server_default", "model_version": "invented"},
                 {"request_template": {"input": "{texts}", "model": ""}},
                 {"request_template": {"input": "{texts}", "model_version": ""}},
                 {"request_template": {"input": "{texts}", "label": "{model}"}},
                 {"request_template": {"input": "{texts}", "label": "{model_version}"}}]
        for extra in cases:
            with self.subTest(extra=extra):
                raw = {"embedding": {"endpoint": "https://embed.invalid/v1/embeddings", **extra}}
                report, calls = self.run_report(raw, "embedding", {})
                self.assertFalse(report["ok"], report)
                self.assertEqual(calls, [])
                self.assertIn(report["code"], {"INVALID_SETTINGS", "INVALID_CONTRACT"})

    def test_model_free_request_400_or_422_requires_contract_confirmation(self):
        for status in (400, 422):
            with self.subTest(status=status):
                report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid"}}, "embedding", {
                    ("GET", "https://embed.invalid/v1/models"): http_error(404),
                    ("GET", "https://embed.invalid/info"): http_error(405),
                    ("POST", "https://embed.invalid/v1/embeddings"): http_error(status),
                })
                self.assertFalse(report["ok"])
                self.assertEqual(report["code"], "HTTP_" + str(status))
                self.assertIn("model kimliği veya özel istek sözleşmesi", report["error"])
                self.assertEqual(report["settings"], {})
                self.assertEqual(len(calls), 3)

    def test_metadata_auth_failure_prevents_model_free_fallback(self):
        for status in (401, 403, 407):
            with self.subTest(status=status):
                report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid"}}, "embedding", {
                    ("GET", "https://embed.invalid/v1/models"): http_error(404),
                    ("GET", "https://embed.invalid/info"): http_error(status),
                })
                self.assertFalse(report["ok"])
                self.assertEqual(report["code"], "HTTP_" + str(status))
                self.assertEqual(len(calls), 2)

    def test_model_free_404_or_bad_vector_does_not_claim_success_or_repeat_metadata(self):
        for body, code in [(http_error(404), "HTTP_404"), ([[True]], "INVALID_VECTOR"),
                           ([[float("nan")]], "INVALID_VECTOR"), ([], "INVALID_VECTOR"),
                           ({"error": "not-a-vector"}, "INVALID_VECTOR")]:
            with self.subTest(code=code):
                report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid"}}, "embedding", {
                    ("GET", "https://embed.invalid/v1/models"): http_error(404),
                    ("GET", "https://embed.invalid/info"): http_error(404),
                    ("POST", "https://embed.invalid/v1/embeddings"): body,
                })
                self.assertFalse(report["ok"])
                self.assertEqual(report["code"], code)
                self.assertEqual(report["settings"], {})
                self.assertEqual(len(calls), 3)

    def test_named_model_stays_explicit_when_metadata_is_unavailable(self):
        report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid", "model": "selected"}}, "embedding", {
            ("GET", "https://embed.invalid/v1/models"): http_error(404),
            ("GET", "https://embed.invalid/info"): http_error(404),
            ("POST", "https://embed.invalid/v1/embeddings"): {"data": [{"embedding": [.2, .4]}]},
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(calls[-1]["body"]["model"], "selected")
        self.assertNotEqual(report["settings"].get("model_selection"), "server_default")

    def test_explicit_server_default_base_only_does_not_need_metadata(self):
        report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid/v1", "model_selection": "server_default"}}, "embedding", {
            ("POST", "https://embed.invalid/v1/embeddings"): [[.3, .6]],
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(len(calls), 1)

    def test_tei_route_fallback_does_not_replace_explicit_custom_contract(self):
        raw = {"embedding": {"base_url": "https://embed.invalid", "request_template": {"sentences": "{texts}"},
                              "response_path": "payload", "dimensions": 2}}
        report, calls = self.run_report(raw, "embedding", {
            ("GET", "https://embed.invalid/v1/models"): {"data": [{"id": "known"}]},
            ("POST", "https://embed.invalid/v1/embeddings"): http_error(404),
            ("GET", "https://embed.invalid/info"): {"model_id": "known"},
            ("POST", "https://embed.invalid/embed"): {"payload": [[.2, .4]]},
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["settings"]["response_path"], "payload")
        self.assertEqual(report["settings"]["request_template"], {"sentences": "{texts}"})
        self.assertEqual(calls[-1]["body"], {"sentences": ["Connection validation"]})

    def test_entering_model_after_server_default_cannot_fake_verified_identity(self):
        raw = {"embedding": {"endpoint": "https://embed.invalid/v1/embeddings", "model_selection": "explicit",
                              "model": "invented", "request_template": {"input": "{texts}"}, "response_path": "$"}}
        report, calls = self.run_report(raw, "embedding", {})
        self.assertFalse(report["ok"])
        self.assertEqual(report["code"], "MODEL_IDENTITY_UNVERIFIED")
        self.assertEqual(report["settings"], {})
        self.assertEqual(calls, [])

    def test_metadata_verified_named_model_can_keep_input_only_contract(self):
        raw = {"embedding": {"base_url": "https://embed.invalid", "model": "known",
                              "request_template": {"input": "{texts}"}, "response_path": "$"}}
        report, calls = self.run_report(raw, "embedding", {
            ("GET", "https://embed.invalid/v1/models"): {"data": [{"id": "known"}]},
            ("POST", "https://embed.invalid/v1/embeddings"): [[.2, .4]],
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["settings"]["model"], "known")
        self.assertEqual(calls[-1]["body"], {"input": ["Connection validation"]})
        # Settings round-trip keeps the named identity verified on a later test.
        second, calls = self.run_report({"embedding": report["settings"]}, "embedding", {
            ("GET", "https://embed.invalid/v1/models"): {"data": [{"id": "known"}]},
            ("POST", "https://embed.invalid/v1/embeddings"): [[.2, .4]],
        })
        self.assertTrue(second["ok"], second)
        self.assertEqual(len(calls), 2)

    def test_explicit_named_model_placeholder_contract_remains_supported(self):
        raw = {"embedding": {"endpoint": "https://embed.invalid/v1/embeddings", "model": "known",
                              "request_template": {"model": "{model}", "input": "{texts}"}, "response_path": "$"}}
        report, calls = self.run_report(raw, "embedding", {
            ("POST", "https://embed.invalid/v1/embeddings"): [[.3, .5]],
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(calls[0]["body"]["model"], "known")

    def test_explicit_named_custom_and_tei_templates_remain_supported(self):
        for path, template in [("/custom", {"sentences": "{texts}"}), ("/embed", {"inputs": "{texts}"})]:
            with self.subTest(path=path):
                raw = {"embedding": {"endpoint": "https://embed.invalid" + path, "model": "known",
                                      "request_template": template, "response_path": "$"}}
                report, calls = self.run_report(raw, "embedding", {("POST", "https://embed.invalid" + path): [[.1, .4]]})
                self.assertTrue(report["ok"], report)
                self.assertEqual(report["settings"]["request_template"], template)
                self.assertEqual(len(calls), 1)

    def test_successful_inference_marks_optional_metadata_404_405_as_unsupported(self):
        report, calls = self.run_report({"embedding": {"base_url": "https://embed.invalid"}}, "embedding", {
            ("GET", "https://embed.invalid/v1/models"): http_error(404),
            ("GET", "https://embed.invalid/info"): http_error(405),
            ("POST", "https://embed.invalid/v1/embeddings"): [[.1] * 4096],
        })
        self.assertTrue(report["ok"], report)
        discovery = [step for step in report["checks"] if step["name"].startswith("GET ")]
        self.assertEqual([step["status"] for step in discovery], ["unsupported", "unsupported"])
        self.assertEqual([step["code"] for step in discovery], ["HTTP_404", "HTTP_405"])
        self.assertTrue(all("isteğe bağlı" in step["message"] and step["elapsed_ms"] >= 0 for step in discovery))
        self.assertEqual(report["checks"][-1]["status"], "passed")
        self.assertEqual(report["settings"]["dimensions"], 4096)
        self.assertEqual(len(calls), 3)

    def test_failed_inference_does_not_soften_discovery_or_final_post_errors(self):
        for result, expected in [(http_error(404), "HTTP_404"), (http_error(401), "HTTP_401"),
                                 (urllib.error.URLError(ssl.SSLCertVerificationError(1, "PRIVATE")), "TLS_CERTIFICATE"),
                                 ([[True]], "INVALID_VECTOR")]:
            with self.subTest(expected=expected):
                report, _ = self.run_report({"embedding": {"base_url": "https://embed.invalid"}}, "embedding", {
                    ("GET", "https://embed.invalid/v1/models"): http_error(404),
                    ("GET", "https://embed.invalid/info"): http_error(405),
                    ("POST", "https://embed.invalid/v1/embeddings"): result,
                })
                self.assertFalse(report["ok"])
                self.assertEqual(report["checks"][-1]["status"], "failed")
                self.assertEqual(report["code"], expected)
                self.assertFalse(any(step["status"] == "unsupported" for step in report["checks"]))
                self.assertTrue(all(step["status"] == "failed" for step in report["checks"] if step["name"].startswith("GET ")))

    def test_llm_success_also_labels_missing_optional_model_list_neutrally(self):
        report, _ = self.run_report({"llm": {"base_url": "https://model.invalid/v1", "model": "known"}}, "llm", {
            ("GET", "https://model.invalid/v1/models"): http_error(404),
            ("POST", "https://model.invalid/v1/chat/completions"): {"choices": [{"message": {"content": '{"assessments":[]}'}}]},
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checks"][1]["status"], "unsupported")
        self.assertEqual(report["checks"][1]["code"], "HTTP_404")

    def test_alternative_inference_success_does_not_hide_failed_post(self):
        report, _ = self.run_report({"embedding": {"base_url": "https://embed.invalid"}}, "embedding", {
            ("GET", "https://embed.invalid/v1/models"): {"data": [{"id": "known"}]},
            ("POST", "https://embed.invalid/v1/embeddings"): http_error(404),
            ("GET", "https://embed.invalid/info"): {"model_id": "known"},
            ("POST", "https://embed.invalid/embed"): [[.3, .6]],
        })
        self.assertTrue(report["ok"], report)
        missing_post = next(step for step in report["checks"] if step.get("code") == "HTTP_404")
        self.assertTrue(missing_post["name"].startswith("POST "))
        self.assertEqual(missing_post["status"], "failed")

    def test_malformed_metadata_remains_failed_even_if_tei_inference_succeeds(self):
        report, _ = self.run_report({"embedding": {"base_url": "https://embed.invalid"}}, "embedding", {
            ("GET", "https://embed.invalid/v1/models"): {"unexpected": "shape"},
            ("GET", "https://embed.invalid/info"): {"model_id": "known"},
            ("POST", "https://embed.invalid/embed"): [[.3, .6]],
        })
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checks"][1]["code"], "MODEL_LIST_FORMAT")
        self.assertEqual(report["checks"][1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
