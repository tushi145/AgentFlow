/* Live Execution: one task's real-time workflow, events, research, verification, approvals and outputs.
   Data sources: GET /tasks/{id} (+ /sources, /verifications, /usage, /approvals, /reports/{id}) and the
   SSE stream /tasks/{id}/events. Queries are refreshed when relevant events arrive, not on a timer;
   polling is only a fallback while the stream is down. */

import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Ban, CheckCircle2, Clock, Coins, Cpu, Hash, Loader2, RotateCcw, Square, XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";

import { ApprovalCard } from "@/components/approval-card";
import { EmptyState, ErrorState, LoadingRows, PageHeader, SectionTitle, StatCard } from "@/components/common";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ConnectionIndicator, EventTimeline } from "@/components/execution/event-timeline";
import { OutputsPanel, ReportCard, UsagePanel } from "@/components/execution/outputs-panel";
import { SourcesPanel, VerificationPanel } from "@/components/execution/research-panels";
import { WorkflowGraph } from "@/components/execution/workflow-graph";
import { ModeBadge, StatusBadge, TONE_CLASSES } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useNow } from "@/hooks/use-now";
import { useTaskEvents } from "@/hooks/use-task-events";
import {
  taskKeys, useCancelTask, useReport, useRetryTask, useTaskApprovals, useTaskDetail, useTaskSources, useTaskUsage,
  useTaskVerifications,
} from "@/hooks/use-tasks";
import { durationBetween, formatCompact, formatCost, formatDuration, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import { deriveWorkflow, isTerminal } from "@/lib/workflow";
import { ApiError, errorMessage } from "@/services/api";
import type { ExecutionEvent, TaskDetail } from "@/types/api";

const FALLBACK_POLL_MS = 5000;
const INVALIDATE_THROTTLE_MS = 300;

/** Which cached queries each event type makes stale. */
function staleKeys(id: string, type: string): readonly (readonly unknown[])[] {
  const keys: (readonly unknown[])[] = [taskKeys.detail(id)];
  if (type === "source_added") keys.push(taskKeys.sources(id));
  if (type === "verification_completed" || type === "agent_skipped") keys.push(taskKeys.verifications(id));
  if (type === "agent_completed" || type === "agent_failed" || type === "task_finished") keys.push(taskKeys.usage(id));
  if (["approval_requested", "approval_decided", "paused", "task_finished"].includes(type)) keys.push(taskKeys.approvals(id));
  return keys;
}

/** Coalesces invalidations so a burst of events causes one refetch per query, not dozens. */
function useThrottledInvalidate() {
  const qc = useQueryClient();
  const pending = useRef(new Map<string, readonly unknown[]>());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return useCallback((keys: readonly (readonly unknown[])[]) => {
    for (const k of keys) pending.current.set(JSON.stringify(k), k);
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      const batch = [...pending.current.values()];
      pending.current.clear();
      for (const queryKey of batch) void qc.invalidateQueries({ queryKey, exact: true });
    }, INVALIDATE_THROTTLE_MS);
  }, [qc]);
}

const OUTCOME: Partial<Record<TaskDetail["status"], { icon: typeof CheckCircle2; tone: keyof typeof TONE_CLASSES; title: string }>> = {
  completed: { icon: CheckCircle2, tone: "success", title: "Execution completed" },
  failed: { icon: XCircle, tone: "danger", title: "Execution failed" },
  cancelled: { icon: Ban, tone: "neutral", title: "Execution cancelled" },
  rejected: { icon: XCircle, tone: "danger", title: "Rejected at an approval checkpoint" },
  interrupted: { icon: Ban, tone: "warning", title: "Execution interrupted" },
};

function OutcomeBanner({ task }: { task: TaskDetail }) {
  const o = OUTCOME[task.status];
  if (!o) return null;
  const Icon = o.icon;
  return (
    <div role="status" className={cn("flex items-start gap-3 rounded-lg border p-4", TONE_CLASSES[o.tone])} data-outcome={task.status}>
      <Icon className="mt-0.5 size-5 shrink-0" aria-hidden />
      <div className="min-w-0 space-y-0.5 text-sm">
        <p className="font-medium text-foreground">{o.title}</p>
        {task.error_message && <p className="text-muted-foreground break-words">{task.error_message}</p>}
        {task.error_code && task.status !== "completed" && <p className="font-mono text-xs text-muted-foreground">{task.error_code}</p>}
        {task.status === "completed" && task.budget_exhausted && (
          <p className="text-muted-foreground">A budget limit was reached; the report discloses what could not be covered.</p>
        )}
        {task.status !== "completed" && !task.report_id && <p className="text-muted-foreground">No report was produced. Work done so far remains below.</p>}
      </div>
    </div>
  );
}

