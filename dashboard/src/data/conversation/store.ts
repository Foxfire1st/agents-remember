// Reconstructable active-conversation store (design §11.1, §11.3). It holds ONLY a browser
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
  requestAgentHistory,
  type AgentHistoryOutcome,
  type AgentHistoryResult,
  type FetchLike,
  type PageResult,
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

// Exported so the React keep-alive pool (ChatsStageBody) bounds its mounted surfaces by the
// SAME limit — a surface is never kept alive for a session the data layer has already evicted.
export const LRU_LIMIT = 6;
// A normal surface can focus only one child at a time, but the exported orchestration function is
// callable by multiple mounted consumers. Bound its per-session bookkeeping so an abandoned or
// hostile caller cannot turn selected-child hydration into an unbounded browser store.
export const AGENT_HISTORY_CHILD_LIMIT = 64;

export type AgentHistoryLoadState =
  | { phase: "loading" }
  | { phase: "ready"; outcome: AgentHistoryOutcome }
  | { phase: "failed"; error: ConversationRouteError; code?: string };

export interface ActiveConversationState {
  bySession: Record<string, ActiveConversationProjection>;
  /** The server's typed reason for a failed page/reconnect, threaded to the banner (§14.5). */
  errorBySession: Record<string, ConversationRouteError | null>;
  /** The operator's timeline focus per session: absent = the parent
      conversation; an agentId = that sub-agent's lane. Deliberately NOT inside `bySession`: it
      survives LRU eviction with the keep-warm runtime, and the surface recomputes it against the
      rehydrated projection's roster (effectiveAgentFocus) instead of re-applying it blindly. */
  agentFocusBySession: Record<string, string>;
  /** Selected-child acquisition state is scoped by session + child. It never changes the parent
      stream phase: a failed child backfill is visible and retryable without poisoning live view. */
  agentHistoryBySession: Record<string, Record<string, AgentHistoryLoadState>>;
  touchOrder: string[];
  applyPage: (sessionId: string, page: ConversationPage, mode: "initial" | "older") => void;
  ingestEvent: (sessionId: string, envelope: ConversationEventEnvelope) => void;
  setStreamPhase: (sessionId: string, phase: ActiveConversationProjection["stream"]) => void;
  setAgentFocus: (sessionId: string, agentId: string | null) => void;
  setAgentHistoryState: (
    sessionId: string,
    agentId: string,
    state: AgentHistoryLoadState | null,
  ) => void;
  failStream: (sessionId: string, error: ConversationRouteError | null) => void;
  evict: (sessionId: string) => void;
  reset: () => void;
}

function touch(order: string[], sessionId: string): string[] {
  return [sessionId, ...order.filter((id) => id !== sessionId)];
}

