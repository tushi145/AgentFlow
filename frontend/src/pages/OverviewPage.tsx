import {
  Activity, ArrowRight, Bot, CheckCircle2, Clock, Coins, Hand, Hash, ListChecks, PlusCircle, XCircle,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { ExecutionsChart } from "@/components/charts/ExecutionsChart";
import { EmptyState, ErrorState, PageHeader, SectionTitle, StatCard } from "@/components/common";
import { ModeBadge, StatusBadge, TASK_STATUS } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useOverview } from "@/hooks/use-tasks";
import {
  formatCompact, formatCost, formatDuration, formatNumber, formatPercent, formatRelative,
} from "@/lib/format";
import type { OverviewAnalytics, TaskStatus } from "@/types/api";

const AGENT_LABELS: Record<string, string> = {
  planner: "Planner", research: "Research", analysis: "Analysis", verification: "Verification", report: "Report",
};
const AGENT_ORDER = ["planner", "research", "analysis", "verification", "report"];

function count(b: OverviewAnalytics["status_breakdown"], ...keys: TaskStatus[]) {
  return keys.reduce((n, k) => n + (b[k] ?? 0), 0);
}

function WelcomeState() {
  return (
    <EmptyState
      icon={Bot}
      title="No executions yet"
      description="Start by describing a research objective. The Planner breaks it into subtasks, Research agents gather sources, and every claim is verified before the report is written."
      action={
        <Button asChild>
          <Link to="/tasks/new"><PlusCircle className="size-4" /> Create your first task</Link>
        </Button>
      }
    />
  );
}

