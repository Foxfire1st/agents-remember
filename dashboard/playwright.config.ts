import { defineConfig, devices } from "@playwright/test";
import { requireDaggerTestEnvironment } from "./scripts/require-dagger-test-environment.mjs";

requireDaggerTestEnvironment();

// Playwright drives the real app against gallery + sim states (the mc2 red-pen loop
// re-hosted, note 15). Effects are frozen via `?effects=off` (D5) so screenshots and
// visual assertions are deterministic.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
  },
});
