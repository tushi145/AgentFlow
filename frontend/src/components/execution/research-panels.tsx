/* Retrieved sources and claim verification, exactly as stored by the backend. */

import { ExternalLink, Globe, Search, ShieldAlert, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState, ErrorState, LoadingRows } from "@/components/common";
import { TONE_CLASSES } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { verdictMeta } from "@/lib/execution-meta";
import { formatClock, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ResearchSource, VerificationResult } from "@/types/api";

const isWebUrl = (u: string) => /^https?:\/\//i.test(u);


/* ------------------------------------------------------------------ sources */

export function SourcesPanel({ sources, verifications, isLoading, error, onRetry }: {
  sources: ResearchSource[] | undefined; verifications: VerificationResult[] | undefined;
  isLoading: boolean; error: unknown; onRetry: () => void;
}) {
  const [q, setQ] = useState("");
  const citedBy = useMemo(() => {
    const m = new Map<string, number>();
    for (const v of verifications ?? []) {
      for (const r of new Set([...v.supporting_refs, ...v.conflicting_refs])) m.set(r, (m.get(r) ?? 0) + 1);
    }
    return m;
  }, [verifications]);

  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (isLoading) return <LoadingRows rows={3} />;
  if (!sources?.length) {
    return <EmptyState icon={Globe} title="No sources yet" description="Sources appear as the Research Agent retrieves them." />;
  }
  const needle = q.trim().toLowerCase();
  const shown = needle
    ? sources.filter((s) => [s.ref, s.title, s.domain, s.url, s.search_query].some((f) => f.toLowerCase().includes(needle)))
    : sources;
  const flagged = sources.filter((s) => s.injection_suspected).length;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter sources" aria-label="Filter sources" className="h-8 max-w-56" />
        <span className="text-xs text-muted-foreground">{sources.length} retrieved{flagged ? ` · ${flagged} flagged` : ""}</span>
      </div>
      <ul className="space-y-2">
        {shown.map((s) => (
          <li key={s.id} id={`source-${s.ref}`} className="rounded-lg border p-3 text-sm" data-source-ref={s.ref}>
            <div className="flex items-start gap-2">
              <Badge variant="outline" className="shrink-0 font-mono">{s.ref}</Badge>
              <div className="min-w-0 flex-1 space-y-1">
                {isWebUrl(s.url) ? (
                  <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex max-w-full items-start gap-1 font-medium hover:underline">
                    <span className="min-w-0 break-words">{s.title}</span><ExternalLink className="mt-1 size-3 shrink-0" aria-hidden />
                  </a>
                ) : (
                  <span className="block font-medium break-words">{s.title}</span>
                )}
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                  <span className="font-mono break-all">{isWebUrl(s.url) ? s.domain : `${s.url} (synthetic demo source — not a web page)`}</span>
                  <span>{s.provider}</span>
                  <span>{formatClock(s.retrieved_at)}</span>
                  {s.relevance_score !== null && <span>relevance {s.relevance_score.toFixed(2)}</span>}
                  {citedBy.get(s.ref) ? <span className="text-foreground">cited in {citedBy.get(s.ref)} verdict{citedBy.get(s.ref) === 1 ? "" : "s"}</span> : null}
                </div>
                {s.snippet && <p className="line-clamp-3 text-xs text-muted-foreground">{s.snippet}</p>}
                {s.search_query && (
                  <p className="flex items-start gap-1 text-xs text-muted-foreground"><Search className="mt-0.5 size-3 shrink-0" /><span className="font-mono break-all">{s.search_query}</span></p>
                )}
                {s.injection_suspected && (
                  <p className={cn("flex items-start gap-1.5 rounded-md border px-2 py-1 text-xs", TONE_CLASSES.warning)}>
                    <ShieldAlert className="mt-0.5 size-3 shrink-0" />
                    Instruction-like text detected; treated as untrusted, low-reliability data
                    {s.injection_rules.length ? ` (${s.injection_rules.join(", ")})` : ""}.
                  </p>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ verification */

function RefChip({ r, source, tone }: { r: string; source?: ResearchSource; tone: "success" | "danger" }) {
  const chip = <Badge variant="outline" className={cn("font-mono text-[11px]", TONE_CLASSES[tone])}>{r}</Badge>;
  if (!source) return chip;
  return (
    <Tooltip>
      <TooltipTrigger asChild><span tabIndex={0}>{chip}</span></TooltipTrigger>
      <TooltipContent className="max-w-72">{source.title} · {source.domain}</TooltipContent>
    </Tooltip>
  );
}

function VerdictItem({ v, sources }: { v: VerificationResult; sources: Map<string, ResearchSource> }) {
  const m = verdictMeta(v.verdict);
  return (
    <li className="space-y-2 rounded-lg border p-3 text-sm" data-verdict={v.verdict}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className={TONE_CLASSES[m.tone]} title={m.hint}>{m.label}</Badge>
        <span className="text-xs text-muted-foreground">confidence <span className="tabular text-foreground">{formatPercent(v.confidence)}</span></span>
      </div>
      <p className="font-medium break-words">{v.claim}</p>
      {v.reasoning && <p className="text-xs text-muted-foreground break-words">{v.reasoning}</p>}
      {(v.supporting_refs.length > 0 || v.conflicting_refs.length > 0) && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {v.supporting_refs.length > 0 && <span className="text-muted-foreground">Supporting</span>}
          {v.supporting_refs.map((r) => <RefChip key={`s-${r}`} r={r} source={sources.get(r)} tone="success" />)}
          {v.conflicting_refs.length > 0 && <span className="ml-1 text-muted-foreground">Conflicting</span>}
          {v.conflicting_refs.map((r) => <RefChip key={`c-${r}`} r={r} source={sources.get(r)} tone="danger" />)}
        </div>
      )}
    </li>
  );
}

export function VerificationPanel({ verifications, sources, isLoading, error, onRetry, demo }: {
  verifications: VerificationResult[] | undefined; sources: ResearchSource[] | undefined;
  isLoading: boolean; error: unknown; onRetry: () => void; demo: boolean;
}) {
  const byRef = useMemo(() => new Map((sources ?? []).map((s) => [s.ref, s])), [sources]);
  const iterations = useMemo(() => [...new Set((verifications ?? []).map((v) => v.iteration))].sort((a, b) => b - a), [verifications]);
  const [selected, setSelected] = useState<number | null>(null);

  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (isLoading) return <LoadingRows rows={3} />;
  if (!verifications?.length) {
    return <EmptyState icon={ShieldCheck} title="No verification results yet" description="Claims are verified against retrieved evidence after analysis." />;
  }
  const it = selected ?? iterations[0];
  const rows = verifications.filter((v) => v.iteration === it);
  const counts = rows.reduce<Record<string, number>>((acc, v) => ({ ...acc, [v.verdict]: (acc[v.verdict] ?? 0) + 1 }), {});
  return (
    <div className="space-y-3">
      {iterations.length > 1 && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Verification iteration">
          {iterations.map((i) => (
            <button key={i} type="button" aria-pressed={i === it} onClick={() => setSelected(i)}
                    className={cn("rounded-md border px-2 py-0.5 text-xs", i === it ? "bg-secondary font-medium" : "text-muted-foreground hover:text-foreground")}>
              Iteration {i + 1}{i === iterations[0] ? " (latest)" : ""}
            </button>
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-1.5" aria-label="Verdict counts">
        {Object.entries(counts).map(([k, n]) => (
          <Badge key={k} variant="outline" className={TONE_CLASSES[verdictMeta(k).tone]}>{verdictMeta(k).label} · {n}</Badge>
        ))}
      </div>
      {demo && <p className="text-xs text-status-demo">Demo mode: verdicts and confidence values are template output — no real verification was performed.</p>}
      <ul className="space-y-2">{rows.map((v) => <VerdictItem key={v.id} v={v} sources={byRef} />)}</ul>
    </div>
  );
}
