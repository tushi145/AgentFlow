"""Verification agent: claim-by-claim checking against retrieved evidence + sufficiency gate."""

from __future__ import annotations

from app.agents import prompts
from app.agents.base import AgentOutput, BaseAgent
from app.schemas.agent_io import Claim, ClaimVerdict, VerificationReport
from app.security.content_firewall import wrap_untrusted
from app.tools.evidence import EvidenceIndex, chunk_text

MAX_CLAIMS = 24
EVIDENCE_PER_CLAIM = 3
INJECTION_CONFIDENCE_CAP = 0.5
_IMPORTANCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def apply_guards(report: VerificationReport, claims: list[Claim], evidence_refs: dict[str, set[str]],
                 flagged_refs: set[str]) -> tuple[VerificationReport, list[str]]:
    """Deterministic post-checks so the gate cannot be satisfied by unsupported output."""
    notes: list[str] = []
    by_id = {c.id: c for c in claims}
    verdicts: list[ClaimVerdict] = []
    for v in report.verdicts:
        if v.claim_id not in by_id or v.claim_id in {x.claim_id for x in verdicts}:
            continue
        shown = evidence_refs.get(v.claim_id, set())
        sup = [r for r in dict.fromkeys(v.supporting_refs) if r in shown]
        con = [r for r in dict.fromkeys(v.conflicting_refs) if r in shown]
        verdict, conf = v.verdict, max(0.0, min(1.0, v.confidence))
        if verdict in ("supported", "partially_supported") and not sup:
            notes.append(f"{v.claim_id}: marked {verdict} without citing shown evidence → unverifiable")
            verdict, conf = "unverifiable", min(conf, 0.3)
        if sup and all(r in flagged_refs for r in sup) and conf > INJECTION_CONFIDENCE_CAP:
            notes.append(f"{v.claim_id}: support only from injection-flagged sources → confidence capped")
            conf = INJECTION_CONFIDENCE_CAP
        verdicts.append(v.model_copy(update={"verdict": verdict, "confidence": round(conf, 3),
                                             "supporting_refs": sup, "conflicting_refs": con}))
    for c in claims:  # claims the model skipped are explicitly unverifiable
        if c.id not in {v.claim_id for v in verdicts}:
            notes.append(f"{c.id}: no verdict returned → unverifiable")
            verdicts.append(ClaimVerdict(claim_id=c.id, verdict="unverifiable", confidence=0.0,
                                         reasoning="The verification agent returned no verdict.",
                                         supporting_refs=[], conflicting_refs=[]))
    high = [v for v in verdicts if by_id[v.claim_id].importance == "high"]
    ok_high = [v for v in high if v.verdict in ("supported", "partially_supported")]
    ratio = len(ok_high) / len(high) if high else 0.0
    sufficient = report.sufficient and ratio >= 0.6
    if report.sufficient and not sufficient:
        notes.append(f"Sufficiency overridden: only {len(ok_high)}/{len(high)} high-importance "
                     "claims are supported.")
    return report.model_copy(update={"verdicts": verdicts, "sufficient": sufficient,
                                     "gap_queries": report.gap_queries[:4]}), notes


class VerificationAgent(BaseAgent):
    key = "verification"

    async def run(self, *, claims: list[Claim], success_criteria: list[str]) -> AgentOutput[VerificationReport]:
        claims = sorted(claims, key=lambda c: _IMPORTANCE_ORDER.get(c.importance, 3))[:MAX_CLAIMS]
        sources = self.rt.sources.all()
        chunks = [ch for s in sources for ch in chunk_text(s.ref, s.content)]
        index = EvidenceIndex(chunks, embed=self.rt.embed)
        flagged = {s.ref for s in sources if s.injection_suspected}

        await self.use_tool("retrieve_evidence")
        blocks: list[str] = []
        evidence_refs: dict[str, set[str]] = {}
        for c in claims:
            hits = await index.search(c.statement, k=EVIDENCE_PER_CLAIM)
            if c.source_refs:  # always include the best chunk of each cited source
                cited = await index.search(c.statement, k=len(c.source_refs) * 2,
                                           refs=set(c.source_refs))
                hits += [h for h in cited if h not in hits]
            evidence_refs[c.id] = {h.chunk.ref for h in hits}
            ev = "\n".join(
                wrap_untrusted(h.chunk.ref, (self.rt.sources.get(h.chunk.ref) or _NA).url,
                               (self.rt.sources.get(h.chunk.ref) or _NA).title, h.chunk.text,
                               flagged_rules=(self.rt.sources.get(h.chunk.ref) or _NA).injection_rules)
                for h in hits[:6]) or "(no relevant evidence retrieved)"
            blocks.append(f"### Claim {c.id} (importance: {c.importance}; cited: "
                          f"{', '.join(c.source_refs) or 'none'})\n{c.statement}\n\nEvidence:\n{ev}")
        await self.emit("tool_result", f"Retrieved evidence for {len(claims)} claim(s) using "
                        f"{index.method}.", tool="retrieve_evidence", method=index.method)

        user = (f"Objective: {self.rt.objective}\n\nSuccess criteria:\n- " +
                "\n- ".join(success_criteria or ["(none given)"]) +
                f"\n\nCLAIMS TO VERIFY ({len(claims)}):\n\n" + "\n\n".join(blocks))
        report = await self.call_llm(
            system=prompts.VERIFICATION, user=user, output_model=VerificationReport,
            demo_context={"claims": [c.model_dump() for c in claims], "evidence_refs":
                          {k: sorted(v) for k, v in evidence_refs.items()}})
        report, notes = apply_guards(report, claims, evidence_refs, flagged)
        for n in notes:
            await self.emit("verification_guard", n)
        return self.output(report, retrieval_method=index.method, guard_notes=notes)


class _NA:
    url = ""
    title = ""
    injection_rules: list[str] = []
