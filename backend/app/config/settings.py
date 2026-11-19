"""Application settings loaded from environment variables / `.env`.

No model identifiers or secrets are hard-coded here: defaults for models come from the
environment and are validated against the provider's live model list at runtime.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

ProviderName = Literal["anthropic", "gemini"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"
    rate_limit_enabled: bool = True
    jwt_secret: SecretStr = SecretStr("dev-insecure-secret-change-me-before-deploying-0000")
    session_hours: int = Field(12, ge=1, le=24 * 30)

    # Auth
    auth_enabled: bool = True
    admin_username: str = "admin"
    admin_password_hash: SecretStr | None = None
    admin_password: SecretStr | None = SecretStr("admin")

    # Database / storage
    database_url: str = "sqlite+aiosqlite:///./data/agentflow.db"
    data_dir: Path = Path("./data")

    # LLM providers
    default_llm_provider: ProviderName = "anthropic"
    anthropic_api_key: SecretStr | None = None
    anthropic_default_model: str | None = None
    anthropic_enable_fallbacks: bool = True
    gemini_api_key: SecretStr | None = None
    gemini_default_model: str | None = None
    llm_request_timeout_seconds: float = 180.0

    # Search
    tavily_api_key: SecretStr | None = None
    search_credits_per_query: float = 1.0

    # Execution limits
    default_max_total_tokens: int = 400_000
    default_max_llm_calls: int = 40
    default_max_tool_calls: int = 60
    default_timeout_seconds: int = 900
    default_max_cost_usd: float | None = None
    default_max_iterations: int = Field(2, ge=1, le=5)
    max_total_tokens_ceiling: int = 2_000_000
    max_concurrent_tasks: int = Field(2, ge=1, le=16)
    max_parallel_searches: int = Field(4, ge=1, le=16)  # per task, across research branches

    # Pricing / approvals
    pricing_file: Path | None = None
    approval_cost_threshold_usd: float | None = 2.0
    approval_timeout_hours: int = 24

    @field_validator(
        "anthropic_api_key", "gemini_api_key", "tavily_api_key", "admin_password_hash",
        "anthropic_default_model", "gemini_default_model", "pricing_file",
        "default_max_cost_usd", "approval_cost_threshold_usd",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    # ---- derived helpers -------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def resolved_data_dir(self) -> Path:
        p = self.data_dir if self.data_dir.is_absolute() else (BACKEND_DIR / self.data_dir)
        return p.resolve()

    @property
    def reports_dir(self) -> Path:
        return self.resolved_data_dir / "reports"

    @property
    def resolved_database_url(self) -> str:
        """Make relative SQLite paths relative to the backend dir, not the CWD."""
        prefix = "sqlite+aiosqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix):]
            if raw and raw != ":memory:" and not Path(raw).is_absolute():
                return prefix + (BACKEND_DIR / raw).resolve().as_posix()
        return self.database_url

    def provider_configured(self, provider: str) -> bool:
        if provider == "anthropic":
            return self.anthropic_api_key is not None
        if provider == "gemini":
            return self.gemini_api_key is not None
        return False

    def default_model_for(self, provider: str) -> str | None:
        return {"anthropic": self.anthropic_default_model,
                "gemini": self.gemini_default_model}.get(provider)

    @property
    def search_configured(self) -> bool:
        return self.tavily_api_key is not None

    @property
    def live_mode_available(self) -> bool:
        return self.provider_configured(self.default_llm_provider) and self.search_configured


@lru_cache
def get_settings() -> Settings:
    return Settings()
