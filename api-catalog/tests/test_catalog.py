import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_app.importers import parse_document
from catalog_app.privacy import masked_example, safe_schema, schema_of
from catalog_app.qwen import Settings, chat_url, complete, describe
from catalog_app.server import App, make_server

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    return json.loads((ROOT / "examples" / "demo-capture.json").read_text(encoding="utf-8"))


class ImportTests(unittest.TestCase):
    def test_import_filters_assets_and_preserves_page_mapping(self):
        items, skipped = parse_document(fixture(), "Portal")
        self.assertEqual(len(items), 3)
        self.assertEqual(skipped, 1)
        self.assertEqual(items[0]["contexts"][0]["page"], "Örnek: yeni gelen talepler")
        self.assertEqual(items[2]["method"], "POST")
        self.assertEqual(items[2]["path"], "/api/requests/{id}/assign")

    def test_credentials_and_business_values_are_not_kept(self):
        document = fixture()
        entry = document["log"]["entries"][2]
        entry["request"]["url"] += "?access_token=captured-private-token&person=private-name"
        entry["request"]["headers"] = [{"name": "Authorization", "value": "Bearer captured-private-token"}, {"name": "Cookie", "value": "session=captured-private-cookie"}, {"name": "X-CSRF-Token", "value": "captured-private-csrf"}]
        entry["request"]["postData"]["text"] = json.dumps({"assignee_id": 985674, "token": "captured-private-token", "description": "private-name"})
        entry["response"]["content"]["text"] = json.dumps({"name": "private-name", "email": "private@example.com"})
        items, _ = parse_document(document, "Portal", "Detay", "Üzerime al")
        stored = json.dumps(items)
        for secret in ("captured-private-token", "captured-private-cookie", "captured-private-csrf", "private-name", "private@example.com", "985674"):
            self.assertNotIn(secret, stored)
        self.assertIn("Bearer", items[2]["auth"])
        self.assertIn("Cookie/oturum", items[2]["auth"])
        self.assertTrue(next(p for p in items[2]["parameters"] if p["name"] == "access_token")["sensitive"])

    def test_requiredness_is_unknown_for_observed_query(self):
        items, _ = parse_document(fixture(), "Portal")
        self.assertIsNone(items[0]["parameters"][0]["required"])
        self.assertNotIn("required", items[0]["response_schemas"]["200"])

    def test_graphql_operations_are_distinct(self):
        doc = fixture()
        entries = []
        for kind, name in (("query", "Requests"), ("mutation", "AssignRequest")):
            entry = copy.deepcopy(doc["log"]["entries"][2])
            entry["request"]["url"] = "https://portal.example.com/graphql"
            entry["request"]["postData"]["text"] = json.dumps({"operationName": name, "query": kind + " " + name + " { requests { id } }", "variables": {"token": "private-value"}})
            entries.append(entry)
        doc["log"]["entries"] = entries
        items, _ = parse_document(doc, "Portal")
        self.assertNotEqual(items[0]["id"], items[1]["id"])
        self.assertEqual(items[1]["operation_kind"], "mutation")
        self.assertNotIn("private-value", json.dumps(items))

    def test_openapi_local_reference_and_requiredness(self):
        doc = {"openapi": "3.0.3", "servers": [{"url": "https://portal.example.com/service"}], "components": {"schemas": {"Request": {"type": "object", "required": ["subject"], "properties": {"subject": {"type": "string", "example": "PRIVATE EXAMPLE"}}}}}, "paths": {"/requests": {"post": {"summary": "Yeni talep oluşturur", "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Request"}}}}, "responses": {"201": {"description": "created"}}}}}}
        items, _ = parse_document(doc, "Portal")
        self.assertEqual(items[0]["path"], "/service/requests")
        self.assertEqual(items[0]["request_schema"]["required"], ["subject"])
        self.assertNotIn("PRIVATE EXAMPLE", json.dumps(items))
        self.assertEqual(items[0]["observations"], 0)

    def test_remote_schema_reference_never_fetches(self):
        self.assertIn("description", safe_schema({"$ref": "https://invalid.example/schema.json"}, {}))

    def test_boolean_schema_and_masked_values(self):
        self.assertEqual(schema_of(True), {"type": "boolean"})
        self.assertEqual(masked_example({"enabled": True, "token": "secret"}), {"enabled": False, "token": "[gizli]"})

    def test_invalid_import(self):
        with self.assertRaises(ValueError):
            parse_document({"hello": "world"}, "Portal")
        with self.assertRaises(ValueError):
            parse_document(fixture(), "")


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = App(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_import_is_idempotent_and_searches_actions(self):
        data = {"document": fixture(), "application": "Portal", "page": "Talep detayı", "action": "Üzerime al"}
        first = self.app.import_capture(data)
        second = self.app.import_capture(data)
        self.assertEqual(first["added"], 3)
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.app.catalog.list(query="Üzerime al")), 3)
        self.assertEqual(self.app.catalog.get(self.app.catalog.list()[0]["id"])["observations"], 1)

    def test_different_record_ids_merge_but_keep_contexts(self):
        doc1, doc2 = fixture(), fixture()
        doc2["log"]["entries"][1]["request"]["url"] = "https://portal.example.com/api/requests/202"
        self.app.import_capture({"document": doc1, "application": "Portal", "page": "Liste"})
        self.app.import_capture({"document": doc2, "application": "Portal", "page": "Detay"})
        items = self.app.catalog.list()
        self.assertEqual(len(items), 3)
        self.assertEqual(len(items[0]["contexts"]), 2)
        self.assertEqual(items[0]["observations"], 2)

    def test_domain_filter(self):
        result = self.app.import_capture({"document": fixture(), "application": "Portal", "allowed_host": "other.example.com"})
        self.assertEqual(result["accepted"], 0)

    def test_openapi_path_parameter_name_merges_with_capture(self):
        self.app.import_capture({"document": fixture(), "application": "Portal"})
        doc = {"openapi": "3.0.3", "servers": [{"url": "https://portal.example.com"}], "paths": {"/api/requests/{request_id}": {"get": {"summary": "Talep detayını getirir", "parameters": [{"name": "request_id", "in": "path", "required": True, "schema": {"type": "integer"}}], "responses": {"200": {"description": "OK"}}}}}}
        self.app.import_capture({"document": doc, "application": "Portal", "page": "API belgesi"})
        items = self.app.catalog.list()
        self.assertEqual(len(items), 3)
        detail = next(v for v in items if v["path"] == "/api/requests/{request_id}")
        self.assertEqual([p["name"] for p in detail["parameters"] if p["in"] == "path"], ["request_id"])
        self.assertEqual(detail["description_source"], "document")

    def test_analysis_revision_detects_new_capture(self):
        self.app.import_capture({"document": fixture(), "application": "Portal"})
        item = self.app.catalog.list()[0]
        self.app.catalog.update_description(item["id"], "Çıkarım", source="qwen", expected_revision=item["revision"])
        self.app.import_capture({"document": fixture(), "application": "Portal", "page": "Başka ekran"})
        changed = self.app.catalog.get(item["id"])
        self.assertNotEqual(changed["analysis_revision"], changed["revision"])
        with self.assertRaises(ValueError):
            self.app.catalog.update_description(item["id"], "stale", source="qwen", expected_revision=item["revision"])


