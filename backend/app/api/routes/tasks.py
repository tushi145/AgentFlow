from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import delete, select
from sse_starlette.sse import EventSourceResponse

from app.api.deps import SessionDep, SettingsDep, UserDep, limiter
from app.database.session import session_factory
from app.models import ResearchSource, Subtask, Task, VerificationResult
from app.models.task import TaskStatus
from app.schemas.outputs import SourceOut, UsageOut, VerificationOut
from app.schemas.tasks import (AgentExecutionOut, SubtaskOut, TaskCreate, TaskDetail, TaskList,
                               TaskSummary)
from app.services.events import TERMINAL_EVENT
from app.services.task_service import (create_task, get_task_or_404, list_tasks,
                                       task_detail_parts)
from app.utils.errors import ConflictError

router = APIRouter(prefix="/tasks", tags=["tasks"])
SSE_PING_S = 15


def _summary(task: Task, report_id: int | None) -> TaskSummary:
    out = TaskSummary.model_validate(task)
    out.report_id = report_id
    return out


@router.post("", response_model=TaskSummary, status_code=201)
@limiter.limit("20/minute")
async def create(request: Request, body: TaskCreate, session: SessionDep, settings: SettingsDep,
                 user: UserDep) -> TaskSummary:
    manager = request.app.state.manager
    task = await create_task(session, settings, body, user_id=user.id,
                             live_llms=manager.live_llms, live_search=manager.live_search)
    await manager.start(task.id)
    return _summary(task, None)


@router.get("", response_model=TaskList)
async def list_(session: SessionDep, _: UserDep, status: str | None = None, q: str | None = None,
                mode: str | None = None, page: Annotated[int, Query(ge=1)] = 1,
                page_size: Annotated[int, Query(ge=1, le=100)] = 20) -> TaskList:
    rows, total = await list_tasks(session, status=status, q=q, mode=mode, page=page,
                                   page_size=page_size)
    return TaskList(items=[_summary(t, rid) for t, rid in rows], total=total, page=page,
                    page_size=page_size)


@router.get("/{task_id}", response_model=TaskDetail)
async def detail(task_id: str, session: SessionDep, _: UserDep) -> TaskDetail:
    task = await get_task_or_404(session, task_id)
    subtasks = (await session.scalars(select(Subtask).where(Subtask.task_id == task_id)
                                      .order_by(Subtask.id))).all()
    execs, report_id, pending = await task_detail_parts(session, task_id)
    base = TaskSummary.model_validate(task).model_dump()
    return TaskDetail(**{**base, "report_id": report_id}, limits=task.limits,
                      subtasks=[SubtaskOut.model_validate(s) for s in subtasks],
                      agent_executions=[AgentExecutionOut.model_validate(e) for e in execs],
                      pending_approval_id=pending)


@router.post("/{task_id}/cancel", response_model=TaskSummary)
async def cancel(task_id: str, request: Request, session: SessionDep, _: UserDep) -> TaskSummary:
    task = await get_task_or_404(session, task_id)
    if task.status in TaskStatus.TERMINAL:
        raise ConflictError(f"Task already {task.status}.")
    await request.app.state.manager.cancel(task_id)
    await session.refresh(task)
    return _summary(task, None)


