"""Planner agent: objective → validated execution plan (subtask DAG)."""

from __future__ import annotations

import re

from app.agents import prompts
from app.agents.base import AgentOutput, BaseAgent
from app.schemas.agent_io import ExecutionPlan, PlannedSubtask


class PlanValidationError(ValueError):
    pass


def normalise_plan(plan: ExecutionPlan, *, max_subtasks: int, max_queries: int) -> tuple[ExecutionPlan, list[str]]:
    """Make the plan safe to execute: unique ids, known deps, no cycles, bounded size.

    Returns the normalised plan and a list of human-readable fixes applied.
    """
    fixes: list[str] = []
    if not plan.subtasks:
        raise PlanValidationError("The plan contains no subtasks.")
    subtasks = plan.subtasks
    if len(subtasks) > max_subtasks:
        fixes.append(f"Trimmed plan from {len(subtasks)} to {max_subtasks} subtasks.")
        subtasks = subtasks[:max_subtasks]

    seen: set[str] = set()
    cleaned: list[PlannedSubtask] = []
    for i, st in enumerate(subtasks, start=1):
        sid = re.sub(r"[^A-Za-z0-9_-]", "", st.id)[:12] or f"t{i}"
        if sid in seen:
            sid = f"t{i}"
            fixes.append(f"Renamed duplicate subtask id to {sid}.")
        seen.add(sid)
        queries = [q.strip()[:300] for q in st.queries if q.strip()][:max_queries] or [st.title[:300]]
        cleaned.append(st.model_copy(update={"id": sid, "queries": queries}))

    ids = {s.id for s in cleaned}
    for idx, st in enumerate(cleaned):
        deps = [d for d in dict.fromkeys(st.depends_on) if d in ids and d != st.id]
        if len(deps) != len(st.depends_on):
            fixes.append(f"Removed unknown/self dependencies from {st.id}.")
        cleaned[idx] = st.model_copy(update={"depends_on": deps})

    # Break cycles: drop edges that point "forward" in a DFS back-edge sense.
    graph = {s.id: list(s.depends_on) for s in cleaned}
    state: dict[str, int] = {}

    def dfs(n: str) -> None:
        state[n] = 1
        for d in list(graph[n]):
            if state.get(d) == 1:
                graph[n].remove(d)
                fixes.append(f"Removed cyclic dependency {n} → {d}.")
            elif d not in state:
                dfs(d)
        state[n] = 2

    for n in graph:
        if n not in state:
            dfs(n)
    cleaned = [s.model_copy(update={"depends_on": graph[s.id]}) for s in cleaned]
    return plan.model_copy(update={"subtasks": cleaned}), fixes


def topological_layers(subtasks: list[PlannedSubtask]) -> list[list[str]]:
    remaining = {s.id: set(s.depends_on) for s in subtasks}
    layers: list[list[str]] = []
    done: set[str] = set()
    while remaining:
        ready = sorted(k for k, deps in remaining.items() if deps <= done)
        if not ready:  # should not happen after normalise_plan
            raise PlanValidationError("Plan dependencies contain a cycle.")
        layers.append(ready)
        done.update(ready)
        for k in ready:
            remaining.pop(k)
    return layers


class PlannerAgent(BaseAgent):
    key = "planner"

    async def run(self, objective: str) -> AgentOutput[ExecutionPlan]:
        limits = self.rt.budget.limits
        await self.emit("agent_progress", "Interpreting the objective and drafting a plan.")
        system = prompts.PLANNER.replace("{max_subtasks}", str(limits.max_subtasks))
        user = (f"<objective>\n{objective}\n</objective>\n\n"
                f"Constraints: at most {limits.max_subtasks} subtasks, at most "
                f"{limits.max_searches_per_subtask} search queries per subtask.")
        plan = await self.call_llm(system=system, user=user, output_model=ExecutionPlan,
                                   demo_context={"objective": objective})
        plan, fixes = normalise_plan(plan, max_subtasks=limits.max_subtasks,
                                     max_queries=limits.max_searches_per_subtask)
        for f in fixes:
            await self.emit("plan_adjusted", f)
        return self.output(plan, fixes=fixes, layers=topological_layers(plan.subtasks))
