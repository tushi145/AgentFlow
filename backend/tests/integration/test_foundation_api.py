from __future__ import annotations

import httpx
from sqlalchemy import select

from app.database.session import session_factory
from app.models import AgentConfig, User


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}
    assert "x-request-id" in r.headers


async def test_protected_route_requires_auth(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/system/status")
    assert r.status_code == 401
    body = r.json()["error"]
    assert body["code"] == "unauthorized" and body["request_id"]


async def test_login_wrong_password(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401
    assert "agentflow_session" not in r.cookies


async def test_login_me_logout(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "pw-test"})
    assert r.status_code == 200
    me = await client.get("/api/v1/auth/me")
    assert me.json()["username"] == "admin"
    await client.post("/api/v1/auth/logout")
    client.cookies.clear()
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_validation_error_envelope(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/auth/login", json={"username": ""})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


async def test_system_status_without_keys_reports_missing(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/v1/system/status")
    data = r.json()
    assert data["live_mode_available"] is False
    assert set(data["missing_for_live"]) == {"ANTHROPIC_API_KEY", "TAVILY_API_KEY"}
    assert "api_key" not in r.text.lower().replace("_api_key", "")  # no secret fields leak


async def test_startup_seeds_admin_and_agents(app) -> None:  # type: ignore[no-untyped-def]
    async with session_factory()() as s:
        users = (await s.scalars(select(User))).all()
        agents = (await s.scalars(select(AgentConfig.key))).all()
    assert [u.username for u in users] == ["admin"]
    assert users[0].password_hash.startswith("$2")  # stored hashed, never plain
    assert sorted(agents) == ["analysis", "planner", "report", "research", "verification"]
