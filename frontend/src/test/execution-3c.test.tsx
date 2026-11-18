import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "@/App";
import { TaskEventStream, type StreamSnapshot } from "@/services/event-stream";
import { deriveWorkflow } from "@/lib/workflow";
import type {
  AgentExecution, ApprovalRequest, ExecutionEvent, ResearchSource, Subtask, TaskDetail, TaskUsage, VerificationResult,
} from "@/types/api";

import { ev, FakeEventSource, resetEventIds } from "./fake-event-source";
import { renderWithProviders } from "./render";
import { server, task } from "./server";

/* ------------------------------------------------------------------ fixtures */

const sub = (plan_key: string, over: Partial<Subtask> = {}): Subtask => ({
  id: Number(plan_key.replace(/\D/g, "")) || 1, plan_key, title: `Subtask ${plan_key}`, description: "", agent_key: "research",
  depends_on: [], queries: [`query ${plan_key}`], status: "pending", iteration: 0, error: null, ...over,
});

let execId = 1;
const exec = (agent_key: string, status: string, over: Partial<AgentExecution> = {}): AgentExecution => ({
  id: execId++, agent_key, subtask_id: null, iteration: 0, status, mode: "live", provider: "anthropic", model: "claude-opus-5-5",
  input_tokens: 1000, output_tokens: 200, llm_calls: 1, tool_calls: 0, cost_usd: 0.01, duration_ms: status === "running" ? null : 1500,
  error_code: null, error_message: null, started_at: new Date().toISOString(), finished_at: null, output: null, ...over,
});

function detail(over: Partial<TaskDetail> = {}): TaskDetail {
  return { ...task({ mode: "live", status: "running", current_stage: "research", progress_done: 2, progress_total: 7,
                     report_id: null, finished_at: null, cost_usd: 0.05, estimated_cost_usd: 0.4 }),
           limits: { max_total_tokens: 400000, max_llm_calls: 40, max_tool_calls: 60, timeout_seconds: 900, max_cost_usd: null, max_iterations: 2 },
           subtasks: [], agent_executions: [], pending_approval_id: null, ...over };
}

const usage: TaskUsage = {
  per_agent: { planner: { input_tokens: 1000, output_tokens: 200, cache_read_tokens: 0, llm_calls: 1, tool_calls: 0, cost_usd: 0.01 } },
  totals: { input_tokens: 1000, output_tokens: 200, llm_calls: 1, tool_calls: 0, cost_usd: 0.01, estimated_cost_usd: 0.4, cost_note: null },
  limits: { max_total_tokens: 400000, max_llm_calls: 40, max_tool_calls: 60, timeout_seconds: 900, max_cost_usd: null, max_iterations: 2 },
};

const source = (ref: string, over: Partial<ResearchSource> = {}): ResearchSource => ({
  id: Number(ref.slice(1)), ref, url: `https://example.org/${ref}`, title: `Source ${ref}`, domain: "example.org", snippet: "snippet",
  search_query: "vector db", provider: "tavily", relevance_score: 0.8, injection_suspected: false, injection_rules: [], mode: "live",
  retrieved_at: new Date().toISOString(), ...over,
});

function useTaskHandlers(t: TaskDetail, extra: { approvals?: ApprovalRequest[]; sources?: ResearchSource[]; verifications?: VerificationResult[] } = {}) {
  server.use(
    http.get("/api/v1/tasks/:id", () => HttpResponse.json(t)),
    http.get("/api/v1/tasks/:id/sources", () => HttpResponse.json(extra.sources ?? [])),
    http.get("/api/v1/tasks/:id/verifications", () => HttpResponse.json(extra.verifications ?? [])),
    http.get("/api/v1/tasks/:id/usage", () => HttpResponse.json(usage)),
    http.get("/api/v1/approvals", ({ request }) =>
      HttpResponse.json(new URL(request.url).searchParams.get("task_id") ? extra.approvals ?? [] : [])),
    http.get("/api/v1/reports/:id", ({ params }) => HttpResponse.json({
      id: Number(params.id), task_id: t.id, title: "Vector DB report", summary: "Summary text", source_count: 2,
      avg_confidence: 0.82, citation_violations: 0, mode: t.mode, created_at: new Date().toISOString(), has_pdf: true })),
  );
}

