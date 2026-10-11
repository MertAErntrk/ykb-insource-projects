"""Conservative, evidence-aware semantics. No database or model execution here.

The parser recognizes a deliberately small Turkish/English vocabulary. An unknown
is not equality. Rich definitions may be supplied by verified metadata providers.
"""
from __future__ import annotations

import html
import re
import unicodedata
from collections import defaultdict


DIMENSIONS = (
    "entity", "grain", "concept", "transaction", "direction", "measure",
    "aggregation", "grouping", "window", "window_bounds", "reference_date",
    "person_key", "unit", "codes", "zero_policy", "null_policy", "numerator",
    "denominator", "business_scope",
)
WINDOWS = {"LM": "last_month", "LW": "last_week", "L2W": "last_2_weeks", "L3M": "last_3_months"}
TRUSTED_SOURCES = {"description", "verified_metadata", "naming_rule"}
IDENTIFIER = r"[^\W\d]\w*"
FORMULA = re.compile(rf"^\s*({IDENTIFIER})\s*/\s*({IDENTIFIER})\s*(?:[;.]|$)", re.UNICODE)


def normalize(text):
    """Normalize for matching only; original descriptions are never overwritten."""
    text = html.unescape(str(text or ""))
    text = re.sub(r"<[^>]*>", " ", text)
    text = text.translate(str.maketrans({"İ": "i", "I": "ı"})).lower()
    text = text.translate(str.maketrans("ıçğöşü", "icgosu"))
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def _key(value):
    if isinstance(value, str):
        return normalize(value)
    if isinstance(value, dict):
        return tuple(sorted((str(k), _key(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_key(v) for v in value)
    return value


def parse_definition(name, description, naming=None):
    facts, issues = {}, []
    definition = {"facts": facts, "issues": issues, "formula": None}
    original = str(description or "")
    clean = re.sub(r"(?i)<br\s*/?>|</p\s*>", "\n", html.unescape(original))
    clean = re.sub(r"<[^>]*>", " ", clean).strip()
    text = normalize(original)
    naming = naming or {}
    windows = {**WINDOWS, **naming.get("windows", {})}

    def put(dimension, value, evidence=None, source="description"):
        if dimension in facts and _key(facts[dimension]["value"]) != _key(value):
            issues.append("conflict:" + dimension)
            return
        facts[dimension] = {"value": value, "source": source, "evidence": evidence or original}

    # Suffixes provide only user-confirmed window semantics, not measures or keys.
    suffixes = str(name or "").split("_")
    name_window = windows.get(suffixes[-1].upper()) if suffixes else None
    has_two_windows = len(suffixes) > 2 and suffixes[-2].upper() in windows and name_window
    formula_match = FORMULA.match(clean)
    if formula_match:
        numerator, denominator = formula_match.groups()
        # A suffix may contain only policy statements, never another expression.
        remainder = clean[formula_match.end():].strip()
        if re.search(r"[/+*()]", remainder):
            issues.append("unsupported_formula")
        else:
            definition["formula"] = {"op": "divide", "numerator": numerator, "denominator": denominator, "zero": None, "null": None}
            for dim, val in (("numerator", numerator), ("denominator", denominator), ("concept", "ratio"), ("measure", "ratio")):
                put(dim, val)
    elif "/" in text or clean.startswith("="):
        issues.append("unsupported_formula")

    # Only the exact user-confirmed example (or explicitly configured bases) may
    # infer a ratio from a name. An arbitrary two-window suffix is ambiguous.
    ratio_bases = naming.get("ratio_bases", ["Giden_max_havale_adeti"])
    approved_base = "_".join(suffixes[:-2])
    if has_two_windows and not definition["formula"]:
        if _key(approved_base) in {_key(x) for x in ratio_bases} and not text:
            numerator = "_".join(suffixes[:-1])
            denominator = approved_base + "_" + suffixes[-1]
            definition["formula"] = {"op": "divide", "numerator": numerator, "denominator": denominator, "zero": None, "null": None}
            for dim, val in (("numerator", numerator), ("denominator", denominator)):
                put(dim, val, str(name), "naming_rule")
            # Ratio concept follows the explicitly confirmed expression.
            put("concept", "ratio", str(name), "naming_rule")
            put("measure", "ratio", str(name), "naming_rule")
        else:
            issues.append("ambiguous_ratio_name")
    if definition["formula"]:
        formula = definition["formula"]
        if has_two_windows:
            expected_num = "_".join(suffixes[:-1])
            expected_den = "_".join(suffixes[:-2] + [suffixes[-1]])
            if _key(formula["numerator"]) != _key(expected_num) or _key(formula["denominator"]) != _key(expected_den):
                issues.append("conflict:ratio_direction")
        zero = re.search(r"payda\s*(?:sifirsa|0\s*ise|sifir\s*ise)\s*[:=]?\s*(-?\d+(?:\.\d+)?)", text)
        if zero:
            val = float(zero.group(1))
            val = int(val) if val.is_integer() else val
            formula["zero"] = val
            put("zero_policy", val)
        null = re.search(r"(?:payda\s+)?null\s*(?:ise|sa)\s*[:=]?\s*(null|-?\d+(?:\.\d+)?)", text)
        if null:
            formula["null"] = "propagate" if null.group(1) == "null" else null.group(1)
            put("null_policy", formula["null"])
        if formula_match:
            trailing = normalize(clean[formula_match.end():])
            trailing = re.sub(r"payda\s*(?:sifirsa|0\s*ise|sifir\s*ise)\s*[:=]?\s*-?\d+(?:\.\d+)?", "", trailing)
            trailing = re.sub(r"(?:payda\s+)?null\s*(?:ise|sa)\s*[:=]?\s*(?:null|-?\d+(?:\.\d+)?)", "", trailing)
            if trailing.strip(" .;,\n\t"):
                issues.append("unparsed_formula_qualifier")
        return definition

    # Negation and multiple competing operations require review, not a guess.
    if re.search(r"\b(?:degil|haric|disinda)\b", text):
        issues.append("unsupported_scope_negation")
    if re.search(r"\bmusteri(?:nin|ye|ler|si)?\b", text):
        put("entity", "customer")
    if "musteri bazinda" in text or "her musteri icin" in text:
        put("grain", "customer")
    if "hesap bazinda" in text:
        put("grain", "account")
    if "basvuru bazinda" in text or "her basvuru icin" in text:
        put("grain", "application")
        put("entity", "application")
    for word, val in (("havale", "havale"), ("eft", "eft")):
        if re.search(r"\b" + word + r"\b", text):
            put("transaction", val)
            put("concept", "transfer")
    incoming, outgoing = "gelen" in text, "giden" in text
    if incoming or outgoing:
        put("direction", "both" if incoming and outgoing else "incoming" if incoming else "outgoing")
    if re.search(r"(?:farkli|tekil|benzersiz)\s+kisi", text):
        put("measure", "distinct_people")
        put("aggregation", "count_distinct")
    elif re.search(r"(?:farkli|tekil|benzersiz)\s+hesap", text):
        put("measure", "distinct_accounts")
        put("aggregation", "count_distinct")
    elif re.search(r"\bislem\s+(?:sayisi|adedi)|\badet(?:i)?\b", text):
        put("measure", "transaction_count")
        put("aggregation", "count")
    if "tutar" in text:
        put("measure", "amount")
    operations = [val for pat, val in ((r"\b(?:max|maksimum|en yuksek)\b", "max"), (r"\b(?:toplam|sum)\b", "sum"), (r"\b(?:ortalama|average|avg)\b", "mean")) if re.search(pat, text)]
    if len(operations) > 1:
        issues.append("ambiguous_aggregation_order")
    elif operations:
        # count followed by max/sum is genuinely multi-stage; grouping stays unknown.
        inner = facts.get("aggregation", {}).get("value")
        facts.pop("aggregation", None)
        put("aggregation", [inner, operations[0]] if inner else operations[0])
    description_windows = []
    for pattern, value in ((r"son (?:3|uc) ay", "last_3_months"), (r"son (?:2|iki) hafta", "last_2_weeks"), (r"son (?:bir )?ay(?:da|in|lik)?\b", "last_month"), (r"son (?:bir )?hafta(?:da|nin|lik)?\b", "last_week")):
        if re.search(pattern, text):
            description_windows.append(value)
    for window in description_windows:
        put("window", window)
    if name_window and not has_two_windows:
        if "window" in facts:
            if _key(facts["window"]["value"]) != _key(name_window):
                issues.append("conflict:window")
        else:
            put("window", name_window, str(name), "naming_rule")

    if "faiz" in text:
        if "sabit" in text or "degisken" in text or "faiz turu" in text:
            put("concept", "interest_type")
            put("measure", "category")
        if "faiz orani" in text:
            put("concept", "interest_rate")
            put("measure", "rate")
    if "gelir" in text:
        put("concept", "income")
        put("measure", "amount")
    if "limit" in text:
        put("concept", "credit_limit")
        put("measure", "amount")
    if "vade" in text:
        put("concept", "maturity")
        put("measure", "duration")
    for phrase, scope in (("beyan edilen", "declared"), ("dogrulanmis", "verified"), ("basvurulan", "requested"), ("onaylanan", "approved")):
        if phrase in text:
            put("business_scope", scope)
    code_text = "\n".join(normalize(line) for line in clean.splitlines())
    code_matches = re.findall(r"(?:^|[,;.\n])\s*(\d+)\s+([^,;\n.]+)", code_text)
    if len(code_matches) >= 2:
        put("codes", {key: label.strip() for key, label in code_matches})
        put("measure", "category")
    if re.search(r"\b(?:tl|try)\b", text):
        put("unit", "TRY")
    elif "%" in text or "yuzde" in text:
        put("unit", "percent")
    # Stated income periodicity changes its unit; never equate monthly and
    # annual amounts. No currency is guessed when none is supplied.
    if facts.get("concept", {}).get("value") == "income":
        periods = [v for p, v in ((r"\baylik\b", "month"), (r"\byillik\b", "year")) if re.search(p, text)]
        if len(periods) > 1:
            issues.append("ambiguous_income_period")
        elif periods and "unit" in facts:
            facts["unit"]["value"] += "/" + periods[0]

    # A recognized noun does not license dropping the rest of the business
    # definition. Consume only supported semantic phrases and harmless grammar;
    # any remaining words force review. Structured verified definitions remain
    # the extension point for richer language, not an expanding stop-word list.
    residual = code_text
    if len(code_matches) >= 2:
        residual = re.sub(r"(?:^|[,;.\n])\s*\d+\s+[^,;\n.]+", " ", residual)
    covered = [
        r"basvuru bazinda|her basvuru icin|musteri bazinda|her musteri icin|hesap bazinda",
        r"son (?:3|uc) ay(?:da)?|son (?:2|iki) hafta(?:da)?|son (?:bir )?ay(?:da|in|lik)?|son (?:bir )?hafta(?:da|nin|lik)?",
        r"(?:farkli|tekil|benzersiz)\s+(?:kisi|hesap)",
        r"islem (?:sayisi|adedi)|adet(?:i)?",
        r"faiz turu|faiz orani|sabit faiz|degisken faiz",
        r"beyan edilen|dogrulanmis|basvurulan|onaylanan",
        r"toplam|sum|max|maksimum|en yuksek|ortalama|average|avg",
        r"musterinin|musteri|basvuruda|havale|eft|gelen|giden|tutar(?:i)?|gelir|limit|vade|try|tl|yuzde",
        r"kac|ile|iliskisine|girdigi|sayisi|yaptigi|ve|burada|isteniyor",
    ]
    if facts.get("concept", {}).get("value") == "income" and "unit" in facts:
        covered.append(r"aylik|yillik")
    for pattern in covered:
        residual = re.sub(r"\b(?:" + pattern + r")\b", " ", residual)
    if re.search(r"[\w]", residual):
        issues.append("unparsed_description_qualifier")
    return definition


def _definition(item, catalog=False):
    definition = item.get("definition")
    if isinstance(definition, dict) and isinstance(definition.get("facts"), dict):
        return definition
    return parse_definition(item.get("column" if catalog else "name", ""), item.get("column_comment" if catalog else "description", ""))


def _trusted(fact, dim):
    if not isinstance(fact, dict) or fact.get("source") not in TRUSTED_SOURCES or not isinstance(fact.get("evidence"), str) or not fact["evidence"].strip():
        return False
    value = fact.get("value")
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str) and (not value.strip() or normalize(value) in {"unknown", "unspecified", "bilinmiyor", "belirsiz", "?"}):
        return False
    if isinstance(value, (dict, list, tuple)) and not value:
        return False
    scalar_dimensions = {"entity", "grain", "concept", "transaction", "direction", "measure", "window", "reference_date", "person_key", "unit", "numerator", "denominator"}
    if dim in scalar_dimensions and not isinstance(value, str):
        return False
    return fact["source"] != "naming_rule" or dim in {"window", "numerator", "denominator", "concept", "measure"}


def _critical(left, right):
    facts = {**right, **left}
    dims = {"entity", "grain", "concept", "measure"}
    dims.update(dim for dim in DIMENSIONS if dim in facts)
    if "transaction" in facts:
        dims.update({"transaction", "direction", "aggregation", "business_scope", "null_policy"})
    if "window" in facts:
        dims.update({"window", "window_bounds", "reference_date"})
    measures = [x.get("measure", {}).get("value") for x in (left, right)]
    if "distinct_people" in measures:
        dims.add("person_key")
    if any(v in ("amount", "rate", "duration") for v in measures):
        dims.add("unit")
    if "category" in measures:
        dims.add("codes")
    if any(x.get("concept", {}).get("value") in ("income", "credit_limit") for x in (left, right)):
        dims.add("business_scope")
    aggregations = [str(x.get("aggregation", {}).get("value", "")) for x in (left, right)]
    if any("max" in a or "mean" in a or a.startswith("[") for a in aggregations):
        dims.add("grouping")
    if "numerator" in facts or "denominator" in facts:
        dims.update({"numerator", "denominator", "zero_policy", "null_policy", "reference_date"})
    return [d for d in DIMENSIONS if d in dims]


def evaluate_candidate(request, record):
    wanted, actual = _definition(request), _definition(record, True)
    left, right = wanted["facts"], actual["facts"]
    checks, reasons = [], []
    for dimension in _critical(left, right):
        a, b = left.get(dimension, {}), right.get(dimension, {})
        if not _trusted(a, dimension) or not _trusted(b, dimension):
            status = "missing"
        else:
            status = "compatible" if _key(a["value"]) == _key(b["value"]) else "conflict"
        checks.append({"dimension": dimension, "status": status, "requested": a.get("value"), "actual": b.get("value"), "evidence": {"request": a.get("evidence", ""), "candidate": b.get("evidence", ""), "request_source": a.get("source"), "candidate_source": b.get("source")}})
        if status != "compatible":
            reasons.append(status + ":" + dimension)
    for origin, definition in (("request", wanted), ("candidate", actual)):
        reasons.extend(origin + ":" + str(issue) for issue in definition.get("issues", []))
    conflicting = any(c["status"] == "conflict" for c in checks) or any("conflict:" in reason for reason in reasons)
    status = "conflict" if conflicting else "insufficient" if reasons else "supported"
    return {"catalog_id": record.get("catalog_id", ""), "path": ".".join(str(record.get(k) or "?") for k in ("source_database", "environment", "container", "schema", "object", "column")), "score": record.get("_score", 0.0), "retrieval": record.get("_retrieval", []), "checks": checks, "status": status, "reasons": reasons}


def build_dependencies(requests):
    """Resolve across the entire workbook. Duplicate names never resolve by order."""
    nodes, edges, issues, by_name, by_id = [], [], {}, defaultdict(list), {}
    for request in requests:
        row_id = request["row_id"]
        if row_id in by_id:
            raise ValueError("duplicate row_id")
        node = {"row_id": row_id, "name": request.get("name", ""), "definition": _definition(request)}
        nodes.append(node)
        by_id[row_id] = node
        by_name[_key(node["name"])].append(row_id)
        issues[row_id] = []
    for ids in by_name.values():
        if len(ids) > 1:
            for row_id in ids:
                issues[row_id].append("duplicate_name")
    for node in nodes:
        row_id, definition = node["row_id"], node["definition"]
        formula = definition.get("formula")
        if not formula:
            continue
        if formula.get("op") != "divide":
            issues[row_id].append("unsupported_formula")
            continue
        for role in ("numerator", "denominator"):
            name = formula.get(role, "")
            matches = by_name.get(_key(name), [])
            if not matches:
                issues[row_id].append("missing_definition:" + str(name))
            elif len(matches) > 1:
                issues[row_id].append("ambiguous_reference:" + str(name))
            else:
                edges.append({"source": row_id, "target": matches[0], "role": role})
    adjacency = defaultdict(list)
    for edge in edges:
        adjacency[edge["source"]].append(edge["target"])
    # Iterative Kosaraju SCC detection handles all cycle members and avoids
    # recursion limits. Back-edge marking alone can miss members of a large SCC.
    state, finish = {}, []
    for start in by_id:
        if state.get(start):
            continue
        stack = [(start, iter(adjacency[start]))]
        state[start] = 1
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                state[node] = 2
                stack.pop()
                finish.append(node)
            elif not state.get(child):
                state[child] = 1
                stack.append((child, iter(adjacency[child])))
    reverse, visited = defaultdict(list), set()
    for source, children in adjacency.items():
        for target in children:
            reverse[target].append(source)
    for start in reversed(finish):
        if start in visited:
            continue
        component, pending = [], [start]
        visited.add(start)
        while pending:
            node = pending.pop()
            component.append(node)
            for parent in reverse[node]:
                if parent not in visited:
                    visited.add(parent)
                    pending.append(parent)
        if len(component) > 1 or start in adjacency[start]:
            for node in component:
                issues[node].append("cycle")
    return {"nodes": nodes, "edges": edges, "issues": issues}


def assess_production(request, results_by_id, dependency_graph, records):
    """Assess a proposal, never generate SQL or compute customer data.

    A candidate requires ready operands, compatible verified keys and scope, and
    fully specified ratio policies. Different operand names alone prove nothing.
    """
    row_id = request["row_id"]
    result = results_by_id.get(row_id, {})
    if result.get("semantic_status") == "supported":
        return {"status": "not_evaluated", "reasons": ["ready_feature_supported"], "dependencies": []}
    definition = _definition(request)
    formula = definition.get("formula")
    dependencies = [e for e in dependency_graph["edges"] if e["source"] == row_id]
    reasons = list(dependency_graph["issues"].get(row_id, [])) + list(definition.get("issues", []))
    if not formula:
        # A movement source is an explicitly tagged source, not a lexical hit.
        requested_transaction = definition["facts"].get("transaction", {})
        source_ids = []
        for record in records:
            facts = _definition(record, True)["facts"]
            grain, transaction = facts.get("grain", {}), facts.get("transaction", {})
            if _trusted(grain, "grain") and grain["value"] == "transaction" and _trusted(transaction, "transaction") and _trusted(requested_transaction, "transaction") and _key(transaction["value"]) == _key(requested_transaction["value"]):
                source_ids.append(record.get("catalog_id"))
        return {"status": "source_only" if source_ids else "insufficient", "reasons": reasons + (["movement_sources_only; aggregation_not_verified"] if source_ids else ["no_verified_derivation"]), "dependencies": source_ids}
    facts = definition["facts"]
    for dimension in ("entity", "grain", "reference_date", "zero_policy", "null_policy", "numerator", "denominator"):
        if not _trusted(facts.get(dimension), dimension):
            reasons.append("missing:" + dimension)
    if formula.get("op") != "divide":
        reasons.append("unsupported_formula")
    if {e["role"] for e in dependencies} != {"numerator", "denominator"}:
        reasons.append("unresolved_operands")
    by_record = {r["catalog_id"]: r for r in records}
    selected = []
    for edge in dependencies:
        dep_id = edge["target"]
        if dependency_graph["issues"].get(dep_id):
            reasons.append("operand_graph_issue:" + dep_id)
        dep_result = results_by_id.get(dep_id, {})
        candidates = [c for c in dep_result.get("candidates", []) if c.get("status") == "supported"]
        if dep_result.get("semantic_status") != "supported" or not candidates:
            reasons.append("operand_not_ready:" + dep_id)
            continue
        record = by_record.get(candidates[0].get("catalog_id"))
        if not record:
            reasons.append("operand_catalog_missing:" + dep_id)
            continue
        selected.append(record)
        actual = _definition(record, True)["facts"]
        for dimension in ("entity", "grain", "reference_date", "business_scope"):
            value = actual.get(dimension)
            if not _trusted(value, dimension):
                reasons.append("operand_missing:" + dep_id + ":" + dimension)
            if dimension in facts and _trusted(facts[dimension], dimension) and _trusted(value, dimension) and _key(value["value"]) != _key(facts[dimension]["value"]):
                reasons.append("operand_conflict:" + dimension)
        if not record.get("key_columns"):
            reasons.append("operand_keys_unknown:" + dep_id)
    if len(selected) == 2:
        a, b = selected
        # Same object and verified unique key is a conservative sufficient join
        # condition. Cross-object joins need a future lineage/relationship plugin.
        for dimension in ("entity", "grain", "reference_date", "business_scope", "measure", "transaction", "direction", "person_key", "unit"):
            x, y = _definition(a, True)["facts"].get(dimension), _definition(b, True)["facts"].get(dimension)
            if x or y:
                if not _trusted(x, dimension) or not _trusted(y, dimension):
                    reasons.append("operands_missing:" + dimension)
                elif _key(x["value"]) != _key(y["value"]):
                    reasons.append("operands_conflict:" + dimension)
        location = ("source_database", "environment", "container", "schema", "object")
        if any(a.get(k) != b.get(k) or a.get(k) is None for k in location):
            reasons.append("cross_object_relationship_unverified")
        if _key(a.get("key_columns")) != _key(b.get("key_columns")):
            reasons.append("operand_keys_conflict")
    return {"status": "insufficient" if reasons else "candidate", "reasons": sorted(set(reasons)) or ["verified_operands; expert_review_required"], "dependencies": dependencies}
