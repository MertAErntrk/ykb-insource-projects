"""Dağıtım dosyaları: manifestler, Containerfile, .containerignore (ağ/kümeye gerek yok)."""

import re
from pathlib import Path

import pytest
import yaml

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
OS_DIR = ROOT / "deploy" / "openshift"


def _load(name: str) -> dict:
    docs = [d for d in yaml.safe_load_all((OS_DIR / name).read_text(encoding="utf-8")) if d]
    assert len(docs) == 1
    return docs[0]


def _containerfile() -> str:
    return (ROOT / "Containerfile").read_text(encoding="utf-8")


def test_all_yaml_parse():
    files = sorted(OS_DIR.glob("*.yaml"))
    assert len(files) >= 7
    for f in files:
        assert [d for d in yaml.safe_load_all(f.read_text(encoding="utf-8")) if d], f.name


def test_deployment_single_replica_recreate():
    spec = _load("deployment.yaml")["spec"]
    assert spec["replicas"] == 1
    assert spec["strategy"]["type"] == "Recreate"


def test_deployment_security_context():
    pod = _load("deployment.yaml")["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert "runAsUser" not in pod["securityContext"]
    assert "runAsUser" not in container["securityContext"]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"


def test_deployment_probes_and_mounts():
    pod = _load("deployment.yaml")["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert c["readinessProbe"]["httpGet"]["path"] == "/api/health"
    assert c["livenessProbe"]["httpGet"]["path"] == "/api/health"
    mounts = {m["mountPath"]: m for m in c["volumeMounts"]}
    assert "/data" in mounts
    vols = {v["name"]: v for v in pod["volumes"]}
    assert "persistentVolumeClaim" in vols[mounts["/data"]["name"]]
    for path, m in mounts.items():
        if path != "/data":
            assert m["readOnly"] is True
    sec_vol = next(v for v in pod["volumes"] if "secret" in v)
    assert sec_vol["secret"]["defaultMode"] == 0o440
    refs = [s["configMapRef"]["name"] for s in c["envFrom"] if "configMapRef" in s]
    assert refs == ["verikopru-config", "verikopru-site"]
    assert not any(s["configMapRef"].get("optional") for s in c["envFrom"] if "configMapRef" in s)
    secret_keys = {e["name"] for e in c["env"]}
    assert {"TT_SESSION_SECRET", "TT_ANON_HMAC_KEY"} <= secret_keys


def test_pvc_and_service():
    pvc = _load("pvc.yaml")["spec"]
    assert pvc["accessModes"] == ["ReadWriteOnce"]
    assert pvc["resources"]["requests"]["storage"] == "1Gi"
    svc = _load("service.yaml")["spec"]
    assert svc["type"] == "ClusterIP"
    assert svc["ports"][0]["port"] == 8080


def test_route_tls():
    tls = _load("route.yaml")["spec"]["tls"]
    assert tls["termination"] == "edge"
    assert tls["insecureEdgeTerminationPolicy"] == "Redirect"


def test_secret_example_only_placeholders():
    sec = _load("secret.example.yaml")
    values = {**(sec.get("stringData") or {}), **(sec.get("data") or {})}
    assert values
    assert all(v == "CHANGE_ME" for v in values.values())


def test_configmap_values():
    data = _load("configmap.yaml")["data"]
    assert data["FORWARDED_ALLOW_IPS"].strip() not in ("", "*")
    assert "*" not in data["FORWARDED_ALLOW_IPS"]
    # Canlı alias ortama özgüdür: depoda değil, oc ile yaratılan verikopru-site ConfigMap'inde.
    assert "TT_TARGET_DENY_ALIASES" not in data
    assert data["TT_RETENTION_DAYS"] == "7"
    assert data["TT_COOKIE_SECURE"] == "true"


def test_configmap_keys_are_settings_fields():
    data = _load("configmap.yaml")["data"]
    fields = set(Settings.model_fields)
    for key in data:
        if key.startswith(("TT_", "LDAP_")):
            assert key.lower() in fields, key


def test_kustomization_resources():
    res = _load("kustomization.yaml")["resources"]
    assert "secret.example.yaml" not in res
    for r in res:
        assert (OS_DIR / r).is_file(), r


def test_containerfile():
    text = _containerfile()
    user = re.search(r"^USER\s+(\S+)\s*$", text, re.M)
    assert user and user.group(1).isdigit() and int(user.group(1)) != 0
    cmd = re.search(r"^CMD\s+(.+)$", text, re.M)
    assert cmd
    flat = re.sub(r'[\[\]",]', " ", cmd.group(1))
    flat = re.sub(r"\s+", " ", flat)
    assert "--factory" in flat and "--proxy-headers" in flat and "--workers 1" in flat
    assert "ARG NODE_IMAGE" in text and "ARG PYTHON_IMAGE" in text
    assert "--frozen-lockfile" in text
    assert "TT_ENV=openshift" in text


def test_containerignore_excludes_secrets():
    lines = {
        ln.strip() for ln in (ROOT / ".containerignore").read_text(encoding="utf-8").splitlines()
    }
    for needed in (".env*", "config/service_accounts.toml", "config/tnsnames.ora"):
        assert needed in lines
    assert any(ln.startswith(".env") for ln in lines)


@pytest.mark.parametrize("name", ["configmap.yaml", "deployment.yaml", "secret.example.yaml"])
def test_no_obvious_real_secrets(name):
    text = (OS_DIR / name).read_text(encoding="utf-8")
    assert "BEGIN " not in text and "password" not in text.lower().replace("passwords", "")
