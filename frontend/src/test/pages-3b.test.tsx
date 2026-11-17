import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { AppRoutes } from "@/App";
import type { OverviewAnalytics, TaskSummary } from "@/types/api";

import { renderWithProviders } from "./render";
import { fixtures, server, task } from "./server";

const listOf = (items: TaskSummary[], total = items.length, page = 1) =>
  HttpResponse.json({ items, total, page, page_size: 20 });

function overview(over: Partial<OverviewAnalytics["totals"]> = {}, breakdown: OverviewAnalytics["status_breakdown"] = {}): OverviewAnalytics {
  return {
    totals: { tasks: 9, completed: 5, failed: 1, active: 3, awaiting_approval: 1, success_rate: 0.8333,
              avg_duration_s: 42, agents: 5, agents_enabled: 5, total_tokens: 123456, total_cost_usd: 0.42, demo_tasks: 2, ...over },
    series: [{ date: "2026-11-16", completed: 2, failed: 1, other: 0 }, { date: "2026-11-17", completed: 3, failed: 0, other: 3 }],
    status_breakdown: { completed: 5, failed: 1, running: 1, planning: 1, awaiting_approval: 1, ...breakdown },
    per_agent: { planner: { runs: 9, failed: 1, avg_ms: 1500, tokens: 5000 }, research: { runs: 20, failed: 0, avg_ms: 3000, tokens: 90000 } },
    recent: [{ id: "r1", title: "Most recent run", status: "completed", mode: "live", created_at: new Date().toISOString(), cost_usd: 0.1 }],
  };
}

