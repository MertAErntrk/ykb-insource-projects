"""Katmanlı arama, oran türetme ve satır kararı. Saf fonksiyonlar; I/O yok.

Arama `run(tier_req, tier)` ile dışarıdan verilir; burada yalnız sıralı katman
gezintisi ve kanıta dayalı karar kuralları vardır. İlişki uydurulmaz: farklı
nesnelerdeki pay/payda yalnız katalogdaki doğrulanmış ilişkiyle türetilebilir.
"""
from __future__ import annotations

import re

from .semantics import DIFFERENCE_LABELS, normalize, parse_definition


VERDICTS = ("dogrudan", "turetilebilir", "belirsiz", "bulunamadi", "karar_verilemedi")
VERDICT_LABELS = {
    "dogrudan": "Doğrudan eşleşti",
    "turetilebilir": "Türetilebilir",
    "belirsiz": "Belirsiz",
    "bulunamadi": "Veritabanında bulunamadı",
    "karar_verilemedi": "Karar verilemedi",
}
SCOPE_ORDER = {"none": 0, "partial": 1, "full": 2}
ROLE_LABELS = {"numerator": "Pay", "denominator": "Payda"}
MAX_REJECTED = 5
MAX_CLOSEST = 3
# Bileşen talebine oranın kendisinden taşınmaması gereken boyutlar; pencere bileşen adından gelir.
OPERAND_DROPPED = {"numerator", "denominator", "window", "window_bounds", "zero_policy", "null_policy"}
# "adedinin", "sayısına" gibi çekimli ölçü sözcükleri yalın biçime indirilir.
INFLECTED_MEASURE = re.compile(r"\b(aded|sayis)i(?:nin|ne|ni|nde|ndeki|nden)\b")
ISSUE_TEXT = {
    "unsupported_formula": "formül biçimi desteklenmiyor",
    "unparsed_formula_qualifier": "formüldeki ek ifade anlaşılamadı",
    "ambiguous_ratio_name": "oran adı belirsiz",
    "ambiguous_grain": "kırılım belirsiz",
    "ambiguous_aggregation_order": "toplama sırası belirsiz",
    "ambiguous_income_period": "gelir dönemi belirsiz",
    "unsupported_scope_negation": "olumsuz kapsam ifadesi (değil/hariç) desteklenmiyor",
    "conflict:ratio_direction": "oran yönü adla çelişiyor",
    "conflict:ratio_window": "oran dönemi adla çelişiyor",
}
ENTITY_TEXT = {"customer": "müşteri", "application": "başvuru"}

REASON_TEXT = {
    "unit_unverified": "Talep birim belirtmiyor, kolon TL tutarı; birim kontrol edilmeli.",
    "entity_unverified": "Talep varlık belirtmiyor, kolon {detail} düzeyinde; düzey kontrol edilmeli.",
    "window_name_mismatch": "Kolon adındaki dönem açıklamadakinden farklı ({detail}); açıklama esas alındı.",
    "unverified_dimension": "{label} bilgisi doğrulanamadı.",
    "conflict_dimension": "{label} bilgisi talepten farklı.",
    "weak_evidence": "Adayın anlam kanıtı zayıf; eşleşme incelenmeli.",
    "candidate_no_description": "Adayın kolon açıklaması yok; anlamı doğrulanamadı.",
    "candidate_unverified": "Aday talep tanımıyla tam doğrulanamadı.",
    "closest_rejected": "{path}: {detail}",
    "tier_not_selected": "{value} şeması seçilmediği için aranmadı.",
    "catalog_incomplete": "{value} şemasının katalog taraması eksik; kolon orada olabilir.",
    "semantic_partial": "Anlamsal arama kataloğun tamamını kapsamadı; eşleşme kaçırılmış olabilir.",
    "definition_issue": "Talep tanımı tam anlaşılamadı: {detail}.",
    "operand_not_found": "{role} ({name}) için katalogda kolon bulunamadı.",
    "operand_weak": "{role} ({name}) için yalnız doğrulanamamış aday var.",
    "operand_facts_incomplete": "{role} ({name}) için ölçü (adet/tutar) veya işlem türü açıklamadan çıkarılamadı.",
    "cross_object_relationship_unverified": "Pay ve payda farklı tablolarda; aralarında doğrulanmış ilişki yok.",
    "derived_same_object": "Pay ve payda aynı tabloda doğrudan bulundu.",
    "derived_verified_relationship": "Pay ve payda doğrulanmış ilişkiyle bağlı tablolarda doğrudan bulundu.",
}


