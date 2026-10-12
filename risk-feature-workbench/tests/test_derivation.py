import unittest

import fixture_medallion as fx
from riskmatch import derivation as dv
from riskmatch.config import DEFAULTS, DEFAULT_SCHEMA_TIERS
from riskmatch.semantics import parse_definition

NAMING = DEFAULTS["naming"]
TIERS = [{"name": "DM_CAD", "schemas": ["DM_CAD"]}, {"name": "DM", "schemas": ["DM"]}, {"name": "EDW", "schemas": ["EDW"]}]
RATIO_NAME = "Giden_havale_adeti_LW_L3M"
RATIO_DESC = "Son bir haftadaki giden havale adedinin son 3 aydaki giden havale adedine oranı"


def cand(schema, obj, col, meaning="strong", status="supported", checks=(), reasons=(), diffs=()):
    return {"meaning": meaning, "status": status, "checks": list(checks), "reasons": list(reasons),
            "differences": [{"text": t} for t in diffs],
            "source": {"schema": schema, "object": obj, "column": col, "column_comment": "", "table_comment": ""}}


def ranked(candidates=(), rejected=(), status="found"):
    return {"candidates": list(candidates), "rejected_candidates": list(rejected), "row_status": status, "notes": []}


def req(name="Giden_EFT_tut_LM", description="Müşterinin son ayda giden toplam eft tutarı", **extra):
    out = {"name": name, "description": description, "definition": parse_definition(name, description, NAMING),
           "scope": {"source_database": "DB"}}
    out.update(extra)
    return out


class FakeRun:
    """Katman adı (ve bileşen rolü) -> (records, info, ranked)."""

    def __init__(self, table, scope="full"):
        self.table, self.scope, self.calls = table, scope, []

    def __call__(self, tier_req, tier):
        self.calls.append((tier_req.get("role"), tier["name"], tier_req["scope"]))
        key = (tier_req.get("role"), tier["name"])
        found = self.table.get(key, self.table.get(tier["name"], ranked(status="none_in_scope")))
        records = found.pop("_records", []) if isinstance(found, dict) else []
        return records, {"semantic_scope": self.table.get(("scope", tier["name"]), self.scope)}, found


class TextTests(unittest.TestCase):
    def test_labels_cover_verdicts(self):
        self.assertEqual(set(dv.VERDICTS), set(dv.VERDICT_LABELS))
        self.assertEqual(dv.VERDICT_LABELS["bulunamadi"], "Veritabanında bulunamadı")

    def test_reason_text_never_shows_raw_code(self):
        codes = ["unit_unverified", "window_name_mismatch", "unverified_dimension:window", "conflict_dimension:measure",
                 "weak_evidence", "candidate_no_description", "candidate_unverified", "closest_rejected",
                 "tier_not_selected:EDW", "catalog_incomplete:DM", "semantic_partial", "definition_issue:conflict:window",
                 "definition_issue:ambiguous_ratio_name", "operand_not_found:denominator", "operand_weak:numerator",
                 "operand_facts_incomplete:numerator", "cross_object_relationship_unverified", "derived_same_object",
                 "derived_verified_relationship", "bilinmeyen_kod"]
        for code in codes:
            text = dv.reason_text(code, name="X_LW", path="S.O.C", detail="fark")
            self.assertTrue(text.strip(), code)
            self.assertNotIn(code.split(":")[0], text, code)
            self.assertNotIn("_", text.replace("X_LW", ""), code)
        self.assertIn("Payda", dv.reason_text("operand_not_found:denominator", name="A_L3M"))
        self.assertIn("Dönem", dv.reason_text("unverified_dimension:window"))
        self.assertIn("EDW", dv.reason_text("tier_not_selected:EDW"))


class PlanTierTests(unittest.TestCase):
    def test_config_order_intersection_and_other(self):
        plan = dv.plan_tiers(["edw", "DM_CAD", "ZZ", "AA"], DEFAULT_SCHEMA_TIERS)
        self.assertEqual(plan["tiers"], [{"name": "DM_CAD", "schemas": ["DM_CAD"]}, {"name": "EDW", "schemas": ["edw"]},
                                         {"name": "DIGER", "schemas": ["AA", "ZZ"]}])
        self.assertEqual(plan["unselected"], ["DM"])

    def test_empty_config_is_single_tier(self):
        self.assertEqual(dv.plan_tiers(["B", "A"], []), {"tiers": [{"name": "TUMU", "schemas": ["B", "A"]}], "unselected": []})


