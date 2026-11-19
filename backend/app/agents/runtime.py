"""Per-task runtime objects shared by all agents of one execution.

Held outside LangGraph state on purpose: they contain live clients, locks and counters that
parallel branches share. Everything that must survive a restart is persisted via callbacks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.config.settings import Settings
from app.llm.factory import ProviderSet
from app.security.content_firewall import SanitizedContent, sanitize
from app.services.agent_config_service import EffectiveAgentConfig
from app.services.budget import BudgetGuard
from app.tools.evidence import EmbedFn

EmitFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]
PersistSourceFn = Callable[["SourceRecord"], Awaitable[None]]


@dataclass
class SourceRecord:
    ref: str
    url: str
    title: str
    domain: str
    snippet: str
    content: str           # sanitised text used as evidence
    search_query: str
    provider: str
    relevance_score: float | None
    injection_suspected: bool
    injection_rules: list[str]
    is_demo: bool

    def index_line(self) -> str:
        flag = " ⚠ possible prompt injection" if self.injection_suspected else ""
        return f"{self.ref}: {self.title or '(untitled)'} — {self.domain}{flag}"


class SourceRegistry:
    """Assigns stable task-wide refs (S1, S2, ...) and de-duplicates by URL."""

    def __init__(self, persist: PersistSourceFn | None = None) -> None:
        self._by_ref: dict[str, SourceRecord] = {}
        self._by_url: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._persist = persist

    async def add(self, *, url: str, title: str, snippet: str, sanitized: SanitizedContent,
                  search_query: str, provider: str, score: float | None,
                  is_demo: bool) -> tuple[SourceRecord, bool]:
        """Returns (record, created)."""
        key = url.rstrip("/")
        # Titles come from the web too: sanitise them and merge their injection flags.
        clean_title = sanitize(title, max_chars=300)
        rules = list(dict.fromkeys([*sanitized.matched_rules,
                                    *(f"title:{r}" for r in clean_title.matched_rules)]))
        async with self._lock:
            if key in self._by_url:
                return self._by_ref[self._by_url[key]], False
            ref = f"S{len(self._by_ref) + 1}"
            rec = SourceRecord(
                ref=ref, url=url, title=clean_title.text, snippet=snippet[:1000],
                domain=urlparse(url).netloc.lower().removeprefix("www."),
                content=sanitized.text, search_query=search_query, provider=provider,
                relevance_score=score, injection_suspected=bool(rules),
                injection_rules=rules, is_demo=is_demo)
            self._by_ref[ref], self._by_url[key] = rec, ref
        if self._persist:
            await self._persist(rec)
        return rec, True

    def restore(self, records: list[SourceRecord]) -> None:
        for rec in records:
            self._by_ref[rec.ref] = rec
            self._by_url[rec.url.rstrip("/")] = rec.ref

    def get(self, ref: str) -> SourceRecord | None:
        return self._by_ref.get(ref)

    @property
    def refs(self) -> set[str]:
        return set(self._by_ref)

    def all(self) -> list[SourceRecord]:
        return sorted(self._by_ref.values(), key=lambda r: int(r.ref[1:]))

    def __len__(self) -> int:
        return len(self._by_ref)


@dataclass
class TaskRuntime:
    task_id: str
    mode: str
    objective: str
    settings: Settings
    providers: ProviderSet
    budget: BudgetGuard
    agent_configs: dict[str, EffectiveAgentConfig]
    sources: SourceRegistry
    emit: EmitFn
    embed: EmbedFn | None = None
    store: Any = None  # ExecutionStore (typed loosely to avoid an import cycle)
    execution_mode: str = "parallel"
    approval_mode: str = "none"
    extra: dict[str, Any] = field(default_factory=dict)
    # Caps concurrent web searches across all parallel research branches of this task
    # (protects provider rate limits).
    search_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4))

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"
