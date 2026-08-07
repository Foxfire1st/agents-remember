// Reconstructable conversation-library store (design §11.2, §11.3, R4). Holds paged list query
// state, read-only preview pages, the selected preview identity, and the caller-stable open
// operation phase/revision. It NEVER marks a row active — only exact catalog evidence in the live
// session store can do that, and this store surfaces `arSessionId` for focus ONLY when the open
// operation is `phase="opened" && outcome="opened"`. No durable browser index; reload reconstructs
// from server authority.

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import type { FetchLike } from "../conversation/client";
import type { HarnessId } from "../conversation/types";
import {
  fetchLibraryList,
  fetchLibraryRead,
  openConversation,
  openReconcile,
  openStatus,
  type ListQuery,
  type OpenResult,
} from "./client";
import type {
  ConversationLibraryRow,
  HistoricalConversationPage,
  LibraryConversationKey,
  LibraryListCursor,
  LibraryRouteError,
  OpenConversationOperation,
} from "./types";

export interface LibraryListView {
  harnessId: HarnessId;
  cwd?: string;
  rows: ConversationLibraryRow[];
  nextCursor: LibraryListCursor | null;
  canonicalProjectScope?: string;
  /** The page's sub-agent availability note — the exact native reason when
      agent conversations are (partially) unavailable, rendered verbatim, never silently absent. */
  agentsNote?: string | null;
  loading: boolean;
  error?: string;
}

export interface LibraryPreview {
  key: LibraryConversationKey;
  page?: HistoricalConversationPage;
  loading: boolean;
  error?: string;
}

export interface OpenTracker {
  requestId: string;
  harnessId: HarnessId;
  conversationKey: LibraryConversationKey;
  operation?: OpenConversationOperation;
  error?: LibraryRouteError;
  /** True from dispatch until the first response resolves — blocks a double-dispatch (F6c). */
  dispatching: boolean;
  /** True after the poll budget is spent while still non-terminal — offer a manual reconcile (F6a). */
  pollsExhausted: boolean;
  /** True only when phase==="opened" && outcome==="opened" — the sole gate to focus a new row. */
  openedForFocus: boolean;
}

export interface ConversationLibraryState {
  list?: LibraryListView;
  preview?: LibraryPreview;
  selectedKey: LibraryConversationKey | null;
  open?: OpenTracker;
  setList: (list: LibraryListView | undefined) => void;
  setPreview: (preview: LibraryPreview | undefined) => void;
  setSelectedKey: (key: LibraryConversationKey | null) => void;
  setOpen: (open: OpenTracker | undefined) => void;
  reset: () => void;
}

export const conversationLibraryStore = createStore<ConversationLibraryState>((set) => ({
  selectedKey: null,
  setList: (list) => set({ list }),
  setPreview: (preview) => set({ preview }),
  setSelectedKey: (selectedKey) => set({ selectedKey }),
  setOpen: (open) => set({ open }),
  reset: () => set({ list: undefined, preview: undefined, selectedKey: null, open: undefined }),
}));

export const useConversationLibrary = <T>(
  selector: (state: ConversationLibraryState) => T,
): T => useStore(conversationLibraryStore, selector);

// ── Orchestration ────────────────────────────────────────────────────────────────────────────

export interface LibraryDeps {
  base?: string;
  fetchImpl?: FetchLike;
  setTimeoutImpl?: (fn: () => void, ms: number) => number;
}

function listSeed(
  harnessId: HarnessId,
  query: ListQuery,
  previous: LibraryListView | undefined,
  appending: boolean,
): LibraryListView {
  return {
    harnessId,
    cwd: query.cwd,
    rows: appending ? (previous?.rows ?? []) : [],
    nextCursor: previous?.nextCursor ?? null,
    canonicalProjectScope: previous?.canonicalProjectScope,
    agentsNote: previous?.agentsNote ?? null,
    loading: true,
    error: undefined,
  };
}

