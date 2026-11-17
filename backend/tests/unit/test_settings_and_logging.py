from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.models.agent_config import AgentConfig
from app.services.agent_config_service import resolve_effective
from app.utils.logging import RedactingFilter, redact
from tests.conftest import make_settings


def test_redacts_known_key_shapes() -> None:
    text = "key sk-ant-api03-abcdefghijklmnop and tvly-ABCDEFGHIJKL api_key=hunter2 ok"
    out = redact(text)
    assert "sk-ant-api03" not in out and "tvly-ABCD" not in out and "hunter2" not in out
    assert out.endswith("ok")


def test_logging_filter_redacts_args() -> None:
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "using %s", ("sk-ant-secretsecret1",),
                            None)
    RedactingFilter().filter(rec)
    assert "secretsecret" not in rec.getMessage()


def test_empty_env_strings_become_none(tmp_path: Path) -> None:
    s = make_settings(tmp_path, anthropic_api_key="", tavily_api_key="  ",
                      default_max_cost_usd="")
    assert s.anthropic_api_key is None and s.tavily_api_key is None
    assert s.default_max_cost_usd is None
    assert s.live_mode_available is False


def test_live_mode_needs_llm_and_search(tmp_path: Path) -> None:
    s = make_settings(tmp_path, anthropic_api_key=SecretStr("k"), tavily_api_key=SecretStr("t"))
    assert s.live_mode_available is True


def _row(**kw: object) -> AgentConfig:
    base = dict(key="research", name="R", description="d", enabled=True, provider=None,
                model=None, effort=None, max_output_tokens=1000, instructions="",
                max_retries=2, timeout_s=60)
    base.update(kw)
    return AgentConfig(**base)


def test_effective_config_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = make_settings(tmp_path, anthropic_default_model="env-default")
    assert resolve_effective(_row(), s).model == "env-default"
    monkeypatch.setenv("AGENT_MODEL_RESEARCH", "anthropic:env-override")
    assert resolve_effective(_row(), s).model == "env-override"
    eff = resolve_effective(_row(provider="anthropic", model="db-model"), s)
    assert eff.model == "db-model"  # DB setting wins over env
