"""Deterministic report assembly around the LLM-written prose.

The LLM writes the narrative; code writes everything that must be exact: citation filtering,
the references list, the verification table, comparison tables and run statistics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agents.runtime import SourceRecord
from app.schemas.agent_io import ComparisonTable, ReportDraft

# Any bracket group made only of source refs and separators: [S1], [S1, S2], [S2; S9],
# [S1–S3], [S1-S3], [S1 and S4], [S1][S2] (matched separately).
_SEP = r"\s*(?:,|;|–|—|-|\band\b)\s*"
CITATION = re.compile(rf"\[\s*(S\d+(?:{_SEP}S?\d+)*)\s*\]")
_RANGE = re.compile(r"S(\d+)\s*[–—-]\s*S?(\d+)")
MAX_RANGE = 30


def _expand_refs(group: str) -> list[str]:
    refs: list[str] = []
    for m in _RANGE.finditer(group):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= hi and hi - lo <= MAX_RANGE:
            refs += [f"S{i}" for i in range(lo, hi + 1)]
        else:
            refs += [f"S{lo}", f"S{hi}"]
    group = _RANGE.sub(" ", group)
    refs += re.findall(r"S\d+", group)
    return list(dict.fromkeys(refs))
VERDICT_LABEL = {"supported": "✅ Supported", "partially_supported": "🟡 Partially supported",
                 "unsupported": "❌ Unsupported", "conflicting": "⚠️ Conflicting",
                 "unverifiable": "❔ Unverifiable"}


@dataclass
class CitationCheck:
    text: str
    cited: set[str] = field(default_factory=set)
    removed: list[str] = field(default_factory=list)


def filter_citations(text: str, known: set[str]) -> CitationCheck:
    """Keep only citations to stored sources; remove and record the rest."""
    check = CitationCheck(text="")

    def repl(m: re.Match[str]) -> str:
        refs = _expand_refs(m.group(1))
        good = [r for r in refs if r in known]
        check.removed += [r for r in refs if r not in known]
        check.cited.update(good)
        return f"[{', '.join(good)}]" if good else ""

    check.text = re.sub(r"[ \t]+([.,;:])", r"\1", CITATION.sub(repl, text))
    return check


def _md_escape_cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(_md_escape_cell(h) for h in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_md_escape_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


@dataclass
class RenderInput:
    draft: ReportDraft
    sources: list[SourceRecord]
    comparisons: list[ComparisonTable]
    calculations: list[dict]
    verdicts: list[dict]          # {claim_id, claim, verdict, confidence, supporting_refs, ...}
    stats: dict                   # run statistics for the methodology appendix
    is_demo: bool
    demo_disclaimer: str = ""


@dataclass
class RenderedReport:
    title: str
    summary: str
    markdown: str
    cited_refs: list[str]
    citation_violations: list[str]
    avg_confidence: float | None


def render_report(inp: RenderInput) -> RenderedReport:
    known = {s.ref for s in inp.sources}
    removed: list[str] = []
    cited: set[str] = set()

    def clean(text: str) -> str:
        chk = filter_citations(text, known)
        removed.extend(chk.removed)
        cited.update(chk.cited)
        return chk.text

    d = inp.draft
    parts: list[str] = [f"# {d.title.strip()}"]
    if inp.is_demo:
        parts.append(f"> **{inp.demo_disclaimer}**")
    summary = clean(d.executive_summary)
    parts += ["## Executive summary", summary]

    parts += ["## Methodology", clean(d.methodology), _stats_block(inp.stats)]
    for sec in d.sections:
        parts += [f"## {sec.heading.strip()}", clean(sec.body_markdown)]

    if inp.comparisons:
        parts.append("## Comparison tables")
        for t in inp.comparisons:
            rows = []
            for r in t.rows:
                refs = [x for x in r.source_refs if x in known]
                cited.update(refs)
                cells = (r.cells + ["Not found"] * len(t.columns))[:len(t.columns)]
                rows.append([clean(r.label), *(clean(c) for c in cells), ", ".join(refs) or "—"])
            parts += [f"### {clean(t.title)}", _table(["", *t.columns, "Sources"], rows)]

    good_calcs = [c for c in inp.calculations if c.get("value") is not None]
    if good_calcs:
        parts.append("## Calculations")
        parts.append(_table(["Quantity", "Expression", "Result", "Sources"], [
            [clean(c["label"]), f"`{c['expression']}`", f"{c['value']:,.4g} {c.get('unit', '')}".strip(),
             ", ".join(c.get("source_refs") or []) or "—"] for c in good_calcs]))

    parts += ["## Conclusion", clean(d.conclusion)]

    if inp.verdicts:
        parts.append("## Verification summary")
        counts: dict[str, int] = {}
        for v in inp.verdicts:
            counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
        parts.append(" · ".join(f"{VERDICT_LABEL[k]}: {n}" for k, n in counts.items()))
        parts.append(_table(["Claim", "Verdict", "Confidence", "Evidence"], [
            [v["claim"], VERDICT_LABEL.get(v["verdict"], v["verdict"]), f"{v['confidence']:.0%}",
             ", ".join(v.get("supporting_refs") or []) or "—"] for v in inp.verdicts]))

    limitations = [clean(x) for x in d.limitations]
    if removed:
        limitations.append(f"{len(removed)} citation(s) to unknown sources were removed "
                           "automatically during report assembly.")
    if any(s.injection_suspected for s in inp.sources):
        flagged = ", ".join(s.ref for s in inp.sources if s.injection_suspected)
        limitations.append(f"Sources {flagged} contained instruction-like text (possible prompt "
                           "injection) and were treated as low-reliability.")
    if limitations:
        parts += ["## Limitations", "\n".join(f"- {x}" for x in limitations if x.strip())]

    parts.append("## References")
    ref_lines = []
    for s in inp.sources:
        if s.ref not in cited:
            continue
        warn = " ⚠️ flagged: possible prompt injection" if s.injection_suspected else ""
        ref_lines.append(f"- **[{s.ref}]** {s.title or s.url} — <{s.url}> ({s.domain}){warn}")
    parts.append("\n".join(ref_lines) or "_No sources were cited._")
    uncited = [s for s in inp.sources if s.ref not in cited]
    if uncited:
        parts.append("**Other sources consulted (not cited):**\n" + "\n".join(
            f"- [{s.ref}] {s.title or s.url} — <{s.url}>" for s in uncited))

    confs = [v["confidence"] for v in inp.verdicts]
    return RenderedReport(
        title=d.title.strip(), summary=summary, markdown="\n\n".join(p for p in parts if p).strip() + "\n",
        cited_refs=sorted(cited, key=lambda r: int(r[1:])), citation_violations=removed,
        avg_confidence=round(sum(confs) / len(confs), 3) if confs else None)


def _stats_block(stats: dict) -> str:
    if not stats:
        return ""
    rows = [[k, str(v)] for k, v in stats.items()]
    return "**Run statistics (recorded by the system):**\n\n" + _table(["Metric", "Value"], rows)
