"""Sözleşme uyumu (P12): frontend/src/api/endpoints.ts'deki her çağrının backend'de karşılığı var,
backend'in her /api ucu ön yüzde ya da bilinçli istisna listesinde."""

import re
from pathlib import Path

from app.main import create_app

ENDPOINTS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "endpoints.ts"
CALL_RE = re.compile(r"request(?:Text)?(?:<[^>]*>)?\(\s*'(GET|POST|DELETE|PUT|PATCH)'\s*,\s*([`'])(.+?)\2")
# Ön yüzün EventSource ile (api/sse.ts) açtığı uç; endpoints.ts'de değil.
BACKEND_ONLY = {("GET", "/api/transfers/{}/events")}


def _norm(path: str) -> str:
    """'/db/${role}/tables/${seg(s)}' ve '/db/{role}/tables/{schema}' → '/db/{}/tables/{}'."""
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    return re.sub(r"\{[^}]*\}", "{}", path)


def _frontend_calls() -> set[tuple[str, str]]:
    text = ENDPOINTS_TS.read_text(encoding="utf-8")
    calls = {(m, "/api" + _norm(p)) for m, _, p in CALL_RE.findall(text)}
    assert len(calls) >= 20, "endpoints.ts ayrıştırılamadı"
    return calls


def _backend_routes(settings) -> set[tuple[str, str]]:
    # Şema uç noktası kapalı (openapi_url=None) ama app.openapi() rota listesini yine üretir;
    # app.routes içe alınan router'ları tembel sakladığından doğrudan gezilemez.
    paths = create_app(settings).openapi()["paths"]
    out = {(m.upper(), _norm(p)) for p, ops in paths.items() for m in ops}
    assert len(out) >= 20, "backend rotaları okunamadı"
    return out


def test_every_frontend_call_exists_in_backend(settings):
    missing = _frontend_calls() - _backend_routes(settings)
    assert not missing, f"backend'de olmayan uçlar: {sorted(missing)}"


def test_every_backend_route_is_used_or_listed(settings):
    unused = _backend_routes(settings) - _frontend_calls() - BACKEND_ONLY
    assert not unused, f"ön yüzde kullanılmayan uçlar: {sorted(unused)}"


def test_sse_path_matches_frontend():
    sse = (ENDPOINTS_TS.parent / "sse.ts").read_text(encoding="utf-8")
    assert "/transfers/" in sse and "/events" in sse
