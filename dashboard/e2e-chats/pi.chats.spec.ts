// 260718-CHATS-L5F R7/R8: pi 0.80.7 durable Chats E2E. Pi confirmed unexposed for the alias/default
// collision (it requires an exact provider-qualified key), so this pins that an ordinary pi session
// opens clean, settles a turn once, and never floods — the same composed invariants as the others.

import { expect, test } from "@playwright/test";

import {
  assertNoProjectionAlarm,
  assertNoUnknownVendorRows,
  assertSingleTurnResultInvariants,
  driveTurn,
  endChat,
  openChat,
} from "./support/drive";
import { gateChatsE2E } from "./support/gate";


gateChatsE2E();

test.describe("pi Chats", () => {
  test("fresh open is clean and a turn settles once with no vendor flood", async ({ page }) => {
    await page.goto("/");
    const sessionId = await openChat(page, "pi");
    await assertNoProjectionAlarm(page);
    await assertNoUnknownVendorRows(page);
    await driveTurn(page, "reply with ok");
    await assertSingleTurnResultInvariants(page);
    await expect(page.getByTestId("failed-launch-banner")).toHaveCount(0);
    await endChat(page, sessionId);
  });
});
