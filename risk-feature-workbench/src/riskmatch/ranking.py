"""Display gate over the background retrieval pool. Pure and deterministic.

The pool (<= ~30 records with ``_features``) is never shown as is. Physical name
match and business meaning are judged separately; a candidate is shown only when
the name, or the semantic and lexical evidence, clearly relates it to the request.
Scores are internal ordering keys, never percentages.

Decisions:
- A conflicting evaluation sets meaning "different" before the empty-comment
  check, so the exact-name exception (rule 3) always reads "different".
- A candidate that passes the gate only through the lexical rule (z unknown) is
  "weak" unless its evaluation is supported; a supported one follows the meaning
  rule (coverage >= 0.8 with z unknown gives "strong").
- Subject rule (require_subject): the semantic rule (z >= z_min, coverage >= c_min)
  and the lexical rule also need subject_coverage > 0 when it is known, because
  window/measure/direction/aggregation tokens alone can fill coverage. The
  z >= z_strong rule (synonyms outside the lexicon) and name rules are exempt.
- Deduplication happens before the gate: the representative of a
  (column, comment) group is the first in (schema, object, catalog_id) order and
  carries its own features; the others become "locations". Retrieval already
  collapses groups and passes the other members as ``_locations``.
- The margin cut measures against the best meaning class shown (strong/likely if
  any), so a candidate of a lower class never cuts one of a higher class.
"""
from __future__ import annotations

import re

from .errors import ConfigError
from .lexicon import fold
from .semantics import differences, evaluate_candidate


GATE_DEFAULTS = {"z_min": 2.5, "c_min": 0.5, "z_strong": 4.0, "c_low": 0.3, "margin": 1.5,
                 "lexical_min_coverage": 0.8, "lexical_min_tokens": 2, "max_shown": 3, "max_rejected": 5,
                 "require_subject": True, "table_bonus": 0.0, "fact_min": 4}
NAME_BONUS = {"exact": 3.0, "normalized": 2.0, "partial": 0.5, "none": 0.0}
MEANING_ORDER = {"strong": 0, "likely": 1, "different": 2, "weak": 3, "no_description": 4}
SCOPES = ("none", "partial", "full")
_INTEGER_KEYS = {"lexical_min_tokens", "max_shown", "max_rejected", "fact_min"}
_FRACTION_KEYS = {"c_min", "c_low", "lexical_min_coverage"}
_BOOL_KEYS = {"require_subject"}
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

WEAK_REASON = "En iyi adaydan belirgin şekilde zayıf"
LIMIT_REASON = "Gösterim sınırı"
GATE_REASON = "Eşik altı: anlamsal benzerlik/kelime örtüşmesi yetersiz"
SUBJECT_REASON = "Eşik altı: talebin konu kelimesi adayda yok (yalnız süre/ölçü/yön benzer)"
_NO_SUBJECT = "no_subject"
NOTE_NO_SEMANTIC = "Anlamsal benzerlik ölçülemedi; yalnız ad ve kelime kanıtı kullanıldı."
NOTE_PARTIAL_SCOPE = "Anlamsal arama katalogun yalnız bir kısmında yapıldı (indeks eksik)."
NOTE_SMALL_BACKGROUND = "Katalog küçük; anlamsal benzerlik ölçeği sınırlı."


