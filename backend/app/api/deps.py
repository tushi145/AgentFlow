"""FastAPI dependencies: settings, DB session, current user, rate limiter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings, get_settings
from app.database.session import session_factory
from app.services.auth_service import COOKIE_NAME, decode_token
from app.utils.errors import AuthError

limiter = Limiter(key_func=get_remote_address, default_limits=[])


def settings_dep(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


async def db_session() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


@dataclass(frozen=True)
class CurrentUser:
    id: int | None
    username: str


async def current_user(request: Request,
                       settings: Annotated[Settings, Depends(settings_dep)]) -> CurrentUser:
    if not settings.auth_enabled:
        return CurrentUser(id=None, username=settings.admin_username)
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    if not token:
        raise AuthError("Authentication required.")
    claims = decode_token(token, settings)
    return CurrentUser(id=int(claims["sub"]), username=claims.get("usr", ""))


SettingsDep = Annotated[Settings, Depends(settings_dep)]
SessionDep = Annotated[AsyncSession, Depends(db_session)]
UserDep = Annotated[CurrentUser, Depends(current_user)]
