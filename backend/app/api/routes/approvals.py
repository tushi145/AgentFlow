from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import SessionDep, UserDep
from app.models import ApprovalRequest, Task
from app.schemas.outputs import ApprovalDecision, ApprovalOut

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(request: Request, session: SessionDep, _: UserDep,
                         status: str | None = "pending",
                         task_id: str | None = None) -> list[ApprovalOut]:
    await request.app.state.manager.expire_stale_approvals()
    stmt = select(ApprovalRequest, Task.title).join(Task, Task.id == ApprovalRequest.task_id)
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    if task_id:
        stmt = stmt.where(ApprovalRequest.task_id == task_id)
    rows = (await session.execute(stmt.order_by(ApprovalRequest.created_at.desc()).limit(200))).all()
    out = []
    for req, title in rows:
        item = ApprovalOut.model_validate(req)
        item.task_title = title
        out.append(item)
    return out


@router.post("/{approval_id}/decision", response_model=ApprovalOut)
async def decide(approval_id: int, body: ApprovalDecision, request: Request, _: UserDep) -> ApprovalOut:
    req = await request.app.state.manager.decide_approval(approval_id, body.approved, body.comment)
    return ApprovalOut.model_validate(req)
