"""
llm.py — kurum içi OpenAI uyumlu LLM'e (vLLM, Qwen) ince istemci. BriefMind/llm.py'den sadeleştirildi:
düşünme kapalı, <think> temizliği, JSON şemayla yapılandırılmış cevap, her hatada None (çağıran
deterministik yola düşer). openai/httpx yalnızca bu sınıf kurulunca içe aktarılır; motor LLM'siz çalışır.

config.json: {"route": "https://<llm-adresi>/v1", "model": "Qwen3.8-27B-FP8", "ca_bundle": "", "zaman_asimi": 120}
"""
import json
import re
import ssl
from typing import Optional

_TAM_BLOK = re.compile(r"<think>.*?</think>\s*", re.S)
_ACIK_BLOK = re.compile(r"<think>.*$", re.S)
_KAPANIS = re.compile(r"^.*?</think>\s*", re.S)


def think_temizle(metin: str) -> str:
    """Tam blok, kesilmiş açık blok ve yalnızca kapanışı gelen blok (reasoning parser yokken) temizlenir."""
    metin = _TAM_BLOK.sub("", metin or "")
    metin = _ACIK_BLOK.sub("", metin)
    if "</think>" in metin:
        metin = _KAPANIS.sub("", metin)
    return metin.strip()


class LLM:
    def __init__(self, cfg: dict):
        import httpx
        from openai import OpenAI
        self.model = cfg.get("model") or "Qwen3.8-27B-FP8"
        dogrula = False
        if cfg.get("ca_bundle"):
            dogrula = ssl.create_default_context(cafile=cfg["ca_bundle"])
        self.client = OpenAI(base_url=cfg.get("route") or "http://localhost:8000/v1", api_key="x",
                             http_client=httpx.Client(verify=dogrula, trust_env=False,
                                                      timeout=cfg.get("zaman_asimi", 120)))

    def _iste(self, sistem: str, kullanici: str, max_tokens: int, response_format=None) -> Optional[str]:
        try:
            args = dict(model=self.model, temperature=0, max_tokens=max_tokens,
                        messages=[{"role": "system", "content": sistem}, {"role": "user", "content": kullanici}],
                        extra_body={"chat_template_kwargs": {"enable_thinking": False}})
            if response_format:
                args["response_format"] = response_format
            r = self.client.chat.completions.create(**args)
            secim = r.choices[0]
            if secim.finish_reason == "length":           # kesik cevap kabul edilmez
                return None
            return think_temizle(secim.message.content or "")
        except Exception:
            return None

    def metin(self, sistem: str, kullanici: str, max_tokens: int = 700) -> Optional[str]:
        return self._iste(sistem, kullanici, max_tokens)

    def json_cevap(self, sistem: str, kullanici: str, sema: dict, max_tokens: int = 400) -> Optional[dict]:
        bicim = {"type": "json_schema", "json_schema": {"name": "cevap", "schema": sema}}
        ham = self._iste(sistem, kullanici, max_tokens, bicim)
        if ham is None:                                      # şema desteklenmiyorsa düz istek
            ham = self._iste(sistem + "\nYalnızca JSON döndür.", kullanici, max_tokens)
        if not ham:
            return None
        try:
            return json.loads(ham)
        except ValueError:
            m = re.search(r"\{.*\}", ham, re.S)
            if not m:
                return None
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
