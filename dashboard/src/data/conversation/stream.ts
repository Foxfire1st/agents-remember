// Resumable SSE transport for active-conversation events (§9.2, §14.5). It deliberately does NOT
// rely on EventSource's native auto-reconnect: that would keep the original `after=` query AND add a
// `Last-Event-ID` header from the latest received id, which the server rejects as `cursor-conflict`
// when they differ (active/api.py preflight). Instead, on any transport error we close, back off, and
// open a FRESH EventSource whose only resume input is `after=<latest cursor>` — a brand-new instance
// has no lastEventId, so no header is sent. Gap/reset recovery is owned by the reducer+store; this
// controller only delivers ordered envelopes and reports connect/disconnect.
// The backoff is boot-window aware. Before the FIRST successful open the
// fresh-chat events endpoint can still 503 while the bridge composes (measured ~2 s), and the
// established 2 s backoff would sit blind through it — the boot window retries fast instead,
// aligned with the tuned 250/500/1000 epoch/hydrate schedule. The first successful open closes
// the window; every later drop is a real transport drop and keeps the established backoff.
// Sleep/wake: an OPEN stream that stops showing life (OS-sleep wall-clock jump, or silence
// past the frame backstop) is quietly cycled to a fresh resumable subscribe — see
// data/streamLiveness.ts. A half-open corpse never surfaces an `error`, so without the watchdog
// neither this controller's reconnect nor the dead-stream escalation could ever engage after a sleep.

import { startStreamLivenessWatchdog, type StreamLivenessTuning } from "../streamLiveness";
import { conversationEventsUrl } from "./client";
import type { ActiveEventCursor, ConversationEventEnvelope } from "./types";

export type EventSourceCtor = new (url: string) => EventSource;

// Bound how long a fresh subscribe may sit in CONNECTING before it is treated as
// a dead instance. A healthy open is near-instant (the endpoint's priming comment flushes headers
// at subscribe), so this is deliberately generous — it exists only to catch the pathological socket
// that opens neither `open` NOR `error` (a new half-open corpse, or a connect that hangs). Without
// it, the watchdog's QUIET cycle (which writes no phase) would leave a never-opening resubscribe at
// a fabricated "live" forever: the silent dead chat that the loud open-failure escalation exists to prevent.
export const STREAM_OPEN_DEADLINE_MS = 20_000;

export interface ConversationStreamHandlers {
  onEnvelope: (envelope: ConversationEventEnvelope) => void;
  onOpen?: () => void;
  onDisconnect?: () => void;
  /**
   * The instance errored WITHOUT ever opening. EventSource cannot surface
   * the HTTP status, but an unopened instance is the signature of a REJECTED request (e.g. a
   * stale signed cursor after a daemon restart answering 400 cursor-invalid) rather than a
   * transport drop — the store escalates repeats to a full re-page instead of retrying the same
   * poisoned cursor forever.
   */
  onOpenFailure?: () => void;
}

export interface ConversationStreamOptions {
  sessionId: string;
  epoch: string;
  base?: string;
  /** Reads the latest trusted resume cursor at (re)connect time — the store's projection.eventCursor. */
  getResumeCursor: () => ActiveEventCursor | null;
  handlers: ConversationStreamHandlers;
  eventSourceCtor?: EventSourceCtor;
  /** Reconnect backoff in ms (test override). */
  reconnectDelayMs?: number;
  /**
   * Reconnect backoff DURING THE BOOT WINDOW (before the first successful
   * open) in ms (test override). 500 ms discovers a composing bridge within ~0.5 s — above the
   * tuned 250 ms hydrate cadence so the dead-stream escalation (3 repage cycles of unopened
   * attempts) still spans ~4 s, comfortably past the measured ~2 s boot-503 window: a healthy
   * slow boot never burns into the fail-loud alarm.
   */
  bootReconnectDelayMs?: number;
  /**
   * How long a fresh instance may sit un-opened before it is judged a dead
   * socket and driven down the ordinary disconnect + open-failure path (test override).
   */
  openDeadlineMs?: number;
  setTimeoutImpl?: (fn: () => void, ms: number) => number;
  clearTimeoutImpl?: (handle: number) => void;
  /**
   * Liveness watchdog tuning (test override). The watchdog owns the sleep/wake + half-open-wedge
   * class: an OPEN stream that stops showing life (wall-clock jump = OS sleep, or silence past
   * the generous frame backstop) is cycled — closed and freshly re-subscribed from the latest
   * cursor, QUIETLY (no onDisconnect, so a healthy resume never flashes "connecting…").
   */
  liveness?: StreamLivenessTuning;
}

export interface ConversationStreamController {
  /** Reopen from the current resume cursor (used by the store after a re-page). */
  reconnect: () => void;
  stop: () => void;
}

