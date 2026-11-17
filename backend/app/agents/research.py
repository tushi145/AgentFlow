"""Research agent: real web search → (fetch) → sanitise → LLM fact extraction with citations."""

from __future__ import annotations

from dataclasses import dataclass

from app.agents import prompts
from app.agents.base import AgentOutput, BaseAgent
from app.agents.runtime import SourceRecord
from app.llm.base import LLMError
from app.llm.retry import with_backoff
from app.schemas.agent_io import ExtractedFact, ResearchResult
from app.security.content_firewall import sanitize, wrap_untrusted
from app.tools.fetch import fetch_page
from app.tools.search import SearchHit

PER_SOURCE_CHARS = 6000
MIN_CONTENT_CHARS = 400


@dataclass(frozen=True)
class ResearchTask:
    subtask_id: str
    title: str
    description: str
    queries: list[str]
    iteration: int = 0
    context_facts: tuple[str, ...] = ()  # facts from dependency subtasks, for focus


class ResearchAgent(BaseAgent):
    key = "research"

    async def run(self, task: ResearchTask) -> AgentOutput[ResearchResult]:
        limits = self.rt.budget.limits
        searches_left = limits.max_searches_per_subtask
        sources: list[SourceRecord] = []
        failures: list[str] = []

        queries = list(dict.fromkeys(task.queries))[:searches_left]
        sources += await self._search_round(task, queries, failures)
        searches_left -= len(queries)

        if not sources:
            await self.emit("warning", f"No sources retrieved for '{task.title}'.",
                            subtask_id=task.subtask_id, failures=failures)
            return self.output(ResearchResult(
                facts=[], coverage_notes="No sources could be retrieved for this subtask. " +
                ("; ".join(failures) if failures else "Searches returned no results."),
                follow_up_queries=[]), source_refs=[], failures=failures)

        result = await self._extract(task, sources)

        # One model-directed follow-up round if the model identified gaps and budget allows.
        follow = [q for q in result.follow_up_queries if q.strip()][:min(2, max(0, searches_left))]
        if follow:
            await self.emit("agent_progress", f"Follow-up research for '{task.title}'.",
                            subtask_id=task.subtask_id, queries=follow)
            more = await self._search_round(task, follow, failures)
            new = [s for s in more if s.ref not in {x.ref for x in sources}]
            if new:
                sources += new
                result = await self._extract(task, sources)

        valid_refs = {s.ref for s in sources}
        facts, dropped = [], 0
        for f in result.facts:
            refs = [r for r in dict.fromkeys(f.source_refs) if r in valid_refs]
            if not refs:
                dropped += 1
                continue
            facts.append(f.model_copy(update={"source_refs": refs}))
        if dropped:
            await self.emit("citation_violation",
                            f"Dropped {dropped} extracted fact(s) citing sources not provided.",
                            subtask_id=task.subtask_id, count=dropped)
        result = result.model_copy(update={"facts": facts})
        return self.output(result, source_refs=sorted(valid_refs, key=lambda r: int(r[1:])),
                           failures=failures, dropped_facts=dropped)

    # -------------------------------------------------------------- tools
    async def _search_round(self, task: ResearchTask, queries: list[str],
                            failures: list[str]) -> list[SourceRecord]:
        limits = self.rt.budget.limits
        hits: list[tuple[str, SearchHit]] = []
        for q in queries:
            await self.use_tool("web_search")
            await self.emit("tool_call", f"Searching: {q}", tool="web_search", query=q,
                            subtask_id=task.subtask_id)
            try:
                resp = await with_backoff(lambda q=q: self.rt.providers.search.search(q, max_results=5),
                                          max_attempts=3)
            except LLMError as exc:
                failures.append(f"search '{q[:60]}' failed ({exc.code})")
                await self.emit("tool_error", f"Search failed: {exc}", tool="web_search",
                                error_code=exc.code, subtask_id=task.subtask_id)
                continue
            cost = self.rt.budget.pricing.search_cost(resp.provider, resp.credits)
            await self.rt.budget.record_tool_cost(self.key, 0.0 if resp.is_demo else cost)
            if not resp.is_demo:
                self.stats.add_cost(cost)
            await self.emit("tool_result", f"{len(resp.hits)} result(s) for: {q}",
                            tool="web_search", query=q, count=len(resp.hits),
                            urls=[h.url for h in resp.hits], subtask_id=task.subtask_id)
            hits += [(q, h) for h in resp.hits]

        # Keep the best unique URLs within the per-subtask page budget.
        seen: set[str] = set()
        ranked = sorted(hits, key=lambda qh: -(qh[1].score or 0))
        chosen = []
        for q, h in ranked:
            if h.url in seen:
                continue
            seen.add(h.url)
            chosen.append((q, h))
            if len(chosen) >= max(1, limits.max_pages_per_subtask):
                break

        out: list[SourceRecord] = []
        # Structural guarantee: only URLs returned by the search provider for this subtask are
        # ever fetched (page text can't introduce new URLs); asserted below as defence in depth.
        allowed_urls = {h.url for _, h in hits}
        for q, h in chosen:
            content, title = h.raw_content, h.title
            if len(content) < MIN_CONTENT_CHARS and h.url.startswith(("http://", "https://")) \
                    and h.url in allowed_urls:
                content, title = await self._fetch(task, h, content, failures)
            sanitized = sanitize(content or h.snippet, max_chars=PER_SOURCE_CHARS)
            rec, created = await self.rt.sources.add(
                url=h.url, title=title or h.title, snippet=h.snippet, sanitized=sanitized,
                search_query=q, provider=h.provider, score=h.score, is_demo=self.rt.is_demo)
            if created:
                await self.emit("source_added", f"{rec.ref}: {rec.title[:120]}", ref=rec.ref,
                                url=rec.url, domain=rec.domain, subtask_id=task.subtask_id,
                                injection_suspected=rec.injection_suspected)
                if rec.injection_suspected:
                    await self.emit("injection_flagged",
                                    f"{rec.ref} contains instruction-like text; treated as "
                                    "untrusted and low-reliability.", ref=rec.ref,
                                    rules=rec.injection_rules, subtask_id=task.subtask_id)
            out.append(rec)
        return out

    async def _fetch(self, task: ResearchTask, h: SearchHit, fallback: str,
                     failures: list[str]) -> tuple[str, str]:
        try:
            await self.use_tool("fetch_page")
            await self.emit("tool_call", f"Reading page: {h.url}", tool="fetch_page", url=h.url,
                            subtask_id=task.subtask_id)
            page = await with_backoff(lambda: fetch_page(h.url), max_attempts=2)
            return (page.text or fallback), (page.title or h.title)
        except LLMError as exc:
            failures.append(f"fetch {h.url[:80]} failed ({exc.code})")
            await self.emit("tool_error", f"Could not read {h.url}: {exc}", tool="fetch_page",
                            error_code=exc.code, subtask_id=task.subtask_id)
            return fallback, h.title

    # -------------------------------------------------------------- LLM
    async def _extract(self, task: ResearchTask, sources: list[SourceRecord]) -> ResearchResult:
        await self.emit("agent_progress", f"Extracting facts from {len(sources)} source(s).",
                        subtask_id=task.subtask_id)
        blocks = "\n\n".join(wrap_untrusted(s.ref, s.url, s.title, s.content,
                                            flagged_rules=s.injection_rules) for s in sources)
        context = ""
        if task.context_facts:
            context = ("\n\nContext from earlier subtasks (already established):\n- " +
                       "\n- ".join(task.context_facts[:30]))
        user = (f"Overall objective: {self.rt.objective}\n\n"
                f"Subtask {task.subtask_id}: {task.title}\n{task.description}{context}\n\n"
                f"Available source ids: {', '.join(s.ref for s in sources)}\n\n"
                f"SOURCES:\n{blocks}")
        return await self.call_llm(
            system=prompts.RESEARCH, user=user, output_model=ResearchResult,
            demo_context={"subtask": task.title, "refs": [s.ref for s in sources]})


def fact_line(f: ExtractedFact) -> str:
    return f"{f.statement} [{', '.join(f.source_refs)}]"
