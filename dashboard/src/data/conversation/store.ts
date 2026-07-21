// Reconstructable active-conversation store (design §11.1, §11.3, R1). It holds ONLY a browser
// projection rebuilt from public server/native authority — no IndexedDB/localStorage/SQLite, no
// optimistic durable item authority. House zustand idiom: a vanilla `createStore` + a thin
// `useActiveConversation(selector)` hook (matches store.ts / sessionCockpitStore.ts).
//
// The store orchestrates the page<->stream contract: connect hydrates a native page then opens the
// resumable stream; a reducer-signalled `gap`/`reset` recovery stops the stream, re-pages native
// authority, and resumes ONLY from the fresh page's atomically-captured eventCursor (§6.8). A bounded
// LRU may evict an unfocused session's DOM/projection; it is simply rehydrated on refocus — history
// authority is always native, never this store.

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import {
  fetchConversationPage,
  type FetchLike,
} from "./client";
import {
  applyEvent,
  applyInitialPage,
  applyOlderPage,
  clearRecovery,
  emptyProjection,
  type ActiveConversationProjection,
} from "./reducer";
import { openConversationStream, type EventSourceCtor } from "./stream";
import type {
  ActiveConversationRef,
  ConversationEventEnvelope,
  ConversationPage,
  ConversationRouteError,
} from "./types";

const LRU_LIMIT = 6;

export interface ActiveConversationState {
  bySession: Record<string, ActiveConversationProjection>;
  /** The server's typed reason for a failed page/reconnect, threaded to the banner (§14.5, F15). */
  errorBySession: Record<string, ConversationRouteError | null>;
  touchOrder: string[];
  applyPage: (sessionId: string, page: ConversationPage, mode: "initial" | "older") => void;
  ingestEvent: (sessionId: string, envelope: ConversationEventEnvelope) => void;
  setStreamPhase: (sessionId: string, phase: ActiveConversationProjection["stream"]) => void;
  failStream: (sessionId: string, error: ConversationRouteError | null) => void;
  setScrollAnchor: (
    sessionId: string,
    anchor: ActiveConversationProjection["scrollAnchor"],
  ) => void;
  evict: (sessionId: string) => void;
  reset: () => void;
}

function touch(order: string[], sessionId: string): string[] {
  return [sessionId, ...order.filter((id) => id !== sessionId)];
}

export const activeConversationStore = createStore<ActiveConversationState>((set) => ({
  bySession: {},
  errorBySession: {},
  touchOrder: [],

  applyPage: (sessionId, page, mode) =>
    set((state) => {
      const current = state.bySession[sessionId] ?? emptyProjection(page.identity);
      const next =
        mode === "initial" ? applyInitialPage(current, page) : applyOlderPage(current, page);
      // A successful hydrate clears any prior typed error.
      const errorBySession = { ...state.errorBySession };
      delete errorBySession[sessionId];
      return {
        bySession: { ...state.bySession, [sessionId]: next },
        errorBySession,
        touchOrder: touch(state.touchOrder, sessionId),
      };
    }),

  ingestEvent: (sessionId, envelope) =>
    set((state) => {
      const current = state.bySession[sessionId];
      if (current === undefined) return state; // events for an un-hydrated session are dropped
      const next = applyEvent(current, envelope);
      if (next === current) return state;
      return { bySession: { ...state.bySession, [sessionId]: next } };
    }),

  setStreamPhase: (sessionId, phase) =>
    set((state) => {
      const current = state.bySession[sessionId];
      if (current === undefined || current.stream === phase) return state;
      return { bySession: { ...state.bySession, [sessionId]: { ...current, stream: phase } } };
    }),

  // Record a typed failure reason and mark the projection (when present) projection-failed. When no
  // projection exists yet (first-connect failure) the error alone lets the surface render the reason.
  failStream: (sessionId, error) =>
    set((state) => {
      const current = state.bySession[sessionId];
      const bySession =
        current === undefined
          ? state.bySession
          : { ...state.bySession, [sessionId]: { ...current, stream: "projection-failed" as const } };
      return { bySession, errorBySession: { ...state.errorBySession, [sessionId]: error } };
    }),

  setScrollAnchor: (sessionId, anchor) =>
    set((state) => {
      const current = state.bySession[sessionId];
      if (current === undefined) return state;
      return { bySession: { ...state.bySession, [sessionId]: { ...current, scrollAnchor: anchor } } };
    }),

  evict: (sessionId) =>
    set((state) => {
      if (state.bySession[sessionId] === undefined) return state;
      const bySession = { ...state.bySession };
      delete bySession[sessionId];
      return { bySession, touchOrder: state.touchOrder.filter((id) => id !== sessionId) };
    }),

  reset: () => set({ bySession: {}, errorBySession: {}, touchOrder: [] }),
}));

