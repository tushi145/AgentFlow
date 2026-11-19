"""Base class for agents: budget-checked, retried, repaired, fully-accounted LLM calls."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel

from app.agents.prompts import with_operator_instructions
from app.agents.runtime import TaskRuntime
from app.agents.specs import AGENT_SPECS, AgentSpec
from app.llm.base import LLMError, LLMMessage, LLMOutputError, LLMRequest, Usage
from app.llm.retry import with_backoff
from app.services.budget import estimate_tokens

O = TypeVar("O", bound=BaseModel)
MAX_RETRY_OUTPUT_TOKENS = 32_000  # non-streaming requests stay well under HTTP timeouts


@dataclass
class RunStats:
    """What one agent invocation actually consumed — persisted to agent_executions."""

    provider: str | None = None
    model: str | None = None
    usage: Usage = field(default_factory=Usage)
    llm_calls: int = 0
    tool_calls: int = 0
    cost_usd: float | None = 0.0
    retries: int = 0
    repairs: int = 0
    is_demo: bool = False
    started: float = field(default_factory=time.monotonic)
    notes: list[str] = field(default_factory=list)

    def add_cost(self, cost: float | None) -> None:
        """Unknown pricing for any call makes the agent's total unknown (None), never guessed."""
        if cost is None:
            self.cost_usd = None
        elif self.cost_usd is not None:
            self.cost_usd += cost

    @property
    def duration_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


@dataclass
class AgentOutput(Generic[O]):
    result: O
    stats: RunStats
    extras: dict[str, Any] = field(default_factory=dict)


class BaseAgent:
    key: ClassVar[str]

    def __init__(self, runtime: TaskRuntime) -> None:
        self.rt = runtime
        self.spec: AgentSpec = AGENT_SPECS[self.key]
        self.config = runtime.agent_configs[self.key]
        self.stats = RunStats()

    # ------------------------------------------------------------------ events
    async def emit(self, type_: str, message: str, **payload: Any) -> None:
        await self.rt.emit(type_, message, {"agent": self.key, **payload})

    # ------------------------------------------------------------------ tools
    async def use_tool(self, tool: str) -> None:
        """Enforce the per-agent tool allowlist (in code) and the task tool budget."""
        if tool not in self.spec.allowed_tools:
            raise PermissionError(f"{self.key} agent is not allowed to use tool '{tool}'.")
        await self.rt.budget.reserve_tool_call(self.key, tool)
        self.stats.tool_calls += 1

    # ------------------------------------------------------------------ LLM
    async def call_llm(self, *, system: str, user: str, output_model: type[O],
                       demo_context: dict | None = None) -> O:
        cfg = self.config
        provider = self.rt.providers.llm(cfg.provider)
        if not provider.is_demo and not cfg.model:
            raise LLMError(f"No model configured for the {self.spec.name}. Set "
                           f"{cfg.provider.upper()}_DEFAULT_MODEL or choose one in Agent "
                           "Configuration.", code="model_not_configured")
        system = with_operator_instructions(system, cfg.instructions)
        messages = [LLMMessage("user", user)]
        self.stats.provider, self.stats.model = provider.name, cfg.model
        self.stats.is_demo = provider.is_demo

        want_out = cfg.max_output_tokens
        repaired = widened = False
        # Bounded: original + at most one schema repair + at most one truncation retry.
        for _round in range(3):
            est_in = estimate_tokens(system) + sum(estimate_tokens(m.content) for m in messages)
            priced = None if provider.is_demo else provider.name
            granted = await self.rt.budget.reserve_llm_call(
                self.key, want_out, est_in, provider=priced, model=cfg.model)
            self.stats.llm_calls += 1
            request = LLMRequest(model=cfg.model or "demo", system=system, messages=messages,
                                 output_model=output_model, max_output_tokens=granted,
                                 effort=cfg.effort, timeout_s=float(cfg.timeout_s),
                                 demo_context=demo_context or {})

            async def _on_retry(attempt: int, err: BaseException, sleep_s: float) -> None:
                self.stats.retries += 1
                await self.emit("retry", f"{self.spec.name}: retrying after "
                                f"{getattr(err, 'code', 'error')} (attempt {attempt + 1})",
                                attempt=attempt + 1, error_code=getattr(err, "code", None),
                                wait_s=round(sleep_s, 1))

            try:
                result = await with_backoff(lambda: provider.generate(request),
                                            max_attempts=cfg.max_retries + 1, on_retry=_on_retry)
            except LLMError as exc:
                await self._account(provider.name, cfg.model, exc.usage, granted, est_in, priced)
                if isinstance(exc, LLMOutputError) and exc.code == "invalid_output" \
                        and not repaired:
                    repaired = True
                    self.stats.repairs += 1
                    await self.emit("repair", f"{self.spec.name}: output failed validation; "
                                    "requesting a corrected response.")
                    messages = [*messages,
                                LLMMessage("assistant", exc.raw or "(invalid output)"),
                                LLMMessage("user", "Your previous response did not match the "
                                           f"required schema: {str(exc)[:400]}. Respond again with "
                                           "valid structured output only.")]
                    continue
                # Truncated (thinking + output exceeded max_tokens): retry once with a larger
                # allowance, but only if the budget was not what limited it.
                if exc.code == "truncated" and not widened and granted >= want_out \
                        and want_out < MAX_RETRY_OUTPUT_TOKENS:
                    widened = True
                    want_out = min(want_out * 2, MAX_RETRY_OUTPUT_TOKENS)
                    self.stats.repairs += 1
                    await self.emit("repair", f"{self.spec.name}: output was truncated; retrying "
                                    f"once with up to {want_out:,} output tokens.")
                    continue
                raise
            await self._account(result.provider, result.model, result.usage, granted, est_in, priced)
            self.stats.model = result.model
            if result.served_by_fallback:
                self.stats.notes.append(f"served by fallback model {result.model}")
            return result.parsed
        raise AssertionError("unreachable")  # pragma: no cover

    async def _account(self, provider: str, model: str | None, usage: Usage, granted: int,
                       est_in: int, reserved_provider: str | None = None) -> None:
        cost = await self.rt.budget.record_llm_usage(
            self.key, provider, model or "", usage, granted, est_in,
            reserved_provider=reserved_provider, reserved_model=self.config.model)
        self.stats.usage = self.stats.usage + usage
        if not self.stats.is_demo:
            self.stats.add_cost(cost)

    def output(self, result: O, **extras: Any) -> AgentOutput[O]:
        return AgentOutput(result=result, stats=self.stats, extras=extras)
