"""Olcum araci: aday havuzu + gosterim kapisi taramasi, uctan uca kosu, tutarlilik ve (gercek modda) probe.

Sentetik mod (--fixture): tests/benchmark_fixture katalogu, in-process sahte Oracle ve embedding servisi.
  Sentetik sonuc gercek dogruluk kaniti degildir.
Gercek mod: --config config/real.json --excel etiketli.xlsx --label-column "Beklenen kolon"
  Etiket: SCHEMA.OBJECT.COLUMN (birden cok icin ';'); bos ya da YOK = karsiligi yok.
  Kimlik bilgileri yalniz config'teki *_env ortam degiskenlerinden okunur; rapora yazilmaz.

Havuz her satir icin bir kez uretilir (model cagrisi tekrarlanmaz); kapilar rank() ile saf olarak taranir.

  PYTHONPATH=src;tests python scripts/evaluate.py --fixture --out runtime/olcum
  PYTHONPATH=src python scripts/evaluate.py --config config/real.json --excel x.xlsx --label-column "Beklenen kolon" --probe
"""
import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zlib
from collections import Counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT / "scripts", ROOT / "tests", ROOT / "src"):
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from riskmatch import __version__  # noqa: E402
from riskmatch.config import load_config  # noqa: E402
from riskmatch.errors import ConfigError, InputError, IntegrationError  # noqa: E402
from riskmatch.excelio import read_requests  # noqa: E402
from riskmatch.lexicon import Lexicon, load as load_lexicon  # noqa: E402
from riskmatch.pipeline import analyze, catalog_key, index_settings  # noqa: E402
from riskmatch.ranking import gate_config, rank  # noqa: E402
from riskmatch.resources import Resources  # noqa: E402
from riskmatch.semantics import parse_definition  # noqa: E402

Z_GRID = (2.0, 2.5, 3.0)
C_GRID = (0.3, 0.5, 0.7)
MARGIN = 1.5
SYNTH_NOTE = "Sentetik sonuç gerçek doğruluk kanıtı değildir; yalnız gerileme sinyalidir."


class UserError(Exception):
    """Kullaniciya Turkce mesajla gosterilen hata (sir icermez)."""


# -- etiketler ------------------------------------------------------------------

def parse_label(text):
    """-> (set of (SCHEMA, OBJECT, COLUMN), gecerli_mi). Bos / YOK = (set(), True)."""
    text = str(text or "").strip()
    if not text or text.casefold() in ("yok", "-"):
        return set(), True
    triples = set()
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        pieces = [p.strip().upper() for p in part.split(".")]
        if len(pieces) != 3 or not all(pieces):
            return set(), False
        triples.add(tuple(pieces))
    return triples, bool(triples)


def _triple(schema, obj, column):
    return (str(schema or "").strip().upper(), str(obj or "").strip().upper(), str(column or "").strip().upper())


def candidate_hit(candidate, expected):
    source = candidate.get("source") or {}
    column = source.get("column")
    if _triple(source.get("schema"), source.get("object"), column) in expected:
        return True
    for location in candidate.get("locations") or []:
        schema, _, obj = str(location).partition(".")
        if _triple(schema, obj, column) in expected:
            return True
    return False


# -- sentetik duzenek -----------------------------------------------------------

@contextlib.contextmanager
def synthetic_patches(snapshot, service):
    started = [patch("riskmatch.adapters.oracle.OracleAdapter.scan", return_value=snapshot)]
    if service is not None:
        started.append(patch("urllib.request.build_opener", return_value=service))
    for item in started:
        item.start()
    try:
        yield
    finally:
        for item in started:
            item.stop()


def build_embedder(kind, args):
    import benchmark
    if kind == "none":
        return None
    if kind == "e5":
        try:
            return benchmark.E5Embedding(args.e5_cache)
        except ImportError:
            raise UserError("--embedder e5 için fastembed kurulu değil (yalnız geliştirme aracı).") from None
    return benchmark.HashEmbedding()


# -- zor varyantlar -------------------------------------------------------------

VOWELS = set("AEIOUaeiou")


def garble_name(name):
    """Deterministik bozma: ad bazli (crc32) unlu dusurme / kisaltma / alt cizgi kaldirma."""
    mode = zlib.crc32(str(name).encode("utf-8")) % 3
    parts = str(name).split("_")
    if mode == 0:
        return "_".join(p[:1] + "".join(c for c in p[1:] if c not in VOWELS) if len(p) > 3 else p for p in parts)
    if mode == 1:
        return "_".join(p[:3] if len(p) > 4 else p for p in parts)
    return "".join(parts)


