"""Anthropic Claude provider (official `anthropic` SDK, structured outputs via Pydantic)."""

from __future__ import annotations

import logging

import anthropic
from pydantic import ValidationError

from app.llm.base import (LLMError, LLMMessage, LLMOutputError, LLMRefusalError, LLMRequest,
                          LLMResult, ModelInfo, T, Usage)

log = logging.getLogger(__name__)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _usage(u: object | None) -> Usage:
    if u is None:
        return Usage()
    return Usage(input_tokens=getattr(u, "input_tokens", 0) or 0,
                 output_tokens=getattr(u, "output_tokens", 0) or 0,
                 cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0)


def map_anthropic_error(exc: Exception) -> LLMError:
    """Translate SDK exceptions (most specific first) into retryable / non-retryable errors."""
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError("Anthropic rejected the API key.", code="auth_failed")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return LLMError("The API key lacks permission for this request.", code="permission_denied")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError("Model or endpoint not found.", code="model_not_found")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("Rate limited by Anthropic.", code="rate_limited", retryable=True)
    if isinstance(exc, anthropic.BadRequestError):
        return LLMError(f"Request rejected: {getattr(exc, 'message', 'bad request')}"[:300],
                        code="bad_request")
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError("Anthropic request timed out.", code="timeout", retryable=True)
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("Could not reach Anthropic.", code="connection_error", retryable=True)
    if isinstance(exc, anthropic.APIStatusError):
        retry = exc.status_code >= 500 or exc.status_code in (408, 409, 529)
        return LLMError(f"Anthropic API error ({exc.status_code}).", code="api_error",
                        retryable=retry)
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
        if self._fallbacks:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            resp = await self._client.beta.messages.parse(**kwargs)
        except (ValidationError, ValueError) as exc:  # SDK-side parse failure
            raise LLMOutputError(f"Output did not match schema: {exc}"[:500]) from exc
        except anthropic.AnthropicError as exc:
            raise map_anthropic_error(exc) from exc

        usage = _usage(resp.usage)
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise LLMRefusalError("The model declined this request.",
                                  category=getattr(details, "category", None), usage=usage)
        if resp.stop_reason == "max_tokens":
            raise LLMOutputError("Output truncated at max_tokens.", code="truncated", usage=usage)
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            raw = "".join(getattr(b, "text", "") for b in resp.content if b.type == "text")
            raise LLMOutputError("Model returned no parseable structured output.", raw=raw[:2000],
                                 usage=usage)
        fallback = any(getattr(b, "type", "") == "fallback" for b in resp.content)
        return LLMResult(parsed=parsed, usage=usage, provider=self.name, model=resp.model,
                         stop_reason=resp.stop_reason, request_id=getattr(resp, "_request_id", None),
                         served_by_fallback=fallback)

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
