import { expect, type Page, test } from "@playwright/test";

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

async function api<T>(page: Page, path: string): Promise<T> {
  const r = await page.request.get(`/api/v1${path}`);
  expect(r.ok()).toBeTruthy();
  return r.json() as Promise<T>;
}

async function waitForStatus(page: Page, id: string, statuses: string[]) {
  await expect.poll(async () => (await api<{ status: string }>(page, `/tasks/${id}`)).status, { timeout: 30_000 })
    .toMatch(new RegExp(`^(${statuses.join("|")})$`));
}

async function createTask(page: Page, prompt: string, approval: "Autonomous" | "Approve plan") {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "New Task" }).click();
  await page.getByLabel("Research objective").fill(prompt);
  await page.getByRole("radio", { name: new RegExp(approval) }).check({ force: true });
  await page.getByRole("button", { name: "Start demo run" }).click();
  await expect(page.getByRole("heading", { name: "Active executions" })).toBeVisible();
  return new URL(page.url()).searchParams.get("highlight")!;
}

test("3B pages against the real backend", async ({ page }) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  // Chromium logs every 4xx as a console error; the only expected one is the signed-out session check.
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    if (m.text().includes("401") && m.location().url.endsWith("/api/v1/auth/me")) return;
    errors.push(`${m.text()} @ ${m.location().url}`);
  });
  page.on("pageerror", (e) => errors.push(e.message));
  const unauthorized: string[] = [];
  let signedIn = false;
  page.on("response", (r) => { if (r.status() === 401) unauthorized.push(`${signedIn ? "AFTER" : "BEFORE"} login: ${new URL(r.url()).pathname}`); });
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  signedIn = true;
  const stamp = Date.now().toString(36);

  // --- New Task: live is blocked without keys; demo is labelled -------------------------
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "New Task" }).click();
  const status = await api<{ live_mode_available: boolean }>(page, "/system/status");
  if (!status.live_mode_available) {
    await expect(page.getByRole("radio", { name: /Live AI execution/ })).toBeDisabled();
    await expect(page.getByRole("radio", { name: /Demo/ })).toBeChecked();
  }
  await page.getByRole("button", { name: /Limits/ }).click();
  await shot(page, "3b-new-task");

  // --- Plan-approval task: appears in Active with an approval card; approve it -------------
  const t1 = await createTask(page, `Compare vector databases ${stamp} alpha for production`, "Approve plan");
  await waitForStatus(page, t1, ["awaiting_approval"]);
  const approvalCard = page.locator("[data-slot=card]", { hasText: `${stamp} alpha` }).filter({ hasText: "Plan review" });
  await expect(approvalCard).toBeVisible();
  await expect(approvalCard).toContainText("just now"); // timestamps are UTC-correct in any browser timezone
  await expect(page.getByText(/steps ·/).first()).toBeVisible();
  await shot(page, "3b-active-approval");
  await approvalCard.getByRole("button", { name: /Approve/ }).click();
  await waitForStatus(page, t1, ["completed"]);
  await expect(approvalCard).toBeHidden();

  // --- Autonomous task + a task cancelled from the Active page -----------------------------
  const t2 = await createTask(page, `Summarise four-day work week evidence ${stamp} beta`, "Autonomous");
  await waitForStatus(page, t2, ["completed"]);
  const t3 = await createTask(page, `Evaluate AWS deployment options ${stamp} gamma`, "Approve plan");
  await waitForStatus(page, t3, ["awaiting_approval"]);
  const row = page.locator("[data-slot=card]", { hasText: `${stamp} gamma` }).filter({ has: page.getByRole("button", { name: /Cancel/ }) });
  await row.getByRole("button", { name: /Cancel/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel execution" }).click();
  await waitForStatus(page, t3, ["cancelled"]);

  // --- History: search, filter, re-run, delete -----------------------------------------------
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Execution History" }).click();
  await page.getByLabel("Search executions").fill(stamp);
  await expect(page.getByRole("row")).toHaveCount(4); // header + 3 tasks
  await page.getByLabel("Search executions").fill(`${stamp} beta`);
  await expect(page.getByRole("row")).toHaveCount(2);
  await page.getByLabel("Search executions").fill(stamp);
  await page.getByLabel("Filter by status").click();
  await page.getByRole("option", { name: "Cancelled" }).click();
  await expect(page.getByRole("row")).toHaveCount(2);
  await expect(page.getByRole("row").nth(1)).toContainText("gamma");
  await shot(page, "3b-history-filtered");

  // re-run the cancelled task → becomes a new task
  await page.getByRole("button", { name: /Actions for Evaluate AWS/ }).click();
  await page.getByRole("menuitem", { name: /Re-run/ }).click();
  await expect(page.getByRole("heading", { name: "Active executions" })).toBeVisible();
  const rerun = new URL(page.url()).searchParams.get("highlight")!;
  expect(rerun).not.toBe(t3);
  await waitForStatus(page, rerun, ["awaiting_approval"]);
  await page.request.post(`/api/v1/tasks/${rerun}/cancel`);

  // delete the cancelled original
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Execution History" }).click();
  await page.getByLabel("Search executions").fill(`${stamp} beta`);
  await expect(page.getByRole("row")).toHaveCount(2);
  await page.getByRole("button", { name: /Actions for Summarise/ }).click();
  await page.getByRole("menuitem", { name: /Delete/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("No matching executions")).toBeVisible();
  expect((await page.request.get(`/api/v1/tasks/${t2}`)).status()).toBe(404);

  // --- Overview numbers equal the API's ------------------------------------------------------
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Overview" }).click();
  const ov = await api<{ totals: { tasks: number; completed: number } }>(page, "/analytics/overview");
  const stat = (label: string) => page.locator("[data-slot=card]", { has: page.locator(`[data-stat-label]:text-is("${label}")`) });
  await expect(stat("Total executions")).toContainText(String(ov.totals.tasks));
  await expect(stat("Completed")).toContainText(String(ov.totals.completed));
  await expect(page.getByRole("img", { name: "Executions per day by outcome" })).toBeVisible();
  await shot(page, "3b-overview-dark");
  await page.getByRole("button", { name: "Change theme" }).click();
  await page.getByRole("menuitemradio", { name: "Light" }).click();
  await shot(page, "3b-overview-light");

  // --- Responsive: every 3B page at phone width, no horizontal overflow ---------------------
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ["/", "/tasks/new", "/executions", "/history"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0);
    await shot(page, `3b-mobile-${path === "/" ? "overview" : path.slice(1).replace("/", "-")}`);
  }
  expect(errors, errors.join("\n")).toEqual([]);
});
