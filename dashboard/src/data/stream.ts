import { dashboardStore } from "./store";
import {
  reviewMemoryScope,
  reviewPublicationScope,
  publishReviewPublication,
  subscribeReviewMemoryScope,
  publishReviewMemory,
  publishReviewMemoryError,
  publishReviewExplainerMoved,
} from './reviewMemoryWatch';
import { reviewPageId } from "./reviewOpen";
import { startStreamLivenessWatchdog } from "./streamLiveness";

// The state channel's named SSE events (serving/app.py + serving/delta.py). Each is merged
// into the store by key; the browser's EventSource auto-reconnects and the server
// re-snapshots, so a reconnect simply replaces the store via the `snapshot` event again.
const STATE_EVENTS = [
  "task",
  "task.removed",
  "taskCounts",
  "enclosure",
  "enclosure.removed",
  "activeWorktreeGroups",
  "metrics",
  "analytics",
] as const;

// 260724 review M4: bound how long a fresh state subscribe may sit in CONNECTING before it is
// treated as a dead instance. A healthy open is near-instant (a reconnect re-snapshots at once
// through the identity-preserving merge), so this is deliberately generous — it exists only to
// catch the pathological socket that fires NEITHER `open` NOR `error` (a half-open corpse or a
// hung connect). Without it the watchdog's QUIET cycle (which writes no phase) would strand a
// never-opening resubscribe at a fabricated "live" forever, so the cockpit LIVE badge could stay
// lit with nothing connected.
export const STATE_STREAM_OPEN_DEADLINE_MS = 20_000;

// A stream the browser will not reopen: an error status (a server shutting down answers one) or a
// failed connect leaves the EventSource CLOSED, and a closed EventSource never retries. The page
// reopens it itself, after a wait that doubles from the first to the last below and starts again
// once a stream opens; the badge says SIGNAL LOST meanwhile.
export const STREAM_REOPEN_FIRST_MS = 1_000;
export const STREAM_REOPEN_LAST_MS = 15_000;

// One pending timeout at a time: setting it again replaces the pending one.
function oneTimer(): { set: (run: () => void, ms: number) => void; clear: () => void; pending: () => boolean } {
  let timer: number | null = null;
  const clear = (): void => {
    if (timer !== null) globalThis.clearTimeout(timer);
    timer = null;
  };
  return {
    set: (run, ms) => {
      clear();
      timer = globalThis.setTimeout(() => {
        timer = null;
        run();
      }, ms) as unknown as number;
    },
    clear,
    pending: () => timer !== null,
  };
}

// The state channel's own frames: the liveness beat, the snapshot and the per-entity deltas.
function attachStateListeners(next: EventSource, isCurrent: () => boolean, markAlive: () => void): void {
  const store = dashboardStore.getState();
  // The server names the stream alive every 15 s; its transport keepalive is a comment frame
  // an EventSource never surfaces. With it, silence means the transport died.
  next.addEventListener("alive", () => {
    if (isCurrent()) markAlive();
  });
  next.addEventListener("snapshot", (event) => {
    if (!isCurrent()) return;
    markAlive();
    store.applySnapshot(JSON.parse((event as MessageEvent).data));
  });
  for (const name of STATE_EVENTS) {
    next.addEventListener(name, (event) => {
      if (!isCurrent()) return;
      markAlive();
      store.applyDelta(name, JSON.parse((event as MessageEvent).data));
    });
  }
}

function reopenWaits(): { next: () => number; reset: () => void } {
  let failures = 0;
  return {
    next: () => Math.min(STREAM_REOPEN_FIRST_MS * 2 ** failures++, STREAM_REOPEN_LAST_MS),
    reset: () => {
      failures = 0;
    },
  };
}

