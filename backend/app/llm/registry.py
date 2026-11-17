"""Live model discovery and validation (no hard-coded model lists)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal

from app.llm.base import LLMError, LLMProvider, ModelInfo

log = logging.getLogger(__name__)

ValidationStatus = Literal["valid", "invalid", "unverified", "provider_not_configured"]


@dataclass(frozen=True)
class ModelCheck:
    provider: str
    model: str
    status: ValidationStatus
    message: str
    suggestions: tuple[str, ...] = ()


class ModelRegistry:
    """Caches each provider's `models.list()` for `ttl_s` seconds."""

    def __init__(self, providers: dict[str, LLMProvider], ttl_s: float = 3600.0) -> None:
        self._providers = providers
        self._ttl = ttl_s
        self._cache: dict[str, tuple[float, list[ModelInfo]]] = {}
        self._errors: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @property
    def configured_providers(self) -> list[str]:
        return sorted(self._providers)

    async def models(self, provider: str, *, refresh: bool = False) -> list[ModelInfo]:
        if provider not in self._providers:
            return []
        async with self._lock:
            hit = self._cache.get(provider)
            if hit and not refresh and time.monotonic() - hit[0] < self._ttl:
                return hit[1]
            try:
                models = await self._providers[provider].list_models()
            except LLMError as exc:
                self._errors[provider] = exc.code
                log.warning("Model listing failed for %s: %s", provider, exc.code)
                if hit:  # serve stale data rather than nothing
                    return hit[1]
                raise
            self._errors.pop(provider, None)
            self._cache[provider] = (time.monotonic(), models)
            return models

    def last_error(self, provider: str) -> str | None:
        return self._errors.get(provider)

    async def validate(self, provider: str, model: str | None) -> ModelCheck:
        if provider not in self._providers:
            return ModelCheck(provider, model or "", "provider_not_configured",
                              f"Provider '{provider}' has no API key configured.")
        if not model:
            return ModelCheck(provider, "", "invalid",
                              f"No model set. Set {provider.upper()}_DEFAULT_MODEL or choose one.")
        try:
            models = await self.models(provider)
        except LLMError as exc:
            return ModelCheck(provider, model, "unverified",
                              f"Could not reach {provider} to verify the model ({exc.code}).")
        ids = [m.id for m in models]
        if model in ids:
            return ModelCheck(provider, model, "valid", "Model is available.")
        close = tuple(i for i in ids if model.split("-")[0] in i)[:8] or tuple(ids[:8])
        return ModelCheck(provider, model, "invalid",
                          f"'{model}' is not in {provider}'s current model list.", close)