def gate_config(gate=None):
    """Merge a partial gate over the defaults; unknown keys or bad values raise ConfigError."""
    config = dict(GATE_DEFAULTS)
    if gate is None:
        return config
    if not isinstance(gate, dict):
        raise ConfigError("Sıralama eşikleri sözlük olmalı.")
    unknown = sorted(set(gate) - set(GATE_DEFAULTS))
    if unknown:
        raise ConfigError("Bilinmeyen sıralama eşiği: " + ", ".join(map(str, unknown)))
    for key, value in gate.items():
        if key in _BOOL_KEYS:
            if not isinstance(value, bool):
                raise ConfigError("Sıralama ayarı true/false olmalı: " + key)
            config[key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            raise ConfigError("Sıralama eşiği sayı olmalı: " + key)
        if key in _INTEGER_KEYS and (not isinstance(value, int) or value < 0):
            raise ConfigError("Sıralama eşiği negatif olmayan tam sayı olmalı: " + key)
        if key in _FRACTION_KEYS and not 0.0 <= value <= 1.0:
            raise ConfigError("Sıralama eşiği 0 ile 1 arasında olmalı: " + key)
        if key in ("margin", "z_min", "z_strong", "table_bonus") and (value < 0 or value == float("inf")):
            raise ConfigError("Sıralama eşiği negatif olmayan sonlu sayı olmalı: " + key)
        config[key] = value
    if config["max_shown"] < 1:
        raise ConfigError("max_shown en az 1 olmalı.")
    if config["z_strong"] < config["z_min"]:
        raise ConfigError("z_strong, z_min değerinden küçük olamaz.")
    if config["c_low"] > config["c_min"]:
        raise ConfigError("c_low, c_min değerinden büyük olamaz.")
    return config


def _features(record):
    features = record.get("_features") or {}
    level = features.get("name_level")
    subject = features.get("subject_coverage")
    return {"z": features.get("z"), "cos": features.get("cos"),
            "coverage": float(features.get("coverage") or 0.0),
            "subject_coverage": None if subject is None else float(subject),
            "subject_missing": list(features.get("subject_missing") or []),
            "name_level": level if level in NAME_BONUS else "none",
            "channels": list(features.get("channels") or []),
            "distinctive_matched": int(features.get("distinctive_matched") or 0),
            "query_tokens": features.get("query_tokens"),
            "background_small": bool(features.get("background_small"))}


def _no_subject(features, config):
    return config["require_subject"] and features["subject_coverage"] is not None and features["subject_coverage"] <= 0


def _gate(features, config):
    """Return "strong" (name or semantic gate), "weak" (lexical only), _NO_SUBJECT or None."""
    z, coverage = features["z"], features["coverage"]
    if features["name_level"] in ("exact", "normalized"):
        return "strong"
    if z is not None:
        if z >= config["z_strong"] and coverage >= config["c_low"]:
            return "strong"
        if z >= config["z_min"] and coverage >= config["c_min"]:
            return _NO_SUBJECT if _no_subject(features, config) else "strong"
        return None
    # Tek kelimelik talep de geçebilsin: gereken token sayısı talepteki konu tokenı sayısını aşmaz.
    min_tokens = config["lexical_min_tokens"]
    if isinstance(features["query_tokens"], int):
        min_tokens = min(min_tokens, max(1, features["query_tokens"]))
    if coverage >= config["lexical_min_coverage"] and features["distinctive_matched"] >= min_tokens:
        return _NO_SUBJECT if _no_subject(features, config) else "weak"
    return None


def _fact_supported(evaluation, features, config):
    """Embedding eşiği geçilmese de talebin en az fact_min anlam boyutu kolon açıklamasıyla doğrulandıysa aday kalır.
    Ad kuralından gelen olgu sayılmaz: benzer ad tek başına kanıt değildir."""
    agreed = sum(1 for check in evaluation.get("checks", [])
                 if check["status"] == "compatible" and check["dimension"] not in ("data_type", "qualifier")
                 and "naming_rule" not in ((check.get("evidence") or {}).get("request_source"),
                                           (check.get("evidence") or {}).get("candidate_source")))
    return (config["fact_min"] > 0 and evaluation.get("status") == "supported" and agreed >= config["fact_min"]
            and features["coverage"] >= config["c_min"] and not _no_subject(features, config))


def _meaning(record, evaluation, features, gate, config):
    z, coverage, level = features["z"], features["coverage"], features["name_level"]
    supported = evaluation["status"] == "supported"
    if evaluation["status"] == "conflict":
        return "different"
    if not str(record.get("column_comment") or "").strip():
        return "no_description"
    if gate == "weak" and not supported:
        return "weak"
    if supported and ((z is not None and z >= config["z_strong"]) or level in ("exact", "normalized")
                      or (z is None and coverage >= 0.8)):
        return "strong"
    if supported or (z is not None and z >= config["z_min"] and coverage >= config["c_min"]
                     and not _no_subject(features, config)):
        return "likely"
    return "weak"


def _words(*texts):
    return {w for text in texts for w in _WORD.findall(fold(text)) if len(w) >= 3 and not w.isdigit()}


def _evidence(request, record, features, config):
    out = []
    level = features["name_level"]
    if level == "exact":
        out.append("Kolon adı birebir aynı")
    elif level == "normalized":
        out.append("Ad kısaltma açılımıyla aynı")
    elif level == "partial":
        out.append("Ad kısmen benzer")
    if features["distinctive_matched"]:
        shared = sorted(_words(request.get("name"), request.get("description")) & _words(record.get("column"), record.get("column_comment")))
        if shared:
            out.append("Ortak ayırt edici kelimeler: " + ", ".join(shared[:6]))
    if _no_subject(features, config):
        missing = features["subject_missing"]
        out.append("Konu kelimesi farklı: talep " + (", ".join(missing[:4]) if missing else "başka bir konu")
                   + " içeriyor, aday içermiyor")
    z = features["z"]
    if z is not None and z >= config["z_min"]:
        out.append("Anlamsal benzerlik arka plana göre yüksek (z=" + ("%.1f" % z).replace(".", ",") + ")")
    table_rank = (record.get("_features") or {}).get("table_rank")
    if isinstance(table_rank, int) and not isinstance(table_rank, bool) and 1 <= table_rank <= 3:
        out.append("Tablo açıklaması talep konusuyla uyumlu (tablo sırası %d)" % table_rank)
    return out


def _score(features):
    return round((features["z"] or 0.0) + 1.5 * features["coverage"] + NAME_BONUS[features["name_level"]], 6)


def _location(record):
    return str(record.get("schema") or "?") + "." + str(record.get("object") or "?")


def _order(record):
    return (str(record.get("schema") or ""), str(record.get("object") or ""), str(record.get("catalog_id") or ""))


def _candidate(request, record, evaluation, locations, config):
    features = _features(record)
    gate = _gate(features, config)
    if gate is None and _fact_supported(evaluation, features, config):
        gate = "weak"
    candidate = dict(evaluation)
    candidate.update({
        "catalog_id": record.get("catalog_id", ""),
        "source": {k: record.get(k) for k in ("schema", "object", "column", "data_type", "column_comment", "table_comment")},
        "locations": locations,
        "name_match": features["name_level"],
        "meaning": _meaning(record, evaluation, features, gate, config),
        "differences": differences(evaluation),
        "evidence": _evidence(request, record, features, config),
        "features": {k: features[k] for k in ("z", "cos", "coverage", "channels")},
        "score": _score(features),
        "retrieval": features["channels"],
    })
    return candidate, gate


def _sort_key(candidate):
    # An exact physical name leads unless its meaning is "different", which keeps its class place.
    lead = 0 if candidate["name_match"] == "exact" and candidate["meaning"] != "different" else 1
    return (lead, MEANING_ORDER[candidate["meaning"]], -candidate["score"], str(candidate["catalog_id"]))


def _rejected(candidate, reason):
    out = dict(candidate)
    out["rejected_reason"] = reason
    return out


def rank(request, pool, *, gate=None, semantic_scope="none", catalog_complete=False):
    config = gate_config(gate)
    if semantic_scope not in SCOPES:
        raise ConfigError("Bilinmeyen anlamsal kapsam: " + str(semantic_scope))
    records = sorted(pool or [], key=_order)
    evaluations = [evaluate_candidate(request, record) for record in records]
    groups = {}
    for record, evaluation in zip(records, evaluations):
        key = tuple(" ".join(fold(record.get(k)).split()) for k in ("column", "column_comment"))
        groups.setdefault(key, []).append((record, evaluation))
    shown, conflicts, cut, below = [], [], [], []
    for members in groups.values():
        record, evaluation = members[0]
        # Arama havuzu kopyaları önceden toplamışsa temsilci onları "_locations" ile taşır.
        found = set(record.get("_locations") or [])
        for other, _ in members[1:]:
            found.add(_location(other))
            found.update(other.get("_locations") or [])
        found.discard(_location(record))
        locations = sorted(found)
        candidate, passed = _candidate(request, record, evaluation, locations, config)
        if evaluation["status"] == "conflict" and candidate["name_match"] != "exact":
            reason = "; ".join(d["text"] for d in candidate["differences"]) or "Anlam çelişkisi"
            conflicts.append(_rejected(candidate, reason))
        elif passed in ("strong", "weak") or candidate["name_match"] == "exact":
            shown.append(candidate)
        else:
            below.append(_rejected(candidate, SUBJECT_REASON if passed == _NO_SUBJECT else GATE_REASON))
    shown.sort(key=_sort_key)
    if shown:
        # Marj en iyi anlam sınıfına göre: daha zayıf sınıftaki aday güçlü/olası adayı kesemez.
        top = min(MEANING_ORDER[c["meaning"]] for c in shown)
        leaders = ([c for c in shown if c["meaning"] in ("strong", "likely")]
                   or [c for c in shown if MEANING_ORDER[c["meaning"]] == top])
        best = max(c["score"] for c in leaders)
        kept = []
        for candidate in shown:
            if candidate["name_match"] != "exact" and candidate["score"] < best - config["margin"]:
                cut.append(_rejected(candidate, WEAK_REASON))
            else:
                kept.append(candidate)
        shown = kept
    cut.extend(_rejected(c, LIMIT_REASON) for c in shown[config["max_shown"]:])
    shown = shown[:config["max_shown"]]
    by_strength = lambda c: (-c["score"], str(c["catalog_id"]))
    rejected = (sorted(conflicts, key=by_strength) + sorted(cut, key=by_strength)
                + sorted(below, key=by_strength))[:config["max_rejected"]]
    if any(c["meaning"] in ("strong", "likely") or c["name_match"] == "exact" for c in shown):
        status = "found"
    elif shown:
        status = "weak_only"
    elif catalog_complete and semantic_scope == "full":
        status = "none_in_scope"
    else:
        status = "none_partial"
    notes = []
    if semantic_scope == "none" or (records and all(_features(r)["z"] is None for r in records)):
        notes.append(NOTE_NO_SEMANTIC)
    if semantic_scope == "partial":
        notes.append(NOTE_PARTIAL_SCOPE)
    if any(_features(r)["background_small"] for r in records):
        notes.append(NOTE_SMALL_BACKGROUND)
    return {"candidates": shown, "rejected_candidates": rejected, "row_status": status, "notes": notes}