def empty_lexicon(full=True):
    """full=False: yalniz groups/expansions bos; generic/suffixes/structural paket sozlugunden gelir."""
    if not full:
        return Lexicon({**load_lexicon()._data, "groups": [], "expansions": {}})
    return Lexicon({"version": 1, "groups": [], "expansions": {}, "generic": [], "suffixes": [], "structural": []})


# -- gercek mod girdileri -------------------------------------------------------

def real_credentials(config):
    """Ortamdan okur; eksik degiskenlerin yalniz ADLARINI bildirir."""
    oracle, embedding = config["oracle"], config.get("embedding", {})
    missing, values = [], {}
    for key in ("user", "password", "dsn"):
        name = oracle.get(key + "_env") or ""
        values[key] = os.environ.get(name, "")
        if not values[key]:
            missing.append(name or (key + "_env tanımsız"))
    for key in ("wallet_dir", "wallet_password"):
        name = oracle.get(key + "_env")
        if name and os.environ.get(name):
            values[key] = os.environ[name]
    if missing:
        raise UserError("Şu ortam değişkenleri tanımlı değil: " + ", ".join(missing)
                        + ". Değerleri bu araca yazmayın; PowerShell oturumunda ayarlayın.")
    key_name = embedding.get("api_key_env")
    api_key = os.environ.get(key_name, "") if key_name else ""
    return {"oracle": values, "embedding": api_key, "llm": ""}


def load_rows(args):
    mapping = None
    if args.column_mapping:
        mapping = json.loads(Path(args.column_mapping).read_text(encoding="utf-8-sig"))
    requests = read_requests(args.excel, mapping)
    rows = []
    for req in requests:
        if req.get("input_errors"):
            continue
        raw = (req.get("original") or {}).get(args.label_column)
        expected, valid = parse_label(raw)
        rows.append({"req": req, "expected": expected, "label_valid": valid})
    if not rows:
        raise UserError("Excel'de işlenebilir satır bulunamadı.")
    if not any(args.label_column in (r["req"].get("original") or {}) for r in rows):
        raise UserError("Etiket kolonu bulunamadı: " + args.label_column)
    return rows


# -- havuz ----------------------------------------------------------------------

def sanitize_record(record):
    return {k: v for k, v in record.items() if k != "captured_at"}


def build_pools(rows, snapshot, config, resources, adapter, args):
    settings = index_settings(config)
    ctx, warnings = None, []
    if adapter is not None:
        try:
            ctx = resources.embedding(adapter)
        except Exception as error:
            warnings.append("Embedding parmak izi alınamadı (%s); yalnız ad/metin araması." % type(error).__name__)
    index = resources.index(snapshot, ctx, settings, refresh=False)
    indexing = None
    if ctx is not None and args.build_index:
        indexer = resources.indexer(snapshot, ctx, settings)
        indexer.start()
        done = indexer.wait(args.index_timeout)
        indexing = dict(indexer.status(), finished=done)
        if not done:
            indexer.stop(wait=True)
            warnings.append("Arka plan indeksleme süre sınırında bitmedi; kapsam kısmi.")
    index.refresh_index()
    service = getattr(args, "_service", None)
    index_calls = service.calls if service else 0
    index.begin_job(reset_circuit=True)
    strategy = "hybrid" if ctx is not None else "lexical"
    naming = config["naming"]
    started = time.monotonic()
    for row in rows:
        req = row["req"]
        req["definition"] = parse_definition(req["name"], req.get("description_for_parse", req["description"]), naming)
        row["pool"] = [sanitize_record(r) for r in index.search(req, strategy=strategy)]
        row["info"] = dict(index.last_search_info)
    return {"strategy": strategy, "search_seconds": round(time.monotonic() - started, 2), "indexing": indexing,
            "index_coverage": round(index.index_coverage, 4), "fingerprint": _fp(ctx), "warnings": warnings,
            "embedding_calls": {"indeks": index_calls, "havuz_sorgu": (service.calls - index_calls) if service else 0,
                                "havuz_satir_basi": round((service.calls - index_calls) / max(1, len(rows)), 2) if service else 0}}


