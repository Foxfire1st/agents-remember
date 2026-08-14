// 260718-CHATS-L5F R7/R8: the COMPOSED drive across harnesses + the per-session leak assertion
// (R5's daemon-side release rides here). Repeated open → turn → End cycles must not grow the
// browser JS heap without bound — the tombstone-projector / _locks / queue_rows classes the
// half-time diagnosis flagged. The whole-experience invariants (no vendor floods, no refused pairs,
// no cried-wolf strips) must hold across every harness in one run.

import { expect, test } from "@playwright/test";

import {
  assertAcceptanceValidated,
  assertNoProjectionAlarm,
  assertNoUnknownVendorRows,
  driveTurn,
  endChat,
  openChat,
  sampleHeapBytes,
} from "./support/drive";
import { gateChatsE2E } from "./support/gate";

const HARNESSES = (process.env.AR_CHATS_E2E_HARNESSES ?? "codex,claude,pi").split(
  ",",
) as Array<"codex" | "claude" | "pi">;


gateChatsE2E();

test("composed: every installed harness opens clean, settles once, and validates acceptance", async ({
  page,
}) => {
  await page.goto("/");
  for (const harness of HARNESSES) {
    const sessionId = await openChat(page, harness);
    await assertNoProjectionAlarm(page);
    await assertNoUnknownVendorRows(page);
    await assertAcceptanceValidated(page);
    await driveTurn(page, "reply with ok");
    await assertNoUnknownVendorRows(page);
    await assertAcceptanceValidated(page);
    await endChat(page, sessionId);
  }
});

test("per-session structures release across repeated open/End cycles (no unbounded heap growth)", async ({
  page,
}) => {
  await page.goto("/");
  const harness = HARNESSES[0];
  // Warm up so lazy module/first-render allocation is not counted as a leak.
  const warm = await openChat(page, harness);
  await driveTurn(page, "reply with ok");
  await endChat(page, warm);
  const baseline = await sampleHeapBytes(page);

  for (let cycle = 0; cycle < 5; cycle += 1) {
    const sessionId = await openChat(page, harness);
    await driveTurn(page, "reply with ok");
    await assertNoUnknownVendorRows(page);
    await endChat(page, sessionId);
  }

  const after = await sampleHeapBytes(page);
  // performance.memory is Chromium-only; when unavailable both samples are 0 and this is a no-op
  // guard. When available, five open/End cycles must not balloon the heap — a per-session structure
  // never released would grow it linearly. Allow generous slack for GC timing; fail on runaway.
  if (baseline > 0 && after > 0) {
    expect(after).toBeLessThan(baseline * 2.5);
  }
});
