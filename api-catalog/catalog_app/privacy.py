"""Keep field names and types, never captured credentials or business values."""

import re
from urllib.parse import parse_qsl, urlsplit

SECRET_NAME = re.compile(r"authorization|cookie|token|secret|password|passwd|api.?key|csrf|xsrf|session", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
PHONE = re.compile(r"(?<!\w)(?:\+?90\s*)?0?5\d{2}[\s()-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\w)")
OPAQUE = re.compile(r"^(?:\d{2,}|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}|[A-Za-z0-9_-]{40,})$")


def clean_text(value, limit=2000):
    text = str(value or "")[:limit]
    text = EMAIL.sub("[e-posta]", text)
    text = JWT.sub("[token]", text)
    text = PHONE.sub("[telefon]", text)
    return re.sub(r"(?i)(bearer\s+)[^\s\"',;]+", r"\1[maskeli]", text)


def schema_of(value, depth=0):
    if depth > 15:
        return {}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, dict):
        return {"type": "object", "properties": {str(k): schema_of(v, depth + 1) for k, v in list(value.items())[:150]}}
    if isinstance(value, list):
        variants = []
        for item in value[:10]:
            schema = schema_of(item, depth + 1)
            if schema not in variants:
                variants.append(schema)
        return {"type": "array", "items": variants[0] if len(variants) == 1 else {"anyOf": variants} if variants else {}}
    return {"type": "string"}


def masked_example(value, depth=0):
    if depth > 10:
        return "[derin yapı]"
    if isinstance(value, dict):
        return {str(k): "[gizli]" if SECRET_NAME.search(str(k)) else masked_example(v, depth + 1) for k, v in list(value.items())[:60]}
    if isinstance(value, list):
        return [masked_example(v, depth + 1) for v in value[:2]]
    if value is None:
        return None
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0
    return "[değer]"


def normalized_url(url):
    parsed = urlsplit(str(url))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("İstek adresi geçerli bir HTTP/HTTPS adresi olmalı.")
    host = parsed.hostname
    if ":" in host:
        host = "[" + host + "]"
    if parsed.port:
        host += ":" + str(parsed.port)
    origin = parsed.scheme + "://" + host
    segments, parameters = [], []
    for segment in (parsed.path or "/").split("/"):
        if OPAQUE.fullmatch(segment) or "%40" in segment.lower() or "@" in segment:
            name = "id" if not parameters else "id" + str(len(parameters) + 1)
            segments.append("{" + name + "}")
            parameters.append({"name": name, "in": "path", "schema": {"type": "string"}, "required": True, "inferred_name": True})
        else:
            segments.append(segment)
    return origin, "/".join(segments), parameters, parse_qsl(parsed.query, keep_blank_values=True)


def safe_schema(schema, document, depth=0, seen=()):
    """Resolve local references only. Omit examples, defaults and remote refs."""
    if not isinstance(schema, dict) or depth > 15:
        return {}
    ref = schema.get("$ref")
    if ref:
        if not str(ref).startswith("#/") or ref in seen:
            return {"description": "Çözümlenmemiş şema referansı"}
        node = document
        try:
            for part in ref[2:].split("/"):
                node = node[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            return {}
        return safe_schema(node, document, depth + 1, seen + (ref,))
    result = {}
    for key in ("type", "format", "nullable", "minimum", "maximum", "minLength", "maxLength"):
        if key in schema:
            result[key] = schema[key]
    if isinstance(schema.get("required"), list):
        result["required"] = [str(v) for v in schema["required"][:150]]
    if "description" in schema:
        result["description"] = clean_text(schema["description"], 800)
    if isinstance(schema.get("properties"), dict):
        result["properties"] = {str(k): safe_schema(v, document, depth + 1, seen) for k, v in list(schema["properties"].items())[:150]}
    if "items" in schema:
        result["items"] = safe_schema(schema["items"], document, depth + 1, seen)
    for key in ("allOf", "anyOf", "oneOf"):
        if isinstance(schema.get(key), list):
            result[key] = [safe_schema(v, document, depth + 1, seen) for v in schema[key][:10]]
    return result
