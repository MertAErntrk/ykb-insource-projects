"""Scope-keyed cache of complete Oracle catalog snapshots (metadata only)."""

import copy
import hashlib
import json
import os
import re
import tempfile
import time

# 2: şema bazlı tamlık, dışlama ve öncelik alanları eklendi; eski önbellek karışmaz.
FORMAT_VERSION = 2


def cache_key(user, dsn, container, schemas):
    """Hash the scope; user and DSN are never stored in raw form."""
    payload = {"format": FORMAT_VERSION, "user": str(user), "dsn": str(dsn), "container": str(container),
               "schemas": sorted({str(name).upper() for name in schemas})}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


class CatalogCache:
    def __init__(self, directory=None, ttl_seconds=4*3600, clock=time.time):
        self.directory = os.fspath(directory) if directory is not None else None
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._memory = {}

    def _path(self, key):
        if self.directory is None or not re.fullmatch(r"[0-9a-f]{64}", str(key)):
            return None
        return os.path.join(self.directory, key + ".json")

    def _load(self, key):
        entry = self._memory.get(key)
        if entry is not None:
            return entry
        path = self._path(key)
        if path is None:
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                entry = json.load(handle)
        except (OSError, ValueError):
            return None
        if (not isinstance(entry, dict) or not isinstance(entry.get("snapshot"), dict)
                or not isinstance(entry.get("stored_at"), (int, float))):
            return None
        self._memory[key] = entry
        return entry

    def _fresh(self, key):
        entry = self._load(key)
        if entry is None:
            return None, None
        age = self.clock() - entry["stored_at"]
        if age < 0 or age > self.ttl_seconds:
            return None, None
        return entry, age

    def get(self, key, signature=None):
        entry, _ = self._fresh(key)
        if entry is None:
            return None
        if signature is not None and entry.get("signature") != signature:
            return None
        return copy.deepcopy(entry["snapshot"])

    def put(self, key, snapshot, signature=None):
        if not isinstance(snapshot, dict) or not snapshot.get("complete"):
            return False
        entry = {"stored_at": self.clock(), "signature": signature, "snapshot": snapshot}
        try:
            text = json.dumps(entry, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return False
        path = self._path(key)
        if path is not None:
            temp = None
            try:
                os.makedirs(self.directory, exist_ok=True)
                descriptor, temp = tempfile.mkstemp(dir=self.directory, prefix=key + ".", suffix=".tmp")
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(text)
                os.replace(temp, path)
                temp = None
            except Exception:
                return False
            finally:
                if temp is not None:
                    try:
                        os.unlink(temp)
                    except OSError:
                        pass
        self._memory[key] = json.loads(text)
        return True

    def invalidate(self, key):
        self._memory.pop(key, None)
        path = self._path(key)
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                pass

    def info(self, key):
        entry, age = self._fresh(key)
        if entry is None:
            return None
        return {"age_s": age, "signature": entry.get("signature"),
                "records": len(entry["snapshot"].get("records") or [])}