def _fp(ctx):
    if ctx is None:
        return None
    return {"status": ctx.fingerprint.get("status"), "dims": ctx.fingerprint.get("dims"),
            "namespace": str(ctx.namespace.id)[:12]}


def save_pools(path, rows):
    data = [{"row_id": r["req"]["row_id"], "name": r["req"]["name"], "etiket": sorted(".".join(t) for t in r["expected"]),
             "info": r["info"], "pool": r["pool"]} for r in rows]
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


# -- kapi taramasi --------------------------------------------------------------

def gate_grid(base):
    grid = [("varsayilan", base)]
    for z in Z_GRID:
        for c in C_GRID:
            grid.append(("z%.1f_c%.1f" % (z, c), {**base, "z_min": z, "c_min": c, "margin": MARGIN}))
    return grid


def score_gate(rows, gate, complete):
    pos = [r for r in rows if r["expected"] and r["label_valid"]]
    neg = [r for r in rows if not r["expected"] and r["label_valid"]]
    status, status_yok = Counter(), Counter()
    recall = hit3 = 0
    precisions, ranks, shown_counts = [], [], []
    for row in rows:
        if not row["label_valid"]:
            continue
        ranked = rank(row["req"], row["pool"], gate=gate, semantic_scope=row["info"].get("semantic_scope", "none"),
                      catalog_complete=complete)
        row["_last"] = ranked
        status[ranked["row_status"]] += 1
        if not row["expected"]:
            status_yok[ranked["row_status"]] += 1
    for row in pos:
        shown = row["_last"]["candidates"]
        flags = [candidate_hit(c, row["expected"]) for c in shown]
        pool_triples = {_triple(r.get("schema"), r.get("object"), r.get("column")) for r in row["pool"]}
        recall += bool(pool_triples & row["expected"])
        hit3 += any(flags)
        shown_counts.append(len(shown))
        if shown:
            precisions.append(sum(flags) / len(shown))
        ranks.append(next((i for i, f in enumerate(flags, 1) if f), None))
    found = [r for r in ranks if r]
    false_rows = sum(1 for r in neg if r["_last"]["candidates"])
    n = len(pos) or 1
    return {
        "pozitif_satir": len(pos), "yok_satir": len(neg),
        "recall_havuz": round(recall / n, 3),
        "hit_at_3": round(hit3 / n, 3),
        "precision_at_3": round(sum(precisions) / len(precisions), 3) if precisions else None,
        "ilk_dogru_sira_ortalama": round(sum(found) / len(found), 2) if found else None,
        "ilk_dogru_sira_dagilim": {**{str(k): sum(1 for r in ranks if r == k) for k in (1, 2, 3)},
                                   "yok": sum(1 for r in ranks if not r)},
        "yok_satirda_aday_gosterilen": false_rows,
        "yok_yanlis_oneri_orani": round(false_rows / len(neg), 3) if neg else None,
        "gosterilmeyen_pozitif_satir": sum(1 for c in shown_counts if not c),
        "row_status": dict(status), "row_status_yok": dict(status_yok),
    }


def sweep(rows, config, complete):
    base = gate_config(config["retrieval"].get("gate"))
    results = []
    for name, gate in gate_grid(base):
        results.append({"kapi": name, "z_min": gate["z_min"], "c_min": gate["c_min"], "margin": gate["margin"],
                        **score_gate(rows, gate, complete)})
    for row in rows:
        row.pop("_last", None)
    return results


def best_gate(grid):
    def key(item):
        return ((item["hit_at_3"] or 0) - (item["yok_yanlis_oneri_orani"] or 0), item["precision_at_3"] or 0)
    return max((g for g in grid if g["kapi"] != "varsayilan"), key=key)["kapi"]


# -- uctan uca ------------------------------------------------------------------

