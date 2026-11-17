import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import type { AgentConfig, SystemStatus, TaskSummary } from "@/types/api";

/* Test fixtures only — used by unit tests, never shipped in the app bundle. */
export const fixtures = {
  user: { id: 1, username: "admin" },
  statusDemoOnly: {
    app_env: "development", auth_enabled: true, live_mode_available: false, default_llm_provider: "anthropic",
    llm_providers: [{ name: "anthropic", configured: false, default_model: "claude-opus-5-5" },
                    { name: "gemini", configured: false, default_model: null }],
    search_provider: { name: "tavily", configured: false, default_model: null },
    missing_for_live: ["ANTHROPIC_API_KEY", "TAVILY_API_KEY"], version: "0.1.0",
    limits: [
      { name: "max_total_tokens", default: 400000, min: 1000, max: 2000000, kind: "int" },
      { name: "max_llm_calls", default: 40, min: 1, max: 500, kind: "int" },
      { name: "max_cost_usd", default: null, min: 0, max: null, kind: "float" },
      { name: "max_iterations", default: 2, min: 1, max: 5, kind: "int" },
    ],
    approval_cost_threshold_usd: 2, approval_timeout_hours: 24,
  } satisfies SystemStatus,
};

export function task(over: Partial<TaskSummary> = {}): TaskSummary {
  return {
    id: "t-1", title: "Compare AI hiring", prompt: "Compare AI hiring in India", mode: "demo", status: "completed",
    execution_mode: "parallel", approval_mode: "none", current_stage: "finished", progress_done: 7, progress_total: 7,
    iterations: 0, input_tokens: 1200, output_tokens: 300, llm_calls: 6, tool_calls: 4, cost_usd: null,
    estimated_cost_usd: null, budget_exhausted: false, error_code: null, error_message: null,
    created_at: new Date().toISOString(), started_at: new Date(Date.now() - 65_000).toISOString(),
    finished_at: new Date().toISOString(), report_id: 11, ...over,
  };
}

export const agentsFixture: AgentConfig[] = (["planner", "research", "analysis", "verification", "report"] as const).map((k) => ({
  key: k, name: `${k[0].toUpperCase()}${k.slice(1)} Agent`, description: "d", role: "r", allowed_tools: [], enabled: true,
  provider: null, model: null, effort: null, max_output_tokens: 8000, instructions: "", max_retries: 2, timeout_s: 240,
  updated_at: new Date().toISOString(), effective_provider: "anthropic", effective_model: "claude-opus-5-5",
  required: ["planner", "research", "report"].includes(k),
}));

export const defaultHandlers = [
  http.get("/api/v1/auth/me", () => HttpResponse.json(fixtures.user)),
  http.get("/api/v1/system/status", () => HttpResponse.json(fixtures.statusDemoOnly)),
  http.get("/api/v1/approvals", () => HttpResponse.json([])),
  http.get("/api/v1/agents", () => HttpResponse.json(agentsFixture)),
  http.get("/api/v1/tasks", () => HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 })),
  // Brand-new account: the Overview route ("/") is rendered by shell tests too.
  http.get("/api/v1/analytics/overview", () => HttpResponse.json({
    totals: { tasks: 0, completed: 0, failed: 0, active: 0, awaiting_approval: 0, success_rate: null, avg_duration_s: null,
              agents: 5, agents_enabled: 5, total_tokens: 0, total_cost_usd: null, demo_tasks: 0 },
    series: [], status_breakdown: {}, per_agent: {}, recent: [],
  })),
];

export const server = setupServer(...defaultHandlers);

export const unauthorized = http.get("/api/v1/auth/me", () =>
  HttpResponse.json({ error: { code: "unauthorized", message: "Authentication required.", request_id: "r1" } },
                    { status: 401 }));
