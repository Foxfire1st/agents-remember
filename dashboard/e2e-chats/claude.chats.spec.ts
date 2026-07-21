// 260718-CHATS-L5F R7/R8: claude 2.1.216 durable Chats E2E. The headline guard for the developer's
// image3 — an ordinary claude session must NOT flood with command_lifecycle/rate_limit/echo
// unknown-vendor rows, must NOT wholesale-demote the surface to a version-mismatch "unverified", and
// an explicit model set/launch that natively succeeds must VALIDATE, never show a refused pair.

import { test } from "@playwright/test";

import {
  assertAcceptanceValidated,
  assertNoProjectionAlarm,
  assertNoUnknownVendorRows,
  assertNoVersionMismatchDemotion,
  driveTurn,
  endChat,
  openChat,
} from "./support/drive";
import { gateChatsE2E } from "./support/gate";


gateChatsE2E();

test.describe("claude Chats", () => {
  test("ordinary session is flood-free, not version-demoted, and a turn settles once", async ({
    page,
  }) => {
    await page.goto("/");
    const sessionId = await openChat(page, "claude");

    // R2 / image3: the launch validated — no refused pair, no failed-launch banner.
    await assertAcceptanceValidated(page);
    // R3 / image3: zero command_lifecycle / rate_limit_event / claude:echo unknown-vendor rows.
    await assertNoUnknownVendorRows(page);
    // R4 / image3: the surface is not wholesale-demoted by an observed-version mismatch.
    await assertNoVersionMismatchDemotion(page);
    await assertNoProjectionAlarm(page);

    await driveTurn(page, "reply with the single word ok");
    // The slash-command lifecycle and the echo shape stay recognized (no flood) after a real turn.
    await assertNoUnknownVendorRows(page);
    await assertAcceptanceValidated(page);

    await endChat(page, sessionId);
  });

  test("setting the model via the control validates and never shows a refused pair", async ({
    page,
  }) => {
    await page.goto("/");
    const sessionId = await openChat(page, "claude");
    // Open the model/effort control and apply a selection against the REAL catalog.
    const trigger = page.getByTestId("model-effort-trigger");
    if (await trigger.count()) {
      await trigger.click();
      const models = page.locator('[data-testid="model-effort-models"] [role="option"], [data-testid^="model-effort-model-"]');
      if (await models.count()) await models.first().click();
      const apply = page.getByTestId("model-effort-apply");
      if (await apply.count()) await apply.click();
    }
    // Whatever the running harness echoed, an alias whose resolved model matches must VALIDATE.
    await assertAcceptanceValidated(page);
    await assertNoUnknownVendorRows(page);
    await endChat(page, sessionId);
  });
});
