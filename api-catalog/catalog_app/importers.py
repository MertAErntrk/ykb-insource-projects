"""Deterministic HAR and OpenAPI JSON import; no network calls."""

import hashlib
import json
import re
from urllib.parse import urlsplit

from .privacy import SECRET_NAME, clean_text, masked_example, normalized_url, safe_schema, schema_of

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def endpoint_id(item):
    canonical_path = re.sub(r"\{[^}]+\}", "{}", item["path"])
    key = [item["application"], item["origin"], item["method"], canonical_path, item.get("operation", "")]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24]


def _auth(headers):
    names = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in headers if isinstance(h, dict)}
    kinds = []
    authorization = names.get("authorization", "")
    if authorization:
        scheme = authorization.split(" ", 1)[0].lower()
        kinds.append({"bearer": "Bearer", "basic": "Basic", "negotiate": "SSO/Negotiate", "ntlm": "NTLM"}.get(scheme, "Authorization başlığı"))
    if "cookie" in names:
        kinds.append("Cookie/oturum")
    if any("csrf" in k or "xsrf" in k for k in names):
        kinds.append("CSRF başlığı")
    if any("api-key" in k or "apikey" in k for k in names):
        kinds.append("API anahtarı")
    return kinds or ["Belirlenemedi; sanitized HAR başlıkları kaldırmış olabilir"]


def _json_body(text):
    if not isinstance(text, str) or len(text) > 500_000:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _operation(body):
    if not isinstance(body, dict) or not isinstance(body.get("query"), str):
        return "", ""
    query = body["query"]
    match = re.search(r"\b(query|mutation|subscription)\s+(\w+)", query)
    name = body.get("operationName") or (match.group(2) if match else "")
    kind = match.group(1) if match else "query" if query.lstrip().startswith("{") else "bilinmiyor"
    # Arbitrary user strings must not become operation identifiers.
    name = name if isinstance(name, str) and re.fullmatch(r"[A-Za-z_]\w{0,100}", name) else ""
    if not name:
        root = re.search(r"\{\s*(\w+)", query)
        name = root.group(1) if root else "unnamed"
    return "graphql:" + kind + ":" + name, kind


def import_har(document, application, page, action, filename):
    entries = document.get("log", {}).get("entries")
    if not isinstance(entries, list):
        raise ValueError("HAR içinde log.entries listesi bulunamadı.")
    if len(entries) > 20_000:
        raise ValueError("Tek seferde en fazla 20.000 istek alınabilir; kaydı bölün.")
    pages = {p.get("id"): clean_text(p.get("title"), 200) for p in document.get("log", {}).get("pages", []) if isinstance(p, dict)}
    items, skipped = [], 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            skipped += 1
            continue
        request, response = entry.get("request", {}), entry.get("response", {})
        if not isinstance(request, dict) or not isinstance(response, dict):
            skipped += 1
            continue
        method = str(request.get("method", "")).upper()
        content = response.get("content") or {}
        if not isinstance(content, dict):
            content = {}
        try:
            origin, path, path_params, query = normalized_url(request.get("url", ""))
        except ValueError:
            skipped += 1
            continue
        mime = str(content.get("mimeType", "")).lower()
        resource_type = str(entry.get("_resourceType", "")).lower()
        api_hint = any(v in path.lower().split("/") for v in ("api", "rest", "graphql", "services"))
        if method not in METHODS or method == "OPTIONS" or not (api_hint or "json" in mime or resource_type in ("fetch", "xhr")):
            skipped += 1
            continue
        body_info = request.get("postData") or {}
        body = _json_body(body_info.get("text")) if isinstance(body_info, dict) else None
        if body is None and isinstance(body_info, dict) and isinstance(body_info.get("params"), list):
            body = {str(p.get("name")): str(p.get("value", "")) for p in body_info["params"] if isinstance(p, dict)}
        operation, operation_kind = _operation(body)
        parameters = path_params[:]
        query_names = set()
        for name, value in query:
            if name not in query_names:
                parameters.append({"name": name, "in": "query", "schema": {"type": "string"}, "required": None, "sensitive": bool(SECRET_NAME.search(name))})
                query_names.add(name)
        for param in request.get("queryString") or []:
            if isinstance(param, dict) and str(param.get("name")) not in query_names:
                name = str(param.get("name"))
                parameters.append({"name": name, "in": "query", "schema": {"type": "string"}, "required": None, "sensitive": bool(SECRET_NAME.search(name))})
                query_names.add(name)
        result_body = _json_body(content.get("text")) if content.get("encoding") != "base64" else None
        status = response.get("status", 0)
        context_page = clean_text(page or pages.get(entry.get("pageref")) or "Sayfa belirtilmedi", 200)
        item = {
            "application": clean_text(application, 100), "origin": origin, "path": path,
            "method": method, "operation": operation, "operation_kind": operation_kind,
            "title": "Gözlenen " + method + " çağrısı", "description": "Açıklama henüz oluşturulmadı.",
            "description_source": "pending", "parameters": parameters,
            "request_schema": schema_of(body) if body is not None else {},
            "request_example": masked_example(body) if body is not None else None,
            "response_schemas": {str(status): schema_of(result_body) if result_body is not None else {}},
            "auth": _auth(request.get("headers") or []),
            "contexts": [{"page": context_page, "action": clean_text(action, 200), "file": clean_text(filename, 120)}],
            "evidence": [{"source": "har", "entry": index, "status": status, "page": context_page}],
            "sources": ["har"], "observations": 1, "usage": [],
            "unknowns": ["İşlem etiketi bağlamdır; düğme ile doğrudan ilişki ayrıca incelenmeli.", "Parametre zorunluluğu ve tüm hata durumları tek kayıttan belirlenemez."],
        }
        item["id"] = endpoint_id(item)
        items.append(item)
    return items, skipped


