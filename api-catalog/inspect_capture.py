"""Produce a shareable structural summary without field values/auth secrets."""

import argparse
import json
from pathlib import Path

from catalog_app.importers import parse_document


def main():
    parser = argparse.ArgumentParser(description="HAR/OpenAPI kaydından maskeli API özeti çıkar")
    parser.add_argument("file", type=Path)
    parser.add_argument("--application", default="Uygulama")
    parser.add_argument("--page", default="İncelenen sayfa")
    parser.add_argument("--action", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.file.stat().st_size > 20 * 1024 * 1024:
        raise SystemExit("Dosya 20 MB sınırını aşıyor; kaydı bölün.")
    document = json.loads(args.file.read_text(encoding="utf-8-sig"))
    items, skipped = parse_document(document, args.application, args.page, args.action, "capture.json")
    for item in items:
        item["origin"] = "[kurum içi adres]"
    text = json.dumps({"endpoints": items, "skipped": skipped, "note": "Alan değerleri ve auth sırları kaldırıldı; alan adları ve açıklamaları paylaşmadan önce inceleyin."}, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print("Maskeli özet hazır. Paylaşmadan önce dosyayı inceleyin.")
    else:
        print(text)


if __name__ == "__main__":
    main()
