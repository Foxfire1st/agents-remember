import { defineConfig, devices } from "@playwright/test";

// 260718-CHATS-L5F R7 (FB5): the durable, opt-in Chats end-to-end suite. Unlike e2e-production
// (route-mocked bundle) this drives the REAL composed app against the REAL installed harnesses —
// it boots an isolated dashboard daemon from THIS worktree on a free port (the developer's :8871
// is off-limits), builds+syncs the current bundle, and drives codex / claude / pi through open →
// submit → full turn → set-model/effort with ACCEPTANCE VALIDATION → interrupt → End. It is the
// suite that would have caught the codex unknown-vendor flood, the claude frame flood, and the
// opus[1m] refused pair before the developer did (FB5 doctrine: real assertions, not smoke tests).
//
// OPT-IN: set AR_RUN_CHATS_E2E=1 to run (never part of the default gate — it spawns real harnesses).
// Optional env: AR_CHATS_E2E_PYTHON (python with the agents-remember mcp package installed;
// default "python3"), AR_CHATS_E2E_HARNESSES (comma list, default "codex,claude,pi"),
// AR_CHATS_E2E_SKIP_BUILD=1 to reuse the already-synced package_data bundle.

export default defineConfig({
  testDir: "./e2e-chats",
  testMatch: /.*\.chats\.spec\.ts/,
  // Real harness turns are slow; keep it serial and generous.
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 30_000 },
  forbidOnly: !!process.env.CI,
  globalSetup: "./e2e-chats/support/global-setup.ts",
  globalTeardown: "./e2e-chats/support/global-teardown.ts",
  // When AR_RUN_CHATS_E2E is unset every spec skips (see support/gate.ts), so the run is a green
  // "all skipped" no-op — the daemon never boots and no real harness is spawned.
  use: {
    // baseURL is filled in by global-setup via the AR_CHATS_E2E_BASE_URL it exports.
    baseURL: process.env.AR_CHATS_E2E_BASE_URL ?? "http://127.0.0.1:8781",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