def _local_ref(value, document):
    if not isinstance(value, dict) or not value.get("$ref"):
        return value if isinstance(value, dict) else {}
    ref = value["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return {}
    node = document
    try:
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node if isinstance(node, dict) else {}
    except (KeyError, TypeError):
        return {}


def import_openapi(document, application, page, action, filename):
    if not isinstance(document.get("paths"), dict):
        raise ValueError("OpenAPI/Swagger içinde paths nesnesi bulunamadı.")
    is_v2 = str(document.get("swagger", "")).startswith("2")
    default_servers = document.get("servers") or []
    default_url = default_servers[0].get("url", "") if default_servers else ""
    if is_v2:
        default_url = (document.get("schemes") or ["https"])[0] + "://" + str(document.get("host", "")) + str(document.get("basePath", ""))
    items = []
    for path, path_item in document["paths"].items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.upper() not in METHODS or not isinstance(operation, dict):
                continue
            server_list = operation.get("servers") or path_item.get("servers") or default_servers
            url = server_list[0].get("url", "") if server_list else default_url
            parsed = urlsplit(url)
            if parsed.scheme in ("http", "https") and parsed.hostname:
                origin, prefix, _, _ = normalized_url(url)
                actual_path = prefix.rstrip("/") + "/" + str(path).lstrip("/")
            else:
                origin, actual_path = "", str(path)
            parameters, request_schema = [], {}
            for raw in (path_item.get("parameters") or []) + (operation.get("parameters") or []):
                parameter = _local_ref(raw, document)
                if parameter.get("in") == "body":
                    request_schema = safe_schema(parameter.get("schema", {}), document)
                    continue
                if parameter.get("name"):
                    parameters.append({"name": str(parameter["name"]), "in": parameter.get("in", "query"), "schema": safe_schema(parameter.get("schema") or {"type": parameter.get("type", "string")}, document), "required": bool(parameter.get("required", False)), "sensitive": bool(SECRET_NAME.search(str(parameter["name"])))})
            request_body = _local_ref(operation.get("requestBody", {}), document)
            media = request_body.get("content") or {}
            if media:
                request_schema = safe_schema(next(iter(media.values())).get("schema", {}), document)
            responses = {}
            for code, raw in (operation.get("responses") or {}).items():
                response = _local_ref(raw, document)
                media = response.get("content") or {}
                schema = next(iter(media.values())).get("schema", {}) if media else response.get("schema", {})
                responses[str(code)] = safe_schema(schema, document)
            security = operation.get("security", document.get("security", []))
            auth = [str(name) for requirement in security for name in requirement] if isinstance(security, list) else []
            item = {
                "application": clean_text(application, 100), "origin": origin, "path": actual_path,
                "method": method.upper(), "operation": "", "operation_kind": "",
                "title": clean_text(operation.get("summary") or operation.get("operationId") or method.upper() + " " + path, 250),
                "description": clean_text(operation.get("description") or operation.get("summary"), 3000),
                "description_source": "document", "parameters": parameters, "request_schema": request_schema,
                "request_example": None, "response_schemas": responses,
                "auth": auth or ["Belgede belirtilmedi / kimlik doğrulama gerekmeyebilir"],
                "contexts": [{"page": clean_text(page or "API belgesi", 200), "action": clean_text(action, 200), "file": clean_text(filename, 120)}],
                "evidence": [{"source": "openapi", "operation_id": clean_text(operation.get("operationId"), 150), "page": clean_text(page or "API belgesi", 200)}],
                "sources": ["openapi"], "observations": 0, "usage": [],
                "unknowns": ["Belgede olması, bu kurulumda erişilebilir veya çalıştırılarak doğrulanmış olduğu anlamına gelmez."],
            }
            item["id"] = endpoint_id(item)
            items.append(item)
    return items, 0


def parse_document(document, application, page="", action="", filename="capture.json"):
    if not isinstance(document, dict):
        raise ValueError("Dosya bir JSON nesnesi içermeli.")
    if not str(application).strip():
        raise ValueError("Uygulama adı gerekli.")
    if "log" in document:
        return import_har(document, application, page, action, filename)
    if "openapi" in document or "swagger" in document:
        return import_openapi(document, application, page, action, filename)
    raise ValueError("HAR veya OpenAPI/Swagger JSON dosyası bekleniyor.")
