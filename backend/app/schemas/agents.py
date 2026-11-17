from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AgentConfigOut(ORMModel):
    key: str
    name: str
    description: str
    role: str = ""
    allowed_tools: list[str] = []
    enabled: bool
    provider: str | None
    model: str | None
    effort: str | None
    max_output_tokens: int
    instructions: str
    max_retries: int
    timeout_s: int
    updated_at: datetime
    effective_provider: str = ""
    effective_model: str | None = None
    required: bool = False


class AgentConfigUpdate(BaseModel):
    enabled: bool | None = None
    provider: Literal["anthropic", "gemini"] | None = None
    model: str | None = Field(default=None, max_length=128)
    clear_model_override: bool = False
    effort: Effort | None = None
    clear_effort: bool = False
    max_output_tokens: int | None = Field(default=None, ge=256, le=64_000)
    instructions: str | None = Field(default=None, max_length=4000)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    timeout_s: int | None = Field(default=None, ge=10, le=900)


class ModelOut(BaseModel):
    id: str
    display_name: str
    provider: str
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None


class ProviderModels(BaseModel):
    provider: str
    configured: bool
    default_model: str | None
    default_model_status: str | None
    error: str | None
    models: list[ModelOut]
