"""Medallion kabul testleri (A1-A5): pipeline.analyze üzerinden katman, karar ve oran türetme."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook

import benchmark_fixture
import fixture_medallion as fm
from riskmatch.config import DEFAULT_EXCLUDE_OBJECTS
from riskmatch.excelio import read_requests
from riskmatch.pipeline import analyze
from test_retrieval import hash_vector

SCHEMAS = ("DM_CAD", "DM", "EDW")
RATIO_NAME = "Giden_havale_adeti_LW_L3M"


class HashEmbedding:
    """test_retrieval.HashEmbed ile aynı deterministik trigram vektörü; DemoEmbedding arayüzüyle."""
    model = "test-trigram-hash"
    model_version = "1"
    dimensions = 64

    def embed(self, texts, kind="document"):
        return [hash_vector(text) for text in texts]


def _background():
    """Gerçekçi z ölçeği için benchmark dolgusu: kapsam Medallion'a, şema tablo bazında üç şemaya dağıtılır."""
    homes, out = {}, []
    for record in benchmark_fixture.build(filler_tables=60)[0]["records"]:
        row = dict(record)
        row.update({key: fm.SCOPE[key] for key in ("source_database", "environment", "container")})
        row["schema"] = homes.setdefault((record["schema"], record["object"]), SCHEMAS[len(homes) % len(SCHEMAS)])
        row["catalog_id"] = "bg-" + record["catalog_id"]
        out.append(row)
    return out


BACKGROUND = _background()


def _excluded(row):
    # Oracle taraması DEFAULT_EXCLUDE_OBJECTS'i SQL'de uygular; demo kataloğunda aynı kural burada uygulanır.
    return any(rule["schema"] == row["schema"] and any(row["object"].upper().startswith(p) for p in rule["prefixes"])
               for rule in DEFAULT_EXCLUDE_OBJECTS)


def _snapshot(variant, schemas, complete):
    snap = fm.snapshot(variant, schemas, complete)
    rows = [r for r in snap["records"] + BACKGROUND if r["schema"] in schemas and not _excluded(r)]
    snap["records"] = sorted(rows, key=lambda r: (r["schema"], r["object"], r["column"], r["catalog_id"]))
    return snap


