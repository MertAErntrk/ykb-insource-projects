"""Bounded, response-verified discovery of the institution's model services.

Only the configured host and known OpenAI / TEI routes are tested. A model list
alone never constitutes a successful connection test: the application adapter
must also accept a small real inference response. No credentials or response
bodies are included in the report.
"""
from copy import deepcopy
import io
import json
import math
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .adapters._http import _NoRedirect, response_path, validate_endpoint
from .adapters.qwen import EmbeddingAdapter, LLMAdapter, _has_placeholder, response_limit
from .deployment import EMBEDDING_BASE_URL, LLM_BASE_URL
from .errors import ConfigError, IntegrationError

MAX_RESPONSE = 1_000_000
TOTAL_SECONDS = 55
FALLBACK_CODES = {"HTTP_404", "HTTP_405", "MODEL_LIST_FORMAT"}
# Sunucu varsayılanında toplu istek gerçek bir çağrıyla denenir; ilk geçen boyut kullanılır.
BATCH_SIZES = (16, 4)


class _Failure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _string(value, label, limit=4096):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise _Failure("INVALID_SETTINGS", label + " geçersiz.")
    return value.strip()


def _url(value):
    try:
        parts = urlsplit(value)
        validate_endpoint({"endpoint": value, "allowed_hosts": [parts.hostname]})
        if parts.query:
            raise ValueError()
        # Trigger validation of malformed ports before making a request.
        parts.port
    except (ConfigError, ValueError, TypeError):
        raise _Failure("INVALID_URL", "Servis adresi HTTPS olmalı; kullanıcı bilgisi, anahtar veya sorgu parametresi içermemeli.") from None
    return value.rstrip("/")


def _same_origin(base, endpoint):
    left, right = urlsplit(base), urlsplit(endpoint)
    if (left.hostname, left.port or 443) != (right.hostname, right.port or 443):
        raise _Failure("HOST_MISMATCH", "Temel adres ve çağrı adresi aynı servis sunucusuna ait olmalı.")
    return endpoint


def _safe_failure(error):
    if isinstance(error, _Failure):
        return error
    if isinstance(error, urllib.error.HTTPError):
        status = error.code
        hints = {
            400: "İstek servis sözleşmesine uymadı; seçilen model ve çağrı biçimini kontrol edin.",
            401: "Servis kimlik doğrulaması istiyor; bu servisin API anahtarını girin.",
            403: "Servis erişimi reddetti; API anahtarı ve erişim yetkisini kontrol edin.",
            404: "Bu çağrı yolu serviste bulunamadı.",
            405: "Servis bu çağrı yolunda istenen HTTP yöntemini desteklemiyor.",
            407: "Kurum proxy sunucusu kimlik doğrulaması istiyor.",
            408: "Servis isteği zaman aşımına uğradı.",
            413: "Servis deneme isteğinin boyutunu kabul etmedi.",
            422: "Servis istek alanlarını kabul etmedi; model veya sözleşme uyumunu kontrol edin.",
            429: "Servis istek sınırına ulaştı; kısa süre sonra tekrar deneyin.",
            502: "Ağ geçidi model sunucusuna erişemedi.",
            503: "Model servisi şu anda hazır değil.",
            504: "Ağ geçidi model yanıtını zamanında alamadı.",
        }
        message = ("Yönlendirme takip edilmedi; servis için doğrudan HTTPS adresini kullanın."
                   if 300 <= status < 400 else hints.get(status, "Servis isteği başarısız oldu; servis ekibine HTTP kodunu iletin."))
        return _Failure("HTTP_" + str(status), "HTTP " + str(status) + ": " + message)
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return _Failure("TLS_CERTIFICATE", "TLS sertifikası doğrulanamadı; kurum CA sertifikasının PEM yolunu girin.")
    if isinstance(reason, ssl.SSLError):
        return _Failure("TLS_HANDSHAKE", "TLS bağlantısı kurulamadı; HTTPS adresini ve kurum proxy ayarlarını kontrol edin.")
    if isinstance(reason, socket.gaierror):
        return _Failure("DNS", "Servis adı çözümlenemedi; kurum ağı/VPN ve DNS erişimini kontrol edin.")
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return _Failure("TIMEOUT", "Servis denemesi zaman aşımına uğradı; ağ ve model servisinin hazır olma durumunu kontrol edin.")
    if isinstance(reason, ConnectionRefusedError):
        return _Failure("CONNECTION_REFUSED", "Servis bağlantıyı reddetti; adres, port ve servis durumunu kontrol edin.")
    if isinstance(reason, (OSError, urllib.error.URLError)) or isinstance(error, urllib.error.URLError):
        return _Failure("NETWORK", "Servise erişilemedi; kurum ağı/VPN, proxy ve güvenlik duvarı erişimini kontrol edin.")
    if isinstance(error, ConfigError):
        return _Failure("INVALID_CONTRACT", "Seçilen servis sözleşmesi eksik veya geçersiz; gelişmiş çağrı ayarlarını kontrol edin.")
    if isinstance(error, IntegrationError):
        return _Failure("RESPONSE_CONTRACT", "Servis yanıt verdi ancak yanıt uygulamanın beklediği biçim veya içerik doğrulamasını geçemedi.")
    return _Failure("DIAGNOSTIC_ERROR", "Bağlantı denemesi tamamlanamadı; servis sözleşmesini kontrol edin.")