def _issue_text(issue):
    issue = str(issue or "")
    if issue in ISSUE_TEXT:
        return ISSUE_TEXT[issue]
    if issue.startswith("conflict:"):
        dim = issue.split(":", 1)[1]
        return DIFFERENCE_LABELS.get(dim, "bir") + " bilgisi açıklamada çelişkili"
    return "tanımda çözülemeyen bir ifade var"


def reason_text(code, **ctx):
    """Kod -> Türkçe cümle. Ham kod hiçbir zaman metin olarak dönmez."""
    base, _, value = str(code or "").partition(":")
    value = ctx.pop("value", value)
    fields = {"value": value, "detail": "", "path": "", "name": "", "label": DIFFERENCE_LABELS.get(value, "Bir boyut"),
              "role": ROLE_LABELS.get(value, "Bileşen")}
    if base == "definition_issue":
        fields["detail"] = _issue_text(value)
    if base in ("operand_not_found", "operand_weak", "operand_facts_incomplete"):
        fields["role"] = ROLE_LABELS.get(value, "Bileşen")
    fields.update({k: v for k, v in ctx.items() if v is not None})
    template = REASON_TEXT.get(base)
    if template is None:
        return "Ek inceleme gerekiyor."
    return template.format(**fields).replace(" ()", "")


def _reason(code, **extra):
    ctx = {k: v for k, v in extra.items() if k not in ("component", "dimension")}
    out = {"code": code, "text": reason_text(code, **ctx)}
    for key in ("component", "dimension"):
        if extra.get(key) is not None:
            out[key] = extra[key]
    return out


def plan_tiers(selected_schemas, schema_tiers):
    selected = [str(s) for s in (selected_schemas or [])]
    if not schema_tiers:
        return {"tiers": [{"name": "TUMU", "schemas": list(selected)}], "unselected": []}
    by_key = {}
    for schema in selected:
        by_key.setdefault(schema.upper(), schema)
    tiers, unselected, placed = [], [], set()
    for tier in schema_tiers:
        schemas = []
        for schema in tier.get("schemas") or []:
            key = str(schema).upper()
            if key in placed:
                continue
            placed.add(key)
            if key in by_key:
                schemas.append(by_key[key])
            else:
                unselected.append(str(schema))
        if schemas:
            tiers.append({"name": tier.get("name"), "schemas": schemas})
    rest = sorted((s for k, s in by_key.items() if k not in placed), key=str.upper)
    if rest:
        tiers.append({"name": "DIGER", "schemas": rest})
    return {"tiers": tiers, "unselected": unselected}


def is_direct(candidate):
    return bool(candidate) and candidate.get("meaning") in ("strong", "likely") and candidate.get("status") == "supported"


def _path(candidate):
    source = candidate.get("source") or {}
    return ".".join(str(source.get(k) or "?") for k in ("schema", "object", "column"))


def _direct_candidate(ranked):
    return next((c for c in (ranked or {}).get("candidates") or [] if is_direct(c)), None)


def tiered_search(req, tiers, run):
    trace, searched, tried = [], [], []
    chosen = None
    for tier in tiers:
        tier_req = {**req, "scope": {**(req.get("scope") or {}), "schemas": tier["schemas"]}}
        records, info, ranked = run(tier_req, tier)
        info, ranked = info or {}, ranked or {}
        candidates = ranked.get("candidates") or []
        top = candidates[0] if candidates else None
        scope = info.get("semantic_scope") or "none"
        trace.append({"tier": tier.get("name"), "schemas": list(tier["schemas"]), "row_status": ranked.get("row_status"),
                      "top": _path(top) if top else None, "meaning": top.get("meaning") if top else None,
                      "semantic_scope": scope})
        searched.append(tier.get("name"))
        tried.append((tier, records, info, ranked))
        if _direct_candidate(ranked):
            chosen = tried[-1]
            break
    if chosen is None:
        chosen = next((t for t in tried if (t[3].get("candidates") or [])), None)
    if chosen is not None:
        tier, records, info, ranked = chosen
        name = tier.get("name")
    elif tried:
        tier, records, info, ranked = tried[-1]
        merged = [c for t in tried for c in (t[3].get("rejected_candidates") or [])][:MAX_REJECTED]
        ranked = {**ranked, "rejected_candidates": merged}
        name = None
    else:
        records, info, name = [], {}, None
        ranked = {"candidates": [], "rejected_candidates": [], "row_status": "none_partial", "notes": []}
    scope = min((t["semantic_scope"] for t in trace), key=lambda s: SCOPE_ORDER.get(s, 0)) if trace else "none"
    return {"ranked": ranked, "records": records or [], "info": info, "tier": name, "tier_trace": trace,
            "semantic_scope": scope, "searched": searched}


