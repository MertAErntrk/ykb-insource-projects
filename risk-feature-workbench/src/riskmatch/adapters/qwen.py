"""Config-driven local embedding and LLM clients. Model identity is never inferred."""

import json
import hashlib
import math

from riskmatch.adapters._http import (
    _bounded_int,
    _bounded_timeout,
    post_json,
    render_template,
    response_path,
    validate_endpoint,
)
from riskmatch.errors import ConfigError, IntegrationError


def _identity(config):
    model = config.get("model")
    version = config.get("model_version")
    if not isinstance(model, str) or not model or not isinstance(version, str) or not version:
        raise ConfigError("model ve model_version açıkça belirtilmeli.")
    return model, version


def _has_placeholder(value, placeholder):
    if isinstance(value, dict):
        return any(_has_placeholder(item, placeholder) for item in value.values())
    if isinstance(value, list):
        return any(_has_placeholder(item, placeholder) for item in value)
    return value == "{" + placeholder + "}"


class EmbeddingAdapter:
    def __init__(self, config, *, transport=None):
        self.config = dict(config)
        validate_endpoint(self.config)
        _bounded_timeout(self.config)
        _bounded_int(self.config.get("max_retries"), 1, 0, 2, "max_retries")
        self.model, self.model_version = _identity(self.config)
        self.dimensions = _bounded_int(self.config.get("dimensions"), None, 1, 8192, "dimensions")
        if self.dimensions is None:
            raise ConfigError("Embedding boyutu dimensions gerekli.")
        if not isinstance(self.config.get("request_template"), dict):
            raise ConfigError("Embedding request_template gerekli.")
        if not _has_placeholder(self.config["request_template"], "texts"):
            raise ConfigError("Embedding request_template {texts} içermeli.")
        if not isinstance(self.config.get("response_path"), str):
            raise ConfigError("Embedding response_path gerekli.")
        self.max_batch = _bounded_int(self.config.get("max_batch"), 16, 1, 64, "max_batch")
        contract = {key: self.config.get(key) for key in
                    ("endpoint", "model", "model_version", "dimensions", "request_template", "response_path")}
        self.cache_identity = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False,
                                                        separators=(",", ":")).encode()).hexdigest()
        self.transport = transport

    def embed(self, texts):
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise ConfigError("Embedding girdisi metin listesi olmalı.")
        if not texts:
            return []
        if any(len(text) > 8000 for text in texts):
            raise ConfigError("Embedding metni uzunluk sınırını aşıyor.")
        vectors = []
        for start in range(0, len(texts), self.max_batch):
            batch = texts[start : start + self.max_batch]
            payload = render_template(
                self.config["request_template"],
                {"texts": batch, "model": self.model, "model_version": self.model_version},
            )
            response = post_json(self.config, payload, transport=self.transport)
            output = response_path(response, self.config["response_path"])
            if not isinstance(output, list) or len(output) != len(batch):
                raise IntegrationError("Embedding yanıtındaki vektör sayısı istekle uyuşmuyor.")
            for vector in output:
                if (not isinstance(vector, list) or len(vector) != self.dimensions
                        or any(isinstance(x, bool) or not isinstance(x, (int, float))
                               or not math.isfinite(x) for x in vector)):
                    raise IntegrationError("Embedding yanıtının boyutu veya sayısal değerleri geçersiz.")
                vectors.append([float(x) for x in vector])
        return vectors


