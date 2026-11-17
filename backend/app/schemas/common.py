from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int | None
    username: str


class ProviderStatus(BaseModel):
    name: str
    configured: bool
    default_model: str | None


class SystemStatus(BaseModel):
    app_env: str
    auth_enabled: bool
    live_mode_available: bool
    default_llm_provider: str
    llm_providers: list[ProviderStatus]
    search_provider: ProviderStatus
    missing_for_live: list[str]
    version: str
