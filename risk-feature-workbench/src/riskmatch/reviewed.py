"""Optional expert-maintained definitions, never generated or trusted from LLM output."""
from copy import deepcopy
import json
from pathlib import Path

from .errors import ConfigError

def apply_reviewed(path,requests,snapshot):
    """Return a provenance manifest; mutate only explicitly identified definitions.

    Reviewer attribution is audit information, not a digital signature or proof of
    semantic correctness. Institutional approval remains an operational process.
    """
    from .semantics import DIMENSIONS,normalize,parse_definition
    try:
        payload=json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError,ValueError):
        raise ConfigError("Onaylı tanım dosyası okunamadı veya geçersiz JSON.") from None
    if not isinstance(payload,dict) or payload.get("schema_version")!=1:
        raise ConfigError("Onaylı tanım schema_version=1 olmalı.")
    review=payload.get("review",{})
    if not isinstance(review,dict) or not all(isinstance(review.get(k),str) and review[k].strip() for k in ("reviewer","reviewed_at","reference")):
        raise ConfigError("Onaylı tanımlar için reviewer, reviewed_at ve reference gerekli.")
    counts={}
    for group,items,id_field in (("requests",requests,"row_id"),("catalog",snapshot["records"],"catalog_id")):
        definitions=payload.get(group,{})
        if not isinstance(definitions,dict):
            raise ConfigError("Onaylı tanım grupları kimlik anahtarlı nesne olmalı.")
        lookup={r[id_field]:r for r in items}
        if set(definitions)-set(lookup):
            raise ConfigError("Onaylı tanım kimliği mevcut giriş veya katalogda bulunamadı. Güncel kimliklerle yenileyin.")
        for identity,supplied in definitions.items():
            if not isinstance(supplied,dict) or supplied.get("covers_full_definition") is not True or not isinstance(supplied.get("facts"),dict):
                raise ConfigError("Her onaylı tanım facts ve covers_full_definition=true içermeli.")
            facts=supplied["facts"]
            if set(facts)-set(DIMENSIONS):
                raise ConfigError("Onaylı tanımda desteklenmeyen boyut var.")
            for fact in facts.values():
                if not isinstance(fact,dict) or fact.get("source")!="verified_metadata" or not isinstance(fact.get("evidence"),str) or not fact["evidence"].strip() or fact.get("value") is None:
                    raise ConfigError("Her onaylı boyut verified_metadata kaynağı ve açık kanıt gerektirir.")
                value=fact["value"]
                if isinstance(value,str) and not value.strip() or isinstance(value,(list,dict)) and not value:
                    raise ConfigError("Onaylı boyut değeri boş olamaz; bilinmeyen boyutu çıkartın.")
            record=lookup[identity]
            original=record.get("definition") or parse_definition(record.get("column",""),record.get("column_comment",""))
            issues=[]
            for dim,fact in original.get("facts",{}).items():
                if dim in facts and normalize(str(fact["value"]))!=normalize(str(facts[dim]["value"])):
                    issues.append("conflict:reviewed_definition:"+dim)
            # A review may resolve unknown qualifiers, but cannot silently discard explicit conflicts.
            issues.extend(i for i in original.get("issues",[]) if "conflict:" in i)
            formula=supplied.get("formula",original.get("formula"))
            if original.get("formula") and formula is None:
                raise ConfigError("Onaylı tanım kaynak formülü silemez.")
            if formula:
                if not isinstance(formula,dict) or formula.get("op")!="divide" or not all(isinstance(formula.get(k),str) for k in ("numerator","denominator")):
                    raise ConfigError("Onaylı formül yalnız adlandırılmış iki alanın bölümü olabilir.")
                if original.get("formula") and any(original["formula"].get(k) is not None and original["formula"].get(k)!=formula.get(k) for k in ("op","numerator","denominator","zero","null")):
                    issues.append("conflict:reviewed_formula")
                for dim,key in (("numerator","numerator"),("denominator","denominator"),("zero_policy","zero"),("null_policy","null")):
                    if dim in facts and facts[dim]["value"]!=formula.get(key):
                        issues.append("conflict:reviewed_formula:"+dim)
            merged={**deepcopy(original.get("facts",{})),**deepcopy(facts)}
            record["definition"]={"facts":merged,"issues":issues,"formula":deepcopy(formula)}
        counts[group]=len(definitions)
    return {"version":payload.get("version","unversioned"),"review":review,"counts":counts}