function attachReviewListeners(
  next: EventSource,
  isCurrent: () => boolean,
  markAlive: () => void,
  resetInitialMemory: { current: boolean },
): void {
  next.addEventListener('review.explainer.changed', (event) => {
    if (!isCurrent()) return;
    markAlive();
    publishReviewPublication(JSON.parse((event as MessageEvent).data));
  });
  next.addEventListener('review.explainer.watch.error', (event) => {
    if (!isCurrent()) return;
    publishReviewPublication(JSON.parse((event as MessageEvent).data), true);
  });
  next.addEventListener('review.memory', (event) => {
    if (!isCurrent()) return;
    markAlive();
    publishReviewMemory(JSON.parse((event as MessageEvent).data), resetInitialMemory.current);
    resetInitialMemory.current = false;
  });
  next.addEventListener('review.memory.error', (event) => {
    if (!isCurrent()) return;
    publishReviewMemoryError(JSON.parse((event as MessageEvent).data));
  });
  next.addEventListener('review.explainer.moved', (event) => {
    if (!isCurrent()) return;
    markAlive();
    publishReviewExplainerMoved(JSON.parse((event as MessageEvent).data));
  });
}
export function connectState(base = ""): () => void {
  const store = dashboardStore.getState(); // actions are stable references
  let source: EventSource | null = null;
  let openNow = false;
  let stopped = false;
  // M4: the per-instance open-deadline — armed at each open(), cleared the moment that instance
  // opens or errors, and fired only if it does NEITHER (the CONNECTING wedge a quiet cycle leaves).
  const openDeadline = oneTimer();
  const reopen = oneTimer();
  const waits = reopenWaits();

  const open = (): void => {
    if (stopped) return;
    reopen.clear();
    source?.close();
    const scope = reviewMemoryScope();
    const publication = reviewPublicationScope();
    const params = new URLSearchParams();
    if (scope) { params.set('reviewRepo', scope.repo); params.set('reviewMaster', scope.master); params.set('reviewLeaf', scope.leaf); }
    if (publication) {
      params.set('publication', [publication.repo, publication.master, publication.leaf].join('/'));
      params.set('page', reviewPageId()); // the review page's opens are released when it leaves (D585)
    }
    const query = params.size ? `?${params}` : '';
    const next = new EventSource(`${base}/api/stream${query}`);
    source = next;
    openNow = false;
    const initialMemory = { current: true };
    const isCurrent = () => !stopped && source === next;
    attachReviewListeners(next, isCurrent, () => watchdog.markAlive(), initialMemory);
    attachStateListeners(next, isCurrent, () => watchdog.markAlive());
    next.addEventListener("open", () => {
      if (!isCurrent()) return;
      initialMemory.current = true;
      openNow = true;
      openDeadline.clear(); // it opened in time — the M4 deadline no longer applies
      waits.reset();
      watchdog.markAlive();
      store.setConn("live");
    });
    next.addEventListener("error", () => {
      if (stopped || source !== next) return;
      openNow = false;
      openDeadline.clear(); // it surfaced an error — the native auto-reconnect owns recovery now
      store.setConn("signal-lost"); // SIGNAL LOST fiction; the native auto-reconnect retries
      // ...unless the browser gave up: a closed EventSource never retries, so the page does.
      if (next.readyState === EventSource.CLOSED && !reopen.pending()) reopen.set(open, waits.next());
    });

    // M4: arm the open-deadline for THIS instance. A socket hung in CONNECTING fires NEITHER `open`
    // NOR `error`, so after a quiet watchdog cycle `conn` would sit at a fabricated "live" with
    // nothing connected (the watchdog only judges an OPEN channel, so it can never recover a
    // never-opened instance). If the deadline fires — the instance did neither — stop asserting
    // LIVE: drop to "signal-lost" exactly as the error path does, then re-enter the open cycle for
    // a fresh subscribe. A healthy fast reopen clears this before it fires, so the HEALTHY cycle
    // stays QUIET (no badge flash, no needless re-snapshot).
    openDeadline.set(() => {
      if (stopped || source !== next) return;
      store.setConn("signal-lost");
      open();
    }, STATE_STREAM_OPEN_DEADLINE_MS);
  };

  // The sleep/wake + half-open-wedge class (streamLiveness.ts):
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

  const unsubscribeScope = subscribeReviewMemoryScope(open);
  return () => {
    stopped = true;
    unsubscribeScope();
    watchdog.stop();
    openDeadline.clear();
    reopen.clear();
    source?.close();
    source = null;
  };
}

/**
 * Subscribe to `GET /api/events` (the raw `ar-observer-event/v1` channel). Verbatim JSONL
 * lines for the Event River; the browser carries the opaque byte-offset `Last-Event-ID`
 * cursor across reconnects. A separate connection from the state channel by design.
 *
 * Deliberately NOT under the liveness watchdog: the resume cursor lives only in the instance's
 * own `Last-Event-ID`, which a FRESH EventSource cannot be told to send — a quiet cycle would
 * re-flood the bounded initial window into the river on every judgment. The state channel
 * (`connectState`, above) has no such cursor and carries the sleep/wake fix.
 */
export function connectEvents(
  onLine: (line: string) => void,
  base = "",
  onReady?: (ready?: { lastEventTs?: string }) => void,
): () => void {
  // A river the browser gave up on is reopened by the page, from the last event it had: a fresh
  // EventSource cannot send `Last-Event-ID`, so the cursor goes in the query instead.
  let source: EventSource | null = null;
  let cursor = "";
  let stopped = false;
  const reopen = oneTimer();
  const waits = reopenWaits();
  const open = (): void => {
    if (stopped) return;
    const query = cursor ? `?${new URLSearchParams({ lastEventId: cursor })}` : "";
    const next = new EventSource(`${base}/api/events${query}`);
    source = next;
    next.addEventListener("open", () => waits.reset());
    next.addEventListener("event", (event) => {
      const message = event as MessageEvent;
      if (message.lastEventId) cursor = message.lastEventId;
      onLine(message.data);
    });
    next.addEventListener("ready", (event) => onReady?.(parseReady((event as MessageEvent).data)));
    next.addEventListener("error", () => {
      if (stopped || source !== next || next.readyState !== EventSource.CLOSED || reopen.pending()) return;
      reopen.set(open, waits.next());
    });
  };
  open();
  return () => {
    stopped = true;
    reopen.clear();
    source?.close();
  };
}

/** The /api/events `ready` marker, carrying the last feed when the store has one. */
function parseReady(data: string): { lastEventTs?: string } | undefined {
  try {
    const parsed: unknown = JSON.parse(data);
    if (typeof parsed !== "object" || parsed === null) return undefined;
    const lastEventTs = (parsed as { lastEventTs?: unknown }).lastEventTs;
    return typeof lastEventTs === "string" ? { lastEventTs } : undefined;
  } catch {
    return undefined; // an unreadable marker still opens the gate; never break the feed
  }
}
