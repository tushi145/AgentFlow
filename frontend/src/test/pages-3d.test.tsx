import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

import { AppRoutes } from "@/App";
import { MarkdownReport } from "@/components/report/markdown-report";
import { TooltipProvider } from "@/components/ui/tooltip";
import { buildUpdate, DEFAULT, draftFrom, modelChoice, validateDraft } from "@/lib/agent-form";
import { initialMode, loadPreferences } from "@/lib/preferences";
import { tableOfContents } from "@/lib/report-markdown";
import type {
  AgentConfig, EvaluationRun, EvaluationSummary, ProviderModels, ReportDetail, ReportSummary, ResearchSource,
} from "@/types/api";

import { renderWithProviders } from "./render";
import { agentsFixture, fixtures, server } from "./server";

/* ------------------------------------------------------------------ fixtures */

const src = (ref: string, over: Partial<ResearchSource> = {}): ResearchSource => ({
  id: Number(ref.slice(1)), ref, url: `https://example.org/${ref}`, title: `Source ${ref} title`, domain: "example.org",
  snippet: "snippet", search_query: "q", provider: "tavily", relevance_score: null, injection_suspected: false,
  injection_rules: [], mode: "live", retrieved_at: new Date().toISOString(), ...over,
});

const summary = (over: Partial<ReportSummary> = {}): ReportSummary => ({
  id: 7, task_id: "t-9", title: "Vector database comparison", summary: "Pinecone and pgvector compared.",
  source_count: 2, avg_confidence: 0.82, citation_violations: 1, mode: "live", created_at: new Date().toISOString(),
  has_pdf: true, ...over,
});

const MARKDOWN = `# Vector database comparison

## Executive summary

pgvector is cheaper [S1, S2]. Managed services scale further [S2].

<script>window.__pwned = true</script>
<img src="https://evil.example/x.png" onerror="alert(1)">

## Comparison tables

| Option | Cost | Sources |
|---|---|---|
| pgvector | Low | S1 |

## References

- **[S1]** Source S1 — <https://example.org/S1> (example.org)
- **[S2]** Demo — <demo://source/abc> (source)
`;

const detail = (over: Partial<ReportDetail> = {}): ReportDetail => ({
  ...summary(), markdown: MARKDOWN, prompt: "Compare vector databases for production",
  sources: [src("S1"), src("S2", { url: "demo://source/abc", title: "Synthetic S2", mode: "demo" })],
  verifications: [
    { id: 1, iteration: 0, claim: "pgvector is cheaper", verdict: "supported", confidence: 0.9, reasoning: "S1 says so", supporting_refs: ["S1"], conflicting_refs: [] },
    { id: 2, iteration: 0, claim: "Managed scales further", verdict: "unverifiable", confidence: 0.3, reasoning: "Thin", supporting_refs: [], conflicting_refs: [] },
  ],
  ...over,
});

const agent = (over: Partial<AgentConfig> = {}): AgentConfig => ({ ...agentsFixture[2], ...over });

const providers = (configured: boolean): ProviderModels[] => [
  { provider: "anthropic", configured, default_model: configured ? "claude-opus-5-5" : null, default_model_status: configured ? "valid" : null, error: null,
    models: configured ? [
      { id: "claude-opus-5-5", display_name: "Claude Opus 5.5", provider: "anthropic", max_input_tokens: 200000, max_output_tokens: 32000 },
      { id: "claude-haiku-5-5", display_name: "Claude Haiku 5.5", provider: "anthropic", max_input_tokens: 200000, max_output_tokens: 8000 },
    ] : [] },
  { provider: "gemini", configured: false, default_model: null, default_model_status: null, error: null, models: [] },
];

const evalSummary = (over: Partial<EvaluationSummary> = {}): EvaluationSummary => ({
  live_runs_evaluated: 0, task_completion_rate: null, citation_integrity_rate: null, source_coverage_rate: null,
  claims_with_source_rate: null, supported_claim_rate: null, mean_confidence: null, agent_failures: {},
  verification_benchmark: null, duration_s: { runs: 0, mean: null, median: null, max: null },
  tokens: { runs: 0, total: null, mean_per_run: null }, ...over,
});