def run_groups(rows, group_size, analyze_kwargs, resources, state_dir, xlsx, epoch, service=None):
    started = time.monotonic()
    calls_before = service.calls if service else 0
    first, shown, stats = [], {}, Counter()
    errors = 0

    def on_result(_row):
        if not first:
            first.append(time.monotonic() - started)
    for start in range(0, len(rows), group_size):
        ids = [r["req"]["row_id"] for r in rows[start:start + group_size]]
        result = analyze(xlsx, Path(state_dir) / "e2e.xlsx", row_ids=ids, resources=resources, state_dir=state_dir,
                         resume=False, write_report=False, index_epoch=epoch, result_callback=on_result,
                         **analyze_kwargs)
        for item in result["results"]:
            shown[item["row_id"]] = frozenset(c["catalog_id"] for c in item.get("candidates") or [])
            errors += bool(item.get("errors"))
        for key, value in result["run_info"]["Arama istatistikleri"].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                stats[key] += value
    calls = (service.calls - calls_before) if service else 0
    return shown, {"grup_boyutu": group_size, "satir": len(rows), "hatali_satir": errors,
                   "embedding_cagri": calls, "embedding_cagri_satir_basi": round(calls / max(1, len(rows)), 2),
                   "ilk_sonuc_s": round(first[0], 2) if first else None,
                   "toplam_s": round(time.monotonic() - started, 2),
                   "arama_istatistikleri": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in stats.items()}}


# -- probe ----------------------------------------------------------------------

def probe(adapter, resources, count=10):
    times, errors, dims = [], 0, None
    for number in range(count):
        started = time.monotonic()
        try:
            vectors = adapter.embed(["ölçüm metni %d müşteri bakiye" % number], kind="document")
            dims = len(vectors[0])
            times.append(time.monotonic() - started)
        except Exception as error:
            errors += 1
            last = type(error).__name__
    times.sort()

    def pct(p):
        return round(times[min(len(times) - 1, math.ceil(p * len(times)) - 1)] * 1000, 1) if times else None
    out = {"cagri": count, "hata": errors, "vektor_boyutu": dims, "p50_ms": pct(0.5), "p95_ms": pct(0.95)}
    if errors:
        out["son_hata_turu"] = last
    try:
        ctx = resources.embedding(adapter)
        out["parmak_izi"] = _fp(ctx)
        out["verify_sample2"] = "same" if resources.verify(ctx, sample=2) else "changed"
    except Exception as error:
        out["parmak_izi_hata"] = type(error).__name__
    return out


# -- rapor ----------------------------------------------------------------------

def git_commit():
    try:
        done = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT), capture_output=True, text=True,
                              timeout=10)
        return done.stdout.strip() or None
    except Exception:
        return None


