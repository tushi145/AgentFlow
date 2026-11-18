/* Evaluation dashboard: only metrics the backend records (app/evaluation/metrics.py, benchmarks.py).
   A metric with no underlying data is shown as "No data" — never as 0. */

import { CheckCircle2, ChevronLeft, ChevronRight, Clock, FlaskConical, Hash, Link2, Loader2, Play, ShieldCheck, Telescope } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { EmptyState, ErrorState, LoadingRows, PageHeader, SectionTitle } from "@/components/common";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ModeBadge, StatusBadge, TONE_CLASSES } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBenchmarks, useEvaluationRuns, useEvaluationSummary, useRunBenchmark } from "@/hooks/use-phase3d";
import { useSystemStatus } from "@/hooks/use-system";
import { formatCompact, formatCost, formatDateTime, formatDuration, formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import type { EvaluationRun } from "@/types/api";

const RUNS_PAGE = 15;

/** A metric tile that says "No data" (with why) instead of inventing a number. */
function MetricCard({ label, value, hint, noData, icon: Icon, loading }: {
  label: string; value: ReactNode | null; hint?: ReactNode; noData?: string; icon: typeof Clock; loading: boolean;
}) {
  const missing = value === null || value === undefined;
  return (
    <Card className="gap-0 py-4" data-metric={label}>
      <CardContent className="space-y-2 px-4">
        <div className="flex items-center justify-between text-xs font-medium text-muted-foreground">
          <span>{label}</span><Icon className="size-3.5" aria-hidden />
        </div>
        {loading ? <Skeleton className="h-7 w-20" /> : missing ? (
          <div className="text-lg font-medium text-muted-foreground" data-no-data>No data</div>
        ) : (
          <div className="tabular text-2xl font-semibold tracking-tight">{value}</div>
        )}
        <div className="text-xs text-muted-foreground">{missing && !loading ? noData : hint}</div>
      </CardContent>
    </Card>
  );
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? null : formatPercent(v, 1));

/** Table cell: em dash with an explanation when the run recorded no value. */
function Cell({ v, title = "Not recorded for this run" }: { v: string | null; title?: string }) {
  return v === null ? <span className="text-muted-foreground" title={title}>—</span> : <>{v}</>;
}

function RunsTable({ items }: { items: EvaluationRun[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="min-w-56">Run</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Citation integrity</TableHead>
            <TableHead className="text-right">Source coverage</TableHead>
            <TableHead className="text-right">Supported claims</TableHead>
            <TableHead className="text-right">Claims</TableHead>
            <TableHead className="text-right">Duration</TableHead>
            <TableHead className="text-right">Tokens</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((r) => {
            const m = r.metrics;
            return (
              <TableRow key={r.task_id} data-run={r.task_id}>
                <TableCell className="max-w-0">
                  <div className="flex items-center gap-2">
                    <ModeBadge mode={r.mode} />
                    <Link to={`/tasks/${r.task_id}`} className="truncate font-medium hover:underline">{r.title}</Link>
                  </div>
                  <div className="text-xs text-muted-foreground">{formatDateTime(r.created_at)}{m.iterations > 1 ? ` · ${m.iterations} iterations` : ""}</div>
                </TableCell>
                <TableCell><StatusBadge status={r.status} /></TableCell>
                <TableCell className="tabular text-right"><Cell v={pct(m.citation_integrity?.rate)} title="No citations in the report" /></TableCell>
                <TableCell className="tabular text-right"><Cell v={pct(m.source_coverage?.rate)} /></TableCell>
                <TableCell className="tabular text-right"><Cell v={pct(m.verification?.supported_rate)} title="No claims verified" /></TableCell>
                <TableCell className="tabular text-right">{m.verification ? formatNumber(m.verification.claims) : <Cell v={null} />}</TableCell>
                <TableCell className="tabular text-right"><Cell v={r.duration_s === null ? null : formatDuration(r.duration_s)} /></TableCell>
                <TableCell className="tabular text-right">{r.mode === "demo" ? <span className="text-muted-foreground" title="Demo templates use no tokens">n/a</span> : formatCompact(r.input_tokens + r.output_tokens)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

export default function EvaluationPage() {
  const summary = useEvaluationSummary();
  const status = useSystemStatus();
  const [mode, setMode] = useState<"live" | "demo" | "all">("live");
  const [page, setPage] = useState(1);
  const runs = useEvaluationRuns({ mode: mode === "all" ? undefined : mode, page, page_size: RUNS_PAGE });
  const [confirmBench, setConfirmBench] = useState(false);
  const runBench = useRunBenchmark();
  const benchmarks = useBenchmarks();
  const anyRunning = (benchmarks.data ?? []).some((b) => b.status === "running");

  const s = summary.data;
  const loading = summary.isLoading;
  const none = s && s.live_runs_evaluated === 0;
  const liveAvailable = !!status.data?.live_mode_available;
  const failures = Object.entries(s?.agent_failures ?? {});
  const totalRuns = runs.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(totalRuns / RUNS_PAGE));

  return (
    <>
      <PageHeader title="Evaluation" description="Quality metrics recorded automatically for every completed run. Aggregates cover LIVE runs only — demo runs use templates and are excluded." />
      <div className="space-y-8">
        {summary.error && <ErrorState error={summary.error} onRetry={() => summary.refetch()} />}
        {none && (
          <div role="note" className={cn("rounded-lg border px-4 py-3 text-sm", TONE_CLASSES.neutral)}>
            No live runs have been evaluated yet, so the aggregate metrics below show <strong>No data</strong> rather than zero.
            {!liveAvailable && " Live execution needs provider API keys (see Settings)."}
          </div>
        )}

        <section aria-labelledby="quality-title" className="space-y-3">
          <SectionTitle><span id="quality-title">Live run quality{s ? ` · ${s.live_runs_evaluated} evaluated` : ""}</span></SectionTitle>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <MetricCard label="Task completion rate" icon={CheckCircle2} loading={loading} value={pct(s?.task_completion_rate)}
                        hint="Completed ÷ finished live runs" noData="No finished live runs" />
            <MetricCard label="Citation integrity" icon={Link2} loading={loading} value={pct(s?.citation_integrity_rate)}
                        hint="Report citations that resolve to stored sources" noData="No live reports with citations" />
            <MetricCard label="Source coverage" icon={Telescope} loading={loading} value={pct(s?.source_coverage_rate)}
                        hint={s?.claims_with_source_rate != null ? `Subtasks with ≥1 source · ${formatPercent(s.claims_with_source_rate, 1)} of claims cite a source` : "Subtasks with at least one source"}
                        noData="No evaluated live runs" />
            <MetricCard label="Supported claims" icon={ShieldCheck} loading={loading} value={pct(s?.supported_claim_rate)}
                        hint={s?.mean_confidence != null ? `Supported or partially supported · mean confidence ${formatPercent(s.mean_confidence, 1)}` : "Supported or partially supported"}
                        noData="No verified claims in live runs" />
            <MetricCard label="Execution duration" icon={Clock} loading={loading}
                        value={s?.duration_s.median == null ? null : formatDuration(s.duration_s.median)}
                        hint={s?.duration_s.mean != null ? `Median of ${s.duration_s.runs} completed · mean ${formatDuration(s.duration_s.mean)} · max ${formatDuration(s.duration_s.max)}` : undefined}
                        noData="No completed live runs" />
            <MetricCard label="Token usage" icon={Hash} loading={loading}
                        value={s?.tokens.mean_per_run == null ? null : `${formatCompact(s.tokens.mean_per_run)} / run`}
                        hint={s?.tokens.total != null ? `${formatNumber(s.tokens.total)} tokens across ${s.tokens.runs} completed runs` : undefined}
                        noData="No completed live runs" />
          </div>
        </section>

        <section aria-labelledby="failures-title">
          <SectionTitle><span id="failures-title">Agent reliability (live)</span></SectionTitle>
          {loading ? <LoadingRows rows={3} /> : failures.length === 0 ? (
            <EmptyState className="py-8" title="No live agent runs recorded" description="Failure rates appear after live executions." />
          ) : (
            <div className="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader><TableRow><TableHead>Agent</TableHead><TableHead className="text-right">Runs</TableHead><TableHead className="text-right">Failed</TableHead><TableHead className="text-right">Failure rate</TableHead></TableRow></TableHeader>
                <TableBody>
                  {failures.map(([k, f]) => (
                    <TableRow key={k}>
                      <TableCell className="font-medium capitalize">{k}</TableCell>
                      <TableCell className="tabular text-right">{f.runs}</TableCell>
                      <TableCell className="tabular text-right">{f.failed}</TableCell>
                      <TableCell className="tabular text-right"><Cell v={pct(f.failure_rate)} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </section>

        <section aria-labelledby="bench-title" className="space-y-3">
          <SectionTitle actions={
            <Button size="sm" variant="outline" disabled={!liveAvailable || runBench.isPending || anyRunning} onClick={() => setConfirmBench(true)}
                    title={liveAvailable ? undefined : "Requires live provider keys"}>
              {runBench.isPending || anyRunning ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />} Run benchmark
            </Button>
          }><span id="bench-title">Verification benchmark</span></SectionTitle>
          <p className="text-sm text-muted-foreground">
            Runs the Verification Agent against a labelled dataset of claims with known verdicts. Accuracy = exact verdict match; binary = supported vs not.
            {!liveAvailable && " Requires live provider keys — benchmarks never use demo templates."}
          </p>
          {benchmarks.error && <ErrorState error={benchmarks.error} onRetry={() => benchmarks.refetch()} />}
          {benchmarks.isLoading ? <LoadingRows rows={2} /> : !benchmarks.data?.length ? (
            <EmptyState icon={FlaskConical} className="py-8" title="No benchmark runs yet" description="Benchmark results appear here after a run." />
          ) : (
            <div className="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader><TableRow><TableHead>Run</TableHead><TableHead>Status</TableHead><TableHead>Model</TableHead><TableHead className="text-right">Accuracy</TableHead><TableHead className="text-right">Binary</TableHead><TableHead className="text-right">Items</TableHead><TableHead className="text-right">Cost</TableHead></TableRow></TableHeader>
                <TableBody>
                  {benchmarks.data.map((b) => {
                    const m = b.metrics as { accuracy?: number | null; binary_accuracy?: number | null; items?: number; error?: string };
                    return (
                      <TableRow key={b.id}>
                        <TableCell className="whitespace-nowrap">#{b.id} · {b.suite}<div className="text-xs text-muted-foreground">{formatDateTime(b.created_at)}</div></TableCell>
                        <TableCell>
                          <Badge variant="outline" className={TONE_CLASSES[b.status === "completed" ? "success" : b.status === "failed" ? "danger" : "running"]}>{b.status}</Badge>
                          {m.error && <div className="mt-1 max-w-56 truncate text-xs text-status-danger" title={m.error}>{m.error}</div>}
                        </TableCell>
                        <TableCell className="font-mono text-xs">{b.model ?? "—"}</TableCell>
                        <TableCell className="tabular text-right"><Cell v={pct(m.accuracy)} /></TableCell>
                        <TableCell className="tabular text-right"><Cell v={pct(m.binary_accuracy)} /></TableCell>
                        <TableCell className="tabular text-right"><Cell v={m.items == null ? null : String(m.items)} /></TableCell>
                        <TableCell className="tabular text-right"><Cell v={b.cost_usd === null ? null : formatCost(b.cost_usd)} title="Pricing not configured or run unfinished" /></TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </section>

        <section aria-labelledby="runs-title" className="space-y-3">
          <SectionTitle actions={
            <Tabs value={mode} onValueChange={(v) => { setMode(v as typeof mode); setPage(1); }}>
              <TabsList><TabsTrigger value="live">Live</TabsTrigger><TabsTrigger value="demo">Demo</TabsTrigger><TabsTrigger value="all">All</TabsTrigger></TabsList>
            </Tabs>
          }><span id="runs-title">Per-run metrics</span></SectionTitle>
          {mode !== "live" && (
            <p className="text-xs text-status-demo">Demo rows measure template output and synthetic sources — they say nothing about research quality.</p>
          )}
          {runs.error && <ErrorState error={runs.error} onRetry={() => runs.refetch()} />}
          {runs.isLoading ? <LoadingRows rows={4} /> : totalRuns === 0 ? (
            <EmptyState className="py-8" title={`No ${mode === "all" ? "" : `${mode} `}runs evaluated yet`} description="Each completed execution records its metrics automatically." />
          ) : (
            <>
              <RunsTable items={runs.data!.items} />
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="tabular">{(page - 1) * RUNS_PAGE + 1}–{Math.min(page * RUNS_PAGE, totalRuns)} of {totalRuns}</span>
                <div className="flex items-center gap-1">
                  <Button variant="ghost" size="icon" className="size-7" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page"><ChevronLeft className="size-4" /></Button>
                  <span className="tabular px-1">Page {page} / {pages}</span>
                  <Button variant="ghost" size="icon" className="size-7" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="Next page"><ChevronRight className="size-4" /></Button>
                </div>
              </div>
            </>
          )}
        </section>
      </div>

      <ConfirmDialog open={confirmBench} onOpenChange={setConfirmBench} title="Run the verification benchmark?"
                     description="This calls the live model configured for the Verification Agent and incurs API cost. It is limited to 3 runs per hour."
                     confirmLabel="Run benchmark" pending={runBench.isPending}
                     onConfirm={() => runBench.mutate("verification", {
                       onSuccess: () => { toast.success("Benchmark started"); setConfirmBench(false); },
                       onError: (e) => { toast.error(errorMessage(e)); setConfirmBench(false); },
                     })} />
    </>
  );
}
