"""Secret-exposure verification with fake Anthropic/Tavily keys planted in every error path.

The keys below are fake but well-formed, so redaction patterns and leak checks are exercised
exactly as they would be for real credentials. No network access.
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

import httpx
from pydantic import SecretStr

from app.llm.base import LLMError
from app.main import create_app
from app.tools.search import ToolError
from tests.conftest import make_settings
from tests.fakes import FakeLLM, FakeSearch
from tests.integration.test_workflow import MODEL, Script, default_search

ANTH = "sk-ant-api03-FAKEKEYfake0123456789abcdefABCDEF"
TAV = "tvly-dev-FAKEKEYfake0123456789abcdef"
FRAGMENTS = [ANTH, TAV, "FAKEKEYfake0123456789"]


def _assert_clean(text: str, where: str) -> None:
    for f in FRAGMENTS:
        assert f not in text, f"secret fragment leaked in {where}"


async def _run_task(c: httpx.AsyncClient) -> dict:
    tid = (await c.post("/api/v1/tasks", json={"prompt": "Research Acme graduate hiring."})).json()["id"]
    for _ in range(400):
        t = (await c.get(f"/api/v1/tasks/{tid}")).json()
        if t["status"] in ("completed", "failed"):
            return t
        await asyncio.sleep(0.05)
    raise AssertionError(t["status"])


async def _scenario(tmp_path: Path, llm: FakeLLM, search: FakeSearch) -> tuple[dict, str]:
    """Run one task with fake keys configured; return (task, everything the outside world saw)."""
    settings = make_settings(tmp_path, approval_cost_threshold_usd=None,
                             anthropic_api_key=SecretStr(ANTH), tavily_api_key=SecretStr(TAV))
    app = create_app(settings, live_llms={"anthropic": llm}, live_search=search)
    buf = io.StringIO()
    seen: list[str] = []
    async with app.router.lifespan_context(app):
        root = logging.getLogger()
        for h in root.handlers:  # capture the *formatted* production log output
            if isinstance(h, logging.StreamHandler) and type(h).__name__ == "StreamHandler":
                h.setStream(buf)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.post("/api/v1/auth/login",
                                 json={"username": "admin", "password": "pw-test"})).status_code == 200
            t = await _run_task(c)
            tid = t["id"]
            for path in [f"/api/v1/tasks/{tid}", f"/api/v1/tasks/{tid}/events", f"/api/v1/tasks/{tid}/usage",
                         f"/api/v1/evaluations/tasks/{tid}", "/api/v1/system/status", "/api/v1/models",
                         "/api/v1/tasks", "/api/v1/evaluations/runs", "/api/v1/agents"]:
                r = await c.get(path)
                seen.append(f"{path} {r.status_code} {r.text}")
            # Error responses that echo request data: 404 with a key in the path, 422 with a key
            # in the body, 401 after logout with a key in a header.
            seen.append((await c.get(f"/api/v1/tasks/{ANTH}")).text)
            seen.append((await c.post("/api/v1/tasks", json={"prompt": 123, "x": ANTH})).text)
            seen.append((await c.post("/api/v1/tasks", json={"prompt": TAV * 0 + "x" * 9000})).text)
            await c.post("/api/v1/auth/logout")
            seen.append((await c.get("/api/v1/tasks", headers={"x-api-key": ANTH,
                                                              "authorization": f"Bearer {TAV}"})).text)
    return t, "\n".join(seen) + "\n--- LOG ---\n" + buf.getvalue()


async def test_provider_error_echoing_a_key_is_redacted_everywhere(tmp_path: Path) -> None:
    llm = FakeLLM(Script(fail={"ExecutionPlan": LLMError(
        f"Request rejected: invalid x-api-key {ANTH} for project", code="bad_request")}), models=[MODEL])
    t, seen = await _scenario(tmp_path, llm, default_search())
    assert t["status"] == "failed" and t["error_code"] == "bad_request"
    assert "[REDACTED]" in t["error_message"]
    _assert_clean(seen, "API/events/logs after an LLM error")


async def test_search_error_echoing_a_key_is_redacted_everywhere(tmp_path: Path) -> None:
    search = FakeSearch(fail=ToolError(f"Search failed for key {TAV}", code="search_error"))
    t, seen = await _scenario(tmp_path, FakeLLM(Script(), models=[MODEL]), search)
    assert "tool_error" in seen  # the failure is visible in the timeline...
    _assert_clean(seen, "API/events/logs after a search error")  # ...without the key


async def test_unexpected_exception_traceback_is_redacted_in_logs(tmp_path: Path) -> None:
    llm = FakeLLM(Script(fail={"AnalysisResult": RuntimeError(f"socket dump {ANTH} {TAV}")}), models=[MODEL])
    t, seen = await _scenario(tmp_path, llm, default_search())
    assert t["status"] == "failed" and t["error_code"] == "internal_error"
    log = seen.split("--- LOG ---")[1]
    assert "Traceback" in log and "RuntimeError" in log  # the traceback was logged...
    _assert_clean(seen, "API/events/logs after an unexpected exception")  # ...redacted


def test_env_files_are_git_ignored_and_example_has_no_credentials() -> None:
    import re
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    for name in (".env", ".env.local", ".env.production", "backend/.env", "backend/.env.test"):
        r = subprocess.run(["git", "check-ignore", "-q", name], cwd=repo, capture_output=True)
        assert r.returncode == 0, f"{name} is not git-ignored"
    r = subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=repo, capture_output=True)
    assert r.returncode == 1, ".env.example must stay tracked"
    tracked = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout.split()
    assert not [f for f in tracked if re.search(r"(^|/)\.env(\.|$)", f) and not f.endswith(".env.example")]

    example = (repo / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"sk-ant-[A-Za-z0-9]|tvly-[A-Za-z0-9]|AIza[0-9A-Za-z]{20}", example)
    for var in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY", "GEMINI_API_KEY"):
        line = re.search(rf"^{var}=([^\n#]*)", example, re.M)
        assert line is not None and line.group(1).strip() == "", f"{var} must be blank in .env.example"
