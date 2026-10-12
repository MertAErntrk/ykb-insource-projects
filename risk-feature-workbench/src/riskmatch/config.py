"""Secrets içermeyen dosya yapılandırması; gerçek erişim açık seçim ister."""
from copy import deepcopy
import json
from pathlib import Path

from .errors import ConfigError

# Şema önceliği (Faz 7): önce DM_CAD, bulunamazsa DM, sonra EDW. Seçili ama listede olmayan şemalar son katmandır.
DEFAULT_SCHEMA_TIERS = [{"name": "DM_CAD", "schemas": ["DM_CAD"]},
                        {"name": "DM", "schemas": ["DM"]},
                        {"name": "EDW", "schemas": ["EDW"]}]
# Taramada dışlanan nesneler: DM_CAD'deki ara (INP_) ve çıktı (OUT_) tabloları.
DEFAULT_EXCLUDE_OBJECTS = [{"schema": "DM_CAD", "prefixes": ["INP_", "OUT_"]}]
RATIO_RULES = ("suffix_pair", "approved_bases_only")
MAX_CATALOG_RECORDS = 100000

DEFAULTS = {
    "mode": "demo",
    # limit/expanded_limit/search_mode/candidate_limit/embedding_budget are accepted for old config files
    # but no longer change the search: every row gets the same channels and pool (0.5).
    "retrieval": {"limit": 8, "expanded_limit": 24, "search_mode": "full", "candidate_limit": 24, "embedding_budget": 256,
                  "pool_size": 30, "lexical_k": 40, "semantic_k": 30, "shortlist_embed": 10, "background_size": 128,
                  "table_k": 10, "table_embed": 40, "gate": {}, "schema_tiers": DEFAULT_SCHEMA_TIERS},
    "llm_review": True,
    "naming": {"windows": {"LM": "last_month", "LW": "last_week", "L2W": "last_2_weeks", "L3M": "last_3_months"},
               "ratio_rule": "suffix_pair"},
}


