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
        for name in ("rapor.json", "rapor.md", "havuz.json", "iskalar.md", "satirlar.json", "katalog_ozet.md",
                     "katalog_ozet.json"):
            self.assertTrue((self.out / name).is_file(), name)

    def test_report_top_has_cache_and_coverage(self):
        head = (self.out / "rapor.md").read_text(encoding="utf-8").splitlines()[:6]
        self.assertTrue(head[2].startswith("- Katalog:"), head)
        self.assertTrue(any("Anlamsal indeks kapsamı: başta" in line for line in head), head)
        self.assertIn("etiketsiz", self.report["satir"])
        self.assertIn("index_coverage_baslangic", self.report["kurulum"])

    def test_row_diagnosis(self):
        rows = json.loads((self.out / "satirlar.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), self.report["satir"]["pozitif"] + self.report["satir"]["yok"])
        allowed = ("dogru_ilk3", "havuzda_yok", "yok_temiz", "yok_oneri")
        for row in rows:
            self.assertTrue(row["sinif"] in allowed or row["sinif"].startswith("elendi:"), row["sinif"])
            for key in ("ad", "aciklama", "veri_tipi", "kategori", "beklenen", "beklenen_katalog",
                        "beklenen_havuz_sirasi", "beklenen_ozellik", "ilk3"):
                self.assertIn(key, row)
            self.assertLessEqual(len(row["ilk3"]), 3)
            if row["sinif"] == "dogru_ilk3":
                self.assertTrue(any(c["hit"] for c in row["ilk3"]))
                for key in ("z", "cos", "coverage", "subject_coverage", "subject_missing", "name_level", "meaning",
                            "checks", "rejected_reason"):
                    self.assertIn(key, row["beklenen_ozellik"])
            if not row["beklenen"]:
                self.assertIn(row["sinif"], ("yok_temiz", "yok_oneri"))
        self.assertEqual(sum(self.report["satir_siniflari"].values()), len(rows))
        self.assertIn("| Sınıf | Satır |", (self.out / "iskalar.md").read_text(encoding="utf-8"))

    def test_catalog_summary(self):
        summary = json.loads((self.out / "katalog_ozet.json").read_text(encoding="utf-8"))
        for key in ("kolon", "tablo", "kolon_yorum_doluluk_yuzde", "tablo_yorum_doluluk_yuzde", "veri_tipi_ilk10",
                    "kolon_adi_token_sayisi", "sozlukte_olmayan_kolon_tokenlari", "sozlukte_olmayan_talep_tokenlari"):
            self.assertIn(key, summary)
        self.assertEqual(summary["kolon"], self.report["kurulum"]["katalog_kolon"])
        self.assertTrue(0 <= summary["kolon_yorum_doluluk_yuzde"] <= 100)
        self.assertLessEqual(len(summary["sozlukte_olmayan_kolon_tokenlari"]), 60)
        self.assertLessEqual(len(summary["sozlukte_olmayan_talep_tokenlari"]), 40)
        for item in summary["sozlukte_olmayan_kolon_tokenlari"]:
            self.assertTrue(1 <= len(item["ornek"]) <= 2)

    def test_report_keys(self):
        for key in ("mod", "not", "kurulum", "satir", "kapi_taramasi", "en_iyi_kapi", "uctan_uca", "tutarlilik"):
            self.assertIn(key, self.report)
        self.assertEqual(self.report["mod"], "sentetik")
        for key in ("surum", "katalog_kolon", "kapsam", "index_coverage"):
            self.assertIn(key, self.report["kurulum"])
        self.assertGreater(self.report["kurulum"]["katalog_kolon"], 0)
        for key in ("ilk_sonuc_s", "toplam_s", "arama_istatistikleri"):
            self.assertIn(key, self.report["uctan_uca"])

    def test_verdict_distribution_in_report(self):
        counts = self.report["sonuc_sinifi"]
        self.assertEqual(sum(counts.values()), self.report["uctan_uca"]["satir"])
        self.assertEqual(self.report["uctan_uca"]["sonuc_sinifi"], counts)
        text = (self.out / "rapor.md").read_text(encoding="utf-8")
        self.assertIn("Sonuç sınıfı dağılımı:", text)

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
        self.assertEqual(evaluate.parse_label("")["durum"], "etiketsiz")
        self.assertEqual(evaluate.parse_label(None)["durum"], "etiketsiz")
        self.assertEqual(evaluate.parse_label("yok")["durum"], "yok")
        self.assertEqual(evaluate.parse_label(" - ")["durum"], "yok")
        label = evaluate.parse_label("a.b.c; d.e.f")
        self.assertEqual((label["durum"], label["uclu"]), ("pozitif", {("A", "B", "C"), ("D", "E", "F")}))
        label = evaluate.parse_label("a.b.c; t.k")
        self.assertEqual((label["uclu"], label["ikili"]), ({("A", "B", "C")}, {("T", "K")}))
        self.assertEqual(evaluate.parse_label("abc")["durum"], "gecersiz")
        self.assertEqual(evaluate.parse_label("a.b.c.d")["durum"], "gecersiz")

    def test_blank_label_is_unlabelled_not_yok(self):
        requests = [{"row_id": "r%d" % i, "excel_row": i + 2, "name": "N%d" % i, "description": "",
                     "original": {"Beklenen kolon": value}} for i, value in enumerate(["", "YOK", "S.T.C", None])]
        args = evaluate.parse_args(["--config", "x.json", "--excel", "x.xlsx", "--out", "o"])
        with patch.object(evaluate, "read_requests", return_value=requests):
            rows = evaluate.load_rows(args)
        self.assertEqual([r["etiket"] for r in rows], ["etiketsiz", "yok", "pozitif", "etiketsiz"])
        self.assertEqual([r["label_valid"] for r in rows], [False, True, True, False])

    def test_input_errors_counted_and_sheets_without_label_ignored(self):
        requests = [{"row_id": "a", "excel_row": 2, "name": "N", "original": {"Beklenen kolon": "YOK"}},
                    {"row_id": "b", "excel_row": 3, "name": "", "original": {"Beklenen kolon": ""},
                     "input_errors": ["Alan adı boş."]},
                    {"row_id": "c", "excel_row": 1, "name": "", "original": {"A": "yardım"},
                     "input_errors": ["Alan Adı başlığı bulunamadı; satır incelenemedi."]}]
        args = evaluate.parse_args(["--config", "x.json", "--excel", "x.xlsx", "--out", "o"])
        with patch.object(evaluate, "read_requests", return_value=requests):
            rows = evaluate.load_rows(args)
        self.assertEqual([r["req"]["row_id"] for r in rows], ["a"])
        self.assertEqual(args._atlanan, {"adet": 1, "ilk_hata": "Alan adı boş."})
        with patch.object(evaluate, "read_requests", return_value=requests[1:2]):
            with self.assertRaisesRegex(evaluate.UserError, "1 satır girdi hatasıyla atlandı; ilk hata: Alan adı boş"):
                evaluate.load_rows(args)
        with patch.object(evaluate, "read_requests", return_value=requests[2:]):
            with self.assertRaisesRegex(evaluate.UserError, "Etiket kolonu bulunamadı"):
                evaluate.load_rows(args)

    def test_resolve_two_part_labels(self):
        records = [{"schema": s, "object": o, "column": c} for s, o, c in
                   (("S1", "T", "C"), ("S2", "T", "C"), ("S1", "U", "D"))]

        def row(text):
            label = evaluate.parse_label(text)
            return {"expected": set(label["uclu"]), "pairs": label["ikili"], "etiket": label["durum"],
                    "label_valid": True, "neden": ""}
        unique, ambiguous, missing, full = row("u.d"), row("T.C"), row("S9.T.C"), row("S2.T.C; U.D")
        evaluate.resolve_labels([unique, ambiguous, missing, full], records)
        self.assertEqual((unique["etiket"], unique["expected"]), ("pozitif", {("S1", "U", "D")}))
        self.assertEqual((ambiguous["etiket"], ambiguous["label_valid"]), ("gecersiz", False))
        self.assertIn("belirsiz", ambiguous["neden"])
        self.assertIn("katalogda yok", missing["neden"])
        self.assertEqual(full["expected"], {("S2", "T", "C"), ("S1", "U", "D")})

    def test_classify_row(self):
        hit = {"source": {"schema": "S", "object": "T", "column": "C"}, "locations": []}
        other = {"source": {"schema": "S", "object": "T", "column": "X"}, "locations": []}
        pool = [{"schema": "S", "object": "T", "column": "C"}]
        expected = {("S", "T", "C")}
        cases = [
            ({"expected": expected, "pool": pool}, [hit], [], "dogru_ilk3"),
            ({"expected": expected, "pool": pool}, [other], [dict(hit, rejected_reason=evaluate.GATE_REASON)],
             "elendi:" + evaluate.GATE_REASON),
            ({"expected": expected, "pool": pool}, [], [dict(hit, rejected_reason="Veri tipi farklı")],
             "elendi:Anlam çelişkisi"),
            ({"expected": expected, "pool": []}, [other], [], "havuzda_yok"),
            ({"expected": set(), "pool": pool}, [other], [], "yok_oneri"),
            ({"expected": set(), "pool": pool}, [], [], "yok_temiz"),
        ]
        for row, shown, rejected, want in cases:
            got, _ = evaluate.classify_row(row, {"candidates": shown, "rejected_candidates": rejected})
            self.assertEqual(got, want)

    def test_candidate_hit_uses_locations(self):
        candidate = {"source": {"schema": "S1", "object": "T1", "column": "C"}, "locations": ["S2.T2"]}
        self.assertTrue(evaluate.candidate_hit(candidate, {("S2", "T2", "C")}))
        self.assertFalse(evaluate.candidate_hit(candidate, {("S3", "T3", "C")}))


class ResourcesDirTests(unittest.TestCase):
    def test_second_run_reuses_vectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp) / "web"
            reports = []
            for number in (1, 2):
                out = Path(tmp) / ("out%d" % number)
                code = evaluate.main(["--fixture", "--filler-tables", "2", "--no-e2e", "--resources-dir", str(shared),
                                      "--out", str(out)])
                self.assertEqual(code, 0)
                reports.append(json.loads((out / "rapor.json").read_text(encoding="utf-8")))
            self.assertTrue((shared / "vectors.sqlite").is_file())
            first, second = (r["kurulum"] for r in reports)
            self.assertGreater(first["indeksleme"]["done"], 0)
            self.assertEqual(second["indeksleme"]["done"], 0)
            self.assertEqual(second["index_coverage_baslangic"], 1.0)
            self.assertEqual(second["onbellek_dizini"], "--resources-dir")


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


class WebEquivalentTests(unittest.TestCase):
    """Web-esdeger gercek mod: ag yok; probe/kesif/Oracle taramasi/embedding servisi sahte."""
    USER, PASSWORD, KEY = "olcum_kullanici_x7", "Parola-Q8-gizli", "anahtar-K5-gizli"
    LEAKS = (USER, PASSWORD, KEY, "yapikredi", "svc_edw_dwh", "edwlive.sys", "embedding.bench.invalid", "password")

    @classmethod
    def setUpClass(cls):
        import benchmark
        import etiket_sablonu
        cls.benchmark = benchmark
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        snapshot, requests, labels = benchmark_fixture.build(2)
        records = [r for r in snapshot["records"] if r["schema"] == "DM_CAD"]
        cls.snapshot = dict(snapshot, records=records, scope={"source_database": "EDWLIVE", "environment": "LIVE",
                                                               "container": "CDB1", "schemas": ["DM_CAD"]})
        by_id = {r["catalog_id"]: r for r in records}
        headers = list(etiket_sablonu.BASE) + [etiket_sablonu.STATUS]
        for n in range(1, etiket_sablonu.N_CAND + 1):
            headers += ["Aday %d" % n, "Aday %d açıklama" % n, "Aday %d iş anlamı" % n]
        headers += [etiket_sablonu.LABEL, etiket_sablonu.NOTE]
        rows, cls.counts = [], {"pozitif": 0, "yok": 0, "etiketsiz": 0}
        for request, label in zip(requests, labels):
            if label and all(c in by_id for c in label):
                kind = "etiketsiz" if cls.counts["pozitif"] >= 6 else "pozitif"
                text = "; ".join("%s.%s.%s" % (by_id[c]["schema"], by_id[c]["object"], by_id[c]["column"]) for c in label)
            elif not label:
                kind, text = "yok", "YOK"
            else:
                continue
            if cls.counts[kind] >= (6 if kind == "pozitif" else 3):
                continue
            cls.counts[kind] += 1
            rows.append([request["category"], request["name"], request["data_type"], request["description"], "Aday var"]
                        + [""] * (3 * etiket_sablonu.N_CAND) + [text if kind != "etiketsiz" else "", ""])
        cls.excel = root / "etiketleme.xlsx"
        etiket_sablonu.write(cls.excel, headers, rows)
        cls.settings = {"base_url": "https://embedding.bench.invalid", "endpoint": benchmark.ENDPOINT,
                        "model_selection": "server_default", "model": "", "model_version": "", "max_batch": 1,
                        "dimensions": getattr(benchmark.HashEmbedding(), "dimensions", 4), "request_template": {"input": "{texts}"},
                        "response_path": "data[*].embedding"}
        cls.code, cls.out, cls.raw, cls.stdout, cls.stderr = cls.run_web(root / "out", ["--build-index", "--e2e"])
        cls.report = json.loads((cls.out / "rapor.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    @classmethod
    def run_web(cls, out, extra, schemas="dm_cad", discover=None):
        import contextlib
        import io
        from copy import deepcopy
        from riskmatch import web_services
        captured, real_build = [], web_services.build_run

        def spy(raw):
            captured.append(deepcopy(raw))
            return real_build(raw)
        found = discover or {"ok": True, "message": "Doğrulandı.", "settings": dict(cls.settings),
                             "dimensions": cls.settings["dimensions"]}
        env = {"RISK_ORACLE_USER": cls.USER, "RISK_ORACLE_PASSWORD": cls.PASSWORD, "RISK_EMBEDDING_API_KEY": cls.KEY}
        stdout, stderr = io.StringIO(), io.StringIO()
        service = cls.benchmark.Service(cls.benchmark.HashEmbedding())
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env))
            stack.enter_context(patch("riskmatch.web_services.probe_oracle", return_value={
                "ok": True, "container": "CDB1", "schemas": ["DM_CAD", "DM_RISK", "OTHER"]}))
            cls.discover = stack.enter_context(patch("riskmatch.model_diagnostics.discover_model", return_value=found))
            stack.enter_context(patch("riskmatch.web_services.build_run", side_effect=spy))
            stack.enter_context(patch("riskmatch.adapters.oracle.OracleAdapter.scan", return_value=cls.snapshot))
            stack.enter_context(patch("urllib.request.build_opener", return_value=service))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            code = evaluate.main(["--excel", str(cls.excel), "--schemas", schemas, "--group-size", "20",
                                  "--resources-dir", str(Path(out).parent / "web"), "--out", str(out)] + extra)
        return code, Path(out), (captured[0] if captured else None), stdout.getvalue(), stderr.getvalue()

    def test_end_to_end_outputs_and_labels(self):
        self.assertEqual(self.code, 0, self.stderr)
        for name in ("rapor.md", "rapor.json", "iskalar.md", "katalog_ozet.md", "satirlar.json", "havuz.json"):
            self.assertTrue((self.out / name).is_file(), name)
        satir = self.report["satir"]
        self.assertEqual((satir["pozitif"], satir["yok"], satir["etiketsiz"], satir["gecersiz_etiket"]),
                         (self.counts["pozitif"], self.counts["yok"], self.counts["etiketsiz"], 0))
        self.assertEqual(len(json.loads((self.out / "satirlar.json").read_text(encoding="utf-8"))),
                         self.counts["pozitif"] + self.counts["yok"])
        self.assertEqual(self.report["ayar_kaynagi"], "web")
        self.assertEqual(self.report["kurulum"]["strateji"], "hybrid")
        self.assertIn("uctan_uca", self.report)
        models = self.report["kurulum"]["modeller"]
        self.assertEqual((models["embedding"], models["llm_yorumu"], models["search_mode"]), (True, False, "fast"))
        self.assertEqual(models["embedding_boyut"], self.settings["dimensions"])

    def test_build_run_receives_web_profile(self):
        oracle = self.raw["oracle"]
        self.assertEqual((oracle["schemas"], oracle["container"], oracle["source_database"], oracle["environment"]),
                         (["DM_CAD"], "CDB1", "EDWLIVE", "LIVE"))
        self.assertEqual((self.raw["search_mode"], self.raw["llm_review"], self.raw["use_models"]), ("fast", False, True))
        self.assertEqual(self.raw["embedding"]["api_key"], self.KEY)
        sent = self.discover.call_args[0]
        self.assertEqual(sent[1], "embedding")
        self.assertEqual(sent[0]["embedding"]["api_key"], self.KEY)

    def test_no_secrets_anywhere(self):
        texts = [p.read_text(encoding="utf-8") for p in self.out.iterdir() if p.is_file()]
        for text in texts + [self.stdout, self.stderr]:
            for leak in self.LEAKS:
                self.assertNotIn(leak, text)

    def test_inaccessible_schema_is_user_error(self):
        args = evaluate.parse_args(["--schemas", "DM_YOK", "--excel", "x.xlsx", "--out", "o"])
        env = {"RISK_ORACLE_USER": self.USER, "RISK_ORACLE_PASSWORD": self.PASSWORD}
        with patch.dict(os.environ, env), patch("riskmatch.web_services.probe_oracle", return_value={
                "ok": True, "container": "CDB1", "schemas": ["DM_CAD", "DM_RISK"]}):
            with self.assertRaisesRegex(evaluate.UserError, "DM_YOK.*DM_CAD, DM_RISK") as caught:
                evaluate.web_settings(args)
        self.assertNotIn(self.PASSWORD, str(caught.exception))

    def test_own_schema_not_listed_and_message_stays_visible(self):
        args = evaluate.parse_args(["--schemas", "DM_YOK", "--excel", "x.xlsx", "--out", "o"])
        env = {"RISK_ORACLE_USER": self.USER, "RISK_ORACLE_PASSWORD": self.PASSWORD}
        with patch.dict(os.environ, env), patch("riskmatch.web_services.probe_oracle", return_value={
                "ok": True, "container": "CDB1", "schemas": ["DM_CAD", self.USER.upper()]}):
            with self.assertRaises(evaluate.UserError) as caught:
                evaluate.web_settings(args)
        shown = evaluate.safe_message(caught.exception)
        self.assertIn("DM_CAD", shown)
        self.assertNotIn(self.USER.casefold(), shown.casefold())
        self.assertEqual(evaluate.safe_message("x " + self.USER.upper()), "ayrıntı gizlendi (bağlantı bilgisi içeriyordu)")

    def test_model_ids_reach_discovery(self):
        calls = []

        class Diagnostics:
            @staticmethod
            def discover_model(raw, name):
                calls.append(raw)
                return {"ok": True, "settings": {"endpoint": "https://model.invalid/v1/x"}}
        evaluate._discover(Diagnostics, "llm", "https://model.invalid", "", "qwen38-27b")
        evaluate._discover(Diagnostics, "embedding", "https://model.invalid", "")
        self.assertEqual(calls[0]["llm"]["model"], "qwen38-27b")
        self.assertNotIn("model", calls[1]["embedding"])
        with contextlib_quiet():
            with self.assertRaises(SystemExit):
                evaluate.parse_args(["--schemas", "A", "--llm-model", "m", "--excel", "x", "--out", "o"])
        args = evaluate.parse_args(["--schemas", "A", "--llm", "--llm-model", "m", "--embedding-model", "e",
                                    "--excel", "x", "--out", "o"])
        self.assertEqual((args.llm_model, args.embedding_model), ("m", "e"))

    def test_embedding_discovery_failure_and_lexical_fallback(self):
        failed = {"ok": False, "message": "HTTP 401: Servis kimlik doğrulaması istiyor; bu servisin API anahtarını girin.",
                  "settings": {}}
        root = Path(self.directory.name)
        code, _, _, _, stderr = self.run_web(root / "fail", [], discover=failed)
        self.assertEqual(code, 2)
        self.assertIn("Embedding servisi doğrulanamadı: HTTP 401", stderr)
        self.assertIn("--no-embedding", stderr)
        for leak in self.LEAKS:
            self.assertNotIn(leak, stderr)
        code, out, raw, _, stderr = self.run_web(root / "lex", ["--no-embedding"], discover=failed)
        self.assertEqual(code, 0, stderr)
        self.assertFalse(self.discover.called)
        self.assertFalse(raw["use_models"])
        report = json.loads((out / "rapor.json").read_text(encoding="utf-8"))
        self.assertEqual(report["kurulum"]["strateji"], "lexical")
        self.assertIn("**Embedding yok", (out / "rapor.md").read_text(encoding="utf-8"))

    def test_mode_arguments(self):
        with contextlib_quiet():
            for argv in (["--excel", "x", "--out", "o"], ["--schemas", "A", "--llm", "--no-embedding", "--excel", "x", "--out", "o"],
                         ["--config", "c.json", "--llm", "--excel", "x", "--out", "o"]):
                with self.assertRaises(SystemExit):
                    evaluate.parse_args(argv)
        args = evaluate.parse_args(["--config", "c.json", "--schemas", "dm_cad, dm_risk", "--excel", "x", "--out", "o"])
        self.assertEqual((args.web, evaluate.parse_schemas(args.schemas)), (False, ["DM_CAD", "DM_RISK"]))


def contextlib_quiet():
    import contextlib
    import io
    return contextlib.redirect_stderr(io.StringIO())


if __name__ == "__main__":
    unittest.main()
