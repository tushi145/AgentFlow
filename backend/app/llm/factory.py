"""Builds provider instances. The ONLY place that may import the demo package.

Live and demo are never mixed: a task's mode selects one complete provider set up front.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import Settings
from app.llm.base import LLMProvider
from app.tools.search import SearchProvider, TavilySearch
from app.utils.errors import ProviderNotConfiguredError


def build_llm_providers(settings: Settings) -> dict[str, LLMProvider]:
    """Real providers that have credentials configured."""
    providers: dict[str, LLMProvider] = {}
    if settings.anthropic_api_key is not None:
        from app.llm.anthropic_provider import AnthropicProvider

        providers["anthropic"] = AnthropicProvider(
            settings.anthropic_api_key.get_secret_value(),
            enable_fallbacks=settings.anthropic_enable_fallbacks,
            timeout_s=settings.llm_request_timeout_seconds)
    if settings.gemini_api_key is not None:
        from app.llm.gemini_provider import GeminiProvider

        providers["gemini"] = GeminiProvider(settings.gemini_api_key.get_secret_value(),
                                             timeout_s=settings.llm_request_timeout_seconds)
    return providers


def build_search_provider(settings: Settings) -> SearchProvider | None:
    if settings.tavily_api_key is None:
        return None
    return TavilySearch(settings.tavily_api_key.get_secret_value(),
                        credits_per_query=settings.search_credits_per_query)


@dataclass(frozen=True)
class ProviderSet:
    mode: str  # "live" | "demo"
    llms: dict[str, LLMProvider]
    search: SearchProvider

    def llm(self, provider: str) -> LLMProvider:
        if self.mode == "demo":
            return self.llms["demo"]
        if provider not in self.llms:
            raise ProviderNotConfiguredError(
                f"LLM provider '{provider}' is not configured. Set {provider.upper()}_API_KEY.")
        return self.llms[provider]


def provider_set_for_mode(mode: str, settings: Settings,
                          live_llms: dict[str, LLMProvider] | None = None,
                          live_search: SearchProvider | None = None) -> ProviderSet:
    if mode == "demo":
        from app.demo.providers import DemoLLMProvider, DemoSearchProvider

        return ProviderSet(mode="demo", llms={"demo": DemoLLMProvider()},
                           search=DemoSearchProvider())
    if mode != "live":
        raise ValueError(f"Unknown mode {mode!r}")
    llms = live_llms if live_llms is not None else build_llm_providers(settings)
    search = live_search if live_search is not None else build_search_provider(settings)
    missing = []
    if settings.default_llm_provider not in llms:
        missing.append(f"{settings.default_llm_provider.upper()}_API_KEY")
    if search is None:
        missing.append("TAVILY_API_KEY")
    if missing:
        raise ProviderNotConfiguredError(
            "Live execution needs real credentials: " + ", ".join(missing) +
            ". Add them to .env, or start the task in demo mode.",
            details={"missing": missing})
    return ProviderSet(mode="live", llms=llms, search=search)  # type: ignore[arg-type]
