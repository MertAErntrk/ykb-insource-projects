import unittest

from riskmatch.semantics import (
    assess_production, build_dependencies, differences, evaluate_candidate, normalize, parse_definition,
    type_class,
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


def described(comment, column="OTHER_NAME"):
    result = record(column=column)
    result["column_comment"] = comment
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

    def test_unhandled_qualifiers_are_notes(self):
        # Unparsed words are reported, not used to lower the status.
        for qualifier in ("Yalnız kredi kartı başvuruları", "Türkiye müşterileri", "net gelir", "vergiler düşüldükten sonra", "2025 yılı itibarıyla", "iptal edilmiş başvurular"):
            req = request("income", "Başvuru bazında beyan edilen gelir TRY. " + qualifier)
            rec = record(column="income")
            rec["column_comment"] = "Başvuru bazında beyan edilen gelir TRY."
            with self.subTest(qualifier=qualifier):
                outcome = evaluate_candidate(req, rec)
                self.assertTrue(any(r.startswith("ayrıştırılamayan ifade: talep") for r in outcome["reasons"]))
                self.assertNotIn("window", parse_definition("income", qualifier)["facts"])

    def test_negated_scope_never_supported(self):
        req = request("income", "Başvuru bazında beyan edilen gelir TRY. kurumsal müşteri hariç")
        rec = record(column="income")
        rec["column_comment"] = "Başvuru bazında beyan edilen gelir TRY."
        self.assertNotEqual(evaluate_candidate(req, rec)["status"], "supported")

    def test_different_unknown_qualifiers_need_review(self):
        req = request("income", "Başvuru bazında beyan edilen net gelir TRY.")
        rec = record(column="income")
        rec["column_comment"] = "Başvuru bazında beyan edilen brüt gelir TRY."
        outcome = evaluate_candidate(req, rec)
        self.assertEqual(outcome["status"], "insufficient")
        self.assertIn("missing:qualifier", outcome["reasons"])

    def test_equal_unknown_qualifiers_are_notes(self):
        text = "Başvuru bazında beyan edilen gelir TRY. Bölge grubuna göre."
        rec = record(column="income")
        rec["column_comment"] = text
        outcome = evaluate_candidate(request("income", text), rec)
        self.assertEqual(outcome["status"], "supported")
        self.assertIn("ayrıştırılamayan ifade: talep 'bolge grubuna gore'", outcome["reasons"])

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
                # Known on the request, unknown on the candidate.
                incomplete = complete()
                incomplete["facts"].pop(dimension)
                self.assertEqual(evaluate_candidate(request(definition=complete()), record(incomplete))["status"], "insufficient")

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

    def test_same_text_supported_when_all_stated_facts_agree(self):
        desc = "Müşterinin son haftada kaç farklı kişi ile EFT ilişkisine girdiği."
        rec = record(column="EFT_LW")
        rec["column_comment"] = desc
        self.assertEqual(evaluate_candidate(request("EFT_LW", desc), rec)["status"], "supported")

    def test_identical_description_supported(self):  # (a)
        text = "Musteri bazinda son 3 ay toplam havale tutari TL"
        self.assertEqual(evaluate_candidate(request("X", text), described(text))["status"], "supported")

    def test_numeric_windows(self):  # (b)
        outcome = evaluate_candidate(request("X", "son 12 ay toplam havale"), described("son 3 ay toplam havale"))
        self.assertEqual(outcome["status"], "conflict")
        self.assertIn("conflict:window", outcome["reasons"])
        self.assertEqual(parse_definition("AVG_BAL_3M", "")["facts"]["window"]["value"], "last_3_months")
        self.assertEqual(parse_definition("X", "son 1 yıl")["facts"]["window"]["value"], parse_definition("X", "son 12 ay")["facts"]["window"]["value"])
        self.assertEqual(parse_definition("X", "son 14 gün")["facts"]["window"]["value"], "last_2_weeks")
        diff = differences(outcome)
        self.assertEqual(diff[0]["dimension"], "window")
        self.assertEqual(diff[0]["text"], "Dönem farklı: talep son 12 ay, kolon son 3 ay")

    def test_count_vs_amount(self):  # (c)
        outcome = evaluate_candidate(request("X", "Musteri bazinda son 3 ay havale adedi"), described("Musteri bazinda son 3 ay havale tutari TL"))
        self.assertEqual(outcome["status"], "conflict")
        self.assertIn("conflict:measure", outcome["reasons"])
        texts = {d["dimension"]: d["text"] for d in differences(outcome)}
        self.assertIn("talep adet", texts["measure"])
        self.assertIn("kolon tutar", texts["measure"])
        self.assertEqual(parse_definition("X", "havale sayısı")["facts"]["measure"]["value"], "count")
        self.assertEqual(parse_definition("X", "toplam havale adedi")["facts"]["aggregation"]["value"], "count")

    def test_time_unit_count_is_duration(self):
        # "gün sayısı" measures a duration, not a count of events.
        facts = parse_definition("X", "Son 12 ayda ortalama gecikme gün sayısı")["facts"]
        self.assertEqual(facts["measure"]["value"], "duration")
        self.assertEqual(facts["aggregation"]["value"], "mean")
        outcome = evaluate_candidate(request("X", "Son 12 ayda ortalama gecikme gün sayısı"), described("Son 12 ayda ortalama gecikme gün"))
        self.assertNotEqual(outcome["status"], "conflict")
        facts = parse_definition("X", "Aylık ortalama işlem adedi")["facts"]
        self.assertEqual(facts["measure"]["value"], "count")
        self.assertEqual(facts["aggregation"]["value"], ["count", "mean"])
        # The period word is the unit, so an identical duration definition can be supported.
        same = "Kredi gecikme gün sayısı"
        self.assertEqual(parse_definition("X", same)["facts"]["unit"]["value"], "day")
        self.assertEqual(evaluate_candidate(request("X", same), described(same))["status"], "supported")
        # A window before "adet" is not a duration; possessive "günü sayısı" is the same duration as "gün sayısı".
        self.assertEqual(parse_definition("X", "Son 3 ay adet havale işlemi")["facts"]["measure"]["value"], "count")
        outcome = evaluate_candidate(request("X", "İşlem yapılan gün sayısı"), described("İşlem günü sayısı"))
        self.assertNotIn("conflict:measure", outcome["reasons"])

    def test_grain_difference(self):  # (d)
        outcome = evaluate_candidate(request("X", "Kredi bazinda son 3 ay toplam havale tutari TL"), described("Musteri bazinda son 3 ay toplam havale tutari TL"))
        self.assertEqual(outcome["status"], "conflict")
        self.assertIn("conflict:grain", outcome["reasons"])
        diff = differences(outcome)
        self.assertEqual(diff[0]["dimension"], "grain")
        self.assertIn("talep kredi", diff[0]["text"])
        self.assertIn("kolon müşteri", diff[0]["text"])
        self.assertTrue(all(d["dimension"] != "grain" for d in diff[1:]))
        self.assertNotIn("grain", parse_definition("MUS_TOPLAM_KREDI_ADEDI", "")["facts"])

    def test_data_type_conflict(self):  # (e)
        text = "Musteri bazinda son 3 ay toplam havale tutari TL"
        req = request("X", text)
        req["data_type"] = "Tarih"
        rec = described(text)
        rec["data_type"] = "NUMBER(18,2)"
        outcome = evaluate_candidate(req, rec)
        self.assertEqual(outcome["status"], "conflict")
        self.assertIn("conflict:data_type", outcome["reasons"])
        for wanted, actual in (("Flag", "VARCHAR2(1)"), ("Sayısal", "NUMBER"), ("", "DATE")):
            req["data_type"], rec["data_type"] = wanted, actual
            self.assertEqual(evaluate_candidate(req, rec)["status"], "supported")
        self.assertEqual([type_class(x) for x in ("TIMESTAMP(6)", "Metin", "Evet/Hayır", "FLOAT")], ["date", "text", "unknown", "number"])

    def test_blank_comment_is_insufficient_not_conflict(self):  # (f)
        outcome = evaluate_candidate(request("X", "Musteri bazinda son 3 ay toplam havale tutari TL"), described(""))
        self.assertEqual(outcome["status"], "insufficient")
        self.assertIn("Kolon açıklamasında dönem bilgisi yok", [d["text"] for d in differences(outcome)])

    def test_differences_order_is_conflict_first(self):
        outcome = evaluate_candidate(request("X", "Musteri bazinda son 12 ay toplam havale tutari TL"), described("son 3 ay havale tutari"))
        statuses = [next(c["status"] for c in outcome["checks"] if c["dimension"] == d["dimension"]) for d in differences(outcome)]
        self.assertEqual(statuses, sorted(statuses))
        self.assertEqual(differences(outcome), differences(outcome))

    def test_differences_accept_structured_values(self):
        # Regression: dict-valued facts (e.g. ratio operands, bounds) crashed the label lookup.
        check = {"dimension": "window_bounds", "status": "conflict", "requested": {"start": 1, "end": 3},
                 "actual": {"start": 0, "end": 3}}
        text = differences({"checks": [check]})[0]["text"]
        self.assertIn("end=3", text)
        self.assertIn("start=1", text)

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
