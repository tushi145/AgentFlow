import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { AppRoutes } from "@/App";
import { ModeBadge, StatusBadge } from "@/components/status";
import { durationBetween, formatCost, formatDuration, formatNumber, formatPercent, formatRelative, parseApiDate } from "@/lib/format";
import { api, ApiError } from "@/services/api";

import { renderWithProviders } from "./render";
import { fixtures, server, unauthorized } from "./server";

describe("api client", () => {
  it("parses the backend error envelope", async () => {
    server.use(http.get("/api/v1/system/status", () =>
      HttpResponse.json({ error: { code: "provider_not_configured", message: "Missing keys", request_id: "abc" } },
                        { status: 409 })));
    const err = await api.systemStatus().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "provider_not_configured", message: "Missing keys", requestId: "abc" });
  });

  it("reports network failures clearly", async () => {
    server.use(http.get("/api/v1/health", () => HttpResponse.error()));
    const err = await api.health().catch((e) => e);
    expect(err).toMatchObject({ status: 0, code: "network_error" });
  });

  it("serialises query parameters and skips empty ones", async () => {
    let url = "";
    server.use(http.get("/api/v1/tasks", ({ request }) => {
      url = request.url;
      return HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 });
    }));
    await api.listTasks({ status: "active", q: "", page: 2 });
    expect(url).toContain("status=active");
    expect(url).toContain("page=2");
    expect(url).not.toContain("q=");
  });
});

describe("formatters never invent values", () => {
  it("renders missing values as an em dash", () => {
    expect(formatNumber(null)).toBe("—");
    expect(formatCost(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
    expect(formatDuration(null)).toBe("—");
  });
  it("formats real values", () => {
    expect(formatNumber(12345)).toBe("12,345");
    expect(formatCost(0.0042)).toBe("$0.0042");
    expect(formatCost(1.5)).toBe("$1.50");
    expect(formatPercent(0.873, 1)).toBe("87.3%");
    expect(formatDuration(75)).toBe("1m 15s");
  });
});

describe("timestamps", () => {
  it("treats API timestamps as UTC whether or not they carry an offset", () => {
    expect(parseApiDate("2026-11-17T10:00:00").toISOString()).toBe("2026-11-17T10:00:00.000Z");
    expect(parseApiDate("2026-11-17T10:00:00+00:00").toISOString()).toBe("2026-11-17T10:00:00.000Z");
    expect(parseApiDate("2026-11-17T15:30:00+05:30").toISOString()).toBe("2026-11-17T10:00:00.000Z");
    const now = Date.parse("2026-11-17T10:00:30Z");
    expect(formatRelative("2026-11-17T10:00:00", now)).toBe("just now"); // not "5 hours ago"
    expect(durationBetween("2026-11-17T10:00:00", null, now)).toBe(30);
  });
});

describe("status components", () => {
  it("labels demo mode explicitly", () => {
    renderWithProviders(<><ModeBadge mode="demo" /><ModeBadge mode="live" /></>);
    expect(screen.getByText("DEMO")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });
  it("renders every task status", () => {
    renderWithProviders(<><StatusBadge status="awaiting_approval" /><StatusBadge status="failed" /></>);
    expect(screen.getByText("Awaiting approval")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });
});

describe("app shell", () => {
  it("redirects to login when the session is missing, then signs in", async () => {
    server.use(unauthorized);
    let posted: unknown = null;
    server.use(http.post("/api/v1/auth/login", async ({ request }) => {
      posted = await request.json();
      server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(fixtures.user)));
      return HttpResponse.json(fixtures.user);
    }));
    renderWithProviders(<AppRoutes />, { route: "/history" });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(posted).toEqual({ username: "admin", password: "pw" });
    // returns to the originally requested page
    expect(await screen.findByRole("heading", { name: "Execution history" })).toBeInTheDocument();
  });

  it("shows a login error from the API", async () => {
    server.use(unauthorized, http.post("/api/v1/auth/login", () =>
      HttpResponse.json({ error: { code: "unauthorized", message: "Invalid username or password.", request_id: "x" } },
                        { status: 401 })));
    renderWithProviders(<AppRoutes />, { route: "/login" });
    await userEvent.type(await screen.findByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "bad");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password.");
  });

  it("renders navigation, real system status and the demo-mode notice", async () => {
    renderWithProviders(<AppRoutes />, { route: "/" });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    for (const label of ["Overview", "New Task", "Active Executions", "Execution History", "Reports",
                         "Evaluation", "Agent Configuration", "Settings"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(await screen.findByText("Demo only")).toBeInTheDocument();
    expect(screen.getByText(/Live AI execution is not configured/)).toBeInTheDocument();
    expect(screen.getByText("ANTHROPIC_API_KEY, TAVILY_API_KEY")).toBeInTheDocument();
  });

  it("hides the demo notice when live execution is available", async () => {
    server.use(http.get("/api/v1/system/status", () =>
      HttpResponse.json({ ...fixtures.statusDemoOnly, live_mode_available: true, missing_for_live: [] })));
    renderWithProviders(<AppRoutes />, { route: "/" });
    expect(await screen.findByText("Live AI ready")).toBeInTheDocument();
    expect(screen.queryByText(/Live AI execution is not configured/)).not.toBeInTheDocument();
  });

  it("shows the real pending-approval count", async () => {
    server.use(http.get("/api/v1/approvals", () => HttpResponse.json([
      { id: 1, task_id: "t", kind: "plan", payload: {}, status: "pending", comment: null,
        created_at: new Date().toISOString(), decided_at: null, task_title: "x" },
      { id: 2, task_id: "t", kind: "tools", payload: {}, status: "pending", comment: null,
        created_at: new Date().toISOString(), decided_at: null, task_title: "x" }])));
    renderWithProviders(<AppRoutes />, { route: "/" });
    expect(await screen.findByLabelText("2 pending approvals")).toBeInTheDocument();
  });

  it("navigates between pages and highlights the active link", async () => {
    renderWithProviders(<AppRoutes />, { route: "/" });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await userEvent.click(within(nav).getByRole("link", { name: "Reports" }));
    expect(await screen.findByRole("heading", { name: "Reports" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Reports" })).toHaveAttribute("aria-current", "page");
  });

  it("defaults to dark theme and persists a change", async () => {
    renderWithProviders(<AppRoutes />, { route: "/" });
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    await userEvent.click(await screen.findByRole("button", { name: "Change theme" }));
    await userEvent.click(await screen.findByRole("menuitemradio", { name: /Light/ }));
    await waitFor(() => expect(document.documentElement).not.toHaveClass("dark"));
    expect(localStorage.getItem("agentflow-theme")).toBe("light");
  });

  it("shows an API error state with the request id when the backend fails", async () => {
    server.use(http.get("/api/v1/auth/me", () =>
      HttpResponse.json({ error: { code: "internal_error", message: "An internal error occurred.", request_id: "req-9" } },
                        { status: 500 })));
    renderWithProviders(<AppRoutes />, { route: "/" });
    expect(await screen.findByRole("alert", {}, { timeout: 4000 })).toHaveTextContent("req-9");
  });
});