def _suffix_window(name, naming):
    parts = str(name or "").split("_")
    rule = {**(naming or {}), "ratio_rule": "approved_bases_only", "ratio_bases": []}
    fact = parse_definition(parts[-1], "", rule)["facts"].get("window") if parts[-1] else None
    return fact.get("value") if fact else None


def operand_request(req, role, name, naming=None):
    """Oran satırının adı+açıklamasından bir bileşen talebi; katalog adı kullanılmaz."""
    rule = {**(naming or {}), "ratio_rule": "approved_bases_only", "ratio_bases": []}
    window = _suffix_window(name, naming)
    parts = str(name or "").split("_")
    base = "_".join(parts[:-1]) if window and len(parts) > 1 else str(name or "")
    text = INFLECTED_MEASURE.sub(r"\1i", normalize(req.get("description_for_parse", req.get("description", ""))))
    parsed = parse_definition(base, text, rule)
    conflicts = {i.split(":", 1)[1] for i in parsed["issues"] if i.startswith("conflict:")}
    facts = {dim: fact for dim, fact in parsed["facts"].items() if dim not in OPERAND_DROPPED and dim not in conflicts}
    if facts.get("concept", {}).get("value") == "ratio":
        facts.pop("concept")
    if facts.get("measure", {}).get("value") == "ratio":
        facts.pop("measure")
    if window:
        facts["window"] = {"value": window, "source": "naming_rule", "evidence": str(name)}
    issues = [i for i in parsed["issues"] if not i.startswith("conflict:") and i not in ("ambiguous_ratio_name", "unsupported_formula")]
    definition = {"facts": facts, "issues": issues, "formula": None}
    if parsed.get("residual"):
        definition["residual"] = parsed["residual"]
    return {**req, "name": name, "definition": definition, "role": role, "ratio_name": req.get("name")}


def _object_key(source):
    return (str(source.get("schema") or "").upper(), str(source.get("object") or "").upper())


def _endpoint(value, schema=None, obj=None):
    if isinstance(value, str) and "." in value:
        schema, obj = value.split(".", 1)
    elif isinstance(value, dict):
        schema, obj = value.get("schema", schema), value.get("object", obj)
    return (str(schema or "").upper(), str(obj or "").upper())


def _verified_link(left, right, records):
    """Yalnız katalog kaydındaki verified_relationships iki nesneyi açıkça bağlıyorsa True."""
    wanted = {left, right}
    for record in records:
        for rel in record.get("verified_relationships") or []:
            if not isinstance(rel, dict) or rel.get("verified") is False:
                continue
            start = _endpoint(rel.get("from"), rel.get("schema", record.get("schema")), rel.get("object", record.get("object")))
            target = rel.get("to")
            if target is None:
                target = {"schema": rel.get("to_schema", rel.get("ref_schema")), "object": rel.get("to_object", rel.get("ref_object"))}
            end = _endpoint(target)
            if all(end) and {start, end} == wanted:
                return True
    return False


def derive_ratio(req, search, naming):
    formula = (req.get("definition") or {}).get("formula")
    if not isinstance(formula, dict) or formula.get("op") != "divide":
        return None
    components, reasons, found, records = [], [], {}, []
    for role in ("numerator", "denominator"):
        name = formula.get(role)
        operand = operand_request(req, role, name, naming)
        facts = operand["definition"]["facts"]
        item = {"role": role, "name": name, "window": facts.get("window", {}).get("value"), "verdict": "belirsiz",
                "source": None, "tier": None, "reasons": []}
        if "measure" not in facts or "transaction" not in facts:
            item["reasons"].append("operand_facts_incomplete:" + role)
        else:
            outcome = search(operand)
            records.extend(outcome.get("records") or [])
            direct = _direct_candidate(outcome.get("ranked"))
            if direct:
                source = direct.get("source") or {}
                item.update(verdict="dogrudan", tier=outcome.get("tier"),
                            source={k: source.get(k) for k in ("schema", "object", "column")})
                found[role] = item["source"]
            elif (outcome.get("ranked") or {}).get("candidates"):
                item["reasons"].append("operand_weak:" + role)
            else:
                item.update(verdict="bulunamadi")
                item["reasons"].append("operand_not_found:" + role)
        reasons.extend(_reason(code, component=role, name=name) for code in item["reasons"])
        components.append(item)
    relationship = None
    if len(found) == 2:
        left, right = _object_key(found["numerator"]), _object_key(found["denominator"])
        if left == right:
            relationship = "same_object"
        elif _verified_link(left, right, records):
            relationship = "verified"
        else:
            relationship = "unverified"
            reasons.append(_reason("cross_object_relationship_unverified"))
    if relationship in ("same_object", "verified"):
        verdict = "turetilebilir"
        reasons = [_reason("derived_same_object" if relationship == "same_object" else "derived_verified_relationship")]
    else:
        verdict = "belirsiz"
    return {"formula": str(formula.get("numerator")) + " / " + str(formula.get("denominator")), "components": components,
            "relationship": relationship, "verdict": verdict, "verdict_reasons": reasons}


