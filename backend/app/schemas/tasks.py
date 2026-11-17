from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class LimitOverrides(BaseModel):
    max_total_tokens: int | None = Field(default=None, ge=1_000)
    max_llm_calls: int | None = Field(default=None, ge=1, le=500)
    max_tool_calls: int | None = Field(default=None, ge=0, le=1000)
    timeout_seconds: int | None = Field(default=None, ge=30, le=7200)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_iterations: int | None = Field(default=None, ge=1, le=5)
    max_subtasks: int | None = Field(default=None, ge=1, le=12)
    max_searches_per_subtask: int | None = Field(default=None, ge=1, le=8)


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=10, max_length=4000)
    mode: Literal["live", "demo"] = "live"
    execution_mode: Literal["parallel", "sequential"] = "parallel"
    approval_mode: Literal["none", "plan", "tools", "all"] = "none"
    limits: LimitOverrides = Field(default_factory=LimitOverrides)
    disabled_agents: list[Literal["analysis", "verification"]] = Field(default_factory=list)

    @field_validator("prompt")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.replace("\x00", "").strip()
        if len(v) < 10:
            raise ValueError("Describe the task in at least 10 characters.")
        return v


class SubtaskOut(ORMModel):
    id: int
    plan_key: str
    title: str
    description: str
    agent_key: str
    depends_on: list[str]
    queries: list[str]
    status: str
    iteration: int
    error: str | None


class AgentExecutionOut(ORMModel):
    id: int
    agent_key: str
    subtask_id: int | None
    iteration: int
    status: str
    mode: str
    provider: str | None
    model: str | None
    input_tokens: int
    output_tokens: int
    llm_calls: int
    tool_calls: int
    cost_usd: float | None
    duration_ms: int | None
    error_code: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None
    output: dict[str, Any] | None = None


class TaskSummary(ORMModel):
    id: str
    title: str
    prompt: str
    mode: str
    status: str
    execution_mode: str
    approval_mode: str
    current_stage: str | None
    progress_done: int
    progress_total: int
    iterations: int
    input_tokens: int
    output_tokens: int
    llm_calls: int
    tool_calls: int
    cost_usd: float | None
    estimated_cost_usd: float | None
    budget_exhausted: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    report_id: int | None = None


class TaskDetail(TaskSummary):
    limits: dict[str, Any]
    subtasks: list[SubtaskOut]
    agent_executions: list[AgentExecutionOut]
    pending_approval_id: int | None = None


class TaskList(BaseModel):
    items: list[TaskSummary]
    total: int
    page: int
    page_size: int
