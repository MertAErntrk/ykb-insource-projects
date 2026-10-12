import random
import unittest

from riskmatch.errors import ConfigError
from riskmatch.ranking import (GATE_REASON, LIMIT_REASON, NOTE_NO_SEMANTIC, NOTE_PARTIAL_SCOPE, SUBJECT_REASON,
                               WEAK_REASON, rank)


def verified(**values):
    return {"facts": {k: {"value": v, "source": "verified_metadata", "evidence": "synthetic reviewed contract: " + k} for k, v in values.items()}, "issues": [], "formula": None}


CONTRACT = dict(entity="customer", grain="customer", concept="transfer", measure="distinct_people", window="last_week")


def request(name="EFT_KISI_LW", data_type="", definition=None):
    result = {"row_id": "r1", "name": name, "description": "", "data_type": data_type}
    if definition is not None:
        result["definition"] = definition
    return result


def item(cid, z=None, coverage=0.0, level="none", matched=0, column=None, comment=None, schema="RISK", obj="FEATURES", data_type="", definition=None,
         subject=None, missing=None):
    result = {"catalog_id": cid, "schema": schema, "object": obj, "column": column or "COL_" + cid,
              "data_type": data_type, "column_comment": "Sentetik açıklama " + cid if comment is None else comment,
              "table_comment": "", "_score": 0.0, "_retrieval": ["lexical"],
              "_features": {"cos": None if z is None else 0.5, "z": z, "coverage": coverage, "name_level": level,
                            "channels": ["lexical"], "lexical_rank": 1, "semantic_rank": None,
                            "distinctive_matched": matched, "doc_hash": "h" + cid,
                            "subject_coverage": subject, "subject_missing": missing or []}}
    if definition is not None:
        result["definition"] = definition
    return result


def ids(items):
    return [c["catalog_id"] for c in items]


