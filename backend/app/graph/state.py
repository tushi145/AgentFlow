"""Typed shared state for the AgentFlow LangGraph workflow.

Only JSON-serialisable data lives here (it is checkpointed for approval pauses/resume).
Live objects (clients, budget guard, source registry) are in the per-run `TaskRuntime` context.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_subtasks(left: dict[str, dict] | None, right: dict[str, dict] | None) -> dict[str, dict]:
    """Deep-merge per-subtask updates so parallel research branches don't overwrite each other."""
    out = dict(left or {})
    for key, upd in (right or {}).items():
        out[key] = {**out.get(key, {}), **upd}
    return out


def last_true(left: bool | None, right: bool | None) -> bool:
    return bool(left) or bool(right)


def latest_note(left: str | None, right: str | None) -> str | None:
    """Latest non-empty note wins; accepts concurrent writes from parallel branches."""
    return right if right else left


class AgentFlowState(TypedDict, total=False):
    objective: str
    plan: dict[str, Any] | None
    subtasks: Annotated[dict[str, dict], merge_subtasks]
    pending_batch: list[str]
    facts: Annotated[list[dict], operator.add]
    coverage_notes: Annotated[list[str], operator.add]
    analysis: dict[str, Any] | None
    calculations: list[dict]
    claims: list[dict]
    verdicts: list[dict]
    sufficient: bool
    gap_queries: list[str]
    retrieval_method: str | None
    iteration: int
    budget_exhausted: Annotated[bool, last_true]
    budget_note: Annotated[str | None, latest_note]
    rejected: Annotated[bool, last_true]
    rejection_comment: str | None
    errors: Annotated[list[dict], operator.add]
    report: dict[str, Any] | None
    evaluation: dict[str, Any] | None


class ResearchInput(TypedDict):
    """Payload delivered to each parallel research branch via `Send`."""

    subtask: dict[str, Any]
    iteration: int
    context_facts: list[str]
