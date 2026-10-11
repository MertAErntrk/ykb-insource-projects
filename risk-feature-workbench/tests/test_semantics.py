import unittest

from riskmatch.semantics import (
    assess_production, build_dependencies, evaluate_candidate, normalize, parse_definition,
)


def verified(**values):
    return {"facts": {k: {"value": v, "source": "verified_metadata", "evidence": "synthetic reviewed contract: " + k} for k, v in values.items()}, "issues": [], "formula": None}


def complete(window="last_week"):
    return verified(entity="customer", grain="customer", concept="transfer", transaction="eft", direction="outgoing", measure="distinct_people", aggregation="count_distinct", window=window, window_bounds="rolling calendar; exclusive start inclusive end", reference_date="application_date", person_key="national_person_id", business_scope="posted retail only", null_policy="exclude_null_person_keys")


def request(name="EFT_LW", description="", definition=None, row_id="r1"):
    result = {"row_id": row_id, "name": name, "description": description}
    if definition is not None:
        result["definition"] = definition
    return result


def record(definition=None, column="OTHER_NAME", cid="c1"):
    result = {"catalog_id": cid, "column": column, "column_comment": "", "source_database": "synthetic", "environment": "test", "container": "DEMO", "schema": "RISK", "object": "FEATURES", "key_columns": ["CUSTOMER_ID", "APPLICATION_DATE"]}
    if definition is not None:
        result["definition"] = definition
    return result


class DefinitionTests(unittest.TestCase):
    def test_turkish_html(self):
        self.assertEqual(normalize("İŞLEM<br> GİDEN&nbsp;TUTAR"), "islem giden tutar")
        parsed = parse_definition("X_LW", "Müşterinin son haftada<br>kaç farklı kişi ile EFT ilişkisine girdiği.")
        self.assertEqual(parsed["facts"]["measure"]["value"], "distinct_people")
        self.assertEqual(parsed["facts"]["window"]["value"], "last_week")
        self.assertNotIn("direction", parsed["facts"])
        self.assertNotIn("person_key", parsed["facts"])
        self.assertNotIn("window_bounds", parsed["facts"])

    def test_only_confirmed_name_window(self):
        parsed = parse_definition("Giden_EFT_tut_L3M", "")
        self.assertEqual(set(parsed["facts"]), {"window"})
        self.assertEqual(parsed["facts"]["window"]["source"], "naming_rule")

    def test_configurable_windows(self):
        self.assertEqual(parse_definition("X_LQ", "", {"windows": {"LQ": "last_quarter"}})["facts"]["window"]["value"], "last_quarter")

    def test_description_name_conflict(self):
        self.assertIn("conflict:window", parse_definition("EFT_LW", "Son ayda toplam EFT tutarı")["issues"])

    def test_zero_does_not_imply_null(self):
        parsed = parse_definition("Havale_LW_LM", "Havale_LW / Havale_LM; payda sıfırsa -1.")
        self.assertEqual(parsed["formula"]["zero"], -1)
        self.assertIsNone(parsed["formula"]["null"])
        self.assertNotIn("null_policy", parsed["facts"])

    def test_null_separate_policy(self):
        parsed = parse_definition("ratio", "A / B; payda sıfırsa -1; NULL ise NULL")
        self.assertEqual(parsed["formula"]["null"], "propagate")

    def test_reversed_ratio_name_is_conflict(self):
        parsed = parse_definition("EFT_LW_L3M", "EFT_L3M / EFT_LW; payda sıfırsa -1")
        self.assertIn("conflict:ratio_direction", parsed["issues"])

    def test_arbitrary_ratio_not_inferred(self):
        parsed = parse_definition("Whatever_LW_L3M", "")
        self.assertIsNone(parsed["formula"])
        self.assertIn("ambiguous_ratio_name", parsed["issues"])

    def test_exact_confirmed_example_ratio(self):
        parsed = parse_definition("Giden_max_havale_adeti_LW_L3M", "")
        self.assertEqual(parsed["formula"]["numerator"], "Giden_max_havale_adeti_LW")
        self.assertEqual(parsed["formula"]["denominator"], "Giden_max_havale_adeti_L3M")
        self.assertIsNone(parsed["formula"]["zero"])

    def test_formula_is_restricted(self):
        for text in ("__import__('os').system('x') / B", "A / B + C", "=A/B", "A / B; __import__('os')"):
            with self.subTest(text=text):
                parsed = parse_definition("ratio", text)
                self.assertIsNone(parsed["formula"])
                self.assertIn("unsupported_formula", parsed["issues"])

    def test_unparsed_ratio_scope_blocks_evidence(self):
        parsed = parse_definition("ratio", "A / B; payda sıfırsa -1; yalnız onaylanan müşteriler")
        self.assertIn("unparsed_formula_qualifier", parsed["issues"])

    def test_interest_type_is_not_rate(self):
        types = parse_definition("Faiz", "1 sabit faiz, 2 değişken faiz, 8 diğer, 9 bilinmiyor")
        rate = parse_definition("Faiz", "Faiz oranı yüzde")
        self.assertEqual(types["facts"]["concept"]["value"], "interest_type")
        self.assertEqual(rate["facts"]["concept"]["value"], "interest_rate")
        self.assertEqual(types["facts"]["codes"]["value"]["9"], "bilinmiyor")

    def test_declared_requested_distinctions(self):
        for text, value in (("Başvuruda beyan edilen gelir", "declared"), ("Doğrulanmış gelir", "verified"), ("Başvurulan limit", "requested"), ("Onaylanan limit", "approved")):
            self.assertEqual(parse_definition("x", text)["facts"]["business_scope"]["value"], value)

    def test_html_multiline_codes(self):
        parsed = parse_definition("Kanal", "1 şube<br>2 mobil\n3 web")
        self.assertEqual(parsed["facts"]["codes"]["value"], {"1": "sube", "2": "mobil", "3": "web"})

    def test_enum_first_code_after_sentence_preserved(self):
        parsed = parse_definition("Faiz", "Başvuru bazında faiz türü. 1 sabit faiz, 2 değişken faiz, 9 bilinmiyor.")
        self.assertEqual(parsed["facts"]["codes"]["value"]["1"], "sabit faiz")
        self.assertEqual(parsed["issues"], [])