def _schema_name(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 128


def validate_schema_tiers(tiers):
    """[{name, schemas:[...]}] listesi; boş liste eski tek havuz davranışıdır. Büyük harfli kopya döner."""
    if not isinstance(tiers, list) or len(tiers) > 10:
        raise ConfigError("retrieval.schema_tiers en çok 10 katmanlı liste olmalı.")
    out, seen = [], set()
    for tier in tiers:
        if not isinstance(tier, dict) or not isinstance(tier.get("schemas"), list) or not tier["schemas"]                 or not all(_schema_name(s) for s in tier["schemas"]):
            raise ConfigError("Her şema katmanı boş olmayan 'schemas' listesi içermeli.")
        schemas = [s.strip().upper() for s in tier["schemas"]]
        if seen.intersection(schemas):
            raise ConfigError("Bir şema birden çok katmanda olamaz.")
        seen.update(schemas)
        name = tier.get("name") or "/".join(schemas)
        if not isinstance(name, str):
            raise ConfigError("Şema katmanı adı metin olmalı.")
        out.append({"name": name, "schemas": schemas})
    return out


def validate_exclude_objects(rules):
    """[{schema, prefixes:[...]}]; önekler büyük harfle karşılaştırılır."""
    if not isinstance(rules, list) or len(rules) > 30:
        raise ConfigError("exclude_objects en çok 30 kurallı liste olmalı.")
    out = []
    for rule in rules:
        prefixes = rule.get("prefixes") if isinstance(rule, dict) else None
        if not isinstance(rule, dict) or not _schema_name(rule.get("schema")) or not isinstance(prefixes, list)                 or not prefixes or not all(isinstance(p, str) and 0 < len(p.strip()) <= 30 for p in prefixes):
            raise ConfigError("Dışlama kuralı {schema, prefixes:[...]} biçiminde olmalı.")
        out.append({"schema": rule["schema"].strip().upper(), "prefixes": [p.strip().upper() for p in prefixes]})
    return out

def load_config(path=None, mode="demo", *, data=None):
    if mode not in {"demo", "real"}:
        raise ConfigError("Mod demo veya real olmalı.")
    config = deepcopy(DEFAULTS)
    if path or data is not None:
        try:
            supplied = deepcopy(data) if data is not None else json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise ConfigError("Yapılandırma dosyası okunamadı veya JSON geçersiz.") from None
        if not isinstance(supplied, dict):
            raise ConfigError("Yapılandırma JSON nesnesi olmalı.")
        config.update(supplied)
        if config.get("mode") != mode:
            raise ConfigError("Komut modu ile yapılandırma modu uyuşmuyor.")
    elif mode == "real":
        raise ConfigError("Gerçek mod için --config config.local.json gerekli.")
    config["mode"] = mode
    retrieval = config.get("retrieval", {})
    if not isinstance(retrieval,dict):
        raise ConfigError("retrieval ayarı nesne olmalı.")
    retrieval = {**DEFAULTS["retrieval"], **retrieval}
    config["retrieval"] = retrieval
    if retrieval["search_mode"] not in {"fast", "full"}:
        raise ConfigError("Arama biçimi fast veya full olmalı.")
    for field, low, high in (("candidate_limit", 8, 100), ("embedding_budget", 0, 2048),
                             ("pool_size", 5, 60), ("lexical_k", 5, 200), ("semantic_k", 0, 200),
                             ("shortlist_embed", 0, 50), ("background_size", 16, 512)):
        value = retrieval[field]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ConfigError("Arama ayarı geçersiz: " + field)
    if not isinstance(retrieval["gate"], dict):
        raise ConfigError("retrieval.gate nesne olmalı.")
    table_k = retrieval["table_k"]
    if isinstance(table_k, bool) or not isinstance(table_k, int) or not 0 <= table_k <= 50:
        raise ConfigError("Arama ayarı geçersiz: table_k")
    table_embed = retrieval["table_embed"]
    if isinstance(table_embed, bool) or not isinstance(table_embed, int) or not 0 <= table_embed <= 200:
        raise ConfigError("Arama ayarı geçersiz: table_embed")
    retrieval["schema_tiers"] = validate_schema_tiers(retrieval["schema_tiers"])
    if not isinstance(config.get("llm_review"), bool):
        raise ConfigError("llm_review true veya false olmalı.")
    naming=config.get("naming")
    if not isinstance(naming,dict) or not isinstance(naming.get("windows"),dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in naming["windows"].items()):
        raise ConfigError("naming.windows metin anahtar/değer sözlüğü olmalı.")
    naming.setdefault("ratio_rule", DEFAULTS["naming"]["ratio_rule"])
    if naming["ratio_rule"] not in RATIO_RULES:
        raise ConfigError("naming.ratio_rule suffix_pair veya approved_bases_only olmalı.")
    if config.get("reviewed_definitions_path"):
        reviewed_path=Path(config["reviewed_definitions_path"])
        base=Path(path).resolve().parent if path else Path.cwd()
        config["reviewed_definitions_path"]=str((base/reviewed_path).resolve()) if not reviewed_path.is_absolute() else str(reviewed_path)
    for field in ("limit", "expanded_limit"):
        value = retrieval.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
            raise ConfigError("Arama sınırları 1–100 arasında tam sayı olmalı.")
    if retrieval["expanded_limit"] < retrieval["limit"]:
        raise ConfigError("Genişletilmiş arama sınırı ilk aramadan küçük olamaz.")
    if mode == "real":
        for section in ("oracle","llm","embedding"):
            if isinstance(config.get(section),dict) and any(k.lower() in {"password","api_key","secret","token","wallet_password"} for k in config[section]):
                raise ConfigError("Sırlar yapılandırma dosyasına yazılmamalı; *_env alanlarını kullanın.")
        if config.get("allow_real_connections") is not True:
            raise ConfigError("Gerçek bağlantılar kapalı. İş bilgisayarında allow_real_connections=true ayarlayın.")
        use_models=config.get("use_models",True)
        if not isinstance(use_models,bool):
            raise ConfigError("use_models true veya false olmalı.")
        model_sections = ("embedding", "llm") if config["llm_review"] else ("embedding",)
        for section in (("oracle",) + model_sections if use_models else ("oracle",)):
            if not isinstance(config.get(section), dict):
                raise ConfigError(f"Gerçek mod için {section} ayarları gerekli.")
        # Validate both model contracts before Oracle can initiate a connection.
        from .adapters.qwen import EmbeddingAdapter, LLMAdapter
        from .adapters.oracle import OracleAdapter
        OracleAdapter(config["oracle"])
        if use_models:
            EmbeddingAdapter(config["embedding"])
            if config["llm_review"]:
                LLMAdapter(config["llm"])
    return config

def signature_config(config):
    """Only a hash of this representation is persisted, never config values."""
    return json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
