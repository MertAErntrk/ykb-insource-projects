"""Değer çözme ucu (API.md POST /api/anon/reveal). Oracle'a bağlanmaz; değerler loglanmaz."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.anon.reveal import reveal_values
from app.deps import CurrentUser, SettingsDep
from app.transfer.service import get_repo, hmac_key

router = APIRouter(prefix="/api/anon", tags=["anon"])

_REVEAL_MAX_VALUES = 100
_REVEAL_MAX_LEN = 4000


class RevealBody(BaseModel):
    rule: Literal["num_token", "text_token", "date_token"]
    values: list[str] = Field(max_length=_REVEAL_MAX_VALUES)

    @field_validator("values")
    @classmethod
    def _clean(cls, v: list[str], info: ValidationInfo) -> list[str]:
        # Hata metnine değer konmaz (yanıtta yalnız alan yolu döner).
        # text_token boşlukları yerinde tutar ve uzunluk tweak'e girer: kırpılırsa sessizce yanlış çözülür.
        keep_spaces = info.data.get("rule") == "text_token"
        out = [x if keep_spaces else x.strip() for x in v if x.strip()]
        if not out:
            raise ValueError("en az bir değer gerekli")
        if any(len(x) > _REVEAL_MAX_LEN for x in out):
            raise ValueError("değer çok uzun")
        return out


@router.post("/reveal")
def reveal(body: RevealBody, request: Request, user: CurrentUser, settings: SettingsDep):
    """Standart kuralla anonimleştirilmiş değerleri gerçek değere çevirir (giriş yapmış her kullanıcı)."""
    # Denetim kaydı yazılamazsa çözme yapılmaz (hata yutulmaz); detayda değer yok.
    get_repo(request.app).audit(user.username, "reveal", detail=f"{body.rule}: {len(body.values)} değer")
    items = reveal_values(hmac_key(settings), body.rule, body.values)
    return {"rule": body.rule, "items": items}
