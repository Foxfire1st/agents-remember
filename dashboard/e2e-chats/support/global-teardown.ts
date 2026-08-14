// 260718-CHATS-L5F R7: stop the isolated daemon booted by global-setup.

export default async function globalTeardown(): Promise<void> {
  if (process.env.AR_RUN_CHATS_E2E !== "1") return;
  const stop = (globalThis as Record<string, unknown>).__AR_CHATS_E2E_STOP__;
  if (typeof stop === "function") {
    await (stop as () => Promise<void>)();
  }
}
