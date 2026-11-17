from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.evaluation.benchmarks import load_dataset, score
from app.main import create_app
from app.schemas.agent_io import ClaimVerdict, VerificationReport
from tests.conftest import make_settings
from tests.fakes import FakeLLM
from tests.integration.test_workflow import MODEL, Script, default_search


@pytest.fixture
async def api(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = make_settings(tmp_path, approval_cost_threshold_usd=None)
    script = Script()
    llm, search = FakeLLM(script, models=[MODEL]), default_search()
    app = create_app(settings, live_llms={"anthropic": llm}, live_search=search)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/auth/login", json={"username": "admin", "password": "pw-test"})
            assert r.status_code == 200
            c.app, c.llm, c.search, c.script = app, llm, search, script  # type: ignore[attr-defined]
            yield c


async def wait_done(c: httpx.AsyncClient, tid: str, statuses: set[str] | None = None) -> dict:
    statuses = statuses or {"completed", "failed", "cancelled", "rejected"}
    for _ in range(400):
        t = (await c.get(f"/api/v1/tasks/{tid}")).json()
        if t["status"] in statuses:
            return t
        await asyncio.sleep(0.05)
    raise AssertionError(t["status"])


async def test_task_lifecycle_over_http(api: httpx.AsyncClient) -> None:
    r = await api.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring in India."})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    t = await wait_done(api, tid)
    assert t["status"] == "completed" and t["report_id"]
    assert len(t["subtasks"]) == 3 and t["agent_executions"]

    lst = (await api.get("/api/v1/tasks", params={"q": "Acme"})).json()
    assert lst["total"] == 1 and lst["items"][0]["id"] == tid
    assert (await api.get("/api/v1/tasks", params={"status": "active"})).json()["total"] == 0

    sources = (await api.get(f"/api/v1/tasks/{tid}/sources")).json()
    assert sources and sources[0]["ref"] == "S1" and "content_excerpt" not in sources[0]
    assert (await api.get(f"/api/v1/tasks/{tid}/verifications")).json()[0]["verdict"] == "supported"
    usage = (await api.get(f"/api/v1/tasks/{tid}/usage")).json()
    assert usage["per_agent"]["research"]["llm_calls"] == 3
    assert usage["totals"]["cost_usd"] is None and "Pricing not configured" in usage["totals"]["cost_note"]


async def test_sse_replays_history_and_terminates(api: httpx.AsyncClient) -> None:
    tid = (await api.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring.",
                                                 "mode": "demo"})).json()["id"]
    await wait_done(api, tid)
    async with api.stream("GET", f"/api/v1/tasks/{tid}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = (await resp.aread()).decode()
    events = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")
              and line[5:].strip().startswith("{\"id\"")]
    types = [e["type"] for e in events]
    assert types[0] == "task_started" and types[-1] == "task_finished"
    ids = [e["id"] for e in events]
    assert ids == sorted(ids)
    # Resume from the middle with Last-Event-ID
    mid = ids[len(ids) // 2]
    async with api.stream("GET", f"/api/v1/tasks/{tid}/events",
                          headers={"Last-Event-ID": str(mid)}) as resp:
        body2 = (await resp.aread()).decode()
    resumed = [json.loads(line[5:])["id"] for line in body2.splitlines()
               if line.startswith("data:") and "\"id\"" in line]
    assert resumed and min(resumed) > mid


async def test_reports_endpoints(api: httpx.AsyncClient) -> None:
    tid = (await api.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring."})).json()["id"]
    rid = (await wait_done(api, tid))["report_id"]
    lst = (await api.get("/api/v1/reports", params={"q": "Acme"})).json()
    assert lst["total"] == 1 and lst["items"][0]["has_pdf"] is True
    assert (await api.get("/api/v1/reports", params={"q": "zzz-nothing"})).json()["total"] == 0
    detail = (await api.get(f"/api/v1/reports/{rid}")).json()
    assert detail["markdown"].startswith("# Acme hiring report") and detail["sources"] and detail["verifications"]
    md = await api.get(f"/api/v1/reports/{rid}/download.md")
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"]
    pdf = await api.get(f"/api/v1/reports/{rid}/download.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert (await api.get("/api/v1/reports/9999")).status_code == 404


async def test_approval_endpoints(api: httpx.AsyncClient) -> None:
    tid = (await api.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring.",
                                                 "approval_mode": "plan"})).json()["id"]
    t = await wait_done(api, tid, {"awaiting_approval"})
    pending = (await api.get("/api/v1/approvals")).json()
    assert len(pending) == 1 and pending[0]["id"] == t["pending_approval_id"]
    assert pending[0]["task_title"]
    r = await api.post(f"/api/v1/approvals/{pending[0]['id']}/decision", json={"approved": True})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    again = await api.post(f"/api/v1/approvals/{pending[0]['id']}/decision", json={"approved": True})
    assert again.status_code == 409
    assert (await wait_done(api, tid))["status"] == "completed"


async def test_cancel_retry_delete(api: httpx.AsyncClient) -> None:
    tid = (await api.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring.",
                                                 "approval_mode": "plan"})).json()["id"]
    await wait_done(api, tid, {"awaiting_approval"})
    assert (await api.post(f"/api/v1/tasks/{tid}/cancel")).status_code == 200
    assert (await wait_done(api, tid))["status"] == "cancelled"
    assert (await api.post(f"/api/v1/tasks/{tid}/cancel")).status_code == 409
    assert (await api.get("/api/v1/approvals")).json() == []  # pending approval expired

    retry = await api.post(f"/api/v1/tasks/{tid}/retry")
    assert retry.status_code == 201 and retry.json()["id"] != tid
    new_id = retry.json()["id"]
    await wait_done(api, new_id, {"awaiting_approval"})
    await api.post(f"/api/v1/tasks/{new_id}/cancel")
    await wait_done(api, new_id)
    assert (await api.delete(f"/api/v1/tasks/{tid}")).status_code == 204
    assert (await api.get(f"/api/v1/tasks/{tid}")).status_code == 404


