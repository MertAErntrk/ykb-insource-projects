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
    "denominator", "business_scope", "data_type", "qualifier",
)
# Not facts parsed from text: data_type comes from the request/catalog type
# columns, qualifier compares unparsed residual words.
DERIVED_DIMENSIONS = {"data_type", "qualifier"}
# Bir oranın iki bileşeni aynı işlemi sayar; açıklamadaki bu olgular oranın da olgusudur.
RATIO_SHARED_DIMENSIONS = ("entity", "transaction", "direction", "aggregation")
# Farklı adlı bileşenlerde dönem tek başına yetmez; neyin sayıldığını açıklama doğrulamalı.
RATIO_SUBJECT_DIMENSIONS = ("transaction", "concept")
WINDOWS = {"LM": "last_month", "LW": "last_week", "L2W": "last_2_weeks", "L3M": "last_3_months"}
TRUSTED_SOURCES = {"description", "verified_metadata", "naming_rule"}
IDENTIFIER = r"[^\W\d]\w*"
FORMULA = re.compile(rf"^\s*({IDENTIFIER})\s*/\s*({IDENTIFIER})\s*(?:[;.]|$)", re.UNICODE)
GRAINS = (("kredi karti", "credit_card"), ("kart", "card"), ("kredi", "loan"), ("islem", "transaction"), ("hesap", "account"), ("musteri", "customer"), ("basvuru", "application"))
WINDOW_UNIT_NAMES = {"gun": "day", "hafta": "week", "ay": "month"}
WINDOW_UNIT_TEXT = {"day": "gün", "week": "hafta", "month": "ay"}
DURATION_UNITS = {"gun": "day", "hafta": "week", "ay": "month", "yil": "year", "saat": "hour", "dakika": "minute"}
TYPE_CLASSES = (
    ("date", {"tarih", "date", "timestamp", "datetime", "zaman"}),
    ("number", {"sayi", "sayisal", "number", "numeric", "int", "integer", "float", "decimal", "double", "real", "bigint", "smallint", "tutar", "oran", "adet", "binary"}),
    ("text", {"metin", "text", "varchar2", "varchar", "char", "clob", "nvarchar2", "nchar", "nclob", "string", "karakter", "alfanumerik"}),
)
_LEXICON = None


def _lexicon():
    global _LEXICON
    if _LEXICON is None:
        from . import lexicon
        _LEXICON = lexicon.load()
    return _LEXICON


def _window_value(number, unit):
    """(N, birim) -> canonical window. A year is 12 months and a multiple of 7
    days is weeks; weeks and months are never converted into each other."""
    if unit == "ytd":
        return "ytd"
    if unit == "yil":
        number, unit = number * 12, "ay"
    if unit == "gun" and number % 7 == 0:
        number, unit = number // 7, "hafta"
    # "2025 yılı" is a date, not a window.
    if unit not in WINDOW_UNIT_NAMES or not 0 < number <= 366:
        return None
    name = WINDOW_UNIT_NAMES[unit]
    return "last_" + name if number == 1 else "last_%d_%ss" % (number, name)


def _lexicon_windows(text):
    values = []
    for number, unit in _lexicon().window(text):
        value = _window_value(number, unit)
        if value and value not in values:
            values.append(value)
    return values


def _suffix_window(token, windows):
    """Name suffix -> canonical window: confirmed suffixes first, then the
    lexicon (L6M, W1M, LM), so names and descriptions share one canon."""
    token = str(token or "")
    if token.upper() in windows:
        return windows[token.upper()]
    found = _lexicon().tokens(token) if token else []
    if len(found) == 1 and found[0].startswith("win:"):
        _, number, unit = found[0].split(":")
        return _window_value(int(number), unit)
    return None


def _description_windows(clean, text):
    values = _lexicon_windows(clean)
    for pattern, value in ((r"son (?:3|uc) ay", "last_3_months"), (r"son (?:2|iki) hafta", "last_2_weeks"), (r"son (?:bir )?ay(?:da|in|lik)?\b", "last_month"), (r"son (?:bir )?hafta(?:da|nin|lik)?\b", "last_week")):
        if re.search(pattern, text) and value not in values:
            values.append(value)
    return values


