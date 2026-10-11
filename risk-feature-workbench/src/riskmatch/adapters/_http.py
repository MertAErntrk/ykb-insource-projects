"""Small bounded JSON transport; no implicit endpoint discovery or redirects."""

import json
import os
import time
import ssl
import urllib.error
import urllib.parse
import urllib.request

from riskmatch.errors import ConfigError, IntegrationError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def validate_endpoint(config):
    endpoint = config.get("endpoint")
    hosts = config.get("allowed_hosts")
    if not isinstance(endpoint, str) or not endpoint:
        raise ConfigError("Tam servis endpoint adresi gerekli.")
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ConfigError("Servis endpoint adresi HTTPS ve kullanıcı bilgisi içermeyen tam URL olmalı.")
    if not isinstance(hosts, list) or not hosts or parsed.hostname.lower() not in {
        str(host).lower() for host in hosts
    }:
        raise ConfigError("Servis hostu açık allowed_hosts listesinde olmalı.")
    return endpoint


def _bounded_int(value, default, low, high, label):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{label} {low}–{high} aralığında tam sayı olmalı.")
    return value


def _bounded_timeout(config):
    value = config.get("timeout_seconds", 15)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 60:
        raise ConfigError("timeout_seconds 0–60 aralığında olmalı.")
    return float(value)


def post_json(config, payload, *, transport=None, api_key=None):
    endpoint = validate_endpoint(config)
    timeout = _bounded_timeout(config)
    retries = _bounded_int(config.get("max_retries"), 1, 0, 2, "max_retries")
    key_name = config.get("api_key_env")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key is not None:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    elif key_name:
        if not isinstance(key_name, str) or not key_name:
            raise ConfigError("api_key_env ortam değişkeni adı olmalı.")
        secret = os.environ.get(key_name)
        if not secret:
            raise ConfigError("API anahtarı için belirtilen ortam değişkeni boş.")
        headers["Authorization"] = f"Bearer {secret}"
    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigError("Servis isteği JSON biçimine çevrilemedi.") from exc
    if len(body) > 512_000:
        raise ConfigError("Servis isteği boyut sınırını aşıyor.")
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        context=ssl.create_default_context(cafile=config.get("ca_file") or None)
    except (OSError,ssl.SSLError):
        raise ConfigError("Kurum CA dosyası okunamadı; PEM dosyasının yolunu kontrol edin.") from None
    opener = transport or urllib.request.build_opener(_NoRedirect(),urllib.request.HTTPSHandler(context=context))
    for attempt in range(retries + 1):
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise IntegrationError("Servis yanıtı boyut sınırını aşıyor.")
            try:
                return json.loads(raw)
            except (UnicodeDecodeError, ValueError) as exc:
                raise IntegrationError("Servis geçerli JSON döndürmedi.") from exc
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise IntegrationError(f"Servis HTTP {exc.code} hatası verdi.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < retries:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise IntegrationError("Servis erişimi zaman aşımına uğradı veya başarısız oldu.") from None
    raise IntegrationError("Servis erişimi başarısız oldu.")


def render_template(template, values):
    """Only whole-field placeholders, preserving JSON types."""
    if isinstance(template, str):
        if template.startswith("{") and template.endswith("}") and template[1:-1] in values:
            return values[template[1:-1]]
        if "{" in template or "}" in template:
            raise ConfigError("Şablonda yalnız tam alan yer tutucuları desteklenir.")
        return template
    if isinstance(template, list):
        return [render_template(item, values) for item in template]
    if isinstance(template, dict):
        return {key: render_template(value, values) for key, value in template.items()}
    return template


def response_path(value, path):
    """Explicit dotted JSON path, with [*] for one or more arrays."""
    if not isinstance(path, str) or not path:
        raise ConfigError("response_path gerekli.")
    if path == "$":
        return value
    values = [value]
    try:
        for part in path.split("."):
            wildcard = part.endswith("[*]")
            key = part[:-3] if wildcard else part
            if not key:
                raise KeyError
            next_values = []
            for node in values:
                member = node[key] if isinstance(node, dict) else node[int(key)]
                if wildcard:
                    if not isinstance(member, list):
                        raise TypeError
                    next_values.extend(member)
                else:
                    next_values.append(member)
            values = next_values
        return values if "[*]" in path else values[0]
    except (KeyError, IndexError, ValueError, TypeError):
        raise IntegrationError("Servis yanıtı yapılandırılmış response_path ile uyuşmuyor.") from None
