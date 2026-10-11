"""Olcum araci: aday havuzu + gosterim kapisi taramasi, uctan uca kosu, tutarlilik ve (gercek modda) probe.

Sentetik mod (--fixture): tests/benchmark_fixture katalogu, in-process sahte Oracle ve embedding servisi.
  Sentetik sonuc gercek dogruluk kaniti degildir.
Gercek mod (web-esdeger, varsayilan): --schemas DM_CAD --excel etiketli.xlsx [--llm] [--no-embedding]
  Web uygulamasiyla AYNI ayarlar: deployment.login_settings + probe_oracle (container, erisilebilir semalar),
  discover_model ile EMBEDDING_BASE_URL / LLM_BASE_URL kesfi, web_services.build_run; arama modu web varsayilani.
  Kimlik: RISK_ORACLE_USER, RISK_ORACLE_PASSWORD, (varsa) RISK_EMBEDDING_API_KEY, RISK_LLM_API_KEY ortamdan.
Gercek mod (eski yol): --config config/real.json --excel etiketli.xlsx --label-column "Beklenen kolon"
  Etiket: SCHEMA.OBJECT.COLUMN ya da OBJECT.COLUMN (birden cok icin ';'); YOK ya da '-' = karsiligi yok;
  bos = etiketsiz (olcum disi). OBJECT.COLUMN kapsamdaki katalogda tek semada varsa cozulur, yoksa gecersiz.
  --resources-dir runtime/web: web uygulamasinin katalog/vektor onbellegi (web kapaliyken) yeniden kullanilir.
  Kimlik bilgileri yalniz config'teki *_env ortam degiskenlerinden okunur; rapora yazilmaz.

Havuz her satir icin bir kez uretilir (model cagrisi tekrarlanmaz); kapilar rank() ile saf olarak taranir.
Ciktilar: rapor.md/json, iskalar.md + satirlar.json (satir bazli teshis), katalog_ozet.md/json, havuz.json.
Hepsi yalniz metadata (ad, aciklama, katalog yorumu, sayilar) icerir.

  PYTHONPATH=src;tests python scripts/evaluate.py --fixture --out runtime/olcum
  PYTHONPATH=src python scripts/evaluate.py --schemas DM_CAD --excel x.xlsx --resources-dir runtime/web --build-index
  PYTHONPATH=src python scripts/evaluate.py --config config/real.json --excel x.xlsx --label-column "Beklenen kolon" --probe
"""
import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import zlib
from collections import Counter
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT / "scripts", ROOT / "tests", ROOT / "src"):
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from riskmatch import __version__  # noqa: E402
from riskmatch.config import load_config  # noqa: E402
from riskmatch.errors import ConfigError, InputError, IntegrationError  # noqa: E402
from riskmatch.excelio import read_requests  # noqa: E402
from riskmatch.lexicon import Lexicon, _words as lexicon_words, load as load_lexicon  # noqa: E402
from riskmatch.pipeline import analyze, catalog_key, index_settings  # noqa: E402
from riskmatch.ranking import GATE_REASON, LIMIT_REASON, SUBJECT_REASON, WEAK_REASON, gate_config, rank  # noqa: E402
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
    """-> {"durum": pozitif|yok|etiketsiz|gecersiz, "uclu": {(S, O, C)}, "ikili": {(O, C)}, "neden": str}.

    Bos = etiketsiz (olcum disi); YOK / '-' = karsiligi yok. Ikili (OBJECT.COLUMN) etiket katalogla sonra cozulur.
    """
    text = str(text or "").strip()
    out = {"durum": "etiketsiz", "uclu": set(), "ikili": set(), "neden": ""}
    if not text:
        return out
    if text.casefold() in ("yok", "-"):
        return dict(out, durum="yok")
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        pieces = [p.strip().upper() for p in part.split(".")]
        if len(pieces) not in (2, 3) or not all(pieces):
            return dict(out, durum="gecersiz", uclu=set(), ikili=set(), neden="biçim: " + part[:60])
        out["uclu" if len(pieces) == 3 else "ikili"].add(tuple(pieces))
    if not out["uclu"] and not out["ikili"]:
        return dict(out, durum="gecersiz", neden="boş parça")
    return dict(out, durum="pozitif")


