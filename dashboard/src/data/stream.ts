import { dashboardStore } from "./store";
import { startStreamLivenessWatchdog } from "./streamLiveness";

// The state channel's named SSE events (serving/app.py + serving/delta.py). Each is merged
// into the store by key; the browser's EventSource auto-reconnects and the server
// re-snapshots, so a reconnect simply replaces the store via the `snapshot` event again.
const STATE_EVENTS = [
  "lifecycle",
  "lifecycle.removed",
  "enclosure",
  "enclosure.removed",
  "provider",
  "provider.removed",
  "activeWorktreeGroups",
  "metrics",
  "analytics",
] as const;

// 260724 review M4: bound how long a fresh state subscribe may sit in CONNECTING before it is
// treated as a dead instance. A healthy open is near-instant (a reconnect re-snapshots at once
// through the identity-preserving merge), so this is deliberately generous — it exists only to
// catch the pathological socket that fires NEITHER `open` NOR `error` (a half-open corpse or a
// hung connect). Without it the watchdog's QUIET cycle (which writes no phase) would strand a
// never-opening resubscribe at a fabricated "live" forever — the same silent-dead-channel class
// the conversation stream's open-deadline (conversation/stream.ts) fixes, mirrored here so the
// cockpit LIVE badge cannot stay lit with nothing connected.
export const STATE_STREAM_OPEN_DEADLINE_MS = 20_000;

/** Subscribe to `GET /api/stream` (the folded-projection channel). Returns a disposer. */
export function connectState(base = ""): () => void {
  const store = dashboardStore.getState(); // actions are stable references
  let source: EventSource | null = null;
  let openNow = false;
  let stopped = false;
  // M4: the per-instance open-deadline — armed at each open(), cleared the moment that instance
  // opens or errors, and fired only if it does NEITHER (the CONNECTING wedge a quiet cycle leaves).
  let openDeadlineTimer: number | null = null;

  const clearOpenDeadline = (): void => {
    if (openDeadlineTimer !== null) {
      globalThis.clearTimeout(openDeadlineTimer);
      openDeadlineTimer = null;
    }
  };

  const open = (): void => {
    if (stopped) return;
    source?.close();
    const next = new EventSource(`${base}/api/stream`);
    source = next;
    openNow = false;
    next.addEventListener("snapshot", (event) => {
      if (stopped || source !== next) return;
      watchdog.markAlive();
      store.applySnapshot(JSON.parse((event as MessageEvent).data));
    });
    for (const name of STATE_EVENTS) {
      next.addEventListener(name, (event) => {
        if (stopped || source !== next) return;
        watchdog.markAlive();
        store.applyDelta(name, JSON.parse((event as MessageEvent).data));
      });
    }
    next.addEventListener("open", () => {
      if (stopped || source !== next) return;
      openNow = true;
      clearOpenDeadline(); // it opened in time — the M4 deadline no longer applies
      watchdog.markAlive();
      store.setConn("live");
    });
    next.addEventListener("error", () => {
      if (stopped || source !== next) return;
      openNow = false;
      clearOpenDeadline(); // it surfaced an error — the native auto-reconnect owns recovery now
      store.setConn("signal-lost"); // SIGNAL LOST fiction; the native auto-reconnect retries
    });

    // M4: arm the open-deadline for THIS instance. A socket hung in CONNECTING fires NEITHER `open`
    // NOR `error`, so after a quiet watchdog cycle `conn` would sit at a fabricated "live" with
    // nothing connected (the watchdog only judges an OPEN channel, so it can never recover a
    // never-opened instance). If the deadline fires — the instance did neither — stop asserting
    // LIVE: drop to "signal-lost" exactly as the error path does, then re-enter the open cycle for
    // a fresh subscribe. A healthy fast reopen clears this before it fires, so the HEALTHY cycle
    // stays QUIET (no badge flash, no needless re-snapshot).
    clearOpenDeadline();
    openDeadlineTimer = globalThis.setTimeout(() => {
      openDeadlineTimer = null;
      if (stopped || source !== next) return;
      store.setConn("signal-lost");
      open();
    }, STATE_STREAM_OPEN_DEADLINE_MS) as unknown as number;
  };

  // The same sleep/wake + half-open-wedge class as the conversation stream (streamLiveness.ts):
  // deltas are content-only, so an idle channel is legitimately silent, and after an OS sleep
  // the open socket is a corpse that never fires `error` — the native auto-reconnect never
  // engages and the cockpit would show a live-LOOKING but frozen projection forever. The cycle
  // is quiet: a fresh subscribe re-snapshots through the identity-preserving merge, so a healthy
  // resume allocates nothing, re-renders nothing, and never flashes SIGNAL LOST.
  const watchdog = startStreamLivenessWatchdog({
    isOpen: () => openNow,
    onDead: open,
  });

  open();

  return () => {
    stopped = true;
    watchdog.stop();
    clearOpenDeadline();
    source?.close();
    source = null;
  };
}

/**
 * Subscribe to `GET /api/events` (the raw `ar-observer-event/v1` channel). Verbatim JSONL
 * lines for the Event River; the browser carries the opaque byte-offset `Last-Event-ID`
 * cursor across reconnects. A separate connection from the state channel by design.
 * `onInterrupt` fires on a connection error/reconnect: the NEXT connection replays a backlog
 * before its own `ready` marker, so a consumer gating on ready must re-close its gate here
 * (an undecodable cursor makes the server fall back to the full initial window).
 *
 * Deliberately NOT under the liveness watchdog: the resume cursor lives only in the instance's
 * own `Last-Event-ID`, which a FRESH EventSource cannot be told to send — a quiet cycle would
 * re-flood the bounded initial window into the river on every judgment. The state channel
 * (`connectState`, above) has no such cursor and carries the sleep/wake fix.
 */
export function connectEvents(
  onLine: (line: string) => void,
  base = "",
  onReady?: () => void,
  onInterrupt?: () => void,
): () => void {
  const source = new EventSource(`${base}/api/events`);
  source.addEventListener("event", (event) => onLine((event as MessageEvent).data));
  source.addEventListener("ready", () => onReady?.());
  source.addEventListener("error", () => onInterrupt?.());
  return () => source.close();
}