function listFailure(
  harnessId: HarnessId,
  query: ListQuery,
  previous: LibraryListView | undefined,
  appending: boolean,
): LibraryListView {
  return {
    harnessId,
    cwd: query.cwd,
    rows: appending ? (previous?.rows ?? []) : [],
    nextCursor: appending ? (previous?.nextCursor ?? null) : null,
    agentsNote: previous?.agentsNote ?? null,
    loading: false,
    error: "history unavailable for this harness",
  };
}

function listSuccess(
  harnessId: HarnessId,
  query: ListQuery,
  previous: LibraryListView | undefined,
  page: NonNullable<Awaited<ReturnType<typeof fetchLibraryList>>>,
  appending: boolean,
): LibraryListView {
  const merged = appending ? [...(previous?.rows ?? []), ...page.rows] : [...page.rows];
  return {
    harnessId,
    cwd: query.cwd,
    rows: merged,
    nextCursor: page.nextCursor,
    canonicalProjectScope: page.scope.canonicalProjectScope,
    // The freshest page's note wins (it describes the query's current agent availability).
    agentsNote: page.agentsNote ?? null,
    loading: false,
  };
}

export async function loadLibraryList(
  harnessId: HarnessId,
  query: ListQuery,
  deps: LibraryDeps = {},
): Promise<void> {
  const store = conversationLibraryStore.getState();
  const previous = store.list;
  const appending = query.cursor != null && previous?.harnessId === harnessId;
  store.setList(listSeed(harnessId, query, previous, appending));
  const page = await fetchLibraryList(harnessId, query, deps.base ?? "", deps.fetchImpl ?? fetch);
  const current = conversationLibraryStore.getState();
  if (page === null) {
    current.setList(listFailure(harnessId, query, previous, appending));
    return;
  }
  current.setList(listSuccess(harnessId, query, previous, page, appending));
}

export async function loadLibraryPreview(
  harnessId: HarnessId,
  key: LibraryConversationKey,
  deps: LibraryDeps = {},
): Promise<void> {
  const store = conversationLibraryStore.getState();
  store.setSelectedKey(key);
  store.setPreview({ key, loading: true });
  const page = await fetchLibraryRead(harnessId, key, {}, deps.base ?? "", deps.fetchImpl ?? fetch);
  const current = conversationLibraryStore.getState();
  if (current.selectedKey !== key) return; // selection moved on — drop the stale preview
  if (page === null) {
    current.setPreview({ key, loading: false, error: "preview unavailable" });
    return;
  }
  current.setPreview({ key, page, loading: false });
}

const OPEN_POLL_MS = 1200;
const OPEN_POLL_LIMIT = 25;

function applyOpen(tracker: OpenTracker, result: OpenResult): OpenTracker {
  if (!result.ok) {
    // Keep the requestId (F6b): the next attempt reconciles under the SAME id, never a fresh one.
    return { ...tracker, error: result.error, dispatching: false, openedForFocus: false };
  }
  const operation = result.operation;
  return {
    ...tracker,
    operation,
    error: undefined,
    dispatching: false,
    openedForFocus: operation.phase === "opened" && operation.outcome === "opened",
  };
}

function isTerminalOpen(operation: OpenConversationOperation): boolean {
  // pending / timeout-unknown keep polling under the SAME requestId; everything else is terminal.
  return operation.outcome !== "pending" && operation.outcome !== "timeout-unknown";
}

function openStillCurrent(requestId: string): boolean {
  return conversationLibraryStore.getState().open?.requestId === requestId;
}

function shouldReconcile(reconcileFirst: boolean, polls: number): boolean {
  return reconcileFirst ? polls === 1 || polls % 5 === 0 : polls % 5 === 0;
}

function pollBudgetSpent(result: OpenResult, polls: number): boolean {
  return result.ok && !isTerminalOpen(result.operation) && polls >= OPEN_POLL_LIMIT;
}

/**
 * Begin an exact-open under a caller-stable requestId. Resolves the operation; while pending or
 * timeout-unknown it re-drives open-status/open-reconcile under the same id until terminal. Never
 * mints a replacement id and never focuses — the caller focuses only when `openedForFocus` is true.
 */
