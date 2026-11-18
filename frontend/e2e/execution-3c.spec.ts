import { execSync } from "node:child_process";

import { expect, type Page, test } from "@playwright/test";

/* Phase 3C against the REAL backend (demo mode; no API keys needed). Demo runs finish in about a
   second, so tasks use "Approve plan" to hold them at a real approval checkpoint while we look.

   E2E_BACKEND_RESTART_CMD (optional): a command that restarts the backend on the same database and
   returns once it is healthy. When set, the SSE reconnect/replay test performs a real server restart
   mid-stream; otherwise it approves while connected and verifies live streaming instead. */

const RESTART_CMD = process.env.E2E_BACKEND_RESTART_CMD;

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

async function waitForStatus(page: Page, id: string, statuses: string[]) {
  await expect.poll(async () => (await apiGet<{ status: string }>(page, `/tasks/${id}`)).status, { timeout: 30_000 })
    .toMatch(new RegExp(`^(${statuses.join("|")})$`));
}

async function createViaApi(page: Page, prompt: string, approval_mode: "none" | "plan" | "tools") {
  const r = await page.request.post("/api/v1/tasks", { data: { prompt, mode: "demo", approval_mode } });
  expect(r.status()).toBe(201);
  return ((await r.json()) as { id: string }).id;
}

/** Every event id the server has for a finished task (SSE replay from 0 ends with task_finished). */
async function serverEventIds(page: Page, id: string): Promise<number[]> {
  const r = await page.request.get(`/api/v1/tasks/${id}/events`, { timeout: 15_000 });
  const text = await r.text();
  return [...text.matchAll(/^id: ?(\d+)/gm)].map((m) => Number(m[1]));
}

const uiEventIds = (page: Page) =>
  page.locator("[data-event-id]").evaluateAll((els) => els.map((e) => Number(e.getAttribute("data-event-id"))));

const stage = (page: Page, key: string) => page.locator(`[data-stage=${key}]`);

test.describe.configure({ mode: "serial" });

