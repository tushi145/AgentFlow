from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from app.llm.anthropic_provider import AnthropicProvider, map_anthropic_error
from app.llm.base import (LLMError, LLMMessage, LLMOutputError, LLMRefusalError, LLMRequest)
from app.llm.registry import ModelRegistry
from app.llm.retry import with_backoff
from tests.fakes import FakeLLM


class Out(BaseModel):
    answer: str


def _req(**kw: object) -> LLMRequest:
    base = dict(model="m", system="sys", messages=[LLMMessage("user", "hi")], output_model=Out,
                max_output_tokens=100, effort="high")
    base.update(kw)
    return LLMRequest(**base)  # type: ignore[arg-type]


class _FakeMessages:
    def __init__(self, response=None, exc=None):  # type: ignore[no-untyped-def]
        self.response, self.exc, self.kwargs = response, exc, None

    async def parse(self, **kwargs):  # type: ignore[no-untyped-def]
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return self.response


def _client(messages: _FakeMessages) -> SimpleNamespace:
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def _resp(stop: str = "end_turn", parsed: BaseModel | None = Out(answer="42"), content=None):  # type: ignore[no-untyped-def]
    usage = SimpleNamespace(input_tokens=120, output_tokens=30, cache_read_input_tokens=5)
    return SimpleNamespace(stop_reason=stop, parsed_output=parsed, usage=usage, model="m-served",
                           content=content or [SimpleNamespace(type="text", text="{}")],
                           stop_details=SimpleNamespace(category="cyber"), _request_id="req_1")


async def test_anthropic_success_maps_usage_and_request_shape() -> None:
    msgs = _FakeMessages(_resp())
    p = AnthropicProvider("k", client=_client(msgs), enable_fallbacks=True)  # type: ignore[arg-type]
    r = await p.generate(_req())
    assert r.parsed == Out(answer="42") and r.model == "m-served"
    assert (r.usage.input_tokens, r.usage.output_tokens, r.usage.cache_read_tokens) == (120, 30, 5)
    # Untrusted/user content goes in messages; system stays separate; effort via output_config.
    assert msgs.kwargs["system"] == "sys"
    assert msgs.kwargs["output_config"] == {"effort": "high"}
    assert msgs.kwargs["output_format"] is Out
    assert msgs.kwargs["fallbacks"] == "default"


async def test_anthropic_without_effort_or_fallbacks() -> None:
    msgs = _FakeMessages(_resp())
    p = AnthropicProvider("k", client=_client(msgs), enable_fallbacks=False)  # type: ignore[arg-type]
    await p.generate(_req(effort=None))
    assert "output_config" not in msgs.kwargs and "fallbacks" not in msgs.kwargs


@pytest.mark.parametrize("stop,exc_type", [("refusal", LLMRefusalError), ("max_tokens", LLMOutputError)])
async def test_anthropic_stop_reasons(stop: str, exc_type: type) -> None:
    p = AnthropicProvider("k", client=_client(_FakeMessages(_resp(stop=stop))))  # type: ignore[arg-type]
    with pytest.raises(exc_type) as ei:
        await p.generate(_req())
    assert ei.value.usage.input_tokens == 120  # usage is still accounted on failure


async def test_anthropic_unparseable_output() -> None:
    p = AnthropicProvider("k", client=_client(_FakeMessages(_resp(parsed=None))))  # type: ignore[arg-type]
    with pytest.raises(LLMOutputError):
        await p.generate(_req())


def _status_error(cls: type, code: int) -> Exception:
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(code, request=req), body=None)


@pytest.mark.parametrize("cls,code,retryable,expected", [
    (anthropic.RateLimitError, 429, True, "rate_limited"),
    (anthropic.AuthenticationError, 401, False, "auth_failed"),
    (anthropic.NotFoundError, 404, False, "model_not_found"),
    (anthropic.InternalServerError, 500, True, "api_error"),
    (anthropic.BadRequestError, 400, False, "bad_request"),
])
def test_error_mapping(cls: type, code: int, retryable: bool, expected: str) -> None:
    err = map_anthropic_error(_status_error(cls, code))
    assert err.code == expected and err.retryable is retryable


async def test_backoff_retries_only_retryable_errors() -> None:
    attempts, events = [], []

    async def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise LLMError("rl", code="rate_limited", retryable=True)
        return "ok"

    async def on_retry(n: int, e: BaseException, s: float) -> None:
        events.append(n)

    assert await with_backoff(flaky, max_attempts=4, on_retry=on_retry, base=0.001, cap=0.01) == "ok"
    assert len(attempts) == 3 and events == [1, 2]

    async def fatal() -> str:
        attempts.append(1)
        raise LLMError("bad", code="bad_request", retryable=False)

    attempts.clear()
    with pytest.raises(LLMError):
        await with_backoff(fatal, max_attempts=4, base=0.001)
    assert len(attempts) == 1


async def test_backoff_gives_up_after_max_attempts() -> None:
    async def always() -> None:
        raise LLMError("rl", code="rate_limited", retryable=True)

    with pytest.raises(LLMError):
        await with_backoff(always, max_attempts=2, base=0.001, cap=0.01)


async def test_model_registry_validation_and_cache() -> None:
    fake = FakeLLM(models=["claude-x-1", "claude-y-2"])
    reg = ModelRegistry({"anthropic": fake})
    assert (await reg.validate("anthropic", "claude-x-1")).status == "valid"
    bad = await reg.validate("anthropic", "claude-nope")
    assert bad.status == "invalid" and "claude-x-1" in bad.suggestions
    assert (await reg.validate("gemini", "g")).status == "provider_not_configured"
    assert (await reg.validate("anthropic", None)).status == "invalid"
    assert fake.list_calls == 1  # cached


async def test_model_registry_unreachable_is_unverified_not_valid() -> None:
    reg = ModelRegistry({"anthropic": FakeLLM(models=None)})
    check = await reg.validate("anthropic", "anything")
    assert check.status == "unverified"
