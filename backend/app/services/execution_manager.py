"""Runs task executions in the background, pauses/resumes them for approvals, cancels them.

Design: in-process asyncio tasks bounded by a semaphore (see docs/architecture.md D2). Graph
state is checkpointed per task (thread_id = task id), so approval pauses survive restarts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.types import Command
from sqlalchemy import select, update

from app.agents.runtime import SourceRegistry, TaskRuntime
from app.config.settings import Settings
from app.database.session import session_factory
from app.graph.builder import build_graph
from app.graph.runner import safe_error
from app.llm.base import LLMProvider
from app.llm.factory import ProviderSet, provider_set_for_mode
from app.llm.registry import ModelRegistry
from app.models.agent_config import AgentConfig
from app.models.approval import ApprovalRequest
from app.models.task import Task, TaskStatus
from app.services.agent_config_service import resolve_effective
from app.services.budget import DEFAULT_PROFILES, BudgetGuard, PricingTable, TaskLimits
from app.services.db_lock import db_write_lock
from app.services.events import TERMINAL_EVENT, EventBus
from app.services.execution_store import ExecutionStore
from app.tools.search import SearchProvider
from app.utils.errors import AppError, ConflictError, NotFoundError

log = logging.getLogger(__name__)
HARD_TIMEOUT_GRACE_S = 120


class ExecutionManager:
    def __init__(self, settings: Settings, bus: EventBus, *, checkpointer: Any,
                 registry: ModelRegistry, live_llms: dict[str, LLMProvider],
                 live_search: SearchProvider | None, pricing: PricingTable | None = None) -> None:
        self.settings = settings
        self.bus = bus
        self.store = ExecutionStore()
        self.graph = build_graph(checkpointer)
        self.registry = registry
        self.live_llms = live_llms
        self.live_search = live_search
        self.pricing = pricing or PricingTable.load(settings.pricing_file)
        self._sem = asyncio.Semaphore(settings.max_concurrent_tasks)
        self._running: dict[str, asyncio.Task] = {}  # latest run per task
        self._all: set[asyncio.Task] = set()          # every live run (for shutdown)

    # ------------------------------------------------------------------ public API
    def is_running(self, task_id: str) -> bool:
        t = self._running.get(task_id)
        return t is not None and not t.done()

    async def start(self, task_id: str) -> None:
        self._launch(task_id, resume=None)

    async def resume(self, task_id: str, decision: dict) -> None:
        """Resume a paused run. Never blocks and never fails after a decision was recorded: if the
        paused run is still unwinding (writing its checkpoint), the resumed run is chained behind it."""
        self._launch(task_id, resume=decision)

    async def cancel(self, task_id: str) -> None:
        task = await self.store.get_task(task_id)
        if task is None:
            raise NotFoundError("Task not found.")
        if task.status in TaskStatus.TERMINAL:
            raise ConflictError(f"Task already {task.status}.")
        handle = self._running.get(task_id)
        if handle and not handle.done():
            # Safe even while a paused run is unwinding: terminal states are write-once and an
            # unwinding run never re-marks a task as awaiting approval once it has ended.
            handle.cancel()
            await asyncio.wait({handle}, timeout=10)
        # Whatever happened above, guarantee a terminal state (idempotent).
        task = await self.store.get_task(task_id)
        if task is not None and task.status not in TaskStatus.TERMINAL:
            await self.store.expire_pending_approvals(task_id)
            await self._finish(task_id, TaskStatus.CANCELLED, "cancelled", "Cancelled by user.")

    async def recover_after_restart(self) -> int:
        """Runs that were mid-flight when the process died are marked interrupted (honestly)."""
        async with db_write_lock(), session_factory()() as s:
            res = await s.execute(
                update(Task).where(Task.status.in_([TaskStatus.QUEUED, TaskStatus.PLANNING,
                                                    TaskStatus.RUNNING]))
                .values(status=TaskStatus.INTERRUPTED, error_code="interrupted",
                        error_message="The server restarted while this task was running.",
                        finished_at=datetime.now(UTC)))
            await s.commit()
            return res.rowcount or 0

    async def shutdown(self) -> None:
        handles = set(self._all)
        for t in handles:
            if not t.done():
                t.cancel()
        await asyncio.gather(*handles, return_exceptions=True)

    # ------------------------------------------------------------------ internals
    def _launch(self, task_id: str, resume: dict | None) -> None:
        prev = self._running.get(task_id)
        handle = asyncio.create_task(self._run_after(prev, task_id, resume),
                                     name=f"agentflow-{task_id}")
        self._running[task_id] = handle
        self._all.add(handle)

        def _done(t: asyncio.Task) -> None:
            self._all.discard(t)
            if self._running.get(task_id) is t:
                self._running.pop(task_id, None)

        handle.add_done_callback(_done)

    async def _run_after(self, prev: asyncio.Task | None, task_id: str, resume: dict | None) -> None:
        """Serialise runs of one task: wait for the previous run (without inheriting its errors or
        cancelling it), then run. Our own cancellation still propagates from `asyncio.wait`."""
        if prev is not None and not prev.done():
            await asyncio.wait({prev})
        await self._run(task_id, resume)

    async def _build_runtime(self, task: Task) -> TaskRuntime:
        providers: ProviderSet = provider_set_for_mode(task.mode, self.settings, self.live_llms,
                                                       self.live_search)
        async with session_factory()() as s:
            rows = (await s.scalars(select(AgentConfig))).all()
        configs = {r.key: resolve_effective(r, self.settings) for r in rows}
        for key, override in (task.agent_overrides or {}).items():
            if key in configs and "enabled" in override and key not in ("planner", "research", "report"):
                configs[key] = configs[key].__class__(**{**configs[key].__dict__,
                                                         "enabled": bool(override["enabled"])})
        if task.mode == "live":
            for cfg in configs.values():
                if not cfg.enabled:
                    continue
                check = await self.registry.validate(cfg.provider, cfg.model)
                if check.status in ("invalid", "provider_not_configured"):
                    raise AppError(f"{cfg.key} agent: {check.message}", code="invalid_model")
                if check.status == "unverified":
                    log.warning("Model %s for %s could not be verified", cfg.model, cfg.key)

        limits = TaskLimits.model_validate(task.limits)
        final_reserve_cost = None
        if task.mode == "live" and limits.max_cost_usd is not None:
            # A cost cap must be enforceable: every enabled model and the search tool need prices.
            unpriced = [f"{c.key} ({c.provider}:{c.model})" for c in configs.values()
                        if c.enabled and self.pricing.price(c.provider, c.model or "") is None]
            if self.pricing.search_cost(providers.search.name, 1.0) is None:
                unpriced.append(f"web search ({providers.search.name})")
            if unpriced:
                raise AppError("A cost limit is set, but pricing is not configured for: "
                               + ", ".join(unpriced) + ". Add them to the pricing file "
                               "(PRICING_FILE) or remove the cost limit.",
                               code="cost_limit_unenforceable")
            rep = configs["report"]
            p_in, p_out = DEFAULT_PROFILES["report"]
            final_reserve_cost = self.pricing.worst_case_cost(rep.provider, rep.model or "",
                                                              p_in, p_out)
        budget = BudgetGuard(limits, self.pricing, final_reserve_cost_usd=final_reserve_cost)
        used_s = float((task.runtime_state or {}).get("active_seconds", 0.0))
        budget.restore(await self.store.per_agent_usage(task.id), elapsed_s=used_s)
        sources = SourceRegistry(persist=lambda rec: self.store.save_source(task.id, rec))
        sources.restore(await self.store.load_sources(task.id))

        async def emit(type_: str, message: str, payload: dict) -> None:
            await self.bus.publish(task.id, type_, message, payload)

        return TaskRuntime(task_id=task.id, mode=task.mode, objective=task.prompt,
                           settings=self.settings, providers=providers, budget=budget,
                           agent_configs=configs, sources=sources, emit=emit, store=self.store,
                           execution_mode=task.execution_mode, approval_mode=task.approval_mode,
                           extra={"bus": self.bus},
                           search_slots=asyncio.Semaphore(
                               1 if task.execution_mode == "sequential"
                               else self.settings.max_parallel_searches))

    async def _run(self, task_id: str, resume: dict | None) -> None:
        started = time.monotonic()
        rt: TaskRuntime | None = None
        async with self._sem:
            task = await self.store.get_task(task_id)
            if task is None or (resume is not None and task.status in TaskStatus.TERMINAL):
                return  # e.g. cancelled while the paused run was still unwinding
            try:
                rt = await self._build_runtime(task)
                if resume is None:
                    await self.store.update_task(task_id, status=TaskStatus.PLANNING,
                                                 started_at=datetime.now(UTC))
                    await self.bus.publish(task_id, "task_started",
                                           f"Execution started in {task.mode.upper()} mode.",
                                           {"mode": task.mode, "execution_mode": task.execution_mode,
                                            "approval_mode": task.approval_mode,
                                            "limits": task.limits})
                    graph_input: Any = {"objective": task.prompt}
                else:
                    await self.store.update_task(task_id, status=TaskStatus.RUNNING)
                    graph_input = Command(resume=resume)
                remaining = max(30.0, rt.budget.limits.timeout_seconds - rt.budget.elapsed_s())
                config = {"configurable": {"thread_id": task_id}, "recursion_limit": 200}
                async with asyncio.timeout(remaining + HARD_TIMEOUT_GRACE_S):
                    result = await self.graph.ainvoke(graph_input, config, context=rt)
                await self._handle_result(task_id, result, rt)
            except asyncio.CancelledError:
                await self.store.expire_pending_approvals(task_id)
                await self._finish(task_id, TaskStatus.CANCELLED, "cancelled", "Cancelled by user.", rt)
                raise
            except TimeoutError:
                await self._finish(task_id, TaskStatus.FAILED, "timeout",
                                   "Execution exceeded its time limit.", rt)
            except AppError as exc:
                await self._finish(task_id, TaskStatus.FAILED, exc.code, exc.message, rt)
            except Exception as exc:
                code, msg = safe_error(exc)
                if code == "internal_error":
                    log.exception("Task %s failed", task_id)
                await self._finish(task_id, TaskStatus.FAILED, code, msg, rt)
            finally:
                await self._record_active_time(task_id, time.monotonic() - started)

    async def _handle_result(self, task_id: str, result: dict, rt: TaskRuntime) -> None:
        if result.get("__interrupt__"):
            # The decision may already be recorded (or the task cancelled) while this run unwound.
            if await self.store.mark_awaiting_if_pending(task_id):
                await self.bus.publish(task_id, "paused", "Waiting for human approval.", {})
            return
        if result.get("rejected"):
            await self._finish(task_id, TaskStatus.REJECTED, "rejected",
                               result.get("rejection_comment") or "Rejected at an approval checkpoint.", rt)
            return
        if result.get("report"):
            await self.store.update_task(task_id, budget_exhausted=bool(result.get("budget_exhausted")))
            await self._finish(task_id, TaskStatus.COMPLETED, None, None, rt)
            return
        await self._finish(task_id, TaskStatus.FAILED, "no_report", "The run ended without a report.", rt)

    async def _finish(self, task_id: str, status: str, code: str | None, message: str | None,
                      rt: TaskRuntime | None = None) -> None:
        values: dict[str, Any] = {"status": status, "error_code": code, "error_message": message,
                                  "finished_at": datetime.now(UTC), "current_stage": "finished"}
        if not await self.store.finish_task_if_active(task_id, **values):
            return  # already terminal: exactly one terminal state and one task_finished event
        exec_status = "cancelled" if status == TaskStatus.CANCELLED else "aborted"
        await self.store.close_open_executions(
            task_id, exec_status, code or exec_status,
            "Task was cancelled." if status == TaskStatus.CANCELLED else
            f"Task ended ({status}) before this agent finished.")
        if rt is not None:
            await self.store.sync_usage(task_id, rt.budget.snapshot())
        await self.bus.publish(task_id, TERMINAL_EVENT,
                               {"completed": "Execution completed.", "failed": f"Execution failed: {message}",
                                "cancelled": "Execution cancelled.",
                                "rejected": f"Execution rejected: {message}"}.get(status, status),
                               {"status": status, "error_code": code})

    async def _record_active_time(self, task_id: str, seconds: float) -> None:
        task = await self.store.get_task(task_id)
        if task is None:
            return
        state = dict(task.runtime_state or {})
        state["active_seconds"] = round(float(state.get("active_seconds", 0.0)) + seconds, 2)
        await self.store.update_task(task_id, runtime_state=state)

    async def expire_stale_approvals(self) -> int:
        """Pending approvals older than APPROVAL_TIMEOUT_HOURS expire; their task is cancelled."""
        cutoff = datetime.now(UTC) - timedelta(hours=self.settings.approval_timeout_hours)
        async with session_factory()() as s:
            pending = (await s.scalars(select(ApprovalRequest)
                                       .where(ApprovalRequest.status == "pending"))).all()
        stale = [r for r in pending
                 if (r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)) < cutoff]
        for req in stale:
            if self.is_running(req.task_id):
                continue
            await self.store.expire_pending_approvals(req.task_id)
            task = await self.store.get_task(req.task_id)
            if task is not None and task.status not in TaskStatus.TERMINAL:
                await self._finish(req.task_id, TaskStatus.CANCELLED, "approval_timeout",
                                   f"No approval decision within {self.settings.approval_timeout_hours}h.")
        return len(stale)

    async def decide_approval(self, approval_id: int, approved: bool, comment: str | None) -> ApprovalRequest:
        await self.expire_stale_approvals()
        async with db_write_lock(), session_factory()() as s:
            req = await s.get(ApprovalRequest, approval_id)
            if req is None:
                raise NotFoundError("Approval request not found.")
            if req.status != "pending":
                raise ConflictError(f"Approval already {req.status}.")
            task = await s.get(Task, req.task_id)
            if task is None or task.status != TaskStatus.AWAITING_APPROVAL:
                raise ConflictError("Task is not awaiting approval.")
            req.status = "approved" if approved else "rejected"
            req.comment = (comment or "")[:1000] or None
            req.decided_at = datetime.now(UTC)
            task.status = TaskStatus.RUNNING  # reflect the decision immediately
            await s.commit()
            await s.refresh(req)
        # Recorded here, where the decision is made: on resume the graph node finds the request already
        # decided and returns without reaching its interrupt, so it cannot publish this event itself.
        title = (req.payload or {}).get("title") or "Approval"
        await self.bus.publish(req.task_id, "approval_decided",
                               f"{title}: {'approved' if approved else 'rejected'}",
                               {"approval_id": req.id, "kind": req.kind, "approved": approved,
                                "comment": req.comment})
        await self.resume(req.task_id, {"approved": approved, "comment": req.comment})
        return req