def resolve_labels(rows, records):
    """Ikili etiketleri SCHEMA ile cozer; katalogda olmayan ya da belirsiz etiketli satiri gecersiz yapar."""
    known = {_triple(r["schema"], r["object"], r["column"]) for r in records}
    schemas = {}
    for schema, obj, column in known:
        schemas.setdefault((obj, column), set()).add(schema)
    for row in rows:
        if row["etiket"] != "pozitif":
            continue
        reasons = ["katalogda yok: " + ".".join(t) for t in sorted(row["expected"] - known)]
        for pair in sorted(row.get("pairs") or ()):
            found = schemas.get(pair, set())
            if len(found) == 1:
                row["expected"].add((next(iter(found)),) + pair)
            elif found:
                reasons.append("belirsiz: %s %d şemada (%s)" % (".".join(pair), len(found), ", ".join(sorted(found)[:4])))
            else:
                reasons.append("katalogda yok: " + ".".join(pair))
        if reasons:
            row.update(etiket="gecersiz", label_valid=False, neden="; ".join(reasons))


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


# -- web-esdeger ayarlar --------------------------------------------------------

# app.js settings() search_mode gondermez ve index.html'de secim yok: clean_profile varsayilani "fast".
WEB_SEARCH_MODE = "fast"
WEB_ENV = {"user": "RISK_ORACLE_USER", "password": "RISK_ORACLE_PASSWORD",
           "embedding": "RISK_EMBEDDING_API_KEY", "llm": "RISK_LLM_API_KEY"}
_SECRETS = []  # hata mesajlarindan ayiklanacak degerler (kullanici, parola, anahtar, DSN, adresler)


def _remember(*values):
    for value in values:
        if isinstance(value, str) and len(value.strip()) >= 3 and value not in _SECRETS:
            _SECRETS.append(value)


def _remember_url(value):
    if isinstance(value, str) and value:
        _remember(value, urlsplit(value).hostname or "")


def safe_message(text):
    """Bilinen sir/adres iceren mesaji hic gostermez; yalniz guvenli kalan metni dondurur."""
    text = str(text)
    folded = text.casefold()
    if any(secret.casefold() in folded for secret in _SECRETS):
        return "ayrıntı gizlendi (bağlantı bilgisi içeriyordu)"
    return text


def parse_schemas(text):
    return list(dict.fromkeys(s.strip().upper() for s in re.split(r"[,;\s]+", text or "") if s.strip()))


def _discover(diagnostics, name, base_url, api_key, model=""):
    label = "LLM" if name == "llm" else "Embedding"
    hint = (" Yalnız ad/metin araması için --no-embedding (PowerShell: -NoEmbedding) verin." if name == "embedding"
            else " LLM yorumu olmadan ölçmek için --llm (PowerShell: -Llm) vermeyin.")
    hint += " Servis birden çok model sunuyorsa web'de seçtiğiniz model kimliğini %s ile verin." % (
        "-EmbeddingModel" if name == "embedding" else "-LlmModel")
    value = {"base_url": base_url, "api_key": api_key}
    if model:
        value["model"] = model
    try:
        result = diagnostics.discover_model({name: value}, name)
    except Exception as error:
        raise UserError("%s servisi keşfedilemedi (%s).%s" % (label, type(error).__name__, hint)) from None
    result = result if isinstance(result, dict) else {}
    settings = result.get("settings")
    if not result.get("ok") or not isinstance(settings, dict) or not settings:
        message = str(result.get("message") or "-").rstrip(".")
        raise UserError("%s servisi doğrulanamadı: %s.%s" % (label, safe_message(message), hint))
    for key in ("base_url", "endpoint", "models_endpoint"):
        _remember_url(settings.get(key))
    # Web formu dogrulanan ayarlari ve anahtar alanini birlikte gonderir (app.js settings()).
    return dict(settings, api_key=api_key)