@router.post("/{task_id}/retry", response_model=TaskSummary, status_code=201)
@limiter.limit("20/minute")
async def retry(task_id: str, request: Request, session: SessionDep, settings: SettingsDep,
                user: UserDep) -> TaskSummary:
    """Re-run as a NEW task with the same settings (history of the original is preserved)."""
    old = await get_task_or_404(session, task_id)
    if old.status in TaskStatus.ACTIVE:
        raise ConflictError("Task is still active.")
    manager = request.app.state.manager
    body = TaskCreate(prompt=old.prompt, mode=old.mode, execution_mode=old.execution_mode,  # type: ignore[arg-type]
                      approval_mode=old.approval_mode,  # type: ignore[arg-type]
                      limits={k: v for k, v in old.limits.items()
                              if k in TaskCreate.model_fields["limits"].annotation.model_fields},  # type: ignore[union-attr]
                      disabled_agents=[k for k, v in (old.agent_overrides or {}).items()
                                       if v.get("enabled") is False])
    task = await create_task(session, settings, body, user_id=user.id, parent_task_id=old.id,
                             live_llms=manager.live_llms, live_search=manager.live_search)
    await manager.start(task.id)
    return _summary(task, None)


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: str, session: SessionDep, settings: SettingsDep, _: UserDep) -> None:
    task = await get_task_or_404(session, task_id)
    if task.status in TaskStatus.ACTIVE:
        raise ConflictError("Cancel the task before deleting it.")
    for ext in (".md", ".pdf"):
        path = (settings.reports_dir / f"{task_id}{ext}").resolve()
        if path.parent == settings.reports_dir.resolve() and path.exists():
            path.unlink()
    await session.execute(delete(Task).where(Task.id == task_id))
    await session.commit()


@router.get("/{task_id}/sources", response_model=list[SourceOut])
async def sources(task_id: str, session: SessionDep, _: UserDep) -> list[SourceOut]:
    await get_task_or_404(session, task_id)
    rows = (await session.scalars(select(ResearchSource).where(ResearchSource.task_id == task_id)
                                  .order_by(ResearchSource.id))).all()
    return [SourceOut.model_validate(r) for r in rows]


@router.get("/{task_id}/verifications", response_model=list[VerificationOut])
async def verifications(task_id: str, session: SessionDep, _: UserDep) -> list[VerificationOut]:
    await get_task_or_404(session, task_id)
    rows = (await session.scalars(select(VerificationResult)
                                  .where(VerificationResult.task_id == task_id)
                                  .order_by(VerificationResult.iteration, VerificationResult.id))).all()
    return [VerificationOut.model_validate(r) for r in rows]


@router.get("/{task_id}/usage", response_model=UsageOut)
async def usage(task_id: str, request: Request, session: SessionDep, _: UserDep) -> UsageOut:
    task = await get_task_or_404(session, task_id)
    per_agent = await request.app.state.manager.store.per_agent_usage(task_id)
    return UsageOut(per_agent=per_agent, limits=task.limits, totals={
        "input_tokens": task.input_tokens, "output_tokens": task.output_tokens,
        "llm_calls": task.llm_calls, "tool_calls": task.tool_calls, "cost_usd": task.cost_usd,
        "estimated_cost_usd": task.estimated_cost_usd,
        "cost_note": None if task.cost_usd is not None or task.mode == "demo" else
        "Pricing not configured for at least one model used; cost not computed."})


@router.get("/{task_id}/events")
async def events(task_id: str, request: Request, session: SessionDep, _: UserDep,
                 last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
                 after: int = 0) -> EventSourceResponse:
    """Server-Sent Events: replays history after `Last-Event-ID`, then streams live events."""
    task = await get_task_or_404(session, task_id)
    bus = request.app.state.bus
    start_after = int(last_event_id) if (last_event_id or "").isdigit() else after

    async def gen():  # type: ignore[no-untyped-def]
        queue = bus.subscribe(task_id)  # subscribe first so nothing is missed between replay and live
        try:
            last = start_after
            for ev in await bus.history(task_id, after_id=last):
                last = ev.id
                yield {"id": str(ev.id), "event": "agentflow", "data": json.dumps(ev.to_dict())}
                if ev.type == TERMINAL_EVENT:
                    return
            async with session_factory()() as s:  # fresh session: the request's may be closed
                current = await s.get(Task, task_id)
            if current is None or current.status in TaskStatus.TERMINAL:
                yield {"event": "end", "data": json.dumps({"status": getattr(current, "status", None)})}
                return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=SSE_PING_S)
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                if ev.id <= last:
                    continue
                last = ev.id
                yield {"id": str(ev.id), "event": "agentflow", "data": json.dumps(ev.to_dict())}
                if ev.type == TERMINAL_EVENT:
                    return
        finally:
            bus.unsubscribe(task_id, queue)

    return EventSourceResponse(gen(), ping=None, headers={"X-Accel-Buffering": "no"})
