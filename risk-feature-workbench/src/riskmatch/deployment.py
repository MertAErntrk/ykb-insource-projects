"""Institution service addresses. User passwords and API keys never belong here."""

ORACLE_DSN = (
    "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)"
    "(HOST=edwlive.sys.yapikredi.com.tr)(PORT=1453))"
    "(CONNECT_DATA=(SERVER=dedicated)(SERVICE_NAME=svc_edw_dwh)))"
)
SOURCE_DATABASE = "EDWLIVE"
ENVIRONMENT = "LIVE"
LLM_BASE_URL = "https://qwen38-27b-dev-bigdataeng.apps.lan-dev-dc1-ocp.yapikredi.com.tr/v1"
EMBEDDING_BASE_URL = "https://qwen-embedding-dsp-bigdataeng.apps.lan-dev-dc1-ocp.yapikredi.com.tr"


def login_settings(user, password):
    return {"mode": "real", "use_models": False, "oracle": {
        "dsn": ORACLE_DSN, "source_database": SOURCE_DATABASE,
        "environment": ENVIRONMENT, "user": user, "password": password,
    }}
