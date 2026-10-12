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
        self.group_failures = {}

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
        failure = (self.database.execute_failures.get((self.schema, self.phase))
                   or self.database.group_failures.get((self.phase, self.binds.get("object_0"))))
        if failure:
            raise RuntimeError(failure)
        objects = self.database.data.get(self.schema, {})
        allowed = {value for key, value in self.binds.items() if key.startswith("object_")}
        if self.phase == "ALL_OBJECTS":
            rows = [(obj, info["kind"]) for obj, info in objects.items()]
            rows += [(obj, "TABLE") for obj, info in objects.items() if info["kind"] == "MATERIALIZED VIEW"]
            prefixes = [value[:-1].replace("\\", "") for key, value in self.binds.items() if key.startswith("x")]
            rows = [row for row in rows if not any(row[0].startswith(prefix) for prefix in prefixes)]
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
            if "ALL_OBJECTS" in sql or "ALL_TAB_COLUMNS" in sql:
                self.assertIn("ORDER BY", sql)
                self.assertLess(sql.index("ORDER BY"), sql.index("ROWNUM"))
            else:
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
        objects = {"T1": {"kind": "TABLE", "columns": ["F"]}, "T2": {"kind": "TABLE", "columns": ["F"]}}
        database = Database({"A": objects, "B": catalog()})
        result = self.run_scan(database, schemas=["A", "B"], max_records=2)
        self.assertEqual(len(result["records"]), 2)
        self.assertFalse(result["complete"])
        self.assertTrue(any("sınır" in error for error in result["errors"]))
        self.assertTrue(any(check["schema"] == "B" and check["status"] == "skipped" for check in result["checks"]))
        self.assertFalse(any(binds.get("schema_name") == "B" for _, binds in database.calls))

    def test_record_order_from_database_does_not_change_records_or_version(self):
        forward = {"A": {"T"+str(i): {"kind": "TABLE", "columns": ["C"+str(j) for j in range(3)]} for i in range(5)}}
        backward = {"A": {name: {"kind": info["kind"], "columns": list(reversed(info["columns"]))}
                          for name, info in reversed(list(forward["A"].items()))}}
        keys = [(name, "P", "PK_"+name, "C0", 1) for name in forward["A"]]
        first = self.run_scan(Database(forward, key_rows=keys), schemas=["A"])
        second = self.run_scan(Database(backward, key_rows=list(reversed(keys))), schemas=["A"])
        self.assertEqual(first["records"] and [r["catalog_id"] for r in first["records"]],
                         [r["catalog_id"] for r in second["records"]])
        self.assertEqual(first["version"], second["version"])
        self.assertEqual(len(first["records"]), 15)

    def test_helper_timeout_marks_only_that_group_and_scan_continues(self):
        objects = {"T"+str(i).zfill(3): {"kind": "TABLE", "columns": ["F1", "F2"]} for i in range(30)}
        database = Database({"A": objects})
        database.group_failures[("ALL_COL_COMMENTS", "T000")] = "DPY-4024 PRIVATE_PASSWORD_SENTINEL"
        result = self.run_scan(database, schemas=["A"])
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["records"]), 60)
        first = [r for r in result["records"] if r["object"] < "T025"]
        second = [r for r in result["records"] if r["object"] >= "T025"]
        self.assertEqual(len(second), 10)
        self.assertTrue(all("column_comment" in r["missing"] and r["column_comment"] == "" for r in first))
        self.assertTrue(all("column_comment" not in r["missing"] and r["column_comment"] for r in second))
        self.assertEqual(result["coverage"]["failed_groups"], 1)
        self.assertEqual(len(database.connections), 2)
        self.assertTrue(any("DPY-4024" in error for error in result["errors"]))
        self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_record_cap_never_cuts_a_group_in_half_and_names_dropped_objects(self):
        objects = {"T"+str(i).zfill(3): {"kind": "TABLE", "columns": ["F1"] if i < 25 else ["F1", "F2"]}
                   for i in range(28)}
        result = self.run_scan(Database({"A": objects}), schemas=["A"], max_records=30)
        self.assertEqual(len(result["records"]), 25)
        self.assertFalse(result["complete"])
        coverage = result["coverage"]
        self.assertEqual(coverage["objects_total"], 28)
        self.assertEqual(coverage["objects_scanned"], 25)
        self.assertEqual(coverage["columns"], 25)
        self.assertEqual(coverage["truncated_objects"], ["A.T025", "A.T026", "A.T027"])
        self.assertFalse({"A."+r["object"] for r in result["records"]} & set(coverage["truncated_objects"]))

    def test_coverage_is_present_and_clean_on_full_scan(self):
        result = self.run_scan(Database())
        self.assertEqual(result["coverage"], {"objects_total": 3, "objects_scanned": 3, "columns": 3,
                                              "truncated_objects": [], "failed_groups": 0})

    def test_signature_uses_one_query_per_schema_and_is_deterministic(self):
        database = Database()
        adapter = OracleAdapter(dict(CONFIG), credentials=CREDS, connector=database.connect)
        first, second = adapter.signature(), adapter.signature()
        self.assertEqual(first, second)
        queries = [sql for sql, _ in database.calls]
        self.assertEqual(len(queries), 6)
        self.assertTrue(all("COUNT(*)" in sql and "LAST_DDL_TIME" in sql for sql in queries))
        self.assertTrue(all(connection.closed for connection in database.connections))

    def test_signature_error_is_safe(self):
        database = Database(execute_failures={("B", "ALL_OBJECTS"): "DPY-4024 PRIVATE_PASSWORD_SENTINEL"})
        adapter = OracleAdapter(dict(CONFIG), credentials=CREDS, connector=database.connect)
        with self.assertRaises(IntegrationError) as caught:
            adapter.signature()
        self.assertIn("DPY-4024", str(caught.exception))
        self.assertNotIn("PRIVATE_", str(caught.exception))

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
        for config in ({"max_records": 0}, {"max_records": 100001}, {"catalog_timeout_seconds": 0}, {"catalog_timeout_seconds": 901}):
            with self.assertRaises(ConfigError):
                self.run_scan(Database(), **config)
        database = Database()
        with self.assertRaises(IntegrationError):
            self.run_scan(database, container="WRONG")
        self.assertTrue(database.connections[0].closed)
        self.assertFalse(any("ALL_OBJECTS" in sql for sql, _ in database.calls))

    def test_max_records_accepts_100000_and_rejects_100001(self):
        OracleAdapter(dict(CONFIG, max_records=100000), credentials=CREDS, connector=Database().connect)
        with self.assertRaises(ConfigError):
            OracleAdapter(dict(CONFIG, max_records=100001), credentials=CREDS, connector=Database().connect)

    def test_dm_cad_inp_out_excluded_with_bound_escaped_patterns(self):
        data = {"DM_CAD": {"INP_X": {"kind": "TABLE", "columns": ["F"]}, "OUT_Y": {"kind": "TABLE", "columns": ["F"]},
                           "INPUT": {"kind": "TABLE", "columns": ["F"]}, "FEAT": {"kind": "TABLE", "columns": ["F"]}},
                "EDW": {"INP_Z": {"kind": "TABLE", "columns": ["F"]}}}
        database = Database(data)
        result = self.run_scan(database, schemas=["DM_CAD", "EDW"])
        self.assertEqual({(r["schema"], r["object"]) for r in result["records"]},
                         {("DM_CAD", "INPUT"), ("DM_CAD", "FEAT"), ("EDW", "INP_Z")})
        listing = [(sql, binds) for sql, binds in database.calls if "FROM ALL_OBJECTS" in sql]
        dm_sql, dm_binds = next(item for item in listing if item[1]["schema_name"] == "DM_CAD")
        self.assertEqual(dm_sql.count("NOT LIKE"), 2)
        self.assertIn("ESCAPE '" + chr(92) + "'", dm_sql)
        self.assertNotIn("INP", dm_sql)
        self.assertEqual((dm_binds["x0"], dm_binds["x1"]), ("INP\\_%", "OUT\\_%"))
        edw_sql, edw_binds = next(item for item in listing if item[1]["schema_name"] == "EDW")
        self.assertNotIn("NOT LIKE", edw_sql)
        self.assertFalse(any(key.startswith("x") for key in edw_binds))
        self.assertEqual(result["scope"]["exclude_objects"], [{"schema": "DM_CAD", "prefixes": ["INP_", "OUT_"]}])

    def test_signature_applies_same_exclusion_and_empty_list_disables_it(self):
        database = Database()
        OracleAdapter(dict(CONFIG, schemas=["DM_CAD", "EDW"]), credentials=CREDS, connector=database.connect).signature()
        by_schema = {binds["schema_name"]: (sql, binds) for sql, binds in database.calls}
        self.assertEqual(by_schema["DM_CAD"][0].count("NOT LIKE"), 2)
        self.assertEqual(by_schema["DM_CAD"][1]["x0"], "INP\\_%")
        self.assertNotIn("NOT LIKE", by_schema["EDW"][0])
        database = Database()
        OracleAdapter(dict(CONFIG, schemas=["DM_CAD"], exclude_objects=[]), credentials=CREDS,
                      connector=database.connect).signature()
        self.assertNotIn("NOT LIKE", database.calls[0][0])
        with self.assertRaises(ConfigError):
            OracleAdapter(dict(CONFIG, exclude_objects=[{"schema": "A"}]), credentials=CREDS)

    def test_scan_follows_priority_then_alphabetical_order(self):
        database = Database({name: catalog() for name in ("A", "B", "DM", "DM_CAD", "EDW")})
        result = self.run_scan(database, schemas=["A", "B", "EDW", "DM", "DM_CAD"])
        seen = []
        for _, binds in database.calls:
            schema = binds.get("schema_name")
            if schema and schema not in seen:
                seen.append(schema)
        self.assertEqual(seen, ["DM_CAD", "DM", "EDW", "A", "B"])
        self.assertEqual(result["scope"]["schemas"], ["A", "B", "DM", "DM_CAD", "EDW"])
        self.assertTrue(all(result["schema_complete"].values()))
        self.assertEqual(result["schema_errors"], {})
        database = Database()
        self.run_scan(database, schema_priority=["C", "B"])
        order = []
        for _, binds in database.calls:
            if binds.get("schema_name") and binds["schema_name"] not in order:
                order.append(binds["schema_name"])
        self.assertEqual(order, ["C", "B", "A"])

    def test_record_cap_marks_only_later_schemas_incomplete(self):
        objects = {"T1": {"kind": "TABLE", "columns": ["F"]}, "T2": {"kind": "TABLE", "columns": ["F"]}}
        result = self.run_scan(Database({"A": objects, "B": catalog(), "C": catalog()}), max_records=2)
        self.assertEqual(result["schema_complete"], {"A": True, "B": False, "C": False})
        self.assertEqual(result["schema_errors"], {"B": "KAYIT_SINIRI", "C": "KAYIT_SINIRI"})
        self.assertFalse(result["complete"])

    def test_failed_schema_gets_safe_code_and_others_stay_complete(self):
        database = Database(execute_failures={("B", "ALL_TAB_COLUMNS"): "DPY-4024 PRIVATE_PASSWORD_SENTINEL"})
        result = self.run_scan(database)
        self.assertEqual(result["schema_complete"], {"A": True, "B": False, "C": True})
        self.assertEqual(result["schema_errors"], {"B": "DPY-4024"})
        self.assertNotIn("PRIVATE_", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
