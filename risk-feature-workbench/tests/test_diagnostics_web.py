"""The browser endpoints expose partial failures and never save failed logins."""
import json
import unittest
from unittest.mock import patch
import test_web
from test_adapters import FakeConnection
from riskmatch.adapters.oracle import OracleAdapter


class DiagnosticsWebTests(unittest.TestCase):
    setUp=test_web.WebTests.setUp
    tearDown=test_web.WebTests.tearDown
    connect=test_web.WebTests.connect

    def test_timeout_returns_prior_passed_steps_without_accepting_login(self):
        connection=FakeConnection()
        connection.cur.fetchmany=lambda count:(_ for _ in ()).throw(RuntimeError("DPY-4024 secret=DO_NOT_EXPOSE"))
        with patch.object(OracleAdapter,"_connect",return_value=connection):
            response=self.client.post("/api/connect",json={"user":"user","password":"PRIVATE_VALUE"},headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json["ok"])
        self.assertTrue(response.json["connected"])
        self.assertEqual(response.json["checks"][0]["status"],"passed")
        self.assertEqual(response.json["checks"][-1]["code"],"DPY-4024")
        self.assertNotIn("DO_NOT_EXPOSE",response.text)
        self.assertNotIn("PRIVATE_VALUE",response.text)
        self.assertFalse(self.client.get("/api/connection").json["ok"])
        self.assertTrue(connection.closed)

    def test_selected_schema_endpoint_uses_session_credentials_and_validates_selection(self):
        # İstemcinin "schemas" alanı yok sayılır; her zaman bağlantıdaki sabit scan_schemas test edilir.
        seen=[]
        def diagnose(config,credentials,schemas):
            seen.append((credentials["user"],credentials["dsn"],schemas))
            return {"ok":True,"checks":[{"name":"schema", "status":"passed", "elapsed_ms":1,"message":"visible"}]}
        with patch("riskmatch.web.diagnose_selected_schemas",side_effect=diagnose):
            result=self.client.post("/api/diagnostics/oracle-schemas",json={"schemas":["RISK_DWH"]},headers=self.headers)
            self.assertTrue(result.json["ok"])
            ignored=self.client.post("/api/diagnostics/oracle-schemas",json={"schemas":["NOT_LISTED"]},headers=self.headers)
            empty=self.client.post("/api/diagnostics/oracle-schemas",json={},headers=self.headers)
        self.assertEqual((ignored.status_code,empty.status_code),(200,200))
        self.assertEqual([row[2] for row in seen],[["RISK_DWH"]]*3)
        self.assertEqual(seen[0][0],"test-user")
        self.assertNotIn("SECRET_SENTINEL",result.text)
        self.assertTrue(self.client.get("/api/connection").json["ok"])

    def test_model_endpoint_uses_discovery_even_without_full_url(self):
        report={"ok":False,"checks":[{"name":"TLS", "status":"failed","elapsed_ms":4,"code":"TLS_CERTIFICATE","message":"certificate"}]}
        with patch("riskmatch.web.discover_model",return_value=report) as probe:
            result=self.client.post("/api/probe/llm",json={"llm":{},"embedding":{"request_template":"invalid"}},headers=self.headers)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json,report)
        self.assertEqual(probe.call_args.args[1],"llm")

    def test_diagnostics_require_csrf_and_connection(self):
        self.assertEqual(self.client.post("/api/diagnostics/oracle-schemas",json={"schemas":["RISK_DWH"]}).status_code,403)
        self.client.post("/api/disconnect",json={},headers=self.headers)
        self.assertEqual(self.client.post("/api/diagnostics/oracle-schemas",json={"schemas":["RISK_DWH"]},headers=self.headers).status_code,400)


if __name__=="__main__":unittest.main()
