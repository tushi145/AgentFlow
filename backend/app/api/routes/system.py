from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.api.deps import SessionDep, SettingsDep, UserDep
from app.schemas.common import ProviderStatus, SystemStatus

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(session: SessionDep) -> dict:
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@router.get("/system/status", response_model=SystemStatus)
async def system_status(settings: SettingsDep, _: UserDep) -> SystemStatus:
    """Which integrations are configured. Never returns secret values."""
    llms = [ProviderStatus(name=p, configured=settings.provider_configured(p),
                           default_model=settings.default_model_for(p))
            for p in ("anthropic", "gemini")]
    missing: list[str] = []
    if not settings.provider_configured(settings.default_llm_provider):
        missing.append(f"{settings.default_llm_provider.upper()}_API_KEY")
    elif not settings.default_model_for(settings.default_llm_provider):
        missing.append(f"{settings.default_llm_provider.upper()}_DEFAULT_MODEL")
    if not settings.search_configured:
        missing.append("TAVILY_API_KEY")
    return SystemStatus(
        app_env=settings.app_env, auth_enabled=settings.auth_enabled,
        live_mode_available=not missing, default_llm_provider=settings.default_llm_provider,
        llm_providers=llms,
        search_provider=ProviderStatus(name="tavily", configured=settings.search_configured,
                                       default_model=None),
        missing_for_live=missing, version=__version__)