beforeEach(() => {
  FakeEventSource.reset();
  resetEventIds();
  execId = 1;
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/* ------------------------------------------------------------------ SSE client */

describe("TaskEventStream", () => {
  function setup(opts: { maxAttempts?: number } = {}) {
    const received: ExecutionEvent[] = [];
    const snaps: StreamSnapshot[] = [];
    const onEnd = vi.fn();
    const s = new TaskEventStream("/api/v1/tasks/t-1/events", {
      onEvents: (b) => received.push(...b), onSnapshot: (x) => snaps.push(x), onEnd,
      factory: (u) => new FakeEventSource(u), flushMs: 0, baseDelayMs: 1000, ...opts,
    });
    s.start();
    return { s, received, snaps, onEnd };
  }

  it("delivers replayed and live events once each, in order", () => {
    vi.useFakeTimers();
    const { s, received } = setup();
    FakeEventSource.last.open();
    FakeEventSource.last.emit(ev("a", {}, "a", 1), ev("b", {}, "b", 2), ev("b", {}, "b", 2), ev("a", {}, "a", 1), ev("c", {}, "c", 3));
    vi.advanceTimersByTime(1);
    expect(received.map((e) => e.id)).toEqual([1, 2, 3]);
    expect(s.snapshot).toMatchObject({ state: "open", lastEventId: 3 });
    s.close();
  });

  it("closes on task_finished and never reconnects afterwards", () => {
    vi.useFakeTimers();
    const { s, onEnd } = setup();
    const es = FakeEventSource.last;
    es.open();
    es.emit(ev("agent_started", {}, "x", 1), ev("task_finished", { status: "completed" }, "done", 2));
    expect(s.snapshot.state).toBe("ended");
    expect(es.closed).toBe(true);
    expect(onEnd).toHaveBeenCalledOnce();
    es.fail();
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("treats `end` (task already terminal, nothing to replay) as the end of the stream", () => {
    const { s, onEnd } = setup();
    FakeEventSource.last.open();
    FakeEventSource.last.send("end", JSON.stringify({ status: "completed" }));
    expect(s.snapshot.state).toBe("ended");
    expect(onEnd).toHaveBeenCalledOnce();
  });

  it("lets the browser retry transient drops natively (Last-Event-ID) without opening a new stream", () => {
    vi.useFakeTimers();
    const { s } = setup();
    FakeEventSource.last.open();
    FakeEventSource.last.emit(ev("a", {}, "a", 4));
    FakeEventSource.last.fail(false);
    expect(s.snapshot).toMatchObject({ state: "reconnecting", attempt: 1, lastEventId: 4 });
    vi.advanceTimersByTime(30_000);
    expect(FakeEventSource.instances).toHaveLength(1);
    FakeEventSource.last.open(); // browser reconnected
    expect(s.snapshot).toMatchObject({ state: "open", attempt: 0 });
    s.close();
  });

  it("reopens with ?after=<last id> after a hard failure, with backoff, and skips replayed duplicates", () => {
    vi.useFakeTimers();
    const { s, received } = setup();
    FakeEventSource.last.open();
    FakeEventSource.last.emit(ev("a", {}, "a", 1), ev("b", {}, "b", 5));
    vi.advanceTimersByTime(1);
    FakeEventSource.last.fail(true);
    expect(s.snapshot).toMatchObject({ state: "reconnecting", attempt: 1, nextRetryMs: 1000 });
    vi.advanceTimersByTime(999);
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.last.url).toBe("/api/v1/tasks/t-1/events?after=5");
    // second failure doubles the delay
    FakeEventSource.last.fail(true);
    expect(s.snapshot.nextRetryMs).toBe(2000);
    vi.advanceTimersByTime(2000);
    const es = FakeEventSource.last;
    es.open();
    es.emit(ev("b", {}, "b", 5), ev("c", {}, "c", 6));
    vi.advanceTimersByTime(1);
    expect(received.map((e) => e.id)).toEqual([1, 5, 6]);
    expect(s.snapshot).toMatchObject({ state: "open", attempt: 0, lastEventId: 6 });
    s.close();
  });

  it("detects a silent stall (no frames, no error — e.g. backend died behind a proxy) and reopens with ?after", () => {
    vi.useFakeTimers();
    const { s } = setup();
    const es = FakeEventSource.last;
    es.open();
    es.emit(ev("a", {}, "a", 7));
    vi.advanceTimersByTime(20_000);
    es.send("ping"); // keep-alives keep a healthy stream alive
    vi.advanceTimersByTime(30_000);
    expect(s.snapshot.state).toBe("open");
    vi.advanceTimersByTime(5_001); // 35 s since the last frame
    expect(es.closed).toBe(true);
    expect(s.snapshot).toMatchObject({ state: "reconnecting", attempt: 1 });
    vi.advanceTimersByTime(1000);
    expect(FakeEventSource.last.url).toBe("/api/v1/tasks/t-1/events?after=7");
    s.close();
  });

  it("reports `failed` after repeated failures and recovers on manual retry", () => {
    vi.useFakeTimers();
    const { s } = setup({ maxAttempts: 3 });
    FakeEventSource.last.fail(true);
    vi.advanceTimersByTime(1000);
    FakeEventSource.last.fail(true);
    vi.advanceTimersByTime(2000);
    FakeEventSource.last.fail(true);
    expect(s.snapshot.state).toBe("failed");
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.instances).toHaveLength(3);
    s.retry();
    expect(FakeEventSource.instances).toHaveLength(4);
    FakeEventSource.last.open();
    expect(s.snapshot.state).toBe("open");
    s.close();
  });
});

/* ------------------------------------------------------------------ workflow model */

describe("deriveWorkflow", () => {
  it("builds parallel research batches and agent states from DB rows and events", () => {
    const t = detail({
      subtasks: [sub("t1", { status: "done" }), sub("t2", { status: "running" }), sub("t3", { status: "pending", depends_on: ["t2"] })],
      agent_executions: [exec("planner", "completed"), exec("research", "completed", { subtask_id: 1 }), exec("research", "running", { subtask_id: 2 })],
    });
    const m = deriveWorkflow(t, [ev("dispatch", { subtasks: ["t1", "t2"] })]);
    const state = Object.fromEntries(m.stages.map((s) => [s.key, s.state]));
    expect(state).toEqual({ planner: "completed", research: "running", analysis: "pending", verification: "pending", report: "pending" });
    expect(m.batches).toHaveLength(1);
    expect(m.batches[0]).toMatchObject({ parallel: true });
    expect(m.batches[0].lanes.map((l) => [l.key, l.state])).toEqual([["t1", "completed"], ["t2", "running"]]);
    expect(m.waiting.map((l) => l.key)).toEqual(["t3"]);
    expect(m.stages[1].note).toBe("1/3 subtasks done");
  });

  it("marks interrupted work as stopped and never-reached stages as not run when the task ends", () => {
    const t = detail({
      status: "cancelled",
      subtasks: [sub("t1", { status: "running" })],
      agent_executions: [exec("planner", "completed"), exec("research", "cancelled", { subtask_id: 1, error_message: "Task was cancelled." })],
    });
    const m = deriveWorkflow(t, [ev("dispatch", { subtasks: ["t1"] }), ev("approval_requested", { approval_id: 9, kind: "tools" }, "Approve 2 searches")]);
    expect(m.stages.map((s) => s.state)).toEqual(["completed", "stopped", "not_run", "not_run", "not_run"]);
    expect(m.batches[0].parallel).toBe(false);
    expect(m.gates).toEqual([{ approvalId: 9, kind: "tools", title: "Approve 2 searches", state: "expired", comment: null }]);
  });

  it("shows failed and skipped agents, decided gates and extra iterations", () => {
    const t = detail({
      status: "failed",
      subtasks: [sub("t1", { status: "done" }), sub("g1-1", { id: 5, status: "done", iteration: 1 })],
      agent_executions: [exec("planner", "completed"), exec("analysis", "completed"), exec("report", "failed", { error_message: "Model overloaded" })],
    });
    const m = deriveWorkflow(t, [
      ev("approval_requested", { approval_id: 1, kind: "plan" }, "Review the execution plan"),
      ev("approval_decided", { approval_id: 1, approved: true, comment: "go" }),
      ev("dispatch", { subtasks: ["t1"] }), ev("agent_skipped", { agent: "verification" }),
      ev("iteration_started", { iteration: 1 }), ev("dispatch", { subtasks: ["g1-1"] }),
    ]);
    const byKey = Object.fromEntries(m.stages.map((s) => [s.key, s]));
    expect(byKey.verification.state).toBe("skipped");
    expect(byKey.report).toMatchObject({ state: "failed", error: "Model overloaded" });
    expect(m.gates[0]).toMatchObject({ state: "approved", comment: "go" });
    expect(m.extraIterations).toBe(1);
    expect(m.batches.map((b) => b.iteration)).toEqual([0, 1]);
  });
});

/* ------------------------------------------------------------------ page */

describe("Live Execution page", () => {
  const sseOpen = async () => {
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    act(() => FakeEventSource.last.open());
  };

  it("shows the live workflow, timeline and connection state from real events", async () => {
    useTaskHandlers(detail({
      subtasks: [sub("t1", { status: "running" }), sub("t2", { status: "running" })],
      agent_executions: [exec("planner", "completed"), exec("research", "running", { subtask_id: 1 }), exec("research", "running", { subtask_id: 2 })],
    }));
    renderWithProviders(<AppRoutes />, { route: "/tasks/t-1" });
    expect(await screen.findByRole("heading", { name: "Compare AI hiring" })).toBeInTheDocument();
    await sseOpen();
    expect(FakeEventSource.last.url).toBe("/api/v1/tasks/t-1/events");
    act(() => FakeEventSource.last.emit(
      ev("stage_changed", { stage: "research" }, "Plan accepted automatically."),
      ev("dispatch", { subtasks: ["t1", "t2"] }, "Dispatching 2 research subtask(s) in parallel."),
      ev("tool_call", { agent: "research", tool: "web_search", query: "q" }, "Searching: vector db pricing"),
    ));
    expect(await screen.findByText("Searching: vector db pricing")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector("[data-stage=research]")).toHaveAttribute("data-state", "running"));
    expect(document.querySelector("[data-stage=planner]")).toHaveAttribute("data-state", "completed");
    expect(document.querySelector("[data-stage=report]")).toHaveAttribute("data-state", "pending");
    expect(await screen.findByText("Batch 1 · 2 in parallel")).toBeInTheDocument();
    // Activity line comes from the latest event, not a timer
    expect(screen.getByText(/— Searching: vector db pricing/)).toBeInTheDocument();
    // Usage and cost as reported
    expect(screen.getByText("$0.05")).toBeInTheDocument();
    expect(screen.getByText("Estimate ≤ $0.40")).toBeInTheDocument();

    // Hard disconnect → reconnecting indicator, then resume with ?after=<last id>
    act(() => FakeEventSource.last.fail(true));
    expect(await screen.findByText(/Reconnecting/)).toBeInTheDocument();
    expect(screen.getByText(/resumes after #3/)).toBeInTheDocument();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2), { timeout: 3000 });
    expect(FakeEventSource.last.url).toBe("/api/v1/tasks/t-1/events?after=3");
    act(() => FakeEventSource.last.open());
    expect(await screen.findByText("Live")).toBeInTheDocument();
  });

  it("lets the user approve a pending plan with a comment", async () => {
    const approval: ApprovalRequest = {
      id: 42, task_id: "t-1", kind: "plan", status: "pending", comment: null, created_at: new Date().toISOString(), decided_at: null,
      task_title: "Compare AI hiring",
      payload: { title: "Review the execution plan", estimated_cost_usd: 0.4,
                 plan: { objective_summary: "Compare hiring", subtasks: [{ id: "t1", title: "Market size", queries: ["india ai hiring 2026"], depends_on: [] }] } },
    };
    let decision: unknown = null;
    useTaskHandlers(detail({ status: "awaiting_approval", current_stage: "planning", agent_executions: [exec("planner", "completed")] }), { approvals: [approval] });
    server.use(http.post("/api/v1/approvals/42/decision", async ({ request }) => {
      decision = await request.json();
      return HttpResponse.json({ ...approval, status: "approved" });
    }));
    renderWithProviders(<AppRoutes />, { route: "/tasks/t-1" });
    expect(await screen.findByText("Waiting for your decision")).toBeInTheDocument();
    expect(screen.getByText("india ai hiring 2026")).toBeInTheDocument();
    expect(screen.getByText("$0.40 (upper-bound estimate)")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Comment (optional)"), "Looks good");
    await userEvent.click(screen.getByRole("button", { name: /Approve and continue/ }));
    await waitFor(() => expect(decision).toEqual({ approved: true, comment: "Looks good" }));
  });

  it("asks for confirmation before rejecting", async () => {
    const approval: ApprovalRequest = {
      id: 43, task_id: "t-1", kind: "tools", status: "pending", comment: null, created_at: new Date().toISOString(), decided_at: null,
      task_title: null, payload: { title: "Approve 1 web search(es)", queries: { t1: ["q one"] } },
    };
    let decision: unknown = null;
    useTaskHandlers(detail({ status: "awaiting_approval" }), { approvals: [approval] });
    server.use(http.post("/api/v1/approvals/43/decision", async ({ request }) => {
      decision = await request.json();
      return HttpResponse.json({ ...approval, status: "rejected" });
    }));
    renderWithProviders(<AppRoutes />, { route: "/tasks/t-1" });
    await userEvent.type(await screen.findByLabelText("Comment (optional)"), "Too broad");
    await userEvent.click(screen.getByRole("button", { name: /Reject and stop/ }));
    expect(decision).toBeNull();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(decision).toEqual({ approved: false, comment: "Too broad" }));
  });

  it("shows a completed run: report links, sources with citations and verification verdicts", async () => {
    const verifs: VerificationResult[] = [
      { id: 1, iteration: 0, claim: "Claim A", verdict: "supported", confidence: 0.91, reasoning: "Backed by S1", supporting_refs: ["S1"], conflicting_refs: [] },
      { id: 2, iteration: 0, claim: "Claim B", verdict: "conflicting", confidence: 0.4, reasoning: "S2 disagrees", supporting_refs: ["S1"], conflicting_refs: ["S2"] },
      { id: 3, iteration: 0, claim: "Claim C", verdict: "unverifiable", confidence: 0.2, reasoning: "No evidence", supporting_refs: [], conflicting_refs: [] },
    ];
    useTaskHandlers(detail({ status: "completed", current_stage: "finished", progress_done: 7, report_id: 11, finished_at: new Date().toISOString(),
                             agent_executions: [exec("planner", "completed", { output: { result: { objective_summary: "Plan summary", subtasks: [{ id: "t1", title: "Market size" }] } } })] }),
                    { sources: [source("S1"), source("S2", { url: "demo://source/abc", mode: "demo", injection_suspected: true, injection_rules: ["ignore_instructions"] })],
                      verifications: verifs });
    renderWithProviders(<AppRoutes />, { route: "/tasks/t-1" });
    await sseOpen();
    act(() => FakeEventSource.last.emit(ev("task_finished", { status: "completed" }, "Execution completed.")));
    expect(await screen.findByText("Execution completed")).toBeInTheDocument();
    expect(await screen.findByText("Stream closed · execution finished")).toBeInTheDocument();
    expect(await screen.findByText("Vector DB report")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Markdown/ })).toHaveAttribute("href", "/api/v1/reports/11/download.md");
    expect(screen.getByRole("link", { name: /PDF/ })).toHaveAttribute("href", "/api/v1/reports/11/download.pdf");
    expect(screen.getByRole("button", { name: /Re-run/ })).toBeInTheDocument();

    // Sources: real URLs are links, demo:// sources are not; injection flag shown; citation counts from verdicts
    const s1 = await screen.findByRole("link", { name: /Source S1/ });
    expect(s1).toHaveAttribute("href", "https://example.org/S1");
    expect(screen.queryByRole("link", { name: /Source S2/ })).toBeNull();
    expect(screen.getByText(/synthetic demo source/)).toBeInTheDocument();
    expect(screen.getByText(/Instruction-like text detected/)).toBeInTheDocument();
    expect(screen.getByText("cited in 2 verdicts")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /Verification/ }));
    expect(await screen.findByText("Claim B")).toBeInTheDocument();
    expect(screen.getByText("Supported · 1")).toBeInTheDocument();
    expect(screen.getByText("Conflicting · 1")).toBeInTheDocument();
    expect(screen.getByText("Unverifiable · 1")).toBeInTheDocument();
    expect(screen.getByText("91%")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /Outputs/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Planner/ }));
    expect(await screen.findByText("Plan summary")).toBeInTheDocument();
  });

  it("explains failures with the backend's error and offers a re-run", async () => {
    useTaskHandlers(detail({ status: "failed", error_code: "budget_max_llm_calls", error_message: "LLM call limit (40) reached.",
                             finished_at: new Date().toISOString(), agent_executions: [exec("planner", "failed", { error_message: "LLM call limit (40) reached." })] }));
    renderWithProviders(<AppRoutes />, { route: "/tasks/t-1" });
    expect(await screen.findByText("Execution failed")).toBeInTheDocument();
    expect(screen.getAllByText("LLM call limit (40) reached.").length).toBeGreaterThan(0);
    expect(screen.getByText("budget_max_llm_calls")).toBeInTheDocument();
    expect(screen.getByText(/No report was produced/)).toBeInTheDocument();
    expect(document.querySelector("[data-stage=planner]")).toHaveAttribute("data-state", "failed");
    expect(document.querySelector("[data-stage=report]")).toHaveAttribute("data-state", "not_run");
  });

  it("shows a not-found state for an unknown execution", async () => {
    server.use(http.get("/api/v1/tasks/:id", () =>
      HttpResponse.json({ error: { code: "not_found", message: "Task not found.", request_id: "r" } }, { status: 404 })));
    renderWithProviders(<AppRoutes />, { route: "/tasks/nope" });
    expect(await screen.findByText("Execution not found")).toBeInTheDocument();
  });
});
