"""Independent semantic acceptance checks through the real Excel/pipeline path."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from riskmatch.demo import _record, demo_snapshot
from riskmatch.excelio import HEADERS, read_requests
from riskmatch.pipeline import analyze


class SemanticIntegrationTests(unittest.TestCase):
    def run_case(self, description, comment, reported="Hayır"):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            wb = Workbook()
            ws = wb.active
            ws.append(HEADERS)
            ws.append(["Başvuru", "Gelir", "Numeric", description, reported, "Korunmalı", "2 gün"])
            wb.save(root / "input.xlsx")
            wb.close()
            snapshot = demo_snapshot()
            snapshot["records"] = [_record("Gelir", comment)]
            with patch("riskmatch.demo.demo_snapshot", return_value=snapshot):
                return analyze(root / "input.xlsx", root / "out.xlsx", strategy="bm25")["results"][0]

    def test_reported_yes_is_not_semantic_truth(self):
        result = self.run_case("Başvuruda beyan edilen gelir.", "Başvuru bazında doğrulanmış gelir TRY.", "Evet")
        self.assertEqual(result["physical_name"], "present")
        self.assertNotEqual(result["semantic_status"], "supported")
        self.assertEqual(result["expert_decision"], "")

    def test_static_source_qualifier_not_lost(self):
        for wanted, actual in (("net gelir", "brüt gelir"), ("aylık gelir", "yıllık gelir")):
            with self.subTest(wanted=wanted):
                result = self.run_case("Başvuru bazında beyan edilen " + wanted + " TRY.", "Başvuru bazında beyan edilen " + actual + " TRY.")
                self.assertNotEqual(result["semantic_status"], "supported")

    def test_unknown_currency_is_not_assumed(self):
        text = "Başvuru bazında beyan edilen gelir."
        result = self.run_case(text, text)
        self.assertEqual(result["semantic_status"], "insufficient")
        self.assertTrue(any(check["dimension"] == "unit" and check["status"] == "missing" for check in result["candidates"][0]["checks"]))

    def test_supported_static_template_survives_guard(self):
        text = "Başvuru bazında beyan edilen gelir TRY."
        self.assertEqual(self.run_case(text, text)["semantic_status"], "supported")

    def test_safe_excel_formula_dependency_and_null_unknown(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            wb = Workbook()
            ws = wb.active
            ws.append(HEADERS)
            ws.append(["Başvuru", "A", "Numeric", "Başvuru bazında beyan edilen gelir TRY.", "Hayır", "", ""])
            ws.append(["Başvuru", "B", "Numeric", "Başvuru bazında beyan edilen gelir TRY.", "Hayır", "", ""])
            ws.append(["Başvuru", "Ratio", "Numeric", "=IF(B3=0,-1,B2/B3)", "Hayır", "", ""])
            wb.save(root / "input.xlsx")
            wb.close()
            requests = read_requests(root / "input.xlsx")
            self.assertEqual(requests[2]["description_for_parse"], "A / B; payda sıfırsa -1.")
            outcome = analyze(root / "input.xlsx", root / "out.xlsx", strategy="bm25")
            ratio = outcome["results"][2]
            self.assertEqual(len(ratio["dependencies"]), 2)
            self.assertEqual(ratio["production_status"], "insufficient")
            self.assertIn("missing:null_policy", ratio["reasons"])
            self.assertIn("missing:reference_date", ratio["reasons"])


if __name__ == "__main__":
    unittest.main()
