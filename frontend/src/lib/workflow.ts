/* Derives the agent workflow view from REAL execution data only:
   - TaskDetail.agent_executions (one DB row per agent invocation, with status),
   - TaskDetail.subtasks (research subtasks, their status and iteration),
   - persisted execution events (dispatch batches, skipped agents, approval gates, iterations).
   Nothing is inferred from elapsed time; a node is "running" only if the backend says so. */

import type { AgentExecution, ExecutionEvent, Subtask, TaskDetail, TaskStatus } from "@/types/api";

export type NodeState = "pending" | "running" | "completed" | "failed" | "skipped" | "stopped" | "not_run";

export const STAGES = [
  { key: "planner", label: "Planner" },
  { key: "research", label: "Research" },
  { key: "analysis", label: "Analysis" },
  { key: "verification", label: "Verification" },
  { key: "report", label: "Report Generation" },
] as const;
export type StageKey = (typeof STAGES)[number]["key"];

export interface StageNode {
  key: StageKey;
  label: string;
  state: NodeState;
  runs: number;              // number of agent invocations (research: one per subtask)
  durationMs: number | null; // summed over finished runs
  tokens: number;
  model: string | null;
  error: string | null;
  note: string | null;       // e.g. "1 of 3 subtasks failed", "disabled for this run"
}

export interface ResearchLane {
  key: string;
  title: string;
  state: NodeState;
  queries: string[];
  dependsOn: string[];
  error: string | null;
}

export interface ResearchBatch {
  iteration: number;
  /** Order in which the backend dispatched it (from `dispatch` events). */
  index: number;
  parallel: boolean;
  lanes: ResearchLane[];
}

export interface ApprovalGate {
  approvalId: number;
  kind: string;
  title: string;
  state: "pending" | "approved" | "rejected" | "expired";
  comment: string | null;
}

export interface WorkflowModel {
  stages: StageNode[];
  batches: ResearchBatch[];
  /** Subtasks planned but not dispatched yet (e.g. waiting on dependencies). */
  waiting: ResearchLane[];
  gates: ApprovalGate[];
  /** Research iterations started by the Verification Agent (0 = none). */
  extraIterations: number;
}

const TERMINAL: TaskStatus[] = ["completed", "failed", "cancelled", "rejected", "interrupted"];
export const isTerminal = (s: TaskStatus) => TERMINAL.includes(s);

function execState(e: AgentExecution): NodeState {
  switch (e.status) {
    case "running": return "running";
    case "completed": return "completed";
    case "failed": return "failed";
    case "cancelled":
    case "aborted": return "stopped";
    default: return "pending";
  }
}

function subtaskState(s: Subtask, terminal: boolean): NodeState {
  if (s.status === "done") return "completed";
  if (s.status === "failed") return "failed";
  if (s.status === "running") return terminal ? "stopped" : "running";
  return terminal ? "not_run" : "pending";
}

function lane(s: Subtask, terminal: boolean): ResearchLane {
  return { key: s.plan_key, title: s.title, state: subtaskState(s, terminal), queries: s.queries,
           dependsOn: s.depends_on, error: s.error };
}

function sumDuration(execs: AgentExecution[]): number | null {
  const done = execs.filter((e) => e.duration_ms !== null);
  return done.length ? done.reduce((a, e) => a + (e.duration_ms ?? 0), 0) : null;
}

export function deriveWorkflow(task: TaskDetail, events: ExecutionEvent[]): WorkflowModel {
  const terminal = isTerminal(task.status);
  const skipped = new Set(events.filter((e) => e.type === "agent_skipped").map((e) => String(e.payload.agent)));
  const byAgent = new Map<string, AgentExecution[]>();
  for (const e of [...task.agent_executions].sort((a, b) => a.id - b.id)) {
    byAgent.set(e.agent_key, [...(byAgent.get(e.agent_key) ?? []), e]);
  }

  const stages: StageNode[] = STAGES.map(({ key, label }) => {
    const execs = byAgent.get(key) ?? [];
    const latest = execs.at(-1);
    let state: NodeState;
    let note: string | null = null;
    if (key === "research") {
      const subs = task.subtasks;
      const states = subs.map((s) => subtaskState(s, terminal));
      const failed = states.filter((s) => s === "failed").length;
      if (execs.some((e) => e.status === "running") || states.includes("running")) state = "running";
      else if (!subs.length) state = terminal ? "not_run" : "pending";
      else if (states.includes("stopped")) state = "stopped";
      else if (states.every((s) => s === "failed")) state = "failed";
      else if (states.every((s) => s === "completed" || s === "failed")) state = "completed";
      // Partly done but nothing running right now (e.g. between dependency batches): waiting.
      else if (states.some((s) => s === "completed" || s === "failed")) state = terminal ? "stopped" : "pending";
      else state = terminal ? "not_run" : "pending";
      const done = states.filter((s) => s === "completed").length;
      if (subs.length) note = `${done}/${subs.length} subtasks done${failed ? ` · ${failed} failed` : ""}`;
    } else if (execs.some((e) => e.status === "running")) {
      state = "running";
    } else if (latest) {
      state = execState(latest);
    } else if (skipped.has(key)) {
      state = "skipped";
      note = "Disabled for this run";
    } else {
      state = terminal ? "not_run" : "pending";
    }
    const errored = [...execs].reverse().find((e) => e.error_message);
    return {
      key, label, state, note,
      runs: execs.length,
      durationMs: sumDuration(execs),
      tokens: execs.reduce((a, e) => a + e.input_tokens + e.output_tokens, 0),
      model: latest?.model ?? null,
      error: state === "failed" || state === "stopped" ? errored?.error_message ?? null : null,
    };
  });

  // Research batches exactly as the backend dispatched them.
  const subsByKey = new Map(task.subtasks.map((s) => [s.plan_key, s]));
  const dispatched = new Set<string>();
  const batches: ResearchBatch[] = [];
  for (const ev of events) {
    if (ev.type !== "dispatch") continue;
    const keys = Array.isArray(ev.payload.subtasks) ? (ev.payload.subtasks as string[]) : [];
    const lanes = keys.map((k) => subsByKey.get(k)).filter((s): s is Subtask => !!s);
    if (!lanes.length) continue;
    lanes.forEach((s) => dispatched.add(s.plan_key));
    batches.push({ iteration: lanes[0].iteration, index: batches.length, parallel: lanes.length > 1,
                   lanes: lanes.map((s) => lane(s, terminal)) });
  }
  const waiting = task.subtasks.filter((s) => !dispatched.has(s.plan_key)).map((s) => lane(s, terminal));

  // Approval gates (requested → decided) from the event log.
  const gates = new Map<number, ApprovalGate>();
  for (const ev of events) {
    const id = Number(ev.payload.approval_id);
    if (!Number.isFinite(id)) continue;
    if (ev.type === "approval_requested") {
      gates.set(id, { approvalId: id, kind: String(ev.payload.kind ?? "plan"), title: ev.message,
                      state: "pending", comment: null });
    } else if (ev.type === "approval_decided" && gates.has(id)) {
      const g = gates.get(id)!;
      gates.set(id, { ...g, state: ev.payload.approved ? "approved" : "rejected",
                      comment: (ev.payload.comment as string | null) ?? null });
    }
  }
  // A pending gate on a task that ended without a decision (cancelled / timed out) expired.
  for (const [id, g] of gates) if (g.state === "pending" && terminal) gates.set(id, { ...g, state: "expired" });

  return {
    stages, batches, waiting, gates: [...gates.values()],
    extraIterations: events.filter((e) => e.type === "iteration_started").length,
  };
}
