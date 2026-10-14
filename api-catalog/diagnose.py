"""Share-friendly runtime / optional Qwen probe; no endpoint/key output."""

import argparse
import json
import platform
import struct
from pathlib import Path

from catalog_app.qwen import Settings, probe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qwen", action="store_true")
    args = parser.parse_args()
    output = {"python": platform.python_version(), "bits": struct.calcsize("P") * 8, "system": platform.system(), "python_supported": tuple(map(int, platform.python_version_tuple()[:2])) >= (3, 9)}
    if args.qwen:
        settings = Settings(Path(__file__).resolve().parent / "data" / "settings.local.json")
        try:
            output["qwen"] = probe(settings.snapshot())
        except ValueError as exc:
            output["qwen"] = {"ok": False, "error": str(exc)}
        except OSError:
            output["qwen"] = {"ok": False, "error": "Yerel sertifika/ayar dosyası okunamadı."}
    print(json.dumps(output, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
