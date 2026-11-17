"""Real end-to-end run against Anthropic + Tavily. Costs money; opt-in only.

    cd backend && .venv/Scripts/python -m pytest -m live -s

Requires ANTHROPIC_API_KEY, ANTHROPIC_DEFAULT_MODEL and TAVILY_API_KEY in the environment/.env.
Limits are kept small to bound cost.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.config.settings import Settings

pytestmark = pytest.mark.live


def _live_settings(tmp_path: Path) -> Settings:
    real = Settings()  # reads the developer's .env
    if not (real.anthropic_api_key and real.tavily_api_key and real.anthropic_default_model):
        pytest.skip("Live credentials not configured.")
    return real.model_copy(update={
        "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'live.db').as_posix()}",
        "data_dir": tmp_path / "data", "app_env": "test", "rate_limit_enabled": False,
        "approval_cost_threshold_usd": None})


async def test_live_research_task_end_to_end(tmp_path: Path) -> None:
    from app.main import create_app

    settings = _live_settings(tmp_path)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                     timeout=60) as c:
            await c.post("/api/v1/auth/login", json={"username": settings.admin_username,
                                                     "password": settings.admin_password.get_secret_value()})  # type: ignore[union-attr]
            r = await c.post("/api/v1/tasks", json={
                "prompt": "Identify three AI companies that run graduate (fresher) hiring programmes "
                          "in India and summarise the roles they offer, with sources.",
                "limits": {"max_subtasks": 3, "max_searches_per_subtask": 2, "max_iterations": 1,
                           "max_llm_calls": 15, "max_total_tokens": 250_000}})
            assert r.status_code == 201, r.text
            tid = r.json()["id"]
            for _ in range(900):
                t = (await c.get(f"/api/v1/tasks/{tid}")).json()
                if t["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(1)
            assert t["status"] == "completed", t.get("error_message")
            assert t["mode"] == "live" and t["input_tokens"] > 0
            sources = (await c.get(f"/api/v1/tasks/{tid}/sources")).json()
            assert sources and all(s["url"].startswith("http") for s in sources)
            report = (await c.get(f"/api/v1/reports/{t['report_id']}")).json()
            assert "## References" in report["markdown"] and "DEMO" not in report["markdown"]
            ev = (await c.get(f"/api/v1/evaluations/tasks/{tid}")).json()["metrics"]
            print("\nLIVE RUN METRICS:", ev["citation_integrity"], ev["source_coverage"],
                  ev["verification"], f"cost=${t['cost_usd']}")
