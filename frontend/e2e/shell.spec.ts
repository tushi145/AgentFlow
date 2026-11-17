import { expect, type Page, test } from "@playwright/test";

const SHOTS = process.env.SCREENSHOT_DIR;
const USER = process.env.E2E_USER ?? "admin";
const PASS = process.env.E2E_PASS ?? "admin";

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
}

async function login(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Username").fill(USER);
  await page.getByLabel("Password").fill(PASS);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
}

test("shell: login, navigation, real status, themes, no console errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(`${m.text()} @ ${m.location().url}`));
  const unauth: string[] = [];
  page.on("response", (r) => r.status() === 401 && unauth.push(new URL(r.url()).pathname));
  page.on("pageerror", (e) => errors.push(e.message));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.locator("html")).toHaveClass(/dark/); // dark by default
  await shot(page, "01-login-dark");
  await login(page);

  // Status pill reflects the backend's real configuration
  const status = await (await page.request.get("/api/v1/system/status")).json();
  await expect(page.getByText(status.live_mode_available ? "Live AI ready" : "Demo only")).toBeVisible();
  if (!status.live_mode_available) await expect(page.getByText("Live AI execution is not configured.")).toBeVisible();
  await shot(page, "02-shell-dark");

  const nav = page.getByRole("navigation", { name: "Main" });
  for (const label of ["New Task", "Active Executions", "Execution History", "Reports", "Evaluation",
                       "Agent Configuration", "Settings", "Overview"]) {
    await nav.getByRole("link", { name: label }).click();
    await expect(nav.getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
  }

  // Collapse sidebar
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await shot(page, "03-sidebar-collapsed");
  await page.getByRole("button", { name: "Expand sidebar" }).click();

  // Light theme persists across reload
  await page.getByRole("button", { name: "Change theme" }).click();
  await page.getByRole("menuitemradio", { name: "Light" }).click();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await shot(page, "04-shell-light");

  // Responsive: phone width — no horizontal overflow, nav in a sheet
  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await shot(page, "05-mobile");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("dialog").getByRole("link", { name: "Reports" })).toBeVisible();
  await page.waitForTimeout(600); // let the slide-in animation finish before capturing
  await shot(page, "06-mobile-nav");
  await page.getByRole("dialog").getByRole("link", { name: "Reports" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();

  // Tablet width
  await page.setViewportSize({ width: 820, height: 1000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

  // Sign out returns to login
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

  // The only expected 401s are session checks while signed out. Chromium logs each as a console
  // error, so those are excluded; anything else in the console fails the test.
  console.log("401 responses:", unauth);
  expect(unauth.every((p) => p === "/api/v1/auth/me"), unauth.join(",")).toBe(true);
  const unexpected = errors.filter((e) => !/status of 401 .*\/api\/v1\/auth\/me/.test(e));
  expect(unexpected, unexpected.join("\n")).toEqual([]);
});
