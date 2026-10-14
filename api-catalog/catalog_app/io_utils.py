"""Bounded JSON reads and atomic UTF-8 writes."""
import json
import os
import tempfile
from pathlib import Path


def read_json(path, max_bytes=100_000_000):
    with Path(path).open('rb') as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError('Dosya boyut sınırını aşıyor; kaydı küçük parçalara bölün.')
    return json.loads(raw.decode('utf-8-sig'))


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.atlas-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')
