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
from app.tools.quality import NEAR_DUPLICATE_JACCARD, Fingerprint, canonical_url

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
    """Assigns stable task-wide refs (S1, S2, ...) and de-duplicates by canonical URL and by
    near-identical content (syndicated / mirrored copies of the same article)."""

    def __init__(self, persist: PersistSourceFn | None = None) -> None:
        self._by_ref: dict[str, SourceRecord] = {}
        self._by_url: dict[str, str] = {}
        self._prints: dict[str, Fingerprint] = {}
        self._lock = asyncio.Lock()
        self._persist = persist
        self.duplicates_merged = 0  # content duplicates folded into an existing ref

    def _near_duplicate(self, fp: Fingerprint, flagged: bool) -> str | None:
        if not fp.shingles:
            return None
        for ref, other in self._prints.items():
            # A copy that adds injected text keeps its own (flagged) identity.
            if self._by_ref[ref].injection_suspected != flagged:
                continue
            if fp.similarity(other) >= NEAR_DUPLICATE_JACCARD:
                return ref
        return None

    async def add(self, *, url: str, title: str, snippet: str, sanitized: SanitizedContent,
                  search_query: str, provider: str, score: float | None,
                  is_demo: bool) -> tuple[SourceRecord, bool]:
        """Returns (record, created). `created` is False for URL or content duplicates."""
        key = canonical_url(url)
        # Titles come from the web too: sanitise them and merge their injection flags.
        clean_title = sanitize(title, max_chars=300)
        rules = list(dict.fromkeys([*sanitized.matched_rules,
                                    *(f"title:{r}" for r in clean_title.matched_rules)]))
        fp = Fingerprint.of(sanitized.text)
        async with self._lock:
            if key in self._by_url:
                return self._by_ref[self._by_url[key]], False
            dup = self._near_duplicate(fp, bool(rules))
            if dup is not None:
                self._by_url[key] = dup
                self.duplicates_merged += 1
                return self._by_ref[dup], False
            ref = f"S{len(self._by_ref) + 1}"
            rec = SourceRecord(
                ref=ref, url=url, title=clean_title.text, snippet=snippet[:1000],
                domain=urlparse(url).netloc.lower().removeprefix("www."),
                content=sanitized.text, search_query=search_query, provider=provider,
                relevance_score=score, injection_suspected=bool(rules),
                injection_rules=rules, is_demo=is_demo)
            self._by_ref[ref], self._by_url[key] = rec, ref
            self._prints[ref] = fp
        if self._persist:
            await self._persist(rec)
        return rec, True

    def restore(self, records: list[SourceRecord]) -> None:
        for rec in records:
            self._by_ref[rec.ref] = rec
            self._by_url[canonical_url(rec.url)] = rec.ref
            self._prints[rec.ref] = Fingerprint.of(rec.content)

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