class _Client:
    def __init__(self, base, key, ca_file):
        self.base = base
        self.key = key
        self.max_response = MAX_RESPONSE
        self.deadline = time.monotonic() + TOTAL_SECONDS
        try:
            context = ssl.create_default_context(cafile=ca_file or None)
        except (OSError, ssl.SSLError):
            raise _Failure("CA_FILE", "Kurum CA dosyası okunamadı; geçerli PEM dosyasının yolunu kontrol edin.") from None
        self.opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def open(self, request, timeout=15):
        _same_origin(self.base, _url(request.full_url))
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _Failure("TIMEOUT", "Bağlantı denemesinin toplam süre sınırına ulaşıldı.")
        if self.key:
            request.add_header("Authorization", "Bearer " + self.key)
        try:
            with self.opener.open(request, timeout=min(timeout, remaining)) as response:
                # Some custom transports do not raise for non-2xx statuses.
                status = getattr(response, "status", 200)
                if not 200 <= status < 300:
                    raise urllib.error.HTTPError(request.full_url, status, "", {}, None)
                body = response.read(self.max_response + 1)
            if len(body) > self.max_response:
                raise _Failure("RESPONSE_TOO_LARGE", "Servis yanıtı boyut sınırını aştı (%d bayt)." % self.max_response)
            return io.BytesIO(body)
        except Exception as error:
            raise _safe_failure(error) from None

    def get(self, endpoint):
        request = urllib.request.Request(endpoint, headers={"Accept": "application/json"}, method="GET")
        with self.open(request) as response:
            try:
                return json.loads(response.read())
            except (ValueError, UnicodeDecodeError):
                raise _Failure("INVALID_JSON", "Servis JSON yerine farklı bir yanıt verdi; adresin API yolu olduğunu kontrol edin.") from None


class _Report:
    def __init__(self):
        self.checks = []
        self.models = []

    def run(self, name, operation):
        start = time.monotonic()
        try:
            value = operation()
        except Exception as error:
            failure = _safe_failure(error)
            self.checks.append({"name": name, "status": "failed", "elapsed_ms": round((time.monotonic() - start) * 1000),
                                "message": failure.message, "code": failure.code})
            raise failure from None
        self.checks.append({"name": name, "status": "passed", "elapsed_ms": round((time.monotonic() - start) * 1000),
                            "message": "Doğrulandı.", "code": "OK"})
        return value

    def explain_optional_discovery(self):
        """Only call after inference and response validation have succeeded.

        Missing optional GET routes do not make a working service unhealthy.
        Keep the observed HTTP code and timing; never soften a POST failure or
        an authentication, certificate, malformed-response or transport error.
        """
        for check in self.checks:
            if (check["status"] == "failed" and check["name"].startswith("GET ")
                    and check.get("code") in {"HTTP_404", "HTTP_405"}):
                check["status"] = "unsupported"
                check["message"] = ("Servis bu isteğe bağlı keşif isteğini desteklemiyor. "
                                    "Gerçek model çağrısı doğrulandı; bu adım bağlantıyı engellemiyor.")