def _window_text(value):
    match = re.match(r"^last_(?:(\d+)_)?(day|week|month)s?$", str(value))
    if value == "ytd":
        return "yılbaşından beri"
    if not match:
        return str(value)
    return "son " + (match.group(1) or "1") + " " + WINDOW_UNIT_TEXT[match.group(2)]


def type_class(value):
    """Free-form request type or catalog data_type -> date|number|text|unknown.
    Flag/boolean is unknown: it may be NUMBER(1) or VARCHAR2(1)."""
    text = re.sub(r"\(.*", "", normalize(value))
    if not text or re.search(r"flag|bool|evet|hayir", text):
        return "unknown"
    words = [w for w in re.split(r"[^a-z0-9]+", text) if w]
    for name, words_of_class in TYPE_CLASSES:
        if words and words[0] in words_of_class:
            return name
    return "unknown"


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

    # Suffixes provide only window semantics (and an explicit TL unit), not measures or keys.
    suffixes = str(name or "").split("_")
    ratio_rule = naming.get("ratio_rule", "suffix_pair")
    name_window = _suffix_window(suffixes[-1], windows) if suffixes else None
    if ratio_rule == "approved_bases_only":
        has_two_windows = len(suffixes) > 2 and suffixes[-2].upper() in windows and suffixes[-1].upper() in windows
        pair_window = None
    else:
        # <base>_<P1>_<P2>: two window suffixes name the ratio base_P1 / base_P2.
        pair_window = _suffix_window(suffixes[-2], windows) if len(suffixes) > 2 else None
        has_two_windows = bool(pair_window and name_window)
    suffix_pair = has_two_windows and ratio_rule == "suffix_pair"
    if not name_window and not has_two_windows:
        # Explicit numeric windows in a name (3M, 12AY, SON_6_AY); several are ambiguous.
        found = _lexicon_windows(name)
        name_window = found[0] if len(found) == 1 else None
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
    elif clean.startswith("=") or ("/" in text and not suffix_pair):
        # Under suffix_pair the name states the ratio; "A'nın B'ye oranı" or "/" prose is not a formula.
        issues.append("unsupported_formula")

    # suffix_pair: any <base>_<P1>_<P2> name is base_P1 / base_P2.
    # approved_bases_only: only the exact user-confirmed example (or explicitly
    # configured bases) may infer a ratio; an arbitrary two-window suffix is ambiguous.
    ratio_bases = naming.get("ratio_bases", ["Giden_max_havale_adeti"])
    approved_base = "_".join(suffixes[:-2])
    if has_two_windows and not definition["formula"]:
        if suffix_pair or (_key(approved_base) in {_key(x) for x in ratio_bases} and not text):
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
        if suffix_pair and any(w not in (pair_window, name_window) for w in _description_windows(clean, text)):
            issues.append("conflict:ratio_window")
        zero =re.search(r"payda\s*(?:sifirsa|0\s*ise|sifir\s*ise)\s*[:=]?\s*(-?\d+(?:\.\d+)?)", text)
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
        if suffix_pair and text:
            base = parse_definition(approved_base, original, {**naming, "ratio_rule": "approved_bases_only", "ratio_bases": []})
            clashing = {i.split(":", 1)[1] for i in base["issues"] if i.startswith("conflict:")}
            for dim in RATIO_SHARED_DIMENSIONS:
                if dim in base["facts"] and dim not in clashing and dim not in facts:
                    facts[dim] = base["facts"][dim]
        return definition

    # Negation and multiple competing operations require review, not a guess.
    if re.search(r"\b(?:degil|haric|disinda)\b", text):
        issues.append("unsupported_scope_negation")
    if re.search(r"\bmusteri(?:nin|ye|ler|si)?\b", text):
        put("entity", "customer")
    # Grain only from explicit "X bazında/bazlı/başına" or "her X için"; name
    # prefixes (MUS_, KRD_) are not reliable evidence of grain.
    grains = []
    for word, val in GRAINS:
        if re.search(r"\b" + word + r"\s+(?:bazinda|bazli|basina)\b|\bher\s+" + word + r"\s+icin\b", text) and val not in grains:
            grains.append(val)
    if len(grains) > 1:
        issues.append("ambiguous_grain")
    elif grains:
        put("grain", grains[0])
        if grains[0] == "application":
            put("entity", "application")
    # Channels are distinct; "transfer" alone is an umbrella, not a channel.
    for word, val in (("havale", "havale"), ("eft", "eft"), ("swift", "swift"), ("wu", "western_union"), ("western union", "western_union")):
        if re.search(r"\b" + word + r"\b", text):
            put("transaction", val)
            put("concept", "transfer")
    incoming, outgoing = "gelen" in text, "giden" in text
    if incoming or outgoing:
        put("direction", "both" if incoming and outgoing else "incoming" if incoming else "outgoing")
    # "gün sayısı" / "günü sayısı"; "son 3 ay adet ..." is a window followed by a count, not a duration.
    duration = next((m for m in re.finditer(r"\b(gun|hafta|ay|yil|saat|dakika)(?:u|i|si)?\s+(?:adet(?:i|leri)?|adedi|sayisi|sayi)\b", text)
                     if not re.search(r"(?:\d|son|bir|iki|uc)\s*$", text[:m.start()])), None)
    if re.search(r"(?:farkli|tekil|benzersiz)\s+kisi", text):
        put("measure", "distinct_people")
        put("aggregation", "count_distinct")
    elif re.search(r"(?:farkli|tekil|benzersiz)\s+hesap", text):
        put("measure", "distinct_accounts")
        put("aggregation", "count_distinct")
    elif duration:
        # A duration, not a count of events; the period word is its unit.
        put("measure", "duration")
        put("unit", DURATION_UNITS[duration.group(1)])
    elif re.search(r"\b(?:adet(?:i|leri)?|aded\w*|sayisi|sayi|count)(?:dir)?\b", text):
        put("measure", "count")
        put("aggregation", "count")
    if re.search(r"\b(?:tutar\w*|amount|bakiye\w*)\b", text):
        put("measure", "amount")
    operations = [val for pat, val in ((r"\b(?:max|maksimum|en yuksek)\b", "max"), (r"\b(?:toplam|sum)\b", "sum"), (r"\b(?:ortalama|average|avg)\b", "mean")) if re.search(pat, text)]
    if len(operations) > 1:
        issues.append("ambiguous_aggregation_order")
    elif operations:
        # count followed by max/sum is genuinely multi-stage; grouping stays unknown.
        inner = facts.get("aggregation", {}).get("value")
        facts.pop("aggregation", None)
        # "toplam adet" is still a count, not a sum of counts.
        if inner == "count" and operations[0] == "sum":
            put("aggregation", "count")
        else:
            put("aggregation", [inner, operations[0]] if inner else operations[0])
    for window in _description_windows(clean, text):
        put("window", window)
    if name_window and not has_two_windows:
        if "window" in facts:
            if _key(facts["window"]["value"]) != _key(name_window):
                # A request conflict; for a catalog column the comment wins (see evaluate_candidate).
                issues.append("conflict:window")
                definition["window_name_mismatch"] = {"name": name_window, "description": facts["window"]["value"]}
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
    elif any(s.upper() in ("TL", "TRY") for s in suffixes):
        # An explicit currency suffix (AMT_TL) is a unit, never a measure.
        put("unit", "TRY", str(name), "naming_rule")
    # Currency or percent implies a measure only when nothing more specific did.
    if "measure" not in facts:
        if re.search(r"\b(?:tl|try)\b", text):
            put("measure", "amount")
        elif "%" in text or re.search(r"\boran\w*", text):
            put("measure", "ratio")
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
    # remaining words are kept as definition["residual"]: a note for review,
    # compared across sides in evaluate_candidate, never a parse issue.
    residual = code_text
    if len(code_matches) >= 2:
        residual = re.sub(r"(?:^|[,;.\n])\s*\d+\s+[^,;\n.]+", " ", residual)
    covered = [
        r"(?:kredi karti|kart|kredi|islem|hesap|musteri|basvuru) (?:bazinda|bazli|basina)|her (?:kredi karti|kart|kredi|islem|hesap|musteri|basvuru) icin",
        r"(?:(?:ilgili )?(?:yil )?aydan )?onceki \d+ ?(?:gun|hafta|ay|yil)\w*(?: (?:icerisinde|icinde))?",
        r"son (?:\d+|bir|iki|uc|dort|bes|alti|yedi|sekiz|dokuz|on|oniki)? ?(?:gun|hafta|ay|yil)\w*|\d+ ?(?:gun|hafta|ay|yil)\w*",
        r"(?:farkli|tekil|benzersiz)\s+(?:kisi|hesap)",
        r"islem (?:sayisi|adedi)|adet\w*|adedi\w*|sayisi|sayi|count|tutar\w*|amount|bakiye\w*|oran\w*",
        r"faiz turu|faiz orani|sabit faiz|degisken faiz",
        r"beyan edilen|dogrulanmis|basvurulan|onaylanan",
        r"toplam|sum|max|maksimum|en yuksek|ortalama|average|avg",
        r"musterinin|musteri|basvuruda|havale|eft|swift|wu|western union|gelen|giden|tutar(?:i)?|gelir|limit|vade|try|tl|yuzde",
        r"kac|ile|iliskisine|girdigi|sayisi|yaptigi|ve|burada|isteniyor",
    ]
    if facts.get("concept", {}).get("value") == "income" and "unit" in facts:
        covered.append(r"aylik|yillik")
    for pattern in covered:
        residual = re.sub(r"\b(?:" + pattern + r")\b", " ", residual)
    residual = " ".join(re.findall(r"\w+", residual))
    if residual:
        definition["residual"] = residual
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
    return fact["source"] != "naming_rule" or dim in {"window", "numerator", "denominator", "concept", "measure", "unit"}