class SearchTests(unittest.TestCase):
    def test_is_direct(self):
        self.assertTrue(dv.is_direct(cand("S", "O", "C", "likely")))
        self.assertFalse(dv.is_direct(cand("S", "O", "C", "weak")))
        self.assertFalse(dv.is_direct(cand("S", "O", "C", "strong", "insufficient")))

    def test_stops_at_first_direct_tier(self):
        run = FakeRun({"DM_CAD": ranked([cand("DM_CAD", "T", "C")]), "DM": ranked([cand("DM", "T", "C")])})
        out = dv.tiered_search(req(), TIERS, run)
        self.assertEqual(out["tier"], "DM_CAD")
        self.assertEqual(out["searched"], ["DM_CAD"])
        self.assertEqual(run.calls, [(None, "DM_CAD", {"source_database": "DB", "schemas": ["DM_CAD"]})])
        self.assertEqual(out["tier_trace"][0]["top"], "DM_CAD.T.C")
        self.assertEqual(out["tier_trace"][0]["meaning"], "strong")

    def test_falls_through_to_lower_tier_and_worst_scope(self):
        run = FakeRun({"DM_CAD": ranked([cand("DM_CAD", "T", "W", "weak")], status="weak_only"),
                       "DM": ranked([cand("DM", "T", "C")]), ("scope", "DM"): "partial"})
        out = dv.tiered_search(req(), TIERS, run)
        self.assertEqual((out["tier"], out["searched"], out["semantic_scope"]), ("DM", ["DM_CAD", "DM"], "partial"))

    def test_without_direct_returns_first_tier_with_candidates(self):
        run = FakeRun({"DM": ranked([cand("DM", "T", "W", "weak")], status="weak_only")})
        out = dv.tiered_search(req(), TIERS, run)
        self.assertEqual(out["tier"], "DM")
        self.assertEqual(out["searched"], ["DM_CAD", "DM", "EDW"])
        self.assertEqual(out["ranked"]["candidates"][0]["source"]["schema"], "DM")

    def test_nothing_found_merges_rejected_in_tier_order(self):
        rej = lambda s, n: [dict(cand(s, "T", "R%d" % i, "different", "conflict"), rejected_reason="x") for i in range(n)]
        run = FakeRun({"DM_CAD": ranked(rejected=rej("DM_CAD", 3), status="none_in_scope"),
                       "DM": ranked(rejected=rej("DM", 1), status="none_in_scope"),
                       "EDW": ranked(rejected=rej("EDW", 4), status="none_in_scope")})
        out = dv.tiered_search(req(), TIERS, run)
        self.assertIsNone(out["tier"])
        self.assertEqual([c["source"]["schema"] for c in out["ranked"]["rejected_candidates"]],
                         ["DM_CAD", "DM_CAD", "DM_CAD", "DM", "EDW"])
        self.assertEqual(out["semantic_scope"], "full")


def outcome_of(table, tiers=TIERS, scope="full", request=None):
    return dv.tiered_search(request or req(), tiers, FakeRun(table, scope))


