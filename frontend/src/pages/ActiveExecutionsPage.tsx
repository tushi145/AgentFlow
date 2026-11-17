import { Activity, Check, ExternalLink, Hand, Loader2, Search, Square, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState, ErrorState, LoadingRows, PageHeader, SectionTitle } from "@/components/common";
import { ModeBadge, StatusBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { usePendingApprovals } from "@/hooks/use-system";
import { useCancelTask, useDecideApproval, useTaskList } from "@/hooks/use-tasks";
import { durationBetween, formatCompact, formatCost, formatDuration, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import type { ApprovalRequest, TaskSummary } from "@/types/api";

const POLL_MS = 3000;

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Real progress = completed graph steps / planned steps (reported by the backend). */
export function TaskProgress({ task }: { task: TaskSummary }) {
  if (!task.progress_total) {
    return <div className="text-xs text-muted-foreground">{task.status === "queued" ? "Waiting for a worker slot" : "Planning — steps not known yet"}</div>;
  }
  const pct = Math.round((task.progress_done / task.progress_total) * 100);
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span className="capitalize">{task.current_stage ?? "—"}</span>
        <span className="tabular">{task.progress_done}/{task.progress_total} steps · {pct}%</span>
      </div>
      <Progress value={pct} aria-label={`${pct}% of planned steps complete`} className="h-1.5" />
    </div>
  );
}

function ApprovalSummary({ a }: { a: ApprovalRequest }) {
  const p = a.payload as {
    title?: string; estimated_cost_usd?: number | null;
    plan?: { subtasks?: { id: string; title: string }[] };
    queries?: Record<string, string[]>;
  };
  if (a.kind === "tools" && p.queries) {
    const all = Object.values(p.queries).flat();
    return (
      <ul className="space-y-1">
        {all.slice(0, 6).map((q) => (
          <li key={q} className="flex items-start gap-1.5 text-xs text-muted-foreground"><Search className="mt-0.5 size-3 shrink-0" /><span className="font-mono">{q}</span></li>
        ))}
        {all.length > 6 && <li className="text-xs text-muted-foreground">+{all.length - 6} more</li>}
      </ul>
    );
  }
  const subtasks = p.plan?.subtasks ?? [];
  return (
    <div className="space-y-1.5 text-xs text-muted-foreground">
      {subtasks.length > 0 && (
        <ol className="list-decimal space-y-0.5 pl-4">
          {subtasks.slice(0, 5).map((s) => <li key={s.id}>{s.title}</li>)}
          {subtasks.length > 5 && <li className="list-none">+{subtasks.length - 5} more</li>}
        </ol>
      )}
      <div>Estimated cost: <span className="text-foreground">{p.estimated_cost_usd == null ? "unknown (pricing not configured or demo)" : formatCost(p.estimated_cost_usd)}</span></div>
    </div>
  );
}

function ApprovalCard({ a }: { a: ApprovalRequest }) {
  const decide = useDecideApproval();
  const [rejecting, setRejecting] = useState(false);
  const act = (approved: boolean, comment?: string) =>
    decide.mutate({ id: a.id, approved, comment }, {
      onSuccess: () => { toast.success(approved ? "Approved — execution resumed" : "Rejected — execution stopped"); setRejecting(false); },
      onError: (e) => toast.error(errorMessage(e)),
    });
  const kindLabel = { plan: "Plan review", tools: "Search approval", cost: "Cost approval" }[a.kind];
  return (
    <Card className="gap-3 border-status-warning/30 py-4">
      <CardContent className="space-y-3 px-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 space-y-1">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="border-status-warning/40 bg-status-warning/10 text-status-warning"><Hand className="size-3" />{kindLabel}</Badge>
              <span className="text-xs text-muted-foreground">{formatRelative(a.created_at)}</span>
            </div>
            <p className="text-sm font-medium">{(a.payload as { title?: string }).title ?? kindLabel}</p>
            <Link to={`/tasks/${a.task_id}`} className="block truncate text-xs text-muted-foreground hover:text-foreground hover:underline">
              {a.task_title ?? a.task_id}
            </Link>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button size="sm" variant="outline" onClick={() => setRejecting(true)} disabled={decide.isPending}>
              <X className="size-3.5" /> Reject
            </Button>
            <Button size="sm" onClick={() => act(true)} disabled={decide.isPending}>
              {decide.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />} Approve
            </Button>
          </div>
        </div>
        <ApprovalSummary a={a} />
      </CardContent>
      <ConfirmDialog open={rejecting} onOpenChange={setRejecting} title="Reject and stop this execution?"
                     description="The task ends with status “rejected”. Work done so far stays in its history."
                     confirmLabel="Reject" destructive withComment pending={decide.isPending}
                     onConfirm={(c) => act(false, c)} />
    </Card>
  );
}

function ActiveTaskRow({ task, highlighted, now }: { task: TaskSummary; highlighted: boolean; now: number }) {
  const cancel = useCancelTask();
  const [confirm, setConfirm] = useState(false);
  return (
    <Card className={cn("gap-0 py-4 transition-shadow", highlighted && "ring-2 ring-primary/50")}>
      <CardContent className="grid gap-3 px-4 md:grid-cols-[minmax(0,1fr)_260px_auto] md:items-center">
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={task.status} />
            <ModeBadge mode={task.mode} />
            <span className="text-xs text-muted-foreground">{task.execution_mode} · approval: {task.approval_mode}</span>
          </div>
          <Link to={`/tasks/${task.id}`} className="block truncate font-medium hover:underline">{task.title}</Link>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Elapsed <span className="tabular text-foreground">{formatDuration(durationBetween(task.started_at ?? task.created_at, null, now))}</span></span>
            <span>LLM calls <span className="tabular text-foreground">{task.llm_calls}</span></span>
            <span>Tokens <span className="tabular text-foreground">{formatCompact(task.input_tokens + task.output_tokens)}</span></span>
            {task.mode === "live" && <span>Cost <span className="tabular text-foreground">{formatCost(task.cost_usd)}</span></span>}
          </div>
        </div>
        <TaskProgress task={task} />
        <div className="flex gap-2 md:justify-end">
          <Button asChild size="sm" variant="outline"><Link to={`/tasks/${task.id}`}><ExternalLink className="size-3.5" /> Open</Link></Button>
          <Button size="sm" variant="ghost" className="text-status-danger hover:text-status-danger" onClick={() => setConfirm(true)}>
            <Square className="size-3.5" /> Cancel
          </Button>
        </div>
      </CardContent>
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Cancel this execution?"
                     description="Running agents are stopped and the task is marked cancelled. This cannot be undone."
                     confirmLabel="Cancel execution" destructive pending={cancel.isPending}
                     onConfirm={() => cancel.mutate(task.id, {
                       onSuccess: () => { toast.success("Execution cancelled"); setConfirm(false); },
                       onError: (e) => { toast.error(errorMessage(e)); setConfirm(false); },
                     })} />
    </Card>
  );
}

export default function ActiveExecutionsPage() {
  const [params] = useSearchParams();
  const highlight = params.get("highlight");
  const now = useNow();
  const tasks = useTaskList({ status: "active", page_size: 100 }, { poll: POLL_MS });
  // Same cadence as the task list, so a task that just paused shows its approval card promptly.
  const approvals = usePendingApprovals(POLL_MS);
  const items = tasks.data?.items ?? [];

  return (
    <>
      <PageHeader
        title="Active executions"
        description="Tasks that are queued, planning, running or waiting for you. Updates every 3 seconds."
        badge={tasks.isFetching && !tasks.isLoading ? <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Refreshing" /> : undefined}
      />
      <div className="space-y-8">
        {highlight && tasks.data && !items.some((t) => t.id === highlight) && (
          <div role="status" className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/40 px-4 py-3 text-sm">
            The execution you started has already finished.
            <Link to={`/tasks/${highlight}`} className="font-medium underline-offset-4 hover:underline">Open it</Link>
            <span className="text-muted-foreground">or see</span>
            <Link to="/history" className="font-medium underline-offset-4 hover:underline">Execution history</Link>.
          </div>
        )}
        {(approvals.data?.length ?? 0) > 0 && (
          <section aria-labelledby="approvals-title">
            <SectionTitle><span id="approvals-title">Pending approvals ({approvals.data!.length})</span></SectionTitle>
            <div className="grid gap-3 xl:grid-cols-2">
              {approvals.data!.map((a) => <ApprovalCard key={a.id} a={a} />)}
            </div>
          </section>
        )}
        <section aria-labelledby="active-title">
          <SectionTitle><span id="active-title">Running and queued ({tasks.data?.total ?? 0})</span></SectionTitle>
          {tasks.error && <ErrorState error={tasks.error} onRetry={() => tasks.refetch()} />}
          {tasks.isLoading ? <LoadingRows rows={3} /> : items.length === 0 ? (
            <EmptyState icon={Activity} title="Nothing is running"
                        description="Executions appear here while they run. Finished ones move to Execution History."
                        action={<Button asChild variant="outline"><Link to="/tasks/new">Start a task</Link></Button>} />
          ) : (
            <div className="space-y-3">
              {items.map((t) => <ActiveTaskRow key={t.id} task={t} highlighted={t.id === highlight} now={now} />)}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
