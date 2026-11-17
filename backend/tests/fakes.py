"""Reusable test doubles. No network access anywhere in the unit/integration suites."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.llm.base import LLMError, LLMRequest, LLMResult, ModelInfo, Usage
from app.tools.search import SearchHit, SearchResponse


class FakeLLM:
    """Scripted provider: responses are produced by `handler(request) -> BaseModel | Exception`."""

    name = "anthropic"
    is_demo = False

    def __init__(self, handler: Callable[[LLMRequest], Any] | None = None,
                 models: list[str] | None | str = "default", usage: Usage | None = None,
                 delay_s: float = 0.0) -> None:
        self.handler = handler
        self.delay_s = delay_s
        self.calls: list[LLMRequest] = []
        # models=None simulates an unreachable provider.
        self._models = ["model-a", "model-b"] if models == "default" else models
        self._usage = usage or Usage(input_tokens=1000, output_tokens=500)
        self.list_calls = 0

    async def generate(self, request: LLMRequest) -> LLMResult:
        self.calls.append(request)
        if self.delay_s:
            import asyncio
            await asyncio.sleep(self.delay_s)
        out = self.handler(request) if self.handler else None
        if isinstance(out, Exception):
            raise out
        if not isinstance(out, BaseModel):
            raise AssertionError(f"FakeLLM has no scripted output for {request.output_model}")
        return LLMResult(parsed=out, usage=self._usage, provider=self.name, model=request.model,
                         stop_reason="end_turn", request_id="req_fake")

    async def list_models(self) -> list[ModelInfo]:
        self.list_calls += 1
        if self._models is None:
            raise LLMError("down", code="connection_error", retryable=True)
        return [ModelInfo(id=m, display_name=m.upper(), provider=self.name) for m in self._models]


class FakeSearch:
    name = "tavily"
    is_demo = False

    def __init__(self, results: dict[str, list[SearchHit]] | None = None,
                 default: list[SearchHit] | None = None, fail: Exception | None = None) -> None:
        self.results = results or {}
        self.default = default if default is not None else []
        self.fail = fail
        self.queries: list[str] = []

    async def search(self, query: str, *, max_results: int = 5) -> SearchResponse:
        self.queries.append(query)
        if self.fail:
            raise self.fail
        hits = self.results.get(query, self.default)[:max_results]
        return SearchResponse(query=query, hits=hits, credits=1.0, provider=self.name)


def hit(url: str, title: str = "T", content: str = "", snippet: str = "") -> SearchHit:
    return SearchHit(url=url, title=title, snippet=snippet or content[:100], raw_content=content,
                     score=0.9, provider="tavily")
