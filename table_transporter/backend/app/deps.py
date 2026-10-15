from typing import Annotated

from fastapi import Depends

from app.auth.session import User, require_user
from app.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]
CurrentUser = Annotated[User, Depends(require_user)]