test("3C live execution: approval, SSE reconnect + replay, outputs", async ({ page, context }) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  let offline = false;
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const url = m.location().url;
    if (m.text().includes("401") && url.endsWith("/api/v1/auth/me")) return;
    // While offline, Chromium logs the failed EventSource/refetch attempts — that is the scenario under test.
    if (offline && (m.text().includes("ERR_INTERNET_DISCONNECTED") || url.includes("/api/v1/"))) return;
    errors.push(`${m.text()} @ ${url}`);
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  const stamp = Date.now().toString(36);

  // --- Create through the UI, open it from Active Executions -------------------------------
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "New Task" }).click();
  await page.getByLabel("Research objective").fill(`Compare vector databases ${stamp} live view`);
  await page.getByRole("radio", { name: /Approve plan/ }).check({ force: true });
  await page.getByRole("button", { name: "Start demo run" }).click();
  await expect(page.getByRole("heading", { name: "Active executions" })).toBeVisible();
  const id = new URL(page.url()).searchParams.get("highlight")!;
  await waitForStatus(page, id, ["awaiting_approval"]);
  await page.locator("[data-slot=card]", { hasText: `${stamp} live view` }).getByRole("link", { name: "Open" }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${id}$`));

  // --- Paused at the plan checkpoint: real state everywhere --------------------------------
  await expect(page.getByRole("heading", { name: new RegExp(`${stamp} live view`) })).toBeVisible();
  await expect(page.getByText("Waiting for your decision")).toBeVisible();
  await expect(page.locator("[data-stream=open]")).toHaveText(/Live/);
  await expect(stage(page, "planner")).toHaveAttribute("data-state", "completed");
  await expect(stage(page, "research")).toHaveAttribute("data-state", "pending");
  await expect(page.locator("[data-event-type=approval_requested]")).toHaveCount(1);
  await expect(page.locator("[data-event-type=paused]")).toHaveCount(1);
  await expect(page.locator("[data-gate]")).toHaveAttribute("data-state", "pending");
  await shot(page, "3c-awaiting-approval");
  await page.getByLabel("Step counters").check(); // `progress` events are hidden by default; show all
  const before = await uiEventIds(page);
  expect(before.length).toBeGreaterThan(5);

  const approval = (await apiGet<{ id: number }[]>(page, `/approvals?status=pending&task_id=${id}`))[0];
  const comment = RESTART_CMD ? "approved while UI offline" : "approved from the API";
  if (RESTART_CMD) {
    // --- Real disconnect: the browser goes offline (so reconnects fail) and the backend restarts,
    //     which drops the open SSE connection. The run is approved and finishes while the UI is
    //     disconnected; on reconnect the missed events must be replayed after the last seen id.
    offline = true;
    await context.setOffline(true); // affects new requests only; the open stream survives until…
    execSync(RESTART_CMD, { stdio: "inherit", timeout: 60_000 }); // …the server goes away.
    // Behind the dev proxy the dropped backend looks like a silent stall; the client's heartbeat
    // watchdog (35 s without even a ping) detects it.
    await expect(page.locator("[data-stream=reconnecting], [data-stream=failed]")).toBeVisible({ timeout: 50_000 });
    await expect(page.locator("[data-stream=reconnecting], [data-stream=failed]")).toContainText(`after #${before.at(-1)}`);
    await shot(page, "3c-reconnecting");
    // APIRequestContext is not subject to the page's offline emulation: the decision reaches the server,
    // which resumes the paused graph from its persisted checkpoint after the restart.
    const decided = await page.request.post(`/api/v1/approvals/${approval.id}/decision`, { data: { approved: true, comment } });
    expect(decided.ok()).toBeTruthy();
    await waitForStatus(page, id, ["completed"]);
    expect((await uiEventIds(page)).length).toBe(before.length); // nothing could arrive while offline
    await context.setOffline(false);
    offline = false;
  } else {
    // No restart hook: approve while connected and watch the remaining events stream in live.
    const decided = await page.request.post(`/api/v1/approvals/${approval.id}/decision`, { data: { approved: true, comment } });
    expect(decided.ok()).toBeTruthy();
  }

  // Replay after the last seen id fills the gap: complete, ordered, no duplicates.
  await expect(page.locator("[data-stream=ended]")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("[data-event-type=task_finished]")).toHaveCount(1);
  const after = await uiEventIds(page);
  expect(new Set(after).size).toBe(after.length);
  expect(after).toEqual([...after].sort((a, b) => a - b));
  expect(after).toEqual(await serverEventIds(page, id));

  // --- Final state: workflow, outcome, report, decided gate ---------------------------------
  await expect(page.locator("[data-outcome=completed]")).toBeVisible();
  for (const k of ["planner", "research", "analysis", "verification", "report"]) {
    await expect(stage(page, k)).toHaveAttribute("data-state", "completed");
  }
  await expect(page.locator("[data-gate]")).toHaveAttribute("data-state", "approved");
  await expect(page.locator("[data-gate]")).toContainText(comment);
  await expect(page.getByText(/in parallel/).first()).toBeVisible();
  await expect(page.getByText("Waiting for your decision")).toBeHidden();
  const md = page.getByTestId("report-card").getByRole("link", { name: /Markdown/ });
  const mdRes = await page.request.get((await md.getAttribute("href"))!);
  expect(mdRes.ok()).toBeTruthy();
  expect(await mdRes.text()).toContain("DEMO");
  const pdfRes = await page.request.get((await page.getByTestId("report-card").getByRole("link", { name: /PDF/ }).getAttribute("href"))!);
  expect(pdfRes.headers()["content-type"]).toContain("application/pdf");
  await shot(page, "3c-completed");

  // --- Sources / verification / outputs / usage tabs match the API ---------------------------
  const sources = await apiGet<{ ref: string }[]>(page, `/tasks/${id}/sources`);
  await expect(page.locator("[data-source-ref]")).toHaveCount(sources.length);
  await expect(page.locator("[data-source-ref] a")).toHaveCount(0); // demo:// is never a link
  await page.getByRole("tab", { name: /Verification/ }).click();
  const verifs = await apiGet<unknown[]>(page, `/tasks/${id}/verifications`);
  await expect(page.locator("[data-verdict]")).toHaveCount(verifs.length);
  await expect(page.getByText(/no real verification was performed/).first()).toBeVisible();
  await shot(page, "3c-verification");
  await page.getByRole("tab", { name: /Outputs/ }).click();
  const detail = await apiGet<{ agent_executions: unknown[] }>(page, `/tasks/${id}`);
  await expect(page.locator("[data-execution-id]")).toHaveCount(detail.agent_executions.length);
  await page.locator("[data-execution-id]").first().getByRole("button").click();
  await expect(page.getByText("Raw output").first()).toBeVisible();
  await page.getByRole("tab", { name: /Usage/ }).click();
  await expect(page.getByRole("cell", { name: "Planner" })).toBeVisible();

  // --- Timeline filters ---------------------------------------------------------------------
  await page.getByRole("button", { name: "Sources", exact: true }).click();
  await expect(page.locator("[data-event-type=source_added]")).toHaveCount(sources.length);
  await expect(page.locator("[data-event-type=agent_started]")).toHaveCount(0);
  await page.getByRole("button", { name: "All", exact: true }).click();

  // --- Reload of a finished task: full history replays, then the stream closes ---------------
  await page.reload();
  await expect(page.locator("[data-stream=ended]")).toBeVisible();
  await page.getByLabel("Step counters").check();
  await expect.poll(() => uiEventIds(page)).toEqual(after);
  expect(errors, errors.join("\n")).toEqual([]);
});

