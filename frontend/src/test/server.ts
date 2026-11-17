import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import type { SystemStatus } from "@/types/api";

/* Test fixtures only — used by unit tests, never shipped in the app bundle. */
export const fixtures = {
  user: { id: 1, username: "admin" },
  statusDemoOnly: {
    app_env: "development", auth_enabled: true, live_mode_available: false, default_llm_provider: "anthropic",
    llm_providers: [{ name: "anthropic", configured: false, default_model: "claude-opus-5-5" },
                    { name: "gemini", configured: false, default_model: null }],
    search_provider: { name: "tavily", configured: false, default_model: null },
    missing_for_live: ["ANTHROPIC_API_KEY", "TAVILY_API_KEY"], version: "0.1.0",
  } satisfies SystemStatus,
};

export const defaultHandlers = [
  http.get("/api/v1/auth/me", () => HttpResponse.json(fixtures.user)),
  http.get("/api/v1/system/status", () => HttpResponse.json(fixtures.statusDemoOnly)),
  http.get("/api/v1/approvals", () => HttpResponse.json([])),
];

export const server = setupServer(...defaultHandlers);

export const unauthorized = http.get("/api/v1/auth/me", () =>
  HttpResponse.json({ error: { code: "unauthorized", message: "Authentication required.", request_id: "r1" } },
                    { status: 401 }));
