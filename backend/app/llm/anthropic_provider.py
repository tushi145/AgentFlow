"""Anthropic Claude provider (official `anthropic` SDK, structured outputs via Pydantic)."""

from __future__ import annotations

import logging

import anthropic
from pydantic import ValidationError

from app.llm.base import (LLMError, LLMMessage, LLMOutputError, LLMRefusalError, LLMRequest,
                          LLMResult, ModelInfo, T, Usage)

log = logging.getLogger(__name__)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Server-side refusal fallbacks (`fallbacks: "default"`) exist only for these model families on the
# Claude API; Haiku and older models have none, so the parameter is not sent for them.
_FALLBACK_MODEL_PREFIXES = ("claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-sonnet-5-5")


def supports_server_fallback(model: str) -> bool:
    return model.startswith(_FALLBACK_MODEL_PREFIXES)


def _retry_after(exc: Exception) -> float | None:
    """Seconds from a `retry-after` header (numeric form only; HTTP-date forms are ignored)."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after")
    try:
        val = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    return val if val is not None and val >= 0 else None


def _usage_from_dict(u: dict) -> Usage:
    return Usage(input_tokens=int(u.get("input_tokens") or 0),
                 output_tokens=int(u.get("output_tokens") or 0),
                 cache_read_tokens=int(u.get("cache_read_input_tokens") or 0))


def map_anthropic_error(exc: Exception) -> LLMError:
    """Translate SDK exceptions (most specific first) into retryable / non-retryable errors."""
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError("Anthropic rejected the API key.", code="auth_failed")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return LLMError("The API key lacks permission for this request.", code="permission_denied")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError("Model or endpoint not found.", code="model_not_found")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("Rate limited by Anthropic.", code="rate_limited", retryable=True,
                        retry_after_s=_retry_after(exc))
    if isinstance(exc, anthropic.BadRequestError):
        return LLMError(f"Request rejected: {getattr(exc, 'message', 'bad request')}"[:300],
                        code="bad_request")
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError("Anthropic request timed out.", code="timeout", retryable=True)
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("Could not reach Anthropic.", code="connection_error", retryable=True)
    if isinstance(exc, anthropic.APIStatusError):
        retry = exc.status_code >= 500 or exc.status_code in (408, 409, 529)
        code = "overloaded" if exc.status_code == 529 else "api_error"
        return LLMError(f"Anthropic API error ({exc.status_code}).", code=code,
                        retryable=retry, retry_after_s=_retry_after(exc))
    return LLMError(f"Unexpected Anthropic error: {type(exc).__name__}", code="unexpected")


class AnthropicProvider:
    name = "anthropic"
    is_demo = False

    def __init__(self, api_key: str, *, enable_fallbacks: bool = True,
                 timeout_s: float = 180.0, client: anthropic.AsyncAnthropic | None = None) -> None:
        # SDK retries disabled: our own tenacity policy (app.llm.retry) is the single,
        # observable retry layer, and it emits retry events to the UI.
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=0,
                                                          timeout=timeout_s)
        self._fallbacks = enable_fallbacks

    async def generate(self, request: LLMRequest[T]) -> LLMResult[T]:
        kwargs: dict = dict(
            model=request.model,
            max_tokens=request.max_output_tokens,
            system=request.system,
            messages=[_msg(m) for m in request.messages],
            output_format=request.output_model,
            timeout=request.timeout_s,
        )
        if request.effort:
            kwargs["output_config"] = {"effort": request.effort}
        if self._fallbacks and supports_server_fallback(request.model):
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        # Read the raw response first: stop_reason and usage must be known *before* parsing, so
        # that (a) a max_tokens cut-off is reported as truncation rather than invalid JSON and
        # (b) tokens spent on an output that fails validation are still charged to the budget.
        try:
            raw = await self._client.beta.messages.with_raw_response.parse(**kwargs)
            data = await raw.json()
        except anthropic.AnthropicError as exc:
            raise map_anthropic_error(exc) from exc
        usage = _usage_from_dict(data.get("usage") or {})
        stop = data.get("stop_reason")
        blocks = data.get("content") or []
        if stop == "refusal":
            details = data.get("stop_details") or {}
            raise LLMRefusalError("The model declined this request.",
                                  category=details.get("category"), usage=usage)
        if stop == "max_tokens":
            raise LLMOutputError("Output truncated at max_tokens.", code="truncated", usage=usage)
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        try:
            resp = await raw.parse()
        except (ValidationError, ValueError) as exc:  # output did not satisfy the schema
            raise LLMOutputError(f"Output did not match schema: {exc}"[:500], raw=text[:2000],
                                 usage=usage) from exc
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            raise LLMOutputError("Model returned no parseable structured output.", raw=text[:2000],
                                 usage=usage)
        fallback = any(b.get("type") == "fallback" for b in blocks)
        return LLMResult(parsed=parsed, usage=usage, provider=self.name,
                         model=data.get("model") or request.model, stop_reason=stop,
                         request_id=raw.request_id or data.get("id"), served_by_fallback=fallback)

    async def list_models(self) -> list[ModelInfo]:
        try:
            out: list[ModelInfo] = []
            async for m in self._client.models.list(limit=100):
                out.append(ModelInfo(id=m.id, display_name=getattr(m, "display_name", m.id),
                                     provider=self.name,
                                     max_input_tokens=getattr(m, "max_input_tokens", None),
                                     max_output_tokens=getattr(m, "max_tokens", None)))
            return out
        except anthropic.AnthropicError as exc:
            raise map_anthropic_error(exc) from exc


def _msg(m: LLMMessage) -> dict:
    return {"role": m.role, "content": m.content}