def _critical(left, right):
    """A dimension is checked only when at least one side states a trusted fact.
    Unknown on both sides is outside the check, never equality evidence."""
    dims = {d for d in DIMENSIONS if d not in DERIVED_DIMENSIONS and (_trusted(left.get(d), d) or _trusted(right.get(d), d))}
    # An amount, rate or duration is meaningless without its unit (currency, period).
    if any(_trusted(x.get("measure"), "measure") and x["measure"]["value"] in ("amount", "rate", "duration") for x in (left, right)):
        dims.add("unit")
    return [d for d in DIMENSIONS if d in dims]


def evaluate_candidate(request, record):
    wanted, actual = _definition(request), _definition(record, True)
    left, right = wanted["facts"], actual["facts"]
    checks, reasons, notes = [], [], []
    for dimension in _critical(left, right):
        a, b = left.get(dimension, {}), right.get(dimension, {})
        if dimension == "unit" and not _trusted(a, dimension) and _trusted(b, dimension) and b["value"] == "TRY":
            # A request silent on currency does not block a TL column; noted for review.
            notes.append("unit_unverified: talep birim belirtmiyor, kolon TRY")
            continue
        if dimension == "entity" and not _trusted(a, dimension) and _trusted(b, dimension):
            # Talep varlık söylemiyorsa kolonun varlığı engel değil; incelemede not olarak görünür.
            notes.append("entity_unverified: talep varlık belirtmiyor, kolon " + str(b["value"]))
            continue
        if not _trusted(a, dimension) or not _trusted(b, dimension):
            status = "missing"
        elif dimension in ("numerator", "denominator") and _key(a["value"]) != _key(b["value"]):
            # Adlandırma kurumdan kuruma değişir: bileşen son ekindeki dönemle karşılaştırılır, okunamazsa doğrulanamaz.
            wanted_window, actual_window = (_suffix_window(str(v).split("_")[-1], WINDOWS) for v in (a["value"], b["value"]))
            if wanted_window and actual_window:
                status = "compatible" if wanted_window == actual_window else "conflict"
            else:
                # Dönem okunamıyorsa yalnız aynı adın ters rolde geçmesi (B / A) çelişkidir.
                swapped = right.get("denominator" if dimension == "numerator" else "numerator", {})
                status = "conflict" if _key(a["value"]) == _key(swapped.get("value")) else "missing"
        else:
            status = "compatible" if _key(a["value"]) == _key(b["value"]) else "conflict"
        checks.append({"dimension": dimension, "status": status, "requested": a.get("value"), "actual": b.get("value"), "evidence": {"request": a.get("evidence", ""), "candidate": b.get("evidence", ""), "request_source": a.get("source"), "candidate_source": b.get("source")}})
    renamed = [c for c in checks if c["dimension"] in ("numerator", "denominator") and c["status"] == "compatible"
               and _key(c["requested"]) != _key(c["actual"])]
    if renamed and not any(c["dimension"] in RATIO_SUBJECT_DIMENSIONS and c["status"] == "compatible"
                           and "naming_rule" not in (c["evidence"]["request_source"], c["evidence"]["candidate_source"])
                           for c in checks):
        for check in renamed:
            check["status"] = "missing"
    # Physical type: only two known, different classes are a conflict.
    wanted_type, actual_type = request.get("data_type") or "", record.get("data_type") or ""
    wanted_class, actual_class = type_class(wanted_type), type_class(actual_type)
    if "unknown" not in (wanted_class, actual_class):
        checks.append({"dimension": "data_type", "status": "compatible" if wanted_class == actual_class else "conflict", "requested": wanted_type, "actual": actual_type, "evidence": {"request": wanted_type, "candidate": actual_type, "request_source": "data_type", "candidate_source": "data_type"}})
    # Unparsed words are a note; two different unparsed statements need review.
    residuals = (wanted.get("residual") or "", actual.get("residual") or "")
    for label, residual in zip(("talep", "kolon"), residuals):
        if residual:
            notes.append("ayrıştırılamayan ifade: " + label + " '" + residual + "'")
    if all(residuals) and set(residuals[0].split()) != set(residuals[1].split()):
        checks.append({"dimension": "qualifier", "status": "missing", "requested": residuals[0], "actual": residuals[1], "evidence": {"request": residuals[0], "candidate": residuals[1], "request_source": "residual", "candidate_source": "residual"}})
    for check in checks:
        if check["status"] != "compatible":
            reasons.append(check["status"] + ":" + check["dimension"])
    # Catalog column: the comment window wins over the name suffix; a note, not a conflict.
    candidate_issues = list(actual.get("issues", []))
    mismatch = actual.get("window_name_mismatch")
    if isinstance(mismatch, dict) and "conflict:window" in candidate_issues:
        candidate_issues.remove("conflict:window")
        notes.append("window_name_mismatch: ad " + _window_text(mismatch.get("name")) + ", yorum " + _window_text(mismatch.get("description")))
    for origin, issues in (("request", wanted.get("issues", [])), ("candidate", candidate_issues)):
        reasons.extend(origin + ":" + str(issue) for issue in issues)
    conflicting = any(c["status"] == "conflict" for c in checks) or any("conflict:" in reason for reason in reasons)
    # An empty match is not support: at least two meaning dimensions must agree.
    agreed = sum(1 for c in checks if c["status"] == "compatible" and c["dimension"] not in DERIVED_DIMENSIONS)
    if not conflicting and not reasons and agreed < 2:
        reasons.append("insufficient_evidence")
    status = "conflict" if conflicting else "insufficient" if reasons else "supported"
    reasons.extend(notes)
    return {"catalog_id": record.get("catalog_id", ""), "path": ".".join(str(record.get(k) or "?") for k in ("source_database", "environment", "container", "schema", "object", "column")), "score": record.get("_score", 0.0), "retrieval": record.get("_retrieval", []), "checks": checks, "status": status, "reasons": reasons}


