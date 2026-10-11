"""Oracle diagnostic regression tests; no corporate server is contacted."""

import json
import unittest
from unittest.mock import patch

from riskmatch.adapters.oracle import OracleAdapter, oracle_error
from riskmatch.errors import ConfigError
from riskmatch.oracle_diagnostics import diagnose_oracle, diagnose_selected_schemas


CONFIG = {"source_database": "TEST", "environment": "test", "container": "PROBE",
          "schemas": ["PROBE"], "user_env": "U", "password_env": "P", "dsn_env": "D"}
CREDENTIALS = {"user": "test", "password": "PRIVATE_PASSWORD_SENTINEL", "dsn": "PRIVATE_DSN_SENTINEL"}


class Cursor:
    def __init__(self, *, fail=None, schemas=None, visible=None, clock=None):
        self.calls = []
        self.fail = fail or {}
        self.schemas = [("DATA_ONE",), ("DATA_TWO",)] if schemas is None else schemas
        self.visible = {"DATA_ONE", "DATA_TWO"} if visible is None else visible
        self.clock = clock

    def execute(self, sql, binds=None):
        self.calls.append((sql, binds))
        self.sql, self.binds = sql, binds
        for key, error in self.fail.items():
            if key in sql:
                raise error
        if self.clock and "ALL_TAB_COLUMNS" in sql:
            self.clock[0] += 46

    def fetchone(self):
        if "ALL_TAB_COLUMNS" in self.sql:
            if self.binds["schema_name"] in self.visible:
                return ("TABLE_A", "COLUMN_A", "NUMBER", "Description", "Table description")
            return None
        return ("TEST_DB", "TEST_PDB")

    def fetchmany(self, count):
        self.fetch_limit = count
        return self.schemas[:count]


class Connection:
    def __init__(self, cursor=None):
        self.cur = cursor or Cursor()
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