class EvaluationTests(unittest.TestCase):
    def test_income_period_is_semantic_difference(self):
        req = request("income", "Başvuru bazında beyan edilen aylık gelir TRY.")
        rec = record(column="income")
        rec["column_comment"] = "Başvuru bazında beyan edilen yıllık gelir TRY."
        outcome = evaluate_candidate(req, rec)
        self.assertEqual(outcome["status"], "conflict")
        self.assertIn("conflict:unit", outcome["reasons"])

    def test_unhandled_qualifiers_never_supported(self):
        for qualifier in ("Yalnız kredi kartı başvuruları", "Türkiye müşterileri", "net gelir", "vergiler düşüldükten sonra", "2025 yılı itibarıyla", "iptal edilmiş başvurular", "kurumsal müşteri hariç"):
            req = request("income", "Başvuru bazında beyan edilen gelir TRY. " + qualifier)
            rec = record(column="income")
            rec["column_comment"] = "Başvuru bazında beyan edilen gelir TRY."
            with self.subTest(qualifier=qualifier):
                self.assertNotEqual(evaluate_candidate(req, rec)["status"], "supported")

    def test_equal_unknown_qualifiers_still_need_review(self):
        text = "Başvuru bazında beyan edilen gelir TRY. Bölge grubuna göre."
        rec = record(column="income")
        rec["column_comment"] = text
        self.assertEqual(evaluate_candidate(request("income", text), rec)["status"], "insufficient")

    def test_supported_different_name_requires_all_evidence(self):
        self.assertEqual(evaluate_candidate(request(definition=complete()), record(complete()))["status"], "supported")

    def test_same_name_wrong_meaning_conflicts(self):
        actual = complete()
        actual["facts"]["measure"]["value"] = "transaction_count"
        out = evaluate_candidate(request(definition=complete()), record(actual, "EFT_LW"))
        self.assertEqual(out["status"], "conflict")
        self.assertIn("conflict:measure", out["reasons"])

    def test_all_critical_differences(self):
        for dim, val in (("measure", "distinct_accounts"), ("measure", "amount"), ("aggregation", "sum"), ("aggregation", "max"), ("direction", "incoming"), ("window", "last_month"), ("grain", "account"), ("reference_date", "execution_date"), ("person_key", "account_holder_id"), ("business_scope", "pending included")):
            with self.subTest(dim=dim, value=val):
                actual = complete()
                actual["facts"][dim]["value"] = val
                self.assertEqual(evaluate_candidate(request(definition=complete()), record(actual))["status"], "conflict")

    def test_aggregation_order_matters(self):
        wanted, actual = complete(), complete()
        wanted["facts"]["aggregation"]["value"] = ["sum", "max"]
        actual["facts"]["aggregation"]["value"] = ["max", "sum"]
        self.assertEqual(evaluate_candidate(request(definition=wanted), record(actual))["status"], "conflict")

    def test_critical_unknown_never_supported(self):
        for dimension in ("grain", "reference_date", "person_key", "window_bounds", "direction", "null_policy"):
            with self.subTest(dimension=dimension):
                incomplete = complete()
                incomplete["facts"].pop(dimension)
                self.assertEqual(evaluate_candidate(request(definition=incomplete), record(incomplete))["status"], "insufficient")

    def test_model_inference_never_evidence(self):
        actual = complete()
        actual["facts"]["grain"]["source"] = "model_inference"
        self.assertEqual(evaluate_candidate(request(definition=complete()), record(actual))["status"], "insufficient")

    def test_empty_verified_values_do_not_equal_known_facts(self):
        for value in ("", "   ", [], {}, False, "unknown", "belirsiz", 0, ["customer"]):
            with self.subTest(value=value):
                definition = verified(entity=value, grain=value, concept=value, measure=value)
                self.assertNotEqual(evaluate_candidate(request(definition=definition), record(definition))["status"], "supported")

    def test_numeric_zero_policy_is_valid(self):
        definition = verified(entity="customer", grain="customer", concept="ratio", measure="ratio", numerator="A", denominator="B", zero_policy=0, null_policy="propagate", reference_date="application_date")
        self.assertEqual(evaluate_candidate(request(definition=definition), record(definition))["status"], "supported")

    def test_blank_comments_name_not_proof(self):
        self.assertEqual(evaluate_candidate(request("EFT_LW"), record(column="EFT_LW"))["status"], "insufficient")

    def test_same_ambiguous_text_insufficient(self):
        desc = "Müşterinin son haftada kaç farklı kişi ile EFT ilişkisine girdiği."
        rec = record(column="EFT_LW")
        rec["column_comment"] = desc
        self.assertEqual(evaluate_candidate(request("EFT_LW", desc), rec)["status"], "insufficient")

    def test_ratio_reverse_and_null(self):
        a = parse_definition("ratio", "A / B; payda sıfırsa -1; NULL ise NULL")
        b = parse_definition("ratio", "B / A; payda sıfırsa -1; NULL ise -1")
        out = evaluate_candidate(request(definition=a), record(b))
        self.assertEqual(out["status"], "conflict")
        self.assertIn("conflict:numerator", out["reasons"])
        self.assertIn("conflict:null_policy", out["reasons"])