class RankingTests(unittest.TestCase):
    def test_exact_name_conflict_is_shown_as_different(self):
        pool = [item("c1", z=0.5, coverage=0.2, level="exact", column="EFT_KISI_LW", data_type="VARCHAR2")]
        result = rank(request(data_type="NUMBER"), pool, semantic_scope="full")
        self.assertEqual(ids(result["candidates"]), ["c1"])
        candidate = result["candidates"][0]
        self.assertEqual(candidate["meaning"], "different")
        self.assertTrue(candidate["differences"])
        self.assertIn("Kolon adı birebir aynı", candidate["evidence"])
        self.assertEqual(result["row_status"], "found")
        self.assertEqual(result["rejected_candidates"], [])

    def test_conflict_without_exact_name_is_rejected_with_differences(self):
        pool = [item("c1", z=5.0, coverage=1.0, data_type="VARCHAR2")]
        result = rank(request(data_type="NUMBER"), pool, semantic_scope="full")
        self.assertEqual(result["candidates"], [])
        self.assertIn("Alan tipi farklı", result["rejected_candidates"][0]["rejected_reason"])

    def test_unmatched_row_shows_nothing(self):
        pool = [item("c%d" % i, z=1.0 + i / 10, coverage=0.2, level="partial") for i in range(6)]
        partial = rank(request(), pool, semantic_scope="partial")
        self.assertEqual(partial["candidates"], [])
        self.assertEqual(partial["row_status"], "none_partial")
        self.assertIn(NOTE_PARTIAL_SCOPE, partial["notes"])
        full = rank(request(), pool, semantic_scope="full", catalog_complete=True)
        self.assertEqual(full["row_status"], "none_in_scope")
        self.assertEqual(full["notes"], [])
        incomplete = rank(request(), pool, semantic_scope="full", catalog_complete=False)
        self.assertEqual(incomplete["row_status"], "none_partial")

    def test_lexical_only_gate(self):
        shown = rank(request(), [item("c1", coverage=1.0, matched=2)])
        self.assertEqual(ids(shown["candidates"]), ["c1"])
        self.assertEqual(shown["candidates"][0]["meaning"], "weak")
        self.assertEqual(shown["row_status"], "weak_only")
        self.assertIn(NOTE_NO_SEMANTIC, shown["notes"])
        hidden = rank(request(), [item("c1", coverage=0.5, matched=2)])
        self.assertEqual(hidden["candidates"], [])
        self.assertEqual(hidden["rejected_candidates"][0]["rejected_reason"], GATE_REASON)
        few_tokens = rank(request(), [item("c1", coverage=1.0, matched=1)])
        self.assertEqual(few_tokens["candidates"], [])

    def test_lexical_only_supported_candidate_follows_meaning_rule(self):
        # Decision: a lexical-only candidate rises above "weak" only when its evaluation is supported.
        pool = [item("c1", coverage=1.0, matched=2, definition=verified(**CONTRACT))]
        result = rank(request(definition=verified(**CONTRACT)), pool)
        self.assertEqual(result["candidates"][0]["status"], "supported")
        self.assertEqual(result["candidates"][0]["meaning"], "strong")
        self.assertEqual(result["row_status"], "found")

    def test_duplicates_collapse_with_locations(self):
        same = [item("c3", z=5.0, coverage=1.0, column="EFT_SAYI", comment="EFT sayısı", schema="RISK", obj="T3"),
                item("c1", z=5.0, coverage=1.0, column="eft_sayi", comment="eft SAYISI", schema="ARCH", obj="T1"),
                item("c2", z=5.0, coverage=1.0, column="EFT_SAYI", comment="EFT sayısı", schema="RISK", obj="T2")]
        result = rank(request(), same, semantic_scope="full")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["catalog_id"], "c1")
        self.assertEqual(result["candidates"][0]["locations"], ["RISK.T2", "RISK.T3"])

    def test_at_most_three_shown_and_margin_cut(self):
        pool = [item("c%d" % i, z=5.0 + i / 10, coverage=1.0) for i in range(5)]
        pool.append(item("w1", z=2.6, coverage=0.6))
        result = rank(request(), pool, semantic_scope="full")
        self.assertEqual(ids(result["candidates"]), ["c4", "c3", "c2"])
        self.assertTrue(all(c["meaning"] == "likely" for c in result["candidates"]))
        reasons = {c["catalog_id"]: c["rejected_reason"] for c in result["rejected_candidates"]}
        self.assertEqual(reasons["w1"], WEAK_REASON)
        self.assertEqual(reasons["c1"], LIMIT_REASON)
        self.assertEqual(reasons["c0"], LIMIT_REASON)
        self.assertEqual(result["row_status"], "found")
        evidence = result["candidates"][0]["evidence"]
        self.assertIn("Anlamsal benzerlik arka plana göre yüksek (z=5,4)", evidence)
        self.assertFalse(any("%" in text for text in evidence))

    def test_exact_name_is_exempt_from_margin(self):
        pool = [item("c1", z=9.0, coverage=1.0), item("e1", z=0.1, coverage=0.0, level="exact", column="EFT_KISI_LW")]
        result = rank(request(), pool, semantic_scope="full")
        self.assertEqual(sorted(ids(result["candidates"])), ["c1", "e1"])

    def test_order_independent(self):
        pool = [item("c%d" % i, z=1.0 + i * 0.7, coverage=(i % 4) / 3, level=("none", "partial", "normalized")[i % 3], matched=i % 3)
                for i in range(12)]
        pool.append(item("x1", z=6.0, coverage=1.0, data_type="VARCHAR2"))
        pool.append(item("d1", z=4.5, coverage=0.9, column="DUP", comment="Aynı", schema="B"))
        pool.append(item("d2", z=4.5, coverage=0.9, column="DUP", comment="Aynı", schema="A"))
        expected = rank(request(data_type="NUMBER"), pool, semantic_scope="full")
        rng = random.Random(7)
        for _ in range(5):
            shuffled = list(pool)
            rng.shuffle(shuffled)
            self.assertEqual(rank(request(data_type="NUMBER"), shuffled, semantic_scope="full"), expected)

    def test_rejected_limited_conflicts_first(self):
        pool = [item("b%d" % i, z=1.0 + i / 10, coverage=0.1) for i in range(6)]
        pool += [item("x%d" % i, z=0.1, coverage=0.0, data_type="VARCHAR2") for i in range(2)]
        result = rank(request(data_type="NUMBER"), pool, semantic_scope="full")
        rejected = result["rejected_candidates"]
        self.assertEqual(len(rejected), 5)
        self.assertTrue(all(c["rejected_reason"] for c in rejected))
        self.assertEqual(ids(rejected[:2]), ["x0", "x1"])
        self.assertEqual(ids(rejected[2:]), ["b5", "b4", "b3"])

    def test_candidate_contract(self):
        result = rank(request(), [item("c1", z=4.2, coverage=0.75, level="normalized", matched=2)], semantic_scope="full")
        candidate = result["candidates"][0]
        for key in ("catalog_id", "source", "locations", "name_match", "meaning", "differences", "evidence", "status",
                    "checks", "reasons", "features", "score", "retrieval", "path"):
            self.assertIn(key, candidate)
        self.assertEqual(set(candidate["features"]), {"z", "cos", "coverage", "channels"})
        self.assertAlmostEqual(candidate["score"], 4.2 + 1.5 * 0.75 + 2.0)
        self.assertIn("Ad kısaltma açılımıyla aynı", candidate["evidence"])

    def test_no_description_meaning(self):
        result = rank(request(), [item("c1", z=5.0, coverage=1.0, comment="")], semantic_scope="full")
        self.assertEqual(result["candidates"][0]["meaning"], "no_description")
        self.assertEqual(result["row_status"], "weak_only")

    def test_structural_only_overlap_is_rejected(self):
        # Measured e5 cases: coverage filled by window/measure/direction tokens, subject word absent.
        kripto = [item("atm", z=3.04, coverage=0.5, level="partial", column="ATM_ORT_TUT_3AY",
                       comment="Son 3 ay ortalama ATM tutarı", subject=0.0, missing=["alim", "kripto"]),
                  item("pos", z=2.7, coverage=0.5, column="POS_TOP_TUT_3AY", subject=0.0, missing=["alim", "kripto"])]
        result = rank(request("KRIPTO_ALIM_TUT_3AY"), kripto, semantic_scope="full", catalog_complete=True)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["row_status"], "none_in_scope")
        self.assertEqual({c["rejected_reason"] for c in result["rejected_candidates"]}, {SUBJECT_REASON})
        evidence = result["rejected_candidates"][0]["evidence"]
        self.assertIn("Konu kelimesi farklı: talep alim, kripto içeriyor, aday içermiyor", evidence)
        self.assertNotEqual(result["rejected_candidates"][0]["meaning"], "likely")
        havale = [item("tah", z=2.65, coverage=0.75, column="TAH_GDN_ADET_6AY", subject=0.0, missing=["havale"]),
                  item("dov", z=2.6, coverage=0.75, column="DOV_GDN_ADET_6AY", subject=0.0, missing=["havale"]),
                  item("hvl", z=3.1, coverage=1.0, column="HAVALE_GDN_ADET_6AY",
                       comment="son 6 ay giden havale adedi", subject=1.0)]
        result = rank(request("GIDEN_HAVALE_ADET_6AY"), havale, semantic_scope="full")
        self.assertEqual(ids(result["candidates"]), ["hvl"])
        self.assertEqual(result["candidates"][0]["meaning"], "likely")
        self.assertFalse(any("Konu kelimesi" in e for e in result["candidates"][0]["evidence"]))
        self.assertEqual(sorted(ids(result["rejected_candidates"])), ["dov", "tah"])
        lexical = rank(request(), [item("c1", coverage=1.0, matched=2, subject=0.0)])
        self.assertEqual(lexical["rejected_candidates"][0]["rejected_reason"], SUBJECT_REASON)

    def test_subject_rule_exemptions(self):
        strong = rank(request(), [item("c1", z=4.5, coverage=0.4, subject=0.0)], semantic_scope="full")
        self.assertEqual(ids(strong["candidates"]), ["c1"])  # z >= z_strong: synonym outside the lexicon
        named = rank(request(), [item("c1", z=1.0, coverage=0.4, level="normalized", subject=0.0)], semantic_scope="full")
        self.assertEqual(ids(named["candidates"]), ["c1"])
        unknown = rank(request(), [item("c1", z=3.0, coverage=0.6, subject=None)], semantic_scope="full")
        self.assertEqual(ids(unknown["candidates"]), ["c1"])  # no subject word in the request

    def test_require_subject_off_keeps_old_behaviour(self):
        pool = [item("tah", z=2.65, coverage=0.75, subject=0.0, missing=["havale"])]
        result = rank(request(), pool, gate={"require_subject": False}, semantic_scope="full")
        self.assertEqual(ids(result["candidates"]), ["tah"])
        self.assertEqual(result["candidates"][0]["meaning"], "likely")
        self.assertFalse(any("Konu kelimesi" in e for e in result["candidates"][0]["evidence"]))
        for bad in (1, 0, "yes", None):
            with self.assertRaises(ConfigError):
                rank(request(), [], gate={"require_subject": bad})

    def test_gate_validation(self):
        self.assertEqual(rank(request(), [], gate={"z_min": 3.0})["row_status"], "none_partial")
        for gate in ({"unknown": 1}, {"z_min": "2"}, {"c_min": 1.5}, {"max_shown": 0}, {"max_shown": 2.5},
                     {"max_rejected": -1}, {"z_min": 5.0}, {"c_low": 0.9}, {"margin": True}, "bad"):
            with self.assertRaises(ConfigError):
                rank(request(), [], gate=gate)
        with self.assertRaises(ConfigError):
            rank(request(), [], semantic_scope="everything")