export const useActiveConversation = <T>(selector: (state: ActiveConversationState) => T): T =>
  useStore(activeConversationStore, selector);

// ── Orchestration (connect / recovery / LRU) ────────────────────────────────────────────────────
// Per-session runtime lives outside the store to avoid re-render churn (identity-preserving pattern,
// store.ts:reuse/mergeKeyed). It is never conversation authority.

interface SessionRuntime {
  epoch: string;
  base: string;
  fetchImpl: FetchLike;
  eventSourceCtor?: EventSourceCtor;
  controller: { reconnect: () => void; stop: () => void } | null;
  generation: number;
  disposed: boolean;
}

const runtimeBySession = new Map<string, SessionRuntime>();

export interface ConnectOptions {
  base?: string;
  fetchImpl?: FetchLike;
  eventSourceCtor?: EventSourceCtor;
  limit?: number;
}

// 260718-CHATS-L5F R10 (audit V13): a fresh chat's first projection fetch races the session's own
// boot — the runner/bridge is not yet listening for a few hundred ms after the seat row appears. The
// L4 F20 stream auto-retry only covers a DROPPED live stream, not this first-connect race, so a
// healthy launch flashed the fail-loud "structured surface unavailable" alarm until a manual retry.
// The initial hydrate now retries on a quiet `connecting` phase across a bounded window that covers
// boot; it escalates to the honest `projection-failed` alarm only after the window exhausts, so a
// genuinely broken session still fails loud — the strip is deferred, never masked.
const INITIAL_CONNECT_ATTEMPTS = 8;
const INITIAL_CONNECT_RETRY_MS = 400;

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

// Only a TRANSIENT first-connect failure is the launch/boot race: a connection refused before the
// runner is listening (httpStatus 0) or a 5xx while the bridge composes (the diagnosis saw the codex
// bridge answer 503 during boot). A 4xx — 409 epoch/cursor, 404 unknown session — is a real,
// terminal answer and must fail loud immediately, never masked behind a retry (R10 honesty).
function isTransientBootFailure(error: { httpStatus: number } | null): boolean {
  if (error === null) return true; // a null error is a bare transport drop
  return error.httpStatus === 0 || error.httpStatus >= 500;
}

async function hydrateAndStream(sessionId: string, runtime: SessionRuntime, limit?: number): Promise<void> {
  const generation = runtime.generation;
  const store = activeConversationStore.getState();
  store.setStreamPhase(sessionId, "connecting");
  for (let attempt = 1; ; attempt += 1) {
    const page = await fetchConversationPage(
      sessionId,
      runtime.epoch,
      { limit },
      runtime.base,
      runtime.fetchImpl,
    );
    if (runtime.disposed || runtime.generation !== generation) return;
    if (page.ok) {
      activeConversationStore.getState().applyPage(sessionId, page.page, "initial");
      startStream(sessionId, runtime, limit);
      return;
    }
    if (attempt >= INITIAL_CONNECT_ATTEMPTS || !isTransientBootFailure(page.error)) {
      // A hard failure, or the boot window is exhausted: fail loud with the server's reason.
      activeConversationStore.getState().failStream(sessionId, page.error);
      return;
    }
    // Transient boot race: stay on the quiet `connecting` phase and give the bridge time to come up.
    await delay(INITIAL_CONNECT_RETRY_MS);
    if (runtime.disposed || runtime.generation !== generation) return;
  }
}