class DependencyTests(unittest.TestCase):
    def test_missing_havale_l3m(self):
        items = [request("Havale_LW", row_id="a"), request("Havale_LW_L3M", "Havale_LW / Havale_L3M; payda sıfırsa -1", row_id="r")]
        graph = build_dependencies(items)
        self.assertIn("missing_definition:Havale_L3M", graph["issues"]["r"])

    def test_entire_workbook_including_later_sheets(self):
        items = [request("ratio", "A / B", row_id="r"), request("A", row_id="a"), request("B", row_id="b")]
        items[2]["sheet"] = "Last sheet"
        self.assertEqual(len(build_dependencies(items)["edges"]), 2)

    def test_duplicate_reference_not_arbitrary_first(self):
        graph = build_dependencies([request("ratio", "A / B", row_id="r"), request("A", row_id="a"), request("A", row_id="a2"), request("B", row_id="b")])
        self.assertIn("ambiguous_reference:A", graph["issues"]["r"])
        self.assertIn("duplicate_name", graph["issues"]["a2"])
        self.assertEqual(len(graph["edges"]), 1)

    def test_cycle_and_self_cycle(self):
        items = [request("A", "B / C", row_id="a"), request("B", "A / C", row_id="b"), request("C", row_id="c"), request("D", "D / C", row_id="d")]
        graph = build_dependencies(items)
        for rid in ("a", "b", "d"):
            self.assertIn("cycle", graph["issues"][rid])
        self.assertNotIn("cycle", graph["issues"]["c"])

    def test_all_strongly_connected_members_flagged(self):
        items = [request("A", "B / C", row_id="a"), request("B", "A / D", row_id="b"), request("C", "B / D", row_id="c"), request("D", row_id="d")]
        graph = build_dependencies(items)
        for rid in ("a", "b", "c"):
            self.assertIn("cycle", graph["issues"][rid])
        self.assertNotIn("cycle", graph["issues"]["d"])

    def test_large_graph_without_recursion_limit(self):
        items = [request("leaf", row_id="leaf")]
        for i in range(1200):
            previous = "leaf" if i == 0 else "N" + str(i - 1)
            items.append(request("N" + str(i), previous + " / leaf", row_id=str(i)))
        self.assertEqual(len(build_dependencies(items)["edges"]), 2400)


