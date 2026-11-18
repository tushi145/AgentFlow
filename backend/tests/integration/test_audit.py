"""Audit checks: secret non-exposure and approval resume across a process restart."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from tests.conftest import make_settings
from tests.fakes import FakeLLM
from tests.integration.test_workflow import MODEL, Script, default_search

FAKE_KEYS = {"anthropic_api_key": "sk-ant-api03-SECRETVALUEabcdef0123456789",
             "tavily_api_key": "tvly-SECRETVALUEabcdef0123456789",
             "gemini_api_key": "AIzaSECRETVALUEabcdef0123456789xyz"}


async def _login(c: httpx.AsyncClient) -> None:
    assert (await c.post("/api/v1/auth/login", json={"username": "admin", "password": "pw-test"})).status_code == 200


async def _wait(c: httpx.AsyncClient, tid: str, statuses: set[str]) -> dict:
    for _ in range(400):
        t = (await c.get(f"/api/v1/tasks/{tid}")).json()
        if t["status"] in statuses:
            return t
        await asyncio.sleep(0.05)
    raise AssertionError(t["status"])


async def test_no_secret_in_any_response_or_log(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    settings = make_settings(tmp_path, approval_cost_threshold_usd=None,
                             **{k: SecretStr(v) for k, v in FAKE_KEYS.items()})
    app = create_app(settings, live_llms={"anthropic": FakeLLM(Script(), models=[MODEL])},
                     live_search=default_search())
    caplog.set_level(logging.DEBUG)
    bodies: list[str] = []
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await _login(c)
            tid = (await c.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring."})).json()["id"]
            t = await _wait(c, tid, {"completed", "failed"})
            logging.getLogger("x").info("debug dump api_key=%s", FAKE_KEYS["tavily_api_key"])
            for path in ["/api/v1/system/status", "/api/v1/agents", "/api/v1/models", "/api/v1/tasks",
                         f"/api/v1/tasks/{tid}", f"/api/v1/tasks/{tid}/sources",
                         f"/api/v1/tasks/{tid}/verifications", f"/api/v1/tasks/{tid}/usage",
                         f"/api/v1/tasks/{tid}/events", "/api/v1/reports",
                         f"/api/v1/reports/{t['report_id']}", f"/api/v1/reports/{t['report_id']}/download.md",
                         "/api/v1/analytics/overview", "/api/v1/evaluations/summary",
                         f"/api/v1/evaluations/tasks/{tid}", "/api/v1/evaluations/runs",
                         "/api/v1/approvals?status=",
                         "/api/openapi.json", "/api/v1/auth/me"]:
                r = await c.get(path)
                assert r.status_code == 200, (path, r.status_code)
                bodies.append(r.text)
    everything = "\n".join(bodies) + "\n".join(rec.getMessage() for rec in caplog.records)
    for k, v in FAKE_KEYS.items():
        assert v not in everything, f"{k} leaked"
        assert v[8:24] not in everything, f"{k} partially leaked"
    status = next(b for b in bodies if "live_mode_available" in b)
    assert '"live_mode_available":true' in status.replace(" ", "")  # configured, yet values hidden


async def test_approval_resumes_after_server_restart(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, approval_cost_threshold_usd=None)

    def new_app():  # type: ignore[no-untyped-def]
        return create_app(settings, live_llms={"anthropic": FakeLLM(Script(), models=[MODEL])},
                          live_search=default_search())

    app1 = new_app()
    async with app1.router.lifespan_context(app1):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app1), base_url="http://t") as c:
            await _login(c)
            tid = (await c.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring.",
                                                       "approval_mode": "plan"})).json()["id"]
            paused = await _wait(c, tid, {"awaiting_approval"})
            await asyncio.sleep(0.3)
    # --- process "restarts": brand-new app, same database + checkpoint files ---
    app2 = new_app()
    async with app2.router.lifespan_context(app2):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://t") as c:
            await _login(c)
            t = (await c.get(f"/api/v1/tasks/{tid}")).json()
            assert t["status"] == "awaiting_approval"  # not marked interrupted
            r = await c.post(f"/api/v1/approvals/{paused['pending_approval_id']}/decision",
                             json={"approved": True})
            assert r.status_code == 200
            done = await _wait(c, tid, {"completed", "failed"})
            assert done["status"] == "completed", done.get("error_message")
            planner_runs = [e for e in done["agent_executions"] if e["agent_key"] == "planner"]
            assert len(planner_runs) == 1  # resumed from checkpoint, did not re-plan
