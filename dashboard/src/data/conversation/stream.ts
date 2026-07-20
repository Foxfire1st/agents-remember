// Resumable SSE transport for active-conversation events (§9.2, §14.5). It deliberately does NOT
// rely on EventSource's native auto-reconnect: that would keep the original `after=` query AND add a
// `Last-Event-ID` header from the latest received id, which the server rejects as `cursor-conflict`
// when they differ (active/api.py preflight). Instead, on any transport error we close, back off, and
// open a FRESH EventSource whose only resume input is `after=<latest cursor>` — a brand-new instance
// has no lastEventId, so no header is sent. Gap/reset recovery is owned by the reducer+store; this
// controller only delivers ordered envelopes and reports connect/disconnect.

import { conversationEventsUrl } from "./client";
import type { ActiveEventCursor, ConversationEventEnvelope } from "./types";

export type EventSourceCtor = new (url: string) => EventSource;

export interface ConversationStreamHandlers {
  onEnvelope: (envelope: ConversationEventEnvelope) => void;
  onOpen?: () => void;
  onDisconnect?: () => void;
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
  setTimeoutImpl?: (fn: () => void, ms: number) => number;
  clearTimeoutImpl?: (handle: number) => void;
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
    setTimeoutImpl = (fn, ms) => globalThis.setTimeout(fn, ms) as unknown as number,
    clearTimeoutImpl = (handle) => globalThis.clearTimeout(handle),
  } = options;

  let source: EventSource | null = null;
  let timer: number | null = null;
  let stopped = false;

  const clearTimer = (): void => {
    if (timer !== null) {
      clearTimeoutImpl(timer);
      timer = null;
    }
  };

  const closeSource = (): void => {
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
    next.addEventListener("open", () => {
      if (stopped || source !== next) return;
      handlers.onOpen?.();
    });
    next.addEventListener("conversation", (event) => {
      if (stopped || source !== next) return;
      const message = event as MessageEvent<string>;
      let envelope: ConversationEventEnvelope;
      try {
        envelope = JSON.parse(message.data) as ConversationEventEnvelope;
      } catch {
        return; // a malformed frame is ignored; the reducer only ever sees well-formed envelopes
      }
      handlers.onEnvelope(envelope);
    });
    next.addEventListener("error", () => {
      if (stopped || source !== next) return;
      // The server closes the stream after a gap; the store will have set recovery and called stop().
      // Any other close is a transport drop: back off and reopen from the latest cursor.
      closeSource();
      handlers.onDisconnect?.();
      clearTimer();
      timer = setTimeoutImpl(() => {
        timer = null;
        open();
      }, reconnectDelayMs);
    });
  };

  open();

  return {
    reconnect: () => {
      if (stopped) return;
      clearTimer();
      open();
    },
    stop: () => {
      stopped = true;
      clearTimer();
      closeSource();
    },
  };
}