class ProductionTests(unittest.TestCase):
    def fixture(self):
        formula = parse_definition("ratio", "A / B; payda sıfırsa -1; NULL ise NULL")
        formula["facts"].update(verified(entity="customer", grain="customer", reference_date="application_date")["facts"])
        items = [request("ratio", definition=formula, row_id="r"), request("A", definition=complete(), row_id="a"), request("B", definition=complete("last_month"), row_id="b")]
        records = [record(complete(), "A", "ca"), record(complete("last_month"), "B", "cb")]
        results = {"a": {"semantic_status": "supported", "candidates": [evaluate_candidate(items[1], records[0])]}, "b": {"semantic_status": "supported", "candidates": [evaluate_candidate(items[2], records[1])]}}
        return items, records, results, build_dependencies(items)

    def test_production_candidate_with_verified_inputs(self):
        items, records, results, graph = self.fixture()
        self.assertEqual(assess_production(items[0], results, graph, records)["status"], "candidate")

    def test_zero_without_null_blocks_production(self):
        items, records, results, graph = self.fixture()
        items[0]["definition"]["facts"].pop("null_policy")
        self.assertIn("missing:null_policy", assess_production(items[0], results, graph, records)["reasons"])

    def test_incompatible_keys_grain_reference_block(self):
        for dim, val in (("grain", "account"), ("reference_date", "report_date")):
            items, records, results, graph = self.fixture()
            records[1]["definition"]["facts"][dim]["value"] = val
            self.assertEqual(assess_production(items[0], results, graph, records)["status"], "insufficient")
        items, records, results, graph = self.fixture()
        records[0]["key_columns"] = []
        self.assertEqual(assess_production(items[0], results, graph, records)["status"], "insufficient")

    def test_unready_operand_blocks(self):
        items, records, results, graph = self.fixture()
        results["b"]["semantic_status"] = "insufficient"
        self.assertIn("operand_not_ready:b", assess_production(items[0], results, graph, records)["reasons"])

    def test_cross_object_join_not_invented(self):
        items, records, results, graph = self.fixture()
        records[1]["object"] = "OTHER_FEATURES"
        self.assertIn("cross_object_relationship_unverified", assess_production(items[0], results, graph, records)["reasons"])

    def test_daily_distinct_counts_not_summed(self):
        req = request(definition=complete("last_month"))
        source = complete("day")
        source["facts"]["grain"]["value"] = "transaction"
        outcome = assess_production(req, {}, build_dependencies([req]), [record(source)])
        self.assertEqual(outcome["status"], "source_only")
        self.assertNotEqual(outcome["status"], "candidate")


if __name__ == "__main__":
    unittest.main()
