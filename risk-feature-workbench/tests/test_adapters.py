import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riskmatch.adapters.oracle import OracleAdapter
from riskmatch.adapters.qwen import EmbeddingAdapter, LLMAdapter
from riskmatch.errors import ConfigError, IntegrationError
from riskmatch.semantics import evaluate_candidate, parse_definition


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _):
        return self.body


class Transport:
    def __init__(self, body=None, error=None):
        self.body, self.error = body, error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request.full_url, json.loads(request.data), timeout))
        if self.error:
            raise self.error
        return Response(self.body)


EMBED = {"endpoint": "https://embed.internal.example/vectors", "allowed_hosts": ["embed.internal.example"],
         "model": "configured-id", "model_version": "approved-v1", "dimensions": 2,
         "request_template": {"model": "{model}", "inputs": "{texts}"}, "response_path": "vectors[*].values",
         "max_retries": 0}
LLM = {"endpoint": "https://llm.internal.example/v1/chat/completions", "allowed_hosts": ["llm.internal.example"],
       "model": "configured-llm", "model_version": "approved-v1", "contract": "openai_chat", "max_retries": 0}
ORACLE = {"source_database": "risk", "environment": "dev", "container": "PDB1", "schemas": ["RISK_DWH"],
          "user_env": "TEST_ORACLE_USER", "password_env": "TEST_ORACLE_PASSWORD", "dsn_env": "TEST_ORACLE_DSN"}


class FakeCursor:
    def __init__(self):
        self.sql = ""
        self.calls = []

    def execute(self, sql, binds=None):
        self.sql = sql
        self.calls.append((sql, binds))

    def fetchone(self):
        return ("RISK", "PDB1")

    def fetchall(self):
        if "ALL_TAB_COLUMNS" in self.sql:
            return [("RISK_DWH", "CUSTOMER_METRIC", "HAV_LM", "NUMBER", 22, 10, 0, "Y",
                     None, "Synthetic metrics", "TABLE")]
        return [("RISK_DWH", "CUSTOMER_METRIC", "P", "PK_METRIC", "HAV_LM", 1)]


class FakeConnection:
    def __init__(self):
        self.cur = FakeCursor()
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


class EmptyCursor(FakeCursor):
    def fetchall(self):
        return []


class EmptyConnection(FakeConnection):
    def __init__(self):
        self.cur = EmptyCursor()
        self.closed = False


