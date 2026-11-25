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


def test_tracebacks_are_redacted() -> None:
    import io
    import logging

    from app.utils.logging import RedactingFilter, RedactingFormatter

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(RedactingFormatter("%(message)s"))
    log = logging.getLogger("redaction-probe")
    log.handlers, log.propagate = [handler], False
    try:
        raise RuntimeError("upstream said: bad key sk-ant-api03-ABCDEFGH12345678 / tvly-ZYXWVUTS98765432")
    except RuntimeError:
        log.exception("call failed")
    out = buf.getvalue()
    assert "Traceback" in out and "ABCDEFGH12345678" not in out and "ZYXWVUTS98765432" not in out


def test_agent_model_override_is_read_from_env_file(tmp_path: Path) -> None:
    """Regression: overrides were read via os.environ only, so values in .env were ignored."""
    from app.config.settings import Settings

    env = tmp_path / ".env"
    env.write_text("AGENT_MODEL_REPORT=anthropic:claude-sonnet-5-5\n"
                   "ANTHROPIC_DEFAULT_MODEL=claude-opus-5-5\n", encoding="utf-8")
    s = Settings(_env_file=env)
    assert resolve_effective(_row(key="report"), s).model == "claude-sonnet-5-5"
    assert resolve_effective(_row(key="planner"), s).model == "claude-opus-5-5"


@pytest.mark.parametrize("secret", ["dev-insecure-secret-change-me-before-deploying-0000",
                                    "change-me-to-a-long-random-string"])
async def test_production_refuses_known_placeholder_jwt_secret(tmp_path: Path, secret: str) -> None:
    from app.main import create_app
    app = create_app(make_settings(tmp_path, app_env="production", jwt_secret=SecretStr(secret)))
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        async with app.router.lifespan_context(app):
            pass
