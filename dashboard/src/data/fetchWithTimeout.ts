// Bounded request helper for the hung-transport class: a fetch on a
// half-dead socket (the vite proxy keeps half-open connections across daemon restarts) neither
// resolves nor rejects, and every caller that joined it — above all the single-flighted
// boot/poll reads in data/inflight.ts — stays pending FOREVER (the catalog poll dies,
// missedBeats climbs, the UI goes stale). The bound must abort the SOCKET, not just stop
// waiting, so the browser's connection-pool slot is released. `AbortSignal.timeout` would do
// that but is invisible to vitest fake timers, so this uses the controller + window.setTimeout
// idiom (the boundedAttempt pattern in data/submitClient.ts).

/**
 * `fetch` bounded by `timeoutMs`: on expiry the request is aborted, so a hung transport
 * surfaces as an ordinary AbortError rejection and the caller's established failure path
 * (null / error read / thrown status) decides what that means.
 */
export async function fetchWithTimeout(
  url: string,
  timeoutMs: number,
  init: RequestInit = {},
): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}
