"""Contract-level tests: import/export, model HTTP, generated calls, browser scan."""
import ast
import asyncio
import base64
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog_app.cli import main
from catalog_app.discovery import script_candidates
from catalog_app.exporters import export_all, openapi
from catalog_app.importers import parse_document
from catalog_app.privacy import page_context
from catalog_app.recorder import response_entry, request_context
from catalog_app.qwen import describe, load_config, probe
from catalog_app.service import ingest
from catalog_app.store import Catalog


def entry(url='https://helpdesk.example.test/uplift/api/tickets/I12345678', method='GET', response=None, body=None):
    return {'_resourceType': 'fetch', '_page': 'https://helpdesk.example.test/uplift/#!/tickets/I12345678?secret=SECRET_PAGE',
            'request': {'url': url + '?page=0&token=SECRET_QUERY', 'method': method,
                        'headers': [{'name': 'Authorization', 'value': 'Bearer SECRET_AUTH'}, {'name': 'Cookie', 'value': 'sid=SECRET_COOKIE'}],
                        'postData': {'mimeType': 'application/json', 'text': json.dumps(body)} if body is not None else {}},
            'response': {'status': 200, 'content': {'mimeType': 'application/json', 'text': json.dumps(response if response is not None else {'records': [{'openedBy': 'SECRET_PERSON', 'customer': {'cif': 'SECRET_CIF'}, 'description': 'SECRET_DESC'}], 'total': 1})}}}


def har(*entries):
    return {'log': {'entries': list(entries)}}


class Handler(BaseHTTPRequestHandler):
    events = []
    model_payloads = []
    model_answer = {'title': 'Takım kayıtları', 'description': 'Çıkarım: takım kayıtlarını listeler.', 'usage': ['Sayfalamayı kontrol edin.'], 'unknowns': ['Takım filtresi doğrulanmalı.'], 'field_meanings': [{'path': '$.records[].customer.cif', 'meaning': 'Çıkarım: müşteri CIF bilgisi'}, {'path': '$.invented', 'meaning': 'Uydurulan alan kabul edilmemeli'}]}

    def log_message(self, *args):
        pass

    def send(self, body, mime='application/json', code=200):
        body = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.events.append(('GET', self.path, self.headers.get('Authorization')))
        if self.path.startswith('/api/tickets'):
            self.send(json.dumps({'records': [{'openedBy': 'SECRET_PERSON', 'customer': {'cif': 'SECRET_CIF'}, 'description': 'SECRET_DESC'}]}))
        elif self.path == '/view':
            self.send('<html><script src="/app.js"></script><body>Kayıtlar</body></html>', 'text/html')
        elif self.path == '/app.js':
            self.send('fetch("/api/tickets?team=SECRET_TEAM"); function assign(){ return axios.post("/api/tickets/assign", {}); } fetch("/openapi.json");', 'application/javascript')
        elif self.path == '/openapi.json':
            self.send(json.dumps({'openapi': '3.0.0', 'servers': [{'url': '/'}], 'paths': {'/api/comments': {'post': {'summary': 'Yorum ekler', 'responses': {'201': {'description': 'Created'}}}}}}))
        elif self.path.startswith('/redirect'):
            self.send_response(302)
            self.send_header('Location', '/should-not-be-called')
            self.end_headers()
        else:
            self.send('{}')

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
        self.events.append(('POST', self.path, self.headers.get('Authorization')))
        if self.path in ('/v1/chat/completions', '/ask', '/bad'):
            self.model_payloads.append(data)
            answer = json.dumps(self.model_answer, ensure_ascii=False)
            if self.path == '/bad':
                answer = 'not JSON'
            result = {'answer': answer} if self.path == '/ask' else {'choices': [{'message': {'content': '<think>private reasoning</think>```json\n' + answer + '\n```'}}]}
            self.send(json.dumps(result))
        else:
            self.send(json.dumps({'ok': True, 'body': data}))


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        Handler.events, Handler.model_payloads = [], []
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.catalog = Catalog(self.root / 'catalog.sqlite3')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main([*args, '--data-dir', str(self.root / 'data'), '--output', str(self.root / 'out')])