DIFFERENCE_LABELS = {
    "entity": "Varlık", "grain": "Kırılım", "concept": "Kavram", "transaction": "İşlem türü", "direction": "Yön",
    "measure": "Ölçü", "aggregation": "Toplama", "grouping": "Gruplama", "window": "Dönem",
    "window_bounds": "Dönem sınırı", "reference_date": "Referans tarihi", "person_key": "Kişi anahtarı",
    "unit": "Birim", "codes": "Kodlar", "zero_policy": "Sıfır kuralı", "null_policy": "Boş değer kuralı",
    "numerator": "Pay", "denominator": "Payda", "business_scope": "İş kapsamı", "data_type": "Alan tipi",
    "qualifier": "Ek niteleme",
}
VALUE_TEXT = {
    "measure": {"count": "adet", "amount": "tutar", "ratio": "oran", "rate": "oran", "distinct_people": "farklı kişi sayısı", "distinct_accounts": "farklı hesap sayısı", "category": "kategori", "duration": "süre"},
    "grain": {"customer": "müşteri", "account": "hesap", "application": "başvuru", "loan": "kredi", "card": "kart", "credit_card": "kredi kartı", "transaction": "işlem"},
    "entity": {"customer": "müşteri", "application": "başvuru"},
    "direction": {"incoming": "gelen", "outgoing": "giden", "both": "gelen ve giden"},
    "aggregation": {"sum": "toplam", "max": "en yüksek", "mean": "ortalama", "count": "adet", "count_distinct": "tekil adet"},
    "unit": {"percent": "yüzde", "day": "gün", "week": "hafta", "month": "ay", "year": "yıl", "hour": "saat", "minute": "dakika"},
}