def web_settings(args):
    """Web uygulamasinin kurdugu ayarlar: login_settings + probe_oracle + discover_model + build_run.

    -> (config, secrets, raw). config/secrets web_services.build_run ciktisidir (web ile birebir ayni).
    """
    from riskmatch import deployment, model_diagnostics, web_services
    values = {key: os.environ.get(name, "") for key, name in WEB_ENV.items()}
    values["user"] = values["user"].strip()
    _remember(values["user"], values["password"], values["embedding"], values["llm"], deployment.ORACLE_DSN)
    _remember(*re.findall(r"HOST\s*=\s*([^)\s]+)", deployment.ORACLE_DSN, re.I))
    _remember(*re.findall(r"SERVICE_NAME\s*=\s*([^)\s]+)", deployment.ORACLE_DSN, re.I))
    _remember_url(deployment.EMBEDDING_BASE_URL)
    _remember_url(deployment.LLM_BASE_URL)
    missing = [WEB_ENV[key] for key in ("user", "password") if not values[key]]
    if missing:
        raise UserError("Şu ortam değişkenleri tanımlı değil: " + ", ".join(missing)
                        + ". Değerleri bu araca yazmayın; is_pc_dogrulama.ps1 yalnız bu süreç için sorar.")
    wanted = parse_schemas(args.schemas)
    if not 1 <= len(wanted) <= 30:
        raise UserError("--schemas ile 1–30 şema verin (ör. DM_CAD).")
    raw = deployment.login_settings(values["user"], values["password"])
    try:
        probe = web_services.probe_oracle(raw)
    except Exception as error:
        raise UserError("Oracle bağlantısı doğrulanamadı (%s). Kullanıcı adı, parola ve kurum ağı erişimini kontrol edin."
                        % type(error).__name__) from None
    if not probe.get("ok"):
        raise UserError("Oracle bağlantısı kurulamadı: " + safe_message(probe.get("error") or probe.get("warning") or "-"))
    available = {str(s).upper(): str(s) for s in probe.get("schemas") or []}
    unknown = [s for s in wanted if s not in available]
    if unknown:
        # The user's own schema carries the login name; it is never listed.
        listed = sorted(v for v in available.values() if v.casefold() != values["user"].casefold())
        raise UserError("Bu kullanıcının erişebildiği şemalar arasında yok: %s. Erişilebilir şemalar (ilk 20 / %d): %s"
                        % (", ".join(unknown), len(listed), ", ".join(listed[:20]) or "-"))
    raw["oracle"].update(container=probe["container"], schemas=[available[s] for s in wanted])
    raw.update(search_mode=WEB_SEARCH_MODE, llm_review=False, use_models=False)
    if not args.no_embedding:
        raw["embedding"] = _discover(model_diagnostics, "embedding", deployment.EMBEDDING_BASE_URL, values["embedding"],
                                     getattr(args, "embedding_model", ""))
        raw["use_models"] = True
        if args.llm:
            raw["llm"] = _discover(model_diagnostics, "llm", deployment.LLM_BASE_URL, values["llm"],
                                   getattr(args, "llm_model", ""))
            raw["llm_review"] = True
    config, secrets = web_services.build_run(raw)
    return config, secrets, raw


def load_rows(args):
    mapping = None
    if args.column_mapping:
        mapping = json.loads(Path(args.column_mapping).read_text(encoding="utf-8-sig"))
    requests = read_requests(args.excel, mapping)
    # Sheets without the label column (e.g. the template's help sheet) are not part of the measurement.
    labeled = [req for req in requests if args.label_column in (req.get("original") or {})]
    if requests and not labeled:
        raise UserError("Etiket kolonu bulunamadı: " + args.label_column)
    rows, skipped = [], []
    for req in labeled:
        if req.get("input_errors"):
            skipped.append(str(req["input_errors"][0])[:160])
            continue
        label = parse_label((req.get("original") or {}).get(args.label_column))
        rows.append({"req": req, "expected": set(label["uclu"]), "pairs": label["ikili"], "etiket": label["durum"],
                     "label_valid": label["durum"] in ("pozitif", "yok"), "neden": label["neden"]})
    args._atlanan = {"adet": len(skipped), "ilk_hata": skipped[0] if skipped else ""}
    if not rows:
        raise UserError("Excel'de işlenebilir satır bulunamadı." + (
            " %d satır girdi hatasıyla atlandı; ilk hata: %s" % (len(skipped), skipped[0]) if skipped else ""))
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
    coverage_start = round(index.stored_coverage(), 4)
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
            "index_coverage": round(index.index_coverage, 4), "index_coverage_start": coverage_start, "fingerprint": _fp(ctx), "warnings": warnings,
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


# -- satir bazli teshis ---------------------------------------------------------