class QualityFixTests(unittest.TestCase):
    def test_lower_meaning_class_never_cuts_likely(self):
        pool = [item("a", z=9.0, coverage=1.0, comment="", subject=1.0),
                item("b", z=6.0, coverage=1.0, subject=1.0)]
        result = rank(request(), pool, semantic_scope="full", catalog_complete=True)
        meanings = {c["catalog_id"]: c["meaning"] for c in result["candidates"]}
        self.assertEqual(meanings.get("b"), "likely")
        self.assertEqual(result["row_status"], "found")

    def test_exact_name_without_comment_leads_likely(self):
        # Measured miss: blank-comment exact column fell behind "likely" candidates.
        pool = [item("c1", z=3.0, coverage=0.6, subject=1.0),
                item("e1", z=0.1, coverage=0.0, level="exact", column="EFT_KISI_LW", comment="")]
        result = rank(request(), pool, semantic_scope="full")
        self.assertEqual([c["meaning"] for c in result["candidates"]], ["no_description", "likely"])
        self.assertEqual(ids(result["candidates"]), ["e1", "c1"])

    def test_exact_name_different_meaning_keeps_place(self):
        pool = [item("c1", z=3.0, coverage=0.6, subject=1.0),
                item("x1", z=0.1, coverage=0.0, level="exact", column="EFT_KISI_LW", data_type="VARCHAR2")]
        result = rank(request(data_type="NUMBER"), pool, semantic_scope="full")
        self.assertEqual(ids(result["candidates"]), ["c1", "x1"])
        self.assertEqual(result["candidates"][1]["meaning"], "different")

    def test_single_token_request_passes_lexical_rule_as_weak(self):
        single = item("c1", coverage=1.0, matched=1)
        self.assertEqual(rank(request(), [single], semantic_scope="none")["candidates"], [])
        single["_features"]["query_tokens"] = 1
        result = rank(request(), [single], semantic_scope="none")
        self.assertEqual(ids(result["candidates"]), ["c1"])
        self.assertEqual(result["candidates"][0]["meaning"], "weak")

    def test_locations_come_from_retrieval_group(self):
        record = item("c1", z=5.0, coverage=1.0, obj="T0")
        record["_locations"] = ["RISK.T2", "RISK.T1"]
        candidate = rank(request(), [record], semantic_scope="full")["candidates"][0]
        self.assertEqual(candidate["locations"], ["RISK.T1", "RISK.T2"])

    def test_small_background_adds_note(self):
        record = item("c1", z=5.0, coverage=1.0)
        record["_features"]["background_small"] = True
        notes = rank(request(), [record], semantic_scope="full")["notes"]
        self.assertIn("Katalog küçük; anlamsal benzerlik ölçeği sınırlı.", notes)
        self.assertEqual(rank(request(), [item("c1", z=5.0, coverage=1.0)], semantic_scope="full")["notes"], [])



