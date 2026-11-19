"""Provider-neutral LLM interface used by every agent.

Agents ask for a *validated Pydantic object*; providers are responsible for structured output,
usage reporting and translating SDK errors into the small error taxonomy below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMRequest(Generic[T]):
    model: str
    system: str
    messages: list[LLMMessage]
    output_model: type[T]
    max_output_tokens: int
    effort: str | None = None
    timeout_s: float = 180.0
    # Free-form context used only by the demo provider to build deterministic output.
    demo_context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.input_tokens + other.input_tokens,
                     self.output_tokens + other.output_tokens,
                     self.cache_read_tokens + other.cache_read_tokens)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    parsed: T
    usage: Usage
    provider: str
    model: str            # the model that actually served the request
    stop_reason: str | None
    request_id: str | None
    is_demo: bool = False
    served_by_fallback: bool = False


@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str
    provider: str
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None


class LLMError(Exception):
    """Base error. `retryable` drives the backoff policy; `code` is safe to surface."""

    def __init__(self, message: str, *, code: str = "llm_error", retryable: bool = False,
                 usage: Usage | None = None, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.usage = usage or Usage()
        # Server-requested wait (e.g. HTTP `retry-after`); honoured by the backoff policy.
        self.retry_after_s = retry_after_s


class LLMRefusalError(LLMError):
    def __init__(self, message: str, *, category: str | None = None,
                 usage: Usage | None = None) -> None:
        super().__init__(message, code="model_refusal", retryable=False, usage=usage)
        self.category = category


class LLMOutputError(LLMError):
    """Output was truncated or did not validate against the requested schema."""

    def __init__(self, message: str, *, code: str = "invalid_output", raw: str = "",
                 usage: Usage | None = None) -> None:
        super().__init__(message, code=code, retryable=False, usage=usage)
        self.raw = raw


class LLMProvider(Protocol):
    name: str
    is_demo: bool

    async def generate(self, request: LLMRequest[T]) -> LLMResult[T]: ...

    async def list_models(self) -> list[ModelInfo]: ...
