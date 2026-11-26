import { expect, test } from "@playwright/test";

// MIK-R112 F06 retirement census (architect ruling L112-F06-A-VIEW-WITHOUT-AN-ENTRY,
// point 1a + point 4): the engine view has no dashboard entry and no
// URL (Cockpit.tsx: retained for injected initialView tests only), so no spec can mount it —
// a test that cannot pass stays out. This spec censuses every entry a user can reach instead:
// none is the engine view.
// Run (Dagger suite): cd dashboard && npx playwright test e2e/engine-room-retirement.spec.ts
// First-failure rails: every assertion fails fast with a snapshot; fix selectors
// against the live render before any re-run (no blind retry loop).

async function expectNoEngineEntry(page: import("@playwright/test").Page) {
  // No button or link leading to the engine view (case-insensitive; fails with a snapshot).
  const engineButtons = page.getByRole("button", { name: /engine/i });
  await expect(engineButtons).toHaveCount(0);
  const engineLinks = page.getByRole("link", { name: /engine/i });
  await expect(engineLinks).toHaveCount(0);
}

test("cockpit root: reachable entries, no engine view", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("nav, [role='navigation']").first()).toBeVisible({ timeout: 30_000 });
  await expectNoEngineEntry(page);
});

test("dev harness index: reachable entries, no engine view", async ({ page }) => {
  await page.goto("/dev/");
  await expect(page.getByRole("link", { name: /bench/i }).first()).toBeVisible({ timeout: 30_000 });
  await expectNoEngineEntry(page);
});

// The degraded beat is the memory/ledger block: it is the pre-contract failure mode.
const TIMELINES = [
  { state: "normal", scenario: "build-up", caption: "B5 · powered" },
  { state: "degraded", scenario: "memory-block", caption: "M3 · BLOCK" },
  { state: "teardown", scenario: "tear-down", caption: "D6 · stack removed" },
] as const;

for (const { state, scenario, caption } of TIMELINES) {
  test(`bench ${scenario} (${state}): settled timeline shows no engine entry`, async ({
    page,
  }) => {
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
  });
}

test("bench calm resting state: no engine entry", async ({ page }) => {
  await page.goto("/dev/bench?state=calm&effects=off");
  await expect(page.locator("#bench-scenario")).toBeVisible({ timeout: 30_000 });
  await expectNoEngineEntry(page);
});

const DEV_PAGES = ["/dev/reference", "/dev/sprint-graph"] as const;

for (const url of DEV_PAGES) {
  test(`dev page ${url}: no engine entry`, async ({ page }) => {
    await page.goto(url);
    await expect(page.locator("body")).not.toBeEmpty({ timeout: 30_000 });
    await expectNoEngineEntry(page);
  });
}