def write_markdown(report, path):
    kurulum = report["kurulum"]
    lines = ["# Ölçüm raporu", "",
             "- Mod: %s" % report["mod"], "- Sürüm: %s (commit: %s)" % (kurulum["surum"], kurulum["git_commit"] or "yok"),
             "- Katalog kolonu: %s; tarama tamam: %s; kaynak: %s" % (kurulum["katalog_kolon"], kurulum["katalog_tam"], kurulum["katalog_kaynak"]),
             "- Kapsam: %s" % json.dumps(kurulum["kapsam"], ensure_ascii=False),
             "- İndeks kapsamı: %s; strateji: %s" % (kurulum["index_coverage"], kurulum["strateji"]),
             "- Satır: %d (pozitif %d, YOK %d, geçersiz etiket %d)" % (
                 report["satir"]["toplam"], report["satir"]["pozitif"], report["satir"]["yok"], report["satir"]["gecersiz_etiket"])]
    if report.get("not"):
        lines += ["", "> " + report["not"]]
    for warning in report.get("uyarilar", []):
        lines.append("- Uyarı: " + warning)
    lines += ["", "## Kapı taraması", "",
              "| Kapı | Recall@havuz | Hit@3 | Precision@3 | İlk doğru sıra | YOK'ta öneri | row_status |", "|---|---|---|---|---|---|---|"]
    for g in report["kapi_taramasi"]:
        lines.append("| %s | %s | %s | %s | %s | %s/%s | %s |" % (
            g["kapi"], g["recall_havuz"], g["hit_at_3"], g["precision_at_3"], g["ilk_dogru_sira_ortalama"],
            g["yok_satirda_aday_gosterilen"], g["yok_satir"], json.dumps(g["row_status"], ensure_ascii=False)))
    lines += ["", "Tarama içi en iyi kapı (hit@3 − YOK yanlış öneri oranı): **%s** (öneri değil, kalibrasyon kararı ayrı)." % report["en_iyi_kapi"]]
    e2e = report.get("uctan_uca")
    if e2e:
        lines += ["", "## Uçtan uca", "", "- Grup boyutu %s; ilk sonuç %s sn; toplam %s sn; hatalı satır %s" % (
            e2e["grup_boyutu"], e2e["ilk_sonuc_s"], e2e["toplam_s"], e2e["hatali_satir"]),
            "- Arama istatistikleri: " + json.dumps(e2e["arama_istatistikleri"], ensure_ascii=False)]
        if "tutarlilik" in report:
            lines.append("- Tutarlılık oranı (ters sıra, grup %s): %s" % (report["tutarlilik"]["ikinci_grup_boyutu"], report["tutarlilik"]["oran"]))
    if report.get("probe"):
        lines += ["", "## Probe", "", "```", json.dumps(report["probe"], ensure_ascii=False, indent=1), "```"]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv):
    p = argparse.ArgumentParser(description="Risk eşleştirme ölçüm aracı")
    p.add_argument("--fixture", action="store_true", help="sentetik mod")
    p.add_argument("--filler-tables", type=int, default=120)
    p.add_argument("--embedder", choices=("hash", "e5", "none"), default="hash")
    p.add_argument("--e5-cache", default=r"C:\claude\rfw-deveval\e5-cache.pkl")
    p.add_argument("--config", help="config/real.json (gerçek mod)")
    p.add_argument("--excel", help="etiketli Excel (gerçek mod)")
    p.add_argument("--label-column", default="Beklenen kolon")
    p.add_argument("--column-mapping", help="read_requests için kolon eşleme JSON dosyası")
    p.add_argument("--rows", type=int, default=0, help="ilk N satır (0 = tümü)")
    p.add_argument("--state-dir", help="vektör/katalog önbelleği (varsayılan geçici)")
    p.add_argument("--out", required=True)
    p.add_argument("--build-index", dest="build_index", action="store_true", default=None)
    p.add_argument("--no-build-index", dest="build_index", action="store_false")
    p.add_argument("--index-timeout", type=float, default=3600.0)
    p.add_argument("--e2e", action="store_true", help="uçtan uca koşu (sentetikte varsayılan açık)")
    p.add_argument("--no-e2e", action="store_true")
    p.add_argument("--group-size", type=int, default=25)
    p.add_argument("--consistency", action="store_true")
    p.add_argument("--consistency-sizes", default="", help="ör. 25,10,1: ilk boyut referans; son boyut ayrıca ters sırada koşulur")
    p.add_argument("--variant", choices=("normal", "nolex", "nolex_full", "garbled"), default="normal",
                   help="zor varyant (yalnız sentetik): nolex = gruplar/açılımlar kapalı (generic, ek, yapısal kalır); "
                        "nolex_full = sözlüğün tamamı kapalı; garbled = talep adları bozuk")
    p.add_argument("--fixture-rows", type=int, default=0, help="sentetik talep satırı sayısı (0 = elle yazılan 49)")
    p.add_argument("--probe", action="store_true", help="yalnız gerçek mod")
    args = p.parse_args(argv)
    if args.fixture == bool(args.config):
        p.error("Ya --fixture ya da --config verin.")
    if not args.fixture and not args.excel and not args.probe:
        p.error("Gerçek modda --excel (ya da yalnız --probe) gerekli.")
    if args.variant != "normal" and not args.fixture:
        p.error("--variant yalnız sentetik modda.")
    if args.fixture and args.probe:
        p.error("--probe yalnız gerçek modda.")
    if args.build_index is None:
        args.build_index = args.fixture
    return args


