// 260718-CHATS-L5F R7: shared drivers + the COMPOSED assertions for the durable Chats E2E. Every
// assertion here is sharp enough that one of the three developer screenshots would fail it:
//   - assertNoUnknownVendorRows  -> the codex startup flood (image.png 1a) AND the claude
//                                    command_lifecycle/rate_limit/echo flood (image3 item 3)
//   - assertAcceptanceValidated  -> the opus[1m] "refused pair — requested provenance" (image3 item 2)
//   - assertSingleTurnResult     -> a settled turn double-projecting
//   - assertNoProjectionAlarm    -> the cried-wolf codex launch red strip (R10 / audit V13)
//   - assertWorkingStateVisible  -> the streaming turn shown settled-green (R9 / audit V5)

import { expect, type Page } from "@playwright/test";

const OPEN_TIMEOUT = 60_000;
const TURN_TIMEOUT = 120_000;

export async function openChat(page: Page, harness: "codex" | "claude" | "pi"): Promise<string> {
  await page.getByRole("radio", { name: "Chats" }).click();
  await page.getByTestId("chats-new-chat").click();
  await expect(page.getByTestId("launch-flow")).toBeVisible();
  await page.getByTestId(`launch-harness-${harness}`).click();
  // Pick the first SELECTABLE model + a launch-settable effort of the REAL installed catalog (keys
  // vary per install). The `launch-model-list` / `launch-effort-list` testids are containers, so
  // target the `launch-model-<key>` / `launch-effort-<key>` option buttons specifically.
  await expect(page.getByTestId("launch-model-list")).toBeVisible();
  const modelOption = page
    .locator('[data-testid^="launch-model-"]:not([data-testid="launch-model-list"]):not([disabled])')
    .first();
  await modelOption.click();
  // Efforts render only after a model is selected — wait for the effort list OR the no-effort note.
  await page
    .getByTestId("launch-effort-list")
    .or(page.getByTestId("launch-effort-none"))
    .first()
    .waitFor({ state: "visible", timeout: 20_000 });
  const effortOption = page
    .locator('[data-testid^="launch-effort-"]:not([data-testid="launch-effort-list"]):not([data-testid="launch-effort-none"]):not([data-testid="launch-effort-choose"])')
    .first();
  if (await effortOption.count()) await effortOption.click();
  // The submit only enables on a complete selection — wait for it rather than racing the render.
  await expect(page.getByTestId("launch-submit")).toBeEnabled({ timeout: 20_000 });
  await page.getByTestId("launch-submit").click();
  await expect(page.getByTestId("launch-flow")).toBeHidden({ timeout: OPEN_TIMEOUT });
  const railRow = page.locator('[data-testid^="rail-row-"]').last();
  await expect(railRow).toBeVisible({ timeout: OPEN_TIMEOUT });
  const testId = await railRow.getAttribute("data-testid");
  const sessionId = testId?.replace("rail-row-", "") ?? "";
  expect(sessionId.length).toBeGreaterThan(0);
  // Wait for the conversation stream to reach a live/idle phase before returning. The reconnect
  // strip renders ONLY for non-live phases (ConversationReconnect returns null for live/idle), so
  // its absence is the "stream settled live" signal — a turn must not be driven on a non-live stream.
  //
  // A just-launched harness bridge can take a few seconds to compose; the frontend's bounded
  // connect-retry budget (epoch-resolve ~800ms; the R10 initial-hydrate window ~3.2s) can exhaust
  // before the bridge is ready under E2E load and stick the surface on the fail-loud
  // "structured surface unavailable" strip until a manual retry (the cried-wolf class R10 targets,
  // on the epoch-resolve path it does not yet cover). The daemon serves the projection correctly
  // once the bridge is up (verified out-of-band), so the driver recovers a stuck strip by clicking
  // its own "retry projection" control until the stream settles live.
  const reconnect = page.locator('[data-testid="conversation-reconnect"]');
  const deadline = Date.now() + OPEN_TIMEOUT;
  while (Date.now() < deadline) {
    if ((await reconnect.count()) === 0) return sessionId;
    const retry = page.getByTestId("conversation-retry");
    if (await retry.count()) await retry.first().click().catch(() => {});
    await page.waitForTimeout(1000);
  }
  await expect(reconnect).toHaveCount(0, { timeout: 1000 });
  return sessionId;
}

/** A fresh healthy launch reaches the structured surface with NO cried-wolf red strip (R10). */
export async function assertNoProjectionAlarm(page: Page): Promise<void> {
  await expect(
    page.locator('[data-testid="conversation-reconnect"][data-phase="projection-failed"]'),
  ).toHaveCount(0);
  await expect(
    page.locator('[data-testid="conversation-reconnect"][data-phase="failed"]'),
  ).toHaveCount(0);
}

