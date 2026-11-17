"""DEMO MODE ONLY — fixed templates for each agent output schema.

Every string is visibly marked "[DEMO]". These exist to let the UI and pipeline be exercised
without API keys; they are never presented as AI output or research.
"""

from __future__ import annotations

from app.demo.providers import register_demo_builder
from app.schemas.agent_io import (AnalysisResult, Claim, ClaimVerdict, ComparisonRow,
                                  ComparisonTable, ExecutionPlan, ExtractedFact, Finding,
                                  PlannedSubtask, ReportDraft, ReportSection, ResearchResult,
                                  VerificationReport)


def _topic(ctx: dict) -> str:
    return (ctx.get("objective") or "the requested topic").strip().rstrip(".")[:120]


@register_demo_builder(ExecutionPlan)
def demo_plan(ctx: dict) -> ExecutionPlan:
    t = _topic(ctx)
    return ExecutionPlan(
        objective_summary=f"[DEMO] Investigate: {t}",
        desired_outcome="[DEMO] A structured, cited overview (template output, not AI).",
        success_criteria=["[DEMO] Main options identified", "[DEMO] Options compared on key criteria"],
        subtasks=[
            PlannedSubtask(id="t1", title="[DEMO] Background and context",
                           description="[DEMO] Template subtask.", queries=[f"{t} overview"],
                           depends_on=[], expected_output="[DEMO] Context summary"),
            PlannedSubtask(id="t2", title="[DEMO] Identify main options",
                           description="[DEMO] Template subtask.", queries=[f"{t} main options"],
                           depends_on=[], expected_output="[DEMO] List of options"),
            PlannedSubtask(id="t3", title="[DEMO] Details for each option",
                           description="[DEMO] Template subtask depending on t2.",
                           queries=[f"{t} comparison criteria"], depends_on=["t2"],
                           expected_output="[DEMO] Per-option details"),
        ],
        analysis_focus=["[DEMO] Compare options"],
        report_outline=["[DEMO] Context", "[DEMO] Options"],
        assumptions=["[DEMO] Demo mode uses fixed templates."])


@register_demo_builder(ResearchResult)
def demo_research(ctx: dict) -> ResearchResult:
    refs = ctx.get("refs") or []
    facts = [ExtractedFact(statement=f"[DEMO] Placeholder fact {i + 1} for '{ctx.get('subtask', '')}'.",
                           source_refs=[r], evidence_quote="[DEMO] synthetic placeholder content")
             for i, r in enumerate(refs[:2])]
    return ResearchResult(facts=facts, coverage_notes="[DEMO] Template coverage note.",
                          follow_up_queries=[])


@register_demo_builder(AnalysisResult)
def demo_analysis(ctx: dict) -> AnalysisResult:
    facts = ctx.get("facts") or []
    refs = sorted({r for f in facts for r in f.get("source_refs", [])}, key=lambda r: int(r[1:]))
    claims = [Claim(id=f"c{i + 1}", statement=f["statement"], source_refs=f["source_refs"],
                    importance="high" if i < 2 else "medium") for i, f in enumerate(facts[:4])]
    return AnalysisResult(
        summary="[DEMO] Template analysis summary.",
        findings=[Finding(title="[DEMO] Finding", detail="[DEMO] Template finding.",
                          source_refs=refs[:2])],
        comparisons=[ComparisonTable(title="[DEMO] Option comparison", columns=["Criterion A", "Criterion B"],
                                     rows=[ComparisonRow(label="[DEMO] Option 1", cells=["—", "—"],
                                                         source_refs=refs[:1])])],
        calculations=[], claims=claims, gaps=["[DEMO] Real research requires API keys."])


@register_demo_builder(VerificationReport)
def demo_verification(ctx: dict) -> VerificationReport:
    ev = ctx.get("evidence_refs") or {}
    verdicts = []
    for c in ctx.get("claims") or []:
        shown = [r for r in c.get("source_refs", []) if r in ev.get(c["id"], [])]
        verdicts.append(ClaimVerdict(
            claim_id=c["id"], verdict="supported" if shown else "unverifiable",
            confidence=0.5 if shown else 0.0,
            reasoning="[DEMO] Template verdict — no real verification was performed.",
            supporting_refs=shown, conflicting_refs=[]))
    return VerificationReport(verdicts=verdicts, sufficient=True,
                              sufficiency_reasoning="[DEMO] Template decision.", gap_queries=[])


@register_demo_builder(ReportDraft)
def demo_report(ctx: dict) -> ReportDraft:
    refs = ctx.get("refs") or []
    cite = f" [{refs[0]}]" if refs else ""
    return ReportDraft(
        title=f"[DEMO] Report: {_topic(ctx)}",
        executive_summary=f"[DEMO] This report was produced from fixed templates to demonstrate "
                          f"the AgentFlow pipeline{cite}. It contains no real findings.",
        methodology="[DEMO] Template methodology.",
        sections=[ReportSection(heading=h, body_markdown=f"[DEMO] Template section{cite}.")
                  for h in (ctx.get("outline") or ["[DEMO] Findings"])],
        conclusion="[DEMO] Configure ANTHROPIC_API_KEY and TAVILY_API_KEY for genuine execution.",
        limitations=["[DEMO] No AI model or web search was used."])
