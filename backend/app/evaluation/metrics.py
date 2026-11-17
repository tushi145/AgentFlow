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
                        retrieval_method: str | None) -> dict[str, Any]:
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
        "reliability": {
            "agent_failures": len(agent_failures),
            "failures_by_agent": dict(Counter(f.get("agent", "?") for f in agent_failures)),
            "retries": retries,
        },
    }
