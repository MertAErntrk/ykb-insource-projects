import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixture  # noqa: E402
import evaluate  # noqa: E402

SECRET = "s3cret-Zx9-not-for-reports"


class EvaluateSyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.out = Path(cls.directory.name) / "out"
        env = {"BENCH_USER": "bench_user", "BENCH_PASSWORD": SECRET, "BENCH_DSN": "dsn.invalid:1521/x"}
        with patch.dict(os.environ, env):
            code = evaluate.main(["--fixture", "--filler-tables", "6", "--group-size", "20", "--consistency",
                                  "--out", str(cls.out)])
        cls.code = code
        cls.report = json.loads((cls.out / "rapor.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_exit_and_files(self):
        self.assertEqual(self.code, 0)
        for name in ("rapor.json", "rapor.md", "havuz.json"):
            self.assertTrue((self.out / name).is_file(), name)

    def test_report_keys(self):
        for key in ("mod", "not", "kurulum", "satir", "kapi_taramasi", "en_iyi_kapi", "uctan_uca", "tutarlilik"):
            self.assertIn(key, self.report)
        self.assertEqual(self.report["mod"], "sentetik")
        for key in ("surum", "katalog_kolon", "kapsam", "index_coverage"):
            self.assertIn(key, self.report["kurulum"])
        self.assertGreater(self.report["kurulum"]["katalog_kolon"], 0)
        for key in ("ilk_sonuc_s", "toplam_s", "arama_istatistikleri"):
            self.assertIn(key, self.report["uctan_uca"])

    def test_grid_has_nine_gates(self):
        grid = self.report["kapi_taramasi"]
        self.assertEqual([g["kapi"] for g in grid][0], "varsayilan")
        self.assertEqual(len([g for g in grid if g["kapi"] != "varsayilan"]), 9)
        for gate in grid:
            for key in ("recall_havuz", "hit_at_3", "precision_at_3", "ilk_dogru_sira_dagilim",
                        "yok_satirda_aday_gosterilen", "row_status"):
                self.assertIn(key, gate)
            self.assertTrue(0.0 <= gate["recall_havuz"] <= 1.0)

    def test_consistency_in_range(self):
        self.assertTrue(0.0 <= self.report["tutarlilik"]["oran"] <= 1.0)

    def test_no_secrets_in_outputs(self):
        for name in ("rapor.json", "rapor.md"):
            text = (self.out / name).read_text(encoding="utf-8")
            self.assertNotIn("password", text.casefold())
            self.assertNotIn(SECRET, text)
            self.assertNotIn("dsn.invalid", text)


class LabelTests(unittest.TestCase):
    def test_parse_label(self):
        self.assertEqual(evaluate.parse_label("yok"), (set(), True))
        self.assertEqual(evaluate.parse_label(""), (set(), True))
        self.assertEqual(evaluate.parse_label("a.b.c; d.e.f"), ({("A", "B", "C"), ("D", "E", "F")}, True))
        self.assertEqual(evaluate.parse_label("a.b"), (set(), False))

    def test_candidate_hit_uses_locations(self):
        candidate = {"source": {"schema": "S1", "object": "T1", "column": "C"}, "locations": ["S2.T2"]}
        self.assertTrue(evaluate.candidate_hit(candidate, {("S2", "T2", "C")}))
        self.assertFalse(evaluate.candidate_hit(candidate, {("S3", "T3", "C")}))


class ScaledFixtureTests(unittest.TestCase):
    def test_273_rows_deterministic_and_labelled(self):
        first = benchmark_fixture.build(4, rows=273)
        second = benchmark_fixture.build(4, rows=273)
        self.assertEqual(first, second)
        snapshot, requests, labels = first
        self.assertEqual((len(requests), len(labels)), (273, 273))
        self.assertEqual(len({r["row_id"] for r in requests}), 273)
        base_snapshot, base_requests, base_labels = benchmark_fixture.build(4)
        self.assertEqual(requests[:len(base_requests)], base_requests)
        self.assertEqual(labels[:len(base_labels)], base_labels)
        extra = labels[len(base_labels):]
        yok = sum(1 for label in extra if not label)
        self.assertTrue(0.15 <= yok / len(extra) <= 0.25, yok / len(extra))
        known = {r["catalog_id"] for r in snapshot["records"]}
        self.assertTrue(all(set(label) <= known for label in labels))

    def test_garble_name_deterministic(self):
        names = ["ORT_MEVDUAT_BAKIYE_3AY", "GECIKEN_KREDI_SAYISI_6AY", "KART_LIMIT", "Son3AyOrtalamaBakiye"]
        for name in names:
            self.assertEqual(evaluate.garble_name(name), evaluate.garble_name(name))
        garbled = {evaluate.garble_name(n) for n in names}
        self.assertEqual(len(garbled), len(names))
        self.assertTrue(any(g != n for g, n in zip((evaluate.garble_name(n) for n in names), names)))

    def test_empty_lexicon_builds(self):
        self.assertTrue(evaluate.empty_lexicon().tokens("Ortalama Bakiye"))

    def test_nolex_keeps_generic_and_structural(self):
        partial, full = evaluate.empty_lexicon(full=False), evaluate.empty_lexicon()
        self.assertEqual((partial._data["groups"], partial._data["expansions"]), ([], {}))
        self.assertTrue(partial._data["generic"] and partial._data["suffixes"])
        self.assertEqual((full._data["generic"], full._data["suffixes"], full._data["structural"]), ([], [], []))
        self.assertNotEqual(partial._data["generic"], full._data["generic"])

    def test_extra_row_types_follow_concept_type(self):
        snapshot, requests, labels = benchmark_fixture.build(4, rows=273)
        types = {r["catalog_id"]: benchmark_fixture.REQUEST_TYPES[r["data_type"]] for r in snapshot["records"]}
        checked = 0
        for request, label in zip(requests, labels):
            if request["category"] == "Ek":
                self.assertEqual({types[cid] for cid in label}, {request["data_type"]}, request["name"])
                checked += 1
        self.assertGreater(checked, 100)
        self.assertTrue({r["data_type"] for r in requests if r["category"] == "Ek"} > {"Sayısal"})


if __name__ == "__main__":
    unittest.main()
