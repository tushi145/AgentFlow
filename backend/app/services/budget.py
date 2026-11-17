"""Per-task cost & token governance.

`BudgetGuard` is the single choke point every LLM/tool call goes through. It is held outside
LangGraph state (one instance per running task) so parallel branches cannot race on counters.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from app.config.settings import Settings
from app.llm.base import Usage

_BUNDLED_PRICING = Path(__file__).resolve().parents[1] / "config" / "pricing.json"


# ---------------------------------------------------------------------------- limits
class TaskLimits(BaseModel):
    """Per-task limits. Requests may lower defaults; they are clamped to server ceilings."""

    max_total_tokens: int = Field(ge=1_000)
    max_llm_calls: int = Field(ge=1, le=500)
    max_tool_calls: int = Field(ge=0, le=1000)
    timeout_seconds: int = Field(ge=30, le=7200)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_iterations: int = Field(ge=1, le=5)
    max_subtasks: int = Field(default=6, ge=1, le=12)
    max_searches_per_subtask: int = Field(default=3, ge=1, le=8)
    max_pages_per_subtask: int = Field(default=4, ge=0, le=10)

    @classmethod
    def defaults(cls, s: Settings) -> TaskLimits:
        return cls(max_total_tokens=s.default_max_total_tokens, max_llm_calls=s.default_max_llm_calls,
                   max_tool_calls=s.default_max_tool_calls, timeout_seconds=s.default_timeout_seconds,
                   max_cost_usd=s.default_max_cost_usd, max_iterations=s.default_max_iterations)

    @classmethod
    def resolve(cls, s: Settings, overrides: dict | None) -> TaskLimits:
        merged = cls.defaults(s).model_dump()
        merged.update({k: v for k, v in (overrides or {}).items() if v is not None})
        merged["max_total_tokens"] = min(merged["max_total_tokens"], s.max_total_tokens_ceiling)
        return cls.model_validate(merged)


# ---------------------------------------------------------------------------- pricing
@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float | None = None


class PricingTable:
    def __init__(self, data: dict) -> None:
        self._llm: dict[str, dict[str, ModelPrice]] = {
            prov: {m: ModelPrice(**p) for m, p in models.items()}
            for prov, models in data.get("llm", {}).items()}
        self._search: dict[str, float] = {
            prov: float(cfg.get("usd_per_credit", 0)) for prov, cfg in data.get("search", {}).items()}

    @classmethod
    def load(cls, path: Path | None = None) -> PricingTable:
        return cls(json.loads((path or _BUNDLED_PRICING).read_text(encoding="utf-8")))

    def price(self, provider: str, model: str) -> ModelPrice | None:
        return self._llm.get(provider, {}).get(model)

    def llm_cost(self, provider: str, model: str, usage: Usage) -> float | None:
        """USD cost, or None when pricing for this model is not configured (never guessed)."""
        if usage.total == 0 and not usage.cache_read_tokens:
            return 0.0
        p = self.price(provider, model)
        if p is None:
            return None
        uncached_in = max(0, usage.input_tokens)
        cost = uncached_in * p.input_per_mtok + usage.output_tokens * p.output_per_mtok
        if usage.cache_read_tokens and p.cache_read_per_mtok is not None:
            cost += usage.cache_read_tokens * p.cache_read_per_mtok
        return round(cost / 1_000_000, 6)

    def search_cost(self, provider: str, credits: float) -> float | None:
        rate = self._search.get(provider)
        return None if rate is None else round(rate * credits, 6)


# ---------------------------------------------------------------------------- guard
class BudgetExceeded(Exception):
    def __init__(self, limit: str, message: str) -> None:
        super().__init__(message)
        self.limit = limit


@dataclass
class AgentUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    cost_complete: bool = True  # False if any call had unknown pricing


@dataclass
class BudgetSnapshot:
    input_tokens: int
    output_tokens: int
    llm_calls: int
    tool_calls: int
    cost_usd: float | None
    cost_complete: bool
    elapsed_s: float
    limits: dict
    per_agent: dict[str, dict] = field(default_factory=dict)


class BudgetGuard:
    """Enforces limits. A small reserve of tokens and one LLM call is held back for the final
    report agent, so a run that hits its budget can still produce a (partial) report."""

    FINAL_AGENT = "report"

    def __init__(self, limits: TaskLimits, pricing: PricingTable, *,
                 final_reserve_tokens: int | None = None, final_reserve_calls: int = 1) -> None:
        self.limits = limits
        self.pricing = pricing
        self.final_reserve_tokens = (final_reserve_tokens if final_reserve_tokens is not None
                                     else min(30_000, limits.max_total_tokens // 5))
        self.final_reserve_calls = final_reserve_calls
        self._per_agent: dict[str, AgentUsage] = {}
        self._reserved_tokens = 0
        self._lock = asyncio.Lock()
        self._started = time.monotonic()

    # -- totals
    def _tot(self, attr: str) -> int | float:
        return sum(getattr(a, attr) for a in self._per_agent.values())

    @property
    def tokens_used(self) -> int:
        return int(self._tot("input_tokens") + self._tot("output_tokens"))

    @property
    def cost_usd(self) -> float:
        return float(self._tot("cost_usd"))

    @property
    def cost_complete(self) -> bool:
        return all(a.cost_complete for a in self._per_agent.values())

    def elapsed_s(self) -> float:
        return time.monotonic() - self._started

    def _agent(self, key: str) -> AgentUsage:
        return self._per_agent.setdefault(key, AgentUsage())

    # -- LLM calls
    async def reserve_llm_call(self, agent: str, requested_max_output: int,
                               est_input_tokens: int) -> int:
        """Check limits before an LLM call; returns the clamped `max_output_tokens` to use."""
        async with self._lock:
            final = agent == self.FINAL_AGENT
            self._check_common(final=final)
            call_cap = (self.limits.max_llm_calls if final else
                        max(1, self.limits.max_llm_calls - self.final_reserve_calls))
            if self._tot("llm_calls") >= call_cap:
                raise BudgetExceeded("max_llm_calls",
                                     f"LLM call limit reached ({self.limits.max_llm_calls}).")
            token_cap = self.limits.max_total_tokens - (0 if final else self.final_reserve_tokens)
            remaining = (token_cap - self.tokens_used - self._reserved_tokens - est_input_tokens)
            if remaining < 512:
                raise BudgetExceeded("max_total_tokens",
                                     f"Token budget exhausted ({self.limits.max_total_tokens}).")
            granted = min(requested_max_output, remaining)
            self._reserved_tokens += granted + est_input_tokens
            self._agent(agent).llm_calls += 1
            return granted

    async def record_llm_usage(self, agent: str, provider: str, model: str, usage: Usage,
                               reserved_output: int, est_input_tokens: int) -> float | None:
        async with self._lock:
            self._reserved_tokens = max(0, self._reserved_tokens - reserved_output - est_input_tokens)
            a = self._agent(agent)
            a.input_tokens += usage.input_tokens
            a.output_tokens += usage.output_tokens
            a.cache_read_tokens += usage.cache_read_tokens
            cost = self.pricing.llm_cost(provider, model, usage)
            if cost is None:
                a.cost_complete = False
            else:
                a.cost_usd += cost
            return cost

    async def release(self, reserved_output: int, est_input_tokens: int) -> None:
        async with self._lock:
            self._reserved_tokens = max(0, self._reserved_tokens - reserved_output - est_input_tokens)

    # -- tool calls
    async def reserve_tool_call(self, agent: str, tool: str) -> None:
        async with self._lock:
            self._check_common()
            if self._tot("tool_calls") >= self.limits.max_tool_calls:
                raise BudgetExceeded("max_tool_calls",
                                     f"Tool call limit reached ({self.limits.max_tool_calls}).")
            self._agent(agent).tool_calls += 1

    async def record_tool_cost(self, agent: str, cost: float | None) -> None:
        async with self._lock:
            a = self._agent(agent)
            if cost is None:
                a.cost_complete = False
            else:
                a.cost_usd += cost

    def _check_common(self, final: bool = False) -> None:
        # The final report gets a 10% grace period on the time limit.
        grace = 1.1 if final else 1.0
        if self.elapsed_s() > self.limits.timeout_seconds * grace:
            raise BudgetExceeded("timeout_seconds",
                                 f"Execution time limit reached ({self.limits.timeout_seconds}s).")
        if self.limits.max_cost_usd is not None and not final and                 self.cost_usd >= self.limits.max_cost_usd:
            raise BudgetExceeded("max_cost_usd",
                                 f"Cost limit reached (${self.limits.max_cost_usd:.2f}).")

    def restore(self, per_agent: dict[str, dict], elapsed_s: float = 0.0) -> None:
        """Re-seed counters when a run resumes after an approval pause or restart."""
        for key, vals in per_agent.items():
            a = self._agent(key)
            for f in ("input_tokens", "output_tokens", "cache_read_tokens", "llm_calls",
                      "tool_calls"):
                setattr(a, f, int(vals.get(f, 0) or 0))
            cost = vals.get("cost_usd")
            a.cost_complete = cost is not None
            a.cost_usd = float(cost or 0.0)
        self._started = time.monotonic() - elapsed_s

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            input_tokens=int(self._tot("input_tokens")), output_tokens=int(self._tot("output_tokens")),
            llm_calls=int(self._tot("llm_calls")), tool_calls=int(self._tot("tool_calls")),
            cost_usd=round(self.cost_usd, 6) if self._per_agent else 0.0,
            cost_complete=self.cost_complete, elapsed_s=round(self.elapsed_s(), 2),
            limits=self.limits.model_dump(),
            per_agent={k: asdict(v) for k, v in self._per_agent.items()})


def estimate_tokens(text: str) -> int:
    """Cheap, conservative pre-flight estimate (~3.5 chars/token). Real usage comes from the API."""
    return int(len(text) / 3.5) + 16


# ---------------------------------------------------------------------------- estimator
# Conservative per-call token profiles used until enough history exists (labelled "estimate").
DEFAULT_PROFILES: dict[str, tuple[int, int]] = {
    "planner": (2_500, 2_500),
    "research": (14_000, 3_000),
    "analysis": (16_000, 5_000),
    "verification": (20_000, 5_000),
    "report": (18_000, 7_000),
}


@dataclass(frozen=True)
class CostEstimate:
    input_tokens: int
    output_tokens: int
    llm_calls: int
    search_calls: int
    cost_usd: float | None
    cost_complete: bool
    breakdown: dict[str, dict]
    basis: str = "estimate"


def estimate_run_cost(*, research_subtasks: int, searches_per_subtask: int, iterations: int,
                      agent_models: dict[str, tuple[str, str | None]], pricing: PricingTable,
                      search_provider: str, search_credits_per_query: float,
                      profiles: dict[str, tuple[int, int]] | None = None) -> CostEstimate:
    """Upper-bound estimate for the remainder of a run after planning."""
    profiles = profiles or DEFAULT_PROFILES
    calls = {"research": research_subtasks * iterations, "analysis": iterations,
             "verification": iterations, "report": 1}
    total_in = total_out = n_calls = 0
    cost = 0.0
    complete = True
    breakdown: dict[str, dict] = {}
    for agent, n in calls.items():
        if agent not in agent_models:
            continue
        p_in, p_out = profiles.get(agent, DEFAULT_PROFILES[agent])
        u = Usage(input_tokens=p_in * n, output_tokens=p_out * n)
        provider, model = agent_models[agent]
        c = pricing.llm_cost(provider, model or "", u)
        complete &= c is not None
        cost += c or 0.0
        total_in, total_out, n_calls = total_in + u.input_tokens, total_out + u.output_tokens, n_calls + n
        breakdown[agent] = {"calls": n, "input_tokens": u.input_tokens,
                            "output_tokens": u.output_tokens, "cost_usd": c}
    searches = research_subtasks * searches_per_subtask * iterations
    sc = pricing.search_cost(search_provider, searches * search_credits_per_query)
    complete &= sc is not None
    cost += sc or 0.0
    breakdown["search"] = {"calls": searches, "cost_usd": sc}
    # An incomplete estimate (some model unpriced) is reported as unknown, never as a partial
    # sum that would understate cost against the approval threshold.
    return CostEstimate(total_in, total_out, n_calls, searches,
                        round(cost, 4) if complete else None, complete, breakdown)
