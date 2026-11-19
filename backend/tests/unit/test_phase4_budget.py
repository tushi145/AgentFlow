"""Phase 4 cost governance: tiered/caching-aware pricing, hard cost-cap clamping, truncation retry."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.planner import PlannerAgent
from app.llm.base import LLMOutputError, Usage
from app.schemas.agent_io import ExecutionPlan, PlannedSubtask
from app.services.budget import BudgetExceeded, BudgetGuard, PricingTable
from tests.fakes import FakeLLM, FakeSearch
from tests.helpers import limits, make_runtime

PRICES = PricingTable({"llm": {"anthropic": {
    "m": {"input_per_mtok": 1.0, "output_per_mtok": 5.0},
    "tiered": {"input_per_mtok": 0.10, "output_per_mtok": 0.50, "long_context_threshold": 100_000,
               "long_input_per_mtok": 0.50, "long_output_per_mtok": 2.50},
    "cached": {"input_per_mtok": 4.0, "output_per_mtok": 20.0, "cache_read_per_mtok": 0.2}}},
    "search": {"tavily": {"usd_per_credit": 0.008}}})


def test_bundled_pricing_matches_published_rates() -> None:
    t = PricingTable.load()
    for model, (i, o) in {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
                          "claude-haiku-5-5": (0.10, 0.50), "claude-fable-5-1": (10.0, 50.0)}.items():
        p = t.price("anthropic", model)
        assert p is not None and (p.input_per_mtok, p.output_per_mtok) == (i, o), model
    haiku = t.price("anthropic", "claude-haiku-5-5")
    assert haiku and haiku.rates(150_000) == (0.50, 2.50)


def test_long_context_tier_applies_above_threshold() -> None:
    small = PRICES.llm_cost("anthropic", "tiered", Usage(100_000, 1_000))
    large = PRICES.llm_cost("anthropic", "tiered", Usage(100_001, 1_000))
    assert small == pytest.approx((100_000 * 0.10 + 1_000 * 0.50) / 1e6)
    assert large == pytest.approx((100_001 * 0.50 + 1_000 * 2.50) / 1e6, abs=1e-6)


def test_unknown_cache_read_price_makes_cost_unknown_not_zero() -> None:
    assert PRICES.llm_cost("anthropic", "m", Usage(1000, 100, cache_read_tokens=500)) is None
    assert PRICES.llm_cost("anthropic", "cached", Usage(1000, 100, cache_read_tokens=500)) == \
        pytest.approx((1000 * 4 + 100 * 20 + 500 * 0.2) / 1e6)


async def test_output_allowance_is_clamped_to_fit_the_cost_cap() -> None:
    g = BudgetGuard(limits(max_cost_usd=0.05), PRICES)  # 20% (0.01) held for the report
    granted = await g.reserve_llm_call("research", 20_000, 1_000, provider="anthropic", model="m")
    # (0.04 - 1000*1e-6) / 5e-6 = 7800 output tokens fit
    assert granted == 7_800
    # The reservation itself counts: a concurrent call cannot spend the same dollars.
    with pytest.raises(BudgetExceeded) as ei:
        await g.reserve_llm_call("research", 20_000, 1_000, provider="anthropic", model="m")
    assert ei.value.limit == "max_cost_usd"
    # Releasing the reservation with the real (smaller) usage frees the remainder.
    await g.record_llm_usage("research", "anthropic", "m", Usage(1_000, 1_000), granted, 1_000,
                             reserved_provider="anthropic", reserved_model="m")
    assert g.cost_usd == pytest.approx(0.006)
    again = await g.reserve_llm_call("research", 20_000, 1_000, provider="anthropic", model="m")
    assert again == int((0.04 - 0.006 - 0.001) * 1e6 / 5)


async def test_report_can_use_the_held_back_reserve_but_never_exceed_the_cap() -> None:
    g = BudgetGuard(limits(max_cost_usd=0.05), PRICES)
    await g.record_llm_usage("analysis", "anthropic", "m", Usage(0, 8_000), 0, 0)  # $0.04 spent
    with pytest.raises(BudgetExceeded):
        await g.reserve_llm_call("verification", 4_000, 500, provider="anthropic", model="m")
    granted = await g.reserve_llm_call("report", 16_000, 500, provider="anthropic", model="m")
    assert granted == int((0.05 - 0.04 - 0.0005) * 1e6 / 5)  # 1900 tokens: exactly fits the cap


async def test_spend_never_exceeds_cap_under_parallel_load() -> None:
    g = BudgetGuard(limits(max_cost_usd=0.10, max_llm_calls=200), PRICES)

    async def worker() -> None:
        while True:
            try:
                out = await g.reserve_llm_call("research", 4_000, 2_000, provider="anthropic", model="m")
            except BudgetExceeded:
                return
            await asyncio.sleep(0)
            await g.record_llm_usage("research", "anthropic", "m", Usage(2_000, out), out, 2_000,
                                     reserved_provider="anthropic", reserved_model="m")

    await asyncio.gather(*(worker() for _ in range(8)))
    assert g.cost_usd <= 0.10 - g.final_reserve_cost + 1e-9


async def test_cost_cap_with_unpriced_model_fails_clearly() -> None:
    g = BudgetGuard(limits(max_cost_usd=1.0), PRICES)
    with pytest.raises(BudgetExceeded, match="cannot be enforced"):
        await g.reserve_llm_call("planner", 1_000, 100, provider="anthropic", model="unpriced")


async def test_no_cap_means_no_cost_clamp() -> None:
    g = BudgetGuard(limits(), PRICES)
    assert await g.reserve_llm_call("planner", 9_000, 100, provider="anthropic", model="unpriced") == 9_000


def _plan() -> ExecutionPlan:
    return ExecutionPlan(objective_summary="o", desired_outcome="d", success_criteria=["c"],
                         subtasks=[PlannedSubtask(id="t1", title="t", description="d", queries=["q"],
                                                  depends_on=[], expected_output="e")],
                         analysis_focus=[], report_outline=[])


async def test_truncated_output_is_retried_once_with_a_larger_allowance(tmp_path: Path) -> None:
    replies = [LLMOutputError("cut", code="truncated", usage=Usage(500, 4_000)), _plan()]
    llm = FakeLLM(lambda req: replies.pop(0))
    rt, log = make_runtime(tmp_path, llm, FakeSearch())
    out = await PlannerAgent(rt).run("objective")
    assert [c.max_output_tokens for c in llm.calls] == [4_000, 8_000]
    assert out.stats.repairs == 1 and "repair" in log.types()
    # Tokens of the truncated attempt were charged too.
    assert rt.budget.snapshot().output_tokens == 4_000 + 500


async def test_truncation_is_not_retried_when_the_budget_was_the_limit(tmp_path: Path) -> None:
    llm = FakeLLM(lambda req: LLMOutputError("cut", code="truncated"))
    rt, _ = make_runtime(tmp_path, llm, FakeSearch(), lim=limits(max_total_tokens=3_000))
    with pytest.raises(LLMOutputError):
        await PlannerAgent(rt).run("objective")
    assert len(llm.calls) == 1
