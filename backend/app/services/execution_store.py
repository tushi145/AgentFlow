"""Persistence for everything an execution produces. Each call uses a short-lived session."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update

from app.agents.base import RunStats
from app.agents.runtime import SourceRecord
from app.database.session import session_factory
from app.models import (AgentExecution, ApprovalRequest, EvaluationResult, Report,
                        ResearchSource, Subtask, Task, VerificationResult)
from app.services.db_lock import db_write_lock

MAX_JSON_CHARS = 60_000


def _now() -> datetime:
    return datetime.now(UTC)


def _clip(obj: Any) -> Any:
    """Bound stored JSON size without breaking structure."""
    import json
    text = json.dumps(obj, default=str)
    if len(text) <= MAX_JSON_CHARS:
        return json.loads(text)
    return {"truncated": True, "preview": text[:MAX_JSON_CHARS]}


class ExecutionStore:
    # ------------------------------------------------------------- tasks
    async def get_task(self, task_id: str) -> Task | None:
        async with session_factory()() as s:
            return await s.get(Task, task_id)

    async def update_task(self, task_id: str, **values: Any) -> None:
        async with db_write_lock(), session_factory()() as s:
            await s.execute(update(Task).where(Task.id == task_id).values(**values))
            await s.commit()

    async def mark_awaiting_if_pending(self, task_id: str) -> bool:
        """Atomically set AWAITING_APPROVAL only if a decision is really still outstanding.

        A paused run unwinds *after* its approval was requested; a decision may already have been
        recorded (status RUNNING) or the task cancelled meanwhile. Never overwrite those.
        """
        from app.models.task import TaskStatus

        async with db_write_lock(), session_factory()() as s:
            task = await s.get(Task, task_id)
            if task is None or task.status in TaskStatus.TERMINAL:
                return False
            pending = await s.scalar(select(func.count()).select_from(ApprovalRequest).where(
                ApprovalRequest.task_id == task_id, ApprovalRequest.status == "pending"))
            if not pending:
                return False
            task.status = TaskStatus.AWAITING_APPROVAL
            await s.commit()
            return True

    async def finish_task_if_active(self, task_id: str, **values: Any) -> bool:
        """Write a terminal state once. Returns False if the task already ended (idempotent)."""
        from app.models.task import TaskStatus

        async with db_write_lock(), session_factory()() as s:
            task = await s.get(Task, task_id)
            if task is None or task.status in TaskStatus.TERMINAL:
                return False
            for k, v in values.items():
                setattr(task, k, v)
            await s.commit()
            return True

    async def add_progress(self, task_id: str, done: int = 0, total: int = 0) -> tuple[int, int]:
        async with db_write_lock(), session_factory()() as s:
            t = await s.get(Task, task_id)
            assert t is not None
            t.progress_done = min(t.progress_done + done, t.progress_total + total)
            t.progress_total += total
            await s.commit()
            return t.progress_done, t.progress_total

    async def sync_usage(self, task_id: str, snapshot: Any) -> None:
        await self.update_task(task_id, input_tokens=snapshot.input_tokens,
                               output_tokens=snapshot.output_tokens, llm_calls=snapshot.llm_calls,
                               tool_calls=snapshot.tool_calls,
                               cost_usd=snapshot.cost_usd if snapshot.cost_complete else None)

    # ------------------------------------------------------------- subtasks
    async def save_subtasks(self, task_id: str, subtasks: list[dict], iteration: int) -> None:
        async with db_write_lock(), session_factory()() as s:
            for st in subtasks:
                s.add(Subtask(task_id=task_id, plan_key=st["id"], title=st["title"][:300],
                              description=st.get("description", ""), agent_key="research",
                              depends_on=st.get("depends_on", []), queries=st.get("queries", []),
                              status="pending", iteration=iteration))
            await s.commit()

    async def update_subtask(self, task_id: str, plan_key: str, **values: Any) -> int | None:
        async with db_write_lock(), session_factory()() as s:
            row = await s.scalar(select(Subtask).where(Subtask.task_id == task_id,
                                                       Subtask.plan_key == plan_key))
            if row is None:
                return None
            for k, v in values.items():
                setattr(row, k, _clip(v) if k == "result" else v)
            await s.commit()
            return row.id

    async def subtask_id(self, task_id: str, plan_key: str) -> int | None:
        async with session_factory()() as s:
            return await s.scalar(select(Subtask.id).where(Subtask.task_id == task_id,
                                                           Subtask.plan_key == plan_key))

    # ------------------------------------------------------------- agent executions
    async def start_agent_execution(self, task_id: str, agent_key: str, mode: str, *,
                                    iteration: int, subtask_id: int | None, attempt: int,
                                    input_summary: dict) -> int:
        async with db_write_lock(), session_factory()() as s:
            row = AgentExecution(task_id=task_id, agent_key=agent_key, mode=mode,
                                 iteration=iteration, subtask_id=subtask_id, attempt=attempt,
                                 status="running", input_summary=_clip(input_summary),
                                 started_at=_now())
            s.add(row)
            await s.commit()
            return row.id

    async def finish_agent_execution(self, exec_id: int, stats: RunStats, *, status: str,
                                     output: Any = None, error_code: str | None = None,
                                     error_message: str | None = None) -> None:
        async with db_write_lock(), session_factory()() as s:
            row = await s.get(AgentExecution, exec_id)
            if row is None:
                return
            row.status, row.finished_at = status, _now()
            row.provider, row.model = stats.provider, stats.model
            row.input_tokens, row.output_tokens = stats.usage.input_tokens, stats.usage.output_tokens
            row.cache_read_tokens = stats.usage.cache_read_tokens
            row.llm_calls, row.tool_calls = stats.llm_calls, stats.tool_calls
            row.cost_usd = None if stats.cost_usd is None else round(stats.cost_usd, 6)
            row.duration_ms = stats.duration_ms
            row.output = _clip(output) if output is not None else None
            row.error_code, row.error_message = error_code, (error_message or None)
            await s.commit()

    async def close_open_executions(self, task_id: str, status: str, code: str, message: str) -> int:
        """Any execution still 'running' when its task ends is closed with an explicit reason."""
        async with db_write_lock(), session_factory()() as s:
            res = await s.execute(update(AgentExecution).where(
                AgentExecution.task_id == task_id, AgentExecution.status == "running")
                .values(status=status, error_code=code, error_message=message, finished_at=_now()))
            await s.commit()
            return res.rowcount or 0

    async def agent_timings(self, task_id: str) -> dict[str, dict]:
        """Per-agent latency from recorded executions: count, total and max duration (ms)."""
        async with session_factory()() as s:
            rows = (await s.execute(select(AgentExecution.agent_key, AgentExecution.duration_ms)
                                    .where(AgentExecution.task_id == task_id,
                                           AgentExecution.duration_ms.is_not(None)))).all()
        out: dict[str, dict] = {}
        for key, ms in rows:
            a = out.setdefault(key, {"runs": 0, "total_ms": 0, "max_ms": 0})
            a["runs"] += 1
            a["total_ms"] += int(ms)
            a["max_ms"] = max(a["max_ms"], int(ms))
        return out

    async def per_agent_usage(self, task_id: str) -> dict[str, dict]:
        async with session_factory()() as s:
            rows = (await s.execute(
                select(AgentExecution.agent_key, func.sum(AgentExecution.input_tokens),
                       func.sum(AgentExecution.output_tokens),
                       func.sum(AgentExecution.cache_read_tokens),
                       func.sum(AgentExecution.llm_calls), func.sum(AgentExecution.tool_calls),
                       func.sum(AgentExecution.cost_usd),
                       func.count(AgentExecution.id) - func.count(AgentExecution.cost_usd))
                .where(AgentExecution.task_id == task_id)
                .group_by(AgentExecution.agent_key))).all()
        return {k: {"input_tokens": i or 0, "output_tokens": o or 0, "cache_read_tokens": c or 0,
                    "llm_calls": lc or 0, "tool_calls": tc or 0,
                    "cost_usd": None if unknown else (cost or 0.0)}
                for k, i, o, c, lc, tc, cost, unknown in rows}

    # ------------------------------------------------------------- sources / verification
    async def save_source(self, task_id: str, rec: SourceRecord) -> None:
        async with db_write_lock(), session_factory()() as s:
            s.add(ResearchSource(
                task_id=task_id, ref=rec.ref, url=rec.url[:2048], title=rec.title, domain=rec.domain,
                snippet=rec.snippet, content_excerpt=rec.content, search_query=rec.search_query,
                provider=rec.provider, relevance_score=rec.relevance_score,
                injection_suspected=rec.injection_suspected, injection_rules=rec.injection_rules,
                mode="demo" if rec.is_demo else "live"))
            await s.commit()

    async def load_sources(self, task_id: str) -> list[SourceRecord]:
        async with session_factory()() as s:
            rows = (await s.scalars(select(ResearchSource).where(ResearchSource.task_id == task_id)
                                    .order_by(ResearchSource.id))).all()
        return [SourceRecord(ref=r.ref, url=r.url, title=r.title, domain=r.domain, snippet=r.snippet,
                             content=r.content_excerpt, search_query=r.search_query,
                             provider=r.provider, relevance_score=r.relevance_score,
                             injection_suspected=r.injection_suspected,
                             injection_rules=list(r.injection_rules or []),
                             is_demo=r.mode == "demo") for r in rows]

    async def save_verifications(self, task_id: str, iteration: int, verdicts: list[dict]) -> None:
        async with db_write_lock(), session_factory()() as s:
            await s.execute(delete(VerificationResult).where(
                VerificationResult.task_id == task_id, VerificationResult.iteration == iteration))
            for v in verdicts:
                s.add(VerificationResult(
                    task_id=task_id, iteration=iteration, claim=v["claim"], verdict=v["verdict"],
                    confidence=v["confidence"], reasoning=v.get("reasoning", ""),
                    supporting_refs=v.get("supporting_refs", []),
                    conflicting_refs=v.get("conflicting_refs", [])))
            await s.commit()

    # ------------------------------------------------------------- report / evaluation
    async def save_report(self, task_id: str, *, title: str, summary: str, markdown: str,
                          pdf_path: str | None, source_count: int, avg_confidence: float | None,
                          citation_violations: int, mode: str) -> int:
        async with db_write_lock(), session_factory()() as s:
            existing = await s.scalar(select(Report).where(Report.task_id == task_id))
            row = existing or Report(task_id=task_id, title=title, markdown=markdown, mode=mode)
            row.title, row.summary, row.markdown, row.pdf_path = title[:300], summary, markdown, pdf_path
            row.source_count, row.avg_confidence = source_count, avg_confidence
            row.citation_violations, row.mode = citation_violations, mode
            s.add(row)
            await s.commit()
            return row.id

    async def save_evaluation(self, task_id: str, metrics: dict) -> None:
        async with db_write_lock(), session_factory()() as s:
            existing = await s.scalar(select(EvaluationResult).where(EvaluationResult.task_id == task_id))
            row = existing or EvaluationResult(task_id=task_id, metrics={})
            row.metrics = _clip(metrics)
            s.add(row)
            await s.commit()

    # ------------------------------------------------------------- approvals
    async def get_or_create_approval(self, task_id: str, key: str, kind: str,
                                     payload: dict) -> ApprovalRequest:
        """Idempotent: a node re-executed on resume finds its existing approval row."""
        async with db_write_lock(), session_factory()() as s:
            rows = (await s.scalars(select(ApprovalRequest).where(
                ApprovalRequest.task_id == task_id, ApprovalRequest.kind == kind))).all()
            for r in rows:
                if (r.payload or {}).get("key") == key:
                    return r
            row = ApprovalRequest(task_id=task_id, kind=kind, payload=_clip({**payload, "key": key}),
                                  status="pending")
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row

    async def expire_pending_approvals(self, task_id: str) -> None:
        async with db_write_lock(), session_factory()() as s:
            await s.execute(update(ApprovalRequest).where(
                ApprovalRequest.task_id == task_id, ApprovalRequest.status == "pending")
                .values(status="expired", decided_at=_now()))
            await s.commit()
