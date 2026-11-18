import { expect, type Page, test } from "@playwright/test";

/* Phase 3D against the REAL backend (demo mode; no API keys needed): Reports, Agent configuration,
   Evaluation and Settings. Agent changes made here are restored at the end of the test. */

const SHOTS = process.env.SCREENSHOT_DIR;
const shot = async (page: Page, name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(process.env.E2E_USER ?? "admin");
  await page.getByLabel("Password").fill(process.env.E2E_PASS ?? "admin");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
}

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const r = await page.request.get(`/api/v1${path}`);
  expect(r.ok(), `GET ${path} → ${r.status()}`).toBeTruthy();
  return r.json() as Promise<T>;
}

const nav = (page: Page, name: string) => page.getByRole("navigation", { name: "Main" }).getByRole("link", { name }).click();

test("3D pages against the real backend", async ({ page }) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    if (m.text().includes("401") && m.location().url.endsWith("/api/v1/auth/me")) return;
    errors.push(`${m.text()} @ ${m.location().url}`);
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  const stamp = Date.now().toString(36);

  // A real demo run to produce a report.
  const created = await page.request.post("/api/v1/tasks", { data: { prompt: `Compare message queues ${stamp} for event-driven systems`, mode: "demo" } });
  expect(created.status()).toBe(201);
  const taskId = ((await created.json()) as { id: string }).id;
  await expect.poll(async () => (await apiGet<{ status: string }>(page, `/tasks/${taskId}`)).status, { timeout: 30_000 }).toBe("completed");
  const reportId = (await apiGet<{ report_id: number }>(page, `/tasks/${taskId}`)).report_id;
  const report = await apiGet<{ title: string; sources: { ref: string }[]; verifications: unknown[] }>(page, `/reports/${reportId}`);

  // --- Reports: search + open ---------------------------------------------------------------
  await nav(page, "Reports");
  await expect(page.getByRole("heading", { name: "Reports", exact: true })).toBeVisible();
  await page.getByLabel("Search reports").fill(stamp);
  const card = page.getByRole("link", { name: report.title });
  await expect(card).toBeVisible();
  await expect(page.getByRole("list", { name: "Reports" }).getByRole("listitem")).toHaveCount(1);
  await shot(page, "3d-reports");
  await page.getByLabel("Search reports").fill(`${stamp}-no-such-report`);
  await expect(page.getByText("No matching reports")).toBeVisible();
  await page.getByLabel("Search reports").fill(stamp);
  await card.click();
  await expect(page).toHaveURL(new RegExp(`/reports/${reportId}$`));

  // --- Report viewer ------------------------------------------------------------------------
  const article = page.getByTestId("report-markdown");
  await expect(article.getByRole("heading", { level: 1 })).toHaveText(report.title);
  await expect(page.getByText(/Demo report: produced from fixed templates/)).toBeVisible();
  await expect(article.locator("table").first()).toBeVisible(); // GFM tables rendered
  expect(await article.locator("script, img, iframe").count()).toBe(0);
  expect(await article.locator('a[href^="demo:"]').count()).toBe(0); // synthetic sources are never links
  const citations = article.locator("a[data-citation]");
  expect(await citations.count()).toBeGreaterThan(0);
  const ref = (await citations.first().getAttribute("data-citation"))!;
  await citations.first().click();
  await expect(page.locator(`#source-${ref}`)).toBeInViewport();
  await expect(page.locator("[data-source-ref]")).toHaveCount(report.sources.length);
  await expect(page.locator("#report-verification [data-verdict]")).toHaveCount(report.verifications.length);
  const toc = page.getByRole("navigation", { name: "Report contents" });
  await toc.getByRole("link", { name: "Executive summary" }).click();
  await expect(article.getByRole("heading", { name: "Executive summary" })).toBeInViewport();
  const md = await page.request.get((await page.getByRole("link", { name: /Markdown/ }).getAttribute("href"))!);
  expect(md.ok() && (await md.text()).startsWith(`# ${report.title}`)).toBeTruthy();
  const pdf = await page.request.get((await page.getByRole("link", { name: /PDF/ }).getAttribute("href"))!);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  await page.getByRole("link", { name: "Execution", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}$`));
  await expect(page.getByTestId("report-card")).toBeVisible(); // 3C page still works
  await page.goto(`/reports/${reportId}`);
  await expect(article).toBeVisible();
  await shot(page, "3d-report-viewer");

  // --- Agent configuration: validate, save through the API, restore ------------------------
  const agentsBefore = await apiGet<{ key: string; max_output_tokens: number; instructions: string }[]>(page, "/agents");
  const research = agentsBefore.find((a) => a.key === "research")!;
  await nav(page, "Agent Configuration");
  await page.getByRole("tab", { name: /^Research/ }).click();
  const form = page.locator("[data-agent-form=research]");
  await expect(form.getByRole("switch")).toBeDisabled(); // required agent
  await expect(page.getByText(/No LLM provider is configured \(set /)).toBeVisible();
  const tokens = form.getByLabel("Max output tokens");
  await tokens.fill("100");
  await expect(form.getByText(/between 256 and 64,000/)).toBeVisible();
  await expect(form.getByRole("button", { name: /Save changes/ })).toBeDisabled();
  await tokens.fill("12345");
  await form.getByLabel("Additional instructions").fill(`E2E ${stamp}: prefer primary sources.`);
  await form.getByRole("button", { name: /Save changes/ }).click();
  await expect(page.getByText("Research Agent saved")).toBeVisible();
  const saved = (await apiGet<{ key: string; max_output_tokens: number; instructions: string }[]>(page, "/agents")).find((a) => a.key === "research")!;
  expect(saved).toMatchObject({ max_output_tokens: 12345, instructions: `E2E ${stamp}: prefer primary sources.` });
  await page.reload();
  await page.getByRole("tab", { name: /^Research/ }).click();
  await expect(page.locator("[data-agent-form=research]").getByLabel("Max output tokens")).toHaveValue("12345");
  await shot(page, "3d-agents");
  const restore = await page.request.put("/api/v1/agents/research", { data: { max_output_tokens: research.max_output_tokens, instructions: research.instructions } });
  expect(restore.ok()).toBeTruthy();
  // Disabling an optional agent is visible to New Task; restore afterwards.
  await page.reload();
  await page.getByRole("tab", { name: /^Analysis/ }).click();
  const analysis = page.locator("[data-agent-form=analysis]");
  await expect(analysis.getByRole("switch")).toBeEnabled();

  // --- Evaluation ---------------------------------------------------------------------------
  const summary = await apiGet<{ live_runs_evaluated: number }>(page, "/evaluations/summary");
  await nav(page, "Evaluation");
  await expect(page.getByRole("heading", { name: "Evaluation" })).toBeVisible();
  if (summary.live_runs_evaluated === 0) {
    await expect(page.locator("[data-no-data]")).toHaveCount(6);
    await expect(page.getByText(/No live runs have been evaluated yet/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Run benchmark/ })).toBeDisabled();
  }
  await page.getByRole("tab", { name: "Demo" }).click();
  await expect(page.locator(`[data-run="${taskId}"]`)).toBeVisible();
  await expect(page.getByText(/Demo rows measure template output/)).toBeVisible();
  await shot(page, "3d-evaluation");

  // --- Settings: health, providers (no secrets), execution defaults used by New Task --------
  await nav(page, "Settings");
  await expect(page.locator("[data-health=ok]")).toBeVisible();
  const status = await apiGet<{ llm_providers: { name: string; configured: boolean }[]; search_provider: { configured: boolean } }>(page, "/system/status");
  for (const p of status.llm_providers) {
    await expect(page.locator(`[data-provider=${p.name}]`)).toContainText(p.configured ? "Configured" : "Not configured");
  }
  expect(await page.locator("input[type=password]").count()).toBe(0);
  const html = await page.content();
  expect(html).not.toMatch(/sk-ant-|tvly-[A-Za-z0-9]/);
  await page.getByRole("combobox", { name: "Human approval" }).click();
  await page.getByRole("option", { name: "Approve plan", exact: true }).click();
  await page.getByRole("button", { name: /Save defaults/ }).click();
  await expect(page.getByText("Execution defaults saved")).toBeVisible();
  await shot(page, "3d-settings");
  await nav(page, "New Task");
  await expect(page.getByRole("radio", { name: /Approve plan/ })).toBeChecked();
  await nav(page, "Settings");
  await page.getByRole("button", { name: "Reset to defaults" }).click();
  await page.getByRole("button", { name: /Save defaults/ }).click();
  await nav(page, "New Task");
  await expect(page.getByRole("radio", { name: /Autonomous/ })).toBeChecked();

  // --- Responsive: every 3D page at phone width, both themes -------------------------------
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [path, name] of [["/reports", "reports"], [`/reports/${reportId}`, "report"], ["/agents", "agents"], ["/evaluation", "evaluation"], ["/settings", "settings"]] as const) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0);
    await shot(page, `3d-mobile-${name}`);
  }
  await page.getByRole("radio", { name: "Light" }).click();
  await page.goto(`/reports/${reportId}`);
  await expect(page.getByTestId("report-markdown")).toBeVisible();
  await shot(page, "3d-mobile-report-light");
  await page.goto("/settings");
  await page.getByRole("radio", { name: "Dark" }).click();
  expect(errors, errors.join("\n")).toEqual([]);
});