class AdapterTests(unittest.TestCase):
    def test_embedding_explicit_contract_and_dimensions(self):
        transport = Transport({"vectors": [{"values": [1, 0]}, {"values": [0, 1]}]})
        adapter = EmbeddingAdapter(EMBED, transport=transport)
        self.assertEqual(adapter.embed(["ilk", "ikinci"]), [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(transport.requests[0][0], EMBED["endpoint"])
        self.assertEqual(transport.requests[0][1], {"model": "configured-id", "inputs": ["ilk", "ikinci"]})

    def test_embedding_rejects_bad_shape_and_timeout(self):
        with self.assertRaises(IntegrationError):
            EmbeddingAdapter(EMBED, transport=Transport({"vectors": [{"values": [1]}]})).embed(["x"])
        with self.assertRaises(IntegrationError):
            EmbeddingAdapter(EMBED, transport=Transport(error=urllib.error.URLError("secret"))).embed(["x"])

    def test_host_allowlist_and_undocumented_model_rejected(self):
        with self.assertRaises(ConfigError):
            EmbeddingAdapter({**EMBED, "allowed_hosts": ["other.internal.example"]})
        with self.assertRaises(ConfigError):
            EmbeddingAdapter({**EMBED, "model_version": ""})

    def test_llm_verbatim_evidence_and_only_candidate_ids(self):
        candidates = [{"catalog_id": "c1", "schema": "RISK_DWH", "object": "METRIC",
                       "column": "HAV_LM", "column_comment": "Son ay havale kişi sayısı"}]
        body = {"choices": [{"message": {"content": json.dumps({"assessments": [
            {"catalog_id": "c1", "evidence": "havale kişi sayısı", "note": "Olası aday"}]})}}]}
        adapter = LLMAdapter(LLM, transport=Transport(body))
        self.assertEqual(adapter.evaluate({"name": "Havale_LM"}, candidates)["assessments"][0]["catalog_id"], "c1")
        for bad in ({"catalog_id": "invented", "evidence": "havale kişi sayısı"},
                    {"catalog_id": "c1", "evidence": "uydurulmuş kanıt"},
                    {"catalog_id": "c1", "evidence": "HAV_LM", "status": "supported"}):
            response = {"choices": [{"message": {"content": json.dumps({"assessments": [bad]})}}]}
            with self.assertRaises(IntegrationError):
                LLMAdapter(LLM, transport=Transport(response)).evaluate({"name": "Havale_LM"}, candidates)

    def test_llm_malformed_response(self):
        with self.assertRaises(IntegrationError):
            LLMAdapter(LLM, transport=Transport({"choices": [{"message": {"content": "not-json"}}]})).evaluate(
                {"name": "X"}, [{"catalog_id": "c1", "column": "X"}])

    def test_llm_template_contract_and_no_candidate_call(self):
        config = {**LLM, "contract": "template", "request_template": {"payload": "{context}",
                                                                     "model": "{model}"},
                  "response_path": "result"}
        transport = Transport({"result": {"assessments": [{"catalog_id": "c1", "evidence": "METRIC"}]}})
        adapter = LLMAdapter(config, transport=transport)
        self.assertEqual(adapter.evaluate({"name": "X"}, [])['assessments'], [])
        self.assertEqual(len(transport.requests), 0)
        result = adapter.evaluate({"name": "X"}, [{"catalog_id": "c1", "object": "METRIC"}])
        self.assertEqual(result["assessments"][0]["evidence"], "METRIC")
        self.assertEqual(transport.requests[0][1]["payload"]["candidates"][0]["catalog_id"], "c1")

    def test_oracle_read_only_inventory_and_bound_schema(self):
        connection = FakeConnection()
        env = {"TEST_ORACLE_USER": "test", "TEST_ORACLE_PASSWORD": "secret", "TEST_ORACLE_DSN": "fake"}
        with patch.dict(os.environ, env):
            snapshot = OracleAdapter(ORACLE, connector=lambda **_: connection).scan()
        self.assertTrue(connection.closed)
        self.assertTrue(snapshot["complete"])
        self.assertEqual(len(snapshot["records"]), 1)
        self.assertIn("column_comment", snapshot["records"][0]["missing"])
        inventory_sql, binds = connection.cur.calls[1]
        self.assertIn("LEFT JOIN ALL_COL_COMMENTS", inventory_sql)
        self.assertIn("LEFT JOIN ALL_TAB_COMMENTS", inventory_sql)
        self.assertIn(":schema_0", inventory_sql)
        self.assertNotIn("'RISK_DWH'", inventory_sql)
        self.assertEqual(binds, {"schema_0": "RISK_DWH"})
        self.assertEqual(snapshot["records"][0]["key_columns"][0]["type"], "P")
        self.assertEqual(connection.call_timeout, 30000)
        self.assertIn("c.STATUS = 'ENABLED'", connection.cur.calls[2][0])
        self.assertIn("c.VALIDATED = 'VALIDATED'", connection.cur.calls[2][0])

    def test_oracle_comment_flows_to_semantic_evidence(self):
        comment = "Başvuru bazında faiz türü. 1 sabit faiz, 2 değişken faiz."

        class CommentCursor(FakeCursor):
            def fetchall(self):
                if "ALL_TAB_COLUMNS" in self.sql:
                    return [("RISK_DWH", "APPLICATION", "INTEREST_TYPE_CODE", "NUMBER", 22, 1, 0,
                             "Y", comment, "", "TABLE")]
                return []

        connection = FakeConnection()
        connection.cur = CommentCursor()
        env = {"TEST_ORACLE_USER": "u", "TEST_ORACLE_PASSWORD": "p", "TEST_ORACLE_DSN": "d"}
        with patch.dict(os.environ, env):
            record = OracleAdapter(ORACLE, connector=lambda **_: connection).scan()["records"][0]
        self.assertEqual(record["definition"]["facts"]["concept"]["value"], "interest_type")
        self.assertEqual(record["definition"]["facts"]["concept"]["source"], "verified_metadata")
        request = {"name": "FaizTuru", "description": comment,
                   "definition": parse_definition("FaizTuru", comment)}
        result = evaluate_candidate(request, record)
        self.assertNotEqual(result["status"], "conflict")
        self.assertTrue(any(check["dimension"] == "concept" and check["status"] == "compatible"
                            for check in result["checks"]))

    def test_oracle_missing_env_and_wrong_container(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError):
                OracleAdapter(ORACLE, connector=lambda **_: FakeConnection()).scan()
        with patch.dict(os.environ, {"TEST_ORACLE_USER": "u", "TEST_ORACLE_PASSWORD": "p", "TEST_ORACLE_DSN": "d"}):
            with self.assertRaises(IntegrationError):
                OracleAdapter({**ORACLE, "container": "OTHER"}, connector=lambda **_: FakeConnection()).scan()

    def test_oracle_empty_visible_schema_is_incomplete(self):
        with patch.dict(os.environ, {"TEST_ORACLE_USER": "u", "TEST_ORACLE_PASSWORD": "p", "TEST_ORACLE_DSN": "d"}):
            snapshot = OracleAdapter(ORACLE, connector=lambda **_: EmptyConnection()).scan()
        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["records"], [])


if __name__ == "__main__":
    unittest.main()
