import { expect, test } from "@playwright/test";

// MIK-R112 F06 retirement census (architect ruling L112-F06-A-VIEW-WITHOUT-AN-ENTRY,
// point 1a + point 4). REWRITTEN from the engine-view spec: the engine view has no
// dashboard entry and no URL (Cockpit.tsx: retained for injected initialView tests
// only), so no spec can mount it — a test that cannot pass stays out. This spec
// censuses every entry a user can reach instead: none is the engine view, and no
// page reached shows a state of the two providers.
// Bench timelines carry no in-page API authority, so the shell-boot reads are stubbed
// exactly as the cockpit scenarios serve them (harness list + empty sessions).
// Run (Dagger suite): cd dashboard && npx playwright test e2e/engine-room-retirement.spec.ts
// First-failure rails: every assertion fails fast with a snapshot; fix selectors
// against the live render before any re-run (no blind retry loop).

const HARNESSES = {
  harnesses: [
    { id: "claude", name: "Claude", detected: true },
    { id: "codex", name: "Codex", detected: true },
    { id: "pi", name: "Pi", detected: true },
  ],
};

const PROVIDER_TESTIDS = ["engine-gauge", "official-wire", "provider-block", "engine-dropout"];
const PROVIDER_TEXT = ["codegraphcontext-code", "grepai-memory"];

async function stubShellBoot(page: import("@playwright/test").Page) {
  await page.route("**/api/harnesses", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(HARNESSES) })
  );
  await page.route("**/api/terminal/sessions", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ sessions: [] }) })
  );
}

async function expectNoEngineEntry(page: import("@playwright/test").Page) {
  // No button or link leading to the engine view (case-insensitive; fails with a snapshot).
  const engineButtons = page.getByRole("button", { name: /engine/i });
  await expect(engineButtons).toHaveCount(0);
  const engineLinks = page.getByRole("link", { name: /engine/i });
  await expect(engineLinks).toHaveCount(0);
}

async function expectNoProviderState(page: import("@playwright/test").Page) {
  for (const id of PROVIDER_TESTIDS) {
    await expect(page.getByTestId(id)).toHaveCount(0);
  }
  for (const text of PROVIDER_TEXT) {
    await expect(page.getByText(text, { exact: false })).toHaveCount(0);
  }
}

test("cockpit root: reachable entries, no engine view, no provider state", async ({ page }) => {
  await stubShellBoot(page);
  await page.goto("/");
  await expect(page.locator("nav, [role='navigation']").first()).toBeVisible({ timeout: 30_000 });
  await expectNoEngineEntry(page);
  await expectNoProviderState(page);
});

test("dev harness index: reachable entries, no engine view, no provider state", async ({ page }) => {
  await stubShellBoot(page);
  await page.goto("/dev/");
  await expect(page.getByRole("link", { name: /bench/i }).first()).toBeVisible({ timeout: 30_000 });
  await expectNoEngineEntry(page);
  await expectNoProviderState(page);
});

const TIMELINES = [
  { state: "normal", scenario: "build-up", caption: "B5 · powered" },
  { state: "degraded", scenario: "provider-block", caption: "P4 · BLOCK" },
  { state: "teardown", scenario: "tear-down", caption: "D6 · stack removed" },
] as const;

for (const { state, scenario, caption } of TIMELINES) {
  test(`bench ${scenario} (${state}): settled timeline shows no engine entry and no provider state`, async ({
    page,
  }) => {
    await stubShellBoot(page);
    await page.goto(`/dev/bench?scenario=${scenario}&effects=off`);
    await expect(page.locator("#bench-scenario")).toBeVisible({ timeout: 30_000 });
    // Timelines do not autoplay: press play, then settle on the end caption.
    // The player also shows a pause glyph (▶▌): match the play button exactly.
    // Player controls render after the scenario loads: wait for play explicitly.
    const play = page.getByRole("button", { name: "▶", exact: true });
    await play.first().waitFor({ timeout: 60_000 });
    await play.first().click();
    await expect(page.getByText(caption, { exact: false }).first()).toBeVisible({ timeout: 120_000 });
    await expectNoEngineEntry(page);
    await expectNoProviderState(page);
  });
}

test("bench calm resting state: no engine entry, no provider state", async ({ page }) => {
  await stubShellBoot(page);
  await page.goto("/dev/bench?state=calm&effects=off");
  await expect(page.locator("#bench-scenario")).toBeVisible({ timeout: 30_000 });
  await expectNoEngineEntry(page);
  await expectNoProviderState(page);
});

const DEV_PAGES = ["/dev/reference", "/dev/pty-bench", "/dev/sprint-graph", "/dev/flows"] as const;

for (const url of DEV_PAGES) {
  test(`dev page ${url}: no engine entry, no provider state`, async ({ page }) => {
    await stubShellBoot(page);
    await page.goto(url);
    await expect(page.locator("body")).not.toBeEmpty({ timeout: 30_000 });
    await expectNoEngineEntry(page);
    await expectNoProviderState(page);
  });
}