def _closest(ranked):
    out = []
    for candidate in ((ranked or {}).get("rejected_candidates") or [])[:MAX_CLOSEST]:
        detail = "; ".join(d.get("text", "") for d in candidate.get("differences") or [] if d.get("text"))
        out.append(_reason("closest_rejected", path=_path(candidate), detail=detail or candidate.get("rejected_reason") or "anlam farkı"))
    return out


def decide(req, outcome, *, unselected, schema_complete, catalog_complete, derivation=None):
    ranked = outcome.get("ranked") or {}
    direct = _direct_candidate(ranked)
    if direct:
        reasons = []
        for note in direct.get("reasons") or []:
            note = str(note)
            if note.startswith("unit_unverified"):
                reasons.append(_reason("unit_unverified"))
            elif note.startswith("entity_unverified"):
                value = note.rpartition(" ")[2]
                reasons.append(_reason("entity_unverified", detail=ENTITY_TEXT.get(value, value)))
            elif note.startswith("window_name_mismatch"):
                reasons.append(_reason("window_name_mismatch", detail=note.partition(":")[2].strip()))
        return {"verdict": "dogrudan", "verdict_reasons": reasons}
    if derivation:
        return {"verdict": derivation["verdict"], "verdict_reasons": list(derivation.get("verdict_reasons") or [])}
    candidates = ranked.get("candidates") or []
    if candidates:
        best, reasons = candidates[0], []
        for check in best.get("checks") or []:
            if check.get("status") == "conflict":
                reasons.append(_reason("conflict_dimension:" + check["dimension"], dimension=check["dimension"]))
            elif check.get("status") == "missing":
                reasons.append(_reason("unverified_dimension:" + check["dimension"], dimension=check["dimension"]))
        if best.get("meaning") == "weak":
            reasons.append(_reason("weak_evidence"))
        elif best.get("meaning") == "no_description":
            reasons.append(_reason("candidate_no_description"))
        if not reasons:
            reasons.append(_reason("candidate_unverified"))
        return {"verdict": "belirsiz", "verdict_reasons": reasons + _closest(ranked)}
    reasons = [_reason("tier_not_selected:" + str(s)) for s in unselected or []]
    searched = [s for item in outcome.get("tier_trace") or [] for s in item.get("schemas") or []]
    reasons += [_reason("catalog_incomplete:" + str(s)) for s in searched if not (schema_complete or {}).get(s, catalog_complete)]
    if outcome.get("semantic_scope") != "full":
        reasons.append(_reason("semantic_partial"))
    reasons += [_reason("definition_issue:" + str(i)) for i in (req.get("definition") or {}).get("issues") or []]
    return {"verdict": "karar_verilemedi" if reasons else "bulunamadi", "verdict_reasons": reasons + _closest(ranked)}


def resolve_row(req, tiers, run, *, unselected, schema_complete, catalog_complete, naming):
    outcome = tiered_search(req, tiers, run)
    trace = list(outcome["tier_trace"])
    derivation = None
    formula = (req.get("definition") or {}).get("formula")
    if not _direct_candidate(outcome["ranked"]) and isinstance(formula, dict) and formula.get("op") == "divide":
        def search(operand_req):
            found = tiered_search(operand_req, tiers, run)
            trace.extend(dict(item, role=operand_req.get("role")) for item in found["tier_trace"])
            return found
        derivation = derive_ratio(req, search, naming)
    decision = decide(req, outcome, unselected=unselected, schema_complete=schema_complete,
                      catalog_complete=catalog_complete, derivation=derivation)
    return {"outcome": outcome, "verdict": decision["verdict"], "verdict_reasons": decision["verdict_reasons"],
            "derivation": derivation, "tier_trace": trace}
