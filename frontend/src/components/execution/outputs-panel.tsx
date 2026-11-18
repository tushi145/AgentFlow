/* Intermediate agent outputs (agent_executions rows), final report and per-agent usage. */

import { ChevronRight, Download, FileText } from "lucide-react";

import { EmptyState, ErrorState, LoadingRows } from "@/components/common";
import { NodeStatePill } from "@/components/execution/workflow-graph";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AGENT_LABEL } from "@/lib/execution-meta";
import { formatCompact, formatCost, formatDuration, formatNumber, formatPercent } from "@/lib/format";
import type { NodeState } from "@/lib/workflow";
import { urls } from "@/services/api";
import type { AgentExecution, ReportSummary, Subtask, TaskDetail, TaskUsage } from "@/types/api";

const AGENT_NAME = AGENT_LABEL;

const execNodeState = (s: string): NodeState =>
  s === "running" ? "running" : s === "completed" ? "completed" : s === "failed" ? "failed"
    : s === "cancelled" || s === "aborted" ? "stopped" : "pending";

type Obj = Record<string, unknown>;
const arr = (v: unknown): Obj[] => (Array.isArray(v) ? (v as Obj[]) : []);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

function Refs({ refs }: { refs: unknown }) {
  const list = Array.isArray(refs) ? (refs as string[]) : [];
  if (!list.length) return null;
  return <span className="ml-1 font-mono text-[11px] text-muted-foreground">[{list.join(", ")}]</span>;
}

/** Renders the stored output of an agent run; unknown shapes fall back to raw JSON. */
function OutputPreview({ exec }: { exec: AgentExecution }) {
  const result = (exec.output?.result ?? null) as Obj | null;
  if (exec.error_message) {
    return <p className="text-xs text-status-danger">{exec.error_code ? <span className="font-mono">{exec.error_code}: </span> : null}{exec.error_message}</p>;
  }
  if (!result) return <p className="text-xs text-muted-foreground">{exec.status === "running" ? "Still running — output is stored when the agent finishes." : "No output was stored for this run."}</p>;
  if (result.truncated) return <p className="text-xs text-muted-foreground">Output too large to store in full (truncated by the backend).</p>;

  const body = (() => {
    switch (exec.agent_key) {
      case "planner":
        return (
          <div className="space-y-2">
            {str(result.objective_summary) && <p>{str(result.objective_summary)}</p>}
            <ol className="list-decimal space-y-0.5 pl-5">
              {arr(result.subtasks).map((s) => <li key={String(s.id)}><span className="font-mono text-muted-foreground">{String(s.id)}</span> {String(s.title)}</li>)}
            </ol>
            {arr(result.success_criteria).length > 0 && (
              <div><div className="font-medium">Success criteria</div><ul className="list-disc pl-5">{(result.success_criteria as string[]).map((c) => <li key={c}>{c}</li>)}</ul></div>
            )}
          </div>
        );
      case "research":
        return (
          <div className="space-y-2">
            <ul className="list-disc space-y-0.5 pl-5">
              {arr(result.facts).map((f, i) => <li key={i}>{String(f.statement)}<Refs refs={f.source_refs} /></li>)}
            </ul>
            {arr(result.facts).length === 0 && <p className="text-muted-foreground">No facts extracted.</p>}
            {str(result.coverage_notes) && <p className="text-muted-foreground">Coverage: {str(result.coverage_notes)}</p>}
          </div>
        );
      case "analysis":
        return (
          <div className="space-y-2">
            {str(result.summary) && <p>{str(result.summary)}</p>}
            {arr(result.findings).length > 0 && (
              <ul className="list-disc space-y-0.5 pl-5">
                {arr(result.findings).map((f, i) => <li key={i}><span className="font-medium">{String(f.title)}</span> — {String(f.detail)}<Refs refs={f.source_refs} /></li>)}
              </ul>
            )}
            {arr(result.gaps).length > 0 && <p className="text-muted-foreground">Gaps: {(result.gaps as unknown as string[]).join("; ")}</p>}
          </div>
        );
      case "verification":
        return (
          <div className="space-y-1">
            <p>{arr(result.verdicts).length} verdict(s) · sufficient: <span className="font-medium">{String(result.sufficient)}</span></p>
            {str(result.sufficiency_reasoning) && <p className="text-muted-foreground">{str(result.sufficiency_reasoning)}</p>}
            {arr(result.gap_queries).length > 0 && <p className="text-muted-foreground">Gap queries: {(result.gap_queries as unknown as string[]).join("; ")}</p>}
          </div>
        );
      case "report":
        return (
          <div className="space-y-1">
            {str(result.title) && <p className="font-medium">{str(result.title)}</p>}
            {str(result.summary) && <p className="text-muted-foreground">{str(result.summary)}</p>}
          </div>
        );
      default:
        return null;
    }
  })();
  return (
    <div className="space-y-2 text-xs">
      {body}
      <details>
        <summary className="cursor-pointer text-[11px] text-muted-foreground hover:text-foreground">Raw output</summary>
        <pre className="mt-1 max-h-72 overflow-auto rounded bg-muted p-2 font-mono text-[11px] whitespace-pre-wrap break-all">
          {JSON.stringify(exec.output, null, 2).slice(0, 20_000)}
        </pre>
      </details>
    </div>
  );
}