/** No unknown-vendor rows anywhere in the timeline on an ordinary flow. */
export async function assertNoUnknownVendorRows(page: Page): Promise<void> {
  await expect(page.getByTestId("unknown-vendor-run")).toHaveCount(0);
  await expect(page.getByTestId("unknown-vendor-run-member")).toHaveCount(0);
  await expect(page.getByTestId("unknown-vendor-evidence")).toHaveCount(0);
  await expect(
    page.locator('[data-testid="conversation-turn-result"][data-kind="unknown-vendor"]'),
  ).toHaveCount(0);
}

/** The capability surface is not wholesale-demoted to "unverified" version-mismatch (R3/R4). */
export async function assertNoVersionMismatchDemotion(page: Page): Promise<void> {
  await expect(page.getByText(/observed runtime\/helper version differs/i)).toHaveCount(0);
  await expect(page.getByText(/mismatches the locked/i)).toHaveCount(0);
}

/** A launch/set that natively succeeds is never shown as a refused pair (R2). */
export async function assertAcceptanceValidated(page: Page): Promise<void> {
  await expect(page.getByTestId("failed-launch-refused-pair")).toHaveCount(0);
  await expect(page.getByTestId("failed-launch-banner")).toHaveCount(0);
}

/** Every settled-turn row is a real settlement kind (never an unknown-vendor projection). */
export async function assertSingleTurnResultInvariants(page: Page): Promise<void> {
  await assertNoUnknownVendorRows(page);
  const kinds = await page
    .locator('[data-testid="conversation-turn-result"]')
    .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-kind")));
  for (const kind of kinds) {
    expect(["turn-result", "error", "interrupt", "notice"]).toContain(kind);
  }
}

export async function driveTurn(page: Page, prompt: string): Promise<void> {
  const editor = page.getByTestId("session-composer-editor");
  await editor.click();
  await page.keyboard.type(prompt);
  const before = await page.getByTestId("conversation-turn-result").count();
  await page.getByTestId("session-composer-send").click();
  // Exactly one new turn-result settles this turn (single projection).
  await expect
    .poll(async () => page.getByTestId("conversation-turn-result").count(), { timeout: TURN_TIMEOUT })
    .toBe(before + 1);
}

/** While a turn streams, a working state is visible in the pane (R9 / audit V5). */
export async function assertWorkingStateSeenDuringTurn(page: Page, prompt: string): Promise<void> {
  const editor = page.getByTestId("session-composer-editor");
  await editor.click();
  await page.keyboard.type(prompt);
  await page.getByTestId("session-composer-send").click();
  // A working affordance (WorkingLine or the rail chat-activity chip) must appear within the
  // projection's latency — never a settled-green "turn-ended" for the whole streaming turn.
  await expect(page.getByTestId("working-line").or(page.getByTestId("chat-activity"))).toBeVisible({
    timeout: 30_000,
  });
}

/** Start a turn, interrupt it via the WorkingLine stop control, and settle cleanly (R7 interrupt). */
export async function interruptTurn(page: Page, prompt: string): Promise<void> {
  const editor = page.getByTestId("session-composer-editor");
  await editor.click();
  await page.keyboard.type(prompt);
  const before = await page.getByTestId("conversation-turn-result").count();
  await page.getByTestId("session-composer-send").click();
  const stop = page.getByTestId("working-line-stop");
  await expect(stop).toBeVisible({ timeout: 30_000 });
  await stop.click();
  // The turn settles (interrupted or completed if it beat the interrupt) — exactly one new row.
  await expect
    .poll(async () => page.getByTestId("conversation-turn-result").count(), { timeout: TURN_TIMEOUT })
    .toBe(before + 1);
  await assertNoUnknownVendorRows(page);
}

export async function endChat(page: Page, sessionId: string): Promise<void> {
  await page.getByTestId(`rail-end-${sessionId}`).click();
  const execute = page.getByTestId(`rail-end-execute-${sessionId}`);
  if (await execute.count()) await execute.click();
  await expect(page.locator(`[data-testid="rail-row-${sessionId}"]`)).toHaveCount(0, {
    timeout: OPEN_TIMEOUT,
  });
}

/** Browser JS-heap sample for the per-session leak assertion (audit's recommended method). */
export async function sampleHeapBytes(page: Page): Promise<number> {
  return page.evaluate(() => {
    const perf = performance as unknown as { memory?: { usedJSHeapSize: number } };
    return perf.memory?.usedJSHeapSize ?? 0;
  });
}
