"""Live job snapshots, owner-scoped cancellation and retained partial evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from riskmatch.errors import AnalysisCancelled
from riskmatch.excelio import read_requests
from riskmatch.web import create_app


class OptimizedWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.ready=threading.Event()
        self.release=threading.Event()
        self.behavior="cancel"
        self.seen={}
        self.app=create_app(self.root,analyzer=self.analyze)
        self.client=self.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.headers={"X-CSRF-Token":session["csrf"]}
        with patch("riskmatch.web.services.probe_oracle",return_value={
                "ok":True,"db_name":"EDWLIVE","container":"PDB1","schemas":["DM_CAD"]}):
            response=self.client.post("/api/connect",json={"user":"user","password":"CREDENTIAL_SENTINEL"},headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)

    def tearDown(self):
        self.release.set()
        for job in self.app.extensions["riskmatch"]["jobs"].values():
            job["cancel_event"].set()
        self.app.extensions["riskmatch"]["executor"].shutdown(wait=True)
        self.tmp.cleanup()

    def analyze(self,path,output,**kwargs):
        self.seen["config"]=deepcopy(kwargs["config_data"])
        requests=read_requests(path,kwargs["column_mapping"])
        row={"row_id":requests[0]["row_id"],"physical_name":"present",
             "semantic_status":"insufficient","production_status":"not_evaluated",
             "reasons":["Açıklama eksik"],"errors":[],"candidates":[],
             "retrieval_warnings":["<img src=x onerror=alert(1)>"]}
        kwargs["progress"]({"stage":"Aday metinleri karşılaştırılıyor","phase":"embedding",
            "completed":0,"total":2,"items_completed":3,"items_total":24,"variable":requests[0]["name"]})
        if self.behavior!="before_first":
            kwargs["result_callback"](row)
            kwargs["result_callback"](row)
            # Callback snapshots must not alias in-progress pipeline objects.
            row["reasons"].append("NOT_PUBLISHED_YET")
        self.ready.set()
        if not self.release.wait(5):raise AssertionError("Test worker was not released")
        if kwargs["cancelled"]():raise AnalysisCancelled()
        if self.behavior=="failure":raise RuntimeError("RAW_DRIVER_CREDENTIAL_SENTINEL")
        row["production_status"]="not_producible"
        return {"results":[row],"run_info":{"Tamamlanma":"Eksik / teknik hata","Arama modu":"fast"}}

    def start(self,settings=None):
        data={"input_type":"manual","schemas":["DM_CAD"],"variables":[
            {"name":"FIRST","description":"Birinci değişken"},
            {"name":"SECOND","description":"İkinci değişken"}],"settings":settings or {}}
        response=self.client.post("/api/analyze",json=data,headers=self.headers)
        self.assertEqual(response.status_code,202,response.json)
        self.assertTrue(self.ready.wait(3))
        return response.json["id"],data

    def finish(self,identity):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            value=self.client.get("/api/jobs/"+identity).json
            if value["status"] not in ("running","cancelling"):return value
            time.sleep(.01)
        self.fail("Worker did not finish")

    def test_live_partial_results_are_annotated_and_progress_is_phase_specific(self):
        identity,_=self.start()
        response=self.client.get("/api/jobs/"+identity)
        job=response.json
        self.assertEqual(job["status"],"running")
        self.assertEqual(len(job["results"]),1)
        row=job["results"][0]
        self.assertEqual(row["request"]["name"],"FIRST")
        self.assertTrue(row["labels"]["physical_name"])
        self.assertNotIn("NOT_PUBLISHED_YET",row["reasons"])
        self.assertEqual(row["retrieval_warnings"],["<img src=x onerror=alert(1)>"])
        self.assertEqual(job["progress"]["items_total"],24)
        self.assertEqual(job["progress"]["items_completed"],3)
        self.assertEqual(job["search_mode"],"fast")
        self.assertGreaterEqual(job["elapsed_seconds"],0)
        self.assertGreaterEqual(job["last_update_seconds"],0)
        for private in ("cancel_event","partial_results","row_positions","owner","CREDENTIAL_SENTINEL"):
            self.assertNotIn(private,response.text)
        self.assertEqual(self.client.get("/api/jobs/"+identity+"/download").status_code,400)

    def test_cancel_requires_csrf_and_job_owner_then_preserves_completed_rows(self):
        identity,data=self.start()
        endpoint="/api/jobs/"+identity+"/cancel"
        self.assertEqual(self.client.post(endpoint,json={}).status_code,403)
        self.assertEqual(self.client.post(endpoint,json={},headers={**self.headers,"Origin":"https://evil.invalid"}).status_code,403)
        other=self.app.test_client();other.get("/")
        with other.session_transaction() as session:other_headers={"X-CSRF-Token":session["csrf"]}
        self.assertEqual(other.post(endpoint,json={},headers=other_headers).status_code,404)
        self.assertEqual(other.get("/api/jobs/"+identity).status_code,404)
        self.assertEqual(self.client.get("/api/jobs/"+identity).json["status"],"running")
        cancel=self.client.post(endpoint,json={},headers=self.headers)
        self.assertEqual(cancel.json["status"],"cancelling")
        self.assertEqual(self.client.post("/api/analyze",json=data,headers=self.headers).status_code,409)
        self.release.set();job=self.finish(identity)
        self.assertEqual(job["status"],"cancelled")
        self.assertEqual(len(job["results"]),1)
        self.assertNotIn("error",job)
        self.assertEqual(self.client.get("/api/jobs/"+identity+"/download").status_code,400)
        self.assertFalse((self.root/identity/"sonuclar.xlsx").exists())
        self.assertEqual(self.client.post(endpoint,json={},headers=self.headers).json["status"],"cancelled")
        self.assertEqual(self.client.get("/api/connection").json["ok"],True)

    def test_cancel_before_first_result_reports_zero_processed(self):
        self.behavior="before_first"
        identity,_=self.start()
        self.client.post("/api/jobs/"+identity+"/cancel",json={},headers=self.headers)
        self.release.set();job=self.finish(identity)
        self.assertEqual(job["status"],"cancelled")
        self.assertEqual(job["results"],[])
        self.assertEqual(job["progress"]["completed"],0)

    def test_failure_keeps_partial_evidence_without_raw_exception(self):
        self.behavior="failure"
        identity,_=self.start()
        self.release.set();job=self.finish(identity)
        self.assertEqual(job["status"],"failed")
        self.assertEqual(len(job["results"]),1)
        self.assertNotIn("RAW_DRIVER",json.dumps(job))
        self.assertNotIn("CREDENTIAL_SENTINEL",json.dumps(job))
        self.assertEqual(self.client.get("/api/jobs/"+identity+"/download").status_code,400)

    def test_finished_rows_replace_partial_snapshot_and_mode_reaches_pipeline(self):
        self.behavior="success"
        identity,_=self.start({"search_mode":"full","use_models":False,"llm_review":False})
        self.assertEqual(self.seen["config"]["retrieval"]["search_mode"],"full")
        self.release.set();job=self.finish(identity)
        self.assertEqual(job["status"],"complete")
        self.assertEqual(len(job["results"]),1)
        self.assertIn("NOT_PUBLISHED_YET",job["results"][0]["reasons"])
        self.assertEqual(job["results"][0]["production_status"],"not_producible")
        self.assertEqual(job["run_info"]["Arama modu"],"fast")
        self.assertEqual(self.client.post("/api/jobs/"+identity+"/cancel",json={},headers=self.headers).json["status"],"complete")

    def test_shell_has_accessible_analysis_choices_and_cancellation(self):
        page=self.client.get("/").text
        for value in ('id="search-mode"','value="fast" selected','id="llm-review"',
                      'id="cancel-analysis"','id="progress-time"','aria-describedby="search-mode-help"'):
            self.assertIn(value,page)
        # All dynamic warnings are added through node(), which assigns textContent.
        source=(Path(__file__).resolve().parents[1]/"src/riskmatch/static/app.js").read_text("utf-8")
        self.assertIn('el.textContent=text',source)
        self.assertIn('warnings.append(node("p",warning))',source)
        self.assertNotIn("innerHTML",source)


if __name__=="__main__":unittest.main()
