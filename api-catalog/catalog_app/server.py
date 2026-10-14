"""Loopback-only stdlib server. Never sends requests to cataloged APIs."""

import hashlib
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .importers import parse_document
from .privacy import clean_text
from .qwen import Settings, describe, probe
from .store import Catalog, markdown_export

MAX_BODY = 25 * 1024 * 1024
ROOT = Path(__file__).resolve().parent.parent


class App:
    def __init__(self, data_dir):
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.catalog = Catalog(data_dir / "catalog.sqlite3")
        self.settings = Settings(data_dir / "settings.local.json")
        self.token = secrets.token_urlsafe(32)

    def import_capture(self, data):
        document = data.get("document")
        application = clean_text(data.get("application", ""), 100)
        page = clean_text(data.get("page", ""), 200)
        action = clean_text(data.get("action", ""), 200)
        filename = clean_text(str(data.get("filename", "capture.json")).replace("\\", "/").rsplit("/", 1)[-1], 120)
        items, skipped = parse_document(document, application, page, action, filename)
        allowed_host = str(data.get("allowed_host", "")).strip().lower()
        if allowed_host:
            if "://" in allowed_host:
                allowed_host = urlsplit(allowed_host).hostname or ""
            filtered = [v for v in items if not v["origin"] or (urlsplit(v["origin"]).hostname or "").lower() == allowed_host]
            skipped += len(items) - len(filtered)
            items = filtered
        fingerprint = json.dumps([application, page, action, allowed_host, document], ensure_ascii=False, sort_keys=True)
        capture_id = hashlib.sha256(fingerprint.encode()).hexdigest()
        return self.catalog.import_items(items, capture_id, application, page, action, filename, skipped)


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalAPICatalog/" + __version__

        def log_message(self, fmt, *args):
            # Search text and imported metadata need not appear in console logs.
            pass

        def _trusted(self, mutation=False):
            host = urlsplit("http://" + self.headers.get("Host", ""))
            if host.hostname not in ("127.0.0.1", "localhost") or host.port != self.server.server_port:
                return False
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.port != self.server.server_port:
                    return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return False
            return not mutation or secrets.compare_digest(self.headers.get("X-Catalog-Token", ""), app.token)

        def _send(self, payload, status=200, content_type="application/json; charset=utf-8", filename=None):
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False).encode()
            elif isinstance(payload, str):
                payload = payload.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
            self.end_headers()
            self.wfile.write(payload)

        def _json_body(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Geçersiz istek boyutu.")
            if not 0 < size <= MAX_BODY:
                raise ValueError("Dosya/istek 25 MB sınırını aşıyor veya boş.")
            try:
                data = json.loads(self.rfile.read(size).decode("utf-8-sig"))
            except (ValueError, UnicodeError):
                raise ValueError("Geçerli JSON gönderilmeli.") from None
            if not isinstance(data, dict):
                raise ValueError("JSON nesnesi bekleniyor.")
            return data

        def _filters(self, parsed):
            values = parse_qs(parsed.query)
            return {"query": values.get("q", [""])[0], "application": values.get("application", [""])[0], "page": values.get("page", [""])[0], "action": values.get("action", [""])[0]}

        def do_GET(self):
            try:
                if not self._trusted():
                    self._send({"error": "Yalnızca yerel uygulama üzerinden erişilebilir."}, 403)
                    return
                parsed = urlsplit(self.path)
                static = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/styles.css": ("styles.css", "text/css")}
                if parsed.path in static:
                    filename, mime = static[parsed.path]
                    self._send((ROOT / "web" / filename).read_bytes(), content_type=mime + "; charset=utf-8")
                elif parsed.path == "/api/bootstrap":
                    self._send({"token": app.token, "version": __version__})
                elif parsed.path == "/api/meta":
                    self._send(app.catalog.metadata())
                elif parsed.path == "/api/catalog":
                    self._send(app.catalog.list(**self._filters(parsed)))
                elif parsed.path == "/api/settings":
                    self._send(app.settings.public())
                elif parsed.path.startswith("/api/endpoints/"):
                    item = app.catalog.get(parsed.path.rsplit("/", 1)[-1])
                    self._send(item if item else {"error": "API bulunamadı."}, 200 if item else 404)
                elif parsed.path in ("/api/export.json", "/api/export.md"):
                    items = app.catalog.list(**self._filters(parsed))
                    if parsed.path.endswith(".md"):
                        self._send(markdown_export(items), content_type="text/markdown; charset=utf-8", filename="api-katalogu.md")
                    else:
                        self._send({"version": __version__, "target_apis_executed": False, "endpoints": items}, filename="api-katalogu.json")
                else:
                    self._send({"error": "Adres bulunamadı."}, 404)
            except (ValueError, TypeError):
                self._send({"error": "Geçersiz istek."}, 400)

        def do_POST(self):
            try:
                if not self._trusted(mutation=True):
                    self._send({"error": "Yerel oturum doğrulanamadı; sayfayı yenileyin."}, 403)
                    return
                path = urlsplit(self.path).path
                data = self._json_body()
                if path == "/api/import":
                    self._send(app.import_capture(data))
                elif path == "/api/demo":
                    doc = json.loads((ROOT / "examples" / "demo-capture.json").read_text(encoding="utf-8"))
                    self._send(app.import_capture({"document": doc, "application": "Örnek Portal", "page": "Talep ekranları", "action": "Örnek gezinme", "filename": "demo-capture.json"}))
                elif path == "/api/settings":
                    self._send(app.settings.update(data))
                elif path == "/api/qwen/probe":
                    self._send(probe(app.settings.snapshot()))
                elif path.startswith("/api/endpoints/") and path.endswith("/analyze"):
                    item_id = path.split("/")[-2]
                    item = app.catalog.get(item_id)
                    if not item:
                        self._send({"error": "API bulunamadı."}, 404)
                        return
                    if not data.get("force") and item.get("analysis_revision") == item.get("revision"):
                        self._send({"cached": True, "item": item})
                        return
                    result = describe(app.settings.snapshot(), item)
                    updated = app.catalog.update_description(item_id, source="qwen", expected_revision=item["revision"], **result)
                    self._send({"cached": False, "item": updated})
                elif path.startswith("/api/endpoints/") and path.endswith("/note"):
                    item_id = path.split("/")[-2]
                    self._send(app.catalog.update_description(item_id, str(data.get("description", "")), title=data.get("title")))
                else:
                    self._send({"error": "Adres bulunamadı."}, 404)
            except ValueError as exc:
                self._send({"error": clean_text(str(exc), 800)}, 400)
            except (TypeError, KeyError, AttributeError, RecursionError):
                self._send({"error": "Dosya yapısı beklenen biçimde değil. HAR/OpenAPI JSON kaydını kontrol edin."}, 400)
            except OSError:
                self._send({"error": "Yerel dosya veya sertifika okunamadı; ayarları ve yazma erişimini kontrol edin."}, 400)

    return Handler


def make_server(data_dir, port=8788):
    app = App(data_dir)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(app))
    server.daemon_threads = True
    return server, app