KNOWN_REASONS = (WEAK_REASON, LIMIT_REASON, GATE_REASON, SUBJECT_REASON)
MISS_TITLES = {"havuzda_yok": "Beklenen kolon havuzda yok", "yok_oneri": "YOK satırında aday gösterildi"}


def _path(schema, obj, column):
    return "%s.%s.%s" % (schema, obj, column)


def _round(value, digits=3):
    return None if value is None else round(value, digits)


def pool_hit(record, expected):
    column = record.get("column")
    if _triple(record.get("schema"), record.get("object"), column) in expected:
        return True
    return any(_triple(*(str(loc).split(".", 1) + [""])[:2], column) in expected
               for loc in record.get("_locations") or [])


def classify_row(row, ranked):
    """dogru_ilk3 | elendi:<neden> | havuzda_yok | yok_temiz | yok_oneri; ikinci deger beklenene isabet eden aday."""
    shown, expected = ranked["candidates"], row["expected"]
    if not expected:
        return ("yok_oneri" if shown else "yok_temiz"), None
    hit = next((c for c in shown + ranked["rejected_candidates"] if candidate_hit(c, expected)), None)
    if hit is not None and not hit.get("rejected_reason") and hit in shown[:3]:
        return "dogru_ilk3", hit
    if not any(pool_hit(r, expected) for r in row["pool"]):
        return "havuzda_yok", hit
    if hit is None:
        return "elendi:bilinmiyor", None
    reason = hit.get("rejected_reason") or LIMIT_REASON
    return "elendi:" + (reason if reason in KNOWN_REASONS else "Anlam çelişkisi"), hit


def diagnose(rows, config, complete, records):
    """Varsayilan kapiyla (elenenler tam: max_rejected buyuk) her etiketli satirin sinifi ve kaniti."""
    gate = {**gate_config(config["retrieval"].get("gate")), "max_rejected": 1000}
    catalog = {_triple(r["schema"], r["object"], r["column"]): r for r in records}
    out = []
    for row in rows:
        req, expected = row["req"], row["expected"]
        ranked = rank(req, row["pool"], gate=gate, semantic_scope=row["info"].get("semantic_scope", "none"),
                      catalog_complete=complete)
        cls, hit = classify_row(row, ranked)
        position = next((i for i, r in enumerate(row["pool"], 1) if expected and pool_hit(r, expected)), None)
        features = None
        if position:
            f = row["pool"][position - 1].get("_features") or {}
            features = {"z": _round(f.get("z")), "cos": _round(f.get("cos")), "coverage": _round(f.get("coverage")),
                        "subject_coverage": _round(f.get("subject_coverage")),
                        "subject_missing": f.get("subject_missing") or [], "name_level": f.get("name_level"),
                        "meaning": hit.get("meaning") if hit else None,
                        "checks": [{k: c.get(k) for k in ("dimension", "status", "requested", "actual")}
                                   for c in (hit or {}).get("checks") or [] if c.get("status") not in ("match", "ok")],
                        "rejected_reason": hit.get("rejected_reason") if hit else None}
        top = []
        for c in ranked["candidates"][:3]:
            src = c.get("source") or {}
            top.append({"yol": _path(src.get("schema"), src.get("object"), src.get("column")),
                        "yorum": str(src.get("column_comment") or "")[:80], "meaning": c.get("meaning"),
                        "name_match": c.get("name_match"), "z": _round((c.get("features") or {}).get("z")),
                        "coverage": _round((c.get("features") or {}).get("coverage")),
                        "hit": candidate_hit(c, expected)})
        out.append({"satir": req.get("excel_row"), "row_id": req.get("row_id"), "ad": req.get("name"),
                    "aciklama": req.get("description"), "veri_tipi": req.get("data_type"),
                    "kategori": req.get("category"), "sinif": cls, "row_status": ranked["row_status"],
                    "beklenen": sorted(_path(*t) for t in expected),
                    "beklenen_katalog": [{"yol": _path(*t), "yorum": (catalog.get(t) or {}).get("column_comment"),
                                          "veri_tipi": (catalog.get(t) or {}).get("data_type")} for t in sorted(expected)],
                    "beklenen_havuz_sirasi": position, "havuz_boyutu": len(row["pool"]),
                    "beklenen_ozellik": features, "ilk3": top})
    return out


