// 260718-CHATS-L5F R7/R8: codex 0.144.5 durable Chats E2E. The headline guard for the developer's
// image.png — a fresh codex open must NOT flood the timeline with unknown-vendor startup rows and
// must NOT open on a cried-wolf red "structured surface unavailable" strip.

import { test } from "@playwright/test";

import {
  assertNoProjectionAlarm,
  assertNoUnknownVendorRows,
  assertSingleTurnResultInvariants,
  assertWorkingStateSeenDuringTurn,
  driveTurn,
  endChat,
  interruptTurn,
  openChat,
} from "./support/drive";
import { gateChatsE2E } from "./support/gate";


gateChatsE2E();

test.describe("codex Chats", () => {
  test("fresh open is clean, a turn settles once, no vendor flood, no cried-wolf strip", async ({
    page,
  }) => {
    await page.goto("/");
    const sessionId = await openChat(page, "codex");

    // R10 / audit V13: a healthy launch reaches the structured surface with zero alarm strip.
    await assertNoProjectionAlarm(page);
    // R1 / image.png: zero unknown-vendor rows on a stock codex open (the startup burst is dropped).
    await assertNoUnknownVendorRows(page);

    // The turn settles as exactly one projection, still with no vendor flood or alarm strip.
    await driveTurn(page, "reply with the single word ok");
    await assertNoUnknownVendorRows(page);
    await assertNoProjectionAlarm(page);

    await endChat(page, sessionId);
  });

  test("a streaming turn shows a working state, never settled-green throughout (R9)", async ({
    page,
  }) => {
    await page.goto("/");
    const sessionId = await openChat(page, "codex");
    // A genuinely long turn so the streaming working-state is observable (a one-word reply settles
    // before it can be seen). Audit V5: the pane must show activity while output streams.
    await assertWorkingStateSeenDuringTurn(
      page,
      "count from 1 to 40, printing one number per line",
    );
    await endChat(page, sessionId);
  });

  test("a second ordinary turn stays flood-free", async ({ page }) => {
    await page.goto("/");
    const sessionId = await openChat(page, "codex");
    await driveTurn(page, "reply with ok");
    await driveTurn(page, "reply with ok again");
    await assertNoUnknownVendorRows(page);
    await assertSingleTurnResultInvariants(page);
    await endChat(page, sessionId);
  });

  test("interrupt settles the turn cleanly with no vendor flood", async ({ page }) => {
    await page.goto("/");
    const sessionId = await openChat(page, "codex");
    await interruptTurn(page, "count slowly from 1 to 50, one number per line");
    await assertSingleTurnResultInvariants(page);
    await endChat(page, sessionId);
  });
});
