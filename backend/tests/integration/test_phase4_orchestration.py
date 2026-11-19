"""Phase 4 orchestration reliability: approval race, degraded agents, gap-loop dedupe, ordering."""

from __future__ import annotations

import asyncio

from app.llm.base import LLMError
from app.models import ApprovalRequest, EvaluationResult, Subtask
from tests.fakes import FakeLLM
from tests.integration.test_workflow import (MODEL, Script, default_search, event_types, harness,  # noqa: F401
                                             rows, run, wait_status)


async def _pending_approval(task_id: str) -> ApprovalRequest:
    await wait_status(task_id, {"awaiting_approval"})
    for _ in range(100):
        pending = [a for a in await rows(ApprovalRequest, task_id) if a.status == "pending"]
        if pending:
            return pending[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no pending approval")


async def test_fast_approval_decision_is_not_overwritten_by_unwinding_run(run, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: a decision recorded while the paused run is still unwinding used to be
    overwritten (status flipped back to awaiting_approval, a stale `paused` event published), and if
    unwinding took >15 s the API returned 409 *after* committing the approval — a stuck task."""
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    mgr = run.app.state.manager
    gate = asyncio.Event()
    original = mgr._handle_result

    async def slow_unwind(task_id, result, rt):  # type: ignore[no-untyped-def]
        await gate.wait()
        await original(task_id, result, rt)

    monkeypatch.setattr(mgr, "_handle_result", slow_unwind)
    tid = await run.submit(approval_mode="plan")
    approval = await _pending_approval(tid)

    # The decision must be accepted immediately, without waiting for the old run to unwind.
    await asyncio.wait_for(mgr.decide_approval(approval.id, True, "ok"), timeout=5)
    monkeypatch.setattr(mgr, "_handle_result", original)
    gate.set()

    task = await wait_status(tid, {"completed", "failed", "cancelled"})
    assert task.status == "completed", task.error_message
    types = await event_types(tid)
    after = types[types.index("approval_decided"):]
    assert "paused" not in after
    assert types.count("task_finished") == 1
    assert types.index("approval_requested") < types.index("approval_decided") < types.index("dispatch")


async def test_cancel_during_unwind_after_decision_stays_cancelled(run, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    mgr = run.app.state.manager
    gate = asyncio.Event()
    original = mgr._handle_result

    async def slow_unwind(task_id, result, rt):  # type: ignore[no-untyped-def]
        await gate.wait()
        await original(task_id, result, rt)

    monkeypatch.setattr(mgr, "_handle_result", slow_unwind)
    tid = await run.submit(approval_mode="plan")
    approval = await _pending_approval(tid)
    await mgr.decide_approval(approval.id, True, None)
    cancel = asyncio.create_task(mgr.cancel(tid))
    await asyncio.sleep(0.1)
    gate.set()
    await asyncio.wait_for(cancel, timeout=10)
    task = await wait_status(tid, {"cancelled", "completed", "failed"})
    await asyncio.sleep(0.3)  # let any straggler finish
    task = await wait_status(tid, {"cancelled", "completed", "failed"})
    assert task.status == "cancelled"
    types = await event_types(tid)
    assert types.count("task_finished") == 1
    assert run.search.queries == []  # the resumed run never executed research


async def test_analysis_failure_degrades_instead_of_failing_task(run) -> None:  # type: ignore[no-untyped-def]
    script = Script(fail={"AnalysisResult": LLMError("upstream exploded", code="api_error")})
    await run.start(FakeLLM(script, models=[MODEL]), default_search())
    tid = await run.submit()
    task = await wait_status(tid, {"completed", "failed"})
    assert task.status == "completed", task.error_message
    types = await event_types(tid)
    assert "agent_degraded" in types and "report_ready" in types
    ev = (await rows(EvaluationResult, tid))[0].metrics
    assert ev["reliability"]["agent_failures"] >= 1
    assert ev["reliability"]["degraded_agents"] == ["analysis"]


async def test_verification_failure_reports_claims_as_unverifiable(run) -> None:  # type: ignore[no-untyped-def]
    script = Script(fail={"VerificationReport": LLMError("timeout", code="timeout")})
    await run.start(FakeLLM(script, models=[MODEL]), default_search())
    tid = await run.submit()
    task = await wait_status(tid, {"completed", "failed"})
    assert task.status == "completed", task.error_message
    ev = (await rows(EvaluationResult, tid))[0].metrics
    assert set(ev["verification"]["verdicts"]) == {"unverifiable"}
    assert ev["reliability"]["degraded_agents"] == ["verification"]


async def test_gap_loop_skips_queries_already_executed(run) -> None:  # type: ignore[no-untyped-def]
    class RepeatGaps(Script):
        def __call__(self, req):  # type: ignore[no-untyped-def]
            out = super().__call__(req)
            if req.output_model.__name__ == "VerificationReport":
                # Verifier asks for a query that the plan already ran, plus one new one.
                out = out.model_copy(update={"gap_queries": ["acme jobs", "Acme Jobs ", "acme visa policy"]})
            return out

    script = RepeatGaps(sufficient=[False, True])
    await run.start(FakeLLM(script, models=[MODEL]), default_search(), default_max_iterations=3)
    tid = await run.submit()
    assert (await wait_status(tid, {"completed", "failed"})).status == "completed"
    gap = [s for s in await rows(Subtask, tid) if s.iteration == 1]
    assert [s.queries for s in gap] == [["acme visa policy"]]
    assert run.search.queries.count("acme jobs") == 1