async def test_task_validation_and_live_without_keys(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))  # no keys, no fakes
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/v1/auth/login", json={"username": "admin", "password": "pw-test"})
            short = await c.post("/api/v1/tasks", json={"prompt": "hi"})
            assert short.status_code == 422
            live = await c.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring."})
            assert live.status_code == 409 and live.json()["error"]["code"] == "provider_not_configured"
            assert (await c.get("/api/v1/tasks")).json()["total"] == 0  # nothing silently created
            demo = await c.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring.", "mode": "demo"})
            assert demo.status_code == 201 and demo.json()["mode"] == "demo"
            bench = await c.post("/api/v1/evaluations/benchmarks/verification/run")
            assert bench.status_code == 409  # benchmarks never fall back to demo


async def test_analytics_and_evaluation_summary(api: httpx.AsyncClient) -> None:
    live = (await api.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring."})).json()["id"]
    demo = (await api.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring.", "mode": "demo"})).json()["id"]
    await wait_done(api, live)
    await wait_done(api, demo)
    ov = (await api.get("/api/v1/analytics/overview")).json()
    assert ov["totals"]["tasks"] == 2 and ov["totals"]["completed"] == 2
    assert ov["totals"]["success_rate"] == 1.0 and ov["totals"]["agents"] == 5
    assert len(ov["series"]) == 14 and ov["per_agent"]["research"]["runs"] >= 3
    ev = (await api.get("/api/v1/evaluations/summary")).json()
    assert ev["live_runs_evaluated"] == 1  # demo run excluded
    assert ev["task_completion_rate"] == 1.0 and ev["source_coverage_rate"] == 1.0
    per_task = (await api.get(f"/api/v1/evaluations/tasks/{live}")).json()
    assert per_task["metrics"]["mode"] == "live"


async def test_benchmark_run_with_fake_live_model(api: httpx.AsyncClient) -> None:
    items = load_dataset("verification")

    def judge(req):  # type: ignore[no-untyped-def]
        # A deliberately naive judge: says every claim is supported, citing its first evidence.
        import re
        ids = re.findall(r"### Claim (c\d+).*?cited: (S\d+)", req.messages[-1].content, re.S)
        return VerificationReport(verdicts=[ClaimVerdict(claim_id=c, verdict="supported", confidence=0.8,
                                                         reasoning="r", supporting_refs=[s]) for c, s in ids],
                                  sufficient=True, sufficiency_reasoning="ok")

    api.llm.handler = judge  # type: ignore[attr-defined]
    r = await api.post("/api/v1/evaluations/benchmarks/verification/run")
    assert r.status_code == 202
    run_id = r.json()["id"]
    for _ in range(100):
        run = (await api.get(f"/api/v1/evaluations/benchmarks/{run_id}")).json()
        if run["status"] != "running":
            break
        await asyncio.sleep(0.05)
    assert run["status"] == "completed", run
    expected = sum(1 for it in items if it["gold"] == "supported") / len(items)
    assert run["metrics"]["accuracy"] == pytest.approx(expected, abs=1e-4)
    assert len(run["item_results"]) == len(items)


def test_benchmark_scoring() -> None:
    items = [{"id": "a", "gold": "supported"}, {"id": "b", "gold": "unsupported"},
             {"id": "c", "gold": "conflicting"}, {"id": "d", "gold": "partially_supported"}]
    preds = {"a": {"verdict": "supported"}, "b": {"verdict": "supported"},
             "c": {"verdict": "conflicting"}, "d": {"verdict": "supported"}}
    m = score(items, preds)
    assert m["accuracy"] == 0.5 and m["binary_accuracy"] == 0.75
    assert m["per_class"]["supported"]["precision"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["confusion"]["unsupported"] == {"supported": 1}


def test_benchmark_dataset_is_well_formed() -> None:
    items = load_dataset("verification")
    assert len(items) >= 12 and len({i["id"] for i in items}) == len(items)
    assert {i["gold"] for i in items} >= {"supported", "unsupported", "conflicting", "partially_supported"}


async def test_stale_approval_expires_and_cancels_task(api: httpx.AsyncClient) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.database.session import session_factory
    from app.models import ApprovalRequest
    tid = (await api.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring.",
                                                 "approval_mode": "plan"})).json()["id"]
    t = await wait_done(api, tid, {"awaiting_approval"})
    await asyncio.sleep(0.3)  # let the paused run fully unwind
    async with session_factory()() as s:
        await s.execute(update(ApprovalRequest).values(created_at=datetime.now(UTC) - timedelta(hours=48)))
        await s.commit()
    assert (await api.get("/api/v1/approvals")).json() == []
    t = (await api.get(f"/api/v1/tasks/{tid}")).json()
    assert t["status"] == "cancelled" and t["error_code"] == "approval_timeout"
    r = await api.post(f"/api/v1/approvals/{t['pending_approval_id'] or 1}/decision", json={"approved": True})
    assert r.status_code == 409
