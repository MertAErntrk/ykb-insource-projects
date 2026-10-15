from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, SecretStr

from app.auth.session import require_session
from app.deps import CurrentUser, SettingsDep
from app.errors import AppError, ErrorCode
from app.oracle import dictionary, pools, tns
from app.oracle.identifiers import is_valid_ident
from app.oracle.pools import DbHandle

router = APIRouter(prefix="/api", tags=["oracle"])

ROLES = ("source", "target")


class ConnectBody(BaseModel):
    role: str
    alias: str
    user: str
    password: SecretStr


def _check_role(role: str) -> str:
    if role not in ROLES:
        raise AppError(ErrorCode.VALIDATION, "Geçersiz bağlantı rolü.")
    return role


def _check_ident(value: str, what: str) -> str:
    if not is_valid_ident(value):
        raise AppError(ErrorCode.VALIDATION, f"Geçersiz {what} adı.")
    return value


def require_handle(request: Request, role: str) -> DbHandle:
    """Oturumdaki açık bağlantı; yok ya da kapalıysa DB_DISCONNECTED. P7b kullanır."""
    _check_role(role)
    session = require_session(request)
    handle = session.resources.get(role)
    if handle is None or handle.closed:
        raise AppError(ErrorCode.DB_DISCONNECTED)
    return handle


@router.get("/tns/aliases")
def tns_aliases(user: CurrentUser, settings: SettingsDep, q: str | None = None):
    return {"aliases": tns.list_aliases(settings, q)}


@router.post("/db/connect")
def db_connect(body: ConnectBody, request: Request, settings: SettingsDep):
    session = require_session(request)
    _check_role(body.role)
    handle = pools.open_db(settings, role=body.role, alias=body.alias, user=body.user,
                           password=body.password, driver=pools.get_driver())
    previous = session.resources.get(body.role)
    session.resources[body.role] = handle
    if previous is not None:
        previous.close()
    repo = getattr(request.app.state, "repository", None)
    if repo is not None:
        repo.audit(session.user.username, "connect", detail=f"{body.role}:{handle.alias}")
    return handle.info()


@router.delete("/db/connect/{role}", status_code=204)
def db_disconnect(role: str, request: Request):
    session = require_session(request)
    _check_role(role)
    handle = session.resources.pop(role, None)
    if handle is not None:
        handle.close()
    return Response(status_code=204)


@router.get("/db/status")
def db_status(request: Request):
    session = require_session(request)
    out = {}
    for role in ROLES:
        h = session.resources.get(role)
        out[role] = h.info() if h is not None and not h.closed else None
    return out


@router.get("/db/{role}/schemas")
def db_schemas(role: str, request: Request, q: str | None = None):
    handle = require_handle(request, role)
    with handle.connection() as conn:
        return {"schemas": dictionary.list_schemas(conn, q)}


@router.get("/db/{role}/tables")
def db_tables(role: str, request: Request, schema: str, q: str | None = None,
              limit: Annotated[int, Query(ge=1, le=200)] = 50,
              offset: Annotated[int, Query(ge=0)] = 0):
    handle = require_handle(request, role)
    _check_ident(schema, "şema")
    with handle.connection() as conn:
        return dictionary.list_tables(conn, schema, q, limit, offset)


@router.get("/db/{role}/tables/{schema}/{table}/columns")
def db_columns(role: str, schema: str, table: str, request: Request):
    handle = require_handle(request, role)
    _check_ident(schema, "şema")
    _check_ident(table, "tablo")
    with handle.connection() as conn:
        cols, extra = dictionary.read_columns(conn, schema, table)
    return {"columns": dictionary.columns_to_api(cols, extra)}
