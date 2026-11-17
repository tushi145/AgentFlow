from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, or_, select

from app.api.deps import SessionDep, SettingsDep, UserDep
from app.models import Report, ResearchSource, Task, VerificationResult
from app.schemas.outputs import ReportDetail, ReportSummary, SourceOut, VerificationOut
from app.services.pdf import markdown_to_pdf
from app.utils.errors import NotFoundError

router = APIRouter(prefix="/reports", tags=["reports"])


def _safe_filename(title: str, ext: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-.")[:80] or "report"
    return f"{stem}.{ext}"


def _summary(r: Report, settings) -> ReportSummary:  # type: ignore[no-untyped-def]
    out = ReportSummary.model_validate(r)
    out.has_pdf = bool(r.pdf_path) and (settings.reports_dir / r.pdf_path).exists()
    return out


async def _get(session, report_id: int) -> Report:  # type: ignore[no-untyped-def]
    r = await session.get(Report, report_id)
    if r is None:
        raise NotFoundError("Report not found.")
    return r


@router.get("", response_model=dict)
async def list_reports(session: SessionDep, settings: SettingsDep, _: UserDep, q: str | None = None,
                       mode: str | None = None, page: Annotated[int, Query(ge=1)] = 1,
                       page_size: Annotated[int, Query(ge=1, le=100)] = 20) -> dict:
    stmt, count = select(Report), select(func.count(Report.id))
    if q:
        like = f"%{q.strip()[:100]}%"
        cond = or_(Report.title.ilike(like), Report.summary.ilike(like), Report.markdown.ilike(like))
        stmt, count = stmt.where(cond), count.where(cond)
    if mode:
        stmt, count = stmt.where(Report.mode == mode), count.where(Report.mode == mode)
    total = await session.scalar(count) or 0
    rows = (await session.scalars(stmt.order_by(Report.created_at.desc())
                                  .offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [_summary(r, settings).model_dump(mode="json") for r in rows], "total": total,
            "page": page, "page_size": page_size}


@router.get("/{report_id}", response_model=ReportDetail)
async def get_report(report_id: int, session: SessionDep, settings: SettingsDep, _: UserDep) -> ReportDetail:
    r = await _get(session, report_id)
    task = await session.get(Task, r.task_id)
    sources = (await session.scalars(select(ResearchSource).where(ResearchSource.task_id == r.task_id)
                                     .order_by(ResearchSource.id))).all()
    verifs = (await session.scalars(select(VerificationResult)
                                    .where(VerificationResult.task_id == r.task_id)
                                    .order_by(VerificationResult.iteration.desc(),
                                              VerificationResult.id))).all()
    latest = max((v.iteration for v in verifs), default=0)
    base = _summary(r, settings).model_dump()
    return ReportDetail(**base, markdown=r.markdown, prompt=task.prompt if task else "",
                        sources=[SourceOut.model_validate(s) for s in sources],
                        verifications=[VerificationOut.model_validate(v) for v in verifs
                                       if v.iteration == latest])


@router.get("/{report_id}/download.md")
async def download_md(report_id: int, session: SessionDep, _: UserDep) -> Response:
    r = await _get(session, report_id)
    return Response(r.markdown, media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{_safe_filename(r.title, "md")}"'})


@router.get("/{report_id}/download.pdf")
async def download_pdf(report_id: int, session: SessionDep, settings: SettingsDep, _: UserDep) -> FileResponse:
    r = await _get(session, report_id)
    base = settings.reports_dir.resolve()
    path = (base / (r.pdf_path or f"{r.task_id}.pdf")).resolve()
    if path.parent != base:  # path containment: never serve outside the reports directory
        raise NotFoundError("Report file not found.")
    if not path.exists():  # regenerate on demand from the stored markdown
        markdown_to_pdf(r.markdown, path, title=r.title, watermark="DEMO" if r.mode == "demo" else None)
        r.pdf_path = path.name
        await session.commit()
    return FileResponse(Path(path), media_type="application/pdf",
                        filename=_safe_filename(r.title, "pdf"))
