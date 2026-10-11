"""Development benchmark on the labelled SYNTHETIC catalog (tests/benchmark_fixture.py).

Runs the real analyze() path with Oracle and the HTTP model service replaced in-process, then reports
Recall, Hit@3, Precision@3, first-correct rank, false suggestions on YOK rows, timings, call counts and
order consistency. Results describe this synthetic set only; they are not bank accuracy.

Embedding backends: hash (deterministic character n-grams, no dependency), e5 (dev-only fastembed
multilingual-e5-large; never an application dependency), none (models disabled).

  PYTHONPATH=src;tests python scripts/benchmark.py --embedding hash --mode fast
"""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import pickle
import sys
import tempfile
import time
from unittest.mock import patch

from openpyxl import Workbook

import benchmark_fixture
from riskmatch.pipeline import analyze

ENDPOINT = "https://embedding.bench.invalid/v1/embeddings"


class HashEmbedding:
    dimensions = 512

    def __call__(self, text):
        vector = [0.0] * self.dimensions
        folded = " " + text.casefold().translate(benchmark_fixture.FOLD) + " "
        for size in (3, 4):
            for start in range(len(folded) - size + 1):
                digest = hashlib.blake2b(folded[start:start + size].encode("utf-8"), digest_size=4).digest()
                value = int.from_bytes(digest, "little")
                vector[value % self.dimensions] += 1.0 if value & 1 << 31 else -1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


class E5Embedding:
    dimensions = 1024

    def __init__(self, cache_path):
        from fastembed import TextEmbedding
        self.model = TextEmbedding("intfloat/multilingual-e5-large")
        self.cache_path = Path(cache_path)
        self.cache = pickle.loads(self.cache_path.read_bytes()) if self.cache_path.is_file() else {}
        self.dirty = 0

    def __call__(self, text):
        if text not in self.cache:
            # Symmetric use of the "query: " prefix as the e5 authors recommend for similarity tasks.
            self.cache[text] = [float(x) for x in next(iter(self.model.embed(["query: " + text])))]
            self.dirty += 1
            if self.dirty % 200 == 0:
                self.save()
        return self.cache[text]

    def save(self):
        if self.dirty:
            self.cache_path.write_bytes(pickle.dumps(self.cache))


class Service:
    """Fake urllib opener honouring the verified contract: one text per request, no model name."""

    def __init__(self, embed, latency=0.0):
        self.embed, self.latency, self.calls, self.texts = embed, latency, 0, []

    def open(self, request, timeout):
        if request.full_url != ENDPOINT:
            raise AssertionError("unexpected request " + request.full_url)
        payload = json.loads(request.data)
        texts = payload["input"]
        self.calls += 1
        self.texts.extend(texts)
        if self.latency:
            time.sleep(self.latency)
        return io.BytesIO(json.dumps({"data": [{"embedding": self.embed(text)} for text in texts]}).encode("utf-8"))


def config(mode, dimensions, use_models):
    return {"mode": "real", "allow_real_connections": True, "use_models": use_models, "llm_review": False,
            "retrieval": {"limit": 8, "expanded_limit": 24, "search_mode": mode, "candidate_limit": 24,
                          "embedding_budget": 256},
            "oracle": {**{k: benchmark_fixture.SCOPE[k] for k in ("source_database", "environment", "container")},
                       "schemas": list(benchmark_fixture.SCOPE["schemas"]),
                       "user_env": "BENCH_USER", "password_env": "BENCH_PASSWORD", "dsn_env": "BENCH_DSN"},
            "embedding": {"endpoint": ENDPOINT, "allowed_hosts": ["embedding.bench.invalid"],
                          "model_selection": "server_default", "model": "", "model_version": "",
                          "dimensions": dimensions, "request_template": {"input": "{texts}"},
                          "response_path": "data[*].embedding", "max_retries": 0}}


def write_requests(path, requests):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Degiskenler"
    sheet.append(["Kategori", "Alan Adı", "Alan tipi", "Açıklama", "Halihazırda bildiriliyor mu"])
    for row in requests:
        sheet.append([row["category"], row["name"], row["data_type"], row["description"], ""])
    workbook.save(path)
    workbook.close()


def shown(row):
    """Candidates a user sees: explicit 'shown' list when the pipeline provides one, else the top 3."""
    candidates = row.get("candidates") or []
    return [c for c in candidates if c.get("shown", True)][:3]


def candidate_id(candidate, ids):
    source = candidate.get("source", {})
    return candidate.get("catalog_id") or ids.get((source.get("schema"), source.get("object"), source.get("column")))


