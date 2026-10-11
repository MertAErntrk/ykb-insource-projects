"""Catalog cache keeps only complete metadata snapshots and never raw credentials."""

import os
import tempfile
import unittest
from unittest.mock import patch

from riskmatch.catalog_cache import CatalogCache, cache_key


def snapshot(complete=True, marker="v1"):
    return {"complete": complete, "version": marker, "records": [{"schema": "A", "object": "T", "column": "C"}],
            "scope": {"container": "PDB1", "schemas": ["A"]}}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CatalogCacheTests(unittest.TestCase):
    def test_key_sorts_schemas_and_hides_user_and_dsn(self):
        key = cache_key("scott", "host:1521/svc", "PDB1", ["b", "A"])
        self.assertEqual(key, cache_key("scott", "host:1521/svc", "PDB1", ["A", "B"]))
        self.assertNotEqual(key, cache_key("scott", "host:1521/svc", "PDB2", ["A", "B"]))
        self.assertNotEqual(key, cache_key("other", "host:1521/svc", "PDB1", ["A", "B"]))
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertNotIn("scott", key)

    def test_ttl_and_signature(self):
        clock = Clock()
        cache = CatalogCache(ttl_seconds=100, clock=clock)
        self.assertTrue(cache.put("k", snapshot(), signature="s1"))
        self.assertEqual(cache.get("k")["version"], "v1")
        self.assertEqual(cache.get("k", "s1")["version"], "v1")
        self.assertIsNone(cache.get("k", "s2"))
        clock.now += 50
        self.assertEqual(cache.info("k"), {"age_s": 50.0, "signature": "s1", "records": 1})
        clock.now += 51
        self.assertIsNone(cache.get("k"))
        self.assertIsNone(cache.info("k"))

    def test_incomplete_snapshot_is_not_stored(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CatalogCache(directory)
            self.assertFalse(cache.put("k", snapshot(complete=False)))
            self.assertIsNone(cache.get("k"))
            self.assertEqual(os.listdir(directory), [])

    def test_disk_roundtrip_survives_new_instance_and_invalidate(self):
        with tempfile.TemporaryDirectory() as directory:
            key = cache_key("u", "d", "PDB1", ["A"])
            self.assertTrue(CatalogCache(directory).put(key, snapshot(), "sig"))
            fresh = CatalogCache(directory)
            self.assertEqual(fresh.get(key, "sig")["version"], "v1")
            fresh.invalidate(key)
            self.assertIsNone(CatalogCache(directory).get(key))
            self.assertEqual(os.listdir(directory), [])

    def test_failed_replace_keeps_old_file_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            key = cache_key("u", "d", "PDB1", ["A"])
            self.assertTrue(CatalogCache(directory).put(key, snapshot(marker="old"), "s"))
            with patch("riskmatch.catalog_cache.os.replace", side_effect=OSError("disk")):
                self.assertFalse(CatalogCache(directory).put(key, snapshot(marker="new"), "s"))
            self.assertEqual(os.listdir(directory), [key + ".json"])
            self.assertEqual(CatalogCache(directory).get(key)["version"], "old")

    def test_corrupt_file_is_a_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            key = cache_key("u", "d", "PDB1", ["A"])
            with open(os.path.join(directory, key + ".json"), "w") as handle:
                handle.write("{broken")
            self.assertIsNone(CatalogCache(directory).get(key))

    def test_file_contains_no_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            key = cache_key("PRIVATE_USER_SENTINEL", "PRIVATE_DSN_SENTINEL", "PDB1", ["A"])
            CatalogCache(directory).put(key, snapshot(), "A:1:2026-01-01")
            for name in os.listdir(directory):
                self.assertNotIn("PRIVATE_", name)
                with open(os.path.join(directory, name), encoding="utf-8") as handle:
                    text = handle.read()
                for secret in ("PRIVATE_USER_SENTINEL", "PRIVATE_DSN_SENTINEL", "password"):
                    self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
