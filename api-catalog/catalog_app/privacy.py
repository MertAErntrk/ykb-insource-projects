"""Retain field names/types, discard captured credentials and business values."""
import re
from urllib.parse import parse_qsl, unquote, urlsplit

SECRET_NAME = re.compile(r'authorization|cookie|token|secret|password|passwd|api.?key|csrf|xsrf|session', re.I)
EMAIL = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
JWT = re.compile(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b')
PHONE = re.compile(r'(?<!\w)(?:\+?90\s*)?0?5\d{2}[\s()-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\w)')
OPAQUE = re.compile(r'^(?:\d+|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}|[A-Za-z]{1,12}[-_]?\d{3,}|[A-Za-z0-9_-]{32,})$')


def clean_text(value, limit=2000):
    text = str(value or '')[:limit]
    text = EMAIL.sub('[e-posta]', text)
    text = JWT.sub('[token]', text)
    text = PHONE.sub('[telefon]', text)
    return re.sub(r'(?i)(bearer\s+)[^\s\"\x27,;]+', r'\1[maskeli]', text)


def schema_of(value, depth=0):
    if depth > 12:
        return {'x-atlas-truncated': True}
    if value is None:
        return {'type': 'null'}
    if isinstance(value, bool):
        return {'type': 'boolean'}
    if isinstance(value, int):
        return {'type': 'integer'}
    if isinstance(value, float):
        return {'type': 'number'}
    if isinstance(value, dict):
        properties = {str(k): schema_of(v, depth + 1) for k, v in list(value.items())[:200]}
        result = {'type': 'object', 'properties': properties}
        if len(value) > 200:
            result['x-atlas-truncated'] = True
        return result
    if isinstance(value, list):
        variants = []
        for item in value[:20]:
            schema = schema_of(item, depth + 1)
            if schema not in variants:
                variants.append(schema)
        result = {'type': 'array', 'items': variants[0] if len(variants) == 1 else {'anyOf': variants} if variants else {}}
        if len(value) > 20:
            result['x-atlas-sampled'] = True
        return result
    return {'type': 'string'}


def masked_example(value, depth=0):
    if depth > 10:
        return '[derin yapı]'
    if isinstance(value, dict):
        return {str(k): '[gizli]' if SECRET_NAME.search(str(k)) else masked_example(v, depth + 1) for k, v in list(value.items())[:100]}
    if isinstance(value, list):
        return [masked_example(v, depth + 1) for v in value[:2]]
    if value is None:
        return None
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0
    return '[değer]'


def normalize_path(path, keep_paths=False):
    segments, parameters = [], []
    for segment in path.split('/'):
        decoded = unquote(segment)
        dynamic = OPAQUE.fullmatch(decoded) or '@' in decoded or JWT.search(decoded)
        if dynamic and not keep_paths:
            name = 'id' if not parameters else 'id' + str(len(parameters) + 1)
            segments.append('{' + name + '}')
            parameters.append({'name': name, 'in': 'path', 'schema': {'type': 'string'}, 'required': True, 'inferred_name': True})
        else:
            segments.append(clean_text(segment, 500))
    return '/'.join(segments), parameters


def normalized_url(url, keep_paths=False):
    parsed = urlsplit(str(url))
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('Geçerli HTTP/HTTPS adresi bekleniyor.')
    host = parsed.hostname
    if ':' in host:
        host = '[' + host + ']'
    if parsed.port:
        host += ':' + str(parsed.port)
    origin = parsed.scheme + '://' + host
    path, parameters = normalize_path(parsed.path or '/', keep_paths)
    return origin, path, parameters, parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=1000)


def page_context(value):
    if not value:
        return ''
    if str(value).startswith(('https://', 'http://')):
        try:
            origin, path, _, query = normalized_url(value)
            fragment = urlsplit(value).fragment
            route = normalize_path(fragment.split('?', 1)[0])[0] if fragment else ''
            return origin + path + ('?' + '&'.join(k + '=[değer]' for k, _ in query) if query else '') + ('#' + route if route else '')
        except ValueError:
            return '[sayfa adresi çözümlenemedi]'
    return clean_text(value, 250)


def field_paths(schema, prefix='$', depth=0):
    if not isinstance(schema, dict) or depth > 15:
        return []
    fields = []
    for key, child in schema.get('properties', {}).items():
        path = prefix + '.' + key
        fields.append({'path': path, 'type': child.get('type', 'unknown')})
        fields.extend(field_paths(child, path, depth + 1))
    if 'items' in schema:
        fields.extend(field_paths(schema['items'], prefix + '[]', depth + 1))
    for kind in ('anyOf', 'allOf', 'oneOf'):
        for variant in schema.get(kind, []):
            for field in field_paths(variant, prefix, depth + 1):
                if field not in fields:
                    fields.append(field)
    return fields


def safe_schema(schema, document, depth=0, seen=()):
    if not isinstance(schema, dict) or depth > 12:
        return {}
    ref = schema.get('$ref')
    if ref:
        if not str(ref).startswith('#/') or ref in seen:
            return {'x-atlas-unresolved-ref': True}
        node = document
        try:
            for part in ref[2:].split('/'):
                node = node[part.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError):
            return {}
        return safe_schema(node, document, depth + 1, seen + (ref,))
    result = {k: schema[k] for k in ('type', 'format', 'nullable', 'minimum', 'maximum', 'minLength', 'maxLength') if k in schema}
    if isinstance(schema.get('required'), list):
        result['required'] = [str(v) for v in schema['required'][:200]]
    if 'description' in schema:
        result['description'] = clean_text(schema['description'], 800)
    if isinstance(schema.get('properties'), dict):
        result['properties'] = {str(k): safe_schema(v, document, depth + 1, seen) for k, v in list(schema['properties'].items())[:200]}
    if 'items' in schema:
        result['items'] = safe_schema(schema['items'], document, depth + 1, seen)
    for key in ('allOf', 'anyOf', 'oneOf'):
        if isinstance(schema.get(key), list):
            result[key] = [safe_schema(v, document, depth + 1, seen) for v in schema[key][:20]]
    return result