def _cell(value, limit=60):
    text = "" if value is None else str(value)
    text = text.replace("|", "/").replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def write_diagnosis(out, records):
    Path(out, "satirlar.json").write_text(json.dumps(records, ensure_ascii=False, indent=1, default=str),
                                          encoding="utf-8")
    counts = Counter(r["sinif"] for r in records)
    lines = ["# Satır bazlı ıskalar", "", "Varsayılan kapı; elenen adaylar sınırsız incelendi. Ayrıntı: satirlar.json.",
             "", "| Sınıf | Satır |", "|---|---|"]
    lines += ["| %s | %d |" % (name, n) for name, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]
    for name in sorted(n for n in counts if n not in ("dogru_ilk3", "yok_temiz")):
        group = [r for r in records if r["sinif"] == name]
        title = MISS_TITLES.get(name) or ("Beklenen kolon elendi: " + name.split(":", 1)[1] if name.startswith("elendi:") else name)
        lines += ["", "## %s (%d)" % (title, len(group)), "",
                  "| Satır | Ad | Veri tipi | Beklenen | Havuz sırası | z | Kapsam | Konu eksik | Gösterilen ilk aday |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in group[:25]:
            f = r["beklenen_ozellik"] or {}
            first = r["ilk3"][0] if r["ilk3"] else None
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["satir"], _cell(r["ad"], 40), _cell(r["veri_tipi"], 12), _cell("; ".join(r["beklenen"]) or "YOK", 60),
                r["beklenen_havuz_sirasi"] or "-", f.get("z", "-"), f.get("coverage", "-"),
                _cell(", ".join(f.get("subject_missing") or []), 30),
                _cell("%s (%s)" % (first["yol"], first["meaning"]), 60) if first else "-"))
        if len(group) > 25:
            lines.append("")
            lines.append("… ve %d satır daha (satirlar.json)." % (len(group) - 25))
    Path(out, "iskalar.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dict(counts)


# -- katalog metadata ozeti -----------------------------------------------------

def _lexicon_knows(lexicon, word):
    if word.isdigit() or len(word) < 2:
        return True
    norm = lexicon._norm_word(word)
    return (lexicon.known(word) or norm in lexicon._canon or lexicon.is_generic(norm) or lexicon.is_structural(norm)
            or lexicon._unit(word, True) is not None)


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def catalog_summary(records, request_names, lexicon):
    """Yalniz metadata: sayilar, doluluk, tip dagilimi, sozlukte olmayan tokenlar (kisaltma adaylari)."""
    tables = {}
    for r in records:
        key = (r.get("schema"), r.get("object"))
        tables[key] = tables.get(key) or bool(str(r.get("table_comment") or "").strip())
    unknown, examples, lengths = Counter(), {}, Counter()
    for r in records:
        column = str(r.get("column") or "")
        words = lexicon_words(column)
        lengths[str(len(words)) if len(words) < 6 else "6+"] += 1
        for word in set(words):
            if not _lexicon_knows(lexicon, word):
                unknown[word] += 1
                bucket = examples.setdefault(word, [])
                if len(bucket) < 2 and column not in bucket:
                    bucket.append(column)
    asked = Counter()
    for name in request_names:
        for word in set(lexicon_words(name)):
            if not _lexicon_knows(lexicon, word):
                asked[word] += 1
    commented = sum(1 for r in records if str(r.get("column_comment") or "").strip())
    return {"kolon": len(records), "tablo": len(tables),
            "kolon_yorum_doluluk_yuzde": _pct(commented, len(records)),
            "tablo_yorum_doluluk_yuzde": _pct(sum(tables.values()), len(tables)),
            "veri_tipi_ilk10": Counter(str(r.get("data_type") or "?") for r in records).most_common(10),
            "kolon_adi_token_sayisi": dict(sorted(lengths.items())),
            "sozlukte_olmayan_kolon_tokenlari": [{"token": t, "adet": n, "ornek": examples[t]}
                                                 for t, n in unknown.most_common(60)],
            "talep_adi_sayisi": len(request_names),
            "sozlukte_olmayan_talep_tokenlari": [{"token": t, "adet": n} for t, n in asked.most_common(40)]}


def write_catalog_summary(out, summary):
    Path(out, "katalog_ozet.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# Katalog metadata özeti", "", "Yalnız metadata (sayılar, kolon adı parçaları); müşteri verisi yok.", "",
             "- Kolon: %d; tablo: %d" % (summary["kolon"], summary["tablo"]),
             "- Kolon yorumu doluluk: %%%s; tablo yorumu doluluk: %%%s" % (
                 summary["kolon_yorum_doluluk_yuzde"], summary["tablo_yorum_doluluk_yuzde"]),
             "- Veri tipi (ilk 10): " + ", ".join("%s %d" % (t, n) for t, n in summary["veri_tipi_ilk10"]),
             "- Kolon adındaki parça sayısı: " + ", ".join("%s: %d" % kv for kv in summary["kolon_adi_token_sayisi"].items()),
             "", "## Sözlükte karşılığı olmayan kolon adı parçaları (kısaltma adayları, ilk 60)", "",
             "| Parça | Kolon | Örnek kolon adları |", "|---|---|---|"]
    lines += ["| %s | %d | %s |" % (x["token"], x["adet"], ", ".join(x["ornek"])) for x in summary["sozlukte_olmayan_kolon_tokenlari"]]
    lines += ["", "## Talep adlarında sözlükte karşılığı olmayan parçalar (ilk 40; %d talep)" % summary["talep_adi_sayisi"], "",
              "| Parça | Talep |", "|---|---|"]
    lines += ["| %s | %d |" % (x["token"], x["adet"]) for x in summary["sozlukte_olmayan_talep_tokenlari"]]
    Path(out, "katalog_ozet.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    satir = report["satir"]
    source = {"cache": "önbellekten (yaş %s sn)" % kurulum.get("katalog_yasi_s"), "scan": "Oracle'dan tarandı"}
    start, end = kurulum.get("index_coverage_baslangic"), kurulum["index_coverage"]
    lines = ["# Ölçüm raporu", "",
             "- Katalog: %s; önbellek dizini: %s" % (source.get(kurulum["katalog_kaynak"], "-"), kurulum.get("onbellek_dizini")),
             "- Anlamsal indeks kapsamı: başta %s, sonda %s" % (
                 "-" if start is None else "%%%.1f" % (100 * start), "-" if end is None else "%%%.1f" % (100 * end))]
    if kurulum["strateji"] == "hybrid" and end is not None and end < 1.0:
        lines.append("- **Anlamsal indeks %%%.1f — sonuçlar eksik kapsamla ölçüldü; -BuildIndex ile tekrar koşun**" % (100 * end))
    elif kurulum["strateji"] == "lexical":
        lines.append("- **Embedding yok: yalnız ad/metin araması ölçüldü.**")
    lines += ["- Etiket: pozitif %d, YOK %d, etiketsiz %d (ölçüm dışı), geçersiz %d" % (
        satir["pozitif"], satir["yok"], satir["etiketsiz"], satir["gecersiz_etiket"])]
    for item in satir.get("gecersiz_ornekler", []):
        lines.append("  - satır %s `%s`: %s" % (item["satir"], item["ad"], item["neden"]))
    skipped = satir.get("girdi_hatasi_atlanan") or {}
    if skipped.get("adet"):
        lines.append("- Girdi hatası nedeniyle atlanan satır: %d (ilk hata: %s)" % (skipped["adet"], skipped["ilk_hata"]))
    if report.get("satir_siniflari"):
        lines.append("- Satır sınıfları (varsayılan kapı, iskalar.md): " + ", ".join(
            "%s %d" % kv for kv in sorted(report["satir_siniflari"].items(), key=lambda x: (-x[1], x[0]))))
    lines += ["", "## Kurulum", "",
             "- Mod: %s" % report["mod"], "- Sürüm: %s (commit: %s)" % (kurulum["surum"], kurulum["git_commit"] or "yok"),
             "- Katalog kolonu: %s; tarama tamam: %s; kaynak: %s" % (kurulum["katalog_kolon"], kurulum["katalog_tam"], kurulum["katalog_kaynak"]),
             "- Ayar kaynağı: %s" % {"web": "web uygulamasıyla aynı (login_settings + keşif + build_run)",
                                      "config": "--config dosyası"}.get(report.get("ayar_kaynagi"), "sentetik düzenek"),
             "- Kapsam: %s" % json.dumps(kurulum["kapsam"], ensure_ascii=False),
             "- Modeller: embedding %s (model: %s, boyut: %s); LLM yorumu %s; arama modu: %s" % (
                 "açık" if kurulum["modeller"]["embedding"] else "**kapalı**", kurulum["modeller"]["embedding_model"] or "-",
                 kurulum["modeller"]["embedding_boyut"] or "-", "açık" if kurulum["modeller"]["llm_yorumu"] else "kapalı",
                 kurulum["modeller"]["search_mode"]),
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
    p.add_argument("--config", help="config/real.json (gerçek mod, eski yol)")
    p.add_argument("--web-ayarlari", dest="web", action="store_true",
                   help="web uygulamasıyla aynı bağlantı/model ayarları (--fixture ve --config yoksa varsayılan)")
    p.add_argument("--schemas", help="taranacak şemalar, ör. DM_CAD,DM_RISK (web-eşdeğer modda zorunlu; --config'i geçersiz kılar)")
    p.add_argument("--llm", action="store_true", help="web-eşdeğer: LLM yorumu (LLM_BASE_URL keşfi)")
    p.add_argument("--no-embedding", action="store_true", help="web-eşdeğer: embedding yok, yalnız ad/metin araması")
    p.add_argument("--embedding-model", default="", help="web-eşdeğer: servis birden çok model sunuyorsa web'de seçilen model kimliği")
    p.add_argument("--llm-model", default="", help="web-eşdeğer: --llm ile; web'de seçilen LLM model kimliği")
    p.add_argument("--excel", help="etiketli Excel (gerçek mod)")
    p.add_argument("--label-column", default="Beklenen kolon")
    p.add_argument("--column-mapping", help="read_requests için kolon eşleme JSON dosyası")
    p.add_argument("--rows", type=int, default=0, help="ilk N satır (0 = tümü)")
    p.add_argument("--state-dir", help="vektör/katalog önbelleği (varsayılan geçici)")
    p.add_argument("--resources-dir", help="katalog/vektör önbelleği bu dizinden (web ile aynı yerleşim, ör. runtime/web)")
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
    if sum((args.fixture, bool(args.config), args.web)) > 1:
        p.error("--fixture, --config ve --web-ayarlari birlikte verilemez.")
    # Config dosyasi gerektirmeyen web-esdeger mod varsayilan: is bilgisayarinda config/real.json yok.
    args.web = not args.fixture and not args.config
    if args.web and not args.schemas:
        p.error("Web-eşdeğer modda --schemas gerekli (ör. --schemas DM_CAD).")
    if not args.web and (args.llm or args.no_embedding or args.embedding_model or args.llm_model):
        p.error("--llm, --no-embedding ve model kimlikleri yalnız web-eşdeğer modda (--config/--fixture olmadan).")
    if args.llm and args.no_embedding:
        p.error("--llm embedding olmadan kullanılamaz (web'de de LLM yorumu embedding bağlantısı ister).")
    if (args.llm_model and not args.llm) or (args.embedding_model and args.no_embedding):
        p.error("--llm-model yalnız --llm ile, --embedding-model yalnız embedding açıkken.")
    if args.fixture and args.schemas:
        p.error("--schemas yalnız gerçek modda.")
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
    resources_dir = Path(args.resources_dir) if args.resources_dir else work / "resources"
    resources = Resources(resources_dir)
    report["_onbellek"] = "--resources-dir" if args.resources_dir else "geçici/--state-dir"
    try:
        with contextlib.ExitStack() as stack:
            if real and args.config:
                config = load_config(args.config, "real")
                credentials = real_credentials(config)
                analyze_kwargs = {"mode": "real", "config_path": args.config, "credentials": credentials}
                if args.schemas:
                    config["oracle"]["schemas"] = parse_schemas(args.schemas)
                    analyze_kwargs = {"mode": "real", "config_data": config, "credentials": credentials}
            elif real:
                # Web ile ayni: build_run ciktisi analyze'a config_data olarak gider (web.py worker).
                config, credentials, _ = web_settings(args)
                analyze_kwargs = {"mode": "real", "config_data": config, "credentials": credentials}
            if real:
                report["ayar_kaynagi"] = "config" if args.config else "web"
                from riskmatch.adapters.oracle import OracleAdapter
                from riskmatch.adapters.qwen import EmbeddingAdapter
                adapter = (EmbeddingAdapter(config["embedding"], api_key=credentials.get("embedding", ""))
                           if config.get("use_models", True) else None)
                oracle_adapter = OracleAdapter(config["oracle"], credentials=credentials["oracle"])
                # web.py index_inputs/analyze ile ayni anahtar: --resources-dir runtime/web onbellegi paylasilir.
                key = catalog_key(config["oracle"], credentials["oracle"])
                rows = load_rows(args) if args.excel else []
                report["_atlanan"] = getattr(args, "_atlanan", None)
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
                rows = [{"req": req, "expected": {_triple(*by_id[c]) for c in label}, "pairs": set(),
                         "etiket": "pozitif" if label else "yok", "label_valid": True, "neden": ""}
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
            resolve_labels(rows, snapshot["records"])
            scored = [r for r in rows if r["label_valid"]]
            if not scored:
                raise UserError("Ölçülecek etiketli satır yok (etiket kolonu boş ya da tüm etiketler geçersiz).")
            pools = build_pools(scored, snapshot, config, resources, adapter, args)
            report["uyarilar"] += pools["warnings"]
            save_pools(out / "havuz.json", scored)
            write_catalog_summary(out, catalog_summary(snapshot["records"], [r["req"]["name"] for r in rows],
                                                       resources._lexicon or load_lexicon()))
            report["satir_siniflari"] = write_diagnosis(
                out, diagnose(scored, config, bool(snapshot["complete"]), snapshot["records"]))
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


def model_summary(config, pools):
    """Yalniz model kimligi/boyutu ve secimler; adres ve anahtar yazilmaz."""
    use_models = bool(config.get("use_models", True))
    embedding = config.get("embedding") or {} if use_models else {}
    dims = embedding.get("dimensions") or ((pools or {}).get("fingerprint") or {}).get("dims")
    model = embedding.get("model") or ("sunucu varsayılanı" if embedding.get("model_selection") == "server_default" else None)
    return {"embedding": bool(embedding), "embedding_model": model if embedding else None,
            "embedding_boyut": dims if embedding else None,
            "llm_yorumu": bool(use_models and config.get("llm_review", True) and config.get("llm")),
            "search_mode": config.get("retrieval", {}).get("search_mode")}


def finish(report, out, *, config, snapshot, rows, pools, real, catalog_info=None):
    invalid = [r for r in rows if r.get("etiket") == "gecersiz"]
    valid = [r for r in rows if r["label_valid"]]
    report["satir"] = {"toplam": len(rows), "pozitif": sum(1 for r in valid if r["expected"]),
                       "yok": sum(1 for r in valid if not r["expected"]),
                       "etiketsiz": sum(1 for r in rows if r.get("etiket") == "etiketsiz"),
                       "gecersiz_etiket": len(invalid),
                       "girdi_hatasi_atlanan": report.pop("_atlanan", None) or {"adet": 0, "ilk_hata": ""},
                       "gecersiz_ornekler": [{"satir": r["req"].get("excel_row"), "ad": r["req"].get("name"),
                                              "neden": r.get("neden")} for r in invalid[:10]]}
    oracle = config["oracle"]
    report["kurulum"] = {
        "surum": __version__, "git_commit": git_commit(),
        "onbellek_dizini": report.pop("_onbellek", None),
        "katalog_yasi_s": _round((catalog_info or {}).get("age_seconds"), 0),
        "index_coverage_baslangic": pools["index_coverage_start"] if pools else None,
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
        "modeller": model_summary(config, pools),
    }
    report.setdefault("ayar_kaynagi", "sentetik")
    (out / "rapor.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_markdown(report, out / "rapor.md")
    return report


def main(argv=None):
    args = parse_args(argv)
    del _SECRETS[:]
    try:
        report = evaluate(args)
    except UserError as error:
        print("HATA: " + safe_message(error), file=sys.stderr)
        return 2
    except (ConfigError, InputError, IntegrationError, OSError) as error:
        print("HATA (%s): %s" % (type(error).__name__, safe_message(error)), file=sys.stderr)
        return 2
    except Exception as error:
        if args.fixture:
            raise
        # Ham metin/iz DSN, adres ya da kullanici adi tasiyabilir: yalniz tur adi.
        print("HATA (%s): beklenmeyen hata; ayrıntı gizlendi." % type(error).__name__, file=sys.stderr)
        return 2
    finally:
        del _SECRETS[:]
    print("Rapor: " + str(Path(args.out).resolve() / "rapor.md"))
    return 0 if report else 1


if __name__ == "__main__":
    sys.exit(main())