class LLMAdapter:
    """Returns checked suggestions only. Deterministic support remains elsewhere."""

    def __init__(self, config, *, transport=None):
        self.config = dict(config)
        validate_endpoint(self.config)
        _bounded_timeout(self.config)
        _bounded_int(self.config.get("max_retries"), 1, 0, 2, "max_retries")
        self.model, self.model_version = _identity(self.config)
        self.contract = self.config.get("contract")
        if self.contract not in ("openai_chat", "template"):
            raise ConfigError("LLM contract openai_chat veya template olmalı.")
        if self.contract == "template" and (
            not isinstance(self.config.get("request_template"), dict)
            or not isinstance(self.config.get("response_path"), str)
        ):
            raise ConfigError("Template LLM için request_template ve response_path gerekli.")
        if self.contract == "template" and not _has_placeholder(self.config["request_template"], "context"):
            raise ConfigError("LLM request_template {context} içermeli.")
        self.max_candidates = _bounded_int(self.config.get("max_candidates"), 8, 1, 24, "max_candidates")
        self.max_chars = _bounded_int(self.config.get("max_chars"), 1800, 100, 6000, "max_chars")
        self.max_output_tokens = _bounded_int(
            self.config.get("max_output_tokens"), 600, 64, 2048, "max_output_tokens"
        )
        self.transport = transport

    def evaluate(self, request, candidates):
        if not isinstance(request, dict) or not isinstance(candidates, list):
            raise ConfigError("LLM isteği ve aday listesi geçersiz.")
        selected = candidates[: self.max_candidates]
        if not selected:
            return {"assessments": [], "model": self.model, "model_version": self.model_version}
        names = set()
        context = []
        evidence_sources = {}
        for record in selected:
            if not isinstance(record, dict) or not isinstance(record.get("catalog_id"), str):
                raise ConfigError("Aday katalog kimliği eksik.")
            catalog_id = record["catalog_id"]
            if catalog_id in names:
                raise ConfigError("Yinelenen katalog kimliği var.")
            names.add(catalog_id)
            fields = {key: str(record.get(key) or "")[: self.max_chars]
                      for key in ("schema", "object", "column", "column_comment", "table_comment")}
            evidence_sources[catalog_id] = [v for v in fields.values() if v]
            context.append({"catalog_id": catalog_id, **fields})
        constrained_request = {key: str(request.get(key) or "")[: self.max_chars]
                               for key in ("row_id", "name", "description", "data_type", "category")}
        values = {"model": self.model, "context": {"request": constrained_request, "candidates": context},
                  "max_output_tokens": self.max_output_tokens}
        if self.contract == "openai_chat":
            payload = {
                "model": self.model,
                "temperature": 0,
                "max_tokens": self.max_output_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "Return JSON {\"assessments\":[{\"catalog_id\":\"...\",\"evidence\":\"exact source substring\",\"note\":\"...\"}]}. Use only supplied catalog_id values and verbatim evidence. If none, return empty assessments. This is a suggestion, never a verified match."},
                    {"role": "user", "content": json.dumps(values["context"], ensure_ascii=False)},
                ],
            }
            path = "choices.0.message.content"
        else:
            payload = render_template(self.config["request_template"], values)
            path = self.config["response_path"]
        response = post_json(self.config, payload, transport=self.transport)
        raw = response_path(response, path)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raise IntegrationError("LLM geçerli JSON nesnesi döndürmedi.") from None
        if not isinstance(raw, dict) or not isinstance(raw.get("assessments"), list):
            raise IntegrationError("LLM çıktı şeması geçersiz.")
        if len(raw["assessments"]) > len(selected):
            raise IntegrationError("LLM beklenenden fazla öneri döndürdü.")
        checked = []
        seen = set()
        for item in raw["assessments"]:
            if not isinstance(item, dict) or set(item) - {"catalog_id", "evidence", "note"}:
                raise IntegrationError("LLM öneri şeması geçersiz.")
            catalog_id, evidence, note = item.get("catalog_id"), item.get("evidence"), item.get("note", "")
            if (not isinstance(catalog_id, str) or catalog_id not in names or catalog_id in seen
                    or not isinstance(evidence, str) or not evidence
                    or not any(evidence in source for source in evidence_sources[catalog_id])
                    or not isinstance(note, str) or len(note) > 1000):
                raise IntegrationError("LLM katalog kimliği veya kaynak kanıtı geçersiz.")
            seen.add(catalog_id)
            checked.append({"catalog_id": catalog_id, "evidence": evidence, "note": note})
        return {"assessments": checked, "model": self.model, "model_version": self.model_version}