test("3C approve with comment, reject, cancel and responsive layout", async ({ page }) => {
  test.setTimeout(150_000);
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !(m.text().includes("401") && m.location().url.endsWith("/api/v1/auth/me"))) errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  const stamp = Date.now().toString(36);

  // Approve in the page with a comment → streams live to completion (no reload).
  const a = await createViaApi(page, `Assess edge AI chips ${stamp} approve`, "plan");
  await waitForStatus(page, a, ["awaiting_approval"]);
  await page.goto(`/tasks/${a}`);
  await page.getByLabel("Comment (optional)").fill("Ship it");
  await page.getByRole("button", { name: /Approve and continue/ }).click();
  await expect(page.locator("[data-outcome=completed]")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("[data-event-type=approval_decided]")).toContainText("approved");
  const decided = await apiGet<{ comment: string | null; status: string }[]>(page, `/approvals?status=approved&task_id=${a}`);
  expect(decided[0]).toMatchObject({ status: "approved", comment: "Ship it" });

  // Tool approvals: reject the searches with a comment.
  const r = await createViaApi(page, `Review battery supply chains ${stamp} reject`, "tools");
  await waitForStatus(page, r, ["awaiting_approval"]);
  await page.goto(`/tasks/${r}`);
  await expect(page.getByText(/Approve \d+ web search/).first()).toBeVisible();
  await page.getByLabel("Comment (optional)").fill("Queries too broad");
  await page.getByRole("button", { name: /Reject and stop/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Reject" }).click();
  await expect(page.locator("[data-outcome=rejected]")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("[data-outcome=rejected]")).toContainText("Queries too broad");
  await expect(stage(page, "research")).toHaveAttribute("data-state", "not_run");
  await expect(stage(page, "report")).toHaveAttribute("data-state", "not_run");
  await shot(page, "3c-rejected");

  // Cancel from the page while paused.
  const c = await createViaApi(page, `Survey robotics startups ${stamp} cancel`, "plan");
  await waitForStatus(page, c, ["awaiting_approval"]);
  await page.goto(`/tasks/${c}`);
  await page.getByRole("button", { name: /Cancel execution/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel execution" }).click();
  await expect(page.locator("[data-outcome=cancelled]")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("[data-gate]")).toHaveAttribute("data-state", "expired");
  await expect(page.locator("[data-stream=ended]")).toBeVisible();
  await expect(page.getByRole("button", { name: /Re-run/ })).toBeVisible();

  // Not found.
  await page.goto("/tasks/does-not-exist");
  await expect(page.getByText("Execution not found")).toBeVisible();
  errors.splice(0, errors.length, ...errors.filter((e) => !e.includes("404"))); // the expected 404

  // Responsive: phone width, both themes, no horizontal overflow.
  await page.setViewportSize({ width: 390, height: 844 });
  for (const id of [a, r]) {
    await page.goto(`/tasks/${id}`);
    await expect(page.locator("[data-stream=ended]")).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on /tasks/${id}`).toBeLessThanOrEqual(0);
  }
  await shot(page, "3c-mobile-rejected");
  await page.goto(`/tasks/${a}`);
  await expect(page.locator("[data-stream=ended]")).toBeVisible();
  await shot(page, "3c-mobile-completed");
  expect(errors, errors.join("\n")).toEqual([]);
});