def _models(client, endpoint):
    body = client.get(endpoint)
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise _Failure("MODEL_LIST_FORMAT", "Yanıt geçerli bir OpenAI model listesi içermiyor (1–100 model bekleniyor).")
    values = []
    for row in rows:
        if not isinstance(row, dict):
            raise _Failure("MODEL_LIST_FORMAT", "Model listesi beklenen id alanlarını içermiyor.")
        model = _string(row.get("id"), "Model kimliği", 512)
        if not model:
            raise _Failure("MODEL_LIST_FORMAT", "Model listesinde boş model kimliği var.")
        if model not in values:
            values.append(model)
    return values


def _select_model(models, selected):
    if selected:
        if selected not in models:
            raise _Failure("MODEL_NOT_LISTED", "Girilen model sunucunun listesinde bulunamadı; listeden bir model seçin.")
        return selected
    if len(models) == 1:
        return models[0]
    raise _Failure("MODEL_SELECTION_REQUIRED", "Servis birden fazla model sunuyor; model kimliğini listeden seçip testi tekrarlayın.")


def _version(settings, model, reported=None):
    version = _string(settings.get("model_version"), "Model sürümü", 1024)
    if version:
        return version
    return _string(reported, "Bildirilen model sürümü", 1024) or "deployment:" + model


def _base_config(base, endpoint, model, version, ca_file):
    return {"base_url": base, "endpoint": endpoint, "model": model, "model_version": version,
            "allowed_hosts": [urlsplit(base).hostname], "timeout_seconds": 30, "max_retries": 0,
            **({"ca_file": ca_file} if ca_file else {})}


def _server_default_config(base, endpoint, ca_file):
    config = _base_config(base, endpoint, "", "", ca_file)
    config.update(model_selection="server_default", max_batch=1,
                  request_template={"input": "{texts}"})
    # response_path is established from the actual response, unless supplied
    # explicitly by the user. No model identity or model version is invented.
    return config


def _llm_probe(client, config):
    adapter = LLMAdapter(config, transport=client, api_key=client.key)
    adapter.evaluate({"name": "CONNECTION_CHECK", "description": "Connection validation"},
                     [{"catalog_id": "connection-check", "column": "CONNECTION_CHECK",
                       "column_comment": "Connection validation"}])


def _embedding_probe(client, config, seen=None):
    # Discover dimension from one real vector, then validate it through the same
    # adapter used by analyses, without issuing a second inference request.
    from .adapters._http import post_json, render_template
    EmbeddingAdapter({**config, "dimensions": config.get("dimensions") or 1,
                      "response_path": config.get("response_path") or "$"},
                     transport=client, api_key=client.key)
    payload = render_template(config["request_template"], {"texts": ["Connection validation"],
                              "model": config["model"], "model_version": config["model_version"]})
    try:
        body = post_json(config, payload, transport=client, api_key=client.key)
    except _Failure as failure:
        if config.get("model_selection") == "server_default" and failure.code in {"HTTP_400", "HTTP_422"}:
            raise _Failure(failure.code, "Sunucu model alanı olmadan deneme isteğini kabul etmedi; model kimliği veya özel istek sözleşmesi servis ekibinden doğrulanmalı.") from None
        raise
    if not config.get("response_path"):
        if isinstance(body, list):
            config["response_path"] = "$"
        elif isinstance(body, dict) and isinstance(body.get("data"), list):
            config["response_path"] = "data[*].embedding"
        else:
            raise _Failure("INVALID_VECTOR", "Embedding yanıtında desteklenen vektör dizisi bulunamadı; özel yanıt yolunu kontrol edin.")
    vectors = response_path(body, config["response_path"])
    if (not isinstance(vectors, list) or len(vectors) != 1 or not isinstance(vectors[0], list)
            or not 1 <= len(vectors[0]) <= 8192
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vectors[0])):
        raise _Failure("INVALID_VECTOR", "Embedding yanıtı 1–8192 sonlu sayıdan oluşan tek vektör içermeli.")
    dimensions = len(vectors[0])
    declared = config.get("dimensions")
    if declared and declared != dimensions:
        raise _Failure("DIMENSION_MISMATCH", "Embedding yanıtının boyutu girilen boyuttan farklı; boyut alanını temizleyip yeniden doğrulayın.")

    class CapturedResponse:
        def open(self, request, timeout):
            return io.BytesIO(json.dumps(body, allow_nan=False).encode("utf-8"))

    EmbeddingAdapter({**config, "dimensions": dimensions}, transport=CapturedResponse(), api_key=client.key).embed(["Connection validation"])
    if seen is not None:
        seen["vector"] = vectors[0]
        reported = body.get("model") if isinstance(body, dict) else None
        seen["model"] = (reported.strip() if isinstance(reported, str) and reported.isprintable()
                         and 0 < len(reported.strip()) <= 200 else "")
    return dimensions