def _value_text(dimension, value):
    if dimension == "window":
        return _window_text(value)
    if isinstance(value, (list, tuple)):
        return " > ".join(_value_text(dimension, v) for v in value)
    if isinstance(value, dict):
        # Structured facts (window bounds, reference dates, grouping keys) have no lookup label.
        return ", ".join(str(k) + "=" + _value_text(dimension, v) for k, v in sorted(value.items()))
    return VALUE_TEXT.get(dimension, {}).get(value, str(value))


def differences(evaluation):
    """Short Turkish texts for conflict and missing checks; conflicts first, in check order."""
    out = []
    for wanted in ("conflict", "missing"):
        for check in evaluation.get("checks", []):
            if check.get("status") != wanted:
                continue
            dimension, requested, actual = check["dimension"], check.get("requested"), check.get("actual")
            label = DIFFERENCE_LABELS.get(dimension, dimension)
            lower = label.replace("İ", "i").lower()
            if requested is not None and actual is not None:
                state = " farklı" if wanted == "conflict" else " doğrulanamadı"
                text = label + state + ": talep " + _value_text(dimension, requested) + ", kolon " + _value_text(dimension, actual)
            elif actual is None:
                text = "Kolon açıklamasında " + lower + " bilgisi yok"
            else:
                text = "Talep açıklamasında " + lower + " bilgisi yok"
            out.append({"dimension": dimension, "requested": requested, "actual": actual, "text": text})
    return out


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


