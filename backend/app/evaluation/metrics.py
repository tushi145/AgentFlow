"""Deterministic per-run quality metrics (computed by the `evaluate` graph node).

All metrics here are computed from recorded data — no LLM judgement — so they are cheap,
reproducible and safe to aggregate. LLM-judged citation *support* is a separate, optional,
cost-capped benchmark (see `app.evaluation.benchmarks`).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

SUPPORTED = {"supported", "partially_supported"}


def compute_run_metrics(*, subtasks: dict[str, dict], facts: list[dict], verdicts: list[dict],
                        sources: list[Any], cited_refs: list[str], citation_violations: list[str],
                        agent_failures: list[dict], retries: int, iterations: int,
                        budget_exhausted: bool, completed: bool, mode: str,
                        retrieval_method: str | None, report_quality: dict | None = None,
                        usage: Any = None, agent_timings: dict | None = None,
                        content_duplicates: int = 0) -> dict[str, Any]:
    research = [s for s in subtasks.values()]
    with_sources = [s for s in research if s.get("source_refs")]
    domains = {getattr(s, "domain", "") for s in sources if getattr(s, "domain", "")}
    known = {getattr(s, "ref", "") for s in sources}

    total_citations = len(cited_refs) + len(citation_violations)
    claims_with_source = [v for v in verdicts if v.get("source_refs") or v.get("supporting_refs")]
    verdict_counts = Counter(v["verdict"] for v in verdicts)
    supported = [v for v in verdicts if v["verdict"] in SUPPORTED]
    high = [v for v in verdicts if v.get("importance") == "high"]
    confs = [v["confidence"] for v in verdicts]

    g = Counter()
    for s in research:
        g.update({k: v for k, v in (s.get("grounding") or {}).items() if isinstance(v, int)})
    grounded = g["verified"] + g["corrected"]
    rq = report_quality or {}
    prose = rq.get("prose_citations", 0)

    return {
        "mode": mode,
        "completed": completed,
        "budget_exhausted": budget_exhausted,
        "iterations": iterations,
        "citation_integrity": {
            "description": "Share of report citations that resolve to stored sources.",
            "valid": len(cited_refs), "removed": len(citation_violations),
            "rate": round(len(cited_refs) / total_citations, 4) if total_citations else None,
            "unknown_refs_in_facts": sum(1 for f in facts for r in f.get("source_refs", [])
                                         if r not in known),
        },
        "source_coverage": {
            "subtasks": len(research),
            "subtasks_with_sources": len(with_sources),
            "rate": round(len(with_sources) / len(research), 4) if research else None,
            "sources": len(sources),
            "unique_domains": len(domains),
            "sources_per_subtask": round(len(sources) / len(research), 2) if research else None,
            "claims_with_source_rate": round(len(claims_with_source) / len(verdicts), 4) if verdicts else None,
            "flagged_injection_sources": sum(1 for s in sources if getattr(s, "injection_suspected", False)),
        },
        "verification": {
            "claims": len(verdicts),
            "verdicts": dict(verdict_counts),
            "supported_rate": round(len(supported) / len(verdicts), 4) if verdicts else None,
            "high_importance_supported_rate": round(
                sum(1 for v in high if v["verdict"] in SUPPORTED) / len(high), 4) if high else None,
            "mean_confidence": round(sum(confs) / len(confs), 4) if confs else None,
            "retrieval_method": retrieval_method,
        },
        "grounding": {
            "description": "Extracted facts whose quoted evidence was found verbatim (or nearly) "
                           "in a cited source. Ungrounded facts are dropped before analysis.",
            "facts_extracted": g["facts_in"],
            "quote_verified": g["verified"],
            "citation_corrected": g["corrected"],
            "dropped_ungrounded_quote": g["ungrounded"],
            "dropped_unknown_source": g["unknown_refs"],
            "dropped_injected": g["injected"],
            "grounded_rate": round(grounded / g["facts_in"], 4) if g["facts_in"] else None,
            "citation_accuracy": round(g["verified"] / g["facts_in"], 4) if g["facts_in"] else None,
        },
        "report_quality": {
            "prose_citations": prose,
            "citation_support_rate": round(rq.get("prose_citations_to_verified_sources", 0) / prose, 4)
            if prose else None,
            "numeric_sentences": rq.get("numeric_sentences"),
            "uncited_numeric_sentences": rq.get("uncited_numeric_sentences"),
            "unsafe_markup_removed": rq.get("unsafe_markup_removed"),
            "disclosed_conflicts": rq.get("disclosed_conflicts"),
            "content_duplicates_merged": content_duplicates,
        },
        "cost": _cost_block(usage, agent_timings or {}, mode),
        "reliability": {
            "agent_failures": len(agent_failures),
            "failures_by_agent": dict(Counter(f.get("agent", "?") for f in agent_failures)),
            "degraded_agents": sorted({f.get("agent", "?") for f in agent_failures if f.get("degraded")}),
            "retries": retries,
        },
    }


def _cost_block(usage: Any, timings: dict, mode: str) -> dict[str, Any]:
    """Tokens, calls, cost (only when every call was priced) and per-agent latency."""
    if usage is None:
        return {}
    per_agent = getattr(usage, "per_agent", {}) or {}
    return {
        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
        "llm_calls": usage.llm_calls, "tool_calls": usage.tool_calls,
        "cost_usd": None if mode == "demo" or not usage.cost_complete else usage.cost_usd,
        "cost_complete": usage.cost_complete if mode != "demo" else None,
        "elapsed_s": usage.elapsed_s,
        "per_agent": {k: {"input_tokens": v.get("input_tokens", 0),
                          "output_tokens": v.get("output_tokens", 0),
                          "llm_calls": v.get("llm_calls", 0),
                          "cost_usd": None if mode == "demo" or not v.get("cost_complete", True)
                          else round(v.get("cost_usd", 0.0), 6),
                          **({"latency_ms": timings[k]} if k in timings else {})}
                      for k, v in per_agent.items()},
    }
