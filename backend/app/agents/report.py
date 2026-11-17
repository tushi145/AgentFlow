"""Report agent: verified material → narrative draft → deterministic, citation-checked report."""

from __future__ import annotations

from app.agents import prompts
from app.agents.base import AgentOutput, BaseAgent
from app.utils.labels import DEMO_DISCLAIMER
from app.schemas.agent_io import AnalysisResult, ReportDraft
from app.services.report_renderer import RenderInput, RenderedReport, render_report


class ReportAgent(BaseAgent):
    key = "report"

    async def run(self, *, report_outline: list[str], analysis: AnalysisResult | None,
                  calculations: list[dict], verdicts: list[dict], gaps: list[str],
                  run_stats: dict, budget_note: str | None = None) -> AgentOutput[RenderedReport]:
        await self.emit("agent_progress", "Writing the final report.")
        sources = self.rt.sources.all()
        source_index = "\n".join(s.index_line() + f" <{s.url}>" for s in sources)
        verified = [v for v in verdicts if v["verdict"] in ("supported", "partially_supported")]
        not_verified = [v for v in verdicts if v not in verified]

        def vline(v: dict) -> str:
            refs = ", ".join(v.get("supporting_refs") or v.get("source_refs") or []) or "no sources"
            return f"- ({v['verdict']}, {v['confidence']:.0%}) {v['claim']} [{refs}]"

        analysis_txt = ""
        if analysis:
            analysis_txt = analysis.summary + "\n" + "\n".join(
                f"- {f.title}: {f.detail} [{', '.join(f.source_refs)}]" for f in analysis.findings)
            for t in analysis.comparisons:
                analysis_txt += f"\nComparison '{t.title}' columns: {', '.join(t.columns)}; rows: " \
                                f"{', '.join(r.label for r in t.rows)} (rendered by the system)."
        calc_txt = "\n".join(f"- {c['label']} = {c['value']} {c.get('unit', '')}"
                             for c in calculations if c.get("value") is not None)
        user = (
            f"Objective: {self.rt.objective}\n\nReport outline:\n- " +
            "\n- ".join(report_outline or ["Findings"]) +
            f"\n\nSOURCE IDS YOU MAY CITE:\n{source_index or '(none)'}\n\n"
            f"ANALYSIS:\n{analysis_txt or '(no analysis available)'}\n\n"
            f"CALCULATOR RESULTS (exact):\n{calc_txt or '(none)'}\n\n"
            "VERIFIED CLAIMS (may be stated as fact, qualify partial support):\n" +
            ("\n".join(vline(v) for v in verified) or "(none)") +
            "\n\nNOT VERIFIED (mention only as limitations):\n" +
            ("\n".join(vline(v) for v in not_verified) or "(none)") +
            "\n\nKNOWN GAPS:\n- " + "\n- ".join(gaps or ["(none)"]) +
            (f"\n\nNOTE: {budget_note}" if budget_note else ""))
        draft = await self.call_llm(system=prompts.REPORT, user=user, output_model=ReportDraft,
                                    demo_context={"objective": self.rt.objective,
                                                  "outline": report_outline,
                                                  "refs": [s.ref for s in sources]})
        rendered = render_report(RenderInput(
            draft=draft, sources=sources, comparisons=analysis.comparisons if analysis else [],
            calculations=calculations, verdicts=verdicts, stats=run_stats, is_demo=self.rt.is_demo,
            demo_disclaimer=DEMO_DISCLAIMER))
        if rendered.citation_violations:
            await self.emit("citation_violation",
                            f"Removed {len(rendered.citation_violations)} citation(s) to unknown "
                            "sources.", refs=rendered.citation_violations)
        return self.output(rendered, draft=draft.model_dump())