describe("Overview", () => {
  it("shows KPIs exactly as reported by the API", async () => {
    server.use(http.get("/api/v1/analytics/overview", () => HttpResponse.json(overview())));
    renderWithProviders(<AppRoutes />, { route: "/" });
    const card = async (label: string) => (await screen.findByText(label, { selector: "[data-stat-label]" })).closest("[data-slot=card]") as HTMLElement;
    expect(await within(await card("Total executions")).findByText("9")).toBeInTheDocument();
    expect(await within(await card("Completed")).findByText("83% success rate")).toBeInTheDocument();
    expect(await within(await card("Running")).findByText("2")).toBeInTheDocument(); // running + planning
    expect(await within(await card("Pending")).findByText("1 awaiting approval")).toBeInTheDocument();
    expect(await within(await card("API cost (live)")).findByText("$0.42")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Most recent run/ })).toHaveAttribute("href", "/tasks/r1");
    expect(screen.getByRole("img", { name: "Executions per day by outcome" })).toBeInTheDocument();
  });

  it("shows a welcome state for a brand-new user (no invented numbers)", async () => {
    server.use(http.get("/api/v1/analytics/overview", () =>
      HttpResponse.json({ ...overview({ tasks: 0, completed: 0, failed: 0, active: 0, awaiting_approval: 0, success_rate: null,
                                          avg_duration_s: null, total_tokens: 0, total_cost_usd: null, demo_tasks: 0 }),
                          series: [], status_breakdown: {}, per_agent: {}, recent: [] })));
    renderWithProviders(<AppRoutes />, { route: "/" });
    expect(await screen.findByText("No executions yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Create your first task/ })).toHaveAttribute("href", "/tasks/new");
  });

  it("filters by mode through the API and shows API errors", async () => {
    const modes: (string | null)[] = [];
    server.use(http.get("/api/v1/analytics/overview", ({ request }) => {
      const m = new URL(request.url).searchParams.get("mode");
      modes.push(m);
      if (m === "demo") return HttpResponse.json({ error: { code: "boom", message: "Analytics failed", request_id: "q1" } }, { status: 500 });
      return HttpResponse.json(overview());
    }));
    renderWithProviders(<AppRoutes />, { route: "/" });
    await userEvent.click(await screen.findByRole("tab", { name: "Demo" }));
    expect(await screen.findByText("Analytics failed", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(modes).toContain("demo");
  });
});

describe("New task", () => {
  it("defaults to demo when live is unavailable and blocks live", async () => {
    renderWithProviders(<AppRoutes />, { route: "/tasks/new" });
    const live = await screen.findByRole("radio", { name: /Live AI execution/ });
    expect(live).toBeDisabled();
    expect(screen.getByRole("radio", { name: /Demo/ })).toBeChecked();
    expect(within(live.closest("label")!).getByText("ANTHROPIC_API_KEY, TAVILY_API_KEY")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start demo run" })).toBeInTheDocument();
  });

  it("validates the prompt and limits client-side", async () => {
    renderWithProviders(<AppRoutes />, { route: "/tasks/new" });
    await userEvent.type(await screen.findByLabelText("Research objective"), "short");
    await userEvent.click(screen.getByRole("button", { name: "Start demo run" }));
    expect(await screen.findByText(/at least 10 characters/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Limits/ }));
    await userEvent.type(screen.getByLabelText(/Max LLM calls/), "9999");
    expect(await screen.findByText("Maximum 500.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start demo run" })).toBeDisabled();
  });

  it("submits exactly what the user configured and goes to Active Executions", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/v1/tasks", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(task({ id: "new-1", status: "queued" }), { status: 201 });
      }),
      http.get("/api/v1/tasks", () => listOf([task({ id: "new-1", status: "running", progress_done: 1, progress_total: 6 })])),
    );
    renderWithProviders(<AppRoutes />, { route: "/tasks/new" });
    await userEvent.type(await screen.findByLabelText("Research objective"), "Compare vector databases for production use");
    await userEvent.click(screen.getByRole("radio", { name: /Sequential/ }));
    await userEvent.click(screen.getByRole("radio", { name: /Approve plan/ }));
    await userEvent.click(screen.getByRole("switch", { name: "Enable Verification Agent" }));
    await userEvent.click(screen.getByRole("button", { name: /Limits/ }));
    await userEvent.type(screen.getByLabelText(/Max LLM calls/), "12");
    await userEvent.type(screen.getByLabelText(/Research iterations/), "2"); // same as default → not sent
    await userEvent.click(screen.getByRole("button", { name: "Start demo run" }));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({
      prompt: "Compare vector databases for production use", mode: "demo", execution_mode: "sequential",
      approval_mode: "plan", limits: { max_llm_calls: 12 }, disabled_agents: ["verification"],
    });
    expect(await screen.findByRole("heading", { name: "Active executions" })).toBeInTheDocument();
  });

  it("selects live when available and surfaces backend refusals", async () => {
    server.use(
      http.get("/api/v1/system/status", () => HttpResponse.json({ ...fixtures.statusDemoOnly, live_mode_available: true, missing_for_live: [] })),
      http.post("/api/v1/tasks", () => HttpResponse.json(
        { error: { code: "invalid_model", message: "research agent: 'x' is not in anthropic's current model list.", request_id: "rq" } },
        { status: 422 })),
    );
    renderWithProviders(<AppRoutes />, { route: "/tasks/new" });
    await waitFor(() => expect(screen.getByRole("radio", { name: /Live AI execution/ })).toBeChecked());
    await userEvent.type(screen.getByLabelText("Research objective"), "Compare vector databases for production use");
    await userEvent.click(screen.getByRole("button", { name: "Start live execution" }));
    expect(await screen.findByText(/not in anthropic's current model list/)).toBeInTheDocument();
  });
});

