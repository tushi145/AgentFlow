from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class SourceOut(ORMModel):
    id: int
    ref: str
    url: str
    title: str
    domain: str
    snippet: str
    search_query: str
    provider: str
    relevance_score: float | None
    injection_suspected: bool
    injection_rules: list[str]
    mode: str
    retrieved_at: datetime


class VerificationOut(ORMModel):
    id: int
    iteration: int
    claim: str
    verdict: str
    confidence: float
    reasoning: str
    supporting_refs: list[str]
    conflicting_refs: list[str]


class ReportSummary(ORMModel):
    id: int
    task_id: str
    title: str
    summary: str
    source_count: int
    avg_confidence: float | None
    citation_violations: int
    mode: str
    created_at: datetime
    has_pdf: bool = False


class ReportDetail(ReportSummary):
    markdown: str
    prompt: str = ""
    sources: list[SourceOut] = []
    verifications: list[VerificationOut] = []


class ApprovalOut(ORMModel):
    id: int
    task_id: str
    kind: str
    payload: dict[str, Any]
    status: str
    comment: str | None
    created_at: datetime
    decided_at: datetime | None
    task_title: str | None = None


class ApprovalDecision(BaseModel):
    approved: bool
    comment: str | None = Field(default=None, max_length=1000)


class UsageOut(BaseModel):
    per_agent: dict[str, dict]
    totals: dict[str, Any]
    limits: dict[str, Any]


class BenchmarkRunOut(ORMModel):
    id: int
    suite: str
    status: str
    provider: str | None
    model: str | None
    metrics: dict[str, Any]
    cost_usd: float | None
    created_at: datetime
