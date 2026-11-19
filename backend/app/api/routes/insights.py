"""Analytics (dashboard) and evaluation endpoints."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.deps import SessionDep, SettingsDep, UserDep, limiter
from app.database.session import session_factory
from app.evaluation.benchmarks import (DATASETS, OFFLINE_SUITES, run_guard_benchmark,
                                      run_verification_benchmark)
from app.graph.runner import safe_error
from app.llm.factory import provider_set_for_mode
from app.models import AgentConfig, AgentExecution, BenchmarkRun, EvaluationResult, Task
from app.models.task import TaskStatus
from app.schemas.outputs import BenchmarkRunOut
from app.services.agent_config_service import resolve_effective
from app.services.db_lock import db_write_lock
from app.utils.errors import NotFoundError

log = logging.getLogger(__name__)
router = APIRouter(tags=["analytics"])
_bench_tasks: set[asyncio.Task] = set()


@router.get("/analytics/overview")
async def overview(session: SessionDep, _: UserDep, days: int = 14, mode: str | None = None) -> dict:
    stmt = select(Task)
    if mode:
        stmt = stmt.where(Task.mode == mode)
    tasks = (await session.scalars(stmt)).all()
    terminal = [t for t in tasks if t.status in TaskStatus.TERMINAL]
    done = [t for t in tasks if t.status == TaskStatus.COMPLETED]
    durations = [(t.finished_at - t.started_at).total_seconds() for t in done
                 if t.finished_at and t.started_at]
    since = datetime.now(UTC) - timedelta(days=days - 1)
    series: dict[str, dict] = {}
    for i in range(days):
        d = (since + timedelta(days=i)).date().isoformat()
        series[d] = {"date": d, "completed": 0, "failed": 0, "other": 0}
    for t in tasks:
        created = t.created_at if t.created_at.tzinfo else t.created_at.replace(tzinfo=UTC)
        key = created.date().isoformat()
        if key in series:
            bucket = ("completed" if t.status == TaskStatus.COMPLETED else
                      "failed" if t.status in (TaskStatus.FAILED, TaskStatus.INTERRUPTED) else "other")
            series[key][bucket] += 1
    agent_rows = (await session.execute(
        select(AgentExecution.agent_key, AgentExecution.status, func.count(AgentExecution.id),
               func.avg(AgentExecution.duration_ms), func.sum(AgentExecution.input_tokens),
               func.sum(AgentExecution.output_tokens))
        .group_by(AgentExecution.agent_key, AgentExecution.status))).all()
    per_agent: dict[str, dict] = defaultdict(lambda: {"runs": 0, "failed": 0, "avg_ms": None,
                                                      "tokens": 0})
    for key, status, n, avg_ms, tin, tout in agent_rows:
        a = per_agent[key]
        a["runs"] += n
        a["tokens"] += int((tin or 0) + (tout or 0))
        if status == "failed":
            a["failed"] += n
        if status == "completed" and avg_ms is not None:
            a["avg_ms"] = int(avg_ms)
    agents_total = await session.scalar(select(func.count(AgentConfig.id))) or 0
    agents_enabled = await session.scalar(select(func.count(AgentConfig.id)).where(AgentConfig.enabled)) or 0
    recent = sorted(tasks, key=lambda t: t.created_at, reverse=True)[:8]
    live_costs = [t.cost_usd for t in done if t.mode == "live" and t.cost_usd is not None]
    return {
        "totals": {
            "tasks": len(tasks), "completed": len(done),
            "failed": sum(1 for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.INTERRUPTED)),
            "active": sum(1 for t in tasks if t.status in TaskStatus.ACTIVE),
            "awaiting_approval": sum(1 for t in tasks if t.status == TaskStatus.AWAITING_APPROVAL),
            "success_rate": round(len(done) / len(terminal), 4) if terminal else None,
            "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
            "agents": agents_total, "agents_enabled": agents_enabled,
            "total_tokens": sum(t.input_tokens + t.output_tokens for t in tasks),
            "total_cost_usd": round(sum(live_costs), 4) if live_costs else None,
            "demo_tasks": sum(1 for t in tasks if t.mode == "demo"),
        },
        "series": list(series.values()),
        "status_breakdown": dict(Counter(t.status for t in tasks)),
        "per_agent": dict(per_agent),
        "recent": [{"id": t.id, "title": t.title, "status": t.status, "mode": t.mode,
                    "created_at": t.created_at.isoformat(), "cost_usd": t.cost_usd} for t in recent],
    }


@router.get("/evaluations/summary")
async def evaluation_summary(session: SessionDep, _: UserDep) -> dict:
    """Aggregates over LIVE runs only — demo runs are excluded from quality metrics."""
    rows = (await session.execute(select(EvaluationResult.metrics, Task.status)
                                  .join(Task, Task.id == EvaluationResult.task_id)
                                  .where(Task.mode == "live"))).all()
    tasks = (await session.scalars(select(Task).where(Task.mode == "live"))).all()
    terminal = [t for t in tasks if t.status in TaskStatus.TERMINAL]

    def avg(path: tuple[str, str]) -> float | None:
        vals = [m[path[0]][path[1]] for m, _ in rows
                if m.get(path[0], {}).get(path[1]) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    fail_rows = (await session.execute(
        select(AgentExecution.agent_key, AgentExecution.status, func.count(AgentExecution.id))
        .where(AgentExecution.mode == "live").group_by(AgentExecution.agent_key, AgentExecution.status))).all()
    failures: dict[str, dict] = defaultdict(lambda: {"runs": 0, "failed": 0})
    for key, status, n in fail_rows:
        failures[key]["runs"] += n
        if status in ("failed", "aborted"):
            failures[key]["failed"] += n
    for v in failures.values():
        v["failure_rate"] = round(v["failed"] / v["runs"], 4) if v["runs"] else None
    latest_bench = await session.scalar(select(BenchmarkRun).where(BenchmarkRun.status == "completed")
                                        .order_by(BenchmarkRun.id.desc()))
    done = [t for t in terminal if t.status == TaskStatus.COMPLETED]
    durations = sorted(_duration_s(t) for t in done if _duration_s(t) is not None)  # type: ignore[type-var]
    tokens = [t.input_tokens + t.output_tokens for t in done]
    return {
        "live_runs_evaluated": len(rows),
        "task_completion_rate": round(sum(1 for t in terminal if t.status == TaskStatus.COMPLETED)
                                      / len(terminal), 4) if terminal else None,
        "citation_integrity_rate": avg(("citation_integrity", "rate")),
        "source_coverage_rate": avg(("source_coverage", "rate")),
        "claims_with_source_rate": avg(("source_coverage", "claims_with_source_rate")),
        "supported_claim_rate": avg(("verification", "supported_rate")),
        "mean_confidence": avg(("verification", "mean_confidence")),
        "agent_failures": dict(failures),
        # Over completed live runs; None (not 0) when there are none.
        "duration_s": {"runs": len(durations),
                       "mean": round(sum(durations) / len(durations), 1) if durations else None,
                       "median": _median(durations), "max": durations[-1] if durations else None},
        "tokens": {"runs": len(tokens), "total": sum(tokens) if tokens else None,
                   "mean_per_run": round(sum(tokens) / len(tokens)) if tokens else None},
        "verification_benchmark": {"accuracy": latest_bench.metrics.get("accuracy"),
                                   "binary_accuracy": latest_bench.metrics.get("binary_accuracy"),
                                   "model": latest_bench.model, "run_id": latest_bench.id,
                                   "created_at": latest_bench.created_at.isoformat()}
        if latest_bench else None,
    }


def _duration_s(t: Task) -> float | None:
    if not (t.started_at and t.finished_at):
        return None
    return round((t.finished_at - t.started_at).total_seconds(), 1)


def _median(sorted_vals: list[float]) -> float | None:
    if not sorted_vals:
        return None
    n, mid = len(sorted_vals), len(sorted_vals) // 2
    return sorted_vals[mid] if n % 2 else round((sorted_vals[mid - 1] + sorted_vals[mid]) / 2, 1)


@router.get("/evaluations/runs")
async def evaluation_runs(session: SessionDep, _: UserDep, mode: str | None = None,
                          page: Annotated[int, Query(ge=1)] = 1,
                          page_size: Annotated[int, Query(ge=1, le=100)] = 20) -> dict:
    """Per-run recorded metrics, newest first. Demo runs are included only when asked for and are
    labelled by `mode` — their metrics describe template output, not research quality."""
    stmt = select(EvaluationResult, Task).join(Task, Task.id == EvaluationResult.task_id)
    count = select(func.count(EvaluationResult.id)).join(Task, Task.id == EvaluationResult.task_id)
    if mode in ("live", "demo"):
        stmt, count = stmt.where(Task.mode == mode), count.where(Task.mode == mode)
    total = await session.scalar(count) or 0
    rows = (await session.execute(stmt.order_by(EvaluationResult.created_at.desc(), EvaluationResult.id.desc())
                                  .offset((page - 1) * page_size).limit(page_size))).all()
    items = [{"task_id": t.id, "title": t.title, "mode": t.mode, "status": t.status,
              "created_at": t.created_at.isoformat(), "duration_s": _duration_s(t),
              "input_tokens": t.input_tokens, "output_tokens": t.output_tokens,
              "llm_calls": t.llm_calls, "tool_calls": t.tool_calls, "cost_usd": t.cost_usd,
              "metrics": ev.metrics} for ev, t in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/evaluations/tasks/{task_id}")
async def task_evaluation(task_id: str, session: SessionDep, _: UserDep) -> dict:
    row = await session.scalar(select(EvaluationResult).where(EvaluationResult.task_id == task_id))
    if row is None:
        raise NotFoundError("No evaluation recorded for this task yet.")
    return {"task_id": task_id, "metrics": row.metrics, "created_at": row.created_at.isoformat()}


@router.get("/evaluations/benchmarks", response_model=list[BenchmarkRunOut])
async def list_benchmarks(session: SessionDep, _: UserDep) -> list[BenchmarkRunOut]:
    rows = (await session.scalars(select(BenchmarkRun).order_by(BenchmarkRun.id.desc()).limit(50))).all()
    return [BenchmarkRunOut.model_validate(r) for r in rows]


@router.get("/evaluations/benchmarks/{run_id}")
async def get_benchmark(run_id: int, session: SessionDep, _: UserDep) -> dict:
    row = await session.get(BenchmarkRun, run_id)
    if row is None:
        raise NotFoundError("Benchmark run not found.")
    return {**BenchmarkRunOut.model_validate(row).model_dump(mode="json"), "item_results": row.item_results}


@router.post("/evaluations/benchmarks/{suite}/run", response_model=BenchmarkRunOut, status_code=202)
@limiter.limit("3/hour")
async def run_benchmark(suite: str, request: Request, session: SessionDep, settings: SettingsDep,
                        _: UserDep) -> BenchmarkRunOut:
    if suite not in DATASETS:
        raise NotFoundError(f"Unknown benchmark suite '{suite}'.")
    manager = request.app.state.manager
    offline = suite in OFFLINE_SUITES
    if offline:  # deterministic code-level checks: no model, no credentials, no cost
        providers, cfg = None, None
        row = BenchmarkRun(suite=suite, status="running", provider="deterministic", model=None,
                           metrics={}, item_results=[])
    else:
        providers = provider_set_for_mode("live", settings, manager.live_llms, manager.live_search)
        cfg_row = await session.scalar(select(AgentConfig).where(AgentConfig.key == "verification"))
        cfg = resolve_effective(cfg_row, settings)  # type: ignore[arg-type]
        row = BenchmarkRun(suite=suite, status="running", provider=cfg.provider, model=cfg.model,
                           metrics={}, item_results=[])
    session.add(row)
    await session.commit()
    await session.refresh(row)
    run_id = row.id

    async def _go() -> None:
        try:
            res = (run_guard_benchmark() if offline else
                   await run_verification_benchmark(settings, providers, cfg, manager.pricing))  # type: ignore[arg-type]
            values = dict(status="completed", metrics={**res["metrics"], "tokens": res["tokens"]},
                          item_results=res["item_results"], model=res["model"], cost_usd=res["cost_usd"])
        except Exception as exc:
            code, msg = safe_error(exc)
            values = dict(status="failed", metrics={"error_code": code, "error": msg})
        async with db_write_lock(), session_factory()() as s:
            r = await s.get(BenchmarkRun, run_id)
            if r is not None:
                for k, v in values.items():
                    setattr(r, k, v)
                await s.commit()

    t = asyncio.create_task(_go())
    _bench_tasks.add(t)
    t.add_done_callback(_bench_tasks.discard)
    return BenchmarkRunOut.model_validate(row)
