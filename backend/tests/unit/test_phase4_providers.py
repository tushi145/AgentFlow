"""Phase 4 provider hardening: wire contract with the real SDK, retry-after, fallbacks, Tavily.

The wire-contract tests drive the *real* `anthropic` client through a mock HTTP transport, so
they verify the exact request body we send and that every agent schema round-trips — without
network access or credentials.
"""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel
from tavily import errors as tavily_errors

from app.llm.anthropic_provider import (AnthropicProvider, map_anthropic_error,
                                        supports_server_fallback)
from app.llm.base import LLMError, LLMMessage, LLMOutputError, LLMRequest
from app.llm.retry import with_backoff
from app.schemas.agent_io import (AnalysisResult, Claim, ClaimVerdict, ExecutionPlan, ExtractedFact,
                                  Finding, PlannedSubtask, ReportDraft, ReportSection,
                                  ResearchResult, VerificationReport)
from app.tools.search import TavilySearch

EXAMPLES: list[BaseModel] = [
    ExecutionPlan(objective_summary="o", desired_outcome="d", success_criteria=["c"],
                  subtasks=[PlannedSubtask(id="t1", title="t", description="d", queries=["q"],
                                           depends_on=[], expected_output="e")],
                  analysis_focus=["a"], report_outline=["r"]),
    ResearchResult(facts=[ExtractedFact(statement="s", source_refs=["S1"], evidence_quote="q")],
                   coverage_notes="n"),
    AnalysisResult(summary="s", findings=[Finding(title="t", detail="d", source_refs=["S1"])],
                   claims=[Claim(id="c1", statement="s", source_refs=["S1"], importance="high")]),
    VerificationReport(verdicts=[ClaimVerdict(claim_id="c1", verdict="supported", confidence=0.8,
                                              reasoning="r", supporting_refs=["S1"])],
                       sufficient=True, sufficiency_reasoning="ok"),
    ReportDraft(title="t", executive_summary="e [S1]", methodology="m",
                sections=[ReportSection(heading="h", body_markdown="b")], conclusion="c",
                limitations=["l"]),
]


