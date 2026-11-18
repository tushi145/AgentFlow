"""End-to-end LangGraph workflow tests with scripted (fake) live providers. No network."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.database.session import session_factory
from app.llm.base import LLMError, LLMRefusalError
from app.main import create_app
from app.models import (AgentExecution, ApprovalRequest, EvaluationResult, ExecutionEvent, Report,
                        ResearchSource, Subtask, Task, VerificationResult)
from app.schemas.agent_io import (AnalysisResult, Claim, ClaimVerdict, ExecutionPlan, ExtractedFact,
                                  Finding, PlannedSubtask, ReportDraft, ReportSection,
                                  ResearchResult, VerificationReport)
from app.schemas.tasks import TaskCreate
from app.services.task_service import create_task
from tests.conftest import make_settings
from tests.fakes import FakeLLM, FakeSearch, hit

MODEL = "test-model-default"
LONG = " Acme Corp hires graduate engineers in Bengaluru; the starting salary is 6 LPA." * 12


class Script:
    """Scripted 'LLM' that behaves like a cooperative model, keyed by the requested schema."""

    def __init__(self, *, sufficient: bool | list[bool] = True, plan_subtasks: list[PlannedSubtask] | None = None,
                 fail: dict[str, Exception] | None = None) -> None:
        self.sufficient = sufficient if isinstance(sufficient, list) else [sufficient] * 10
        self.plan_subtasks = plan_subtasks
        self.fail = fail or {}
        self.verify_calls = 0

    def __call__(self, req: Any) -> Any:
        name = req.output_model.__name__
        if name in self.fail:
            return self.fail[name]
        text = req.messages[-1].content
        if req.output_model is ExecutionPlan:
            return ExecutionPlan(
                objective_summary="o", desired_outcome="d", success_criteria=["pay known"],
                subtasks=self.plan_subtasks or [
                    PlannedSubtask(id="t1", title="Find companies", description="d", queries=["acme jobs"],
                                   depends_on=[], expected_output="e"),
                    PlannedSubtask(id="t2", title="Find salaries", description="d", queries=["acme salary"],
                                   depends_on=[], expected_output="e"),
                    PlannedSubtask(id="t3", title="Eligibility", description="d", queries=["acme eligibility"],
                                   depends_on=["t1", "t2"], expected_output="e")],
                analysis_focus=["compare"], report_outline=["Companies", "Pay"])
        if req.output_model is ResearchResult:
            refs = re.search(r"Available source ids: ([S0-9, ]+)", text).group(1).split(", ")
            return ResearchResult(facts=[ExtractedFact(statement=f"Acme pays 6 LPA ({refs[0]})",
                                                       source_refs=[refs[0]], evidence_quote="6 LPA")],
                                  coverage_notes="covered")
        if req.output_model is AnalysisResult:
            refs = sorted(set(re.findall(r"\[(S\d+)", text)))
            return AnalysisResult(summary="s", findings=[Finding(title="Pay", detail="6 LPA", source_refs=refs[:1])],
                                  claims=[Claim(id="c1", statement="Acme starting salary is 6 LPA",
                                                source_refs=refs[:1], importance="high")])
        if req.output_model is VerificationReport:
            self.verify_calls += 1
            suff = self.sufficient[min(self.verify_calls - 1, len(self.sufficient) - 1)]
            cited = re.search(r"cited: (S\d+)", text)
            return VerificationReport(
                verdicts=[ClaimVerdict(claim_id="c1", verdict="supported", confidence=0.9,
                                       reasoning="stated", supporting_refs=[cited.group(1)] if cited else [])],
                sufficient=suff, sufficiency_reasoning="ok",
                gap_queries=[] if suff else ["acme eligibility criteria 2026"])
        if req.output_model is ReportDraft:
            return ReportDraft(title="Acme hiring report", executive_summary="Acme pays 6 LPA [S1]. Bogus [S42].",
                               methodology="Web research.", conclusion="Done.", limitations=["Small sample."],
                               sections=[ReportSection(heading="Pay", body_markdown="| A | B |\n|---|---|\n| 1 | 2 |")])
        raise AssertionError(name)


def default_search() -> FakeSearch:
    return FakeSearch(default=[hit("https://acme.example/jobs", "Acme jobs", LONG),
                               hit("https://news.example/acme", "Acme news", LONG)])


async def wait_status(task_id: str, statuses: set[str], timeout: float = 20.0) -> Task:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with session_factory()() as s:
            t = await s.get(Task, task_id)
        if t and t.status in statuses:
            return t
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timeout; status={t.status if t else None}")
        await asyncio.sleep(0.05)


@pytest.fixture
def harness(tmp_path: Path):  # type: ignore[no-untyped-def]
    class H:
        def __init__(self) -> None:
            self.app = None

        async def start(self, llm: FakeLLM, search: FakeSearch, **settings_kw: Any):  # type: ignore[no-untyped-def]
            settings = make_settings(tmp_path, approval_cost_threshold_usd=None, **settings_kw)
            self.app = create_app(settings, live_llms={"anthropic": llm}, live_search=search)
            self._cm = self.app.router.lifespan_context(self.app)
            await self._cm.__aenter__()
            self.llm, self.search, self.settings = llm, search, settings
            return self

        async def submit(self, **body: Any) -> str:
            body.setdefault("prompt", "Research Acme graduate hiring and compare salaries.")
            async with session_factory()() as s:
                task = await create_task(s, self.settings, TaskCreate(**body), user_id=None,
                                         live_llms={"anthropic": self.llm}, live_search=self.search)
            await self.app.state.manager.start(task.id)
            return task.id

        async def stop(self) -> None:
            if self.app is not None:
                await self._cm.__aexit__(None, None, None)

    return H()


@pytest.fixture
async def run(harness):  # type: ignore[no-untyped-def]
    yield harness
    await harness.stop()


async def rows(model: Any, task_id: str) -> list[Any]:
    async with session_factory()() as s:
        return list((await s.scalars(select(model).where(model.task_id == task_id))).all())


async def event_types(task_id: str) -> list[str]:
    return [e.type for e in sorted(await rows(ExecutionEvent, task_id), key=lambda e: e.id)]


# ---------------------------------------------------------------------------------------------
async def test_full_parallel_run(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    tid = await run.submit()
    task = await wait_status(tid, {"completed", "failed"})
    assert task.status == "completed", task.error_message

    subs = await rows(Subtask, tid)
    assert {s.plan_key: s.status for s in subs} == {"t1": "done", "t2": "done", "t3": "done"}
    execs = await rows(AgentExecution, tid)
    agents = [e.agent_key for e in execs]
    assert agents.count("research") == 3 and {"planner", "analysis", "verification", "report"} <= set(agents)
    assert all(e.status == "completed" and e.mode == "live" and e.model == MODEL for e in execs)
    assert task.llm_calls == len(execs) and task.input_tokens > 0
    assert task.cost_usd is None  # no pricing configured for the test model → never guessed
    assert task.progress_done == task.progress_total

    types = await event_types(tid)
    first_dispatch = next(e for e in await rows(ExecutionEvent, tid) if e.type == "dispatch")
    assert first_dispatch.payload["subtasks"] == ["t1", "t2"]  # parallel layer, t3 waits for deps
    for t in ("task_started", "plan_created", "source_added", "verification_completed",
              "report_ready", "evaluation_completed", "task_finished"):
        assert t in types, t

    report = (await rows(Report, tid))[0]
    assert "[S42]" not in report.markdown and report.citation_violations == 1
    assert (run.settings.reports_dir / f"{tid}.pdf").stat().st_size > 1000
    assert (await rows(ResearchSource, tid)) and (await rows(VerificationResult, tid))[0].verdict == "supported"
    ev = (await rows(EvaluationResult, tid))[0].metrics
    assert ev["citation_integrity"]["removed"] == 1 and ev["source_coverage"]["rate"] == 1.0


async def test_sequential_mode_dispatches_one_at_a_time(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    tid = await run.submit(execution_mode="sequential")
    assert (await wait_status(tid, {"completed", "failed"})).status == "completed"
    dispatches = [e.payload["subtasks"] for e in await rows(ExecutionEvent, tid) if e.type == "dispatch"]
    assert dispatches == [["t1"], ["t2"], ["t3"]]


async def test_insufficient_verification_triggers_gap_iteration(run) -> None:  # type: ignore[no-untyped-def]
    script = Script(sufficient=[False, True])
    await run.start(FakeLLM(script, models=[MODEL]), default_search())
    tid = await run.submit(limits={"max_iterations": 2})
    task = await wait_status(tid, {"completed", "failed"})
    assert task.status == "completed" and task.iterations == 1
    keys = {s.plan_key for s in await rows(Subtask, tid)}
    assert "g1-1" in keys and script.verify_calls == 2
    assert "acme eligibility criteria 2026" in run.search.queries
    assert "iteration_started" in await event_types(tid)


async def test_iterations_are_bounded(run) -> None:  # type: ignore[no-untyped-def]
    script = Script(sufficient=False)  # never satisfied
    await run.start(FakeLLM(script, models=[MODEL]), default_search())
    tid = await run.submit(limits={"max_iterations": 2})
    assert (await wait_status(tid, {"completed", "failed"})).status == "completed"
    assert script.verify_calls == 2  # stopped at the limit instead of looping forever


async def test_plan_approval_approve(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    tid = await run.submit(approval_mode="plan")
    await wait_status(tid, {"awaiting_approval"})
    assert run.search.queries == []  # nothing executed before approval
    approval = (await rows(ApprovalRequest, tid))[0]
    assert approval.kind == "plan" and approval.payload["plan"]["subtasks"]
    await run.app.state.manager.decide_approval(approval.id, True, "looks good")
    assert (await wait_status(tid, {"completed", "failed"})).status == "completed"
    # The decision is part of the persisted timeline (and so of SSE replay), exactly once.
    decided = [e for e in await rows(ExecutionEvent, tid) if e.type == "approval_decided"]
    assert len(decided) == 1
    assert decided[0].payload == {"approval_id": approval.id, "kind": "plan", "approved": True,
                                  "comment": "looks good"}
    types = await event_types(tid)
    assert types.index("approval_requested") < types.index("approval_decided") < types.index("dispatch")


async def test_plan_approval_reject(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    tid = await run.submit(approval_mode="plan")
    await wait_status(tid, {"awaiting_approval"})
    approval = (await rows(ApprovalRequest, tid))[0]
    await run.app.state.manager.decide_approval(approval.id, False, "too broad")
    task = await wait_status(tid, {"rejected", "completed", "failed"})
    assert task.status == "rejected" and task.error_message == "too broad"
    assert run.search.queries == []
    decided = [e for e in await rows(ExecutionEvent, tid) if e.type == "approval_decided"]
    assert [(e.payload["approved"], e.payload["comment"]) for e in decided] == [(False, "too broad")]


async def test_tool_approval_per_batch(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    tid = await run.submit(approval_mode="tools")
    for expected_batch in (["t1", "t2"], ["t3"]):
        await wait_status(tid, {"awaiting_approval"})
        pending = [a for a in await rows(ApprovalRequest, tid) if a.status == "pending"]
        assert len(pending) == 1 and pending[0].payload["subtasks"] == expected_batch
        await run.app.state.manager.decide_approval(pending[0].id, True, None)
    assert (await wait_status(tid, {"completed", "failed"})).status == "completed"
    assert (await event_types(tid)).count("approval_decided") == 2  # one per approved batch


async def test_research_failures_are_non_fatal(run) -> None:  # type: ignore[no-untyped-def]
    search = FakeSearch(fail=LLMError("quota exceeded", code="search_quota"))
    await run.start(FakeLLM(Script(), models=[MODEL]), search)
    tid = await run.submit()
    task = await wait_status(tid, {"completed", "failed"})
    assert task.status == "completed"  # honest report about missing evidence
    report = (await rows(Report, tid))[0]
    assert "No sources were cited" in report.markdown or "Limitations" in report.markdown
    assert all(e.type != "source_added" for e in await rows(ExecutionEvent, tid))


async def test_planner_refusal_fails_task_with_safe_error(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(fail={"ExecutionPlan": LLMRefusalError("declined")}), models=[MODEL]),
                    default_search())
    tid = await run.submit()
    task = await wait_status(tid, {"completed", "failed"})
    assert task.status == "failed" and task.error_code == "model_refusal"
    failed = [e for e in await rows(AgentExecution, tid) if e.status == "failed"]
    assert failed and failed[0].agent_key == "planner"


async def test_budget_exhaustion_still_produces_report(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    tid = await run.submit(limits={"max_llm_calls": 4})
    task = await wait_status(tid, {"completed", "failed"})
    assert task.status == "completed" and task.budget_exhausted
    assert task.llm_calls <= 4
    assert "budget" in (await rows(Report, tid))[0].markdown.lower() or task.budget_exhausted


async def test_invalid_model_fails_fast(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=["some-other-model"]), default_search())
    tid = await run.submit()
    task = await wait_status(tid, {"completed", "failed"})
    assert task.status == "failed" and task.error_code == "invalid_model"
    assert await rows(AgentExecution, tid) == []


async def test_cancel_running_task(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL], delay_s=0.5), default_search())
    tid = await run.submit()
    for _ in range(200):  # wait until the planner is actually mid-call
        if await rows(AgentExecution, tid):
            break
        await asyncio.sleep(0.02)
    await run.app.state.manager.cancel(tid)
    task = await wait_status(tid, {"cancelled"})
    assert task.finished_at is not None
    ex = await rows(AgentExecution, tid)
    assert ex and all(e.status == "cancelled" and e.finished_at for e in ex)


async def test_demo_run_is_labelled_end_to_end(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    tid = await run.submit(mode="demo")
    task = await wait_status(tid, {"completed", "failed"})
    assert task.status == "completed" and task.mode == "demo"
    assert run.llm.calls == [] and run.search.queries == []  # real providers untouched
    assert all(s.url.startswith("demo://") for s in await rows(ResearchSource, tid))
    assert all(e.mode == "demo" for e in await rows(AgentExecution, tid))
    assert "DEMO MODE" in (await rows(Report, tid))[0].markdown
    assert task.input_tokens == 0


async def test_restart_recovery_marks_running_as_interrupted(run) -> None:  # type: ignore[no-untyped-def]
    await run.start(FakeLLM(Script(), models=[MODEL]), default_search())
    async with session_factory()() as s:
        s.add(Task(id="stale", prompt="p" * 20, title="t", mode="live", status="running", limits={},
                   agent_overrides={}, runtime_state={}))
        await s.commit()
    assert await run.app.state.manager.recover_after_restart() == 1
    assert (await wait_status("stale", {"interrupted"})).error_code == "interrupted"
