import {
  ChevronLeft, ChevronRight, ExternalLink, FileText, History, Loader2, MoreHorizontal, RotateCcw, Search, Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { EmptyState, ErrorState, LoadingRows, PageHeader } from "@/components/common";
import { ModeBadge, StatusBadge, TASK_STATUS } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useDebounced } from "@/hooks/use-debounced";
import { useDeleteTask, useRetryTask, useTaskList } from "@/hooks/use-tasks";
import {
  durationBetween, formatCompact, formatCost, formatDateTime, formatDuration,
} from "@/lib/format";
import { errorMessage } from "@/services/api";
import { ACTIVE_STATUSES, type TaskStatus, type TaskSummary } from "@/types/api";

const PAGE_SIZE = 20;
const ALL = "all";

function RowActions({ task }: { task: TaskSummary }) {
  const navigate = useNavigate();
  const retry = useRetryTask();
  const del = useDeleteTask();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const active = ACTIVE_STATUSES.includes(task.status);
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="size-8" aria-label={`Actions for ${task.title}`}>
            {retry.isPending || del.isPending ? <Loader2 className="size-4 animate-spin" /> : <MoreHorizontal className="size-4" />}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          <DropdownMenuItem asChild><Link to={`/tasks/${task.id}`}><ExternalLink className="size-3.5" /> Open execution</Link></DropdownMenuItem>
          {task.report_id && (
            <DropdownMenuItem asChild><Link to={`/reports/${task.report_id}`}><FileText className="size-3.5" /> Open report</Link></DropdownMenuItem>
          )}
          <DropdownMenuItem disabled={active} onSelect={() => retry.mutate(task.id, {
            onSuccess: (t) => { toast.success("Re-run started as a new task"); navigate(`/executions?highlight=${t.id}`); },
            onError: (e) => toast.error(errorMessage(e)),
          })}>
            <RotateCcw className="size-3.5" /> Re-run as new task
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={active} variant="destructive" onSelect={() => setConfirmDelete(true)}>
            <Trash2 className="size-3.5" /> Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ConfirmDialog open={confirmDelete} onOpenChange={setConfirmDelete} title="Delete this execution?"
                     description="Removes the task, its sources, verification results, report and files. This cannot be undone."
                     confirmLabel="Delete" destructive pending={del.isPending}
                     onConfirm={() => del.mutate(task.id, {
                       onSuccess: () => { toast.success("Execution deleted"); setConfirmDelete(false); },
                       onError: (e) => { toast.error(errorMessage(e)); setConfirmDelete(false); },
                     })} />
    </>
  );
}

export default function HistoryPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? ALL;
  const mode = params.get("mode") ?? ALL;
  const [search, setSearch] = useState(params.get("q") ?? "");
  const q = useDebounced(search.trim());
  // While a new search term is being synced to the URL, query page 1 (not the stale page).
  const page = (params.get("q") ?? "") !== q ? 1 : Math.max(1, Number(params.get("page") ?? 1) || 1);

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "" || v === ALL) next.delete(k); else next.set(k, v);
    }
    if (!("page" in patch)) next.delete("page");
    setParams(next, { replace: true });
  };
  useEffect(() => {
    if ((params.get("q") ?? "") !== q) update({ q });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const list = useTaskList({
    q: q || undefined, status: status === ALL ? undefined : status, mode: mode === ALL ? undefined : mode,
    page, page_size: PAGE_SIZE,
  }, { poll: 10_000 });
  const total = list.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = !!(q || status !== ALL || mode !== ALL);

  return (
    <>
      <PageHeader title="Execution history" description="Every execution recorded by the backend, newest first." />
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search title or objective…"
                 className="pl-8" aria-label="Search executions" />
        </div>
        <div className="flex gap-2">
          <Select value={status} onValueChange={(v) => update({ status: v })}>
            <SelectTrigger className="w-full sm:w-44" aria-label="Filter by status"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All statuses</SelectItem>
              <SelectItem value="active">Active (any)</SelectItem>
              {(Object.keys(TASK_STATUS) as TaskStatus[]).map((s) => (
                <SelectItem key={s} value={s}>{TASK_STATUS[s].label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={mode} onValueChange={(v) => update({ mode: v })}>
            <SelectTrigger className="w-full sm:w-32" aria-label="Filter by mode"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All modes</SelectItem>
              <SelectItem value="live">Live</SelectItem>
              <SelectItem value="demo">Demo</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {list.error && <ErrorState error={list.error} onRetry={() => list.refetch()} className="mb-4" />}
      {list.isLoading ? <LoadingRows rows={6} /> : total === 0 ? (
        filtered ? (
          <EmptyState icon={Search} title="No matching executions" description="Try a different search or clear the filters."
                      action={<Button variant="outline" onClick={() => { setSearch(""); setParams({}, { replace: true }); }}>Clear filters</Button>} />
        ) : (
          <EmptyState icon={History} title="No executions yet" description="Completed and failed runs are kept here."
                      action={<Button asChild><Link to="/tasks/new">Start a task</Link></Button>} />
        )
      ) : (
        <Card className="gap-0 overflow-hidden py-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="min-w-64">Execution</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden md:table-cell">Created</TableHead>
                  <TableHead className="hidden text-right md:table-cell">Duration</TableHead>
                  <TableHead className="hidden text-right lg:table-cell">Tokens</TableHead>
                  <TableHead className="hidden text-right lg:table-cell">Cost</TableHead>
                  <TableHead className="w-10"><span className="sr-only">Actions</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data!.items.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="max-w-0">
                      <div className="flex items-center gap-2">
                        <ModeBadge mode={t.mode} />
                        <Link to={`/tasks/${t.id}`} className="truncate font-medium hover:underline">{t.title}</Link>
                        {t.report_id && (
                          <Link to={`/reports/${t.report_id}`} aria-label="Open report" className="shrink-0 text-muted-foreground hover:text-foreground">
                            <FileText className="size-3.5" />
                          </Link>
                        )}
                      </div>
                      {t.error_message && (
                        <p className={`mt-0.5 truncate text-xs ${["failed", "interrupted"].includes(t.status) ? "text-status-danger" : "text-muted-foreground"}`}>
                          {t.error_message}
                        </p>
                      )}
                    </TableCell>
                    <TableCell><StatusBadge status={t.status} /></TableCell>
                    <TableCell className="hidden whitespace-nowrap text-muted-foreground md:table-cell">{formatDateTime(t.created_at)}</TableCell>
                    <TableCell className="tabular hidden text-right md:table-cell">
                      {formatDuration(t.started_at ? durationBetween(t.started_at, t.finished_at) : null)}
                    </TableCell>
                    <TableCell className="tabular hidden text-right lg:table-cell">{formatCompact(t.input_tokens + t.output_tokens)}</TableCell>
                    <TableCell className="tabular hidden text-right lg:table-cell">{t.mode === "demo" ? "—" : formatCost(t.cost_usd)}</TableCell>
                    <TableCell><RowActions task={t} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <div className="flex items-center justify-between border-t px-4 py-2.5 text-xs text-muted-foreground">
            <span className="tabular">
              {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total}
            </span>
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="icon" className="size-7" disabled={page <= 1}
                      onClick={() => update({ page: String(page - 1) })} aria-label="Previous page">
                <ChevronLeft className="size-4" />
              </Button>
              <span className="tabular px-1">Page {page} / {pages}</span>
              <Button variant="ghost" size="icon" className="size-7" disabled={page >= pages}
                      onClick={() => update({ page: String(page + 1) })} aria-label="Next page">
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </Card>
      )}
    </>
  );
}
