"""Import coordinator shared by CLI and browser recorder."""
import hashlib
import json
from fnmatch import fnmatchcase
from urllib.parse import urlsplit

from .importers import parse_document


def host_matches(origin, patterns):
    host = (urlsplit(origin).hostname or '').lower()
    return not patterns or any(fnmatchcase(host, p.lower()) for p in patterns)


def ingest(catalog, document, application='', page='', action='', filename='capture.json', hosts=(), keep_paths=False, base_url='', capture_id=None):
    if capture_id is None:
        fingerprint = [document, application, page, action, filename, list(hosts), keep_paths, base_url]
        capture_id = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    items, skipped = parse_document(document, application, page, action, filename, keep_paths, base_url)
    accepted = [i for i in items if host_matches(i['origin'], hosts)]
    skipped += len(items) - len(accepted)
    return catalog.import_items(accepted, capture_id, application, page, action, filename, skipped)
