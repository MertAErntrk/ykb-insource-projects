"""Scoped catalog scans use injected cursors; no corporate database is queried."""

import json
import unittest
from unittest.mock import patch

from riskmatch.adapters.oracle import OracleAdapter
from riskmatch.errors import ConfigError, IntegrationError


CONFIG = {"source_database": "TEST", "environment": "test", "container": "PDB1",
          "schemas": ["A", "B", "C"], "user_env": "U", "password_env": "P", "dsn_env": "D"}
CREDS = {"user": "user", "password": "PRIVATE_PASSWORD_SENTINEL", "dsn": "PRIVATE_DSN_SENTINEL"}


def catalog(count=1):
    return {"METRICS": {"kind": "TABLE", "columns": ["FEATURE_"+str(i) for i in range(count)]}}


class Database:
    def __init__(self, data=None, *, execute_failures=None, fetch_failure=None, key_rows=None):
        self.data = data if data is not None else {schema: catalog() for schema in CONFIG["schemas"]}
        self.execute_failures = execute_failures or {}
        self.fetch_failure = fetch_failure
        self.key_rows = key_rows
        self.calls = []
        self.connections = []
        self.fetch_sizes = []
        self.after_fetch = None

    def connect(self, **kwargs):
        connection = Connection(self)
        self.connections.append(connection)
        return connection


class Connection:
    def __init__(self, database):
        self.database = database
        self.closed = False
        self.call_timeout = None

    def cursor(self):
        return Cursor(self.database)

    def close(self):
        self.closed = True


class Cursor:
    def __init__(self, database):
        self.database = database

    def execute(self, sql, binds=None):
        self.sql, self.binds = sql, binds or {}
        self.position = self.fetches = 0
        self.schema = self.binds.get("schema_name")
        self.database.calls.append((sql, self.binds))
        self.phase = next((phase for phase in ["ALL_OBJECTS", "ALL_TAB_COLUMNS", "ALL_COL_COMMENTS", "ALL_TAB_COMMENTS", "ALL_CONSTRAINTS"] if phase in sql), "CONTEXT")
        failure = self.database.execute_failures.get((self.schema, self.phase))
        if failure:
            raise RuntimeError(failure)
        objects = self.database.data.get(self.schema, {})
        allowed = {value for key, value in self.binds.items() if key.startswith("object_")}
        if self.phase == "ALL_OBJECTS":
            rows = [(obj, info["kind"]) for obj, info in objects.items()]
            rows += [(obj, "TABLE") for obj, info in objects.items() if info["kind"] == "MATERIALIZED VIEW"]
        elif self.phase == "ALL_TAB_COLUMNS":
            rows = [(self.schema, obj, column, "NUMBER", 22, 10, 0, "Y")
                    for obj, info in objects.items() if obj in allowed for column in info["columns"]]
        elif self.phase == "ALL_COL_COMMENTS":
            rows = [(obj, column, "Son ay havale kişi sayısı")
                    for obj, info in objects.items() if obj in allowed for column in info["columns"]]
        elif self.phase == "ALL_TAB_COMMENTS":
            rows = [(obj, "Synthetic metadata") for obj in objects if obj in allowed]
        elif self.phase == "ALL_CONSTRAINTS":
            rows = self.database.key_rows if self.database.key_rows is not None else [
                (obj, "P", "PK_"+obj, info["columns"][0], 1)
                for obj, info in objects.items() if obj in allowed and info["columns"]]
        else:
            rows = [("TEST_DB", "PDB1")]
        self.rows = rows[:self.binds.get("row_limit", len(rows))]

    def fetchone(self):
        if self.position == len(self.rows):
            return None
        row = self.rows[self.position]
        self.position += 1
        return row

    def fetchmany(self, size):
        self.database.fetch_sizes.append(size)
        if self.database.fetch_failure == (self.schema, self.phase, self.fetches):
            raise RuntimeError("DPY-4024 PRIVATE_PASSWORD_SENTINEL PRIVATE_DSN_SENTINEL")
        result = self.rows[self.position:self.position+size]
        self.position += len(result)
        self.fetches += 1
        if self.database.after_fetch:
            self.database.after_fetch(self)
        return result


