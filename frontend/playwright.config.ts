import { defineConfig, devices } from "@playwright/test";

/* E2E tests run against a real backend + Vite dev server (see README):
     backend:  uvicorn app.main:app --port 8000      frontend: npm run dev            */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
