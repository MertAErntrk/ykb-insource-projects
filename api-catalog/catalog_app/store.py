"""SQLite persistence for already-sanitized catalog entries."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from .privacy import clean_text


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _union(left, right, limit=200):
    result = left[:]
    for item in right:
        if item not in result:
            result.append(item)
    return result[-limit:]


def _schema_union(left, right):
    if not left:
        return right
    if not right or left == right:
        return left
    return {"anyOf": _union(left.get("anyOf", [left]), right.get("anyOf", [right]), 10)}


def revision(item):
    fields = {key: item.get(key) for key in ("method", "path", "operation", "parameters", "request_schema", "response_schemas", "auth", "contexts", "request_media_type", "request_header_names")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Catalog:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS endpoints (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS captures (id TEXT PRIMARY KEY, application TEXT, page TEXT, action TEXT, filename TEXT, created_at TEXT, accepted INTEGER, skipped INTEGER);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def get(self, item_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM endpoints WHERE id=?", (item_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, query="", application="", page="", action=""):
        with self.connect() as db:
            rows = db.execute("SELECT data FROM endpoints ORDER BY id").fetchall()
        results = []
        for row in rows:
            item = json.loads(row[0])
            if application and item["application"] != application:
                continue
            if page and not any(c["page"] == page for c in item["contexts"]):
                continue
            if action and not any(c["action"] == action for c in item["contexts"]):
                continue
            haystack = json.dumps({k: item.get(k) for k in ("application", "path", "method", "operation", "title", "description", "contexts", "usage", "parameters", "response_fields", "analysis")}, ensure_ascii=False).casefold()
            if query and query.casefold() not in haystack:
                continue
            results.append(item)
        return sorted(results, key=lambda i: (i["application"], i["path"], i["method"], i["operation"]))

    def import_items(self, items, capture_id, application, page, action, filename, skipped):
        added, updated = 0, 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT id FROM captures WHERE id=?", (capture_id,)).fetchone():
                return {"added": 0, "updated": 0, "accepted": 0, "skipped": skipped, "duplicate": True}
            for incoming in items:
                row = db.execute("SELECT data FROM endpoints WHERE id=?", (incoming["id"],)).fetchone()
                if row:
                    item = json.loads(row[0])
                    if "openapi" in incoming["sources"]:
                        item["path"] = incoming["path"]
                        item["parameters"] = [p for p in item["parameters"] if p.get("in") != "path"]
                        for parameter in incoming["parameters"]:
                            item["parameters"] = [p for p in item["parameters"] if (p.get("in"), p.get("name")) != (parameter.get("in"), parameter.get("name"))]
                    # A documented parameter name/required flag outranks HAR inference.
                    incoming_params = incoming["parameters"]
                    if "openapi" in item["sources"] and "openapi" not in incoming["sources"]:
                        incoming_params = [p for p in incoming_params if p.get("in") != "path"]
                    parameters = {(p.get("in"), p.get("name")): p for p in item["parameters"]}
                    for parameter in incoming_params:
                        key = (parameter.get("in"), parameter.get("name"))
                        if key not in parameters or "openapi" in incoming["sources"]:
                            parameters[key] = parameter
                    item["parameters"] = list(parameters.values())
                    for key in ("sources", "auth", "contexts", "evidence", "unknowns", "response_fields", "request_header_names", "response_media_types"):
                        item[key] = _union(item.get(key, []), incoming.get(key, []))
                    item["response_body_observed"] = item.get("response_body_observed", False) or incoming.get("response_body_observed", False)
                    if incoming.get("request_media_type"):
                        item["request_media_type"] = incoming["request_media_type"]
                    item["request_schema"] = _schema_union(item["request_schema"], incoming["request_schema"])
                    for status, schema in incoming["response_schemas"].items():
                        item["response_schemas"][status] = _schema_union(item["response_schemas"].get(status, {}), schema)
                    item["observations"] += incoming["observations"]
                    if item["request_example"] is None:
                        item["request_example"] = incoming["request_example"]
                    if item["description_source"] == "candidate" and "har" in incoming["sources"]:
                        item["title"], item["description"], item["description_source"] = incoming["title"], incoming["description"], incoming["description_source"]
                    if incoming["description_source"] == "document" and item["description_source"] != "document":
                        item["title"], item["description"], item["description_source"] = incoming["title"], incoming["description"], "document"
                    updated += 1
                else:
                    item = incoming.copy()
                    item["created_at"] = now()
                    added += 1
                item["updated_at"] = now()
                item["revision"] = revision(item)
                db.execute("INSERT OR REPLACE INTO endpoints VALUES (?,?)", (item["id"], json.dumps(item, ensure_ascii=False)))
            db.execute("INSERT INTO captures VALUES (?,?,?,?,?,?,?,?)", (capture_id, application, page, action, filename, now(), len(items), skipped))
        return {"added": added, "updated": updated, "accepted": len(items), "skipped": skipped, "duplicate": False}

    def update_description(self, item_id, description, title=None, source="manual", usage=None, unknowns=None, expected_revision=None, analysis=None):
        with self.connect() as db:
            row = db.execute("SELECT data FROM endpoints WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise ValueError("API bulunamadı.")
            item = json.loads(row[0])
            if expected_revision and item["revision"] != expected_revision:
                raise ValueError("Analiz sırasında kayıt değişti; güncel kayıt için yeniden deneyin.")
            item["description"] = clean_text(description, 4000)
            if title:
                item["title"] = clean_text(title, 250)
            item["description_source"] = source
            if usage is not None:
                item["usage"] = [clean_text(v, 1000) for v in usage[:12]]
            if unknowns is not None:
                item["unknowns"] = _union(item["unknowns"], [clean_text(v, 1000) for v in unknowns[:12]], 20)
            if source == "qwen":
                item["analysis_revision"] = item["revision"]
                item["analysis"] = analysis or {}
            item["updated_at"] = now()
            db.execute("UPDATE endpoints SET data=? WHERE id=?", (json.dumps(item, ensure_ascii=False), item_id))
        return item

    def metadata(self):
        items = self.list()
        apps = {}
        for item in items:
            apps[item["application"]] = apps.get(item["application"], 0) + 1
        with self.connect() as db:
            captures = db.execute("SELECT application,page,action,filename,created_at,accepted,skipped FROM captures ORDER BY created_at DESC LIMIT 30").fetchall()
        return {
            "total": len(items), "applications": apps,
            "pages": sorted({c["page"] for i in items for c in i["contexts"]}),
            "actions": sorted({c["action"] for i in items for c in i["contexts"] if c["action"]}),
            "observed": sum("har" in i["sources"] for i in items),
            "described": sum(i["description_source"] != "pending" for i in items),
            "captures": [dict(zip(("application", "page", "action", "file", "created_at", "accepted", "skipped"), row)) for row in captures],
        }