class OracleCatalogScanTests(unittest.TestCase):
    def run_scan(self, database, progress=None, **changes):
        return OracleAdapter(dict(CONFIG, **changes), credentials=CREDS,
                             connector=database.connect).scan(progress=progress)

    def test_three_schemas_have_scoped_separate_queries_and_bound_parameters(self):
        database = Database()
        updates = []
        result = self.run_scan(database, progress=updates.append)
        self.assertTrue(result["complete"], result["errors"])
        self.assertEqual({row["schema"] for row in result["records"]}, {"A", "B", "C"})
        self.assertEqual(result["scan_stats"]["schemas_completed"], 3)
        self.assertEqual(result["scan_stats"]["record_count"], 3)
        self.assertTrue(updates)
        self.assertEqual(updates[-1]["records"], 3)
        self.assertTrue(all(connection.closed for connection in database.connections))
        self.assertEqual(len(database.connections), 1)
        for sql, binds in database.calls:
            if "SYS_CONTEXT" in sql:
                continue
            self.assertIn(":schema_name", sql)
            self.assertIn("ROWNUM <= :row_limit", sql)
            self.assertNotIn("ORDER BY", sql)
            self.assertIn(binds["schema_name"], CONFIG["schemas"])
            if "ALL_TAB_COLUMNS" in sql:
                self.assertNotIn("JOIN", sql)
                self.assertNotIn("COMMENTS", sql)
            if "ALL_OBJECTS" not in sql:
                self.assertEqual(binds["object_0"], "METRICS")
                self.assertNotIn("'METRICS'", sql)
        self.assertLessEqual(max(database.fetch_sizes), 200)
        record = result["records"][0]
        self.assertEqual(record["column_comment"], "Son ay havale kişi sayısı")
        self.assertEqual(record["table_comment"], "Synthetic metadata")
        self.assertEqual(record["key_columns"][0]["type"], "P")
        self.assertNotIn("key_constraints", record["missing"])

    def test_timeout_in_middle_schema_keeps_earlier_and_later_evidence(self):
        database = Database(execute_failures={("B", "ALL_TAB_COLUMNS"): "DPY-4024 private=PRIVATE_PASSWORD_SENTINEL"})
        result = self.run_scan(database)
        self.assertFalse(result["complete"])
        self.assertEqual({row["schema"] for row in result["records"]}, {"A", "C"})
        self.assertEqual(result["scan_stats"]["schemas_completed"], 2)
        self.assertEqual(len(database.connections), 2)
        self.assertTrue(all(connection.closed for connection in database.connections))
        failure = next(check for check in result["checks"] if check["status"] == "failed")
        self.assertEqual(failure["schema"], "B")
        self.assertEqual(failure["code"], "DPY-4024")
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_fetch_timeout_keeps_rows_already_received(self):
        database = Database({"A": catalog(250), "B": catalog()}, fetch_failure=("A", "ALL_TAB_COLUMNS", 1))
        result = self.run_scan(database, schemas=["A", "B"])
        self.assertFalse(result["complete"])
        self.assertEqual(len([row for row in result["records"] if row["schema"] == "A"]), 200)
        self.assertEqual(len([row for row in result["records"] if row["schema"] == "B"]), 1)
        partial = next(row for row in result["records"] if row["schema"] == "A")
        self.assertIn("column_comment", partial["missing"])
        self.assertIn("key_constraints", partial["missing"])
        self.assertEqual(partial["column_comment"], "")
        self.assertEqual(result["scan_stats"]["schemas_completed"], 1)

    def test_record_cap_prevents_absence_and_skips_remaining_schemas(self):
        database = Database({"A": catalog(5), "B": catalog()})
        result = self.run_scan(database, schemas=["A", "B"], max_records=2)
        self.assertEqual(len(result["records"]), 2)
        self.assertFalse(result["complete"])
        self.assertTrue(any("sınır" in error for error in result["errors"]))
        self.assertTrue(any(check["schema"] == "B" and check["status"] == "skipped" for check in result["checks"]))
        self.assertFalse(any(binds.get("schema_name") == "B" for _, binds in database.calls))

    def test_exact_last_batch_record_limit_can_still_be_complete(self):
        result = self.run_scan(Database({"A": catalog(2)}), schemas=["A"], max_records=2)
        self.assertEqual(len(result["records"]), 2)
        self.assertTrue(result["complete"], result["errors"])

    def test_listed_object_without_visible_columns_prevents_complete_scope(self):
        objects = catalog()
        objects["NO_VISIBLE_COLUMNS"] = {"kind": "VIEW", "columns": []}
        result = self.run_scan(Database({"A": objects}), schemas=["A"])
        self.assertEqual(len(result["records"]), 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["scan_stats"]["schemas_completed"], 0)
        visibility = next(check for check in result["checks"] if check.get("code") == "OBJECT_COLUMNS_NOT_VISIBLE")
        self.assertEqual(visibility["status"], "failed")
        self.assertIn("NO_VISIBLE_COLUMNS", visibility["message"])
        self.assertTrue(any("görünürlüğü eksik" in error for error in result["errors"]))

    def test_comment_timeout_retains_physical_columns_without_inventing_comments(self):
        database = Database(execute_failures={("A", "ALL_COL_COMMENTS"): "DPY-4024"})
        result = self.run_scan(database)
        self.assertEqual(len(result["records"]), 3)
        self.assertFalse(result["complete"])
        first = result["records"][0]
        self.assertEqual(first["schema"], "A")
        self.assertEqual(first["column_comment"], "")
        self.assertIn("column_comment", first["missing"])
        self.assertEqual(result["scan_stats"]["schemas_completed"], 2)

    def test_object_batches_are_small_and_mview_duplicates_are_deduplicated(self):
        objects = {"T"+str(i).zfill(3): {"kind": "TABLE", "columns": ["FIELD"]} for i in range(60)}
        objects["T000"]["kind"] = "MATERIALIZED VIEW"
        database = Database({"A": objects})
        result = self.run_scan(database, schemas=["A"])
        self.assertTrue(result["complete"], result["errors"])
        self.assertEqual(len(result["records"]), 60)
        self.assertEqual(result["records"][0]["object_type"], "MATERIALIZED VIEW")
        column_queries = [binds for sql, binds in database.calls if "ALL_TAB_COLUMNS" in sql]
        self.assertEqual(len(column_queries), 3)
        self.assertEqual([len([key for key in binds if key.startswith("object_")]) for binds in column_queries], [25, 25, 10])

    def test_time_budget_skips_more_work_and_preserves_existing_rows(self):
        database, clock = Database(), [0]
        def advance(cursor):
            if cursor.schema == "A" and cursor.phase == "ALL_CONSTRAINTS" and not cursor.rows:
                clock[0] = 301
        # A schema without keys ends cleanly before the next schema starts.
        database.key_rows = []
        database.after_fetch = advance
        with patch("riskmatch.adapters.oracle.monotonic", side_effect=lambda: clock[0]):
            result = self.run_scan(database)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["records"]), 1)
        self.assertTrue(any("süre" in error for error in result["errors"]))
        self.assertFalse(any(binds.get("schema_name") in {"B", "C"} for _, binds in database.calls))

    def test_truncated_key_query_never_presents_partial_composite_key_as_verified(self):
        rows = [("METRICS", "P", "PK_COMPOSITE", "KEY_"+str(i), i+1) for i in range(201)]
        result = self.run_scan(Database({"A": catalog()}, key_rows=rows), schemas=["A"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["records"][0]["key_columns"], [])
        self.assertIn("key_constraints", result["records"][0]["missing"])

    def test_all_schema_timeouts_return_zero_records_and_safe_phase_evidence(self):
        failures = {(schema, "ALL_TAB_COLUMNS"): "DPY-4024 PRIVATE_PASSWORD_SENTINEL" for schema in CONFIG["schemas"]}
        database = Database(execute_failures=failures)
        result = self.run_scan(database)
        self.assertFalse(result["complete"])
        self.assertEqual(result["records"], [])
        self.assertEqual(len(database.connections), 3)
        self.assertEqual(len([check for check in result["checks"] if check["status"] == "failed"]), 3)
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_invalid_limits_rejected_and_scope_mismatch_aborts(self):
        for config in ({"max_records": 0}, {"max_records": 10001}, {"catalog_timeout_seconds": 0}, {"catalog_timeout_seconds": 901}):
            with self.assertRaises(ConfigError):
                self.run_scan(Database(), **config)
        database = Database()
        with self.assertRaises(IntegrationError):
            self.run_scan(database, container="WRONG")
        self.assertTrue(database.connections[0].closed)
        self.assertFalse(any("ALL_OBJECTS" in sql for sql, _ in database.calls))


if __name__ == "__main__":
    unittest.main()
