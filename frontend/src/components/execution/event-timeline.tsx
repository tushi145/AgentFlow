/* Live event timeline: every persisted execution event, streamed over SSE. */

import { ArrowDownToLine, Loader2, RefreshCw, Wifi, WifiOff } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { EmptyState } from "@/components/common";
import { StatusDot } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AGENT_LABEL, type EventCategory as Category, eventMeta, type Tone } from "@/lib/execution-meta";
import { formatClock, formatOffset } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { StreamState } from "@/services/event-stream";
import type { ExecutionEvent } from "@/types/api";

const FILTERS: { key: "all" | Category; label: string }[] = [
  { key: "all", label: "All" }, { key: "agents", label: "Agents" }, { key: "tools", label: "Tools" },
  { key: "sources", label: "Sources" }, { key: "issues", label: "Issues" }, { key: "approvals", label: "Approvals" },
];

function toneFor(ev: ExecutionEvent): Tone {
  if (ev.type === "approval_decided") return ev.payload.approved ? "success" : "danger";
  if (ev.type === "task_finished") {
    const s = ev.payload.status;
    return s === "completed" ? "success" : s === "cancelled" ? "neutral" : "danger";
  }
  return eventMeta(ev.type).tone;
}

/** Payload fields not already in the message; shown on demand, never invented. */
function details(ev: ExecutionEvent): unknown {
  const { agent: _a, ...rest } = ev.payload;
  return Object.keys(rest).length ? rest : null;
}

export function ConnectionIndicator({ state, attempt, nextRetryMs, lastEventId, onRetry, taskActive }: {
  state: StreamState; attempt: number; nextRetryMs: number | null; lastEventId: number; onRetry: () => void; taskActive: boolean;
}) {
  if (state === "open") {
    return (
      <span role="status" className="inline-flex items-center gap-1.5 text-xs text-status-success" data-stream="open">
        <StatusDot tone="success" pulse={taskActive} /> {taskActive ? "Live" : "Connected"}
      </span>
    );
  }
  if (state === "connecting") {
    return <span role="status" className="inline-flex items-center gap-1.5 text-xs text-muted-foreground" data-stream="connecting"><Loader2 className="size-3 animate-spin" /> Connecting…</span>;
  }
  if (state === "reconnecting") {
    return (
      <span role="status" className="inline-flex items-center gap-1.5 text-xs text-status-warning" data-stream="reconnecting">
        <Wifi className="size-3" /> Reconnecting{attempt > 0 ? ` (attempt ${attempt}${nextRetryMs ? `, in ${Math.round(nextRetryMs / 1000)}s` : ""})` : ""}
        {lastEventId > 0 && <span className="text-muted-foreground">· resumes after #{lastEventId}</span>}
      </span>
    );
  }
  if (state === "failed") {
    return (
      <span role="alert" className="inline-flex items-center gap-1.5 text-xs text-status-danger" data-stream="failed">
        <WifiOff className="size-3" /> Disconnected
        <Button size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={onRetry}><RefreshCw className="size-3" /> Reconnect</Button>
      </span>
    );
  }
  return <span role="status" className="inline-flex items-center gap-1.5 text-xs text-muted-foreground" data-stream="ended"><StatusDot tone="neutral" /> Stream closed · execution finished</span>;
}

export function EventTimeline({ events, streamState, origin }: {
  events: ExecutionEvent[]; streamState: StreamState; origin: string | null;
}) {
  const [filter, setFilter] = useState<"all" | Category>("all");
  const [showProgress, setShowProgress] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);

  const visible = useMemo(() => events.filter((e) => {
    const cat = eventMeta(e.type).cat;
    if (cat === "progress") return showProgress && (filter === "all" || filter === "agents");
    return filter === "all" || cat === filter;
  }), [events, filter, showProgress]);

  // Follow the tail while the user is at the bottom; stop following when they scroll up.
  useEffect(() => {
    const el = scroller.current;
    if (el && follow) el.scrollTop = el.scrollHeight;
  }, [visible.length, follow]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
  };

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-1.5" role="toolbar" aria-label="Filter events">
        {FILTERS.map((f) => (
          <Button key={f.key} size="sm" variant={filter === f.key ? "secondary" : "ghost"} className="h-7 px-2.5 text-xs"
                  aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>{f.label}</Button>
        ))}
        <label className="ml-auto inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" className="accent-primary" checked={showProgress} onChange={(e) => setShowProgress(e.target.checked)} />
          Step counters
        </label>
      </div>
      <div className="relative">
        <div ref={scroller} onScroll={onScroll} className="max-h-[560px] min-h-48 overflow-y-auto rounded-lg border bg-muted/20"
             aria-live="polite" aria-relevant="additions" aria-label="Execution events" data-testid="event-list">
          {visible.length === 0 ? (
            <EmptyState className="m-3 border-0 py-10" title={streamState === "connecting" ? "Connecting to the event stream…" : events.length ? "No events match this filter" : "No events yet"}
                        description={events.length ? undefined : "Events appear here as agents work."} />
          ) : (
            <ol className="divide-y">
              {visible.map((ev) => {
                const m = eventMeta(ev.type);
                const tone = toneFor(ev);
                const Icon = m.icon;
                const extra = details(ev);
                const agent = typeof ev.payload.agent === "string" ? ev.payload.agent : null;
                return (
                  <li key={ev.id} className="flex gap-3 px-3 py-2 text-sm" data-event-id={ev.id} data-event-type={ev.type}>
                    <div className="w-[68px] shrink-0 pt-0.5 text-right font-mono text-[11px] leading-tight text-muted-foreground">
                      <time dateTime={ev.created_at}>{formatClock(ev.created_at)}</time>
                      <div className="opacity-70">{formatOffset(ev.created_at, origin)}</div>
                    </div>
                    <Icon className={cn("mt-0.5 size-4 shrink-0", `text-status-${tone}`)} aria-hidden />
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        {agent && <Badge variant="outline" className="h-5 px-1.5 text-[11px] font-normal">{AGENT_LABEL[agent] ?? agent}</Badge>}
                        <span className="break-words">{ev.message || ev.type}</span>
                      </div>
                      {extra !== null && (
                        <details className="group text-xs text-muted-foreground">
                          <summary className="cursor-pointer select-none font-mono text-[11px] hover:text-foreground">{ev.type} · details</summary>
                          <pre className="mt-1 max-h-60 overflow-auto rounded bg-muted p-2 font-mono text-[11px] whitespace-pre-wrap break-all">
                            {JSON.stringify(extra, null, 2).slice(0, 8000)}
                          </pre>
                        </details>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
        {!follow && visible.length > 0 && (
          <Button size="sm" variant="secondary" className="absolute right-3 bottom-3 h-7 shadow" onClick={() => setFollow(true)}>
            <ArrowDownToLine className="size-3.5" /> Latest
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {events.length} event{events.length === 1 ? "" : "s"} received{visible.length !== events.length ? ` · ${visible.length} shown` : ""}
      </p>
    </div>
  );
}