def _workbook(path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Degiskenler"
    sheet.append(["Kategori", "Alan Adı", "Alan tipi", "Açıklama"])
    for item in fm.REQUESTS:
        sheet.append(["Risk", item["name"], "NUMBER", item["description"]])
    workbook.save(path)
    workbook.close()


_RUNS = {}


def run(variant="base", schemas=SCHEMAS, complete=True, embedding=True):
    key = (variant, tuple(schemas), complete, embedding)
    if key not in _RUNS:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "talepler.xlsx"
            _workbook(source)
            config = {"mode": "demo", "oracle": {"schemas": list(schemas)}}
            with patch("riskmatch.demo.demo_snapshot", return_value=_snapshot(variant, schemas, complete)), \
                    patch("riskmatch.demo.DemoEmbedding", HashEmbedding):
                output = analyze(source, root / "out.xlsx", state_dir=root / "state", resume=False, config_data=config,
                                 strategy="hybrid" if embedding else "lexical", write_report=False)
            names = {r["row_id"]: r["name"] for r in read_requests(source)}
        _RUNS[key] = {names[r["row_id"]]: r for r in output["results"]}
    return _RUNS[key]


def path(candidate):
    source = candidate["source"]
    return (source["schema"], source["object"], source["column"])


def codes(result):
    return [reason["code"] for reason in result["verdict_reasons"]]


class MedallionAcceptanceTests(unittest.TestCase):
    def assert_readable(self, result):
        # Yalnız karar gerekçeleri; "reasons" alanı eski ham notları (ör. tanım sorunları) geriye uyum için taşır.
        text = " ".join(r["text"] for r in result["verdict_reasons"])
        for code in codes(result):
            self.assertNotIn(code, text)

    def test_a1_outgoing_eft_direct_in_dm_cad(self):
        expect = fm.REQUESTS[0]["expect"]
        for embedding in (False, True):
            with self.subTest(embedding=embedding):
                result = run(embedding=embedding)["Giden_EFT_tut_LM"]
                self.assertTrue(result["candidates"])
                self.assertEqual(path(result["candidates"][0]), expect)
                self.assertEqual(result["verdict"], "dogrudan")
                self.assertEqual([item["tier"] for item in result["tier_trace"]], ["DM_CAD"])
                self.assertEqual(result["retrieval_info"]["tier"], "DM_CAD")
                self.assert_readable(result)

    def test_a2_distractors_do_not_win_and_differences_are_written(self):
        distractors = {item["expect"][2] for item in fm.REQUESTS[1:3]} | {fm.REQUESTS[4]["expect"][2]}
        for embedding in (False, True):
            with self.subTest(embedding=embedding):
                result = run(embedding=embedding)["Giden_EFT_tut_LM"]
                self.assertNotIn(path(result["candidates"][0])[2], distractors)
                rejected = [c for c in result["rejected_candidates"] if c["source"]["column"] in distractors]
                self.assertTrue(rejected)
                for candidate in rejected:
                    self.assertTrue([d["text"] for d in candidate["differences"] if d.get("text")], path(candidate))

    def test_a3_ratio_column_is_direct(self):
        ratio = run("ratio_column")[RATIO_NAME]
        self.assertEqual(ratio["verdict"], "dogrudan", codes(ratio))
        self.assertEqual(path(ratio["candidates"][0])[2], fm.RATIO_COLUMN)

    def test_a3_same_table_operands_are_derivable(self):
        base = run("base")[RATIO_NAME]
        self.assertEqual(base["verdict"], "turetilebilir", codes(base))
        derivation = base["derivation"]
        self.assertEqual(derivation["relationship"], "same_object")
        self.assertEqual(derivation["formula"].count("/"), 1)
        sources = {c["role"]: c["source"] for c in derivation["components"]}
        self.assertTrue(all(c["verdict"] == "dogrudan" for c in derivation["components"]))
        self.assertEqual(sources["numerator"], {"schema": "DM_CAD", "object": fm.XFER_TABLE,
                                                "column": "TOTAL_HVL_TRX_CNT_W1W"})
        self.assertEqual(sources["denominator"], {"schema": "DM_CAD", "object": fm.XFER_TABLE,
                                                  "column": fm.DENOMINATOR})
        self.assert_readable(base)

    def test_a3_cross_table_operands_stay_ambiguous(self):
        cross = run("cross_table")[RATIO_NAME]
        self.assertEqual(cross["verdict"], "belirsiz")
        self.assertIn("cross_object_relationship_unverified", codes(cross))
        self.assertEqual(cross["derivation"]["relationship"], "unverified")
        self.assert_readable(cross)

    def test_a3_missing_denominator_stays_ambiguous(self):
        missing = run("denominator_missing")[RATIO_NAME]
        self.assertEqual(missing["verdict"], "belirsiz")
        self.assertIn("operand_not_found:denominator", codes(missing))
        self.assertNotIn("operand_not_found:numerator", codes(missing))
        self.assert_readable(missing)

    def test_a4_absence_needs_selected_complete_catalog(self):
        name = "Sigorta_police_adet_L12M"
        only = run(schemas=("DM_CAD",))[name]
        self.assertEqual(only["candidates"], [])
        self.assertEqual(only["verdict"], "karar_verilemedi")
        self.assertIn("tier_not_selected:DM", codes(only))
        self.assertIn("tier_not_selected:EDW", codes(only))
        self.assert_readable(only)

        full = run()[name]
        self.assertEqual(full["verdict"], "bulunamadi", full["verdict_reasons"])

        partial = run(complete=False)[name]
        self.assertEqual(partial["verdict"], "karar_verilemedi")
        self.assertTrue(any(code.startswith("catalog_incomplete:") for code in codes(partial)))

    def test_a5_swift_l6m_direct_in_dm_and_excluded_tables_never_pooled(self):
        result = run()["Giden_swift_tut_L6M"]
        self.assertEqual(result["verdict"], "dogrudan")
        self.assertEqual(path(result["candidates"][0]), fm.REQUESTS[6]["expect"])
        self.assertEqual([item["tier"] for item in result["tier_trace"]], ["DM_CAD", "DM"])
        excluded = {r["catalog_id"] for r in fm.records("base") if _excluded(r)}
        self.assertTrue(excluded)
        for results in (run(), run(embedding=False)):
            for row in results.values():
                seen = set(row.get("pool_ids") or [])
                seen |= {c["catalog_id"] for c in row["candidates"] + row["rejected_candidates"]}
                self.assertFalse(seen & excluded)


if __name__ == "__main__":
    unittest.main()
