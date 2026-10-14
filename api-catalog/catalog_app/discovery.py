"""Static URL candidates from already-loaded scripts. No candidate is called."""
import re
from urllib.parse import urljoin, urlsplit

from .importers import endpoint_id
from .privacy import clean_text, normalized_url, page_context

CALL = re.compile(r'''\b(?:(?:axios|api|http|client)\.(get|post|put|patch|delete|head|options)|(?P<fetch>fetch))\s*\(\s*(['"`])([^'"`\n]{1,500})\3''', re.I)
LITERAL = re.compile(r'''(['"`])((?:https?://[^/'"`\s]+)?/(?:[^'"`\s]{0,200}/)?(?:api|rest|graphql|services)(?:/[^'"`\s]{0,400})?)\1''', re.I)
ASSET = re.compile(r'\.(?:js|css|png|jpe?g|svg|woff2?|ico|map|html?)(?:$|\?)', re.I)


def script_candidates(text, script_url, page='', application=''):
    if not isinstance(text, str) or len(text) > 2_000_000:
        return []
    found = [(m.group(4), m.group(1).upper() if m.group(1) else 'UNKNOWN') for m in CALL.finditer(text)]
    found += [(m.group(2), 'UNKNOWN') for m in LITERAL.finditer(text)]
    items, seen = [], set()
    known = {literal for literal, method in found if method != 'UNKNOWN'}
    for literal, method in found[:1000]:
        if method == 'UNKNOWN' and literal in known:
            continue
        if not literal.startswith(('/', 'http://', 'https://')) or literal.startswith('//') or ASSET.search(literal):
            continue
        literal = re.sub(r'\$\{[^}]+\}', '{param}', literal)
        try:
            origin, path, parameters, query = normalized_url(urljoin(script_url, literal))
        except ValueError:
            continue
        if (origin, path, method) in seen:
            continue
        seen.add((origin, path, method))
        # Template variables stay candidates and are not executable examples.
        for name in re.findall(r'\{([^}]+)\}', path):
            if not any(p['name'] == name for p in parameters):
                parameters.append({'name': name, 'in': 'path', 'schema': {'type': 'string'}, 'required': True, 'inferred_name': True})
        parameters += [{'name': name, 'in': 'query', 'schema': {'type': 'string'}, 'required': None} for name, _ in query]
        item = {'application': clean_text(application or urlsplit(origin).netloc, 100), 'origin': origin, 'path': path,
                'method': method, 'operation': '', 'operation_kind': '', 'title': 'JavaScript içinde bulunan API adayı',
                'description': 'Yüklenen JavaScript dosyasında adres metni bulundu. Gerçekte çağrıldığı, tam adres olduğu ve bu kurulumda erişilebilir olduğu doğrulanmadı.',
                'description_source': 'candidate', 'parameters': parameters, 'request_schema': {}, 'request_example': None,
                'request_media_type': '', 'response_schemas': {}, 'response_media_types': [], 'response_fields': [], 'request_header_names': [],
                'response_body_observed': False, 'effect': 'unknown', 'auth': ['Bilinmiyor'],
                'contexts': [{'page': page_context(page), 'action': '', 'file': 'loaded-javascript'}],
                'evidence': [{'source': 'javascript', 'script': page_context(script_url)}], 'sources': ['javascript'], 'observations': 0, 'usage': [],
                'unknowns': ['Statik adres adayı; yöntem/parametreler eksik olabilir. Bu adres otomatik olarak çağrılmadı.']}
        item['id'] = endpoint_id(item)
        items.append(item)
    return items