def prepare_production_catalog(records):
    """Prepare immutable-for-this-run lookup data without a shared/global cache.

    Metadata definitions are parsed at most once here. Movement-source discovery
    then uses the same trusted grain/transaction predicates as the direct path.
    The caller must rebuild this context after changing the catalog snapshot.
    """
    by_id, definitions, movements = {}, {}, defaultdict(list)
    for record in records:
        identity = record["catalog_id"]
        by_id[identity] = record
        definition = _definition(record, True)
        definitions[identity] = definition
        facts = definition["facts"]
        grain, transaction = facts.get("grain", {}), facts.get("transaction", {})
        if (_trusted(grain, "grain") and grain["value"] == "transaction"
                and _trusted(transaction, "transaction")):
            movements[_key(transaction["value"])].append(identity)
    return {"records_by_id": by_id, "definitions_by_id": definitions,
            "movement_sources": dict(movements)}


def assess_production(request, results_by_id, dependency_graph, records, catalog_context=None):
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
        # Without a trusted requested transaction no record can satisfy this
        # predicate. Do not reparse an entire catalog to establish that fact.
        if _trusted(requested_transaction, "transaction"):
            if catalog_context is not None:
                source_ids = list(catalog_context["movement_sources"].get(_key(requested_transaction["value"]), ()))
            else:
                for record in records:
                    facts = _definition(record, True)["facts"]
                    grain, transaction = facts.get("grain", {}), facts.get("transaction", {})
                    if _trusted(grain, "grain") and grain["value"] == "transaction" and _trusted(transaction, "transaction") and _key(transaction["value"]) == _key(requested_transaction["value"]):
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
    by_record = (catalog_context["records_by_id"] if catalog_context is not None
                 else {r["catalog_id"]: r for r in records})
    def record_definition(record):
        if catalog_context is not None:
            return catalog_context["definitions_by_id"][record["catalog_id"]]
        return _definition(record, True)
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
        actual = record_definition(record)["facts"]
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
            x, y = record_definition(a)["facts"].get(dimension), record_definition(b)["facts"].get(dimension)
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