function ExecutionRow({ exec, subtask, demo }: { exec: AgentExecution; subtask?: Subtask; demo: boolean }) {
  return (
    <Collapsible asChild>
      <li className="rounded-lg border" data-execution-id={exec.id}>
        <CollapsibleTrigger className="group flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/40">
          <ChevronRight className="size-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-90" aria-hidden />
          <span className="font-medium">{AGENT_NAME[exec.agent_key] ?? exec.agent_key}</span>
          {subtask && <span className="min-w-0 truncate text-xs text-muted-foreground"><span className="font-mono">{subtask.plan_key}</span> {subtask.title}</span>}
          {exec.iteration > 0 && <Badge variant="outline" className="h-5 px-1.5 text-[11px]">iter {exec.iteration + 1}</Badge>}
          <span className="ml-auto flex shrink-0 items-center gap-2">
            <span className="hidden text-xs text-muted-foreground tabular sm:inline">
              {exec.duration_ms !== null ? formatDuration(exec.duration_ms / 1000) : ""}
              {!demo && exec.status !== "running" ? ` · ${formatCompact(exec.input_tokens + exec.output_tokens)} tok` : ""}
            </span>
            <NodeStatePill state={execNodeState(exec.status)} />
          </span>
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-2 border-t px-3 py-2">
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
            <span>model <span className="font-mono text-foreground">{exec.model ?? "—"}</span></span>
            <span>LLM calls <span className="tabular text-foreground">{exec.llm_calls}</span></span>
            <span>tool calls <span className="tabular text-foreground">{exec.tool_calls}</span></span>
            {!demo && <span>tokens <span className="tabular text-foreground">{formatNumber(exec.input_tokens)} in / {formatNumber(exec.output_tokens)} out</span></span>}
            {!demo && <span>cost <span className="tabular text-foreground">{exec.cost_usd === null ? "not computed" : formatCost(exec.cost_usd)}</span></span>}
          </div>
          <OutputPreview exec={exec} />
        </CollapsibleContent>
      </li>
    </Collapsible>
  );
}

