// Witness runner for the MIK-R121 sandbox spec: real sandbox dashboard, no dev server,
// no Dagger gate (the ruling admits scripted Chromium as the evidence method, not a
// certification run). Env selects the sandbox and the expected arm:
//
//   AR_WITNESS_DASH=http://127.0.0.1:9871 AR_WITNESS_PASEO=http://127.0.0.1:6871 \
//   AR_WITNESS_EXPECT=pass AR_WITNESS_EVIDENCE=<abs dir> \
//   AR_WITNESS_IDENTITY='{...}' \
//   node_modules/.bin/playwright test --config playwright.l121witness.config.ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "document-chat-sandbox-witness.spec.ts",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  use: {
    trace: "off",
    screenshot: "off",
    video: "off",
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
  projects: [
    { name: "wide", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } },
    { name: "narrow", use: { ...devices["Desktop Chrome"], viewport: { width: 380, height: 800 } } },
  ],
});