class TableEvidenceTests(unittest.TestCase):
    def test_table_rank_evidence_and_bonus_config(self):
        from riskmatch import ranking
        config = ranking.gate_config()
        self.assertEqual(config["table_bonus"], 0.0)
        self.assertEqual(ranking.gate_config({"table_bonus": 0.5})["table_bonus"], 0.5)
        with self.assertRaises(ConfigError):
            ranking.gate_config({"table_bonus": -1})
        request = {"name": "Kart gecikme", "description": ""}
        for table_rank, expected in ((2, True), (4, False), (None, False)):
            record = {"column": "C_017", "column_comment": "",
                      "_features": {"name_level": "none", "table_rank": table_rank}}
            evidence = ranking._evidence(request, record, ranking._features(record), config)
            self.assertEqual("Tablo açıklaması talep konusuyla uyumlu (tablo sırası 2)" in evidence, expected)


class FactSupportedGateTests(unittest.TestCase):
    """z eşiğin altında kalsa da talebin anlam boyutları kolon açıklamasıyla doğrulanan aday gösterilir."""

    def test_fully_supported_candidate_below_z_min_is_shown(self):
        req = request(definition=verified(**CONTRACT))
        pool = [item("a", z=2.2, coverage=0.75, definition=verified(**CONTRACT))]
        out = rank(req, pool, semantic_scope="full", catalog_complete=True)
        self.assertEqual(ids(out["candidates"]), ["a"])
        self.assertEqual(out["candidates"][0]["meaning"], "likely")
        off = rank(req, pool, gate={"fact_min": 0}, semantic_scope="full", catalog_complete=True)
        self.assertEqual(off["candidates"], [])
        with self.assertRaises(ConfigError):
            rank(req, pool, gate={"fact_min": -1})

    def test_partial_or_conflicting_facts_stay_below_gate(self):
        req = request(definition=verified(**CONTRACT))
        partial = verified(entity="customer", concept="transfer", window="last_week")
        conflict = verified(**dict(CONTRACT, window="last_month"))
        for definition in (partial, conflict):
            out = rank(req, [item("b", z=2.2, coverage=0.75, definition=definition)],
                       semantic_scope="full", catalog_complete=True)
            self.assertEqual(out["candidates"], [])

    def test_ratio_with_other_base_is_not_supported_by_name_rule_alone(self):
        from riskmatch.derivation import is_direct
        from riskmatch.semantics import evaluate_candidate, parse_definition
        naming = {"windows": {"LW": "last_week", "L3M": "last_3_months"}, "ratio_rule": "suffix_pair"}
        record = {"catalog_id": "a", "schema": "DM", "object": "T", "column": "KREDI_LIMIT_LW_L3M",
                  "column_comment": "Kredi limit oranı", "data_type": "NUMBER"}
        for description in ("", "Son hafta / son 3 ay oranı"):
            req = {"row_id": 1, "name": "GIDEN_EFT_ADET_LW_L3M", "description": description,
                   "definition": parse_definition("GIDEN_EFT_ADET_LW_L3M", description, naming)}
            checks = {c["dimension"]: c["status"] for c in evaluate_candidate(req, record)["checks"]}
            self.assertEqual((checks["numerator"], checks["denominator"]), ("missing", "missing"))
            for features in ({"z": 1.0, "coverage": 0.6}, {"z": 3.0, "coverage": 0.6}):
                out = rank(req, [dict(record, _features=features)], semantic_scope="full", catalog_complete=True)
                self.assertFalse(any(is_direct(c) for c in out["candidates"]))


if __name__ == "__main__":
    unittest.main()