const run = (over: Partial<EvaluationRun> = {}): EvaluationRun => ({
  task_id: "r-1", title: "Live run one", mode: "live", status: "completed", created_at: new Date().toISOString(),
  duration_s: 84, input_tokens: 9000, output_tokens: 1000, llm_calls: 7, tool_calls: 5, cost_usd: 0.12,
  metrics: { mode: "live", completed: true, budget_exhausted: false, iterations: 1,
             citation_integrity: { valid: 4, removed: 0, rate: 1 },
             source_coverage: { subtasks: 3, subtasks_with_sources: 3, rate: 1, sources: 9, unique_domains: 6, claims_with_source_rate: 1, flagged_injection_sources: 0 },
             verification: { claims: 5, verdicts: { supported: 4, unverifiable: 1 }, supported_rate: 0.8, mean_confidence: 0.71, retrieval_method: "bm25" } },
  ...over,
});

/* ------------------------------------------------------------------ pure helpers */

describe("report markdown helpers", () => {
  it("builds a table of contents with unique, stable ids and ignores code fences", () => {
    const toc = tableOfContents("# T\n## Findings [S1]\n```\n## not a heading\n```\n### Detail\n## Findings\n");
    expect(toc).toEqual([
      { id: "findings", text: "Findings", level: 2 },
      { id: "detail", text: "Detail", level: 3 },
      { id: "findings-2", text: "Findings", level: 2 },
    ]);
  });
});

describe("report heading anchors", () => {
  it("match the table of contents even under StrictMode double rendering", () => {
    const md = "# Title\n## Executive summary [S1]\n## Findings\n### Detail\n## Findings\n";
    const { container } = render(<StrictMode><TooltipProvider><MarkdownReport markdown={md} sources={[]} /></TooltipProvider></StrictMode>);
    const ids = [...container.querySelectorAll("h2, h3")].map((h) => h.id);
    expect(ids).toEqual(tableOfContents(md).map((t) => t.id));
    expect(ids).toEqual(["executive-summary", "findings", "detail", "findings-2"]);
  });
});

describe("agent form model", () => {
  const a = agent({ max_output_tokens: 12000, provider: null, model: null, effort: null });

  it("sends only changed fields, and clears overrides explicitly", () => {
    expect(buildUpdate(a, draftFrom(a))).toBeUndefined();
    expect(buildUpdate(a, { ...draftFrom(a), max_output_tokens: "16000", instructions: "Prefer primary sources." }))
      .toEqual({ max_output_tokens: 16000, instructions: "Prefer primary sources." });
    expect(buildUpdate(a, { ...draftFrom(a), model: modelChoice("anthropic", "claude-haiku-5-5"), effort: "high" }))
      .toEqual({ provider: "anthropic", model: "claude-haiku-5-5", effort: "high" });
    const overridden = agent({ provider: "anthropic", model: "claude-haiku-5-5", effort: "low" });
    expect(buildUpdate(overridden, { ...draftFrom(overridden), model: DEFAULT, effort: DEFAULT }))
      .toEqual({ clear_model_override: true, clear_effort: true });
  });

  it("validates exactly the backend's bounds and the model's output limit", () => {
    const d = draftFrom(a);
    expect(validateDraft(d, a)).toEqual({});
    expect(validateDraft({ ...d, max_output_tokens: "100" }, a).max_output_tokens).toMatch(/between 256 and 64,000/);
    expect(validateDraft({ ...d, max_output_tokens: "12.5" }, a).max_output_tokens).toMatch(/whole number/);
    expect(validateDraft({ ...d, max_retries: "6" }, a).max_retries).toMatch(/between 0 and 5/);
    expect(validateDraft({ ...d, timeout_s: "5" }, a).timeout_s).toMatch(/between 10 and 900/);
    expect(validateDraft({ ...d, instructions: "x".repeat(4001) }, a).instructions).toMatch(/4000/);
    expect(validateDraft({ ...d, max_output_tokens: "12000" }, a, 8000).max_output_tokens).toMatch(/at most 8,000/);
    const required = agent({ key: "planner", required: true, name: "Planner Agent" });
    expect(validateDraft({ ...draftFrom(required), enabled: false }, required).enabled).toMatch(/required/);
  });
});

