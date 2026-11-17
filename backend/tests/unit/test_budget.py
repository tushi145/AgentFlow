from __future__ import annotations

from pathlib import Path

import pytest

from app.llm.base import Usage
from app.services.budget import (BudgetExceeded, BudgetGuard, PricingTable, TaskLimits,
                                 estimate_run_cost)
from tests.conftest import make_settings

PRICING = PricingTable({
    "llm": {"anthropic": {"priced": {"input_per_mtok": 4.0, "output_per_mtok": 20.0,
                                     "cache_read_per_mtok": 0.2}}},
    "search": {"tavily": {"usd_per_credit": 0.01}},
})


def limits(**kw: object) -> TaskLimits:
    base = dict(max_total_tokens=10_000, max_llm_calls=3, max_tool_calls=2, timeout_seconds=60,
                max_cost_usd=None, max_iterations=2)
    base.update(kw)
    return TaskLimits(**base)  # type: ignore[arg-type]


def test_cost_calculation_and_unknown_model() -> None:
    u = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=1_000_000)
    assert PRICING.llm_cost("anthropic", "priced", u) == pytest.approx(4.0 + 2.0 + 0.2)
    assert PRICING.llm_cost("anthropic", "unpriced", u) is None  # never guessed
    assert PRICING.search_cost("tavily", 3) == pytest.approx(0.03)


def test_bundled_pricing_file_loads() -> None:
    table = PricingTable.load()
    assert table.price("anthropic", "claude-opus-5-5") is not None


def test_limits_resolve_clamps_to_ceiling(tmp_path: Path) -> None:
    s = make_settings(tmp_path, max_total_tokens_ceiling=50_000)
    lim = TaskLimits.resolve(s, {"max_total_tokens": 10_000_000, "max_llm_calls": 5})
    assert lim.max_total_tokens == 50_000 and lim.max_llm_calls == 5


async def test_llm_call_limit() -> None:
    g = BudgetGuard(limits(max_llm_calls=2), PRICING, final_reserve_tokens=0, final_reserve_calls=0)
    for _ in range(2):
        granted = await g.reserve_llm_call("planner", 1000, 100)
        await g.record_llm_usage("planner", "anthropic", "priced", Usage(100, 100), granted, 100)
    with pytest.raises(BudgetExceeded) as ei:
        await g.reserve_llm_call("planner", 1000, 100)
    assert ei.value.limit == "max_llm_calls"


async def test_token_budget_clamps_then_blocks() -> None:
    g = BudgetGuard(limits(max_total_tokens=3_000, max_llm_calls=10), PRICING,
                    final_reserve_tokens=0)
    granted = await g.reserve_llm_call("research", 5_000, 1_000)
    assert granted == 2_000  # clamped to remaining budget
    await g.record_llm_usage("research", "anthropic", "priced", Usage(1_000, 1_800), granted, 1_000)
    with pytest.raises(BudgetExceeded) as ei:
        await g.reserve_llm_call("research", 5_000, 100)
    assert ei.value.limit == "max_total_tokens"


async def test_cost_cap_and_per_agent_accounting() -> None:
    g = BudgetGuard(limits(max_cost_usd=0.01, max_llm_calls=10), PRICING)
    granted = await g.reserve_llm_call("analysis", 1000, 10)
    cost = await g.record_llm_usage("analysis", "anthropic", "priced", Usage(1000, 1000), granted, 10)
    assert cost == pytest.approx(0.024)
    snap = g.snapshot()
    assert snap.per_agent["analysis"]["llm_calls"] == 1 and snap.cost_complete
    with pytest.raises(BudgetExceeded) as ei:
        await g.reserve_llm_call("analysis", 1000, 10)
    assert ei.value.limit == "max_cost_usd"


async def test_unknown_pricing_marks_cost_incomplete() -> None:
    g = BudgetGuard(limits(), PRICING)
    granted = await g.reserve_llm_call("report", 100, 10)
    assert await g.record_llm_usage("report", "anthropic", "other", Usage(10, 10), granted, 10) is None
    assert g.snapshot().cost_complete is False


async def test_tool_call_limit_and_timeout() -> None:
    g = BudgetGuard(limits(max_tool_calls=1), PRICING)
    await g.reserve_tool_call("research", "web_search")
    with pytest.raises(BudgetExceeded):
        await g.reserve_tool_call("research", "web_search")
    g2 = BudgetGuard(limits(timeout_seconds=30), PRICING)
    g2._started -= 31
    with pytest.raises(BudgetExceeded) as ei:
        await g2.reserve_tool_call("research", "web_search")
    assert ei.value.limit == "timeout_seconds"


def test_estimate_run_cost() -> None:
    est = estimate_run_cost(research_subtasks=3, searches_per_subtask=2, iterations=1,
                            agent_models={a: ("anthropic", "priced") for a in
                                          ("research", "analysis", "verification", "report")},
                            pricing=PRICING, search_provider="tavily", search_credits_per_query=1)
    assert est.cost_complete and est.cost_usd and est.cost_usd > 0
    assert est.search_calls == 6 and est.breakdown["research"]["calls"] == 3
    est2 = estimate_run_cost(research_subtasks=1, searches_per_subtask=1, iterations=1,
                             agent_models={"report": ("anthropic", "unknown")}, pricing=PRICING,
                             search_provider="tavily", search_credits_per_query=1)
    assert est2.cost_complete is False and est2.cost_usd is None


async def test_reserve_is_held_back_for_final_report() -> None:
    g = BudgetGuard(limits(max_total_tokens=10_000, max_llm_calls=3), PRICING,
                    final_reserve_tokens=4_000, final_reserve_calls=1)
    for _ in range(2):
        granted = await g.reserve_llm_call("analysis", 1_000, 100)
        await g.record_llm_usage("analysis", "anthropic", "priced", Usage(100, 900), granted, 100)
    with pytest.raises(BudgetExceeded):  # third call is reserved for the report
        await g.reserve_llm_call("verification", 1_000, 100)
    granted = await g.reserve_llm_call("report", 8_000, 100)
    assert 0 < granted <= 10_000 - 2_000 - 100


async def test_restore_reseeds_counters() -> None:
    g = BudgetGuard(limits(max_llm_calls=5), PRICING)
    g.restore({"planner": {"input_tokens": 10, "output_tokens": 5, "llm_calls": 2, "cost_usd": 0.5}},
              elapsed_s=12)
    snap = g.snapshot()
    assert snap.llm_calls == 2 and snap.cost_usd == 0.5 and snap.elapsed_s >= 12
