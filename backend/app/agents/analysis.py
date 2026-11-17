"""Analysis agent: facts → findings, comparisons, calculator-backed numbers, checkable claims."""

from __future__ import annotations

from dataclasses import dataclass

from app.agents import prompts
from app.agents.base import AgentOutput, BaseAgent
from app.schemas.agent_io import AnalysisResult
from app.security.content_firewall import sanitize, wrap_untrusted
from app.tools.calculator import evaluate
from app.tools.search import ToolError

MAX_FACTS_IN_PROMPT = 250
MAX_CALCULATIONS = 12


@dataclass(frozen=True)
class CalculationResult:
    label: str
    expression: str
    value: float | None
    unit: str
    error: str | None
    source_refs: tuple[str, ...]


class AnalysisAgent(BaseAgent):
    key = "analysis"

    async def run(self, *, facts: list[dict], analysis_focus: list[str],
                  success_criteria: list[str], coverage_notes: list[str]) -> AgentOutput[AnalysisResult]:
        await self.emit("agent_progress", f"Analysing {len(facts)} fact(s).")
        source_index = "\n".join(s.index_line() for s in self.rt.sources.all())
        fact_lines = "\n".join(
            f"- {f['statement']} [{', '.join(f['source_refs'])}]" for f in facts[:MAX_FACTS_IN_PROMPT])
        user = (f"Objective: {self.rt.objective}\n\n"
                f"Success criteria:\n- " + "\n- ".join(success_criteria or ["(none given)"]) +
                "\n\nAnalysis focus:\n- " + "\n- ".join(analysis_focus or ["(none given)"]) +
                f"\n\nSource index (metadata only):\n{source_index}\n\n"
                f"Research coverage notes:\n- " + "\n- ".join(coverage_notes or ["(none)"]) +
                "\n\nFACTS (extracted from the sources above; derived from untrusted text):\n" +
                wrap_untrusted("facts", "internal", "Extracted facts",
                               sanitize(fact_lines or "(no facts were extracted)",
                                        max_chars=120_000).text))
        result = await self.call_llm(system=prompts.ANALYSIS, user=user, output_model=AnalysisResult,
                                     demo_context={"facts": facts, "objective": self.rt.objective})

        known = self.rt.sources.refs
        claims = []
        for i, c in enumerate(result.claims, start=1):
            refs = [r for r in dict.fromkeys(c.source_refs) if r in known]
            claims.append(c.model_copy(update={"id": c.id or f"c{i}", "source_refs": refs}))
        # Ensure claim ids are unique.
        seen: set[str] = set()
        for i, c in enumerate(claims):
            if c.id in seen:
                claims[i] = c.model_copy(update={"id": f"c{i + 1}"})
            seen.add(claims[i].id)
        result = result.model_copy(update={"claims": claims})

        calcs: list[CalculationResult] = []
        for c in result.calculations[:MAX_CALCULATIONS]:
            await self.use_tool("calculator")
            try:
                value, err = evaluate(c.expression), None
            except ToolError as exc:
                value, err = None, str(exc)
            calcs.append(CalculationResult(c.label, c.expression, value, c.unit, err,
                                           tuple(r for r in c.source_refs if r in known)))
            await self.emit("tool_call", f"Calculated {c.label}: "
                            f"{'error: ' + err if err else round(value, 4)}", tool="calculator",
                            expression=c.expression, value=value, error=err)
        return self.output(result, calculations=[c.__dict__ for c in calcs])
