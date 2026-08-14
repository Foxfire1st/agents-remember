import { defineConfig, devices } from "@playwright/test";
import { requireDaggerTestEnvironment } from "./scripts/require-dagger-test-environment.mjs";

requireDaggerTestEnvironment();

// L8's explicit performance/fetch gate stays out of the ordinary e2e suite: frame timing must run
// alone, on one worker. `npm run perf:cockpit` also runs the rail and inspector 50/51 + 100/101
// virtualization boundary suites before this browser measurement.
export default defineConfig({
  testDir: "./perf",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "line",
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
  },
});
