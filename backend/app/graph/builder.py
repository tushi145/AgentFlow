"""Assembles the AgentFlow StateGraph.

    START → plan → plan_gate ─(rejected)→ END
                       │
                       ▼
                   dispatch ──Send×N──► research ─┐   (parallel or sequential, dependency-aware)
                       ▲  └─(none ready)─► analysis
                       └───────────────────────────┘
    analysis → verify ─(sufficient / limits)→ report → evaluate → END
                  └─(gaps)→ plan_gaps → dispatch   (bounded by max_iterations)
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from sqlalchemy.exc import OperationalError

from app.agents.runtime import TaskRuntime
from app.graph import nodes
from app.graph.state import AgentFlowState

# Node-level retry only for transient infrastructure errors (e.g. SQLite busy). LLM/tool errors
# are retried inside agents with visible backoff, so they are not retried twice here.
INFRA_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2.0,
                          retry_on=(OperationalError, ConnectionError))


def build_graph(checkpointer=None):  # type: ignore[no-untyped-def]
    g = StateGraph(AgentFlowState, context_schema=TaskRuntime)
    for name, fn in [("plan", nodes.plan_node), ("plan_gate", nodes.plan_gate_node),
                     ("dispatch", nodes.dispatch_node), ("research", nodes.research_node),
                     ("analysis", nodes.analysis_node), ("verify", nodes.verify_node),
                     ("plan_gaps", nodes.plan_gaps_node), ("report", nodes.report_node),
                     ("evaluate", nodes.evaluate_node)]:
        g.add_node(name, fn, retry_policy=INFRA_RETRY)

    g.add_edge(START, "plan")
    g.add_edge("plan", "plan_gate")
    g.add_conditional_edges("plan_gate", nodes.route_after_plan_gate,
                            {"end": END, "dispatch": "dispatch"})
    g.add_conditional_edges("dispatch", nodes.route_after_dispatch,
                            {"research": "research", "analysis": "analysis", "end": END})
    g.add_edge("research", "dispatch")
    g.add_edge("analysis", "verify")
    g.add_conditional_edges("verify", nodes.route_after_verify,
                            {"report": "report", "plan_gaps": "plan_gaps"})
    g.add_conditional_edges("plan_gaps", nodes.route_after_gaps,
                            {"dispatch": "dispatch", "report": "report"})
    g.add_edge("report", "evaluate")
    g.add_edge("evaluate", END)
    return g.compile(checkpointer=checkpointer)