class DecideTests(unittest.TestCase):
    def decide(self, outcome, request=None, **kw):
        args = {"unselected": [], "schema_complete": {}, "catalog_complete": True}
        args.update(kw)
        return dv.decide(request or req(), outcome, **args)

    def test_direct_carries_notes(self):
        notes = ["unit_unverified: talep birim belirtmiyor, kolon TRY", "window_name_mismatch: ad son 1 ay, yorum son 3 ay"]
        out = self.decide(outcome_of({"DM_CAD": ranked([cand("DM_CAD", "T", "C", reasons=notes)])}))
        self.assertEqual(out["verdict"], "dogrudan")
        self.assertEqual([r["code"] for r in out["verdict_reasons"]], ["unit_unverified", "window_name_mismatch"])
        self.assertIn("ad son 1 ay, yorum son 3 ay", out["verdict_reasons"][1]["text"])

    def test_derivation_verdict_is_used(self):
        derived = {"verdict": "turetilebilir", "verdict_reasons": [{"code": "derived_same_object", "text": "t"}]}
        out = self.decide(outcome_of({}), derivation=derived)
        self.assertEqual(out, {"verdict": "turetilebilir", "verdict_reasons": derived["verdict_reasons"]})

    def test_candidate_not_direct_is_ambiguous(self):
        checks = [{"dimension": "window", "status": "missing", "requested": "last_month", "actual": None},
                  {"dimension": "measure", "status": "compatible", "requested": "amount", "actual": "amount"}]
        rejected = [dict(cand("DM_CAD", "T", "R%d" % i, "different", "conflict", diffs=["Yön farklı"]), rejected_reason="x")
                    for i in range(4)]
        out = self.decide(outcome_of({"DM_CAD": ranked([cand("DM_CAD", "T", "W", "weak", "insufficient", checks)], rejected)}))
        self.assertEqual(out["verdict"], "belirsiz")
        codes = [r["code"] for r in out["verdict_reasons"]]
        self.assertEqual(codes, ["unverified_dimension:window", "weak_evidence"] + ["closest_rejected"] * 3)
        self.assertEqual(out["verdict_reasons"][0]["dimension"], "window")
        self.assertEqual(out["verdict_reasons"][2]["text"], "DM_CAD.T.R0: Yön farklı")

    def test_no_description_candidate(self):
        out = self.decide(outcome_of({"DM": ranked([cand("DM", "T", "C", "no_description", "insufficient")])}))
        self.assertEqual([r["code"] for r in out["verdict_reasons"]], ["candidate_no_description"])

    def test_not_found_only_with_complete_evidence(self):
        out = self.decide(outcome_of({}), schema_complete={"DM": True})
        self.assertEqual(out, {"verdict": "bulunamadi", "verdict_reasons": []})

    def test_cannot_decide_reasons(self):
        bad = req("Giden_EFT_tut_LM", "Son ayda giden eft tutarı, son 3 ayda")
        self.assertTrue(bad["definition"]["issues"])
        out = self.decide(outcome_of({}, scope="partial", request=bad), request=bad, unselected=["EDW"],
                          schema_complete={"DM_CAD": True, "DM": False}, catalog_complete=True)
        self.assertEqual(out["verdict"], "karar_verilemedi")
        codes = [r["code"] for r in out["verdict_reasons"]]
        self.assertEqual(codes[:3], ["tier_not_selected:EDW", "catalog_incomplete:DM", "semantic_partial"])
        self.assertTrue(codes[3].startswith("definition_issue:"))
        self.assertNotIn("conflict", out["verdict_reasons"][3]["text"])

    def test_schema_complete_falls_back_to_catalog_complete(self):
        out = self.decide(outcome_of({}), schema_complete={}, catalog_complete=False)
        self.assertEqual([r["code"] for r in out["verdict_reasons"]],
                         ["catalog_incomplete:DM_CAD", "catalog_incomplete:DM", "catalog_incomplete:EDW"])


def ratio_req(name=RATIO_NAME, description=RATIO_DESC):
    return req(name, description)


def capture(table):
    seen = []

    def search(operand):
        seen.append(operand)
        return outcome_of(table(operand["role"]) if callable(table) else table.get(operand["role"], {}), request=operand)
    return search, seen


