"""Expert sidecar boundary and reachable production proposal acceptance."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from riskmatch.demo import _record, demo_snapshot
from riskmatch.errors import ConfigError
from riskmatch.excelio import HEADERS
from riskmatch.pipeline import analyze
from riskmatch.reviewed import apply_reviewed
from riskmatch.semantics import parse_definition


def fact(value):
    return {"value": value, "source": "verified_metadata", "evidence": "SYNTHETIC reviewed contract section 1"}


def reviewed(definition):
    out = copy.deepcopy(definition)
    out["covers_full_definition"] = True
    out["facts"] = {key: fact(value["value"]) for key, value in out["facts"].items()}
    return out


def payload(requests):
    return {"schema_version": 1, "review": {"reviewer": "Synthetic test reviewer", "reviewed_at": "2026-10-11", "reference": "synthetic_contract_v1"}, "requests": requests, "catalog": {}}


class ReviewedTests(unittest.TestCase):
    def apply(self, definition, supplied):
        req = {"row_id": "r", "definition": definition}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "reviewed.json"
            path.write_text(json.dumps(payload({"r": supplied})), encoding="utf-8")
            apply_reviewed(path, [req], {"records": []})
        return req["definition"]

    def test_source_facts_cannot_be_omitted(self):
        original = parse_definition("X_LW", "Müşterinin son haftada giden toplam EFT tutarı TRY.")
        supplied = {"covers_full_definition": True, "facts": {"concept": fact("transfer")}}
        updated = self.apply(original, supplied)
        for dimension in ("window", "transaction", "direction", "unit"):
            self.assertEqual(updated["facts"][dimension], original["facts"][dimension])

    def test_explicit_conflict_preserved(self):
        original = parse_definition("X_LW", "Müşterinin son haftada giden toplam EFT tutarı TRY.")
        supplied = reviewed(original)
        supplied["facts"]["direction"] = fact("incoming")
        self.assertIn("conflict:reviewed_definition:direction", self.apply(original, supplied)["issues"])

    def test_model_inference_cannot_be_relabeled_by_envelope(self):
        supplied = {"covers_full_definition": True, "facts": {"grain": {"value": "customer", "source": "model_inference", "evidence": "generated suggestion"}}}
        with self.assertRaises(ConfigError):
            self.apply(parse_definition("X", ""), supplied)

    def test_unknown_null_policy_can_be_reviewed(self):
        original = parse_definition("Ratio", "A / B; payda sıfırsa -1")
        supplied = reviewed(original)
        supplied["formula"]["null"] = "propagate"
        supplied["facts"]["null_policy"] = fact("propagate")
        updated = self.apply(original, supplied)
        self.assertEqual(updated["issues"], [])
        self.assertEqual(updated["formula"]["null"], "propagate")

    def test_explicit_zero_policy_cannot_be_overridden(self):
        original = parse_definition("Ratio", "A / B; payda sıfırsa -1")
        supplied = reviewed(original)
        supplied["formula"]["zero"] = 0
        supplied["facts"]["zero_policy"] = fact(0)
        self.assertTrue(any("conflict:" in issue for issue in self.apply(original, supplied)["issues"]))

    def test_formula_cannot_be_removed(self):
        original = parse_definition("Ratio", "A / B; payda sıfırsa -1")
        supplied = reviewed(original)
        supplied["formula"] = None
        try:
            updated = self.apply(original, supplied)
        except ConfigError:
            return
        self.assertEqual(updated["formula"], original["formula"])

    def pipeline_fixture(self, directory, missing_key=False):
        root = Path(directory)
        descriptions = ["Müşterinin son haftada giden toplam EFT tutarı TRY.", "Müşterinin son ayda giden toplam EFT tutarı TRY.", "MetricA / MetricB; payda sıfırsa -1; NULL ise NULL"]
        names = ["MetricA", "MetricB", "Ratio"]
        ids = ["a", "b", "r"]
        definitions = [parse_definition(name, text) for name, text in zip(names, descriptions)]
        for definition in definitions:
            definition["facts"].update({key: fact(value) for key, value in {"entity": "customer", "grain": "customer", "reference_date": "application_date"}.items()})
        for definition in definitions[:2]:
            definition["facts"].update({key: fact(value) for key, value in {"window_bounds": "rolling calendar exclusive-start inclusive-end", "business_scope": "posted retail", "null_policy": "exclude_missing_amounts"}.items()})
        wb = Workbook()
        ws = wb.active
        ws.append(HEADERS + ["Talep Kimliği"])
        for name, text, rid in zip(names, descriptions, ids):
            ws.append(["Başvuru-mevduat", name, "Numeric", text, "Hayır", "", "", rid])
        wb.save(root / "input.xlsx")
        wb.close()
        records = [_record(name, desc, "METRICS", definition, key_columns=["CUSTOMER_ID", "APPLICATION_DATE"]) for name, desc, definition in zip(names[:2], descriptions[:2], definitions[:2])]
        if missing_key:
            records[0]["key_columns"] = []
        snapshot = demo_snapshot()
        snapshot["records"] = records
        (root / "reviewed.json").write_text(json.dumps(payload({rid: reviewed(definition) for rid, definition in zip(ids, definitions)})), encoding="utf-8")
        (root / "config.json").write_text(json.dumps({"mode": "demo", "reviewed_definitions_path": "reviewed.json"}), encoding="utf-8")
        with patch("riskmatch.demo.demo_snapshot", return_value=snapshot):
            return analyze(root / "input.xlsx", root / "out.xlsx", config_path=root / "config.json", strategy="bm25")

    def test_production_candidate_reachable_through_excel_and_config(self):
        with TemporaryDirectory() as directory:
            outcome = self.pipeline_fixture(directory)
            a, b, ratio = outcome["results"]
            self.assertEqual(a["semantic_status"], "supported")
            self.assertEqual(b["semantic_status"], "supported")
            self.assertEqual(ratio["production_status"], "candidate")
            self.assertEqual(len(ratio["dependencies"]), 2)

    def test_missing_source_key_still_blocks_reviewed_production(self):
        with TemporaryDirectory() as directory:
            ratio = self.pipeline_fixture(directory, missing_key=True)["results"][2]
            self.assertEqual(ratio["production_status"], "insufficient")
            self.assertIn("operand_keys_unknown:a", ratio["reasons"])


if __name__ == "__main__":
    unittest.main()
