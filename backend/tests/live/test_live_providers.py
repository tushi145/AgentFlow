"""Live provider checks against the REAL Anthropic and Tavily APIs (opt-in: `pytest -m live -s`).

Three tiers, each skipped unless its prerequisite is present:
* no credentials needed (network only): invalid-key rejection and client timeouts are mapped to
  the right error codes — proves error handling against the real endpoints at zero cost;
* ANTHROPIC_API_KEY (+ ANTHROPIC_DEFAULT_MODEL): model listing/validation and a structured-output
  round trip for every agent schema (a few cents);
* TAVILY_API_KEY: a real search returning real http(s) sources (1 credit).

Nothing here falls back to demo mode; a missing credential is a skip, never a pass.
"""

from __future__ import annotations

import socket
import time

import pytest

from app.config.settings import Settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMError, LLMMessage, LLMRequest
from app.llm.registry import ModelRegistry
from app.schemas.agent_io import (AnalysisResult, ExecutionPlan, ReportDraft, ResearchResult,
                                  VerificationReport)
from app.services.budget import PricingTable
from app.tools.search import TavilySearch

pytestmark = pytest.mark.live


def _network() -> bool:
    try:
        socket.create_connection(("api.anthropic.com", 443), timeout=5).close()
        return True
    except OSError:
        return False


needs_network = pytest.mark.skipif(not _network(), reason="No network access to api.anthropic.com")
SETTINGS = Settings()
needs_anthropic = pytest.mark.skipif(
    SETTINGS.anthropic_api_key is None or not SETTINGS.anthropic_default_model,
    reason="ANTHROPIC_API_KEY / ANTHROPIC_DEFAULT_MODEL not configured")
needs_tavily = pytest.mark.skipif(SETTINGS.tavily_api_key is None,
                                  reason="TAVILY_API_KEY not configured")


def _req(model: str, out: type, prompt: str, **kw: object) -> LLMRequest:
    return LLMRequest(model=model, system="You are a precise test fixture. Follow the schema.",
                      messages=[LLMMessage("user", prompt)], output_model=out,
                      max_output_tokens=int(kw.pop("max_output_tokens", 6000)),
                      effort=kw.pop("effort", "low"), **kw)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- no credentials
@needs_network
async def test_real_anthropic_rejects_invalid_key_as_auth_failed() -> None:
    p = AnthropicProvider("sk-ant-invalid-agentflow-test", timeout_s=30)
    with pytest.raises(LLMError) as ei:
        await p.generate(_req("claude-opus-5-5", ReportDraft, "hi"))
    assert ei.value.code == "auth_failed" and not ei.value.retryable
    assert "sk-ant-invalid" not in str(ei.value)
    print(f"\nLIVE anthropic invalid key -> {ei.value.code}: {ei.value}")


@needs_network
async def test_real_anthropic_client_timeout_is_retryable_timeout() -> None:
    p = AnthropicProvider("sk-ant-invalid-agentflow-test", timeout_s=0.001)
    with pytest.raises(LLMError) as ei:
        await p.generate(_req("claude-opus-5-5", ReportDraft, "hi", timeout_s=0.001))
    assert ei.value.code in ("timeout", "connection_error") and ei.value.retryable
    print(f"\nLIVE anthropic 1 ms timeout -> {ei.value.code}")


@needs_network
async def test_real_tavily_rejects_invalid_key() -> None:
    s = TavilySearch("tvly-invalid-agentflow-test")
    with pytest.raises(LLMError) as ei:
        await s.search("agentflow test", max_results=1)
    assert ei.value.code in ("search_auth_failed", "search_quota") and not ei.value.retryable
    print(f"\nLIVE tavily invalid key -> {ei.value.code}")


# ----------------------------------------------------------------------------- Anthropic key
@needs_anthropic
async def test_live_model_listing_and_default_model_validation() -> None:
    p = AnthropicProvider(SETTINGS.anthropic_api_key.get_secret_value())  # type: ignore[union-attr]
    reg = ModelRegistry({"anthropic": p})
    models = await reg.models("anthropic")
    assert models and all(m.id.startswith("claude") for m in models)
    check = await reg.validate("anthropic", SETTINGS.anthropic_default_model)
    print(f"\nLIVE models: {len(models)} listed; default {SETTINGS.anthropic_default_model!r} -> "
          f"{check.status}")
    assert check.status == "valid", check.message
    bogus = await reg.validate("anthropic", "claude-does-not-exist-1")
    assert bogus.status == "invalid"