export function ReportCard({ task, report, isLoading }: { task: TaskDetail; report: ReportSummary | undefined; isLoading: boolean }) {
  if (!task.report_id) return null;
  return (
    <div className="space-y-3 rounded-lg border border-status-success/30 bg-status-success/5 p-4" data-testid="report-card">
      <div className="flex items-start gap-2">
        <FileText className="mt-0.5 size-4 shrink-0 text-status-success" aria-hidden />
        <div className="min-w-0 space-y-1">
          <p className="font-medium break-words">{report?.title ?? (isLoading ? "Loading report…" : "Final report")}</p>
          {report?.summary && <p className="line-clamp-4 text-sm text-muted-foreground">{report.summary}</p>}
          {report && (
            <p className="text-xs text-muted-foreground">
              {report.source_count} cited source{report.source_count === 1 ? "" : "s"}
              {report.avg_confidence !== null ? ` · avg. confidence ${formatPercent(report.avg_confidence)}` : ""}
              {report.citation_violations ? ` · ${report.citation_violations} invalid citation(s) removed` : ""}
            </p>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button asChild size="sm"><a href={urls.reportMarkdown(task.report_id)} download><Download className="size-3.5" /> Markdown</a></Button>
        <Button asChild size="sm" variant="outline"><a href={urls.reportPdf(task.report_id)} download><Download className="size-3.5" /> PDF</a></Button>
      </div>
    </div>
  );
}

export function OutputsPanel({ task }: { task: TaskDetail }) {
  const subtaskById = new Map(task.subtasks.map((s) => [s.id, s]));
  const execs = [...task.agent_executions].sort((a, b) => a.id - b.id);
  const demo = task.mode === "demo";
  if (!execs.length) {
    return <EmptyState title="No agent output yet" description="Each agent's stored output appears here when it finishes." />;
  }
  return (
    <ul className="space-y-2">
      {execs.map((e) => <ExecutionRow key={e.id} exec={e} subtask={e.subtask_id !== null ? subtaskById.get(e.subtask_id) : undefined} demo={demo} />)}
    </ul>
  );
}

export function UsagePanel({ usage, isLoading, error, onRetry, demo }: {
  usage: TaskUsage | undefined; isLoading: boolean; error: unknown; onRetry: () => void; demo: boolean;
}) {
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (isLoading || !usage) return <LoadingRows rows={3} />;
  const rows = Object.entries(usage.per_agent);
  const lim = usage.limits;
  return (
    <div className="space-y-3">
      {demo && <p className="text-xs text-status-demo">Demo mode: templates use no model tokens and incur no cost.</p>}
      {rows.length === 0 ? <EmptyState title="No usage recorded yet" /> : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                <TableHead className="text-right">LLM calls</TableHead>
                <TableHead className="text-right">Tool calls</TableHead>
                <TableHead className="text-right">Tokens in</TableHead>
                <TableHead className="text-right">Tokens out</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map(([k, u]) => (
                <TableRow key={k}>
                  <TableCell className="font-medium">{AGENT_NAME[k] ?? k}</TableCell>
                  <TableCell className="text-right tabular">{u.llm_calls}</TableCell>
                  <TableCell className="text-right tabular">{u.tool_calls}</TableCell>
                  <TableCell className="text-right tabular">{formatNumber(u.input_tokens)}</TableCell>
                  <TableCell className="text-right tabular">{formatNumber(u.output_tokens)}</TableCell>
                  <TableCell className="text-right tabular">{formatCost(u.cost_usd)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {usage.totals.cost_note && <p className="text-xs text-muted-foreground">{usage.totals.cost_note}</p>}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-3">
        {[["Token limit", lim.max_total_tokens], ["LLM call limit", lim.max_llm_calls], ["Tool call limit", lim.max_tool_calls],
          ["Time limit", lim.timeout_seconds != null ? formatDuration(lim.timeout_seconds) : null],
          ["Cost limit", lim.max_cost_usd != null ? formatCost(lim.max_cost_usd) : "none"], ["Max iterations", lim.max_iterations]]
          .map(([label, v]) => (
            <div key={String(label)} className="flex justify-between gap-2"><dt>{label}</dt><dd className="tabular text-foreground">{typeof v === "number" ? formatNumber(v) : v ?? "—"}</dd></div>
          ))}
      </dl>
    </div>
  );
}
