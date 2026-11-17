"""Agent configuration persistence and effective-config resolution."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.specs import AGENT_ORDER, AGENT_SPECS
from app.config.settings import Settings
from app.models.agent_config import AgentConfig
from app.utils.errors import NotFoundError


async def seed_agent_configs(session: AsyncSession) -> None:
    existing = set((await session.scalars(select(AgentConfig.key))).all())
    for key in AGENT_ORDER:
        if key in existing:
            continue
        spec = AGENT_SPECS[key]
        session.add(AgentConfig(key=key, name=spec.name, description=spec.description,
                                enabled=True, max_output_tokens=spec.default_max_output_tokens,
                                effort=spec.default_effort))
    await session.commit()


async def list_agent_configs(session: AsyncSession) -> list[AgentConfig]:
    rows = (await session.scalars(select(AgentConfig))).all()
    return sorted(rows, key=lambda r: AGENT_ORDER.index(r.key) if r.key in AGENT_ORDER else 99)


async def get_agent_config(session: AsyncSession, key: str) -> AgentConfig:
    row = await session.scalar(select(AgentConfig).where(AgentConfig.key == key))
    if row is None:
        raise NotFoundError(f"Unknown agent '{key}'.")
    return row


@dataclass(frozen=True)
class EffectiveAgentConfig:
    """The fully-resolved settings an agent runs with (DB > env override > env default)."""

    key: str
    enabled: bool
    provider: str
    model: str | None
    effort: str | None
    max_output_tokens: int
    instructions: str
    max_retries: int
    timeout_s: int


def resolve_effective(row: AgentConfig, settings: Settings) -> EffectiveAgentConfig:
    import os

    provider, model = row.provider, row.model
    if not (provider and model):
        override = os.environ.get(f"AGENT_MODEL_{row.key.upper()}", "").strip()
        if override and ":" in override:
            o_provider, o_model = override.split(":", 1)
            provider = provider or o_provider.strip()
            model = model or o_model.strip()
    provider = provider or settings.default_llm_provider
    model = model or settings.default_model_for(provider)
    return EffectiveAgentConfig(
        key=row.key, enabled=row.enabled, provider=provider, model=model, effort=row.effort,
        max_output_tokens=row.max_output_tokens, instructions=row.instructions,
        max_retries=row.max_retries, timeout_s=row.timeout_s)
