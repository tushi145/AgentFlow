"""Live provider checks against the REAL Anthropic and Tavily APIs (opt-in: `pytest -m live -s`).

Three tiers, each skipped unless its prerequisite is present:
* no credentials needed (network only): invalid-key rejection and client timeouts are mapped to
  the right error codes — proves error handling against the real endpoints at zero cost;
* ANTHROPIC_API_KEY (+ ANTHROPIC_DEFAULT_MODEL): model listing/validation (free), each of the five
  agents run for real with its production prompt (each hard-capped at $0.40), and the verification
  benchmark (hard-capped at $1.50); the Research agent test also needs TAVILY_API_KEY;
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
from app.schemas.agent_io import ReportDraft
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


# ----------------------------------------------------------------------------- the five agents
# Each test runs the REAL agent class (its production prompt, schema, citation/grounding gates and
# budget accounting) against the live API, explicitly in live mode, under a hard cost ceiling.
AGENT_TEST_MAX_COST_USD = 0.40
FICTIONAL = {  # synthetic sources about fictional firms: tests judge the agent, not world knowledge
    "https://helio-a.example/annual": "Helio Grid (a fictional utility) commissioned 420 MW of solar capacity "
        "in 2024, bringing its total to 1,900 MW. Its 2024 capital expenditure was 610 million euros.",
    "https://helio-b.example/press": "Northlight Power (a fictional utility) added 260 MW of solar in 2024 for a "
        "total of 1,150 MW, and reported capital expenditure of 340 million euros for the year.",
    "https://helio-c.example/review": "An industry review states that Helio Grid commissioned 380 MW of solar "
        "in 2024, after two projects slipped into 2025.",
}


def _live_runtime(objective: str, *, search: object | None = None):  # type: ignore[no-untyped-def]
    from app.agents.runtime import SourceRegistry, TaskRuntime
    from app.agents.specs import AGENT_SPECS
    from app.llm.factory import ProviderSet
    from app.services.agent_config_service import EffectiveAgentConfig
    from app.services.budget import BudgetGuard, TaskLimits

    model = SETTINGS.anthropic_default_model or ""
    pricing = PricingTable.load(SETTINGS.pricing_file)
    assert pricing.price("anthropic", model) is not None, f"{model!r} is not in the pricing file"
    llm = AnthropicProvider(SETTINGS.anthropic_api_key.get_secret_value(),  # type: ignore[union-attr]
                            enable_fallbacks=SETTINGS.anthropic_enable_fallbacks,
                            timeout_s=SETTINGS.llm_request_timeout_seconds)
    providers = ProviderSet(mode="live", llms={"anthropic": llm},
                            search=search or TavilySearch("unused-this-agent-never-searches"))
    limits = TaskLimits(max_total_tokens=150_000, max_llm_calls=4, max_tool_calls=12,
                        timeout_seconds=600, max_iterations=1, max_subtasks=4,
                        max_searches_per_subtask=1, max_pages_per_subtask=3,
                        max_cost_usd=AGENT_TEST_MAX_COST_USD)
    configs = {k: EffectiveAgentConfig(key=k, enabled=True, provider="anthropic", model=model,
                                       effort=spec.default_effort,
                                       max_output_tokens=spec.default_max_output_tokens,
                                       instructions="", max_retries=2, timeout_s=240)
               for k, spec in AGENT_SPECS.items()}
    events: list[tuple[str, str, dict]] = []

    async def emit(t: str, m: str, payload: dict) -> None:
        events.append((t, m, payload))

    rt = TaskRuntime(task_id="live-agent-check", mode="live", objective=objective, settings=SETTINGS,
                     providers=providers, budget=BudgetGuard(limits, pricing), agent_configs=configs,
                     sources=SourceRegistry(), emit=emit)
    return rt, events


async def _add_fictional_sources(rt) -> None:  # type: ignore[no-untyped-def]
    from app.security.content_firewall import sanitize

    for url, text in FICTIONAL.items():
        await rt.sources.add(url=url, title=url.split("/")[2], snippet="", sanitized=sanitize(text),
                             search_query="fixture", provider="fixture", score=None, is_demo=False)


def _report(name: str, out, rt) -> None:  # type: ignore[no-untyped-def]
    s = out.stats
    snap = rt.budget.snapshot()
    print(f"\nLIVE {name}: model={s.model} calls={s.llm_calls} in={s.usage.input_tokens} "
          f"out={s.usage.output_tokens} cost=${s.cost_usd} retries={s.retries} repairs={s.repairs} "
          f"time={s.duration_ms / 1000:.1f}s notes={s.notes}")
    assert not s.is_demo and s.llm_calls >= 1
    assert snap.cost_complete and snap.cost_usd <= AGENT_TEST_MAX_COST_USD


@needs_anthropic
async def test_live_agent_planner() -> None:
    from app.agents.planner import PlannerAgent, topological_layers

    objective = ("Compare the three EU countries with the most installed solar PV capacity at the end "
                 "of 2024, including capacity added in 2024 and the main support schemes.")
    rt, _ = _live_runtime(objective)
    out = await PlannerAgent(rt).run(objective)
    plan = out.result
    print("  subtasks:", [(st.id, st.title, st.queries, st.depends_on) for st in plan.subtasks])
    assert 2 <= len(plan.subtasks) <= 4 and all(st.queries for st in plan.subtasks)
    assert plan.success_criteria and plan.report_outline
    assert sum(len(layer) for layer in topological_layers(plan.subtasks)) == len(plan.subtasks)
    _report("Planner", out, rt)


@needs_anthropic
@needs_tavily
async def test_live_agent_research() -> None:
    """Real Tavily search + page reading + fact extraction; every kept quote must be grounded."""
    from app.agents.research import ResearchAgent, ResearchTask
    from app.tools.quality import quote_found

    search = TavilySearch(SETTINGS.tavily_api_key.get_secret_value())  # type: ignore[union-attr]
    rt, events = _live_runtime("How much solar PV capacity was added worldwide in 2023?", search=search)
    out = await ResearchAgent(rt).run(ResearchTask(
        "t1", "Global solar PV additions in 2023",
        "Find the reported global solar PV capacity added in 2023 and who reported it.",
        ["IEA global solar PV capacity additions 2023 gigawatts"]))
    facts = out.result.facts
    print("  sources:", [(s.ref, s.domain) for s in rt.sources.all()])
    print("  grounding:", out.extras["grounding"])
    for f in facts[:8]:
        print(f"  fact: {f.statement} {f.source_refs} quote={f.evidence_quote[:80]!r}")
    assert rt.sources.all() and all(s.url.startswith("http") for s in rt.sources.all())
    assert facts, "no grounded facts extracted"
    texts = {s.ref: s.content for s in rt.sources.all()}
    assert all(any(quote_found(f.evidence_quote, texts[r]) for r in f.source_refs) for f in facts)
    _report("Research", out, rt)


@needs_anthropic
async def test_live_agent_analysis() -> None:
    from app.agents.analysis import AnalysisAgent

    rt, _ = _live_runtime("Compare Helio Grid and Northlight Power's 2024 solar build-out.")
    await _add_fictional_sources(rt)
    facts = [{"statement": "Helio Grid commissioned 420 MW of solar in 2024 (total 1,900 MW).", "source_refs": ["S1"]},
             {"statement": "Helio Grid's 2024 capex was 610 million euros.", "source_refs": ["S1"]},
             {"statement": "Northlight Power added 260 MW of solar in 2024 (total 1,150 MW).", "source_refs": ["S2"]},
             {"statement": "Northlight Power's 2024 capex was 340 million euros.", "source_refs": ["S2"]},
             {"statement": "A review says Helio Grid commissioned 380 MW in 2024.", "source_refs": ["S3"]}]
    out = await AnalysisAgent(rt).run(facts=facts, analysis_focus=["2024 additions", "capex per MW added"],
                                      success_criteria=["Both firms compared"], coverage_notes=[])
    r = out.result
    print("  claims:", [(c.id, c.importance, c.statement, c.source_refs) for c in r.claims])
    print("  calculations:", out.extras["calculations"])
    assert r.claims and all(c.source_refs and set(c.source_refs) <= {"S1", "S2", "S3"} for c in r.claims)
    _report("Analysis", out, rt)


@needs_anthropic
async def test_live_agent_verification() -> None:
    from app.agents.verification import VerificationAgent
    from app.schemas.agent_io import Claim

    rt, _ = _live_runtime("Verify claims about Helio Grid and Northlight Power.")
    await _add_fictional_sources(rt)
    claims = [Claim(id="c1", statement="Northlight Power added 260 MW of solar in 2024.", source_refs=["S2"], importance="high"),
              Claim(id="c2", statement="Helio Grid commissioned 420 MW of solar in 2024.", source_refs=["S1"], importance="high"),
              Claim(id="c3", statement="Northlight Power employs 2,000 people.", source_refs=["S2"], importance="medium")]
    out = await VerificationAgent(rt).run(claims=claims, success_criteria=["2024 additions known"])
    by_id = {v.claim_id: v for v in out.result.verdicts}
    for v in out.result.verdicts:
        print(f"  {v.claim_id}: {v.verdict} ({v.confidence:.2f}) sup={v.supporting_refs} "
              f"con={v.conflicting_refs} - {v.reasoning[:120]}")
    print("  guard notes:", out.extras["guard_notes"])
    assert set(by_id) == {"c1", "c2", "c3"}
    # Expected (informational, not asserted): c1 supported, c2 conflicting (S1 vs S3), c3 unverifiable.
    _report("Verification", out, rt)


@needs_anthropic
async def test_live_agent_report() -> None:
    from app.agents.report import ReportAgent

    rt, events = _live_runtime("Compare Helio Grid and Northlight Power's 2024 solar build-out.")
    await _add_fictional_sources(rt)
    verdicts = [
        {"claim_id": "c1", "claim": "Northlight Power added 260 MW of solar in 2024.", "verdict": "supported",
         "confidence": 0.95, "importance": "high", "supporting_refs": ["S2"], "conflicting_refs": []},
        {"claim_id": "c2", "claim": "Helio Grid commissioned 420 MW of solar in 2024.", "verdict": "conflicting",
         "confidence": 0.5, "importance": "high", "supporting_refs": ["S1"], "conflicting_refs": ["S3"]}]
    out = await ReportAgent(rt).run(report_outline=["2024 additions", "Discrepancies"], analysis=None,
                                    calculations=[], verdicts=verdicts, gaps=["Staffing data not found."],
                                    run_stats={"Mode": "live agent check"})
    md = out.result.markdown
    print(md[:1500])
    print("  citation violations:", out.result.citation_violations, "| quality:", out.result.quality)
    assert "## References" in md and "DEMO" not in md
    assert "Conflicting evidence" in md  # disclosed by code regardless of the model's prose
    assert set(out.result.cited_refs) <= {"S1", "S2", "S3"}
    _report("Report", out, rt)


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

