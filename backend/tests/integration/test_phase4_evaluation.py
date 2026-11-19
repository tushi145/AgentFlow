"""Phase 4 evaluation: offline guard benchmark, batched verification benchmark, calibration,
and end-to-end run metrics (grounding, report quality, cost/latency)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.evaluation.benchmarks import load_dataset, run_guard_benchmark, score
from app.main import create_app
from app.models import EvaluationResult
from tests.conftest import make_settings
from tests.fakes import FakeLLM
from tests.integration.test_api_endpoints import api  # noqa: F401  (fixture)
from tests.integration.test_workflow import (MODEL, Script, default_search, harness,  # noqa: F401
                                             rows, run, wait_status)


def test_guard_benchmark_safety_properties() -> None:
    """Regression gate on the labelled guard set. The *direction* of errors is the contract:
    no fabricated quote may be accepted and no injection may be missed."""
    m = run_guard_benchmark()["metrics"]
    assert m["quote"]["precision"] == 1.0          # zero fabricated / altered quotes accepted
    assert m["injection"]["recall"] == 1.0         # zero attacks missed
    assert m["url"]["accuracy"] == 1.0
    assert m["overall"]["accuracy"] >= 0.9


def test_guard_dataset_is_well_formed() -> None:
    items = load_dataset("guards")
    assert len({i["id"] for i in items}) == len(items)
    kinds = {i["kind"] for i in items}
    assert kinds == {"quote", "injection", "url"}
    for k in kinds:  # both classes present for every check
        assert {i["gold"] for i in items if i["kind"] == k} == {True, False}


async def test_guard_benchmark_runs_over_api_without_credentials(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)  # no API keys at all
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/v1/auth/login", json={"username": "admin", "password": "pw-test"})
            assert (await c.post("/api/v1/evaluations/benchmarks/verification/run")).status_code == 409
            r = await c.post("/api/v1/evaluations/benchmarks/guards/run")
            assert r.status_code == 202 and r.json()["provider"] == "deterministic"
            for _ in range(100):
                runrow = (await c.get(f"/api/v1/evaluations/benchmarks/{r.json()['id']}")).json()
                if runrow["status"] != "running":
                    break
                await asyncio.sleep(0.05)
            assert runrow["status"] == "completed" and runrow["cost_usd"] == 0.0
            assert runrow["metrics"]["overall"]["items"] == len(load_dataset("guards"))


def test_brier_score_rewards_calibration() -> None:
    items = [{"id": "a", "gold": "supported"}, {"id": "b", "gold": "unsupported"}]
    sharp = score(items, {"a": {"verdict": "supported", "confidence": 0.9},
                          "b": {"verdict": "unsupported", "confidence": 0.9}})
    wrong = score(items, {"a": {"verdict": "unsupported", "confidence": 0.9},
                          "b": {"verdict": "supported", "confidence": 0.9}})
    assert sharp["brier_score"] == pytest.approx(0.01) and wrong["brier_score"] == pytest.approx(0.81)


async def test_verification_benchmark_is_batched_so_every_item_is_judged(api) -> None:  # type: ignore[no-untyped-def]
    from app.schemas.agent_io import ClaimVerdict, VerificationReport

    items = load_dataset("verification")
    assert len(items) > 24  # larger than one agent call can verify
    batch_sizes: list[int] = []

    def judge(req):  # type: ignore[no-untyped-def]
        import re
        ids = re.findall(r"### Claim (c\d+).*?cited: (S\d+)", req.messages[-1].content, re.S)
        batch_sizes.append(len(ids))
        return VerificationReport(verdicts=[ClaimVerdict(claim_id=c, verdict="supported", confidence=0.7,
                                                         reasoning="r", supporting_refs=[s]) for c, s in ids],
                                  sufficient=True, sufficiency_reasoning="ok")

    api.llm.handler = judge  # type: ignore[attr-defined]
    r = await api.post("/api/v1/evaluations/benchmarks/verification/run")
    for _ in range(200):
        runrow = (await api.get(f"/api/v1/evaluations/benchmarks/{r.json()['id']}")).json()
        if runrow["status"] != "running":
            break
        await asyncio.sleep(0.05)
    assert runrow["status"] == "completed", runrow
    assert sum(batch_sizes) == len(items) and max(batch_sizes) <= 12
    assert all(x["predicted"] for x in runrow["item_results"])
    assert runrow["metrics"]["brier_score"] is not None


async def test_end_to_end_run_records_grounding_quality_and_cost(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    tid = await run.submit()
    assert (await wait_status(tid, {"completed", "failed"})).status == "completed"
    m = (await rows(EvaluationResult, tid))[0].metrics
    g = m["grounding"]
    assert g["facts_extracted"] == 3 and g["quote_verified"] == 3 and g["grounded_rate"] == 1.0
    assert m["report_quality"]["content_duplicates_merged"] >= 1  # identical pages were merged
    cost = m["cost"]
    assert cost["llm_calls"] >= 7 and cost["input_tokens"] > 0
    assert cost["cost_usd"] is None and cost["cost_complete"] is False  # test model is unpriced
    assert {"planner", "research", "analysis", "verification", "report"} <= set(cost["per_agent"])
    assert all("latency_ms" in v for v in cost["per_agent"].values())


async def test_verification_benchmark_cost_ceiling_stops_spending(tmp_path: Path) -> None:
    from app.evaluation.benchmarks import run_verification_benchmark
    from app.llm.base import LLMResult, Usage
    from app.llm.factory import ProviderSet
    from app.schemas.agent_io import VerificationReport
    from app.services.agent_config_service import EffectiveAgentConfig
    from app.services.budget import BudgetExceeded, PricingTable
    from tests.fakes import FakeSearch

    class GreedyLLM:
        """Worst case a real API allows: every call spends its entire output allowance."""
        name, is_demo = "anthropic", False

        def __init__(self) -> None:
            self.calls: list = []

        async def generate(self, req):  # type: ignore[no-untyped-def]
            self.calls.append(req)
            return LLMResult(parsed=VerificationReport(verdicts=[], sufficient=True, sufficiency_reasoning="x"),
                             usage=Usage(100, req.max_output_tokens), provider="anthropic", model="m",
                             stop_reason="end_turn", request_id=None)

        async def list_models(self):  # type: ignore[no-untyped-def]
            return []

    llm = GreedyLLM()
    pricing = PricingTable({"llm": {"anthropic": {"m": {"input_per_mtok": 4.0, "output_per_mtok": 20.0}}},
                            "search": {"tavily": {"usd_per_credit": 0.008}}})
    cfg = EffectiveAgentConfig(key="verification", enabled=True, provider="anthropic", model="m",
                               effort=None, max_output_tokens=12000, instructions="",
                               max_retries=0, timeout_s=60)
    providers = ProviderSet(mode="live", llms={"anthropic": llm}, search=FakeSearch())
    with pytest.raises(BudgetExceeded):  # one full call would cost $0.24 > the $0.20 ceiling
        await run_verification_benchmark(make_settings(tmp_path), providers, cfg, pricing,
                                         max_cost_usd=0.20)
    spent = sum(pricing.llm_cost("anthropic", "m", Usage(100, c.max_output_tokens)) or 0 for c in llm.calls)
    assert llm.calls and spent <= 0.20
