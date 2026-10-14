"""Render observed catalog, partial OpenAPI specs and non-executing examples."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .io_utils import write_json, write_text


def _cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('`', "'")


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def example(item):
    arguments = [repr(item['id'])]
    paths = {p['name']: '<' + p['name'] + '>' for p in item['parameters'] if p.get('in') == 'path'}
    queries = {p['name']: '<' + p['name'] + '>' for p in item['parameters'] if p.get('in') == 'query' and not p.get('sensitive')}
    if paths:
        arguments.append('path_params=' + repr(paths))
    if queries:
        arguments.append('query=' + repr(queries))
    if item.get('request_example') is not None:
        arguments.append('body=' + repr(item['request_example']))
    arguments.append('auth_headers=auth_headers')
    if item['method'] not in ('GET', 'HEAD', 'OPTIONS'):
        arguments.append('allow_write=True')
    return 'result = call(\n    ' + ',\n    '.join(arguments) + '\n)'


def markdown(items):
    lines = ['# API Atlas — API kataloğu', '',
             'Bu katalog gözlenen trafik ve içe aktarılan belgelerden oluşturuldu. Kapsam tam değildir; hedef API çağrıları araç tarafından yeniden çalıştırılmadı.', '',
             'Qwen açıklamaları çıkarımdır. Gözlenen alanlar gerçek istek/yanıt yapısından çıkarılır; zorunluluk ve iş anlamı ayrıca doğrulanmalıdır.', '',
             '| Uygulama | Yöntem | Uç nokta | Açıklama kaynağı | Gözlem |', '|---|---|---|---|---|']
    for item in items:
        lines.append('| ' + ' | '.join(_cell(v) for v in (item['application'], item['method'], item['path'], item['description_source'], item['observations'])) + ' |')
    for item in items:
        lines.extend(['', '## ' + item['method'] + ' ' + item['path'], '',
                      'ID: `' + item['id'] + '`', '', 'Sunucu: `' + item['origin'] + '`', '',
                      'Uygulama: ' + item['application'], '', item['title'], '', item['description'], '',
                      'Açıklama kaynağı: ' + item['description_source'] + '. Kaynaklar: ' + ', '.join(item['sources']) + '.', '',
                      'Kimlik doğrulama gözlemi: ' + ', '.join(item['auth']), '',
                      'Sayfa / işlem: ' + '; '.join(c['page'] + (' / ' + c['action'] if c['action'] else '') for c in item['contexts']), '',
                      '### Parametreler', '', '| Konum | Alan | Zorunluluk |', '|---|---|---|'])
        for p in item['parameters']:
            required = 'bilinmiyor' if p.get('required') is None else 'evet' if p['required'] else 'hayır (belge)'
            lines.append('| ' + ' | '.join(_cell(v) for v in (p.get('in'), p['name'], required)) + ' |')
        meanings = {m['path']: m['meaning'] for m in item.get('analysis', {}).get('field_meanings', [])}
        lines.extend(['', '### Yanıt alanları', '', '| Alan yolu | Tür | Qwen yorumu (çıkarım) |', '|---|---|---|'])
        for field in item.get('response_fields', []):
            lines.append('| ' + ' | '.join(_cell(v) for v in (field['path'], field['type'], meanings.get(field['path'], 'Henüz açıklanmadı'))) + ' |')
        lines.extend(['', '### Gönderilen gövde (değerler örnektir)', '', '```json', _json(item.get('request_example') if item.get('request_example') is not None else item['request_schema']), '```', '',
                      '### Yanıt şemaları', '', '```json', _json(item['response_schemas']), '```', '',
                      '### Python kullanım taslağı', '', 'Değerleri ve güncel kimlik doğrulamayı doldurun. Bu örnek çalıştırılarak doğrulanmadı.', '',
                      '```python', 'from api_client import call', 'auth_headers = {}  # Bu uygulamanın geçerli kimlik doğrulaması ve gerekiyorsa CSRF başlığı', example(item) if any(s in item['sources'] for s in ('har', 'openapi')) else '# JavaScript adayı: gerçek istekle doğrulanmadan çağrı üretilmez.', '```', '',
                      '### Kullanım notları', '', *['- ' + v for v in item['usage']], '',
                      '### Belirsizlikler', '', *['- ' + v for v in item['unknowns']], ''])
        if item.get('analysis_revision') and item['analysis_revision'] != item.get('revision'):
            lines.extend(['**Yeni gözlemler geldi; Qwen açıklaması güncel şemayla yeniden analiz edilmeli.**', ''])
    return '\n'.join(lines) + '\n'


def openapi(items):
    paths = {}
    for item in items:
        if not any(s in item['sources'] for s in ('har', 'openapi')):
            continue
        key = item['method'].lower()
        if key not in ('get', 'post', 'put', 'patch', 'delete', 'options', 'head', 'trace'):
            continue
        path = paths.setdefault(item['path'], {})
        if key in path:
            path[key].setdefault('x-atlas-variants', []).append({'id': item['id'], 'operation': item.get('operation', ''), 'request_schema': item['request_schema'], 'response_schemas': item['response_schemas']})
            continue
        operation = {'operationId': 'observed_' + item['id'], 'summary': item['title'], 'description': item['description'],
                     'tags': [item['application']], 'parameters': [], 'responses': {},
                     'x-atlas-sources': item['sources'], 'x-atlas-auth-observation': item['auth'],
                     'x-atlas-description-source': item['description_source'], 'x-atlas-executed-by-catalog': False,
                     'x-atlas-unknowns': item['unknowns']}
        if item['origin']:
            operation['servers'] = [{'url': item['origin']}]
        for p in item['parameters']:
            if p.get('in') not in ('path', 'query', 'header', 'cookie'):
                continue
            param = {'name': p['name'], 'in': p['in'], 'schema': p.get('schema', {'type': 'string'})}
            if p.get('required') is not None or p['in'] == 'path':
                param['required'] = p['in'] == 'path' or p['required']
            else:
                param['x-atlas-required-unknown'] = True
            operation['parameters'].append(param)
        if item['request_schema']:
            operation['requestBody'] = {'content': {item.get('request_media_type') or 'application/json': {'schema': item['request_schema']}}, 'x-atlas-required-unknown': True}
        for code, schema in item['response_schemas'].items():
            if not re.fullmatch(r'[1-5]\d\d|[1-5]XX|default', code):
                continue
            resp = {'description': ('Gözlenen HTTP ' if 'har' in item['sources'] else 'Belgelenen HTTP ') + code}
            if schema:
                mime = next((m for m in item.get('response_media_types', []) if 'json' in m), 'application/json')
                resp['content'] = {mime: {'schema': schema}}
            operation['responses'][code] = resp
        if not operation['responses']:
            operation['responses']['default'] = {'description': 'Yanıt sözleşmesi bilinmiyor.'}
        path[key] = operation
    return {'openapi': '3.1.0', 'info': {'title': 'API Atlas — gözlenen/belgelenen API', 'version': '0.2.0',
            'description': 'Kısmi sözleşme. Örnek trafikten tüm alanlar, zorunluluklar ve iş kuralları belirlenemez. Qwen açıklamaları çıkarımdır.'}, 'paths': paths}


def export_all(items, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'catalog.json', {'schema_version': 2, 'generated_at': datetime.now(timezone.utc).isoformat(), 'target_apis_executed': False, 'coverage': 'observed_and_documented_only', 'endpoints': items})
    write_text(output / 'catalog.md', markdown(items))
    write_text(output / 'api_client.py', (Path(__file__).parent / 'client_template.py').read_text(encoding='utf-8'))
    # Groups keep identical paths on different hosts out of the same OpenAPI document.
    groups = {}
    for item in items:
        if not any(s in item['sources'] for s in ('har', 'openapi')):
            continue
        groups.setdefault((item['application'], item['origin']), []).append(item)
    index = []
    for (app, origin), values in groups.items():
        suffix = hashlib.sha256((app + origin).encode()).hexdigest()[:10]
        name = 'openapi-' + suffix + '.json'
        write_json(output / 'openapi' / name, openapi(values))
        index.append({'application': app, 'origin': origin, 'file': 'openapi/' + name, 'endpoints': len(values)})
    write_json(output / 'openapi-index.json', index)
    if len(groups) == 1:
        write_json(output / 'openapi.json', openapi(next(iter(groups.values()))))
    elif (output / 'openapi.json').exists():
        (output / 'openapi.json').unlink()
    return {'endpoints': len(items), 'openapi_documents': len(groups), 'output': str(output)}
