/* Agent workflow: Planner → Research (parallel lanes) → Analysis → Verification → Report.
   Every state comes from deriveWorkflow() — i.e. DB rows and persisted events. No timers. */

import {
  ArrowDown, ArrowRight, BrainCircuit, FileText, GitBranch, Hand, ListChecks, RotateCcw, Search, ShieldCheck,
  type LucideIcon,
} from "lucide-react";

import { TONE_CLASSES } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { APPROVAL_KIND_LABEL, NODE_STATE } from "@/lib/execution-meta";
import { formatCompact, formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { NodeState, ResearchLane, StageKey, StageNode, WorkflowModel } from "@/lib/workflow";

const STAGE_ICON: Record<StageKey, LucideIcon> = {
  planner: ListChecks, research: Search, analysis: BrainCircuit, verification: ShieldCheck, report: FileText,
};

const BORDER: Record<NodeState, string> = {
  pending: "border-dashed",
  running: "border-status-running/60 pulse-ring",
  completed: "border-status-success/40",
  failed: "border-status-danger/50",
  skipped: "border-dashed opacity-70",
  stopped: "border-status-warning/50",
  not_run: "border-dashed opacity-60",
};

export function NodeStatePill({ state, className }: { state: NodeState; className?: string }) {
  const m = NODE_STATE[state];
  const Icon = m.icon;
  return (
    <Badge variant="outline" className={cn("gap-1 font-medium", TONE_CLASSES[m.tone], className)}>
      <Icon className={cn("size-3", m.spin && "animate-spin")} aria-hidden />{m.label}
    </Badge>
  );
}

function LaneRow({ lane }: { lane: ResearchLane }) {
  const m = NODE_STATE[lane.state];
  const Icon = m.icon;
  return (
    <li className="flex min-w-0 items-start gap-1.5 text-xs" data-lane={lane.key} data-state={lane.state} title={lane.error ?? lane.queries.join("\n")}>
      <Icon className={cn("mt-0.5 size-3 shrink-0", `text-status-${m.tone}`, m.spin && "animate-spin")} aria-label={m.label} />
      <span className="shrink-0 font-mono text-muted-foreground">{lane.key}</span>
      <span className="min-w-0 truncate">{lane.title}</span>
    </li>
  );
}

function StageCard({ node, demo, children, className }: {
  node: StageNode; demo: boolean; children?: React.ReactNode; className?: string;
}) {
  const Icon = STAGE_ICON[node.key];
  return (
    <li className={cn("flex min-w-0 flex-col gap-2 rounded-lg border bg-card p-3", BORDER[node.state], className)}
        data-stage={node.key} data-state={node.state} aria-label={`${node.label}: ${NODE_STATE[node.state].label}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate text-sm font-medium">{node.label}</span>
        </div>
        <NodeStatePill state={node.state} />
      </div>
      <dl className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
        {node.runs > 0 && <div><dt className="sr-only">Runs</dt><dd>{node.runs} run{node.runs === 1 ? "" : "s"}</dd></div>}
        {node.durationMs !== null && <div><dt className="sr-only">Duration</dt><dd className="tabular">{formatDuration(node.durationMs / 1000)}</dd></div>}
        {!demo && node.runs > 0 && <div><dt className="sr-only">Tokens</dt><dd className="tabular">{formatCompact(node.tokens)} tok</dd></div>}
        {node.model && <div className="min-w-0"><dt className="sr-only">Model</dt><dd className="truncate font-mono">{node.model}</dd></div>}
      </dl>
      {node.note && <p className="text-xs text-muted-foreground">{node.note}</p>}
      {node.error && <p className="line-clamp-3 text-xs text-status-danger">{node.error}</p>}
      {children}
    </li>
  );
}

/** Arrow between stages; solid once the next stage has actually been reached. */
function Connector({ reached }: { reached: boolean }) {
  return (
    <li aria-hidden className={cn("flex items-center justify-center self-center", reached ? "text-foreground/70" : "text-muted-foreground/40")}>
      <ArrowDown className="size-4 xl:hidden" />
      <ArrowRight className="hidden size-4 xl:block" />
    </li>
  );
}

const reached = (s: NodeState) => s !== "pending" && s !== "not_run";

export function WorkflowGraph({ model, demo }: { model: WorkflowModel; demo: boolean }) {
  const [planner, research, analysis, verification, report] = model.stages;
  const iterations = [...new Set(model.batches.map((b) => b.iteration))];
  return (
    <div className="space-y-3">
      <ol className="flex flex-col gap-2 xl:flex-row xl:items-stretch" aria-label="Agent workflow">
        <StageCard node={planner} demo={demo} className="xl:w-0 xl:flex-1" />
        <Connector reached={reached(research.state)} />
        <StageCard node={research} demo={demo} className="xl:w-0 xl:flex-[1.8]">
          {model.batches.length > 0 && (
            <div className="space-y-2 border-t pt-2">
              {iterations.map((it) => (
                <div key={it} className="space-y-1.5">
                  {iterations.length > 1 && (
                    <div className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                      {it === 0 ? "Initial research" : `Iteration ${it + 1} · gap research`}
                    </div>
                  )}
                  {model.batches.filter((b) => b.iteration === it).map((b) => (
                    <div key={b.index} className="rounded-md bg-muted/40 p-2" data-batch={b.index}>
                      <div className="mb-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                        <GitBranch className="size-3" aria-hidden />
                        Batch {b.index + 1} · {b.parallel ? `${b.lanes.length} in parallel` : "sequential"}
                      </div>
                      <ul className="space-y-1">{b.lanes.map((l) => <LaneRow key={l.key} lane={l} />)}</ul>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
          {model.waiting.length > 0 && (
            <div className="space-y-1 border-t pt-2">
              <div className="text-[11px] text-muted-foreground">Not dispatched yet</div>
              <ul className="space-y-1">{model.waiting.map((l) => <LaneRow key={l.key} lane={l} />)}</ul>
            </div>
          )}
        </StageCard>
        <Connector reached={reached(analysis.state)} />
        <StageCard node={analysis} demo={demo} className="xl:w-0 xl:flex-1" />
        <Connector reached={reached(verification.state)} />
        <StageCard node={verification} demo={demo} className="xl:w-0 xl:flex-1" />
        <Connector reached={reached(report.state)} />
        <StageCard node={report} demo={demo} className="xl:w-0 xl:flex-1" />
      </ol>
      {(model.gates.length > 0 || model.extraIterations > 0) && (
        <ul className="flex flex-wrap gap-2 text-xs">
          {model.gates.map((g) => (
            <li key={g.approvalId} data-gate={g.approvalId} data-state={g.state}
                className={cn("inline-flex items-center gap-1.5 rounded-md border px-2 py-1",
                  g.state === "pending" ? TONE_CLASSES.warning : g.state === "approved" ? TONE_CLASSES.success
                    : g.state === "rejected" ? TONE_CLASSES.danger : TONE_CLASSES.neutral)}>
              <Hand className="size-3" aria-hidden />
              {APPROVAL_KIND_LABEL[g.kind] ?? g.kind}: {g.state}
              {g.comment && <span className="max-w-64 truncate text-muted-foreground">— “{g.comment}”</span>}
            </li>
          ))}
          {model.extraIterations > 0 && (
            <li className={cn("inline-flex items-center gap-1.5 rounded-md border px-2 py-1", TONE_CLASSES.running)}>
              <RotateCcw className="size-3" aria-hidden />
              Verification → Research: {model.extraIterations} extra iteration{model.extraIterations === 1 ? "" : "s"}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
