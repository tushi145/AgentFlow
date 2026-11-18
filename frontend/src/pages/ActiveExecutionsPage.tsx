import { Activity, ExternalLink, Loader2, Square } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";

import { ApprovalCard } from "@/components/approval-card";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState, ErrorState, LoadingRows, PageHeader, SectionTitle } from "@/components/common";
import { ModeBadge, StatusBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useNow } from "@/hooks/use-now";
import { usePendingApprovals } from "@/hooks/use-system";
import { useCancelTask, useTaskList } from "@/hooks/use-tasks";
import { durationBetween, formatCompact, formatCost, formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import type { TaskSummary } from "@/types/api";

const POLL_MS = 3000;

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
