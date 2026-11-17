"""Deliberately simple single-user authentication (bcrypt + signed JWT cookie)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.models.user import User
from app.utils.errors import AuthError

COOKIE_NAME = "agentflow_session"
_ALGO = "HS256"
_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt()).decode()
    return _DUMMY_HASH


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


async def ensure_admin_user(session: AsyncSession, settings: Settings) -> User:
    """Create (or refresh the hash of) the single local user from environment settings."""
    if settings.admin_password_hash is not None:
        pw_hash = settings.admin_password_hash.get_secret_value()
    elif settings.admin_password is not None:
        if settings.app_env == "production":
            raise RuntimeError("Set ADMIN_PASSWORD_HASH in production (plain ADMIN_PASSWORD "
                               "is only accepted in development).")
        pw_hash = None
    else:
        raise RuntimeError("Set ADMIN_PASSWORD_HASH (or ADMIN_PASSWORD in development).")

    user = await session.scalar(select(User).where(User.username == settings.admin_username))
    if user is None:
        user = User(username=settings.admin_username,
                    password_hash=pw_hash or hash_password(
                        settings.admin_password.get_secret_value()))  # type: ignore[union-attr]
        session.add(user)
    elif pw_hash is not None and user.password_hash != pw_hash:
        user.password_hash = pw_hash
    elif pw_hash is None and not verify_password(
            settings.admin_password.get_secret_value(), user.password_hash):  # type: ignore[union-attr]
        user.password_hash = hash_password(settings.admin_password.get_secret_value())  # type: ignore[union-attr]
    await session.commit()
    return user


async def authenticate(session: AsyncSession, username: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.username == username))
    # Always run a bcrypt check to keep timing uniform for unknown users.
    ok = verify_password(password, user.password_hash if user else _dummy_hash())
    if not user or not ok:
        raise AuthError("Invalid username or password.")
    return user


def issue_token(user: User, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user.id), "usr": user.username, "iat": now,
               "exp": now + timedelta(hours=settings.session_hours)}
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=_ALGO)


def decode_token(token: str, settings: Settings) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=[_ALGO])
    except jwt.PyJWTError as exc:
        raise AuthError("Session expired or invalid.") from exc