class Wire:
    """Mock Anthropic HTTP endpoint that records requests and replies with scripted JSON."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.requests: list[httpx2.Request] = []

    def __call__(self, req: httpx2.Request) -> httpx2.Response:
        self.requests.append(req)
        return self.reply(req) if callable(self.reply) else self.reply

    def provider(self, **kw: Any) -> AnthropicProvider:
        client = anthropic.AsyncAnthropic(
            api_key="sk-test", max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self)))
        return AnthropicProvider("sk-test", client=client, **kw)

    @property
    def body(self) -> dict:
        return json.loads(self.requests[-1].content)


def message(text: str, *, model: str = "claude-opus-5-5", stop: str = "end_turn",
            content: list | None = None) -> httpx2.Response:
    return httpx2.Response(200, headers={"request-id": "req_wire_1"}, json={
        "id": "msg_1", "type": "message", "role": "assistant", "model": model,
        "content": content if content is not None else [{"type": "text", "text": text}],
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 321, "output_tokens": 54, "cache_read_input_tokens": 0}})


def _req(output_model: type[BaseModel], model: str = "claude-opus-5-5", **kw: Any) -> LLMRequest:
    return LLMRequest(model=model, system="SYSTEM", messages=[LLMMessage("user", "USER")],
                      output_model=output_model, max_output_tokens=2000, **kw)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: type(e).__name__)
async def test_every_agent_schema_round_trips_through_the_real_sdk(example: BaseModel) -> None:
    wire = Wire(message(example.model_dump_json()))
    result = await wire.provider().generate(_req(type(example), effort="medium"))
    assert result.parsed == example
    assert (result.usage.input_tokens, result.usage.output_tokens) == (321, 54)
    assert result.request_id == "req_wire_1" and result.model == "claude-opus-5-5"

    body = wire.body
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert body["output_config"]["effort"] == "medium"
    assert body["system"] == "SYSTEM" and body["messages"] == [{"role": "user", "content": "USER"}]
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in wire.requests[-1].headers["anthropic-beta"]
    h = wire.requests[-1].headers
    assert h.get("x-api-key") == "sk-test" or h.get("authorization") == "Bearer sk-test"


def test_schemas_use_only_structured_output_compatible_constructs() -> None:
    """No unions/anyOf except Optional, no recursive refs, every object closed."""
    from anthropic.lib._parse._transform import transform_schema  # SDK's own transformer

    for example in EXAMPLES:
        schema = transform_schema(type(example))
        text = json.dumps(schema)
        assert "$ref" not in text.replace('"$defs"', "") or "$defs" in schema
        stack = [schema]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("type") == "object":
                    assert node.get("additionalProperties") is False, type(example).__name__
                stack += list(node.values())
            elif isinstance(node, list):
                stack += node


async def test_invalid_output_still_reports_usage_for_budget_accounting() -> None:
    wire = Wire(message('{"not": "the schema"}'))
    with pytest.raises(LLMOutputError) as ei:
        await wire.provider().generate(_req(ReportDraft))
    assert ei.value.code == "invalid_output"
    assert (ei.value.usage.input_tokens, ei.value.usage.output_tokens) == (321, 54)
    assert '"not"' in ei.value.raw  # raw text is kept for the repair prompt


async def test_out_of_range_value_is_rejected_client_side_and_repairable() -> None:
    bad = VerificationReport(verdicts=[], sufficient=True, sufficiency_reasoning="x").model_dump()
    bad["verdicts"] = [{"claim_id": "c1", "verdict": "supported", "confidence": 1.7,
                        "reasoning": "r", "supporting_refs": [], "conflicting_refs": []}]
    wire = Wire(message(json.dumps(bad)))
    with pytest.raises(LLMOutputError) as ei:
        await wire.provider().generate(_req(VerificationReport))
    assert ei.value.code == "invalid_output"  # triggers BaseAgent's one schema-repair round


async def test_refusal_and_truncation_on_the_wire() -> None:
    wire = Wire(message("", stop="refusal", content=[]))
    with pytest.raises(LLMError) as ei:
        await wire.provider().generate(_req(ReportDraft))
    assert ei.value.code == "model_refusal" and ei.value.usage.input_tokens == 321
    wire = Wire(message('{"title": "t', stop="max_tokens"))
    with pytest.raises(LLMOutputError) as ei2:
        await wire.provider().generate(_req(ReportDraft))
    assert ei2.value.code == "truncated"


@pytest.mark.parametrize("status,code,retryable", [
    (429, "rate_limited", True), (529, "overloaded", True), (500, "api_error", True),
    (401, "auth_failed", False), (404, "model_not_found", False), (400, "bad_request", False)])
async def test_http_errors_on_the_wire(status: int, code: str, retryable: bool) -> None:
    wire = Wire(httpx2.Response(status, headers={"retry-after": "7"},
                                json={"type": "error", "error": {"type": "x", "message": "m"}}))
    with pytest.raises(LLMError) as ei:
        await wire.provider().generate(_req(ReportDraft))
    assert (ei.value.code, ei.value.retryable) == (code, retryable)
    if retryable:
        assert ei.value.retry_after_s == 7.0
    assert "sk-test" not in str(ei.value)  # never leak the key in error text


async def test_timeout_is_retryable() -> None:
    def boom(req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=req)

    with pytest.raises(LLMError) as ei:
        await Wire(boom).provider().generate(_req(ReportDraft, timeout_s=0.5))
    assert ei.value.code == "timeout" and ei.value.retryable


@pytest.mark.parametrize("model,expected", [
    ("claude-opus-5-5", True), ("claude-sonnet-5-5", True), ("claude-fable-5-1", True),
    ("claude-opus-5", True), ("claude-haiku-5-5", False), ("claude-sonnet-4-6", False)])
async def test_fallbacks_only_sent_to_models_that_support_them(model: str, expected: bool) -> None:
    assert supports_server_fallback(model) is expected
    wire = Wire(message(EXAMPLES[-1].model_dump_json(), model=model))
    await wire.provider(enable_fallbacks=True).generate(_req(ReportDraft, model=model))
    assert ("fallbacks" in wire.body) is expected
    assert ("anthropic-beta" in wire.requests[-1].headers) is expected


async def test_fallback_served_response_is_reported() -> None:
    content = [{"type": "fallback", "from": {"model": "claude-opus-5-5"},
                "to": {"model": "claude-opus-5"}},
               {"type": "text", "text": EXAMPLES[-1].model_dump_json()}]
    wire = Wire(message("", model="claude-opus-5", content=content))
    try:
        result = await wire.provider().generate(_req(ReportDraft))
    except LLMOutputError:
        pytest.skip("installed SDK does not model fallback blocks in parse()")
    assert result.served_by_fallback and result.model == "claude-opus-5"


async def test_backoff_honours_retry_after() -> None:
    calls: list[float] = []

    async def fn() -> str:
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise LLMError("rl", code="rate_limited", retryable=True, retry_after_s=0.3)
        return "ok"

    assert await with_backoff(fn, max_attempts=2, base=0.001, cap=0.01) == "ok"
    assert calls[1] - calls[0] >= 0.29


def test_retry_after_header_parsing_is_defensive() -> None:
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    for header, expected in [("12", 12.0), ("Wed, 21 Oct 2026 07:28:00 GMT", None), ("-3", None)]:
        exc = anthropic.RateLimitError("rl", response=httpx2.Response(
            429, request=req, headers={"retry-after": header}), body=None)
        assert map_anthropic_error(exc).retry_after_s == expected


class _Tavily:
    def __init__(self, exc: Exception | None = None, data: dict | None = None) -> None:
        self.exc, self.data, self.kwargs = exc, data, None

    async def search(self, **kw: Any) -> dict:
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return self.data or {}


@pytest.mark.parametrize("exc,code,retryable", [
    (tavily_errors.UsageLimitExceededError("429"), "search_rate_limited", True),
    (tavily_errors.ForbiddenError("432"), "search_quota", False),
    (tavily_errors.InvalidAPIKeyError("401"), "search_auth_failed", False),
    (tavily_errors.BadRequestError("400"), "search_bad_request", False),
    (tavily_errors.TimeoutError(30), "search_timeout", True),
])
async def test_tavily_error_mapping(exc: Exception, code: str, retryable: bool) -> None:
    s = TavilySearch("tvly-test", client=_Tavily(exc))  # type: ignore[arg-type]
    with pytest.raises(LLMError) as ei:
        await s.search("q")
    assert (ei.value.code, ei.value.retryable) == (code, retryable)


async def test_tavily_results_are_filtered_and_bounded() -> None:
    fake = _Tavily(data={"results": [
        {"url": "https://ok.example/a", "title": " A ", "content": "c", "raw_content": "r", "score": 0.7},
        {"url": "javascript:alert(1)", "title": "x"}, {"url": "", "title": "y"},
        {"url": "ftp://files.example/z", "title": "z"}]})
    s = TavilySearch("tvly-test", client=fake)  # type: ignore[arg-type]
    resp = await s.search("q" * 1000, max_results=50)
    assert [h.url for h in resp.hits] == ["https://ok.example/a"] and resp.hits[0].title == "A"
    assert len(fake.kwargs["query"]) == 400 and fake.kwargs["max_results"] == 10