def _cosine(left, right):
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def _batch_probe(client, report, config, reference):
    """Return the largest verified batch size for a server-default embedding route.

    The known text is placed in the middle of the batch; its vector must come
    back at the same position and match the single-request vector. Any error,
    count mismatch or order doubt keeps that size unused; nothing is guessed.
    """
    if not reference:
        return 1
    path = urlsplit(config["endpoint"]).path
    for size in BATCH_SIZES:
        name = "POST %s · toplu istek denemesi (%d metin)" % (path, size)
        start = time.monotonic()
        try:
            texts = ["Batch validation %02d" % index for index in range(size)]
            middle = size // 2
            texts[middle] = "Connection validation"
            adapter = EmbeddingAdapter({**config, "max_batch": size}, transport=client, api_key=client.key)
            # Toplu yanıt büyüktür (4096 boyut × 16 ≈ 1,4 MB); sınır analizdeki adaptörle aynı.
            client.max_response = response_limit(size, len(reference))
            try:
                # _post: geri dönüşsüz tek istek; reddedilirse tek tek gönderime düşülmez, boyut elenir.
                vectors = adapter._post(texts)
            finally:
                client.max_response = MAX_RESPONSE
            scores = [_cosine(vector, reference) for vector in vectors]
            others = max(score for index, score in enumerate(scores) if index != middle)
            if scores[middle] < 0.98 or scores[middle] - others < 0.005:
                raise _Failure("BATCH_ORDER", "Toplu yanıttaki vektörlerin sırası doğrulanamadı.")
        except Exception as error:
            failure = _safe_failure(error)
            report.checks.append({"name": name, "status": "unsupported", "code": failure.code,
                                  "elapsed_ms": round((time.monotonic() - start) * 1000),
                                  "message": failure.message + " Bu boyutta toplu gönderim kullanılmayacak."})
            continue
        report.checks.append({"name": name, "status": "passed", "code": "OK",
                              "elapsed_ms": round((time.monotonic() - start) * 1000),
                              "message": "%d vektör doğru sırayla döndü." % size})
        return size
    return 1


def _tei(client, report, base, raw, endpoint, model):
    root = base[:-3] if base.endswith("/v1") else base
    info_endpoint = root + "/info"

    def read_info():
        info = client.get(info_endpoint)
        if not isinstance(info, dict) or not isinstance(info.get("model_id"), str) or not info["model_id"].strip():
            raise _Failure("TEI_INFO_FORMAT", "TEI bilgisi geçerli model_id içermiyor; servis sözleşmesi otomatik belirlenemedi.")
        return info

    info = report.run("GET " + urlsplit(info_endpoint).path + " · TEI model bilgisi", read_info)
    found = _string(info["model_id"], "Model kimliği", 512)
    report.models = [found]
    if model and model != found:
        raise _Failure("MODEL_NOT_LISTED", "Girilen model TEI servisinin bildirdiği modelle uyuşmuyor.")
    config = _base_config(base, endpoint or root + "/embed", found, _version(raw, found, info.get("model_sha")), raw.get("ca_file"))
    config.update(request_template={"inputs": "{texts}"}, response_path="$")
    return config


