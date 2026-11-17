from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.api.deps import SessionDep, SettingsDep, UserDep, limiter
from app.schemas.common import LoginRequest, UserOut
from app.services.auth_service import COOKIE_NAME, authenticate, issue_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
@limiter.limit("10/minute")
async def login(request: Request, response: Response, body: LoginRequest,
                session: SessionDep, settings: SettingsDep) -> UserOut:
    user = await authenticate(session, body.username, body.password)
    response.set_cookie(
        COOKIE_NAME, issue_token(user, settings), httponly=True, samesite="lax",
        secure=settings.app_env == "production", max_age=settings.session_hours * 3600,
        path="/")
    return UserOut(id=user.id, username=user.username)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
async def me(user: UserDep) -> UserOut:
    return UserOut(id=user.id, username=user.username)
