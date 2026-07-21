// 260718-CHATS-L5F R7: Playwright global setup — boot the isolated real daemon once, publish its
// base URL to the specs, and stash a teardown handle. No-op when AR_RUN_CHATS_E2E is unset.

import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { bootDaemon } from "./daemon";

export const STATE_FILE = join(tmpdir(), "ar-chats-e2e-state.json");

let booted: Awaited<ReturnType<typeof bootDaemon>> | undefined;

export default async function globalSetup(): Promise<void> {
  if (process.env.AR_RUN_CHATS_E2E !== "1") return;
  booted = await bootDaemon();
  process.env.AR_CHATS_E2E_BASE_URL = booted.baseUrl;
  writeFileSync(
    STATE_FILE,
    JSON.stringify({ baseUrl: booted.baseUrl, coordinationRoot: booted.coordinationRoot }),
    "utf-8",
  );
  // Teardown reads STATE_FILE + kills the process group; keep a same-process handle too.
  (globalThis as Record<string, unknown>).__AR_CHATS_E2E_STOP__ = booted.stop;
}
