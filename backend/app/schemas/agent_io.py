"""Structured outputs for every agent. These are the contracts the LLM must satisfy.

Kept deliberately flat (no unions / recursive types) so they translate cleanly to the JSON
schema subsets supported by provider structured-output features. Numeric bounds are also
re-validated client-side by Pydantic.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Importance = Literal["high", "medium", "low"]
Verdict = Literal["supported", "partially_supported", "unsupported", "conflicting", "unverifiable"]


# ------------------------------------------------------------------ planner
class PlannedSubtask(BaseModel):
    id: str = Field(description="Short id such as 't1', 't2'. Unique within the plan.")
    title: str = Field(description="Imperative title, e.g. 'Identify AI companies hiring freshers in India'.")
    description: str = Field(description="What to find out and why it matters for the objective.")
    queries: list[str] = Field(description="1-3 specific web search queries for this subtask.")
    depends_on: list[str] = Field(default_factory=list,
                                  description="Ids of subtasks whose results this one needs first.")
    expected_output: str = Field(description="What a complete answer for this subtask contains.")


class ExecutionPlan(BaseModel):
    objective_summary: str = Field(description="One-sentence restatement of the user's objective.")
    desired_outcome: str = Field(description="What the final deliverable should enable the user to do.")
    success_criteria: list[str] = Field(description="Checkable criteria for a complete answer.")
    subtasks: list[PlannedSubtask] = Field(description="Research subtasks forming a DAG.")
    analysis_focus: list[str] = Field(description="Comparisons/calculations the analysis should perform.")
    report_outline: list[str] = Field(description="Section headings for the final report.")
    assumptions: list[str] = Field(default_factory=list,
                                   description="Interpretations made where the request was ambiguous.")


# ------------------------------------------------------------------ research
class ExtractedFact(BaseModel):
    statement: str = Field(description="One atomic, self-contained fact stated in the sources.")
    source_refs: list[str] = Field(description="Ids (e.g. 'S3') of the sources that state this fact.")
    evidence_quote: str = Field(description="Short verbatim excerpt (<=300 chars) from a cited source.")
    as_of: str | None = Field(default=None, description="Date/period the fact refers to, if stated.")


class SourceNote(BaseModel):
    ref: str
    note: str = Field(description="Why the source is unreliable, outdated, promotional or suspicious.")


class ResearchResult(BaseModel):
    facts: list[ExtractedFact]
    coverage_notes: str = Field(description="What was found and what is still missing for this subtask.")
    follow_up_queries: list[str] = Field(default_factory=list,
                                         description="Up to 2 queries to fill clear gaps; empty if covered.")
    source_notes: list[SourceNote] = Field(default_factory=list)


# ------------------------------------------------------------------ analysis
class Finding(BaseModel):
    title: str
    detail: str
    source_refs: list[str]


class ComparisonRow(BaseModel):
    label: str = Field(description="Row entity, e.g. a company name.")
    cells: list[str] = Field(description="One value per column; use 'Not found' when unknown.")
    source_refs: list[str]


class ComparisonTable(BaseModel):
    title: str
    columns: list[str] = Field(description="Column headings (excluding the row label).")
    rows: list[ComparisonRow]


class CalculationRequest(BaseModel):
    label: str = Field(description="What is being computed.")
    expression: str = Field(description="Arithmetic only: numbers, + - * / ** ( ), avg(), sum(), "
                                        "min(), max(), round(), sqrt().")
    unit: str = ""
    source_refs: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    id: str = Field(description="Short id such as 'c1'.")
    statement: str = Field(description="A specific, checkable claim the report will rely on.")
    source_refs: list[str]
    importance: Importance


class AnalysisResult(BaseModel):
    summary: str
    findings: list[Finding]
    comparisons: list[ComparisonTable] = Field(default_factory=list)
    calculations: list[CalculationRequest] = Field(default_factory=list)
    claims: list[Claim]
    gaps: list[str] = Field(default_factory=list, description="Important questions the evidence does not answer.")


# ------------------------------------------------------------------ verification
class ClaimVerdict(BaseModel):
    claim_id: str
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0, description="Calibrated probability the claim is true "
                                                          "given ONLY the evidence shown.")
    reasoning: str = Field(description="Which evidence supports or contradicts the claim and why.")
    supporting_refs: list[str] = Field(default_factory=list)
    conflicting_refs: list[str] = Field(default_factory=list)


class VerificationReport(BaseModel):
    verdicts: list[ClaimVerdict]
    sufficient: bool = Field(description="True if verified evidence is enough to meet the success criteria.")
    sufficiency_reasoning: str
    gap_queries: list[str] = Field(default_factory=list,
                                   description="Up to 4 web search queries that would close the most important gaps.")


# ------------------------------------------------------------------ report
class ReportSection(BaseModel):
    heading: str
    body_markdown: str = Field(description="Markdown body. Cite sources inline as [S1], [S2].")


class ReportDraft(BaseModel):
    title: str
    executive_summary: str = Field(description="5-8 sentences with inline [S#] citations.")
    methodology: str = Field(description="How the question was approached (the system appends run statistics).")
    sections: list[ReportSection]
    conclusion: str
    limitations: list[str] = Field(description="Including unsupported/conflicting claims and coverage gaps.")