export function openConversationStream(
  options: ConversationStreamOptions,
): ConversationStreamController {
  const {
    sessionId,
    epoch,
    base = "",
    getResumeCursor,
    handlers,
    eventSourceCtor = globalThis.EventSource as unknown as EventSourceCtor,
    reconnectDelayMs = 2000,
    bootReconnectDelayMs = 500,
    openDeadlineMs = STREAM_OPEN_DEADLINE_MS,
    setTimeoutImpl = (fn, ms) => globalThis.setTimeout(fn, ms) as unknown as number,
    clearTimeoutImpl = (handle) => globalThis.clearTimeout(handle),
    liveness,
  } = options;

  let source: EventSource | null = null;
  let timer: number | null = null;
  // The per-instance open-deadline — armed at each open, cleared the moment that instance
  // opens or errors, and fired only if it does NEITHER (the CONNECTING wedge).
  let openDeadlineTimer: number | null = null;
  let stopped = false;
  // The boot window is open until ANY instance reports its first successful open.
  let everOpened = false;
  // The watchdog only judges an OPEN channel: true while the current instance has opened.
  let openNow = false;

  const clearTimer = (): void => {
    if (timer !== null) {
      clearTimeoutImpl(timer);
      timer = null;
    }
  };

  const clearOpenDeadline = (): void => {
    if (openDeadlineTimer !== null) {
      clearTimeoutImpl(openDeadlineTimer);
      openDeadlineTimer = null;
    }
  };

  const closeSource = (): void => {
    openNow = false;
    if (source !== null) {
      source.close();
      source = null;
    }
  };

  const open = (): void => {
    if (stopped) return;
    closeSource();
    const url = conversationEventsUrl(sessionId, epoch, getResumeCursor(), base);
    const next = new eventSourceCtor(url);
    source = next;
    let openedThisInstance = false;

    // The single dead-instance path, shared by a surfaced `error` and by the open-deadline.
    // The server closes the stream after a gap; the store will have set recovery and called stop().
    // Any other close is a transport drop: back off and reopen from the latest cursor. An instance
    // that never OPENED additionally reports onOpenFailure so the store can escalate a
    // rejected/dead resume to a re-page; if the store stops this controller inside the handler, the
    // scheduled reopen is a guarded no-op.
    const onDead = (): void => {
      if (stopped || source !== next) return;
      clearOpenDeadline();
      closeSource();
      handlers.onDisconnect?.();
      if (!openedThisInstance) handlers.onOpenFailure?.();
      if (stopped) return;
      clearTimer();
      // An instance that never opened is likely the boot-503 race — retry fast; an
      // established stream that dropped keeps the measured 2 s backoff.
      timer = setTimeoutImpl(
        () => {
          timer = null;
          open();
        },
        everOpened ? reconnectDelayMs : bootReconnectDelayMs,
      );
    };

    next.addEventListener("open", () => {
      if (stopped || source !== next) return;
      openedThisInstance = true;
      everOpened = true; // the boot window closes at the first successful open
      openNow = true;
      clearOpenDeadline(); // it opened in time — the deadline no longer applies
      // The open itself is a sign of life (the priming comment flushes headers at subscribe).
      watchdog.markAlive();
      handlers.onOpen?.();
    });
    next.addEventListener("conversation", (event) => {
      if (stopped || source !== next) return;
      watchdog.markAlive(); // any received frame proves the channel lives, even a malformed one
      const message = event as MessageEvent<string>;
      let envelope: ConversationEventEnvelope;
      try {
        envelope = JSON.parse(message.data) as ConversationEventEnvelope;
      } catch {
        return; // a malformed frame is ignored; the reducer only ever sees well-formed envelopes
      }
      handlers.onEnvelope(envelope);
    });
    next.addEventListener("error", onDead);

    // Arm the open-deadline for THIS instance. A rejected request surfaces `error` and takes
    // the path above; a socket that hangs in CONNECTING surfaces nothing, so only this deadline can
    // turn the silent wedge into an honest disconnect + escalation. `onDead` is idempotent-safe
    // — it no-ops once the instance is superseded (source !== next) or already opened (cleared).
    clearOpenDeadline();
    openDeadlineTimer = setTimeoutImpl(() => {
      openDeadlineTimer = null;
      onDead();
    }, openDeadlineMs);
  };

  // Sleep/wake + half-open-wedge recovery: the OPEN stream stopped showing life (a wall-clock
  // jump — the OS suspended timers and the socket with them — or silence past the generous frame
  // backstop). Cycle it: close and open a FRESH resumable subscribe from the latest cursor. This
  // is deliberately QUIET — no onDisconnect — because a healthy resume opens instantly on the
  // server's priming comment and replays whatever the corpse missed; the surface stays "live".
  // If the fresh subscribe fails, the ordinary error path reports disconnect/open-failure
  // exactly as for a transport drop, so a real outage still escalates honestly. `open()` arms
  // the open-deadline, so a cycled subscribe that never opens is ALSO escalated — the quiet cycle
  // can no longer strand the phase at a fabricated "live".
  const cycle = (): void => {
    if (stopped) return;
    clearTimer();
    open();
  };

  const watchdog = startStreamLivenessWatchdog({
    isOpen: () => openNow,
    onDead: cycle,
    ...liveness,
  });

  open();

  return {
    reconnect: () => {
      if (stopped) return;
      clearTimer();
      open();
    },
    stop: () => {
      stopped = true;
      watchdog.stop();
      clearTimer();
      clearOpenDeadline();
      closeSource();
    },
  };
}
