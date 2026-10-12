"""Config-driven local embedding and LLM clients. Model identity is never inferred."""

import json
import hashlib
import math

from riskmatch.adapters._http import (
    _bounded_int,
    _bounded_timeout,
    _ReusableHTTPS,
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
    def __init__(self, config, *, transport=None, api_key=None):
        self.config = dict(config)
        self.api_key = api_key
        validate_endpoint(self.config)
        _bounded_timeout(self.config)
        _bounded_int(self.config.get("max_retries"), 1, 0, 2, "max_retries")
        self.model_selection = self.config.get("model_selection", "explicit")
        if self.model_selection not in ("explicit", "server_default"):
            raise ConfigError("Embedding model_selection explicit veya server_default olmalı.")
        if self.model_selection == "server_default":
            if any(self.config.get(key) not in (None, "") for key in ("model", "model_version")):
                raise ConfigError("Sunucu varsayılanı seçiminde model ve model_version boş kalmalı.")
            self.model, self.model_version = "", ""
        else:
            self.model, self.model_version = _identity(self.config)
        # A deployment label is not a model revision; its weights may change in place.
        self.identity_status = ("unknown" if self.model_selection == "server_default"
                                else "unverified" if self.model_version.startswith("deployment:")
                                else "configured")
        self.persistent_cache_safe = self.identity_status == "configured"
        self.dimensions = _bounded_int(self.config.get("dimensions"), None, 1, 8192, "dimensions")
        if self.dimensions is None:
            raise ConfigError("Embedding boyutu dimensions gerekli.")
        if not isinstance(self.config.get("request_template"), dict):
            raise ConfigError("Embedding request_template gerekli.")
        if self.model_selection == "server_default" and (
            any(key in self.config["request_template"] for key in ("model", "model_version"))
            or any(_has_placeholder(self.config["request_template"], key)
                   for key in ("model", "model_version"))
        ):
            raise ConfigError("Sunucu varsayılanı isteği model/model_version alanı veya yer tutucusu içeremez.")
        if not _has_placeholder(self.config["request_template"], "texts"):
            raise ConfigError("Embedding request_template {texts} içermeli.")
        if not isinstance(self.config.get("response_path"), str):
            raise ConfigError("Embedding response_path gerekli.")
        self.max_batch = _bounded_int(self.config.get("max_batch"),
                                     1 if self.model_selection == "server_default" else 16,
                                     1, 64, "max_batch")
        self.rejected_batches = 0
        self.config["max_response_bytes"] = response_limit(self.max_batch, self.dimensions)
        # Optional asymmetric wrappers (e.g. an instruction prefix on queries only). Default: text as is.
        self.templates = {}
        for kind in ("query", "document"):
            template = self.config.get(kind + "_template") or "{text}"
            if not isinstance(template, str) or template.count("{text}") != 1 or len(template) > 1000:
                raise ConfigError("Embedding " + kind + "_template tek bir {text} içeren kısa bir metin olmalı.")
            self.templates[kind] = template
        contract = {key: self.config.get(key) for key in
                    ("endpoint", "model", "model_version", "dimensions", "request_template", "response_path")}
        contract["templates"] = self.templates
        contract["model_selection"] = self.model_selection
        self.cache_identity = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False,
                                                        separators=(",", ":")).encode()).hexdigest()
        self.transport = transport if transport is not None else _ReusableHTTPS()

    def embed(self, texts, kind="document"):
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise ConfigError("Embedding girdisi metin listesi olmalı.")
        if kind not in self.templates:
            raise ConfigError("Embedding türü query veya document olmalı.")
        if not texts:
            return []
        if any(len(text) > 8000 for text in texts):
            raise ConfigError("Embedding metni uzunluk sınırını aşıyor.")
        if self.templates[kind] != "{text}":
            texts = [self.templates[kind].replace("{text}", text) for text in texts]
        vectors = []
        for start in range(0, len(texts), self.max_batch):
            vectors.extend(self._embed_batch(texts[start : start + self.max_batch]))
        return vectors

    def _embed_batch(self, batch):
        try:
            return self._post(batch)
        except IntegrationError as error:
            if len(batch) == 1 or not (getattr(error, "batch_rejected", False)
                                       or getattr(error, "status", None) in BATCH_REJECTED_STATUSES):
                raise
        # Sunucu bu toplu isteği reddetti (boyut/sözleşme): aynı metinler tek tek gider.
        # Üç kez tekrarlanırsa sonraki istekler de tek metinlik olur; indeks yarıda kalmaz.
        self.rejected_batches += 1
        if self.rejected_batches >= 3:
            self.max_batch = 1
        return [vector for text in batch for vector in self._post([text])]

    def _post(self, batch):
        payload = render_template(
            self.config["request_template"],
            {"texts": batch, "model": self.model, "model_version": self.model_version},
        )
        response = post_json(self.config, payload, transport=self.transport,api_key=self.api_key)
        output = response_path(response, self.config["response_path"])
        if not isinstance(output, list) or len(output) != len(batch):
            error = IntegrationError("Embedding yanıtındaki vektör sayısı istekle uyuşmuyor.")
            error.batch_rejected = True
            raise error
        vectors = []
        for vector in output:
            if (not isinstance(vector, list) or len(vector) != self.dimensions
                    or any(isinstance(x, bool) or not isinstance(x, (int, float))
                           or not math.isfinite(x) for x in vector)):
                raise IntegrationError("Embedding yanıtının boyutu veya sayısal değerleri geçersiz.")
            vectors.append([float(x) for x in vector])
        return vectors


def response_limit(batch, dimensions):
    """JSON yanıt üst sınırı: sayı başına en çok ~32 bayt (ör. -1.2345678901234567e-05,).

    4096 boyutlu 16 vektör ~1,4 MB tutar; sabit 1 MB sınırı toplu isteği engellerdi.
    """
    return max(1_000_000, batch * (dimensions * 32 + 256) + 65_536)


# Toplu isteğin boyutu ya da biçimi reddedildiğinde dönen kodlar; kimlik, yetki ve sunucu hataları bölünmez.
BATCH_REJECTED_STATUSES = {400, 413, 422}


class LLMAdapter:
    """Returns checked suggestions only. Deterministic support remains elsewhere."""

    def __init__(self, config, *, transport=None, api_key=None):
        self.config = dict(config)
        self.api_key = api_key
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
        self.transport = transport if transport is not None else _ReusableHTTPS()

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
        response = post_json(self.config, payload, transport=self.transport,api_key=self.api_key)
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
