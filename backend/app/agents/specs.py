"""Static definitions of the five specialised agents.

These describe *what* each agent is allowed to do. The tool allowlist here is enforced in
code by the tool executor; it is not merely a prompt suggestion.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentSpec:
    key: str
    name: str
    description: str
    role: str
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    default_max_output_tokens: int = 8000
    default_effort: str | None = None  # provider-specific; None = provider default


AGENT_SPECS: dict[str, AgentSpec] = {
    "planner": AgentSpec(
        key="planner", name="Planner Agent",
        description="Interprets the objective, defines success criteria and decomposes the work "
                    "into a dependency graph of research subtasks with concrete search queries.",
        role="Planning & task decomposition",
        default_max_output_tokens=8000,
    ),
    "research": AgentSpec(
        key="research", name="Research Agent",
        description="Runs web searches, reads the returned pages and extracts atomic, source-"
                    "attributed facts. Never states facts that are not in the retrieved sources.",
        role="Web research & evidence extraction",
        allowed_tools=frozenset({"web_search", "fetch_page"}),
        default_max_output_tokens=12000,
    ),
    "analysis": AgentSpec(
        key="analysis", name="Analysis Agent",
        description="Compares alternatives, identifies patterns and performs calculations over "
                    "the collected facts, producing explicit claims that cite their sources.",
        role="Comparison, synthesis & calculation",
        allowed_tools=frozenset({"calculator"}),
        default_max_output_tokens=12000,
    ),
    "verification": AgentSpec(
        key="verification", name="Verification Agent",
        description="Cross-checks each claim against retrieved evidence, flags unsupported or "
                    "conflicting statements, assigns calibrated confidence and requests "
                    "additional research when coverage is insufficient.",
        role="Claim verification & quality gate",
        allowed_tools=frozenset({"retrieve_evidence"}),
        default_max_output_tokens=12000,
    ),
    "report": AgentSpec(
        key="report", name="Report Agent",
        description="Writes the final structured report: executive summary, methodology, "
                    "findings, verification summary, limitations and cited sources.",
        role="Report generation",
        default_max_output_tokens=16000,
    ),
}

AGENT_ORDER = ["planner", "research", "analysis", "verification", "report"]
REQUIRED_AGENTS = frozenset({"planner", "research", "report"})