/** Latest human-readable activity line, straight from the event stream. */
function currentActivity(events: ExecutionEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (["stage_changed", "agent_progress", "agent_started", "tool_call", "dispatch", "paused", "approval_requested"].includes(e.type)) return e.message;
  }
  return null;
}

export default function TaskExecutionPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const invalidate = useThrottledInvalidate();

  const onEvents = useCallback((batch: ExecutionEvent[]) => {
    invalidate(batch.flatMap((e) => staleKeys(taskId, e.type)));
  }, [invalidate, taskId]);
  const onEnd = useCallback(() => {
    // Final state: refresh everything this page and the lists show.
    void qc.invalidateQueries({ queryKey: taskKeys.detail(taskId) });
    void qc.invalidateQueries({ queryKey: taskKeys.approvals(taskId) });
    void qc.invalidateQueries({ queryKey: ["tasks", "list"] });
    void qc.invalidateQueries({ queryKey: ["analytics"] });
  }, [qc, taskId]);

  const stream = useTaskEvents(taskId, { onEvents, onEnd });
  const streamDown = stream.state === "reconnecting" || stream.state === "failed";

  const detail = useTaskDetail(taskId, streamDown ? FALLBACK_POLL_MS : false);
  const task = detail.data;
  const active = !!task && !isTerminal(task.status);
  const approvals = useTaskApprovals(taskId, streamDown && active ? FALLBACK_POLL_MS : false);
  const sources = useTaskSources(taskId);
  const verifications = useTaskVerifications(taskId);
  const usage = useTaskUsage(taskId);
  const report = useReport(task?.report_id);
  const now = useNow(1000, active);

  const cancel = useCancelTask();
  const retry = useRetryTask();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [tab, setTab] = useState("sources");

  const workflow = useMemo(() => (task ? deriveWorkflow(task, stream.events) : null), [task, stream.events]);
  const activity = useMemo(() => currentActivity(stream.events), [stream.events]);

  if (detail.error) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="space-y-4">
        <BackLink />
        {notFound ? (
          <EmptyState title="Execution not found" description="It may have been deleted, or the link is wrong."
                      action={<Button asChild variant="outline"><Link to="/history">Execution history</Link></Button>} />
        ) : <ErrorState error={detail.error} onRetry={() => detail.refetch()} />}
      </div>
    );
  }
  if (!task || !workflow) {
    return (
      <div className="space-y-4" aria-busy="true">
        <BackLink />
        <LoadingRows rows={2} />
        <LoadingRows rows={4} />
      </div>
    );
  }

  const demo = task.mode === "demo";
  const pct = task.progress_total ? Math.round((task.progress_done / task.progress_total) * 100) : null;
  const pendingApprovals = approvals.data ?? [];

  return (
    <>
      <BackLink />
      <PageHeader
        title={task.title}
        badge={<><StatusBadge status={task.status} /><ModeBadge mode={task.mode} /></>}
        description={<span className="text-xs">{task.execution_mode} · approval: {task.approval_mode} · <span className="font-mono">{task.id}</span></span>}
        actions={active ? (
          <Button variant="outline" size="sm" className="text-status-danger hover:text-status-danger" onClick={() => setConfirmCancel(true)}>
            <Square className="size-3.5" /> Cancel execution
          </Button>
        ) : (
          <Button variant="outline" size="sm" disabled={retry.isPending} onClick={() => retry.mutate(task.id, {
            onSuccess: (t) => { toast.success("Re-run started as a new task"); navigate(`/tasks/${t.id}`); },
            onError: (e) => toast.error(errorMessage(e)),
          })}>
            {retry.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCcw className="size-3.5" />} Re-run
          </Button>
        )}
      />

      <div className="space-y-6">
        <Card className="gap-0 py-4">
          <CardContent className="space-y-3 px-4">
            <div>
              <div className="text-xs font-medium text-muted-foreground">Objective</div>
              <p className="mt-0.5 text-sm whitespace-pre-wrap break-words">{task.prompt}</p>
            </div>
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="text-muted-foreground">
                  Stage <span className="font-medium text-foreground capitalize" data-testid="current-stage">{task.current_stage ?? "queued"}</span>
                  {active && activity && <span className="ml-2 break-words">— {activity}</span>}
                </span>
                <span className="tabular text-muted-foreground">
                  {pct === null ? "Steps not planned yet" : `${task.progress_done}/${task.progress_total} steps · ${pct}%`}
                </span>
              </div>
              {pct !== null && <Progress value={pct} className="h-1.5" aria-label={`${pct}% of planned steps complete`} />}
            </div>
          </CardContent>
        </Card>

        <OutcomeBanner task={task} />

        {pendingApprovals.length > 0 && (
          <section aria-labelledby="approval-title" className="space-y-3">
            <SectionTitle><span id="approval-title">Waiting for your decision</span></SectionTitle>
            {pendingApprovals.map((a) => <ApprovalCard key={a.id} a={a} detailed />)}
          </section>
        )}

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatCard label="Elapsed" icon={Clock}
                    value={task.started_at ? formatDuration(durationBetween(task.started_at, task.finished_at, now)) : null}
                    hint={task.started_at ? undefined : "Not started yet"} />
          <StatCard label="Tokens" icon={Hash} value={formatCompact(task.input_tokens + task.output_tokens)}
                    hint={demo ? "Demo templates use no tokens" : `${formatNumber(task.input_tokens)} in · ${formatNumber(task.output_tokens)} out`} />
          <StatCard label="Calls" icon={Cpu} value={`${task.llm_calls} / ${task.tool_calls}`} hint="LLM / tool calls" />
          <StatCard label="Cost" icon={Coins}
                    value={demo ? "n/a" : task.cost_usd === null ? "Not computed" : formatCost(task.cost_usd)}
                    hint={demo ? "Demo mode — nothing billed" : task.estimated_cost_usd !== null ? `Estimate ≤ ${formatCost(task.estimated_cost_usd)}` : usage.data?.totals.cost_note ?? undefined} />
        </div>

        <section aria-labelledby="workflow-title">
          <SectionTitle><span id="workflow-title">Agent workflow</span></SectionTitle>
          <WorkflowGraph model={workflow} demo={demo} />
        </section>

        <ReportCard task={task} report={report.data} isLoading={report.isLoading} />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <section aria-labelledby="timeline-title" className="min-w-0">
            <SectionTitle actions={
              <ConnectionIndicator state={stream.state} attempt={stream.attempt} nextRetryMs={stream.nextRetryMs}
                                   lastEventId={stream.lastEventId} onRetry={stream.retry} taskActive={active} />
            }><span id="timeline-title">Event timeline</span></SectionTitle>
            <EventTimeline events={stream.events} streamState={stream.state} origin={stream.events[0]?.created_at ?? task.started_at} />
          </section>

          <section aria-label="Execution details" className="min-w-0">
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList className="w-full overflow-x-auto">
                <TabsTrigger value="sources">Sources{sources.data ? ` (${sources.data.length})` : ""}</TabsTrigger>
                <TabsTrigger value="verification">Verification{verifications.data?.length ? ` (${verifications.data.length})` : ""}</TabsTrigger>
                <TabsTrigger value="outputs">Outputs</TabsTrigger>
                <TabsTrigger value="usage">Usage</TabsTrigger>
              </TabsList>
              <TabsContent value="sources" className="pt-2">
                <SourcesPanel sources={sources.data} verifications={verifications.data} isLoading={sources.isLoading}
                              error={sources.error} onRetry={() => sources.refetch()} />
              </TabsContent>
              <TabsContent value="verification" className="pt-2">
                <VerificationPanel verifications={verifications.data} sources={sources.data} isLoading={verifications.isLoading}
                                   error={verifications.error} onRetry={() => verifications.refetch()} demo={demo} />
              </TabsContent>
              <TabsContent value="outputs" className="pt-2">
                <OutputsPanel task={task} />
              </TabsContent>
              <TabsContent value="usage" className="pt-2">
                <UsagePanel usage={usage.data} isLoading={usage.isLoading} error={usage.error} onRetry={() => usage.refetch()} demo={demo} />
              </TabsContent>
            </Tabs>
          </section>
        </div>
      </div>

      <ConfirmDialog open={confirmCancel} onOpenChange={setConfirmCancel} title="Cancel this execution?"
                     description="Running agents are stopped and the task is marked cancelled. This cannot be undone."
                     confirmLabel="Cancel execution" destructive pending={cancel.isPending}
                     onConfirm={() => cancel.mutate(task.id, {
                       onSuccess: () => { toast.success("Execution cancelled"); setConfirmCancel(false); },
                       onError: (e) => { toast.error(errorMessage(e)); setConfirmCancel(false); },
                     })} />
    </>
  );
}

function BackLink() {
  return (
    <Link to="/executions" className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-3" /> Active executions
    </Link>
  );
}
