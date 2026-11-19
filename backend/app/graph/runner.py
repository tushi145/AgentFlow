"""Runs one agent invocation and records it faithfully (row in agent_executions + events)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from app.agents.base import AgentOutput, BaseAgent
from app.agents.runtime import TaskRuntime
from app.llm.base import LLMError
from app.services.budget import BudgetExceeded
from app.utils.logging import redact

log = logging.getLogger(__name__)
A = TypeVar("A", bound=BaseAgent)


def _serialisable(result: Any) -> Any:
    if isinstance(result, BaseModel):
        return result.model_dump()
    if hasattr(result, "__dataclass_fields__"):
        return {k: getattr(result, k) for k in result.__dataclass_fields__}
    return result


def safe_error(exc: BaseException) -> tuple[str, str]:
    """(code, message) safe to store and show — never raw tracebacks or secrets."""
    # Messages are stored on the task, published as events and returned by the API: redact them
    # in case a provider ever echoes a credential in its error text.
    if isinstance(exc, BudgetExceeded):
        return f"budget_{exc.limit}", redact(str(exc))
    if isinstance(exc, LLMError):
        return exc.code, redact(str(exc))[:500]
    if isinstance(exc, PermissionError):
        return "tool_not_allowed", redact(str(exc))[:300]
    return "internal_error", f"Unexpected {type(exc).__name__} in agent execution."


async def run_agent(rt: TaskRuntime, agent: A, call: Callable[[A], Awaitable[AgentOutput]], *,
                    iteration: int, input_summary: dict, subtask_key: str | None = None,
                    label: str | None = None) -> AgentOutput:
    store = rt.store
    subtask_id = await store.subtask_id(rt.task_id, subtask_key) if subtask_key else None
    exec_id = await store.start_agent_execution(
        rt.task_id, agent.key, rt.mode, iteration=iteration, subtask_id=subtask_id, attempt=1,
        input_summary=input_summary)
    name = label or agent.spec.name
    await rt.emit("agent_started", f"{name} started.",
                  {"agent": agent.key, "execution_id": exec_id, "subtask_id": subtask_key,
                   "iteration": iteration, "mode": rt.mode,
                   "model": agent.config.model if not rt.is_demo else "demo-template"})
    try:
        out = await call(agent)
    except asyncio.CancelledError:
        await store.finish_agent_execution(exec_id, agent.stats, status="cancelled",
                                           error_code="cancelled", error_message="Task cancelled.")
        raise
    except Exception as exc:  # recorded then re-raised
        code, msg = safe_error(exc)
        if not isinstance(exc, (LLMError, BudgetExceeded, PermissionError)):
            log.exception("Agent %s failed", agent.key)
        await store.finish_agent_execution(exec_id, agent.stats, status="failed",
                                           error_code=code, error_message=msg)
        await store.sync_usage(rt.task_id, rt.budget.snapshot())
        await rt.emit("agent_failed", f"{name} failed: {msg}",
                      {"agent": agent.key, "execution_id": exec_id, "error_code": code,
                       "subtask_id": subtask_key, "iteration": iteration})
        raise
    s = out.stats
    await store.finish_agent_execution(exec_id, s, status="completed",
                                       output={"result": _serialisable(out.result),
                                               **{k: v for k, v in out.extras.items()
                                                  if k not in ("draft",)}})
    await store.sync_usage(rt.task_id, rt.budget.snapshot())
    await rt.emit("agent_completed", f"{name} completed.",
                  {"agent": agent.key, "execution_id": exec_id, "subtask_id": subtask_key,
                   "iteration": iteration, "duration_ms": s.duration_ms,
                   "input_tokens": s.usage.input_tokens, "output_tokens": s.usage.output_tokens,
                   "llm_calls": s.llm_calls, "tool_calls": s.tool_calls, "cost_usd": s.cost_usd,
                   "retries": s.retries, "model": s.model, "is_demo": s.is_demo,
                   "notes": s.notes})
    return out