function startStream(sessionId: string, runtime: SessionRuntime, limit?: number): void {
  runtime.controller?.stop();
  runtime.controller = openConversationStream({
    sessionId,
    epoch: runtime.epoch,
    base: runtime.base,
    eventSourceCtor: runtime.eventSourceCtor,
    getResumeCursor: () => activeConversationStore.getState().bySession[sessionId]?.eventCursor ?? null,
    handlers: {
      onOpen: () => activeConversationStore.getState().setStreamPhase(sessionId, "live"),
      onDisconnect: () => activeConversationStore.getState().setStreamPhase(sessionId, "reconnecting"),
      onEnvelope: (envelope) => {
        activeConversationStore.getState().ingestEvent(sessionId, envelope);
        const proj = activeConversationStore.getState().bySession[sessionId];
        if (proj?.recovery !== undefined) void handleRecovery(sessionId, runtime, limit);
      },
    },
  });
}

async function handleRecovery(
  sessionId: string,
  runtime: SessionRuntime,
  limit?: number,
): Promise<void> {
  const proj = activeConversationStore.getState().bySession[sessionId];
  if (proj?.recovery === undefined) return;
  // Freeze incremental apply, close the stream, and re-page native authority. A repage and a reset
  // both re-hydrate; the difference is only diagnostic (reset carries a protocol-fault reason).
  runtime.controller?.stop();
  runtime.controller = null;
  const generation = runtime.generation;
  const page = await fetchConversationPage(
    sessionId,
    runtime.epoch,
    { limit },
    runtime.base,
    runtime.fetchImpl,
  );
  if (runtime.disposed || runtime.generation !== generation) return;
  if (!page.ok) {
    activeConversationStore.getState().failStream(sessionId, page.error);
    return;
  }
  // applyInitialPage clears the recovery + fault and re-establishes the resume cursor; then resume.
  activeConversationStore.getState().applyPage(sessionId, page.page, "initial");
  const cleared = activeConversationStore.getState().bySession[sessionId];
  if (cleared !== undefined) {
    activeConversationStore.setState((state) => ({
      bySession: { ...state.bySession, [sessionId]: clearRecovery(cleared) },
    }));
  }
  startStream(sessionId, runtime, limit);
}

/** Begin (or restart) a session's live projection: hydrate a page, then open the resumable stream. */
export function connectConversation(
  sessionId: string,
  epoch: string,
  options: ConnectOptions = {},
): void {
  const existing = runtimeBySession.get(sessionId);
  if (existing !== undefined) {
    existing.controller?.stop();
    existing.disposed = true;
  }
  const runtime: SessionRuntime = {
    epoch,
    base: options.base ?? "",
    fetchImpl: options.fetchImpl ?? fetch,
    eventSourceCtor: options.eventSourceCtor,
    controller: null,
    generation: (existing?.generation ?? 0) + 1,
    disposed: false,
  };
  runtimeBySession.set(sessionId, runtime);
  enforceLru(sessionId);
  void hydrateAndStream(sessionId, runtime, options.limit);
}

export function disconnectConversation(sessionId: string): void {
  const runtime = runtimeBySession.get(sessionId);
  if (runtime === undefined) return;
  runtime.disposed = true;
  runtime.controller?.stop();
  runtimeBySession.delete(sessionId);
}

/** Fetch and prepend one older page (accessible-paging / infinite-older). */
export async function loadOlderConversation(sessionId: string, limit?: number): Promise<void> {
  const runtime = runtimeBySession.get(sessionId);
  const proj = activeConversationStore.getState().bySession[sessionId];
  if (runtime === undefined || proj === undefined || !proj.hasOlder || proj.olderCursor === null) {
    return;
  }
  const page = await fetchConversationPage(
    sessionId,
    runtime.epoch,
    { before: proj.olderCursor, limit },
    runtime.base,
    runtime.fetchImpl,
  );
  if (!page.ok || runtime.disposed) return;
  activeConversationStore.getState().applyPage(sessionId, page.page, "older");
}

function enforceLru(focused: string): void {
  const order = activeConversationStore.getState().touchOrder;
  const keep = new Set([focused, ...order.slice(0, LRU_LIMIT - 1)]);
  for (const id of order) {
    if (!keep.has(id) && !runtimeBySession.has(id)) {
      activeConversationStore.getState().evict(id);
    }
  }
}

export function identityOf(sessionId: string): ActiveConversationRef | undefined {
  return activeConversationStore.getState().bySession[sessionId]?.identity;
}
