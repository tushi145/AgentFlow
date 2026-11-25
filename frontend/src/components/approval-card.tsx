/* Human-approval request card (shared by Active Executions and the Live Execution page). */

import { Check, GitBranch, Hand, Loader2, Search, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useDecideApproval } from "@/hooks/use-tasks";
import { APPROVAL_KIND_LABEL } from "@/lib/execution-meta";
import { formatCost, formatRelative } from "@/lib/format";
import { errorMessage } from "@/services/api";
import type { ApprovalRequest } from "@/types/api";

interface PlanSubtask { id: string; title: string; description?: string; queries?: string[]; depends_on?: string[] }
interface ApprovalPayload {
  title?: string;
  estimated_cost_usd?: number | null;
  plan?: { objective_summary?: string; subtasks?: PlanSubtask[] };
  queries?: Record<string, string[]>;
}

function CostLine({ p, detailed }: { p: ApprovalPayload; detailed?: boolean }) {
  return (
    <div>
      Estimated cost:{" "}
      <span className="text-foreground">
        {p.estimated_cost_usd == null ? "unknown (pricing not configured or demo)"
          : detailed ? `${formatCost(p.estimated_cost_usd)} (upper-bound estimate)` : formatCost(p.estimated_cost_usd)}
      </span>
    </div>
  );
}

export function ApprovalSummary({ a, detailed = false }: { a: ApprovalRequest; detailed?: boolean }) {
  const p = a.payload as ApprovalPayload;
  if (a.kind === "tools" && p.queries) {
    const entries = Object.entries(p.queries);
    const all = entries.flatMap(([, q]) => q);
    if (detailed) {
      return (
        <div className="space-y-2 text-xs">
          {entries.map(([sub, qs]) => (
            <div key={sub}>
              <div className="font-medium text-muted-foreground">Subtask {sub}</div>
              <ul className="mt-1 space-y-1">
                {qs.map((q) => (
                  <li key={q} className="flex items-start gap-1.5"><Search className="mt-0.5 size-3 shrink-0 text-muted-foreground" /><span className="font-mono break-all">{q}</span></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      );
    }
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
  if (detailed) {
    return (
      <div className="space-y-3 text-xs">
        {p.plan?.objective_summary && <p className="text-sm">{p.plan.objective_summary}</p>}
        {subtasks.length > 0 && (
          <ol className="space-y-2">
            {subtasks.map((s) => (
              <li key={s.id} className="rounded-md border bg-muted/30 p-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-muted-foreground">{s.id}</span>
                  <span className="font-medium">{s.title}</span>
                  {(s.depends_on?.length ?? 0) > 0 && (
                    <span className="inline-flex items-center gap-1 text-muted-foreground"><GitBranch className="size-3" />after {s.depends_on!.join(", ")}</span>
                  )}
                </div>
                {s.description && <p className="mt-1 text-muted-foreground">{s.description}</p>}
                {(s.queries?.length ?? 0) > 0 && (
                  <ul className="mt-1.5 space-y-0.5">
                    {s.queries!.map((q) => <li key={q} className="flex items-start gap-1.5"><Search className="mt-0.5 size-3 shrink-0 text-muted-foreground" /><span className="font-mono break-all">{q}</span></li>)}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        )}
        <div className="text-muted-foreground"><CostLine p={p} detailed /></div>
      </div>
    );
  }
  return (
    <div className="space-y-1.5 text-xs text-muted-foreground">
      {subtasks.length > 0 && (
        <ol className="list-decimal space-y-0.5 pl-4">
          {subtasks.slice(0, 5).map((s) => <li key={s.id}>{s.title}</li>)}
          {subtasks.length > 5 && <li className="list-none">+{subtasks.length - 5} more</li>}
        </ol>
      )}
      <CostLine p={p} />
    </div>
  );
}

/** `detailed` shows the full plan / query list and an optional comment for approving too. */
export function ApprovalCard({ a, detailed = false }: { a: ApprovalRequest; detailed?: boolean }) {
  const decide = useDecideApproval();
  const [rejecting, setRejecting] = useState(false);
  const [comment, setComment] = useState("");
  const act = (approved: boolean, c?: string) =>
    decide.mutate({ id: a.id, approved, comment: c }, {
      onSuccess: () => { toast.success(approved ? "Approved — execution resumed" : "Rejected — execution stopped"); setRejecting(false); setComment(""); },
      onError: (e) => toast.error(errorMessage(e)),
    });
  const kindLabel = APPROVAL_KIND_LABEL[a.kind] ?? a.kind;
  return (
    <Card className="min-w-0 gap-3 border-status-warning/30 py-4" data-approval-id={a.id}>
      <CardContent className="space-y-3 px-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 space-y-1">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="border-status-warning/40 bg-status-warning/10 text-status-warning"><Hand className="size-3" />{kindLabel}</Badge>
              <span className="text-xs text-muted-foreground">{formatRelative(a.created_at)}</span>
            </div>
            <p className="text-sm font-medium">{(a.payload as ApprovalPayload).title ?? kindLabel}</p>
            {!detailed && (
              <Link to={`/tasks/${a.task_id}`} className="block truncate text-xs text-muted-foreground hover:text-foreground hover:underline">
                {a.task_title ?? a.task_id}
              </Link>
            )}
          </div>
          {!detailed && (
            <div className="flex shrink-0 gap-2">
              <Button size="sm" variant="outline" onClick={() => setRejecting(true)} disabled={decide.isPending}>
                <X className="size-3.5" /> Reject
              </Button>
              <Button size="sm" onClick={() => act(true)} disabled={decide.isPending}>
                {decide.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />} Approve
              </Button>
            </div>
          )}
        </div>
        <ApprovalSummary a={a} detailed={detailed} />
        {detailed && (
          <div className="space-y-2 border-t pt-3">
            <Label htmlFor={`approval-comment-${a.id}`}>Comment (optional)</Label>
            <Textarea id={`approval-comment-${a.id}`} rows={2} maxLength={1000} value={comment}
                      placeholder="Recorded with your decision" onChange={(e) => setComment(e.target.value)} />
            <div className="flex flex-wrap justify-end gap-2">
              <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => setRejecting(true)}>
                <X className="size-3.5" /> Reject and stop
              </Button>
              <Button size="sm" disabled={decide.isPending} onClick={() => act(true, comment.trim() || undefined)}>
                {decide.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />} Approve and continue
              </Button>
            </div>
          </div>
        )}
      </CardContent>
      <ConfirmDialog open={rejecting} onOpenChange={setRejecting} title="Reject and stop this execution?"
                     description="The task ends with status “rejected”. Work done so far stays in its history."
                     confirmLabel="Reject" destructive withComment={!detailed} pending={decide.isPending}
                     onConfirm={(c) => act(false, detailed ? comment.trim() || undefined : c)} />
    </Card>
  );
}
