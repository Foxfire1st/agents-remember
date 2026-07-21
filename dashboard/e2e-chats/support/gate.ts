// 260718-CHATS-L5F R7: the opt-in gate. Every Chats spec registers this so that, when
// AR_RUN_CHATS_E2E is unset, all tests SKIP (a green no-op) rather than spawning real harnesses.

import { test } from "@playwright/test";

export function gateChatsE2E(): void {
  test.beforeEach(() => {
    test.skip(
      process.env.AR_RUN_CHATS_E2E !== "1",
      "opt-in Chats E2E — set AR_RUN_CHATS_E2E=1 (drives the real installed harnesses)",
    );
  });
}
