import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from riskmatch.demo import demo_snapshot,DemoEmbedding
from riskmatch.errors import IntegrationError
from riskmatch.excelio import read_requests
from riskmatch.pipeline import analyze

SAMPLE=Path(__file__).resolve().parents[1]/"examples"/"sentetik_talepler.xlsx"

class PipelineTests(unittest.TestCase):
    def test_full_workbook_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            first=analyze(SAMPLE,root/"first.xlsx",state_dir=root/"state")
            second=analyze(SAMPLE,root/"second.xlsx",state_dir=root/"state")
            self.assertEqual(len(first["results"]),28)
            self.assertEqual(second["run_info"]["Devam edilen satır"],28)
            self.assertEqual(first["results"],second["results"])
            requests=read_requests(SAMPLE)
            missing=next(r["row_id"] for r in requests if r["name"]=="Havale_LW_L3M")
            ratio=next(r for r in first["results"] if r["row_id"]==missing)
            self.assertIn("missing_definition:Havale_L3M",ratio["reasons"])
            self.assertEqual(first["run_info"]["Tamamlanma"],"Tamamlandı")

    def test_partial_scope_does_not_prove_absence(self):
        snap=demo_snapshot()
        snap["complete"]=False
        snap["records"]=[]
        with tempfile.TemporaryDirectory() as directory,patch("riskmatch.demo.demo_snapshot",return_value=snap):
            result=analyze(SAMPLE,Path(directory)/"out.xlsx")
            self.assertEqual({r["physical_name"] for r in result["results"]},{"unknown"})
            self.assertNotIn("not_found_in_scope",{r["semantic_status"] for r in result["results"]})
            self.assertEqual(result["run_info"]["Tamamlanma"],"Eksik / teknik hata")

    def test_embedding_timeout_never_becomes_not_found_and_resume_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/"out.xlsx"
            with patch.object(DemoEmbedding,"embed",side_effect=IntegrationError("secret=DO_NOT_LOG")):
                failed=analyze(SAMPLE,out)
            self.assertEqual({r["semantic_status"] for r in failed["results"]},{"incomplete"})
            raw=(Path(directory)/"runtime"/"last_run.json").read_text(encoding="utf-8")
            self.assertNotIn("DO_NOT_LOG",raw)
            successful=analyze(SAMPLE,out)
            self.assertEqual(successful["run_info"]["Devam edilen satır"],0)
            self.assertEqual(successful["run_info"]["Tamamlanma"],"Tamamlandı")

    def test_changed_catalog_invalidates_resume_and_deleted_physical_column(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            first=analyze(SAMPLE,root/"out.xlsx")
            snapshot=demo_snapshot()
            snapshot["records"]=[r for r in snapshot["records"] if r["column"]!="FAIZ"]
            with patch("riskmatch.demo.demo_snapshot",return_value=snapshot):
                second=analyze(SAMPLE,root/"out.xlsx")
            self.assertEqual(second["run_info"]["Devam edilen satır"],0)
            rid=next(r["row_id"] for r in read_requests(SAMPLE) if r["name"]=="Faiz")
            self.assertEqual(next(r for r in first["results"] if r["row_id"]==rid)["physical_name"],"present")
            self.assertEqual(next(r for r in second["results"] if r["row_id"]==rid)["physical_name"],"absent_in_scope")

    def test_recapture_time_does_not_invalidate_identical_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/"out.xlsx"
            analyze(SAMPLE,out)
            snapshot=demo_snapshot()
            snapshot["captured_at"]="2026-10-12T00:00:00Z"
            for record in snapshot["records"]:
                record["captured_at"]=snapshot["captured_at"]
            with patch("riskmatch.demo.demo_snapshot",return_value=snapshot):
                rerun=analyze(SAMPLE,out)
            self.assertEqual(rerun["run_info"]["Devam edilen satır"],28)

    def test_checkpoint_recovers_after_interruption(self):
        from riskmatch.pipeline import atomic_json
        count=0
        def interrupted(path,value):
            nonlocal count
            atomic_json(path,value)
            if str(path).endswith(".json") and "checkpoints" in str(path):
                count+=1
                if count==4:raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/"out.xlsx"
            with patch("riskmatch.pipeline.atomic_json",side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):analyze(SAMPLE,out)
            result=analyze(SAMPLE,out)
            self.assertEqual(result["run_info"]["Devam edilen satır"],4)
            self.assertEqual(len(result["results"]),28)

    def test_real_scan_failure_has_no_demo_fallback(self):
        config={"mode":"real","oracle":{"source_database":"TEST","environment":"TEST","container":"TEST","schemas":["RISK"]},"embedding":{},"llm":{},"naming":{},"retrieval":{"limit":8,"expanded_limit":24}}
        with tempfile.TemporaryDirectory() as directory,patch("riskmatch.pipeline.load_config",return_value=config),patch("riskmatch.adapters.oracle.OracleAdapter") as oracle,patch("riskmatch.adapters.qwen.EmbeddingAdapter"),patch("riskmatch.adapters.qwen.LLMAdapter"),patch("riskmatch.demo.demo_snapshot",side_effect=AssertionError("Must not run")):
            oracle.return_value.scan.side_effect=IntegrationError("DB offline")
            result=analyze(SAMPLE,Path(directory)/"out.xlsx",mode="real")
            self.assertEqual({r["semantic_status"] for r in result["results"]},{"incomplete"})
            self.assertEqual({r["physical_name"] for r in result["results"]},{"unknown"})
            self.assertIn("GERÇEK",result["run_info"]["Mod"])

if __name__=="__main__":unittest.main()
