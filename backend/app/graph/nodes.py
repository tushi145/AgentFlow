"""LangGraph node implementations. Each node does real work via an agent or deterministic code."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import Send, interrupt

from app.agents.analysis import AnalysisAgent
from app.agents.planner import PlannerAgent, topological_layers
from app.agents.report import ReportAgent
from app.agents.research import ResearchAgent, ResearchTask, fact_line
from app.agents.runtime import TaskRuntime
from app.agents.verification import VerificationAgent
from app.evaluation.metrics import compute_run_metrics
from app.graph.runner import run_agent, safe_error
from app.graph.state import AgentFlowState, ResearchInput
from app.llm.base import LLMError
from app.models.task import TaskStatus
from app.schemas.agent_io import AnalysisResult, Claim, ExecutionPlan, ExtractedFact
from app.services.budget import BudgetExceeded, estimate_run_cost
from app.services.pdf import markdown_to_pdf

log = logging.getLogger(__name__)
FINAL_STAGES = 3  # analysis + verification + report (per first iteration)


async def _stage(rt: TaskRuntime, stage: str, message: str, status: str | None = None) -> None:
    values: dict[str, Any] = {"current_stage": stage}
    if status:
        values["status"] = status
    await rt.store.update_task(rt.task_id, **values)
    await rt.emit("stage_changed", message, {"stage": stage, "status": status})


async def _progress(rt: TaskRuntime, done: int = 0, total: int = 0, note: str = "") -> None:
    d, t = await rt.store.add_progress(rt.task_id, done, total)
    await rt.emit("progress", note or f"{d}/{t} steps complete", {"done": d, "total": t})


def _enabled(rt: TaskRuntime, key: str) -> bool:
    return rt.agent_configs[key].enabled


async def _approval(rt: TaskRuntime, *, key: str, kind: str, title: str, payload: dict) -> dict:
    """Pause for a human decision (LangGraph interrupt). Idempotent across node re-execution."""
    req = await rt.store.get_or_create_approval(rt.task_id, key, kind, {"title": title, **payload})
    if req.status in ("approved", "rejected"):
        return {"approved": req.status == "approved", "comment": req.comment}
    await rt.store.update_task(rt.task_id, status=TaskStatus.AWAITING_APPROVAL)
    await rt.emit("approval_requested", title, {"approval_id": req.id, "kind": kind, **payload})
    decision = interrupt({"approval_id": req.id, "kind": kind, "title": title})
    # `approval_decided` is published by ExecutionManager.decide_approval when the decision is recorded.
    await rt.store.update_task(rt.task_id, status=TaskStatus.RUNNING)
    return decision


# ============================================================================ plan
async def plan_node(state: AgentFlowState, runtime: Runtime[TaskRuntime]) -> dict:
    rt = runtime.context
    await _stage(rt, "planning", "Planner is decomposing the objective.", TaskStatus.PLANNING)
    out = await run_agent(rt, PlannerAgent(rt), lambda a: a.run(state["objective"]),
                          iteration=0, input_summary={"objective": state["objective"][:2000]})
    plan: ExecutionPlan = out.result
    subtasks = {s.id: {**s.model_dump(), "iteration": 0, "status": "pending"} for s in plan.subtasks}
    await rt.store.save_subtasks(rt.task_id, list(subtasks.values()), iteration=0)
    await rt.store.update_task(rt.task_id, progress_done=1,
                               progress_total=1 + len(subtasks) + FINAL_STAGES)

    agent_models = {k: (c.provider, c.model) for k, c in rt.agent_configs.items()
                    if c.enabled and k != "planner"}
    est = estimate_run_cost(research_subtasks=len(subtasks),
                            searches_per_subtask=max(len(s.queries) for s in plan.subtasks),
                            iterations=rt.budget.limits.max_iterations, agent_models=agent_models,
                            pricing=rt.budget.pricing, search_provider=rt.providers.search.name,
                            search_credits_per_query=rt.settings.search_credits_per_query)
    est_cost = None if rt.is_demo else est.cost_usd
    await rt.store.update_task(rt.task_id, estimated_cost_usd=est_cost)
    await rt.emit("plan_created", f"Plan created with {len(subtasks)} research subtasks.",
                  {"plan": plan.model_dump(), "layers": out.extras.get("layers", []),
                   "fixes": out.extras.get("fixes", []),
                   "estimate": {"cost_usd": est_cost, "cost_complete": est.cost_complete,
                                "input_tokens": est.input_tokens, "output_tokens": est.output_tokens,
                                "llm_calls": est.llm_calls, "search_calls": est.search_calls,
                                "basis": "upper-bound estimate"}})
    await _progress(rt, note="Plan ready.")
    return {"plan": plan.model_dump(), "subtasks": subtasks, "iteration": 0,
            "analysis": None, "calculations": [], "claims": [], "verdicts": [],
            "gap_queries": [], "report": None}


async def plan_gate_node(state: AgentFlowState, runtime: Runtime[TaskRuntime]) -> dict:
    rt = runtime.context
    task = await rt.store.get_task(rt.task_id)
    est = task.estimated_cost_usd if task else None
    threshold = rt.settings.approval_cost_threshold_usd
    over_budget = est is not None and threshold is not None and est > threshold
    if rt.approval_mode not in ("plan", "all") and not over_budget:
        await _stage(rt, "research", "Plan accepted automatically.", TaskStatus.RUNNING)
        return {}
    kind = "cost" if over_budget and rt.approval_mode not in ("plan", "all") else "plan"
    title = (f"Estimated cost ${est:.2f} exceeds the ${threshold:.2f} approval threshold"
             if kind == "cost" else "Review the execution plan")
    decision = await _approval(rt, key="plan", kind=kind, title=title,
                               payload={"plan": state.get("plan"), "estimated_cost_usd": est})
    if not decision.get("approved"):
        return {"rejected": True, "rejection_comment": decision.get("comment")}
    await _stage(rt, "research", "Plan approved.", TaskStatus.RUNNING)
    return {}


def route_after_plan_gate(state: AgentFlowState) -> str:
    return "end" if state.get("rejected") else "dispatch"


# ============================================================================ dispatch / research
def _ready(state: AgentFlowState) -> list[str]:
    subs = state.get("subtasks", {})
    it = state.get("iteration", 0)
    finished = {k for k, s in subs.items() if s.get("status") in ("done", "failed")}
    return [k for k, s in subs.items()
            if s.get("status") == "pending" and s.get("iteration", 0) == it
            and set(s.get("depends_on", [])) <= finished]


async def dispatch_node(state: AgentFlowState, runtime: Runtime[TaskRuntime]) -> dict:
    rt = runtime.context
    if state.get("budget_exhausted"):
        return {"pending_batch": []}
    ready = _ready(state)
    if rt.execution_mode == "sequential":
        ready = ready[:1]
    if not ready:
        return {"pending_batch": []}
    if rt.approval_mode in ("tools", "all"):
        queries = {k: state["subtasks"][k].get("queries", []) for k in ready}
        decision = await _approval(
            rt, key=f"tools:{state.get('iteration', 0)}:{','.join(ready)}", kind="tools",
            title=f"Approve {sum(len(q) for q in queries.values())} web search(es)",
            payload={"subtasks": ready, "queries": queries})
        if not decision.get("approved"):
            return {"rejected": True, "rejection_comment": decision.get("comment"),
                    "pending_batch": []}
    for k in ready:
        await rt.store.update_subtask(rt.task_id, k, status="running")
    await rt.emit("dispatch", f"Dispatching {len(ready)} research subtask(s)"
                  f"{' in parallel' if len(ready) > 1 else ''}.", {"subtasks": ready})
    return {"pending_batch": ready, "subtasks": {k: {"status": "running"} for k in ready}}


def route_after_dispatch(state: AgentFlowState) -> list[Send] | str:
    if state.get("rejected"):
        return "end"
    batch = state.get("pending_batch") or []
    if not batch:
        return "analysis"
    subs = state["subtasks"]
    out = []
    for k in batch:
        deps = set(subs[k].get("depends_on", []))
        ctx = [f"{f['statement']} [{', '.join(f['source_refs'])}]"
               for f in state.get("facts", []) if f.get("subtask_id") in deps][:30]
        out.append(Send("research", ResearchInput(subtask={"id": k, **subs[k]},
                                                  iteration=state.get("iteration", 0),
                                                  context_facts=ctx)))
    return out


async def research_node(inp: ResearchInput, runtime: Runtime[TaskRuntime]) -> dict:
    rt = runtime.context
    st = inp["subtask"]
    task = ResearchTask(subtask_id=st["id"], title=st["title"], description=st.get("description", ""),
                        queries=st.get("queries", []), iteration=inp["iteration"],
                        context_facts=tuple(inp.get("context_facts", [])))
    try:
        out = await run_agent(rt, ResearchAgent(rt), lambda a: a.run(task), iteration=inp["iteration"],
                              subtask_key=st["id"], label=f"Research Agent · {st['title'][:60]}",
                              input_summary={"subtask": st["id"], "queries": task.queries})
    except Exception as exc:  # research failures are non-fatal: recorded, disclosed, continue
        code, msg = safe_error(exc)
        await rt.store.update_subtask(rt.task_id, st["id"], status="failed", error=msg)
        await _progress(rt, done=1, note=f"Subtask {st['id']} failed.")
        return {"subtasks": {st["id"]: {"status": "failed", "error": msg, "source_refs": []}},
                "errors": [{"agent": "research", "subtask": st["id"], "code": code, "message": msg}],
                "coverage_notes": [f"{st['title']}: FAILED ({msg})"],
                "budget_exhausted": isinstance(exc, BudgetExceeded),
                **({"budget_note": msg} if isinstance(exc, BudgetExceeded) else {})}
    facts = [{**f.model_dump(), "subtask_id": st["id"]} for f in out.result.facts]
    refs = out.extras.get("source_refs", [])
    await rt.store.update_subtask(rt.task_id, st["id"], status="done",
                                  result={"facts": facts, "coverage_notes": out.result.coverage_notes,
                                          "source_refs": refs, "failures": out.extras.get("failures")})
    await _progress(rt, done=1, note=f"Subtask {st['id']} complete ({len(facts)} facts).")
    return {"facts": facts, "coverage_notes": [f"{st['title']}: {out.result.coverage_notes}"],
            "subtasks": {st["id"]: {"status": "done", "source_refs": refs, "fact_count": len(facts)}}}


# ============================================================================ analysis
async def analysis_node(state: AgentFlowState, runtime: Runtime[TaskRuntime]) -> dict:
    rt = runtime.context
    facts = state.get("facts", [])
    plan = state.get("plan") or {}
    if not facts:
        await rt.emit("warning", "No facts were collected; skipping analysis and verification.", {})
        await _progress(rt, done=2)
        return {"analysis": None, "claims": [], "calculations": []}
    if not _enabled(rt, "analysis"):
        claims = _passthrough_claims(facts)
        await rt.emit("agent_skipped", "Analysis Agent disabled — facts passed through as claims.",
                      {"agent": "analysis"})
        await _progress(rt, done=1)
        return {"analysis": None, "claims": claims, "calculations": []}
    await _stage(rt, "analysis", "Analysis Agent is synthesising findings.")
    try:
        out = await run_agent(rt, AnalysisAgent(rt), lambda a: a.run(
            facts=facts, analysis_focus=plan.get("analysis_focus", []),
            success_criteria=plan.get("success_criteria", []),
            coverage_notes=state.get("coverage_notes", [])),
            iteration=state.get("iteration", 0), input_summary={"facts": len(facts)})
    except BudgetExceeded as exc:
        await _progress(rt, done=1)
        return {"budget_exhausted": True, "budget_note": str(exc),
                "errors": [{"agent": "analysis", "code": f"budget_{exc.limit}", "message": str(exc)}]}
    except LLMError as exc:
        # Failure recovery: research already succeeded, so keep going with the raw facts as claims
        # (they are still verified) and disclose the degradation in the report and metrics.
        code, msg = safe_error(exc)
        await rt.emit("agent_degraded", f"Analysis Agent failed ({code}); continuing with "
                      "extracted facts as claims.", {"agent": "analysis", "error_code": code})
        await _progress(rt, done=1)
        return {"analysis": None, "claims": _passthrough_claims(facts), "calculations": [],
                "errors": [{"agent": "analysis", "code": code, "message": msg, "degraded": True}]}
    await _progress(rt, done=1, note="Analysis complete.")
    result: AnalysisResult = out.result
    return {"analysis": result.model_dump(), "claims": [c.model_dump() for c in result.claims],
            "calculations": out.extras.get("calculations", [])}


def _passthrough_claims(facts: list[dict]) -> list[dict]:
    return [Claim(id=f"c{i + 1}", statement=f["statement"], source_refs=f["source_refs"],
                  importance="medium").model_dump() for i, f in enumerate(facts[:24])]


def _unverified(claims: list[Claim], reasoning: str) -> list[dict]:
    return [{"claim_id": c.id, "claim": c.statement, "verdict": "unverifiable", "confidence": 0.0,
             "importance": c.importance, "source_refs": c.source_refs, "reasoning": reasoning,
             "supporting_refs": [], "conflicting_refs": []} for c in claims]


# ============================================================================ verification
async def verify_node(state: AgentFlowState, runtime: Runtime[TaskRuntime]) -> dict:
    rt = runtime.context
    it = state.get("iteration", 0)
    claims = [Claim(**c) for c in state.get("claims", [])]
    if state.get("budget_exhausted") or not claims:
        await _progress(rt, done=1)
        return {"sufficient": False, "gap_queries": []}
    if not _enabled(rt, "verification"):
        verdicts = _unverified(claims, "Verification Agent disabled by configuration.")
        await rt.store.save_verifications(rt.task_id, it, verdicts)
        await rt.emit("agent_skipped", "Verification Agent disabled — claims reported as unverified.",
                      {"agent": "verification"})
        await _progress(rt, done=1)
        return {"verdicts": verdicts, "sufficient": True, "gap_queries": []}
    await _stage(rt, "verification", "Verification Agent is checking claims against evidence.")
    plan = state.get("plan") or {}
    try:
        out = await run_agent(rt, VerificationAgent(rt), lambda a: a.run(
            claims=claims, success_criteria=plan.get("success_criteria", [])),
            iteration=it, input_summary={"claims": len(claims)})
    except BudgetExceeded as exc:
        await _progress(rt, done=1)
        return {"budget_exhausted": True, "budget_note": str(exc), "sufficient": False,
                "errors": [{"agent": "verification", "code": f"budget_{exc.limit}", "message": str(exc)}]}
    except LLMError as exc:
        code, msg = safe_error(exc)
        verdicts = _unverified(claims, f"Verification Agent failed ({code}); claim not verified.")
        await rt.store.save_verifications(rt.task_id, it, verdicts)
        await rt.emit("agent_degraded", f"Verification Agent failed ({code}); all claims are "
                      "reported as unverified.", {"agent": "verification", "error_code": code})
        await _progress(rt, done=1)
        return {"verdicts": verdicts, "sufficient": False, "gap_queries": [],
                "errors": [{"agent": "verification", "code": code, "message": msg, "degraded": True}]}
    by_id = {c.id: c for c in claims}
    verdicts = [{**v.model_dump(), "claim": by_id[v.claim_id].statement,
                 "importance": by_id[v.claim_id].importance,
                 "source_refs": by_id[v.claim_id].source_refs}
                for v in out.result.verdicts if v.claim_id in by_id]
    await rt.store.save_verifications(rt.task_id, it, verdicts)
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
    await rt.emit("verification_completed",
                  f"Verified {len(verdicts)} claim(s); sufficient={out.result.sufficient}.",
                  {"counts": counts, "sufficient": out.result.sufficient,
                   "reasoning": out.result.sufficiency_reasoning,
                   "gap_queries": out.result.gap_queries, "iteration": it})
    await _progress(rt, done=1, note="Verification complete.")
    return {"verdicts": verdicts, "sufficient": out.result.sufficient,
            "gap_queries": out.result.gap_queries,
            "retrieval_method": out.extras.get("retrieval_method")}


def route_after_verify(state: AgentFlowState) -> str:
    if state.get("budget_exhausted") or state.get("sufficient"):
        return "report"
    return "plan_gaps" if state.get("gap_queries") else "report"


async def plan_gaps_node(state: AgentFlowState, runtime: Runtime[TaskRuntime]) -> dict:
    """Iterative refinement: turn verifier gap queries into a new research round (bounded)."""
    rt = runtime.context
    it = state.get("iteration", 0) + 1
    if it >= rt.budget.limits.max_iterations:
        await rt.emit("iteration_limit", f"Maximum iterations ({rt.budget.limits.max_iterations}) "
                      "reached; proceeding to report with disclosed gaps.", {"iteration": it})
        return {"gap_queries": []}
    executed = {_norm_query(q) for st in state.get("subtasks", {}).values()
                for q in st.get("queries", [])}
    queries: list[str] = []
    for q in state.get("gap_queries", []):
        key = _norm_query(q)
        if key and key not in executed and key not in {_norm_query(x) for x in queries}:
            queries.append(q.strip())
    queries = queries[:4]
    skipped = len([q for q in state.get("gap_queries", []) if q.strip()]) - len(queries)
    if skipped > 0:
        await rt.emit("gap_queries_deduplicated", f"Skipped {skipped} gap quer"
                      f"{'y' if skipped == 1 else 'ies'} already researched.", {"skipped": skipped})
    if not queries:
        await rt.emit("iteration_skipped", "No new gap queries to research; proceeding to report "
                      "with disclosed gaps.", {"iteration": it})
        return {"gap_queries": []}
    subs = {f"g{it}-{i + 1}": {"id": f"g{it}-{i + 1}", "title": f"Gap research: {q[:80]}",
                               "description": "Additional research requested by the Verification Agent.",
                               "queries": [q], "depends_on": [], "expected_output": "Evidence for the gap",
                               "iteration": it, "status": "pending"}
            for i, q in enumerate(queries)}
    await rt.store.save_subtasks(rt.task_id, list(subs.values()), iteration=it)
    await rt.store.update_task(rt.task_id, iterations=it)
    await _progress(rt, total=len(subs) + 2, note=f"Iteration {it + 1}: {len(subs)} gap subtask(s) added.")
    await rt.emit("iteration_started", f"Verification found gaps — starting research iteration {it + 1}.",
                  {"iteration": it, "queries": queries})
    await _stage(rt, "research", f"Additional research (iteration {it + 1}).")
    return {"iteration": it, "subtasks": subs, "gap_queries": []}


def _norm_query(q: str) -> str:
    return " ".join((q or "").lower().split())


def route_after_gaps(state: AgentFlowState) -> str:
    pending = any(s.get("status") == "pending" for s in state.get("subtasks", {}).values())
    return "dispatch" if pending else "report"


# ============================================================================ report / evaluate
async def report_node(state: AgentFlowState, runtime: Runtime[TaskRuntime]) -> dict:
    rt = runtime.context
    await _stage(rt, "report", "Report Agent is writing the final report.")
    analysis = AnalysisResult(**state["analysis"]) if state.get("analysis") else None
    snap = rt.budget.snapshot()
    models = sorted({f"{c.provider}:{c.model}" for c in rt.agent_configs.values() if c.enabled})
    run_stats = {
        "Execution mode": "DEMO (templates, not AI)" if rt.is_demo else "Live AI execution",
        "Research subtasks": len(state.get("subtasks", {})),
        "Research iterations": state.get("iteration", 0) + 1,
        "Sources retrieved": len(rt.sources),
        "Facts extracted": len(state.get("facts", [])),
        "Claims verified": len(state.get("verdicts", [])),
        "Evidence retrieval": state.get("retrieval_method") or "n/a",
        "LLM calls": snap.llm_calls, "Tool calls": snap.tool_calls,
        "Tokens (in/out)": f"{snap.input_tokens:,} / {snap.output_tokens:,}",
        "Models": "demo-template" if rt.is_demo else ", ".join(models),
    }
    gaps = list((state.get("analysis") or {}).get("gaps", []))
    gaps += [(f"Research subtask {e['subtask']} failed — {e.get('message')}" if e.get("subtask") else
              f"{str(e.get('agent', 'An')).capitalize()} agent failed — {e.get('message')}"
              + ("; its output was replaced by a degraded fallback." if e.get("degraded") else ""))
             for e in state.get("errors", [])]
    out = await run_agent(rt, ReportAgent(rt), lambda a: a.run(
        report_outline=(state.get("plan") or {}).get("report_outline", []), analysis=analysis,
        calculations=state.get("calculations", []), verdicts=state.get("verdicts", []),
        gaps=gaps, run_stats=run_stats,
        budget_note=state.get("budget_note") if state.get("budget_exhausted") else None),
        iteration=state.get("iteration", 0), input_summary={"verdicts": len(state.get("verdicts", []))})
    rendered = out.result
    reports_dir = rt.settings.reports_dir
    md_path = reports_dir / f"{rt.task_id}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(rendered.markdown, encoding="utf-8")
    pdf_path: str | None = None
    try:
        pdf = markdown_to_pdf(rendered.markdown, reports_dir / f"{rt.task_id}.pdf",
                              title=rendered.title, watermark="DEMO" if rt.is_demo else None)
        pdf_path = pdf.name
    except Exception:  # PDF failure must not lose the report; MD is still available
        log.exception("PDF rendering failed for task %s", rt.task_id)
        await rt.emit("warning", "PDF rendering failed; Markdown report is available.", {})
    report_id = await rt.store.save_report(
        rt.task_id, title=rendered.title, summary=rendered.summary, markdown=rendered.markdown,
        pdf_path=pdf_path, source_count=len(rendered.cited_refs),
        avg_confidence=rendered.avg_confidence,
        citation_violations=len(rendered.citation_violations), mode=rt.mode)
    await _progress(rt, done=1, note="Report ready.")
    await rt.emit("report_ready", f"Report ready: {rendered.title}",
                  {"report_id": report_id, "citations": len(rendered.cited_refs),
                   "citation_violations": rendered.citation_violations})
    return {"report": {"id": report_id, "title": rendered.title, "cited_refs": rendered.cited_refs,
                       "citation_violations": rendered.citation_violations}}


async def evaluate_node(state: AgentFlowState, runtime: Runtime[TaskRuntime]) -> dict:
    rt = runtime.context
    report = state.get("report") or {}
    retries = 0  # counted from events of this task
    try:
        hist = await rt.extra["bus"].history(rt.task_id) if "bus" in rt.extra else []
        retries = sum(1 for e in hist if e.type == "retry")
    except Exception:  # pragma: no cover
        pass
    metrics = compute_run_metrics(
        subtasks=state.get("subtasks", {}), facts=state.get("facts", []),
        verdicts=state.get("verdicts", []), sources=rt.sources.all(),
        cited_refs=report.get("cited_refs", []),
        citation_violations=report.get("citation_violations", []),
        agent_failures=state.get("errors", []), retries=retries,
        iterations=state.get("iteration", 0) + 1,
        budget_exhausted=bool(state.get("budget_exhausted")), completed=bool(report),
        mode=rt.mode, retrieval_method=state.get("retrieval_method"))
    await rt.store.save_evaluation(rt.task_id, metrics)
    await rt.emit("evaluation_completed", "Run quality metrics recorded.", {"metrics": metrics})
    return {"evaluation": metrics}


# Re-export for tests
__all__ = ["plan_node", "plan_gate_node", "dispatch_node", "research_node", "analysis_node",
           "verify_node", "plan_gaps_node", "report_node", "evaluate_node", "route_after_dispatch",
           "route_after_plan_gate", "route_after_verify", "route_after_gaps", "topological_layers",
           "fact_line", "ExtractedFact"]