def discover_model(raw, name):
    """Return a safe diagnostic report and only response-verified form settings.

    ``raw`` is the web profile, with independent ``llm`` and ``embedding``
    sections. A failed service does not inspect the other section. Tests mock
    urllib's opener; this function never substitutes synthetic success data.
    """
    report = _Report()
    try:
        if name not in {"llm", "embedding"} or not isinstance(raw, dict) or not isinstance(raw.get(name, {}), dict):
            raise _Failure("INVALID_SETTINGS", "Model servisi seçimi geçersiz.")
        value = deepcopy(raw.get(name, {}))
        def reject_embedded_secrets(item):
            if isinstance(item, dict):
                for field, child in item.items():
                    if str(field).lower() in {"password", "api_key", "token", "authorization", "secret"}:
                        raise _Failure("INVALID_CONTRACT", "Anahtarları istek şablonuna yazmayın; API anahtarı alanını kullanın.")
                    reject_embedded_secrets(child)
            elif isinstance(item, list):
                for child in item:
                    reject_embedded_secrets(child)
        reject_embedded_secrets(value.get("request_template"))
        key = _string(value.get("api_key"), "API anahtarı", 8192)
        ca_file = _string(value.get("ca_file"), "CA dosyası")
        value["ca_file"] = ca_file
        endpoint = _string(value.get("endpoint"), "Tam çağrı adresi")
        explicit_endpoint = bool(endpoint)
        explicit_base = _string(value.get("base_url"), "Temel servis adresi")
        if not explicit_base and endpoint:
            parts = urlsplit(endpoint)
            explicit_base = parts.scheme + "://" + parts.netloc
        base = _url(explicit_base or (LLM_BASE_URL if name == "llm" else EMBEDDING_BASE_URL))
        if endpoint:
            endpoint = _same_origin(base, _url(endpoint))
        selected = _string(value.get("model"), "Model kimliği", 512)
        selection = value.get("model_selection")
        if selection not in (None, "", "explicit", "server_default"):
            raise _Failure("INVALID_SETTINGS", "Model seçim biçimi geçersiz.")
        if selection == "server_default" and (name != "embedding" or selected or value.get("model_version")):
            raise _Failure("INVALID_SETTINGS", "Sunucu varsayılanı yalnız embedding için kullanılabilir; model kimliği ve sürümü boş olmalı.")
        client = report.run("HTTPS ve sertifika ayarları", lambda: _Client(base, key, ca_file))
        config = None
        models_endpoint = ""
        models_endpoint_verified = False
        api = base if base.endswith("/v1") else base + "/v1"
        if selection == "server_default" and not endpoint:
            endpoint = api + "/embeddings"

        if endpoint and (selected or name == "embedding"):
            config = (_base_config(base, endpoint, selected, _version(value, selected), ca_file)
                      if selected else _server_default_config(base, endpoint, ca_file))
            if name == "llm":
                config["contract"] = value.get("contract") or "openai_chat"
            elif value.get("request_template"):
                pass
            elif urlsplit(endpoint).path.rstrip("/").endswith("/embed"):
                config.update(request_template={"inputs": "{texts}"}, response_path="$")
            elif urlsplit(endpoint).path.rstrip("/").endswith("/embeddings"):
                if selected:
                    config.update(request_template={"model": "{model}", "input": "{texts}"}, response_path="data[*].embedding")
            else:
                raise _Failure("EXPLICIT_CONTRACT_REQUIRED", "Özel embedding adresi için istek şablonu ve yanıt yolu gerekli.")
        else:
            models_endpoint = _same_origin(base, _url(_string(value.get("models_endpoint"), "Model listesi adresi") or api + "/models"))
            try:
                report.models = report.run("GET " + urlsplit(models_endpoint).path + " · model listesi", lambda: _models(client, models_endpoint))
                models_endpoint_verified = True
            except _Failure as failure:
                if failure.code not in FALLBACK_CODES:
                    raise
                if name == "embedding":
                    try:
                        config = _tei(client, report, base, value, endpoint, selected)
                    except _Failure as info_failure:
                        if info_failure.code not in {"HTTP_404", "HTTP_405"}:
                            raise
                        if not selected:
                            config = _server_default_config(base, endpoint or api + "/embeddings", ca_file)
                elif not selected:
                    raise _Failure("MODEL_ID_REQUIRED", "Standart model listesi okunamadı. Servis ekibinin bildirdiği model kimliğini girip testi tekrarlayın.") from None
            if config is None:
                model = _select_model(report.models, selected) if report.models else selected
                endpoint = endpoint or api + ("/chat/completions" if name == "llm" else "/embeddings")
                config = _base_config(base, endpoint, model, _version(value, model), ca_file)
                if name == "llm":
                    config["contract"] = "openai_chat"
                else:
                    config.update(request_template={"model": "{model}", "input": "{texts}"}, response_path="data[*].embedding")

        for setting in ("request_template", "response_path"):
            if value.get(setting):
                config[setting] = value[setting]
        if name == "llm" and value.get("contract"):
            config["contract"] = value["contract"]
        if (name == "embedding" and config.get("model")
                and urlsplit(config["endpoint"]).path.rstrip("/").endswith("/embeddings")
                and not models_endpoint_verified
                and not _has_placeholder(config.get("request_template"), "model")):
            configured_models = _string(value.get("models_endpoint"), "Model listesi adresi")
            if not configured_models:
                raise _Failure("MODEL_IDENTITY_UNVERIFIED", "Model alanı içermeyen embedding isteği girilen model kimliğini doğrulayamaz. Sunucu varsayılanı için model kimliğini boş bırakın; belirli model seçimi için {model} içeren servis sözleşmesi veya model listesinden yeniden keşif gerekli.")
            models_endpoint = _same_origin(base, _url(configured_models))
            report.models = report.run("GET " + urlsplit(models_endpoint).path + " · model kimliğini yeniden doğrulama",
                                       lambda: _models(client, models_endpoint))
            _select_model(report.models, config["model"])
            models_endpoint_verified = True
        if name == "embedding" and value.get("dimensions"):
            dimensions = value["dimensions"]
            if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not 1 <= dimensions <= 8192:
                raise _Failure("INVALID_DIMENSIONS", "Embedding boyutu 1–8192 arasında tam sayı olmalı.")
            config["dimensions"] = dimensions

        seen = {}
        label = "POST " + urlsplit(config["endpoint"]).path + " · gerçek deneme ve yanıt doğrulama"
        if name == "llm":
            report.run(label, lambda: _llm_probe(client, config))
        else:
            try:
                config["dimensions"] = report.run(label, lambda: _embedding_probe(client, config, seen))
            except _Failure as failure:
                if (explicit_endpoint or config.get("model_selection") == "server_default"
                        or failure.code not in {"HTTP_404", "HTTP_405"} or config.get("response_path") == "$"):
                    raise
                config = _tei(client, report, base, value, "", selected)
                for setting in ("request_template", "response_path", "dimensions"):
                    if value.get(setting):
                        config[setting] = value[setting]
                config["dimensions"] = report.run("POST " + urlsplit(config["endpoint"]).path + " · gerçek deneme ve yanıt doğrulama", lambda: _embedding_probe(client, config, seen))

        if name == "embedding" and config.get("model_selection") == "server_default":
            config["max_batch"] = _batch_probe(client, report, config, seen.get("vector"))
        allowed = {"base_url", "endpoint", "model", "model_version", "model_selection", "max_batch", "contract", "request_template", "response_path", "dimensions", "ca_file"}
        settings = {k: v for k, v in config.items() if k in allowed}
        if models_endpoint_verified:
            settings["models_endpoint"] = models_endpoint
        message = ("LLM gerçek çağrısı ve JSON yanıt sözleşmesi doğrulandı." if name == "llm"
                   else "Embedding gerçek çağrısı doğrulandı; vektör boyutu yanıttan okundu.")
        if settings.get("model_selection") == "server_default":
            message += " Sunucunun varsayılan embedding modeli kullanılıyor; servis model kimliği ve sürümü bildirmiyor."
            if settings.get("max_batch", 1) > 1:
                message += " Toplu istek doğrulandı; istekler %d metinlik gruplarla gönderilecek." % settings["max_batch"]
            else:
                message += (" Sunucu toplu isteği doğrulatmadı; istekler tek metinlik gruplarla gönderilecek."
                            " Büyük katalogda indeks yavaş olur; servis ekibinden toplu istek desteğini veya model kimliğini isteyin.")
            if seen.get("model"):
                message += " Yanıtta geçen model adı: %s (model listesiyle doğrulanmadığı için kimlik olarak kullanılmadı)." % seen["model"]
        elif settings["model_version"].startswith("deployment:"):
            message += " Servis model sürümü bildirmedi; deployment etiketi yalnız dağıtım kimliğidir. Model değişirse yeniden doğrulayın."
        report.explain_optional_discovery()
        return {"ok": True, "checks": report.checks, "message": message, "settings": settings, "models": report.models,
                **({"dimensions": settings["dimensions"]} if name == "embedding" else {})}
    except Exception as error:
        failure = _safe_failure(error)
        if not report.checks or report.checks[-1].get("code") != failure.code:
            report.checks.append({"name": "Model bağlantısı doğrulama", "status": "failed", "elapsed_ms": 0,
                                  "message": failure.message, "code": failure.code})
        return {"ok": False, "checks": report.checks, "message": failure.message, "error": failure.message,
                "code": failure.code, "settings": {}, "models": report.models}