class ImportWorkflowTests(ServerCase):
    def test_cli_import_search_show_export_and_privacy(self):
        path = self.root / 'capture.har'
        path.write_text(json.dumps(har(entry())), encoding='utf-8')
        self.assertEqual(self.cli('import', str(path), '--app', 'Çözüm', '--action', 'Yeni gelen kayıtlar'), 0)
        data = json.loads((self.root / 'out/catalog.json').read_text())
        item = data['endpoints'][0]
        self.assertEqual(item['path'], '/uplift/api/tickets/{id}')
        self.assertIn('$.records[].customer.cif', [f['path'] for f in item['response_fields']])
        self.assertIsNone(next(p['required'] for p in item['parameters'] if p['name'] == 'page'))
        for output in (self.root / 'out').rglob('*'):
            if output.is_file():
                self.assertNotIn('SECRET_', output.read_text(encoding='utf-8'), str(output))
        self.assertNotIn(b'SECRET_', (self.root / 'data/catalog.sqlite3').read_bytes())
        self.assertFalse(data['target_apis_executed'])
        self.assertEqual(self.cli('search', 'cif'), 0)
        self.assertEqual(self.cli('show', item['id'][:12]), 0)
        self.assertEqual(Handler.events, [])

    def test_duplicates_merge_ids_but_keep_methods_and_hosts_separate(self):
        capture = har(entry(), entry(url='https://helpdesk.example.test/uplift/api/tickets/I87654321'), entry(method='POST', body={'comment': 'SECRET_COMMENT'}), entry(url='https://other.example.test/uplift/api/tickets/I12345678'))
        result = ingest(self.catalog, capture, action='İşlem')
        self.assertEqual(len(self.catalog.list()), 3)
        item = next(i for i in self.catalog.list() if i['origin'] == 'https://helpdesk.example.test' and i['method'] == 'GET')
        self.assertEqual(item['observations'], 2)
        self.assertTrue(ingest(self.catalog, capture, action='İşlem')['duplicate'])
        self.assertEqual(next(i for i in self.catalog.list() if i['method'] == 'POST')['effect'], 'unknown')

    def test_base64_form_and_missing_response(self):
        item = entry(method='POST')
        item['request']['postData'] = {'mimeType': 'application/x-www-form-urlencoded', 'text': 'team=SECRET_TEAM&status=ACTIVE'}
        item['response']['content']['text'] = base64.b64encode(b'{"count":0,"attmntCount":0}').decode()
        item['response']['content']['encoding'] = 'base64'
        parsed = parse_document(har(item))[0][0]
        self.assertEqual(parsed['request_schema']['properties']['team']['type'], 'string')
        self.assertEqual([f['path'] for f in parsed['response_fields']], ['$.count', '$.attmntCount'])
        item['response']['content'] = {'mimeType': 'application/json'}
        self.assertFalse(parse_document(har(item))[0][0]['response_body_observed'])

    def test_docs_merge_with_observed_preserving_named_path_and_required_params(self):
        ingest(self.catalog, har(entry()))
        doc = {'openapi': '3.0.0', 'servers': [{'url': 'https://helpdesk.example.test/uplift/api'}], 'paths': {'/tickets/{ticketNo}': {'get': {'summary': 'Talep detayı', 'parameters': [{'in': 'path', 'name': 'ticketNo', 'required': True, 'schema': {'type': 'string'}}], 'responses': {'200': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Ticket'}}}}}}}}, 'components': {'schemas': {'Ticket': {'type': 'object', 'properties': {'description': {'type': 'string', 'example': 'SECRET_DOC'}}}}}}
        ingest(self.catalog, doc)
        self.assertEqual(len(self.catalog.list()), 1)
        item = self.catalog.list()[0]
        self.assertEqual(item['path'], '/uplift/api/tickets/{ticketNo}')
        self.assertEqual(item['description_source'], 'document')
        self.assertIn('har', item['sources'])
        self.assertNotIn('SECRET_DOC', json.dumps(item))
        self.assertEqual([p['name'] for p in item['parameters'] if p['in'] == 'path'], ['ticketNo'])

    def test_host_filter_and_page_fragment_masking(self):
        ingest(self.catalog, har(entry(), entry(url='https://other.example.test/api/tickets')), hosts=['helpdesk.example.test'])
        self.assertEqual(len(self.catalog.list()), 1)
        self.assertNotIn('I12345678', page_context(entry()['_page']))
        self.assertNotIn('SECRET_PAGE', page_context(entry()['_page']))

    def test_multihost_export_and_candidates_do_not_create_fake_operations(self):
        ingest(self.catalog, har(entry(), entry(url='https://other.example.test/uplift/api/tickets/I12345678')))
        candidates = script_candidates('axios.post("/api/assign", {}); const endpoint="/api/hidden";', 'https://helpdesk.example.test/app.js')
        self.assertTrue(candidates)
        self.catalog.import_items(candidates, 'js-test', '', '', '', '', 0)
        export_all(self.catalog.list(), self.root / 'out')
        self.assertEqual(len(json.loads((self.root / 'out/openapi-index.json').read_text())), 2)
        self.assertFalse((self.root / 'out/openapi.json').exists())
        spec = openapi(candidates)
        self.assertEqual(spec['paths'], {})

    def test_schema_revision_invalidates_cached_analysis(self):
        ingest(self.catalog, har(entry()))
        item = self.catalog.list()[0]
        self.catalog.update_description(item['id'], 'Çıkarım', source='qwen', expected_revision=item['revision'])
        ingest(self.catalog, har(entry(response={'newField': 1})), filename='new.har')
        changed = self.catalog.get(item['id'])
        self.assertNotEqual(changed['revision'], changed['analysis_revision'])
        with self.assertRaises(ValueError):
            self.catalog.update_description(item['id'], 'Eski analiz', expected_revision=item['revision'])


    def test_documented_path_name_survives_later_traffic(self):
        doc = {'openapi': '3.0.0', 'servers': [{'url': 'https://helpdesk.example.test/uplift/api'}], 'paths': {'/tickets/{ticketNo}': {'get': {'parameters': [{'in': 'path', 'name': 'ticketNo', 'required': True, 'schema': {'type': 'string'}}, {'in': 'query', 'name': 'page', 'required': True, 'schema': {'type': 'integer'}}], 'responses': {}}}}}
        ingest(self.catalog, doc)
        ingest(self.catalog, har(entry()))
        item = self.catalog.list()[0]
        self.assertEqual([p['name'] for p in item['parameters'] if p['in'] == 'path'], ['ticketNo'])
        self.assertTrue(next(p['required'] for p in item['parameters'] if p['name'] == 'page'))
        self.assertEqual(item['path'], '/uplift/api/tickets/{ticketNo}')

    def test_candidate_upgraded_when_real_call_is_observed(self):
        candidates = script_candidates('axios.get("/uplift/api/tickets/I12345678")', 'https://helpdesk.example.test/app.js')
        self.catalog.import_items(candidates, 'candidate', '', '', '', '', 0)
        ingest(self.catalog, har(entry()))
        item = next(i for i in self.catalog.list() if i['method'] == 'GET')
        self.assertEqual(item['description_source'], 'pending')
        self.assertIn('har', item['sources'])
        self.assertEqual(item['observations'], 1)


class ModelWorkflowTests(ServerCase):
    def test_plain_openai_http_no_tools_no_raw_values_and_field_projection(self):
        config = {'protocol': 'openai', 'endpoint': self.origin + '/v1', 'model': 'qwen-test'}
        item = parse_document(har(entry()))[0][0]
        result = describe(config, item)
        payload = Handler.model_payloads[-1]
        self.assertNotIn('tools', payload)
        self.assertFalse(payload['stream'])
        self.assertNotIn('SECRET_', json.dumps(payload))
        self.assertEqual(len(result['analysis']['field_meanings']), 1)
        self.assertFalse(result['analysis']['verified'])
        self.assertNotIn('path', result)

    def test_custom_qa_json_template_works_without_openai_api(self):
        config = {'protocol': 'json_template', 'endpoint': self.origin + '/ask', 'request_template': {'question': '$combined_prompt'}, 'response_path': '/answer'}
        item = parse_document(har(entry()))[0][0]
        self.assertIn('description', describe(config, item))
        self.assertIn('question', Handler.model_payloads[-1])
        self.assertNotIn('messages', Handler.model_payloads[-1])

    def test_invalid_model_result_does_not_change_catalog(self):
        ingest(self.catalog, har(entry()))
        before = self.catalog.list()[0]
        with self.assertRaises(ValueError):
            describe({'protocol': 'json_template', 'endpoint': self.origin + '/bad', 'request_template': {}, 'response_path': '/choices/0/message/content'}, before)
        self.assertEqual(self.catalog.list()[0], before)

    def test_cli_analyze_cache_avoids_repeat_calls(self):
        path = self.root / 'capture.har'
        path.write_text(json.dumps(har(entry())))
        config = self.root / 'qwen.local.json'
        config.write_text(json.dumps({'endpoint': self.origin + '/v1', 'model': 'qwen-test'}))
        self.assertEqual(self.cli('import', str(path), '--config', str(config)), 0)
        self.assertEqual(len(Handler.model_payloads), 1)
        self.assertEqual(self.cli('analyze', '--config', str(config)), 0)
        self.assertEqual(len(Handler.model_payloads), 1)
        self.assertEqual(self.cli('analyze', '--config', str(config), '--force'), 0)
        self.assertEqual(len(Handler.model_payloads), 2)

    def test_config_secrets_are_environment_only(self):
        path = self.root / 'config.json'
        path.write_text(json.dumps({'endpoint': self.origin, 'api_key': 'SECRET'}))
        with self.assertRaises(ValueError):
            load_config(path)


class ClientWorkflowTests(ServerCase):
    def client(self, entries):
        ingest(self.catalog, har(*entries))
        export_all(self.catalog.list(), self.root / 'out')
        spec = importlib.util.spec_from_file_location('generated_api_client', self.root / 'out/api_client.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_generated_client_reads_and_writes_only_when_explicit(self):
        module = self.client([entry(url=self.origin + '/api/tickets/I12345678'), entry(url=self.origin + '/api/comment', method='POST', body={'text': 'SECRET_COMMENT'})])
        get = next(i for i in self.catalog.list() if i['method'] == 'GET')
        post = next(i for i in self.catalog.list() if i['method'] == 'POST')
        result = module.call(get['id'], path_params={'id': 'I99999999'}, query={'team': 'A'}, auth_headers={'Authorization': 'Bearer EXPLICIT_AUTH'})
        self.assertIn('records', result)
        before = len(Handler.events)
        with self.assertRaises(ValueError):
            module.call(post['id'], body={'text': 'test'})
        self.assertEqual(len(Handler.events), before)
        self.assertTrue(module.call(post['id'], body={'text': 'test'}, allow_write=True)['ok'])
        output = subprocess.check_output([sys.executable, str(self.root / 'out/api_client.py')], text=True)
        self.assertIn('Çağrı yapılmadı', output)
        self.assertEqual(len(Handler.events), before + 1)

    def test_redirect_does_not_forward_auth_to_another_route(self):
        module = self.client([entry(url=self.origin + '/redirect')])
        with self.assertRaises(HTTPError):
            module.call(self.catalog.list()[0]['id'], auth_headers={'Authorization': 'Bearer SECRET_AUTH'})
        self.assertEqual([e[1] for e in Handler.events], ['/redirect'])


@unittest.skipUnless(os.environ.get('ATLAS_BROWSER_TEST') == '1', 'Gerçek Chromium testi için ATLAS_BROWSER_TEST=1')
class BrowserWorkflowTests(ServerCase):
    def test_url_scan_captures_calls_and_finds_uninvoked_action_candidate(self):
        extra = ['--executable-path', os.environ['ATLAS_TEST_EXECUTABLE']] if os.environ.get('ATLAS_TEST_EXECUTABLE') else []
        status = self.cli('scan', self.origin + '/view', '--headless', '--channel', 'chromium', '--wait', '2', *extra)
        self.assertEqual(status, 0)
        data = json.loads((self.root / 'out/catalog.json').read_text())
        self.assertTrue(any('har' in i['sources'] and i['path'] == '/api/tickets' for i in data['endpoints']))
        self.assertTrue(any('javascript' in i['sources'] and i['path'] == '/api/tickets/assign' for i in data['endpoints']))
        self.assertTrue(any('openapi' in i['sources'] and i['path'] == '/api/comments' for i in data['endpoints']))
        self.assertFalse(any(event[0] == 'POST' for event in Handler.events))
        self.assertNotIn('SECRET_', (self.root / 'out/catalog.json').read_text())



class RecorderAdapterTests(unittest.TestCase):
    def test_completed_response_preserves_method_body_and_page_context(self):
        class Response:
            status = 200
            async def all_headers(self): return {'content-type': 'application/json'}
            async def body(self): return b'{"count":0,"attmntCount":0}'
        class Request:
            url = 'https://helpdesk.example.test/api/parent-childs/I12345678/parent-count'
            method = 'GET'
            resource_type = 'fetch'
            post_data = None
            async def response(self): return Response()
            async def all_headers(self): return {'cookie': 'SECRET_COOKIE'}
        context = {'page': 'Detay', 'action': 'Kaydı aç', 'started': '2026-10-14T20:00:00Z'}
        captured = asyncio.run(response_entry(Request(), context))
        item = parse_document(har(captured))[0][0]
        self.assertEqual(item['path'], '/api/parent-childs/{id}/parent-count')
        self.assertEqual(item['contexts'][0]['action'], 'Kaydı aç')
        self.assertEqual([f['path'] for f in item['response_fields']], ['$.count', '$.attmntCount'])
        self.assertNotIn('SECRET_COOKIE', json.dumps(item))

class CompatibilityTests(unittest.TestCase):
    def test_core_python39_syntax(self):
        root = Path(__file__).resolve().parents[1]
        for path in [root / 'app.py', *list((root / 'catalog_app').glob('*.py'))]:
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path), feature_version=(3, 9))


if __name__ == '__main__':
    unittest.main()
