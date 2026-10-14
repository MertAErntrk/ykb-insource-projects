"""Generated API call helper. Running this file only lists catalog entries.

Fill in current auth headers and actual inputs yourself. No credentials are
captured here. POST/PUT/PATCH/DELETE require allow_write=True; no retries.
"""
import json
import re
import ssl
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call(endpoint_id, *, path_params=None, query=None, body=None, auth_headers=None,
         raw_body=None, allow_write=False, ca_bundle=None, timeout=60):
    """Call one observed/documented endpoint. HTTP/network errors propagate.

    auth_headers is supplied for THIS call/origin only. Do not share the same
    credentials across different application hosts. JSON and urlencoded bodies
    are supported; for XML/multipart/binary supply raw_body and Content-Type.
    """
    catalog = json.loads((Path(__file__).parent / 'catalog.json').read_text(encoding='utf-8'))
    matches = [i for i in catalog['endpoints'] if i['id'] == endpoint_id]
    if len(matches) != 1:
        raise ValueError('Tam endpoint id değeri catalog.json içinde bulunamadı.')
    item = matches[0]
    if not any(s in item.get('sources', []) for s in ('har', 'openapi')):
        raise ValueError('Bu yalnızca JavaScript adayı; önce gerçek istek veya belgeyle doğrulayın.')
    if not item.get('origin'):
        raise ValueError('API belgesinde sunucu adresi yok; --origin ile yeniden import edin.')
    if item['method'] not in ('GET', 'HEAD', 'OPTIONS') and not allow_write:
        raise ValueError('Bu yöntem veri değiştirebilir. Çağrıyı inceleyip allow_write=True belirtin.')
    if item['method'] in ('CONNECT', 'TRACE'):
        raise ValueError('Bu yardımcı CONNECT/TRACE çalıştırmaz.')
    path = item['path']
    supplied = path_params or {}
    for name in re.findall(r'\{([^}]+)\}', path):
        if name not in supplied:
            raise ValueError('Eksik yol parametresi: ' + name)
        value = str(supplied[name])
        if value in ('.', '..') or not value:
            raise ValueError('Geçersiz yol parametresi: ' + name)
        path = path.replace('{' + name + '}', quote(value, safe=''))
    url = item['origin'].rstrip('/') + '/' + path.lstrip('/')
    if query:
        url += '?' + urlencode(query, doseq=True)
    headers = {'Accept': 'application/json'}
    headers.update(auth_headers or {})
    media = item.get('request_media_type') or 'application/json'
    data = None
    if raw_body is not None:
        data = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode('utf-8')
        headers.setdefault('Content-Type', media)
    elif body is not None:
        if media == 'application/x-www-form-urlencoded':
            data = urlencode(body, doseq=True).encode('utf-8')
        elif 'json' in media:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        else:
            raise ValueError('Bu gövde türü için raw_body ve doğru Content-Type kullanın.')
        headers.setdefault('Content-Type', media)
    request = Request(url, data=data, headers=headers, method=item['method'])
    context = ssl.create_default_context(cafile=ca_bundle)
    opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
    with opener.open(request, timeout=timeout) as response:
        result = response.read(20_000_001)
        if len(result) > 20_000_000:
            raise ValueError('Yanıt 20 MB sınırını aştı; sayfalama kullanın.')
        if not result:
            return None
        if 'json' in response.headers.get('Content-Type', ''):
            return json.loads(result)
        return result.decode('utf-8', errors='replace')


if __name__ == '__main__':
    catalog = json.loads((Path(__file__).parent / 'catalog.json').read_text(encoding='utf-8'))
    for item in catalog['endpoints']:
        print(item['id'], item['method'], item['path'])
    print('Çağrı yapılmadı. Kullanmak için bu modülden call fonksiyonunu içe aktarın.')