export async function beginOpen(
  harnessId: HarnessId,
  conversationKey: LibraryConversationKey,
  requestId: string,
  expectedIdentityDigest: string,
  extra: { cwd?: string; launchContext?: { leafKey?: string; seatRole?: string } } = {},
  deps: LibraryDeps = {},
): Promise<void> {
  const store = conversationLibraryStore.getState();
  const base = deps.base ?? "";
  const fetchImpl = deps.fetchImpl ?? fetch;
  const schedule = pollScheduler(deps);
  // busy from dispatch (F6c): a second click cannot mint a second open into the TOCTOU window.
  let tracker: OpenTracker = {
    requestId,
    harnessId,
    conversationKey,
    dispatching: true,
    pollsExhausted: false,
    openedForFocus: false,
  };
  store.setOpen(tracker);

  const first = await openConversation(
    harnessId,
    conversationKey,
    { requestId, expectedIdentityDigest, cwd: extra.cwd, launchContext: extra.launchContext },
    base,
    fetchImpl,
  );
  tracker = applyOpen(tracker, first);
  conversationLibraryStore.getState().setOpen(tracker);
  if (!first.ok || isTerminalOpen(first.operation)) return;
  runOpenPolls(harnessId, conversationKey, requestId, tracker, base, fetchImpl, schedule);
}

/**
 * Re-drive a stalled or transport-failed open under the SAME requestId (F6a/F6b) — never a fresh id.
 * Used by the poll-exhaustion "reconcile" action and by re-attempting after a transport failure.
 */
export function reconcileOpen(
  harnessId: HarnessId,
  conversationKey: LibraryConversationKey,
  requestId: string,
  deps: LibraryDeps = {},
): void {
  const base = deps.base ?? "";
  const fetchImpl = deps.fetchImpl ?? fetch;
  const schedule = pollScheduler(deps);
  const existing = conversationLibraryStore.getState().open;
  const tracker: OpenTracker = {
    requestId,
    harnessId,
    conversationKey,
    operation: existing?.operation,
    error: undefined,
    dispatching: true,
    pollsExhausted: false,
    openedForFocus: false,
  };
  conversationLibraryStore.getState().setOpen(tracker);
  runOpenPolls(harnessId, conversationKey, requestId, tracker, base, fetchImpl, schedule, true);
}

function pollScheduler(deps: LibraryDeps): (fn: () => void, ms: number) => number {
  return (
    deps.setTimeoutImpl ??
    ((fn: () => void, ms: number) => globalThis.setTimeout(fn, ms) as unknown as number)
  );
}

function runOpenPolls(
  harnessId: HarnessId,
  conversationKey: LibraryConversationKey,
  requestId: string,
  initial: OpenTracker,
  base: string,
  fetchImpl: typeof fetch,
  schedule: (fn: () => void, ms: number) => number,
  reconcileFirst = false,
): void {
  let tracker = initial;
  let polls = 0;
  const poll = async (): Promise<void> => {
    if (!openStillCurrent(requestId)) return; // superseded
    polls += 1;
    // Periodically escalate to reconcile for ambiguous outcomes (or immediately on a re-drive).
    const reconcile = shouldReconcile(reconcileFirst, polls);
    const result = reconcile
      ? await openReconcile(harnessId, conversationKey, requestId, base, fetchImpl)
      : await openStatus(harnessId, conversationKey, requestId, base, fetchImpl);
    if (!openStillCurrent(requestId)) return;
    tracker = applyOpen(tracker, result);
    if (pollBudgetSpent(result, polls)) {
      // Poll budget spent while still non-terminal: stop and offer a manual reconcile (F6a).
      tracker = { ...tracker, pollsExhausted: true };
      conversationLibraryStore.getState().setOpen(tracker);
      return;
    }
    conversationLibraryStore.getState().setOpen(tracker);
    if (result.ok && !isTerminalOpen(result.operation)) {
      schedule(() => void poll(), OPEN_POLL_MS);
    }
  };
  schedule(() => void poll(), OPEN_POLL_MS);
}