@needs_anthropic
@pytest.mark.parametrize("schema,prompt", [
    (ExecutionPlan, "Plan research for: 'Which three EU countries had the highest solar capacity "
                    "in 2024?' Use 2 subtasks with 1 query each."),
    (ResearchResult, "Sources:\n<untrusted_source id=\"S1\" url=\"https://example.org\" title=\"x\">"
                     "Solar capacity in Germany reached 99 GW in 2024.</untrusted_source>\n"
                     "Extract facts citing S1."),
    (AnalysisResult, "Facts: - Germany solar 99 GW in 2024 [S1]\n- Spain solar 32 GW [S2]. "
                     "Produce 2 claims."),
    (VerificationReport, "Claim c1: Germany had 99 GW of solar in 2024 (cited S1). Evidence S1: "
                         "'Solar capacity in Germany reached 99 GW in 2024.' Verify."),
    (ReportDraft, "Write a 2-sentence report that Germany had 99 GW solar in 2024 [S1]."),
], ids=lambda x: x.__name__ if isinstance(x, type) else "")
async def test_live_structured_output_for_every_agent_schema(schema: type, prompt: str) -> None:
    model = SETTINGS.anthropic_default_model or ""
    p = AnthropicProvider(SETTINGS.anthropic_api_key.get_secret_value(),  # type: ignore[union-attr]
                          enable_fallbacks=SETTINGS.anthropic_enable_fallbacks)
    t0 = time.monotonic()
    result = await p.generate(_req(model, schema, prompt))
    dt = time.monotonic() - t0
    assert isinstance(result.parsed, schema) and not result.is_demo
    cost = PricingTable.load().llm_cost("anthropic", result.model, result.usage)
    print(f"\nLIVE {schema.__name__}: model={result.model} in={result.usage.input_tokens} "
          f"out={result.usage.output_tokens} cost=${cost} latency={dt:.1f}s "
          f"request_id={result.request_id} fallback={result.served_by_fallback}")


# ----------------------------------------------------------------------------- Tavily key
@needs_tavily
async def test_live_tavily_search_returns_real_sources() -> None:
    s = TavilySearch(SETTINGS.tavily_api_key.get_secret_value())  # type: ignore[union-attr]
    resp = await s.search("International Energy Agency solar PV capacity 2024", max_results=3)
    assert resp.hits and all(h.url.startswith(("http://", "https://")) for h in resp.hits)
    assert not resp.is_demo
    print("\nLIVE tavily:", [(h.domain, round(h.score or 0, 2), len(h.raw_content)) for h in resp.hits])


# ----------------------------------------------------------------------------- verification benchmark
BENCHMARK_MAX_COST_USD = 1.50


@needs_anthropic
async def test_live_verification_benchmark() -> None:
    """Real Verification Agent on the 26 labelled items, explicitly in live mode, hard-capped."""
    from app.evaluation.benchmarks import run_verification_benchmark
    from app.llm.factory import ProviderSet
    from app.services.agent_config_service import EffectiveAgentConfig

    model = SETTINGS.anthropic_default_model or ""
    pricing = PricingTable.load(SETTINGS.pricing_file)
    assert pricing.price("anthropic", model) is not None, f"{model!r} is not in the pricing file"
    llm = AnthropicProvider(SETTINGS.anthropic_api_key.get_secret_value(),  # type: ignore[union-attr]
                            enable_fallbacks=SETTINGS.anthropic_enable_fallbacks)
    # The benchmark never searches; a placeholder search client keeps the provider set explicit-live.
    providers = ProviderSet(mode="live", llms={"anthropic": llm},
                            search=TavilySearch("unused-benchmark-never-searches"))
    cfg = EffectiveAgentConfig(key="verification", enabled=True, provider="anthropic", model=model,
                               effort=None, max_output_tokens=12000, instructions="",
                               max_retries=2, timeout_s=180)
    t0 = time.monotonic()
    res = await run_verification_benchmark(SETTINGS, providers, cfg, pricing,
                                           max_cost_usd=BENCHMARK_MAX_COST_USD)
    m = res["metrics"]
    print(f"\nLIVE verification benchmark: model={res['model']} items={m['items']} "
          f"accuracy={m['accuracy']} binary={m['binary_accuracy']} brier={m['brier_score']} "
          f"tokens={res['tokens']} cost=${res['cost_usd']} time={time.monotonic() - t0:.0f}s")
    for r in res["item_results"]:
        if r["predicted"] != r["gold"]:
            print(f"  miss {r['id']}: gold={r['gold']} predicted={r['predicted']} conf={r['confidence']}")
    assert m["items"] == 26 and all(r["predicted"] for r in res["item_results"])
    assert res["cost_usd"] is not None and res["cost_usd"] <= BENCHMARK_MAX_COST_USD

