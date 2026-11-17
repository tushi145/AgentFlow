from __future__ import annotations

from fastapi import APIRouter, Request

from app.agents.specs import AGENT_SPECS, REQUIRED_AGENTS
from app.api.deps import SessionDep, SettingsDep, UserDep
from app.config.settings import Settings
from app.llm.base import LLMError
from app.llm.registry import ModelRegistry
from app.models.agent_config import AgentConfig
from app.schemas.agents import AgentConfigOut, AgentConfigUpdate, ModelOut, ProviderModels
from app.services.agent_config_service import (get_agent_config, list_agent_configs,
                                               resolve_effective)
from app.utils.errors import AppError, ModelValidationError

router = APIRouter(tags=["agents"])


def _registry(request: Request) -> ModelRegistry:
    return request.app.state.model_registry


def _out(row: AgentConfig, settings: Settings) -> AgentConfigOut:
    eff = resolve_effective(row, settings)
    spec = AGENT_SPECS.get(row.key)
    out = AgentConfigOut.model_validate(row)
    out.role = spec.role if spec else ""
    out.allowed_tools = sorted(spec.allowed_tools) if spec else []
    out.effective_provider, out.effective_model = eff.provider, eff.model
    out.required = row.key in REQUIRED_AGENTS
    return out


@router.get("/agents", response_model=list[AgentConfigOut])
async def list_agents(session: SessionDep, settings: SettingsDep, _: UserDep) -> list[AgentConfigOut]:
    return [_out(r, settings) for r in await list_agent_configs(session)]


@router.put("/agents/{key}", response_model=AgentConfigOut)
async def update_agent(key: str, body: AgentConfigUpdate, request: Request, session: SessionDep,
                       settings: SettingsDep, _: UserDep) -> AgentConfigOut:
    row = await get_agent_config(session, key)
    if body.enabled is False and key in REQUIRED_AGENTS:
        raise AppError(f"The {row.name} is required and cannot be disabled.", code="agent_required")

    if body.clear_model_override:
        row.provider, row.model = None, None
    elif body.model is not None or body.provider is not None:
        provider = body.provider or row.provider or settings.default_llm_provider
        model = (body.model or "").strip() or None
        check = await _registry(request).validate(provider, model)
        if check.status in ("invalid", "provider_not_configured"):
            raise ModelValidationError(check.message,
                                       details={"status": check.status,
                                                "suggestions": list(check.suggestions)})
        # "unverified" (provider unreachable) is accepted but reported by GET /models.
        row.provider, row.model = provider, model

    if body.clear_effort:
        row.effort = None
    elif body.effort is not None:
        row.effort = body.effort
    for field in ("enabled", "max_output_tokens", "instructions", "max_retries", "timeout_s"):
        val = getattr(body, field)
        if val is not None:
            setattr(row, field, val)
    await session.commit()
    await session.refresh(row)
    return _out(row, settings)


@router.get("/models", response_model=list[ProviderModels])
async def list_models(request: Request, settings: SettingsDep, _: UserDep,
                      refresh: bool = False) -> list[ProviderModels]:
    reg = _registry(request)
    out: list[ProviderModels] = []
    for provider in ("anthropic", "gemini"):
        configured = provider in reg.configured_providers
        default = settings.default_model_for(provider)
        models: list[ModelOut] = []
        error = None
        status = None
        if configured:
            try:
                models = [ModelOut(**m.__dict__) for m in await reg.models(provider, refresh=refresh)]
            except LLMError as exc:
                error = exc.code
            status = (await reg.validate(provider, default)).status if default else None
        out.append(ProviderModels(provider=provider, configured=configured, default_model=default,
                                  default_model_status=status, error=error, models=models))
    return out
