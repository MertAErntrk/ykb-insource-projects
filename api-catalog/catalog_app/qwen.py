"""Plain HTTP question/answer adapter; never uses tools, CLI agents or eval."""
import json
import os
import re
import ssl
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .io_utils import read_json
from .privacy import clean_text

SYSTEM = (Path(__file__).parent / 'prompts' / 'analysis.md').read_text(encoding='utf-8')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def chat_url(value):
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Qwen URL geçerli bir HTTP/HTTPS adresi olmalı; kimlik bilgileri URL içinde olmamalı.')
    path = parsed.path.rstrip('/') or '/v1'
    if not path.endswith('/chat/completions'):
        path += '/chat/completions'
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def load_config(path=None):
    config = read_json(path, 100_000) if path else {}
    if not isinstance(config, dict):
        raise ValueError('Qwen ayarları JSON nesnesi olmalı.')
    if any(k in config for k in ('api_key', 'password', 'token', 'headers')):
        raise ValueError('Anahtarları dosyaya yazmayın; api_key_env / headers_from_env kullanın.')
    config.setdefault('protocol', 'openai')
    config['endpoint'] = os.environ.get('QWEN_ENDPOINT') or os.environ.get('QWEN_BASE_URL') or config.get('endpoint', '')
    config['model'] = os.environ.get('QWEN_MODEL') or config.get('model', '')
    config['ca_bundle'] = os.environ.get('QWEN_CA_BUNDLE') or config.get('ca_bundle', '')
    config.setdefault('api_key_env', 'QWEN_API_KEY')
    config.setdefault('timeout', 90)
    config.setdefault('max_input_chars', 24000)
    config.setdefault('max_tokens', 1800)
    if config['protocol'] not in ('openai', 'json_template'):
        raise ValueError('Qwen protocol: openai veya json_template olmalı.')
    if not 5 <= float(config['timeout']) <= 600:
        raise ValueError('timeout 5–600 saniye arasında olmalı.')
    if not 2000 <= int(config['max_input_chars']) <= 100000:
        raise ValueError('max_input_chars 2000–100000 arasında olmalı.')
    if config['protocol'] == 'json_template' and ('request_template' not in config or not config.get('response_path')):
        raise ValueError('json_template için request_template ve JSON Pointer response_path gerekli.')
    return config


def _replace(value, variables):
    if isinstance(value, str):
        return variables.get(value, value)
    if isinstance(value, dict):
        return {k: _replace(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace(v, variables) for v in value]
    return value


def _pointer(value, pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('response_path /answer veya /choices/0/message/content gibi JSON Pointer olmalı.')
    for part in pointer[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def complete(config, prompt, system=SYSTEM):
    if not config.get('endpoint'):
        raise ValueError('QWEN_ENDPOINT veya config içindeki endpoint ayarlanmalı.')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    key = os.environ.get(config.get('api_key_env', 'QWEN_API_KEY'), '')
    if key:
        headers['Authorization'] = 'Bearer ' + key
    for name, env in config.get('headers_from_env', {}).items():
        value = os.environ.get(env)
        if value:
            headers[str(name)] = value
    if config.get('protocol', 'openai') == 'openai':
        url = chat_url(config['endpoint'])
        if not config.get('model'):
            raise ValueError('OpenAI biçimi için QWEN_MODEL veya config model alanı gerekli.')
        payload = {'model': config['model'], 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}], 'temperature': 0.1, 'max_tokens': config.get('max_tokens', 1800), 'stream': False}
        pointer = '/choices/0/message/content'
    else:
        url = config['endpoint']
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Qwen endpoint URL geçersiz.')
        payload = _replace(config['request_template'], {'$prompt': prompt, '$system': system, '$model': config.get('model', ''), '$combined_prompt': system + '\n\n' + prompt})
        pointer = config['response_path']
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers=headers, method='POST')
    context = ssl.create_default_context(cafile=config.get('ca_bundle') or None)
    opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=float(config.get('timeout', 90))) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError('Model yanıtı boyut sınırını aştı.')
    except HTTPError as exc:
        raise ValueError('Qwen HTTP ' + str(exc.code) + '; endpoint, protokol, model ve kimlik doğrulamayı kontrol edin. Yönlendirmeler izlenmez.') from None
    except (URLError, OSError, TimeoutError):
        raise ValueError('Qwen bağlantısı kurulamadı; ağ/proxy ve kurumsal CA ayarını kontrol edin.') from None
    try:
        text = _pointer(json.loads(raw), pointer)
        if isinstance(text, list):
            text = '\n'.join(v.get('text', '') for v in text if isinstance(v, dict))
        if not isinstance(text, str) or not text.strip():
            raise ValueError()
        return re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError('Qwen yanıtı beklenen JSON alanında metin içermiyor; response_path/protokol ayarını kontrol edin.') from None


def probe(config):
    started = time.monotonic()
    complete(config, 'Yalnızca TAMAM yaz.', system='Kısa yanıt ver.')
    return {'ok': True, 'latency_seconds': round(time.monotonic() - started, 2)}


def describe(config, item):
    evidence = {k: item.get(k) for k in ('application', 'method', 'path', 'operation', 'parameters', 'request_schema', 'response_schemas', 'response_fields', 'auth', 'contexts', 'sources', 'effect')}
    # No hostnames, filenames, actual scalar values or auth values are needed.
    evidence['contexts'] = [{'action': c.get('action', '')} for c in item.get('contexts', [])]
    evidence['existing_description'] = item.get('description') if item.get('description_source') == 'document' else ''
    raw = json.dumps(evidence, ensure_ascii=False)
    if len(raw) > int(config.get('max_input_chars', 24000)):
        raise ValueError('API şeması model girdi sınırını aşıyor; analiz atlandı, katalogda tam gözlem korunuyor.')
    text = complete(config, raw)
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    try:
        result = json.loads(text)
    except ValueError:
        raise ValueError('Qwen geçerli JSON açıklaması döndürmedi; katalog değiştirilmedi.') from None
    if not isinstance(result, dict) or not isinstance(result.get('description'), str) or not result['description'].strip():
        raise ValueError('Qwen yanıtında açıklama bulunamadı.')
    for key in ('usage', 'unknowns', 'field_meanings'):
        if not isinstance(result.get(key, []), list):
            raise ValueError('Qwen kullanım/belirsizlik/alan açıklamaları liste olmalı.')
    for key in ('usage', 'unknowns'):
        if not all(isinstance(v, str) for v in result.get(key, [])):
            raise ValueError('Qwen kullanım ve belirsizlik listeleri metin içermeli.')
    allowed = {f['path'] for f in item.get('response_fields', [])}
    meanings = []
    for field in result.get('field_meanings', [])[:100]:
        if isinstance(field, dict) and field.get('path') in allowed and isinstance(field.get('meaning'), str):
            meanings.append({'path': field['path'], 'meaning': clean_text(field['meaning'], 500), 'source': 'qwen_inference'})
    return {'description': clean_text(result['description'], 4000), 'title': clean_text(result.get('title'), 250), 'usage': result.get('usage', []), 'unknowns': result.get('unknowns', []), 'analysis': {'field_meanings': meanings, 'model': clean_text(config.get('model', ''), 200), 'verified': False}}