describe("Active executions", () => {
  const approval = {
    id: 5, task_id: "a-1", kind: "plan", status: "pending", comment: null, created_at: new Date().toISOString(),
    decided_at: null, task_title: "Waiting task",
    payload: { title: "Review the execution plan", estimated_cost_usd: 0.31,
               plan: { subtasks: [{ id: "t1", title: "Find companies" }, { id: "t2", title: "Find salaries" }] } },
  };

  it("shows real progress and approvals; approve and reject call the API", async () => {
    const decisions: unknown[] = [];
    server.use(
      http.get("/api/v1/tasks", () => listOf([task({ id: "a-1", status: "awaiting_approval", progress_done: 1, progress_total: 6, current_stage: "planning", title: "Waiting task" })])),
      http.get("/api/v1/approvals", () => HttpResponse.json([approval])),
      http.post("/api/v1/approvals/5/decision", async ({ request }) => {
        decisions.push(await request.json());
        return HttpResponse.json({ ...approval, status: "approved" });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/executions" });
    expect(await screen.findByText("1/6 steps · 17%")).toBeInTheDocument();
    expect(screen.getByText("Find salaries")).toBeInTheDocument();
    expect(screen.getByText("$0.31")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Approve/ }));
    await waitFor(() => expect(decisions).toEqual([{ approved: true, comment: null }]));

    await userEvent.click(screen.getByRole("button", { name: /Reject/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Comment/), "too broad");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(decisions[1]).toEqual({ approved: false, comment: "too broad" }));
  });

  it("cancels after confirmation", async () => {
    let cancelled = false;
    server.use(
      http.get("/api/v1/tasks", () => listOf([task({ id: "c-1", status: "running", progress_done: 2, progress_total: 7 })])),
      http.post("/api/v1/tasks/c-1/cancel", () => { cancelled = true; return HttpResponse.json(task({ id: "c-1", status: "cancelled" })); }),
    );
    renderWithProviders(<AppRoutes />, { route: "/executions" });
    await userEvent.click(await screen.findByRole("button", { name: /Cancel/ }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel execution" }));
    await waitFor(() => expect(cancelled).toBe(true));
  });

  it("says when nothing is running and when a started task already finished", async () => {
    server.use(http.get("/api/v1/tasks", () => listOf([])));
    renderWithProviders(<AppRoutes />, { route: "/executions?highlight=done-1" });
    expect(await screen.findByText("Nothing is running")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/already finished/);
  });
});

describe("Execution history", () => {
  it("lists tasks, searches, filters and paginates through the API", async () => {
    const urls: URL[] = [];
    server.use(http.get("/api/v1/tasks", ({ request }) => {
      const u = new URL(request.url);
      urls.push(u);
      const page = Number(u.searchParams.get("page") ?? 1);
      return listOf([task({ id: `h-${page}`, title: `Run on page ${page}`, status: "failed", error_message: "Search quota exceeded" })], 45, page);
    }));
    renderWithProviders(<AppRoutes />, { route: "/history" });
    expect(await screen.findByText("Run on page 1")).toBeInTheDocument();
    expect(screen.getByText("Search quota exceeded")).toBeInTheDocument();
    expect(screen.getByText("1–20 of 45")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Run on page 2")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Search executions"), "vector");
    await waitFor(() => expect(urls.some((u) => u.searchParams.get("q") === "vector")).toBe(true));
    // A new search starts at page 1, without first requesting the stale page.
    const searched = urls.filter((u) => u.searchParams.get("q") === "vector");
    expect(searched.every((u) => (u.searchParams.get("page") ?? "1") === "1")).toBe(true);
  });

  it("re-runs and deletes via the backend operations", async () => {
    let retried = false;
    let deleted = false;
    server.use(
      http.get("/api/v1/tasks", () => listOf(deleted ? [] : [task({ id: "d-1", title: "Old run" })])),
      http.post("/api/v1/tasks/d-1/retry", () => { retried = true; return HttpResponse.json(task({ id: "d-2", status: "queued" }), { status: 201 }); }),
      http.delete("/api/v1/tasks/d-1", () => { deleted = true; return new HttpResponse(null, { status: 204 }); }),
    );
    renderWithProviders(<AppRoutes />, { route: "/history" });
    await userEvent.click(await screen.findByRole("button", { name: "Actions for Old run" }));
    expect(screen.getByRole("menuitem", { name: /Open report/ })).toHaveAttribute("href", "/reports/11");
    await userEvent.click(screen.getByRole("menuitem", { name: /Delete/ }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleted).toBe(true));
    expect(await screen.findByText("No executions yet")).toBeInTheDocument();
    expect(retried).toBe(false);
  });

  it("disables delete and re-run for active tasks", async () => {
    server.use(http.get("/api/v1/tasks", () => listOf([task({ id: "x", title: "Live one", status: "running", report_id: null })])));
    renderWithProviders(<AppRoutes />, { route: "/history" });
    await userEvent.click(await screen.findByRole("button", { name: "Actions for Live one" }));
    expect(screen.getByRole("menuitem", { name: /Delete/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: /Re-run/ })).toHaveAttribute("aria-disabled", "true");
  });
});