def score(results, labels, ids):
    pos = [(r, set(l)) for r, l in zip(results, labels) if l]
    neg = [r for r, l in zip(results, labels) if not l]
    metrics = {"rows": len(results), "positive_rows": len(pos), "yok_rows": len(neg)}
    hit1 = hit3 = recall_pool = 0
    precision, ranks, shown_counts = [], [], []
    for row, expected in pos:
        returned = [candidate_id(c, ids) for c in row.get("candidates") or []]
        top = [candidate_id(c, ids) for c in shown(row)]
        shown_counts.append(len(top))
        hit1 += bool(top[:1] and top[0] in expected)
        hit3 += bool(set(top) & expected)
        recall_pool += bool(set(row.get("pool_ids") or returned) & expected)
        if top:
            precision.append(sum(1 for cid in top if cid in expected) / len(top))
        first = next((rank for rank, cid in enumerate(returned, 1) if cid in expected), None)
        ranks.append(first)
    metrics.update({
        "hit@1": round(hit1 / len(pos), 3), "hit@3_shown": round(hit3 / len(pos), 3),
        "recall_pool": round(recall_pool / len(pos), 3),
        "precision@3_shown": round(sum(precision) / len(precision), 3) if precision else None,
        "mrr_returned": round(sum(1 / r for r in ranks if r) / len(pos), 3),
        "positive_rows_with_no_shown": sum(1 for count in shown_counts if not count),
        "avg_returned": round(sum(len(r.get("candidates") or []) for r in results) / len(results), 1),
        "yok_rows_with_shown": sum(1 for r in neg if shown(r)),
        "yok_rows_with_confident": sum(1 for r in neg if any(c.get("meaning") in ("güçlü", "olası", "strong", "likely")
                                                             for c in shown(r))),
    })
    return metrics


def run(args, snapshot, requests, embed, order):
    reordered = [requests[i] for i in order]
    service = Service(embed, args.latency) if embed else None
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        source, output = folder / "requests.xlsx", folder / "results.xlsx"
        write_requests(source, reordered)
        started, first = time.monotonic(), []

        def on_result(_row):
            if not first:
                first.append(time.monotonic() - started)
        patches = [patch("riskmatch.adapters.oracle.OracleAdapter.scan", return_value=snapshot)]
        if service:
            patches.append(patch("urllib.request.build_opener", return_value=service))
        for item in patches:
            item.start()
        try:
            result = analyze(source, output, mode="real", state_dir=folder / "state",
                             config_data=config(args.mode, getattr(embed, "dimensions", 4), bool(embed)),
                             result_callback=on_result)
        finally:
            for item in patches:
                item.stop()
        total = time.monotonic() - started
    by_name = {row["name"]: row for row in result["results"] if "name" in row}
    rows = result["results"]
    if by_name and len(by_name) == len(rows):
        rows = [by_name[r["name"]] for r in requests]
    else:
        inverse = sorted(range(len(order)), key=lambda i: order[i])
        rows = [rows[i] for i in inverse]
    return rows, {"first_result_s": round(first[0], 3) if first else None, "total_s": round(total, 3),
                  "embedding_calls": service.calls if service else 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedding", choices=("hash", "e5", "none"), default="hash")
    parser.add_argument("--mode", choices=("fast", "full"), default="fast")
    parser.add_argument("--filler-tables", type=int, default=120)
    parser.add_argument("--latency", type=float, default=0.0, help="simulated seconds per embedding call")
    parser.add_argument("--orders", type=int, default=2, help="1 = file order, 2 = also reversed")
    parser.add_argument("--e5-cache", default=r"C:\claude\rfw-deveval\e5-cache.pkl")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    snapshot, requests, labels = benchmark_fixture.build(args.filler_tables)
    ids = {(r["schema"], r["object"], r["column"]): r["catalog_id"] for r in snapshot["records"]}
    embed = {"hash": HashEmbedding, "none": lambda: None,
             "e5": lambda: E5Embedding(args.e5_cache)}[args.embedding]()
    orders = [list(range(len(requests))), list(reversed(range(len(requests))))][:args.orders]
    report = {"dataset": "synthetic-benchmark-v1 (not bank data)", "catalog_columns": len(snapshot["records"]),
              "embedding": args.embedding, "mode": args.mode, "runs": []}
    tops = []
    for order in orders:
        rows, timing = run(args, snapshot, requests, embed, order)
        tops.append([[candidate_id(c, ids) for c in shown(row)] for row in rows])
        report["runs"].append({"order": "file" if order[0] == 0 else "reversed", **timing, **score(rows, labels, ids)})
    if len(tops) > 1:
        report["order_consistency_top3"] = round(sum(a == b for a, b in zip(*tops[:2])) / len(requests), 3)
    if hasattr(embed, "save"):
        embed.save()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
