"""Shared fixtures. Tests never read the developer's `.env` and never call external APIs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import Settings


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict = dict(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        data_dir=tmp_path / "data",
        jwt_secret=SecretStr("test-secret-test-secret-test-secret"),
        admin_username="admin",
        admin_password=SecretStr("pw-test"),
        admin_password_hash=None,
        anthropic_api_key=None,
        gemini_api_key=None,
        tavily_api_key=None,
        anthropic_default_model="test-model-default",
        gemini_default_model=None,
        rate_limit_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator:
    from app.main import create_app

    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "pw-test"})
    assert r.status_code == 200, r.text
    return client