class DeriveTests(unittest.TestCase):
    def test_none_without_formula(self):
        self.assertIsNone(dv.derive_ratio(req(), lambda r: self.fail("aranmamalı"), NAMING))

    def test_fixture_numerator_facts(self):
        row = next(r for r in fx.REQUESTS if r["name"] == RATIO_NAME)
        search, seen = capture({})
        dv.derive_ratio(ratio_req(row["name"], row["description"]), search, NAMING)
        facts = {role: {k: v["value"] for k, v in o["definition"]["facts"].items()} for role, o in
                 ((o["role"], o) for o in seen)}
        num = facts["numerator"]
        self.assertEqual({k: num.get(k) for k in ("direction", "transaction", "measure", "window")},
                         {"direction": "outgoing", "transaction": "havale", "measure": "count", "window": "last_week"})
        self.assertEqual(facts["denominator"]["window"], "last_3_months")
        for role in facts:
            self.assertNotIn("numerator", facts[role])
            self.assertNotIn("ratio", facts[role].values())
        self.assertEqual(seen[0]["definition"]["issues"], [])
        self.assertEqual(seen[0]["definition"]["facts"]["window"]["source"], "naming_rule")

    def test_same_object_is_derivable(self):
        search, _ = capture({"numerator": {"DM_CAD": ranked([cand("DM_CAD", "X", "N_W1W")])},
                             "denominator": {"DM_CAD": ranked([cand("DM_CAD", "x", "N_W3M")])}})
        out = dv.derive_ratio(ratio_req(), search, NAMING)
        self.assertEqual((out["verdict"], out["relationship"]), ("turetilebilir", "same_object"))
        self.assertEqual(out["formula"], "Giden_havale_adeti_LW / Giden_havale_adeti_L3M")
        self.assertEqual(out["components"][0]["source"], {"schema": "DM_CAD", "object": "X", "column": "N_W1W"})
        self.assertEqual(out["components"][0]["tier"], "DM_CAD")
        self.assertEqual(out["components"][0]["window"], "last_week")

    def test_cross_object_needs_verified_relationship(self):
        def table(rel):
            den = ranked([cand("DM_CAD", "Y", "D")])
            den["_records"] = [{"schema": "DM_CAD", "object": "Y", "column": "D", "verified_relationships": rel}]
            return {"numerator": {"DM_CAD": ranked([cand("DM_CAD", "X", "N")])}, "denominator": {"DM_CAD": den}}
        search, _ = capture(table([]))
        out = dv.derive_ratio(ratio_req(), search, NAMING)
        self.assertEqual((out["verdict"], out["relationship"]), ("belirsiz", "unverified"))
        self.assertEqual([r["code"] for r in out["verdict_reasons"]], ["cross_object_relationship_unverified"])
        search, _ = capture(table([{"to": {"schema": "dm_cad", "object": "x"}}]))
        out = dv.derive_ratio(ratio_req(), search, NAMING)
        self.assertEqual((out["verdict"], out["relationship"]), ("turetilebilir", "verified"))

    def test_missing_and_weak_operands(self):
        search, _ = capture({"numerator": {"DM": ranked([cand("DM", "X", "N", "weak", "insufficient")])}})
        out = dv.derive_ratio(ratio_req(), search, NAMING)
        self.assertEqual(out["verdict"], "belirsiz")
        self.assertIsNone(out["relationship"])
        self.assertEqual([c["reasons"] for c in out["components"]], [["operand_weak:numerator"], ["operand_not_found:denominator"]])
        self.assertEqual([c["verdict"] for c in out["components"]], ["belirsiz", "bulunamadi"])
        self.assertEqual([r["component"] for r in out["verdict_reasons"]], ["numerator", "denominator"])
        self.assertIn("Giden_havale_adeti_L3M", out["verdict_reasons"][1]["text"])

    def test_incomplete_operand_facts_are_not_searched(self):
        search, seen = capture({})
        out = dv.derive_ratio(ratio_req(description="Son bir haftanın son 3 aya oranı"), search, NAMING)
        self.assertEqual(seen, [])
        self.assertEqual([c["reasons"] for c in out["components"]],
                         [["operand_facts_incomplete:numerator"], ["operand_facts_incomplete:denominator"]])
        self.assertEqual(out["verdict"], "belirsiz")


class ResolveTests(unittest.TestCase):
    def resolve(self, run, request):
        return dv.resolve_row(request, TIERS, run, unselected=[], schema_complete={}, catalog_complete=True, naming=NAMING)

    def test_ready_ratio_column_is_direct(self):
        run = FakeRun({(None, "DM_CAD"): ranked([cand("DM_CAD", "X", "RATIO")])})
        out = self.resolve(run, ratio_req())
        self.assertEqual((out["verdict"], out["derivation"]), ("dogrudan", None))
        self.assertEqual(len(out["tier_trace"]), 1)

    def test_ratio_is_derived_from_components(self):
        run = FakeRun({("numerator", "DM_CAD"): ranked([cand("DM_CAD", "X", "N")]),
                       ("denominator", "DM"): ranked([cand("DM", "Z", "D")])})
        out = self.resolve(run, ratio_req())
        self.assertEqual(out["verdict"], "belirsiz")
        self.assertEqual(out["derivation"]["relationship"], "unverified")
        self.assertEqual([t.get("role") for t in out["tier_trace"]],
                         [None, None, None, "numerator", "denominator", "denominator"])
        self.assertEqual(out["outcome"]["tier"], None)


if __name__ == "__main__":
    unittest.main()
