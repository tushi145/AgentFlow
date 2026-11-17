from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.llm.factory import provider_set_for_mode
from app.llm.registry import ModelRegistry
from app.utils.errors import ProviderNotConfiguredError
from tests.conftest import make_settings
from tests.fakes import FakeLLM


@pytest.fixture
def with_fake_registry(app):  # type: ignore[no-untyped-def]
    app.state.model_registry = ModelRegistry({"anthropic": FakeLLM(models=["claude-x-1", "claude-y-2"])})
    return app


async def test_list_agents(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.get("/api/v1/agents")
    assert r.status_code == 200
    data = r.json()
    assert [a["key"] for a in data] == ["planner", "research", "analysis", "verification", "report"]
    research = data[1]
    assert research["allowed_tools"] == ["fetch_page", "web_search"]
    assert research["effective_model"] == "test-model-default"  # from env, not hard-coded


async def test_update_agent_validates_model(with_fake_registry, auth_client: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    bad = await auth_client.put("/api/v1/agents/analysis", json={"model": "claude-made-up"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_model"
    assert "claude-x-1" in bad.json()["error"]["details"]["suggestions"]

    ok = await auth_client.put("/api/v1/agents/analysis",
                               json={"model": "claude-y-2", "effort": "high",
                                     "instructions": "Prefer tables."})
    assert ok.status_code == 200
    body = ok.json()
    assert body["effective_model"] == "claude-y-2" and body["effort"] == "high"

    cleared = await auth_client.put("/api/v1/agents/analysis", json={"clear_model_override": True})
    assert cleared.json()["model"] is None
    assert cleared.json()["effective_model"] == "test-model-default"


async def test_unconfigured_provider_rejected(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.put("/api/v1/agents/report", json={"provider": "gemini", "model": "g-1"})
    assert r.status_code == 422
    assert r.json()["error"]["details"]["status"] == "provider_not_configured"


async def test_required_agent_cannot_be_disabled(auth_client: httpx.AsyncClient) -> None:
    r = await auth_client.put("/api/v1/agents/planner", json={"enabled": False})
    assert r.status_code == 400
    ok = await auth_client.put("/api/v1/agents/verification", json={"enabled": False})
    assert ok.status_code == 200 and ok.json()["enabled"] is False


async def test_models_endpoint(with_fake_registry, auth_client: httpx.AsyncClient) -> None:  # type: ignore[no-untyped-def]
    r = await auth_client.get("/api/v1/models")
    data = {p["provider"]: p for p in r.json()}
    assert [m["id"] for m in data["anthropic"]["models"]] == ["claude-x-1", "claude-y-2"]
    assert data["anthropic"]["default_model_status"] == "invalid"  # test default not in list
    assert data["gemini"]["configured"] is False


def test_live_mode_refuses_without_keys(tmp_path: Path) -> None:
    with pytest.raises(ProviderNotConfiguredError) as ei:
        provider_set_for_mode("live", make_settings(tmp_path))
    assert set(ei.value.details["missing"]) == {"ANTHROPIC_API_KEY", "TAVILY_API_KEY"}


async def test_demo_mode_is_labelled(tmp_path: Path) -> None:
    ps = provider_set_for_mode("demo", make_settings(tmp_path))
    assert ps.mode == "demo" and ps.llm("anthropic").is_demo and ps.search.is_demo
    res = await ps.search.search("anything", max_results=3)
    assert all(h.url.startswith("demo://") and h.title.startswith("[DEMO]") for h in res.hits)