class OracleDiagnosticsTests(unittest.TestCase):
    def probe(self, connection):
        with patch.object(OracleAdapter, "_connect", return_value=connection):
            return diagnose_oracle(CONFIG, CREDENTIALS)

    def selected(self, connection, schemas=None, **config):
        with patch.object(OracleAdapter, "_connect", return_value=connection):
            return diagnose_selected_schemas(dict(CONFIG, **config), CREDENTIALS, schemas or ["DATA_ONE", "DATA_TWO"])

    def test_discovery_does_not_scan_column_catalog(self):
        connection = Connection()
        report = self.probe(connection)
        self.assertTrue(report["ok"])
        self.assertTrue(report["connected"])
        self.assertEqual(report["schema_source"], "ALL_USERS")
        self.assertEqual(report["schemas"], ["DATA_ONE", "DATA_TWO"])
        self.assertIn("henüz doğrulanmadı", report["warning"])
        self.assertEqual([check["status"] for check in report["checks"]], ["passed"]*4)
        self.assertTrue(connection.closed)
        sql, binds = connection.cur.calls[-1]
        self.assertIn("ALL_USERS", sql)
        self.assertNotIn("ALL_TAB_COLUMNS", " ".join(row[0] for row in connection.cur.calls))
        self.assertIn("ROWNUM <= :schema_limit", sql)
        self.assertEqual(binds, {"schema_limit": 1001})
        self.assertEqual(connection.cur.fetch_limit, 1001)

    def test_timeout_retains_successful_login_and_context_with_safe_guidance(self):
        connection = Connection(Cursor(fail={"ALL_USERS": RuntimeError("DPY-4024 call timeout PRIVATE_PASSWORD_SENTINEL PRIVATE_DSN_SENTINEL")}))
        report = self.probe(connection)
        self.assertFalse(report["ok"])
        self.assertTrue(report["connected"])
        self.assertEqual(report["container"], "TEST_PDB")
        self.assertEqual(report["schemas"], [])
        self.assertEqual(report["checks"][-1]["code"], "DPY-4024")
        self.assertIn("süre sınırını aştı", report["error"])
        self.assertIn("parola veya yetki hatası anlamına gelmez", report["error"])
        self.assertNotIn("PRIVATE_", json.dumps(report))
        self.assertTrue(connection.closed)

    def test_login_failure_skips_all_queries(self):
        with patch.object(OracleAdapter, "_connect", side_effect=RuntimeError("ORA-01017 PRIVATE_PASSWORD_SENTINEL")):
            report = diagnose_oracle(CONFIG, CREDENTIALS)
        self.assertFalse(report["connected"])
        self.assertEqual(report["checks"][0]["code"], "ORA-01017")
        self.assertEqual([check["status"] for check in report["checks"]], ["failed", "skipped", "skipped", "skipped"])
        self.assertNotIn("PRIVATE_", json.dumps(report))

    def test_context_failure_is_not_reported_as_schema_failure(self):
        connection = Connection(Cursor(fail={"SYS_CONTEXT": RuntimeError("ORA-01031 PRIVATE_DSN_SENTINEL")}))
        report = self.probe(connection)
        self.assertTrue(report["connected"])
        self.assertFalse(report["ok"])
        self.assertIsNone(report["container"])
        self.assertEqual(report["checks"][-2]["code"], "ORA-01031")
        self.assertEqual(report["checks"][-1]["status"], "skipped")
        self.assertTrue(connection.closed)

    def test_schema_limit_is_reported_and_empty_list_is_not_usable(self):
        report = self.probe(Connection(Cursor(schemas=[("S"+str(index),) for index in range(1001)])))
        self.assertTrue(report["truncated"])
        self.assertEqual(len(report["schemas"]), 1000)
        self.assertIn("sınırlı", report["warning"])
        empty = self.probe(Connection(Cursor(schemas=[])))
        self.assertTrue(empty["connected"])
        self.assertFalse(empty["ok"])
        self.assertIn("şema adayı bulunamadı", empty["error"])

    def test_selected_metadata_checks_bind_schemas_and_limit_rows(self):
        connection = Connection()
        report = self.selected(connection)
        self.assertTrue(report["ok"])
        self.assertEqual(report["accessible_schemas"], ["DATA_ONE", "DATA_TWO"])
        sql_calls = connection.cur.calls[1:]
        self.assertEqual(len(sql_calls), 2)
        for sql, binds in sql_calls:
            self.assertIn("ROWNUM <= 1", sql)
            self.assertIn("c.OWNER = :schema_name", sql)
            self.assertNotIn(binds["schema_name"], sql)
            self.assertNotIn("ALL_USERS", sql)
        self.assertIn("İş verisi okunmaz", report["warning"])
        self.assertTrue(connection.closed)

    def test_schema_without_visible_columns_is_not_verified(self):
        report = self.selected(Connection(Cursor(visible={"DATA_ONE"})))
        self.assertFalse(report["ok"])
        self.assertEqual(report["accessible_schemas"], ["DATA_ONE"])
        self.assertEqual(report["checks"][-1]["status"], "failed")
        self.assertIn("Şema boş olabilir", report["checks"][-1]["message"])

    def test_selected_timeout_stops_reusing_possibly_invalid_connection(self):
        connection = Connection(Cursor(fail={"ALL_TAB_COLUMNS": RuntimeError("DPY-4024 PRIVATE_PASSWORD_SENTINEL")}))
        report = self.selected(connection)
        self.assertFalse(report["ok"])
        self.assertEqual(report["checks"][-2]["code"], "DPY-4024")
        self.assertEqual(report["checks"][-1]["status"], "skipped")
        self.assertEqual(len(connection.cur.calls), 2)
        self.assertNotIn("PRIVATE_", json.dumps(report))
        self.assertTrue(connection.closed)

    def test_overall_budget_skips_remaining_schemas(self):
        clock = [0]
        connection = Connection(Cursor(clock=clock))
        with patch("riskmatch.oracle_diagnostics.monotonic", side_effect=lambda: clock[0]):
            report = self.selected(connection)
        self.assertFalse(report["ok"])
        self.assertEqual(report["checks"][-1]["status"], "skipped")
        self.assertEqual(report["accessible_schemas"], ["DATA_ONE"])
        self.assertEqual(len(connection.cur.calls), 2)

    def test_scope_mismatch_prevents_targeted_queries(self):
        connection = Connection()
        report = self.selected(connection, container="WRONG_PDB")
        self.assertFalse(report["ok"])
        self.assertEqual(len(connection.cur.calls), 1)
        self.assertIn("beklenen veritabanı", report["error"])
        self.assertTrue(connection.closed)

    def test_selected_validation_and_safe_timeout_message(self):
        for schemas in ([], ["x"]*31, [None], ["x"*129], ["SYS"]):
            with self.assertRaises(ConfigError):
                diagnose_selected_schemas(CONFIG, CREDENTIALS, schemas)
        message = str(oracle_error(RuntimeError("DPY-4024 secret string"), "test"))
        self.assertNotIn("secret string", message)
        self.assertIn("süre sınırını aştı", message)


if __name__ == "__main__":
    unittest.main()
