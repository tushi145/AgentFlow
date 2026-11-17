"""DEMO MODE ONLY — deterministic, clearly-labelled stand-ins for the LLM and search providers.

Nothing produced here is AI output or real research:
* every LLM result is built by a template function registered per output schema and has
  `is_demo=True`, zero token usage and model name "demo-template";
* every search hit uses the `demo://` URL scheme and a "[DEMO]" title prefix so it can never
  be confused with a real web source.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from pydantic import BaseModel

from app.llm.base import LLMError, LLMRequest, LLMResult, ModelInfo, T, Usage
from app.tools.search import SearchHit, SearchResponse

DEMO_MODEL = "demo-template"
DEMO_DISCLAIMER = ("DEMO MODE — this content was generated from fixed templates, not by an AI "
                   "model, and cites synthetic demo:// sources. It is not research.")

DemoBuilder = Callable[[dict], BaseModel]
_BUILDERS: dict[str, DemoBuilder] = {}


def register_demo_builder(model_cls: type[BaseModel]) -> Callable[[DemoBuilder], DemoBuilder]:
    def deco(fn: DemoBuilder) -> DemoBuilder:
        _BUILDERS[model_cls.__name__] = fn
        return fn
    return deco


class DemoLLMProvider:
    name = "demo"
    is_demo = True

    def __init__(self) -> None:
        from app.demo import templates  # noqa: F401  (registers builders)

    async def generate(self, request: LLMRequest[T]) -> LLMResult[T]:
        builder = _BUILDERS.get(request.output_model.__name__)
        if builder is None:
            raise LLMError(f"No demo template for {request.output_model.__name__}.",
                           code="demo_template_missing")
        parsed = request.output_model.model_validate(builder(request.demo_context).model_dump())
        return LLMResult(parsed=parsed, usage=Usage(), provider=self.name, model=DEMO_MODEL,
                         stop_reason="demo", request_id=None, is_demo=True)

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=DEMO_MODEL, display_name="Demo templates (not AI)", provider="demo")]


class DemoSearchProvider:
    name = "demo"
    is_demo = True

    async def search(self, query: str, *, max_results: int = 3) -> SearchResponse:
        slug = hashlib.sha1(query.encode()).hexdigest()[:8]
        hits = [SearchHit(
            url=f"demo://source/{slug}-{i}",
            title=f"[DEMO] Synthetic source {i + 1} for: {query[:60]}",
            snippet=f"[DEMO] Placeholder text standing in for a search result about '{query[:80]}'.",
            raw_content=(f"[DEMO] This is synthetic placeholder content for the query "
                         f"'{query[:120]}'. It exists only to exercise the AgentFlow pipeline "
                         f"in demo mode and contains no real-world facts."),
            score=None, provider=self.name) for i in range(min(max_results, 2))]
        return SearchResponse(query=query, hits=hits, credits=0.0, provider=self.name, is_demo=True)