def evaluate(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    real = not args.fixture
    report = {"mod": "gercek" if real else "sentetik", "varyant": args.variant, "embedder": args.embedder,
              "fixture_rows": args.fixture_rows or None, "olusturma": time.strftime("%Y-%m-%d %H:%M:%S"), "uyarilar": []}
    if not real:
        report["not"] = SYNTH_NOTE
    temp = tempfile.TemporaryDirectory() if not args.state_dir else None
    work = Path(args.state_dir or temp.name)
    work.mkdir(parents=True, exist_ok=True)
    resources = Resources(work / "resources")
    try:
        with contextlib.ExitStack() as stack:
            if real:
                config = load_config(args.config, "real")
                credentials = real_credentials(config)
                from riskmatch.adapters.oracle import OracleAdapter
                from riskmatch.adapters.qwen import EmbeddingAdapter
                adapter = EmbeddingAdapter(config["embedding"], api_key=credentials["embedding"]) if config.get("use_models", True) else None
                analyze_kwargs = {"mode": "real", "config_path": args.config, "credentials": credentials}
                oracle_adapter = OracleAdapter(config["oracle"], credentials=credentials["oracle"])
                key = catalog_key(config["oracle"], credentials["oracle"])
                rows = load_rows(args) if args.excel else []
                xlsx = args.excel
            else:
                import benchmark
                import benchmark_fixture
                snapshot_fixture, fixture_requests, labels = benchmark_fixture.build(args.filler_tables, rows=args.fixture_rows or None)
                embed = build_embedder(args.embedder, args)
                service = benchmark.Service(embed) if embed else None
                args._service = service
                if args.variant == "garbled":
                    fixture_requests = [{**r, "name": garble_name(r["name"])} for r in fixture_requests]
                if args.variant in ("nolex", "nolex_full"):
                    blank = empty_lexicon(full=args.variant == "nolex_full")
                    resources._lexicon = blank
                    stack.enter_context(patch("riskmatch.semantics._LEXICON", blank))
                stack.enter_context(synthetic_patches(snapshot_fixture, service))
                config_data = benchmark.config("fast", getattr(embed, "dimensions", 4), bool(embed))
                config = load_config(None, "real", data=config_data)
                credentials = None
                analyze_kwargs = {"mode": "real", "config_data": config_data}
                xlsx = work / "talepler.xlsx"
                benchmark.write_requests(xlsx, fixture_requests)
                ids = {(r["schema"], r["object"], r["column"]): r["catalog_id"] for r in snapshot_fixture["records"]}
                by_id = {v: k for k, v in ids.items()}
                read = read_requests(xlsx)
                rows = [{"req": req, "expected": {_triple(*by_id[c]) for c in label}, "label_valid": True}
                        for req, label in zip(read, labels)]
                from riskmatch.adapters.oracle import OracleAdapter
                from riskmatch.adapters.qwen import EmbeddingAdapter
                oracle_adapter = OracleAdapter(config["oracle"])
                adapter = EmbeddingAdapter(config["embedding"]) if embed else None
                key = catalog_key(config["oracle"], {"user": "", "dsn": ""})
            if args.probe:
                if adapter is None:
                    raise UserError("Probe için embedding yapılandırması (use_models) gerekli.")
                report["probe"] = probe(adapter, resources)
            if not rows:
                report["kapi_taramasi"], report["en_iyi_kapi"] = [], "-"
                return finish(report, out, config=config, snapshot=None, rows=[], pools=None, real=real)
            if args.rows:
                rows = rows[:args.rows]
            snapshot, catalog_info = resources.catalog(oracle_adapter, key)
            scope = snapshot["scope"]
            snapshot = {**snapshot, "records": [r for r in snapshot["records"] if r.get("schema") in scope.get("schemas", [])]}
            known = {_triple(r["schema"], r["object"], r["column"]) for r in snapshot["records"]}
            for row in rows:
                if row["expected"] and not row["expected"] <= known:
                    row["label_valid"] = False
                    report["uyarilar"].append("Etiket katalogda bulunamadı (satır %s); ölçüme alınmadı." % row["req"].get("excel_row"))
            pools = build_pools(rows, snapshot, config, resources, adapter, args)
            report["uyarilar"] += pools["warnings"]
            save_pools(out / "havuz.json", [r for r in rows if r["label_valid"]])
            scored = [r for r in rows if r["label_valid"]]
            report["kapi_taramasi"] = sweep(scored, config, bool(snapshot["complete"]))
            report["en_iyi_kapi"] = best_gate(report["kapi_taramasi"])
            if (args.fixture and not args.no_e2e) or args.e2e:
                names = {r["catalog_id"]: "%s.%s.%s" % (r["schema"], r["object"], r["column"]) for r in snapshot["records"]}
                run_e2e(report, args, scored, analyze_kwargs, resources, work, xlsx, names)
            return finish(report, out, config=config, snapshot=snapshot, rows=rows, pools=pools, real=real,
                          catalog_info=catalog_info)
    finally:
        resources.close()
        if temp is not None:
            temp.cleanup()


def run_e2e(report, args, rows, analyze_kwargs, resources, work, xlsx, names=None):
    group = max(1, args.group_size)
    service = getattr(args, "_service", None)
    first, stats = run_groups(rows, group, analyze_kwargs, resources, work, xlsx, "eval-1", service)
    report["uctan_uca"] = stats
    if args.consistency:
        other = max(1, group // 2) if group > 1 else 3
        second, _ = run_groups(list(reversed(rows)), other, analyze_kwargs, resources, work, xlsx, "eval-2", service)
        same = sum(1 for r in rows if first.get(r["req"]["row_id"]) == second.get(r["req"]["row_id"]))
        report["tutarlilik"] = {"oran": round(same / len(rows), 3), "satir": len(rows), "ikinci_grup_boyutu": other}
    if args.consistency_sizes:
        report["tutarlilik_matrisi"] = consistency_matrix(args, rows, analyze_kwargs, resources, work, xlsx,
                                                          names or {}, service)


def consistency_matrix(args, rows, analyze_kwargs, resources, work, xlsx, names, service):
    """Referans: ilk boyut, ileri sira. Diger boyutlar ve ters sira ayni aday kumesini gosteriyor mu."""
    sizes = [max(1, int(x)) for x in args.consistency_sizes.split(",") if x.strip()]
    plan = [(size, False) for size in sizes] + [(sizes[-1], True)]
    results = []
    for number, (size, reverse) in enumerate(plan):
        ordered = list(reversed(rows)) if reverse else rows
        shown, info = run_groups(ordered, size, analyze_kwargs, resources, work, xlsx, "eval-m%d" % number, service)
        results.append(((size, reverse), shown, info))
    ref_key, ref, _ = results[0]
    out = {"referans": {"grup_boyutu": ref_key[0], "ters": ref_key[1]}, "karsilastirma": []}
    for (size, reverse), shown, info in results[1:]:
        diff = [r["req"]["row_id"] for r in rows if ref.get(r["req"]["row_id"]) != shown.get(r["req"]["row_id"])]
        examples = []
        for row_id in diff[:5]:
            row = next(r for r in rows if r["req"]["row_id"] == row_id)
            examples.append({"row_id": row_id, "ad": row["req"]["name"],
                             "referans": sorted(names.get(c, c) for c in ref.get(row_id, ())),
                             "diger": sorted(names.get(c, c) for c in shown.get(row_id, ()))})
        out["karsilastirma"].append({"grup_boyutu": size, "ters": reverse, "oran": round(1 - len(diff) / len(rows), 3),
                                     "farkli_satir": len(diff), "toplam_s": info["toplam_s"], "ornek_farklar": examples})
    return out


def finish(report, out, *, config, snapshot, rows, pools, real, catalog_info=None):
    invalid = sum(1 for r in rows if not r["label_valid"])
    valid = [r for r in rows if r["label_valid"]]
    report["satir"] = {"toplam": len(rows), "pozitif": sum(1 for r in valid if r["expected"]),
                       "yok": sum(1 for r in valid if not r["expected"]), "gecersiz_etiket": invalid}
    oracle = config["oracle"]
    report["kurulum"] = {
        "surum": __version__, "git_commit": git_commit(),
        "katalog_kolon": len(snapshot["records"]) if snapshot else None,
        "katalog_tam": bool(snapshot["complete"]) if snapshot else None,
        "katalog_kaynak": (catalog_info or {}).get("source"),
        "kapsam": (snapshot or {}).get("scope") or {k: oracle.get(k) for k in ("source_database", "environment", "container", "schemas")},
        "index_coverage": pools["index_coverage"] if pools else None,
        "strateji": pools["strategy"] if pools else None,
        "parmak_izi": pools["fingerprint"] if pools else None,
        "indeksleme": pools["indexing"] if pools else None,
        "arama_suresi_s": pools["search_seconds"] if pools else None,
        "embedding_cagri_havuz": pools["embedding_calls"] if pools else None,
    }
    (out / "rapor.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_markdown(report, out / "rapor.md")
    return report


def main(argv=None):
    args = parse_args(argv)
    try:
        report = evaluate(args)
    except UserError as error:
        print("HATA: " + str(error), file=sys.stderr)
        return 2
    except (ConfigError, InputError, IntegrationError, OSError) as error:
        print("HATA (%s): %s" % (type(error).__name__, error), file=sys.stderr)
        return 2
    print("Rapor: " + str(Path(args.out).resolve() / "rapor.md"))
    return 0 if report else 1


if __name__ == "__main__":
    sys.exit(main())