export const activeConversationStore = createStore<ActiveConversationState>((set) => ({
  bySession: {},
  errorBySession: {},
  agentFocusBySession: {},
  agentHistoryBySession: {},
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

  // null clears the focus (back to the parent conversation). The entry is keyed OUTSIDE the
  // projection so an LRU eviction keeps the operator's place; the surface revalidates it against
  // the next roster instead of trusting it.
  setAgentFocus: (sessionId, agentId) =>
    set((state) => {
      const agentFocusBySession = { ...state.agentFocusBySession };
      if (agentId === null) delete agentFocusBySession[sessionId];
      else agentFocusBySession[sessionId] = agentId;
      return { agentFocusBySession };
    }),

  setAgentHistoryState: (sessionId, agentId, next) =>
    set((state) => {
      const sessionState = { ...(state.agentHistoryBySession[sessionId] ?? {}) };
      delete sessionState[agentId];
      if (next !== null) sessionState[agentId] = next;
      while (Object.keys(sessionState).length > AGENT_HISTORY_CHILD_LIMIT) {
        const oldest = Object.keys(sessionState)[0];
        if (oldest === undefined) break;
        delete sessionState[oldest];
      }
      const agentHistoryBySession = { ...state.agentHistoryBySession };
      if (Object.keys(sessionState).length === 0) delete agentHistoryBySession[sessionId];
      else agentHistoryBySession[sessionId] = sessionState;
      return { agentHistoryBySession };
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

  evict: (sessionId) =>
    set((state) => {
      if (state.bySession[sessionId] === undefined) return state;
      const bySession = { ...state.bySession };
      delete bySession[sessionId];
      return { bySession, touchOrder: state.touchOrder.filter((id) => id !== sessionId) };
    }),

  reset: () => {
    scrollMemoryBySession.clear();
    set({
      bySession: {},
      errorBySession: {},
      agentFocusBySession: {},
      agentHistoryBySession: {},
      touchOrder: [],
    });
  },
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
  /** Consecutive stream attempts that errored WITHOUT opening (reset by any open). */
  openFailures: number;
  /** Dead-stream re-pages since the last successful open (bounds the recover loop). */
  streamRecoveries: number;
  /** One external read per child at a time. HTTP timeout of one caller must not duplicate work. */
  agentHistoryInFlight: Map<string, Promise<AgentHistoryResult>>;
  /** Successful selected-child walks are projector-local and need no repeat within this runtime. */
  agentHistoryHydrated: Map<string, true>;
}

const runtimeBySession = new Map<string, SessionRuntime>();

export interface ConnectOptions {
  base?: string;
  fetchImpl?: FetchLike;
  eventSourceCtor?: EventSourceCtor;
  limit?: number;
}

// A fresh chat's first projection fetch races the session's own
// boot — the runner/bridge is not yet listening for a few hundred ms after the seat row appears. The
// stream auto-retry only covers a DROPPED live stream, not this first-connect race, so a
// healthy launch flashed the fail-loud "structured surface unavailable" alarm until a manual retry.
// The initial hydrate retries on a quiet `connecting` phase across a bounded window that covers
// boot; it escalates to the honest `projection-failed` alarm only after the window exhausts, so a
// genuinely broken session still fails loud — the strip is deferred, never masked.
// Playwright-measured: the fixed 8×400 ms window (~3.2 s) still exhausted BEFORE a
// fresh chat's bridge finished booting (submission-authority 503s for ~5–7 s after launch, slower
// on a cold daemon). The window is now a bounded 30 s wall-clock budget with exponential backoff —
// a healthy slow boot never reaches the alarm, the bound keeps a dead bridge honest.
// The hydrate fires right after the epoch resolve and its first
// attempt often still 503s while the bridge finishes, so it shares the boot-quantization cost — the
// coarse 400→2500 ms backoff could sit up to ~2.5 s stale on a ready bridge. It is aligned with the
// epoch-resolve poll in ChatsStageBody: start fine, cap low (the page fetch is cheap), discover
// readiness within ≤1 s.
const INITIAL_CONNECT_WINDOW_MS = 30_000;
const INITIAL_CONNECT_RETRY_MS = 250;
const INITIAL_CONNECT_RETRY_MAX_MS = 1_000;

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

// Only a TRANSIENT first-connect failure is the launch/boot race: a connection refused before the
// runner is listening (httpStatus 0) or a 5xx while the bridge composes (the diagnosis saw the codex
// bridge answer 503 during boot). A 4xx — 409 epoch/cursor, 404 unknown session — is a real,
// terminal answer and must fail loud immediately, never masked behind a retry (fail-loud honesty).
function isTransientBootFailure(error: { httpStatus: number } | null): boolean {
  if (error === null) return true; // a null error is a bare transport drop
  return error.httpStatus === 0 || error.httpStatus >= 500;
}

// Fetch a native page, tolerating a TRANSIENT boot-race (a 503 while the bridge composes, or a bare
// connection-refused) by retrying quietly across the bounded boot window with exponential backoff.
// Shared by the initial hydrate AND the dead-stream re-page so a recomposing bridge gets the
// SAME grace on both paths. Returns the ok page; the terminal (non-transient 4xx) or window-exhausted
// failure the caller must surface loud; or null when the runtime was disposed/superseded mid-flight.
async function fetchPageAcrossBootWindow(
  sessionId: string,
  runtime: SessionRuntime,
  limit: number | undefined,
  generation: number,
): Promise<PageResult | null> {
  const startedAt = Date.now();
  for (let attempt = 1; ; attempt += 1) {
    const page = await fetchConversationPage(
      sessionId,
      runtime.epoch,
      { limit },
      runtime.base,
      runtime.fetchImpl,
    );
    if (runtime.disposed || runtime.generation !== generation) return null;
    if (page.ok) return page;
    const elapsed = Date.now() - startedAt;
    if (!isTransientBootFailure(page.error) || elapsed >= INITIAL_CONNECT_WINDOW_MS) return page;
    // Transient boot race: give the bridge time to come up, then retry from the same window.
    await delay(
      Math.min(
        INITIAL_CONNECT_RETRY_MS * 2 ** (attempt - 1),
        INITIAL_CONNECT_RETRY_MAX_MS,
        INITIAL_CONNECT_WINDOW_MS - elapsed,
      ),
    );
    if (runtime.disposed || runtime.generation !== generation) return null;
  }
}

async function hydrateAndStream(sessionId: string, runtime: SessionRuntime, limit?: number): Promise<void> {
  const generation = runtime.generation;
  // Stay on the quiet `connecting` phase while the boot window retries a transient first fetch.
  activeConversationStore.getState().setStreamPhase(sessionId, "connecting");
  const page = await fetchPageAcrossBootWindow(sessionId, runtime, limit, generation);
  if (page === null) return;
  if (page.ok) {
    activeConversationStore.getState().applyPage(sessionId, page.page, "initial");
    startStream(sessionId, runtime, limit);
    return;
  }
  // A hard failure, or the boot window is exhausted: fail loud with the server's reason.
  activeConversationStore.getState().failStream(sessionId, page.error);
}

// Two consecutive unopened attempts mean the resume request itself is being REJECTED (the
// live case: a stale signed cursor after a daemon restart → 400 cursor-invalid, which EventSource
// can only surface as an unopened error) — retrying the same cursor can never succeed, so the
// store re-pages for a fresh server-minted cursor. Bounded: after this many re-pages without an
// intervening successful open, the projection fails LOUD instead of looping quietly.
const STREAM_OPEN_FAILURES_BEFORE_REPAGE = 2;
const MAX_DEAD_STREAM_RECOVERIES = 3;

function startStream(sessionId: string, runtime: SessionRuntime, limit?: number): void {
  runtime.controller?.stop();
  runtime.controller = openConversationStream({
    sessionId,
    epoch: runtime.epoch,
    base: runtime.base,
    eventSourceCtor: runtime.eventSourceCtor,
    getResumeCursor: () => activeConversationStore.getState().bySession[sessionId]?.eventCursor ?? null,
    handlers: {
      onOpen: () => {
        runtime.openFailures = 0;
        runtime.streamRecoveries = 0;
        activeConversationStore.getState().setStreamPhase(sessionId, "live");
      },
      onDisconnect: () => activeConversationStore.getState().setStreamPhase(sessionId, "reconnecting"),
      onOpenFailure: () => {
        runtime.openFailures += 1;
        if (runtime.openFailures < STREAM_OPEN_FAILURES_BEFORE_REPAGE) return;
        runtime.openFailures = 0;
        if (runtime.streamRecoveries >= MAX_DEAD_STREAM_RECOVERIES) {
          runtime.controller?.stop();
          runtime.controller = null;
          activeConversationStore.getState().failStream(sessionId, null);
          return;
        }
        runtime.streamRecoveries += 1;
        void repageAndResume(sessionId, runtime, limit);
      },
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
  await repageAndResume(sessionId, runtime, limit);
}

// Freeze incremental apply, close the stream, and re-page native authority. A repage and a reset
// both re-hydrate; the difference is only diagnostic (reset carries a protocol-fault reason). Also
// the dead-stream escalation path: the fresh page re-establishes a fresh server-minted resume
// cursor, replacing one a restarted daemon no longer recognizes.
//
// The re-page shares hydrateAndStream's transient-boot tolerance. A daemon restart that dropped
// the stream also briefly 503s the PAGE route while the bridge recomposes; failing loud on that blip
// (as the first cut did) is inconsistent with the initial hydrate and contradicts the dead-stream
// intent in stream.ts. So a transient page failure retries quietly inside the boot window and does
// NOT burn a recovery; only a genuinely terminal (4xx) answer or an exhausted window is a FAILED
// recovery attempt — it counts toward the same MAX_DEAD_STREAM_RECOVERIES cap the never-opened path
// uses, and only at the cap does the honest loud banner land (never a silent, endless re-page).
async function repageAndResume(
  sessionId: string,
  runtime: SessionRuntime,
  limit?: number,
): Promise<void> {
  runtime.controller?.stop();
  runtime.controller = null;
  const generation = runtime.generation;
  for (;;) {
    const page = await fetchPageAcrossBootWindow(sessionId, runtime, limit, generation);
    if (page === null) return;
    if (page.ok) {
      // applyInitialPage clears the recovery + fault and re-establishes the resume cursor; then resume.
      activeConversationStore.getState().applyPage(sessionId, page.page, "initial");
      const cleared = activeConversationStore.getState().bySession[sessionId];
      if (cleared !== undefined) {
        activeConversationStore.setState((state) => ({
          bySession: { ...state.bySession, [sessionId]: clearRecovery(cleared) },
        }));
      }
      startStream(sessionId, runtime, limit);
      return;
    }
    // A terminal (4xx) answer or an exhausted boot window: a FAILED recovery attempt. Past the cap
    // fail loud with the server's reason; else count it and re-attempt (matching the never-opened
    // escalation's bound). A transient boot 503 was already retried inside the helper and never
    // reaches here, so it can never burn the budget.
    if (runtime.streamRecoveries >= MAX_DEAD_STREAM_RECOVERIES) {
      activeConversationStore.getState().failStream(sessionId, page.error);
      return;
    }
    runtime.streamRecoveries += 1;
  }
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
    openFailures: 0,
    streamRecoveries: 0,
    agentHistoryInFlight: new Map(),
    agentHistoryHydrated: new Map(),
  };
  runtimeBySession.set(sessionId, runtime);
  activeConversationStore.setState((state) => {
    const agentHistoryBySession = { ...state.agentHistoryBySession };
    delete agentHistoryBySession[sessionId];
    return { agentHistoryBySession };
  });
  enforceLru(sessionId);
  void hydrateAndStream(sessionId, runtime, options.limit);
}

export function disconnectConversation(sessionId: string): void {
  const runtime = runtimeBySession.get(sessionId);
  if (runtime === undefined) return;
  runtime.disposed = true;
  runtime.controller?.stop();
  runtimeBySession.delete(sessionId);
  activeConversationStore.setState((state) => {
    const agentHistoryBySession = { ...state.agentHistoryBySession };
    delete agentHistoryBySession[sessionId];
    return { agentHistoryBySession };
  });
  // The scroll memory dies with the projection — a rehydrated session opens at the latest
  // (the first-open contract), never at a pixel offset measured against dropped content.
  scrollMemoryBySession.delete(sessionId);
}

// ── Keep-warm focus switching ────────────────────────────────────────────
// The stage used to disconnect on every focus switch, so switching back ALWAYS paid a full
// epoch-resolve + hydrate + stream-open ("connecting…" on every refocus).
// Recently-focused conversations now stay connected; the LRU is the ONLY passive evictor and
// session termination the only active one.

/** True when the session already has a live runtime + projection worth reusing on refocus. */
export function hasWarmConversation(sessionId: string): boolean {
  const runtime = runtimeBySession.get(sessionId);
  if (runtime === undefined || runtime.disposed) return false;
  const proj = activeConversationStore.getState().bySession[sessionId];
  return proj !== undefined && proj.stream !== "projection-failed";
}

/** Bump refocus recency and re-run the LRU without touching the live stream. */
export function touchConversation(sessionId: string): void {
  activeConversationStore.setState((state) => ({
    touchOrder: touch(state.touchOrder, sessionId),
  }));
  enforceLru(sessionId);
}

/** Trigger selected/on-demand child history without replacing the parent page or live stream. */
export async function hydrateAgentConversation(
  sessionId: string,
  agentId: string,
): Promise<AgentHistoryResult | null> {
  const runtime = runtimeBySession.get(sessionId);
  if (runtime === undefined || runtime.disposed) return null;
  if (runtime.agentHistoryHydrated.delete(agentId)) {
    runtime.agentHistoryHydrated.set(agentId, true);
    return {
      ok: true,
      outcome: { status: "already-hydrated", agentId },
    };
  }
  const existing = runtime.agentHistoryInFlight.get(agentId);
  if (existing !== undefined) return existing;
  if (runtime.agentHistoryInFlight.size >= AGENT_HISTORY_CHILD_LIMIT) {
    const result: AgentHistoryResult = {
      ok: false,
      error: {
        status: "local-resource-limit",
        detail: "too many selected child history requests are already in progress",
        httpStatus: 0,
      },
    };
    activeConversationStore
      .getState()
      .setAgentHistoryState(sessionId, agentId, {
        phase: "failed",
        error: result.error,
      });
    return result;
  }

  activeConversationStore
    .getState()
    .setAgentHistoryState(sessionId, agentId, { phase: "loading" });
  const request = (async (): Promise<AgentHistoryResult> => {
    try {
      const result = await requestAgentHistory(
        sessionId,
        runtime.epoch,
        agentId,
        runtime.base,
        runtime.fetchImpl,
      );
      if (runtime.disposed || runtimeBySession.get(sessionId) !== runtime) return result;
      if (
        result.ok &&
        (result.outcome.status === "hydrated" ||
          result.outcome.status === "already-hydrated")
      ) {
        runtime.agentHistoryHydrated.delete(agentId);
        runtime.agentHistoryHydrated.set(agentId, true);
        if (runtime.agentHistoryHydrated.size > AGENT_HISTORY_CHILD_LIMIT) {
          const oldest = runtime.agentHistoryHydrated.keys().next().value;
          if (oldest !== undefined) runtime.agentHistoryHydrated.delete(oldest);
        }
        activeConversationStore
          .getState()
          .setAgentHistoryState(sessionId, agentId, {
            phase: "ready",
            outcome: result.outcome,
          });
      } else if (result.ok) {
        activeConversationStore
          .getState()
          .setAgentHistoryState(sessionId, agentId, {
            phase: "failed",
            error: {
              status: result.outcome.status,
              detail:
                result.outcome.detail ??
                "selected child history is unavailable; retry the child to try again",
              httpStatus: 200,
            },
            ...(result.outcome.code !== undefined ? { code: result.outcome.code } : {}),
          });
      } else {
        activeConversationStore
          .getState()
          .setAgentHistoryState(sessionId, agentId, {
            phase: "failed",
            error: result.error,
          });
      }
      return result;
    } finally {
      runtime.agentHistoryInFlight.delete(agentId);
    }
  })();
  runtime.agentHistoryInFlight.set(agentId, request);
  return request;
}

// ── Keep-warm scroll memory ─────────────────────────────────────────────
// Switching cockpit VIEWS (Chats ↔ Operations ↔ Files) reopens the
// chat at the START of the conversation. The stage's keep-alive pool hides unfocused chats with
// visibility:hidden (which preserves layout — and the scroll offset — across CHAT switches), but
// the cockpit hides the whole Chats layer with display:none on a view switch, and display:none
// destroys the scroll container's box: the browser resets scrollTop to 0. The timeline therefore
// reports its position on every scroll and reads it back on re-show. Like the runtimes above this
// is view-runtime, never conversation authority, so it lives OUTSIDE the reactive state — a write
// per scroll event must not churn the store.

export interface ConversationScrollMemory {
  /** The exact pixel offset, restored verbatim for a chat left mid-conversation or at the top. */
  scrollTop: number;
  /** True when left within the timeline's bottom-follow band: on re-show the chat lands at the
      CURRENT end (catching up with items ingested while away) and the follow re-arms. */
  atBottom: boolean;
}

const scrollMemoryBySession = new Map<string, ConversationScrollMemory>();

/** Record the timeline's current position (called from the scroll listener — cheap, no react state). */
export function rememberConversationScroll(
  sessionId: string,
  memory: ConversationScrollMemory,
): void {
  scrollMemoryBySession.set(sessionId, memory);
}

/** The last remembered position, or undefined for a first-ever open (which opens at the latest). */
export function readConversationScroll(
  sessionId: string,
): ConversationScrollMemory | undefined {
  return scrollMemoryBySession.get(sessionId);
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
  // Rank focused as the most-recent, then the rest of touchOrder by recency, and keep the top
  // LRU_LIMIT. Deduping focused here makes the bound EXACTLY LRU_LIMIT for both callers regardless
  // of whether they pre-inserted it: connectConversation calls in BEFORE the imminent applyPage
  // adds focused, so it reserves a slot; touchConversation prepends focused FIRST, so a naive
  // `[focused, ...slice(0, LRU_LIMIT - 1)]` double-counted it and collapsed the keep set to 5 —
  // silently disconnecting+evicting the 6th warm chat on every refocus (regressing the keep-warm guarantee).
  const ranked = [focused, ...order.filter((id) => id !== focused)];
  const keep = new Set(ranked.slice(0, LRU_LIMIT));
  for (const id of order) {
    if (keep.has(id)) continue;
    // Under keep-warm, evicted sessions may still hold a live runtime — the LRU now owns
    // disconnecting it (the stage no longer disconnects on focus switch). Refocus rehydrates.
    disconnectConversation(id);
    activeConversationStore.getState().evict(id);
  }
}

export function identityOf(sessionId: string): ActiveConversationRef | undefined {
  return activeConversationStore.getState().bySession[sessionId]?.identity;
}