function StatusBreakdown({ data }: { data: OverviewAnalytics }) {
  const entries = (Object.entries(data.status_breakdown) as [TaskStatus, number][])
    .filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const total = data.totals.tasks || 1;
  return (
    <ul className="space-y-2.5">
      {entries.map(([status, n]) => (
        <li key={status} className="space-y-1">
          <div className="flex items-center justify-between text-sm">
            <StatusBadge status={status} />
            <span className="tabular text-muted-foreground">{n} · {formatPercent(n / total)}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-foreground/30" style={{ width: `${(n / total) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function AgentTable({ data }: { data: OverviewAnalytics }) {
  const rows = AGENT_ORDER.filter((k) => data.per_agent[k]).map((k) => ({ key: k, ...data.per_agent[k] }));
  if (!rows.length) return <p className="py-6 text-center text-sm text-muted-foreground">No agent runs recorded yet.</p>;
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Agent</TableHead>
            <TableHead className="text-right">Runs</TableHead>
            <TableHead className="text-right">Failure rate</TableHead>
            <TableHead className="text-right">Avg duration</TableHead>
            <TableHead className="text-right">Tokens</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.key}>
              <TableCell className="font-medium">{AGENT_LABELS[r.key] ?? r.key}</TableCell>
              <TableCell className="tabular text-right">{formatNumber(r.runs)}</TableCell>
              <TableCell className="tabular text-right">{formatPercent(r.runs ? r.failed / r.runs : null, 1)}</TableCell>
              <TableCell className="tabular text-right">{formatDuration(r.avg_ms === null ? null : r.avg_ms / 1000)}</TableCell>
              <TableCell className="tabular text-right">{formatCompact(r.tokens)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function RecentActivity({ data }: { data: OverviewAnalytics }) {
  return (
    <ul className="divide-y">
      {data.recent.map((t) => {
        const Icon = TASK_STATUS[t.status]?.icon ?? Clock;
        return (
          <li key={t.id}>
            <Link to={`/tasks/${t.id}`} className="flex items-center gap-3 rounded-md px-1 py-2.5 hover:bg-accent/50">
              <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1 truncate text-sm">{t.title}</span>
              <ModeBadge mode={t.mode} className="hidden sm:inline-flex" />
              <span className="w-20 shrink-0 text-right text-xs text-muted-foreground">{formatRelative(t.created_at)}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export default function OverviewPage() {
  const [mode, setMode] = useState<"all" | "live" | "demo">("all");
  const q = useOverview(mode === "all" ? undefined : mode);
  const d = q.data;
  const b = d?.status_breakdown ?? {};
  const running = d ? count(b, "running", "planning") : null;
  const pending = d ? count(b, "queued", "awaiting_approval") : null;
  const failed = d ? count(b, "failed", "interrupted") : null;
  const isEmpty = d && d.totals.tasks === 0;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Execution analytics recorded by the backend. Refreshes every 15 seconds."
        actions={
          <>
            <Tabs value={mode} onValueChange={(v) => setMode(v as typeof mode)}>
              <TabsList>
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="live">Live</TabsTrigger>
                <TabsTrigger value="demo">Demo</TabsTrigger>
              </TabsList>
            </Tabs>
            <Button asChild><Link to="/tasks/new"><PlusCircle className="size-4" /> New task</Link></Button>
          </>
        }
      />

      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} className="mb-6" />}

      {isEmpty && mode === "all" ? (
        <WelcomeState />
      ) : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8 [&>*]:min-w-0">
            <StatCard label="Total executions" icon={Hash} loading={q.isLoading} value={d && formatNumber(d.totals.tasks)}
                      hint={d && mode === "all" && d.totals.demo_tasks ? `${d.totals.demo_tasks} in demo mode` : undefined} />
            <StatCard label="Completed" icon={CheckCircle2} loading={q.isLoading} value={d && formatNumber(d.totals.completed)}
                      hint={d && `${formatPercent(d.totals.success_rate)} success rate`} />
            <StatCard label="Running" icon={Activity} loading={q.isLoading} value={running !== null ? formatNumber(running) : null} />
            <StatCard label="Pending" icon={Hand} loading={q.isLoading} value={pending !== null ? formatNumber(pending) : null}
                      hint={d && `${d.totals.awaiting_approval} awaiting approval`} />
            <StatCard label="Failed" icon={XCircle} loading={q.isLoading} value={failed !== null ? formatNumber(failed) : null}
                      hint={d && (b.interrupted ? `${b.interrupted} interrupted` : undefined)} />
            <StatCard label="Avg duration" icon={Clock} loading={q.isLoading} value={d && formatDuration(d.totals.avg_duration_s)}
                      hint="completed runs" />
            <StatCard label="Tokens used" icon={ListChecks} loading={q.isLoading} value={d && formatCompact(d.totals.total_tokens)}
                      hint={d && `${d.totals.agents_enabled}/${d.totals.agents} agents enabled`} />
            <StatCard label="API cost (live)" icon={Coins} loading={q.isLoading} value={d && formatCost(d.totals.total_cost_usd)}
                      hint={d && d.totals.total_cost_usd === null ? "no priced live runs yet" : "priced models only"} />
          </div>

          {isEmpty ? (
            <EmptyState title={`No ${mode} executions`} description="Switch the filter or start a new task." />
          ) : (
            <div className="grid gap-6 lg:grid-cols-3 [&>*]:min-w-0">
              <Card className="lg:col-span-2">
                <CardHeader><CardTitle className="text-sm">Executions · last 14 days</CardTitle></CardHeader>
                <CardContent>{d ? <ExecutionsChart series={d.series} /> : <Skeleton className="h-56 w-full" />}</CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle className="text-sm">Status breakdown</CardTitle></CardHeader>
                <CardContent>{d ? <StatusBreakdown data={d} /> : <Skeleton className="h-40 w-full" />}</CardContent>
              </Card>
              <Card className="lg:col-span-2">
                <CardHeader><CardTitle className="text-sm">Agent performance</CardTitle></CardHeader>
                <CardContent>{d ? <AgentTable data={d} /> : <Skeleton className="h-40 w-full" />}</CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <SectionTitle actions={<Button asChild variant="ghost" size="sm" className="h-7 text-xs"><Link to="/history">All <ArrowRight className="size-3" /></Link></Button>}>
                    Recent activity
                  </SectionTitle>
                </CardHeader>
                <CardContent>{d ? <RecentActivity data={d} /> : <Skeleton className="h-40 w-full" />}</CardContent>
              </Card>
            </div>
          )}
        </div>
      )}
    </>
  );
}