class QwenTests(unittest.TestCase):
    def test_url_normalization_and_credentials_rejected(self):
        self.assertEqual(chat_url("http://127.0.0.1:8000"), "http://127.0.0.1:8000/v1/chat/completions")
        self.assertEqual(chat_url("https://model.example.com/v1/"), "https://model.example.com/v1/chat/completions")
        with self.assertRaises(ValueError):
            chat_url("https://user:secret@model.example.com/v1")
        with self.assertRaises(ValueError):
            chat_url("https://model.example.com/v1?api_key=secret")

    def test_api_key_never_saved_or_returned(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.local.json"
            settings = Settings(path)
            public = settings.update({"base_url": "https://model.example.com/v1", "model": "qwen-test", "api_key": "private-config-key"})
            self.assertTrue(public["has_api_key"])
            self.assertNotIn("private-config-key", path.read_text())
            self.assertNotIn("private-config-key", json.dumps(public))

    @patch("catalog_app.qwen.urlopen")
    def test_description_is_validated_and_does_not_change_endpoints(self, mocked):
        result = {"title": "Talep listesi", "description": "Çıkarım: talepleri listeler.", "usage": ["Filtreleri gönder."], "unknowns": ["Zorunluluk bilinmiyor."]}
        mocked.return_value.__enter__.return_value.read.return_value = json.dumps({"choices": [{"message": {"content": "<think>analysis</think>```json\n" + json.dumps(result) + "\n```"}}]}).encode()
        item = parse_document(fixture(), "Portal")[0][0]
        described = describe({"base_url": "http://127.0.0.1:8000/v1", "model": "qwen-test"}, item)
        self.assertEqual(described["title"], "Talep listesi")
        request_body = json.loads(mocked.call_args.args[0].data)
        self.assertNotIn("tools", request_body)
        self.assertNotIn("SENTETIK ORNEK", request_body["messages"][1]["content"])
        self.assertNotIn("path", described)

    @patch("catalog_app.qwen.urlopen")
    def test_non_json_model_response_is_rejected(self, mocked):
        mocked.return_value.__enter__.return_value.read.return_value = json.dumps({"choices": [{"message": {"content": "not-json"}}]}).encode()
        item = parse_document(fixture(), "Portal")[0][0]
        with self.assertRaises(ValueError):
            describe({"base_url": "http://127.0.0.1:8000/v1", "model": "qwen-test"}, item)

    @patch("catalog_app.qwen.urlopen")
    def test_upstream_errors_do_not_echo_sensitive_body(self, mocked):
        mocked.side_effect = HTTPError("https://model.example.com/private-url", 401, "secret details", {}, None)
        with self.assertRaises(ValueError) as caught:
            complete({"base_url": "https://model.example.com/v1", "model": "qwen-test"}, [])
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("private-url", str(caught.exception))
        self.assertNotIn("secret details", str(caught.exception))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server, self.app = make_server(self.temp.name, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, path, body, headers=None):
        req = Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json", "X-Catalog-Token": self.app.token, **(headers or {})}, method="POST")
        return urlopen(req)

    def test_full_import_filter_export_workflow(self):
        with self.post("/api/import", {"document": fixture(), "application": "Portal", "page": "Detay", "action": "Üzerime al"}) as response:
            self.assertEqual(json.load(response)["added"], 3)
        with urlopen(self.base + "/api/catalog?action=%C3%9Czerime%20al") as response:
            self.assertEqual(len(json.load(response)), 3)
        with urlopen(self.base + "/api/export.json") as response:
            self.assertFalse(json.load(response)["target_apis_executed"])
        with urlopen(self.base + "/api/export.md") as response:
            self.assertIn("API kataloğu", response.read().decode())

    def test_cross_origin_mutation_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/demo", {}, {"Origin": "https://evil.example.com"})
        self.assertEqual(caught.exception.code, 403)

    def test_missing_local_token_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/demo", {}, {"X-Catalog-Token": ""})
        self.assertEqual(caught.exception.code, 403)

    def test_no_target_api_execution_route(self):
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/execute", {"url": "https://portal.example.com/api/requests"})
        self.assertEqual(caught.exception.code, 404)

    def test_static_path_traversal_not_served(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/../data/settings.local.json")
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
