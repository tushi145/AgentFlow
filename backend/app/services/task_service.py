"""Task creation and querying."""

from __future__ import annotations

import re

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.llm.factory import provider_set_for_mode
from app.models import AgentExecution, ApprovalRequest, Report, Task
from app.models.task import TaskStatus
from app.schemas.tasks import TaskCreate
from app.services.budget import TaskLimits
from app.utils.errors import NotFoundError


def make_title(prompt: str) -> str:
    first = re.split(r"(?<=[.?!])\s|\n", prompt.strip(), maxsplit=1)[0]
    return (first[:117] + "…") if len(first) > 120 else first


async def create_task(session: AsyncSession, settings: Settings, body: TaskCreate, *,
                      user_id: int | None, live_llms: dict | None = None,
                      live_search: object | None = None, parent_task_id: str | None = None) -> Task:
    # Fail fast (HTTP 409) if live execution is requested without credentials — never degrade.
    provider_set_for_mode(body.mode, settings, live_llms, live_search)  # type: ignore[arg-type]
    limits = TaskLimits.resolve(settings, body.limits.model_dump(exclude_none=True))
    task = Task(prompt=body.prompt, title=make_title(body.prompt), mode=body.mode,
                status=TaskStatus.QUEUED, execution_mode=body.execution_mode,
                approval_mode=body.approval_mode, limits=limits.model_dump(),
                agent_overrides={k: {"enabled": False} for k in body.disabled_agents},
                user_id=user_id, parent_task_id=parent_task_id, runtime_state={})
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def get_task_or_404(session: AsyncSession, task_id: str) -> Task:
    task = await session.get(Task, task_id)
    if task is None:
        raise NotFoundError("Task not found.")
    return task


async def list_tasks(session: AsyncSession, *, status: str | None, q: str | None, mode: str | None,
                     page: int, page_size: int) -> tuple[list[tuple[Task, int | None]], int]:
    stmt = select(Task, Report.id).outerjoin(Report, Report.task_id == Task.id)
    count = select(func.count(Task.id))
    filters = []
    if status == "active":
        filters.append(Task.status.in_(list(TaskStatus.ACTIVE)))
    elif status:
        filters.append(Task.status == status)
    if mode:
        filters.append(Task.mode == mode)
    if q:
        like = f"%{q.strip()[:100]}%"
        filters.append(or_(Task.title.ilike(like), Task.prompt.ilike(like)))
    for f in filters:
        stmt, count = stmt.where(f), count.where(f)
    total = await session.scalar(count) or 0
    rows = (await session.execute(stmt.order_by(Task.created_at.desc())
                                  .offset((page - 1) * page_size).limit(page_size))).all()
    return [(t, rid) for t, rid in rows], total


async def task_detail_parts(session: AsyncSession, task_id: str) -> tuple[list[AgentExecution], int | None, int | None]:
    execs = (await session.scalars(select(AgentExecution).where(AgentExecution.task_id == task_id)
                                   .order_by(AgentExecution.id))).all()
    report_id = await session.scalar(select(Report.id).where(Report.task_id == task_id))
    pending = await session.scalar(select(ApprovalRequest.id).where(
        ApprovalRequest.task_id == task_id, ApprovalRequest.status == "pending")
        .order_by(ApprovalRequest.id.desc()))
    return list(execs), report_id, pending
