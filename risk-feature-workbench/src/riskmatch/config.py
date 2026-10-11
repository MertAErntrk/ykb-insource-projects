"""Secrets içermeyen dosya yapılandırması; gerçek erişim açık seçim ister."""
from copy import deepcopy
import json
from pathlib import Path

from .errors import ConfigError

DEFAULTS = {
    "mode": "demo",
    "retrieval": {"limit": 8, "expanded_limit": 24, "search_mode": "full", "candidate_limit": 24, "embedding_budget": 256},
    "llm_review": True,
    "naming": {"windows": {"LM": "last_month", "LW": "last_week", "L2W": "last_2_weeks", "L3M": "last_3_months"}},
}

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
    for field, low, high in (("candidate_limit", 8, 100), ("embedding_budget", 0, 2048)):
        value = retrieval[field]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ConfigError("Arama bütçesi geçersiz: " + field)
    if not isinstance(config.get("llm_review"), bool):
        raise ConfigError("llm_review true veya false olmalı.")
    naming=config.get("naming")
    if not isinstance(naming,dict) or not isinstance(naming.get("windows"),dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in naming["windows"].items()):
        raise ConfigError("naming.windows metin anahtar/değer sözlüğü olmalı.")
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
