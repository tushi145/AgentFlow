from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agents.runtime import SourceRegistry, TaskRuntime
from app.agents.specs import AGENT_ORDER
from app.llm.factory import ProviderSet
from app.services.agent_config_service import EffectiveAgentConfig
from app.services.budget import BudgetGuard, PricingTable, TaskLimits
from tests.conftest import make_settings

PRICING = PricingTable({"llm": {"anthropic": {"m": {"input_per_mtok": 1.0, "output_per_mtok": 5.0}}},
                        "search": {"tavily": {"usd_per_credit": 0.01}}})


def limits(**kw: Any) -> TaskLimits:
    base = dict(max_total_tokens=500_000, max_llm_calls=50, max_tool_calls=50, timeout_seconds=600,
                max_iterations=2, max_subtasks=6, max_searches_per_subtask=3, max_pages_per_subtask=4)
    base.update(kw)
    return TaskLimits(**base)


def agent_configs(**overrides: dict) -> dict[str, EffectiveAgentConfig]:
    out = {}
    for k in AGENT_ORDER:
        cfg = dict(key=k, enabled=True, provider="anthropic", model="m", effort=None,
                   max_output_tokens=4000, instructions="", max_retries=1, timeout_s=60)
        cfg.update(overrides.get(k, {}))
        out[k] = EffectiveAgentConfig(**cfg)
    return out


class EventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    async def __call__(self, type_: str, message: str, payload: dict) -> None:
        self.events.append((type_, message, payload))

    def types(self) -> list[str]:
        return [e[0] for e in self.events]


def make_runtime(tmp_path: Path, llm: Any, search: Any, *, mode: str = "live",
                 lim: TaskLimits | None = None, objective: str = "Compare X and Y",
                 configs: dict | None = None) -> tuple[TaskRuntime, EventLog]:
    log = EventLog()
    providers = ProviderSet(mode=mode, llms={"anthropic": llm, "demo": llm}, search=search)
    rt = TaskRuntime(task_id="t-1", mode=mode, objective=objective,
                     settings=make_settings(tmp_path), providers=providers,
                     budget=BudgetGuard(lim or limits(), PRICING),
                     agent_configs=configs or agent_configs(), sources=SourceRegistry(),
                     emit=log)
    return rt, log