describe("execution preferences", () => {
  it("falls back to defaults on bad data and never pre-selects an unavailable Live mode", () => {
    localStorage.setItem("agentflow.execution-preferences.v1", "{not json");
    expect(loadPreferences()).toEqual({ mode: "auto", execution_mode: "parallel", approval_mode: "none" });
    localStorage.setItem("agentflow.execution-preferences.v1", JSON.stringify({ mode: "live", execution_mode: "bogus", approval_mode: "plan" }));
    const p = loadPreferences();
    expect(p).toEqual({ mode: "live", execution_mode: "parallel", approval_mode: "plan" });
    expect(initialMode(p, false)).toBe("demo");
    expect(initialMode(p, true)).toBe("live");
    expect(initialMode({ ...p, mode: "demo" }, true)).toBe("demo");
  });
});

/* ------------------------------------------------------------------ Reports */

describe("Reports", () => {
  it("lists reports from the API and searches server-side", async () => {
    const queries: (string | null)[] = [];
    server.use(http.get("/api/v1/reports", ({ request }) => {
      const q = new URL(request.url).searchParams.get("q");
      queries.push(q);
      const items = q === "nothing" ? [] : [summary(), summary({ id: 8, title: "Demo run", mode: "demo", citation_violations: 0 })];
      return HttpResponse.json({ items, total: items.length, page: 1, page_size: 12 });
    }));
    renderWithProviders(<AppRoutes />, { route: "/reports" });
    expect(await screen.findByRole("link", { name: "Vector database comparison" })).toHaveAttribute("href", "/reports/7");
    expect(screen.getByText("1 invalid citation(s) removed")).toBeInTheDocument();
    expect(screen.getAllByText("DEMO").length).toBeGreaterThan(0);
    await userEvent.type(screen.getByLabelText("Search reports"), "nothing");
    expect(await screen.findByText("No matching reports")).toBeInTheDocument();
    expect(queries).toContain("nothing");
  });

  it("shows an empty state when no reports exist", async () => {
    server.use(http.get("/api/v1/reports", () => HttpResponse.json({ items: [], total: 0, page: 1, page_size: 12 })));
    renderWithProviders(<AppRoutes />, { route: "/reports" });
    expect(await screen.findByText("No reports yet")).toBeInTheDocument();
  });

  it("renders the report safely with citation links, sources, verification and downloads", async () => {
    server.use(http.get("/api/v1/reports/7", () => HttpResponse.json(detail())));
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    renderWithProviders(<AppRoutes />, { route: "/reports/7" });
    const article = await screen.findByTestId("report-markdown");

    // GFM table rendered as a real table
    expect(within(article).getByRole("table")).toBeInTheDocument();
    expect(within(article).getByRole("cell", { name: "pgvector" })).toBeInTheDocument();
    // Raw HTML from the report is NOT rendered as elements
    expect(article.querySelector("script")).toBeNull();
    expect(article.querySelector("img")).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
    // Citations become links to the source list
    const cites = article.querySelectorAll("a[data-citation]");
    expect([...cites].map((c) => c.getAttribute("data-citation"))).toEqual(["S1", "S2", "S2", "S1", "S2"]);
    await userEvent.click(cites[0] as HTMLElement);
    expect(scroll).toHaveBeenCalled();
    expect(document.getElementById("source-S1")).not.toBeNull();
    // http(s) links open externally with safe rel; demo:// is plain text
    const ext = within(article).getByRole("link", { name: "https://example.org/S1" });
    expect(ext).toHaveAttribute("target", "_blank");
    expect(ext.getAttribute("rel")).toContain("noopener");
    expect(within(article).queryByRole("link", { name: /demo:\/\// })).toBeNull();
    expect(within(article).getByText("demo://source/abc")).toBeInTheDocument();
    // Navigation, stats, verification and downloads
    expect(screen.getByRole("navigation", { name: "Report contents" })).toHaveTextContent("Executive summary");
    expect(screen.getByRole("link", { name: /Markdown/ })).toHaveAttribute("href", "/api/v1/reports/7/download.md");
    expect(screen.getByRole("link", { name: /PDF/ })).toHaveAttribute("href", "/api/v1/reports/7/download.pdf");
    expect(screen.getByRole("link", { name: "Execution" })).toHaveAttribute("href", "/tasks/t-9");
    expect(screen.getByText("Managed scales further")).toBeInTheDocument();
    expect(screen.getByText("Supported · 1")).toBeInTheDocument();
    scroll.mockRestore();
  });

  it("labels demo reports and handles missing reports", async () => {
    server.use(http.get("/api/v1/reports/:id", ({ params }) => params.id === "8"
      ? HttpResponse.json(detail({ id: 8, mode: "demo" }))
      : HttpResponse.json({ error: { code: "not_found", message: "Report not found.", request_id: "r" } }, { status: 404 })));
    const { unmount } = renderWithProviders(<AppRoutes />, { route: "/reports/8" });
    expect(await screen.findByText(/Demo report: produced from fixed templates/)).toBeInTheDocument();
    unmount();
    renderWithProviders(<AppRoutes />, { route: "/reports/999" });
    expect(await screen.findByText("Report not found")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ Agents */

describe("Agent configuration", () => {
  it("validates before saving and sends only the changed fields", async () => {
    let body: unknown = null;
    server.use(
      http.get("/api/v1/models", () => HttpResponse.json(providers(false))),
      http.put("/api/v1/agents/:key", async ({ request, params }) => {
        body = await request.json();
        const a = agentsFixture.find((x) => x.key === params.key)!;
        return HttpResponse.json({ ...a, ...(body as object), updated_at: new Date(Date.now() + 1000).toISOString() });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: "/agents" });
    expect(await screen.findByText(/No LLM provider is configured \(set ANTHROPIC_API_KEY or GEMINI_API_KEY on the server\)/)).toBeInTheDocument();
    const form = await screen.findByText("Planner Agent", { selector: "h2" }).then((h) => h.closest("[data-agent-form]") as HTMLElement);
    expect(within(form).getByRole("switch")).toBeDisabled(); // required agent
    const save = within(form).getByRole("button", { name: /Save changes/ });
    expect(save).toBeDisabled(); // nothing changed

    const tokens = within(form).getByLabelText("Max output tokens");
    await userEvent.clear(tokens);
    await userEvent.type(tokens, "99");
    expect(within(form).getByText(/between 256 and 64,000/)).toBeInTheDocument();
    expect(save).toBeDisabled();

    await userEvent.clear(tokens);
    await userEvent.type(tokens, "9000");
    await userEvent.type(within(form).getByLabelText("Additional instructions"), "Cite official sources.");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    await waitFor(() => expect(body).toEqual({ max_output_tokens: 9000, instructions: "Cite official sources." }));
  });

  it("lists provider models and shows backend model validation errors", async () => {
    server.use(
      http.get("/api/v1/models", () => HttpResponse.json(providers(true))),
      http.put("/api/v1/agents/:key", () => HttpResponse.json(
        { error: { code: "model_invalid", message: "Model 'claude-haiku-5-5' is not available.", request_id: "r", details: { suggestions: ["claude-opus-5-5"] } } },
        { status: 422 })),
    );
    renderWithProviders(<AppRoutes />, { route: "/agents" });
    await userEvent.click(await screen.findByRole("tab", { name: /Analysis/ }));
    const form = (await screen.findByText("Analysis Agent", { selector: "h2" })).closest("[data-agent-form]") as HTMLElement;
    await userEvent.click(within(form).getByRole("combobox", { name: "Model" }));
    await userEvent.click(await screen.findByRole("option", { name: /Claude Haiku 5.5/ }));
    // Haiku allows 8,000 output tokens; the agent is set to 8,000 in the fixture, so no error yet.
    await userEvent.click(within(form).getByRole("button", { name: /Save changes/ }));
    expect(await within(form).findByText("Model 'claude-haiku-5-5' is not available.")).toBeInTheDocument();
    expect(within(form).getByText("Suggestions: claude-opus-5-5")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ Evaluation */

describe("Evaluation", () => {
  it("shows 'No data' — not zero — when there are no live runs", async () => {
    server.use(
      http.get("/api/v1/evaluations/summary", () => HttpResponse.json(evalSummary())),
      http.get("/api/v1/evaluations/runs", () => HttpResponse.json({ items: [], total: 0, page: 1, page_size: 15 })),
      http.get("/api/v1/evaluations/benchmarks", () => HttpResponse.json([])),
    );
    renderWithProviders(<AppRoutes />, { route: "/evaluation" });
    expect(await screen.findByText(/No live runs have been evaluated yet/)).toBeInTheDocument();
    await waitFor(() => expect(document.querySelectorAll("[data-no-data]")).toHaveLength(6));
    expect(screen.queryByText("0%")).toBeNull();
    expect(screen.getByRole("button", { name: /Run benchmark/ })).toBeDisabled(); // demo-only environment
    expect(screen.getByText("No live runs evaluated yet")).toBeInTheDocument();
  });

  it("shows recorded metrics, including real zeros, and per-run rows", async () => {
    server.use(
      http.get("/api/v1/evaluations/summary", () => HttpResponse.json(evalSummary({
        live_runs_evaluated: 3, task_completion_rate: 0.6667, citation_integrity_rate: 0, source_coverage_rate: 1,
        claims_with_source_rate: 0.9, supported_claim_rate: 0.8, mean_confidence: 0.71,
        agent_failures: { research: { runs: 9, failed: 1, failure_rate: 0.1111 } },
        duration_s: { runs: 2, mean: 90, median: 84, max: 96 }, tokens: { runs: 2, total: 20000, mean_per_run: 10000 } }))),
      http.get("/api/v1/evaluations/runs", () => HttpResponse.json({ items: [run()], total: 1, page: 1, page_size: 15 })),
      http.get("/api/v1/evaluations/benchmarks", () => HttpResponse.json([
        { id: 2, suite: "verification", status: "completed", provider: "anthropic", model: "claude-opus-5-5",
          metrics: { accuracy: 0.75, binary_accuracy: 0.9, items: 20 }, cost_usd: 0.04, created_at: new Date().toISOString() }])),
    );
    renderWithProviders(<AppRoutes />, { route: "/evaluation" });
    const card = async (label: string) => (await screen.findByText(label, { selector: "[data-metric] span" })).closest("[data-metric]") as HTMLElement;
    expect(await within(await card("Task completion rate")).findByText("66.7%")).toBeInTheDocument();
    expect(within(await card("Citation integrity")).getByText("0.0%")).toBeInTheDocument(); // a real zero
    expect(within(await card("Execution duration")).getByText("1m 24s")).toBeInTheDocument();
    expect(within(await card("Token usage")).getByText("10K / run")).toBeInTheDocument();
    expect(await screen.findByRole("cell", { name: "11.1%" })).toBeInTheDocument();
    expect(await screen.findByRole("cell", { name: "75.0%" })).toBeInTheDocument();
    const row = document.querySelector("[data-run='r-1']") as HTMLElement;
    expect(within(row).getByRole("link", { name: "Live run one" })).toHaveAttribute("href", "/tasks/r-1");
    expect(within(row).getByText("80.0%")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ Settings */

describe("Settings", () => {
  it("shows provider status without secrets and saves execution defaults used by New Task", async () => {
    server.use(
      http.get("/api/v1/health", () => HttpResponse.json({ status: "ok", database: "ok" })),
      http.get("/api/v1/models", () => HttpResponse.json(providers(false))),
    );
    const { unmount } = renderWithProviders(<AppRoutes />, { route: "/settings" });
    expect(await screen.findByText("Reachable")).toBeInTheDocument();
    const anthropic = document.querySelector("[data-provider=anthropic]") as HTMLElement;
    await waitFor(() => expect(anthropic).toHaveTextContent("Not configured"));
    expect(anthropic).toHaveTextContent("ANTHROPIC_API_KEY");
    expect(screen.getByText("Unavailable — demo only")).toBeInTheDocument();
    expect(document.querySelector("input[type=password]")).toBeNull(); // credentials are never entered here
    for (const name of fixtures.statusDemoOnly.missing_for_live) expect(screen.getAllByText(name).length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("combobox", { name: "Human approval" }));
    await userEvent.click(await screen.findByRole("option", { name: "Approve plan" }));
    await userEvent.click(screen.getByRole("button", { name: /Save defaults/ }));
    expect(JSON.parse(localStorage.getItem("agentflow.execution-preferences.v1")!)).toMatchObject({ approval_mode: "plan" });
    unmount();

    renderWithProviders(<AppRoutes />, { route: "/tasks/new" });
    expect(await screen.findByRole("radio", { name: /Approve plan/ })).toBeChecked();
  });

  it("reports an unreachable API", async () => {
    server.use(http.get("/api/v1/health", () => HttpResponse.json({ error: { code: "x", message: "Database unavailable", request_id: "r" } }, { status: 503 })));
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    expect(await screen.findByText("Database unavailable")).toBeInTheDocument();
  });
});
