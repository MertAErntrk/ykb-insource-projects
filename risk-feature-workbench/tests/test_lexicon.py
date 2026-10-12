import json
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riskmatch.errors import ConfigError
from riskmatch.lexicon import fold, load, mine_abbreviations
from riskmatch.retrieval import _fold


def write_json(directory, name, payload):
    path = Path(directory) / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return str(path)


class LexiconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lex = load()

    def tokset(self, text):
        return set(self.lex.tokens(text))

    def test_fold_matches_retrieval(self):
        for text in ("MÜŞTERİ", "Işık ılık", "çğöşü İstanbul", "ABC_def", "", None):
            self.assertEqual(fold(text), _fold(text))

    def test_same_cluster_across_languages(self):
        expected = {"ortalama", "mevduat", "bakiye", "win:3:ay"}
        for text in ("AVG_DPST_BAL_3M", "ORT_MEVDUAT_BKY_3AY", "Son 3 aydaki ortalama mevduat bakiyesi"):
            self.assertEqual(self.tokset(text), expected, text)

    def test_window_in_identifier(self):
        self.assertEqual(self.tokset("KRD_TUT_SON3AY"), {"kredi", "tutar", "win:3:ay"})

    def test_windows_differ(self):
        self.assertNotEqual(self.tokset("Son 12 ay ortalama bakiye"), self.tokset("Son 3 ay ortalama bakiye"))

    def test_order_is_kept(self):
        self.assertEqual(self.lex.tokens("AVG_DPST_BAL_3M"), ["ortalama", "mevduat", "bakiye", "win:3:ay"])

    def test_cross_language_count(self):
        self.assertEqual(self.tokset("HVL_GLN_ADET_3AY"), self.tokset("Son 3 ay gelen havale adedi"))

    def test_count_not_amount(self):
        self.assertNotEqual(self.tokset("Havale adedi"), self.tokset("Havale tutarı"))
        self.assertIn("adet", self.tokset("adedi"))
        self.assertIn("tutar", self.tokset("tutarı"))

    def test_window_forms(self):
        cases = {
            "3M": (3, "ay"), "L3M": (3, "ay"), "03AY": (3, "ay"), "SON3AY": (3, "ay"), "3AY": (3, "ay"),
            "son 3 ay": (3, "ay"), "son üç ay": (3, "ay"), "last 3 months": (3, "ay"),
            "12M": (12, "ay"), "1Y": (1, "yil"), "6 aylık": (6, "ay"), "son ay": (1, "ay"),
            "last month": (1, "ay"), "son 30 gün": (30, "gun"), "last 2 weeks": (2, "hafta"),
            "son on iki ay": (12, "ay"), "LM": (1, "ay"), "LW": (1, "hafta"), "L2W": (2, "hafta"),
            "L6M": (6, "ay"), "L12M": (12, "ay"), "W1W": (1, "hafta"), "W1M": (1, "ay"), "W3M": (3, "ay"),
            "önceki 1 ay içerisinde": (1, "ay"), "son ayda": (1, "ay"), "son bir haftada": (1, "hafta"),
        }
        for text, expected in cases.items():
            self.assertEqual(self.lex.window(text), [expected], text)
        self.assertEqual(self.lex.window("YTD"), [(0, "ytd")])
        self.assertEqual(self.lex.window("kredi tutari"), [])

    def test_excel_and_catalog_windows_share_tokens(self):
        self.assertEqual(self.tokset("Giden_EFT_tut_LM"), {"giden", "eft", "tutar", "win:1:ay"})
        self.assertEqual(self.tokset("TOTAL_EFT_TRX_AMT_TL_W1M"), {"toplam", "eft", "islem", "tutar", "tl", "win:1:ay"})
        self.assertEqual(self.tokset("X_LW"), self.tokset("X_W1W"))

    def test_channels_are_separate(self):
        self.assertEqual(self.tokset("HVL"), {"havale"})
        self.assertEqual(self.tokset("EFT"), {"eft"})
        self.assertNotEqual(self.tokset("havale"), self.tokset("eft"))
        self.assertEqual(self.tokset("western union"), self.tokset("WU"))
        self.assertEqual(self.tokset("SWIFT"), {"swift"})
        for text in ("TRF", "xfer", "para transferi", "money transfer"):
            self.assertEqual(self.tokset(text), {"transfer"}, text)

    def test_son_alone_is_generic_token(self):
        self.assertIn("son", self.lex.tokens("son kullanma"))
        self.assertTrue(self.lex.is_generic("son"))

    def test_generic(self):
        for word in ("musteri", "Müşteri", "bazinda", "ve", "the", "no", "id", "value"):
            self.assertTrue(self.lex.is_generic(word), word)
        self.assertFalse(self.lex.is_generic("bakiye"))

    def test_structural_tokens(self):
        for token in ("win:3:ay", "win:0:ytd", "tutar", "adet", "oran", "gun", "ay", "yil", "tl", "try",
                      "gelen", "giden", "ortalama", "toplam", "maksimum", "minimum", "son", "Tutar"):
            self.assertTrue(self.lex.is_structural(token), token)
        for token in ("bakiye", "mevduat", "havale", "kripto", "kredi", "atm", ""):
            self.assertFalse(self.lex.is_structural(token), token)
        tokens = self.lex.tokens("Son 3 ay ortalama ATM tutarı")
        self.assertEqual([t for t in tokens if not self.lex.is_structural(t)], ["atm"])
        # Bayrak (flag) bir ölçü türüdür: "KRIPTO_FLAG" ile "ATM_FLG" aynı konuyu paylaşmaz.
        self.assertEqual([t for t in self.lex.tokens("KRIPTO_FLAG_1AY") if not self.lex.is_structural(t)], ["kripto"])

    def test_phrases_become_single_token(self):
        self.assertEqual(self.lex.tokens("kredi kartı"), self.lex.tokens("credit card"))
        self.assertEqual(len(self.lex.tokens("kredi kartları")), 1)
        self.assertEqual(self.lex.tokens("CC"), self.lex.tokens("kredi kartı"))
        self.assertEqual(self.lex.tokens("days past due"), self.lex.tokens("DPD"))
        self.assertEqual(self.tokset("kredili mevduat hesabı"), self.tokset("KMH"))
        self.assertEqual(self.lex.tokens("kredi")[0], "kredi")
        self.assertNotEqual(self.lex.tokens("kredi"), self.lex.tokens("kredi kartı"))

    def test_expansion(self):
        self.assertEqual(self.tokset("DTI"), {"borc", "gelir", "oran"})
        self.assertEqual(self.tokset("DPD"), {"gecikme", "gun"})

    def test_camel_case_and_separators(self):
        self.assertEqual(self.lex.tokens("avgDepositBalance"), ["ortalama", "mevduat", "bakiye"])
        self.assertEqual(self.lex.tokens("avg-dpst bal"), ["ortalama", "mevduat", "bakiye"])

    def test_describe(self):
        self.assertEqual(self.lex.describe("AVG_DPST_BAL_3M"), "ortalama mevduat bakiye son 3 ay")
        self.assertEqual(self.lex.describe("KREDI_KARTI_YTD"), "kredi karti yıl başından beri")

    def test_group_count(self):
        data = json.loads((Path(__file__).resolve().parents[1] / "src" / "riskmatch" / "data"
                           / "lexicon.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["groups"]), 60)

    def test_mining(self):
        with tempfile.TemporaryDirectory() as tmp:
            small = load(write_json(tmp, "small.json", {"version": 1, "groups": [["yaş", "age"]],
                                                  "suffixes": ["deki", "si", "sı", "i", "ı"]}))
        records = [
            {"column": "GCK_SAY_3M", "column_comment": "Son 3 ay gecikme sayısı"},
            {"column": "GCK_TUTAR", "column_comment": "Gecikmedeki tutar"},
            {"column": "MUS_YAS", "column_comment": "Müşteri yaşı"},
            {"column": "MUS_KIDEM", "column_comment": "Müşteri kıdemi"},
            {"column": "XQ_FLAG", "column_comment": "Xenon durumu"},
            {"column": "NOCOMMENT_ZZ", "column_comment": ""},
        ]
        mined = mine_abbreviations(records, lexicon=small)
        self.assertEqual(mined.get("gck"), "gecikme")
        self.assertEqual(mined.get("mus"), "musteri")
        self.assertNotIn("xq", mined)
        self.assertEqual(mine_abbreviations(records, min_support=3, lexicon=small).get("gck"), None)
        enriched = small.with_mined(mined)
        self.assertEqual(enriched.tokens("GCK"), enriched.tokens("gecikme"))
        self.assertNotEqual(small.tokens("GCK"), small.tokens("gecikme"))

    def test_extra_path_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = write_json(tmp, "extra.json", {
                "version": 1,
                "groups": [["bakiye", "bkyx"], ["ekmetrik", "ekm", "extra metric"]],
                "generic": ["ozelkelime"],
            })
            lex = load(extra_path=extra)
        self.assertEqual(lex.tokens("BKYX"), ["bakiye"])
        self.assertEqual(lex.tokens("EKM"), lex.tokens("extra metric"))
        self.assertEqual(lex.tokens("EKM"), ["ekmetrik"])
        self.assertTrue(lex.is_generic("ozelkelime"))
        self.assertEqual(lex.tokens("AVG_DPST_BAL"), ["ortalama", "mevduat", "bakiye"])
        self.assertEqual(self.lex.tokens("BKYX"), ["bkyx"])

    def test_bad_json_is_safe_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = write_json(tmp, "bad.json", '{"groups": [["a"], SECRET')
            with self.assertRaises(ConfigError) as caught:
                load(extra_path=bad)
            self.assertNotIn("SECRET", str(caught.exception))
            wrong = write_json(tmp, "wrong.json", {"version": 1, "groups": "x"})
            with self.assertRaises(ConfigError):
                load(wrong)
            with self.assertRaises(ConfigError):
                load(str(Path(tmp) / "missing.json"))

    def test_default_is_cached(self):
        self.assertIs(load(), load())

    def test_throughput_10k(self):
        rng = random.Random(7)
        vocab = ["AVG", "ORT", "DPST", "BAL", "BKY", "MUS", "KRD", "TUT", "AMT", "HVL", "GLN", "ADET",
                 "CNT", "SON3AY", "3M", "12M", "L6M", "TRF", "LMT", "UTIL", "FLG", "XYZ", "QWE", "V2", "TKP",
                 "KK", "CC", "DAYS", "PAST", "DUE", "KREDILI", "MEVDUAT", "HESABI", "03AY"]
        names = ["_".join(rng.choice(vocab) for _ in range(rng.randint(2, 6))) for _ in range(10000)]
        started = time.perf_counter()
        for name in names:
            self.lex.tokens(name)
        elapsed = time.perf_counter() - started
        print("\ntokens() x10000: %.3f s" % elapsed)
        self.assertLess(elapsed, 5.0)


class ShortRootTests(unittest.TestCase):
    def test_short_abbreviation_roots_are_not_suffix_stripped(self):
        lex = load()
        for word in ("bale", "mine", "tele"):
            self.assertEqual(lex.tokens(word), [word])

    def test_known_abbreviations_and_short_words_still_normalize(self):
        lex = load()
        for word, canon in (("bal", "bakiye"), ("min", "minimum"), ("tel", "telefon"), ("bky", "bakiye"),
                            ("bakiyesi", "bakiye"), ("günü", "gun"), ("yaşı", "yas"), ("kodu", "kod"),
                            ("tutarı", "tutar"), ("limiti", "limit")):
            self.assertEqual(lex.tokens(word), [canon], word)


if __name__ == "__main__":
    unittest.main()
