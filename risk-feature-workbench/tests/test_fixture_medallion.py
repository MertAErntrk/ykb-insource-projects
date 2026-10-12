import unittest

import fixture_medallion as fx


def _cols(rows, schema, table):
    return {row["column"] for row in rows if row["schema"] == schema and row["object"] == table}


class MedallionFixtureTests(unittest.TestCase):
    def test_schemas_and_tables(self):
        rows = fx.records()
        self.assertEqual({row["schema"] for row in rows}, {"DM_CAD", "DM", "EDW"})
        tables = {(row["schema"], row["object"]) for row in rows}
        self.assertEqual(len([t for t in tables if t[0] == "DM_CAD"]), 7)
        self.assertEqual(len([t for t in tables if t[0] == "DM"]), 1)
        self.assertEqual(len([t for t in tables if t[0] == "EDW"]), 1)

    def test_expected_columns_exist_with_exact_comments(self):
        rows = {(r["schema"], r["object"], r["column"]): r for r in fx.records()}
        for request in fx.REQUESTS:
            if isinstance(request["expect"], tuple):
                self.assertIn(request["expect"], rows, request["name"])
        eft = rows[("DM_CAD", fx.XFER_TABLE, "TOTAL_EFT_TRX_AMT_TL_W1M")]
        self.assertEqual(eft["column_comment"], "Müşterinin ilgili yıl aydan önceki 1 ay içerisinde tüm "
                         "kanallarından yaptığı toplam giden EFT tutarıdır.")
        self.assertEqual(eft["table_comment"], fx.XFER_COMMENT)
        self.assertEqual(len(_cols(fx.records(), "DM_CAD", fx.XFER_TABLE)), 2 + 24 + 4)
        self.assertIn("GIDEN_EFT_TUT_LM", _cols(fx.records(), "DM_CAD", "OUT_RISK_FEATURES"))
        self.assertEqual(len(fx.REQUESTS), 9)

    def test_catalog_ids_unique(self):
        for variant in fx.VARIANTS:
            ids = [row["catalog_id"] for row in fx.records(variant)]
            self.assertEqual(len(ids), len(set(ids)), variant)

    def test_variants(self):
        base = _cols(fx.records(), "DM_CAD", fx.XFER_TABLE)
        self.assertIn(fx.DENOMINATOR, base)
        self.assertNotIn(fx.RATIO_COLUMN, base)
        self.assertIn(fx.RATIO_COLUMN, _cols(fx.records("ratio_column"), "DM_CAD", fx.XFER_TABLE))
        cross = fx.records("cross_table")
        self.assertNotIn(fx.DENOMINATOR, _cols(cross, "DM_CAD", fx.XFER_TABLE))
        self.assertIn(fx.DENOMINATOR, _cols(cross, "DM_CAD", fx.CROSS_TABLE))
        missing = fx.records("denominator_missing")
        self.assertFalse([r for r in missing if r["column"] == fx.DENOMINATOR])
        with self.assertRaises(ValueError):
            fx.records("yok")

    def test_snapshot_scope_filter(self):
        snap = fx.snapshot(schemas=("DM",), complete=False)
        self.assertEqual(snap["scope"]["schemas"], ["DM"])
        self.assertFalse(snap["complete"])
        self.assertEqual({r["schema"] for r in snap["records"]}, {"DM"})
        self.assertEqual(fx.snapshot()["scope"]["schemas"], ["DM_CAD", "DM", "EDW"])


if __name__ == "__main__":
    unittest.main()
