import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import {
  bracketedPaste,
  openTerminalSession,
  pasteAndConfirm,
  sanitizeForInjection,
  submitAndConfirm,
  type TerminalConnection,
  type TerminalOpenResult,
  type TerminalOpenKind,
  type TerminalSessionInfo,
  type TerminalSessionStatus,
  type HarnessAcceptanceState,
  type HarnessActivityState,
  type HarnessControlState,
} from "./terminal";
import type { TaskDocumentRef } from "../types/terminalCatalog";

export { terminalOpenFailureMessage } from "./terminal";

// The open terminal/chat sessions (slice 6e hardening): the session registry as a module-level store
// — shared, testable client state, the same pattern as the observer projection store (`data/store.ts`)
// but deliberately kept separate from it (ephemeral UI state, not projected truth). Terminal
// *persistence* across cockpit refresh/view/session switches is owned by the backend catalog + tmux.
// The store only has to hold which sessions exist and which live one owns the shared action route;
// the canonical Chats cockpit keeps PTYs mounted and controls its richer inspection focus separately.
export interface OpenSession {
  id: string;
  label: string;
  kind?: TerminalOpenKind;
  harness?: string;
  lifecycleId?: string;
  /** The canonical JSON-primary task document this seat occupies. */
  taskDocumentRef?: TaskDocumentRef;
  /** The AR_SPAWN_ROLE recorded at spawn — the Chats command-tree grouping key. */
  spawnRole?: string;
  /** The role occupying the task document; authoritative for grouping and seat identity. */
  seatRole?: string;
  status?: TerminalSessionStatus;
  createdAt?: string;
  landedAt?: string;
  landedReason?: string;
  landedEdge?: string;
  // Retirement provenance: surfaced on tooltips + the seat inspector.
  retiredAt?: string;
  retiredBySession?: string;
  retiredReason?: string;
  retiredEdge?: string;
  spawnedBySession?: string;
  spawnedByLifecycle?: string;
  structuralParentTaskDocumentRef?: TaskDocumentRef;
  structuralParentRole?: string;
  spawnedLabel?: string;
  /** The RESOLVED dispatch level (leaf|master|portfolio) + explicit-vs-default provenance. */
  spawnLevel?: string;
  spawnLevelSource?: string;
  // The settings-resolved model/effort pinned at launch — REQUESTED provenance, never proof of
  // the effective pair (evidence tiers live in sessionCockpitStore).
  resolvedModel?: string;
  resolvedEffort?: string;
  turnState?: string;
  turnStateChangedAt?: string;
  /**
   * The focused seat's conversation projection reports a live turn actively
   * streaming right now (fresher than the sweep-bounded `turnState`). Set only from the projection's
   * own status; `seatVisualState` prefers it over a lagging catalog `turn-ended`. Not from the catalog.
   */
  liveTurnWorking?: boolean;
  controlState?: HarnessControlState;
  controlProtocol?: string;
  controlActivity?: HarnessActivityState;
  controlAcceptance?: HarnessAcceptanceState;
  controlVendorSessionId?: string;
  controlPendingInteraction?: Record<string, unknown>;
  controlPendingInteractions?: Record<string, unknown>[];
  controlLastEventSequence?: number;
  controlRaw?: Record<string, unknown>;
  // Liveness probe evidence, mirrored for the freshness surfaces.
  livenessFailures?: number;
  livenessFirstFailedAt?: string;
  livenessLastFailedAt?: string;
  livenessEvidence?: string;
  exitEvidence?: string;
}

type SessionCatalogChangeReason = "create" | "terminate" | "task";

interface SessionCatalogChangeMessage {
  type: "terminal-catalog-changed";
  source: string;
  reason: SessionCatalogChangeReason;
  sessionId?: string;
}

const SESSION_CATALOG_CHANNEL = "ar-dashboard:terminal-catalog";
const TAB_SOURCE = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

function openCatalogChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  return new BroadcastChannel(SESSION_CATALOG_CHANNEL);
}

function isSessionCatalogChangeMessage(data: unknown): data is SessionCatalogChangeMessage {
  if (typeof data !== "object" || data === null) return false;
  const message = data as Partial<SessionCatalogChangeMessage>;
  return (
    message.type === "terminal-catalog-changed" &&
    typeof message.source === "string" &&
    (message.reason === "create" || message.reason === "terminate" || message.reason === "task") &&
    (message.sessionId === undefined || typeof message.sessionId === "string")
  );
}

export function notifySessionCatalogChanged(
  reason: SessionCatalogChangeReason,
  sessionId?: string,
): void {
  const channel = openCatalogChannel();
  if (!channel) return;
  channel.postMessage({
    type: "terminal-catalog-changed",
    source: TAB_SOURCE,
    reason,
    ...(sessionId ? { sessionId } : {}),
  });
  channel.close();
}

export function subscribeSessionCatalogChanges(
  callback: (reason: SessionCatalogChangeReason, sessionId?: string) => void,
): () => void {
  const channel = openCatalogChannel();
  if (!channel) return () => {};
  channel.onmessage = (event) => {
    if (!isSessionCatalogChangeMessage(event.data) || event.data.source === TAB_SOURCE) return;
    callback(event.data.reason, event.data.sessionId);
  };
  return () => channel.close();
}

interface SessionState {
  sessions: OpenSession[];
  activeId: string | null;
  /** Highest live ordinal, retained for coarse store inspection. */
  count: number;
  /** Append a session labelled with the lowest available `{prefix} {n}` and make it active. */
  add: (prefix: string, id: string, lifecycleId?: string) => void;
  /** Insert or replace a known server-owned session, optionally making it active. */
  upsert: (session: OpenSession, activate?: boolean) => void;
  /** Hydrate the store from server-owned session rows. */
  hydrate: (sessions: OpenSession[], preferredActiveId?: string | null) => void;
  /** Drop a local row; clear `activeId` if it was the one removed. */
  close: (id: string) => void;
  setStatus: (id: string, status: TerminalSessionStatus) => void;
  /**
   * Merge server-observed fields into one row (the seat-event reconciler). The poll stays
   * authoritative: anything patched here is confirmed or replaced by the next catalog hydrate.
   */
  patch: (id: string, partial: Partial<OpenSession>) => void;
  setActive: (id: string) => void;
  /** Attach a hosted session to one lifecycle; latest attachment owns that lifecycle route. */
  setLifecycle: (id: string, lifecycleId: string | null) => void;
  /**
   * Bind a hosted session to one canonical task document, or clear it (`null`). Advisory
   * uniqueness is scoped to the session's current role; the server remains the real arbiter.
   */
  setTask: (id: string, taskDocumentRef: TaskDocumentRef | null) => void;
  /**
   * Apply a server/catalog-authoritative task assignment after backend attach or hydrate.
   * Same-role local occupants of the destination seat are cleared because the catalog wins.
   */
  applyTaskAssignment: (
    id: string,
    taskDocumentRef: TaskDocumentRef | null,
    seatRole: string,
  ) => void;
}

/** A session's transport-role fallback: a plain shell is a TERMINAL, a harness is a CHAT. */
export type SessionRole = "chat" | "terminal";

/** Derive a session's role from its kind (mirrors the backend `role_for_kind`). */
export function sessionRole(session: Pick<OpenSession, "kind">): SessionRole {
  return session.kind === "terminal" ? "terminal" : "chat";
}

/** The role occupying a task binding; unbound rows fall back to origin provenance, then transport. */
export function sessionSeatRole(
  session: Pick<OpenSession, "kind" | "seatRole" | "spawnRole">,
): string {
  return session.seatRole ?? session.spawnRole ?? sessionRole(session);
}

/** Preselect only a declared/typed attach role; a legacy generic chat must be chosen explicitly. */
export function attachSeatRole(
  session: Pick<OpenSession, "kind" | "seatRole" | "spawnRole">,
): string | undefined {
  if (session.kind === "terminal") return "terminal";
  return (
    session.spawnRole ??
    (session.seatRole && session.seatRole !== "chat" ? session.seatRole : undefined)
  );
}

function clearLifecycle(session: OpenSession): OpenSession {
  const next = { ...session };
  delete next.lifecycleId;
  return next;
}

function clearTask(session: OpenSession): OpenSession {
  const next = { ...session };
  delete next.taskDocumentRef;
  return next;
}

function sameTaskDocument(
  left: TaskDocumentRef | null | undefined,
  right: TaskDocumentRef | null | undefined,
): boolean {
  return Boolean(left && right && left.repository === right.repository && left.path === right.path);
}

function inferOrdinal(label: string): number | null {
  const match = label.match(/\s(\d+)$/);
  return match ? Number(match[1]) : null;
}

function inferPrefix(label: string): string | null {
  const match = label.match(/^(.*)\s\d+$/);
  return match ? match[1] : null;
}

function maxOrdinal(labels: string[]): number {
  return labels.reduce((max, label) => {
    const ordinal = inferOrdinal(label);
    return ordinal === null ? max : Math.max(max, ordinal);
  }, 0);
}

function isLiveSession(session: OpenSession): boolean {
  return (session.status ?? "running") === "running";
}

/**
 * Field-level equality for one catalog-mapped row. Object-valued
 * fields (controlRaw / controlPendingInteraction) arrive as fresh references from every poll's
 * JSON.parse, so they compare by content; everything else is a primitive. Key ORDER never differs
 * between rows from the same mapping (`fromTerminalSessionInfo`), so a key-count + per-key walk
 * suffices.
 */
function sameSessionValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a !== null && b !== null && typeof a === "object" && typeof b === "object") {
    return JSON.stringify(a) === JSON.stringify(b);
  }
  return false;
}

function sameSessionRow(a: OpenSession, b: OpenSession): boolean {
  const keys = Object.keys(a) as (keyof OpenSession)[];
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((key) => sameSessionValue(a[key], b[key]));
}

function liveLabels(sessions: OpenSession[]): string[] {
  return sessions.filter(isLiveSession).map((session) => session.label);
}

function trackedOrdinal(sessions: OpenSession[]): number {
  return maxOrdinal(liveLabels(sessions));
}

function nextSessionLabel(prefix: string, sessions: OpenSession[]): string {
  const used = new Set<number>();
  for (const label of liveLabels(sessions)) {
    if (inferPrefix(label) !== prefix) continue;
    const ordinal = inferOrdinal(label);
    if (ordinal !== null) used.add(ordinal);
  }
  let ordinal = 1;
  while (used.has(ordinal)) ordinal += 1;
  return `${prefix} ${ordinal}`;
}

type SessionStoreSet = (fn: (state: SessionState) => Partial<SessionState>) => void;

function addSessionState(set: SessionStoreSet): Pick<SessionState, "add"> {
  return {
    add: (prefix, id, lifecycleId) =>
      set((state) => {
        const next = {
          id,
          label: nextSessionLabel(prefix, state.sessions),
          ...(lifecycleId ? { lifecycleId } : {}),
        };
        const sessions = [
          ...state.sessions.map((session) =>
            lifecycleId && session.lifecycleId === lifecycleId ? clearLifecycle(session) : session,
          ),
          next,
        ];
        return {
          count: trackedOrdinal(sessions),
          sessions,
          activeId: id,
        };
      }),
  };
}

function upsertSessionState(set: SessionStoreSet): Pick<SessionState, "upsert"> {
  return {
    upsert: (session, activate = true) =>
      set((state) => {
        const nextSessions = [
          ...state.sessions
            .filter((current) => current.id !== session.id)
            .map((current) =>
              session.lifecycleId && current.lifecycleId === session.lifecycleId
                ? clearLifecycle(current)
                : current,
            ),
          session,
        ];
        return {
          sessions: nextSessions,
          count: trackedOrdinal(nextSessions),
          activeId: activate ? session.id : state.activeId,
        };
      }),
  };
}

function resolveHydratedActiveId(
  live: OpenSession[],
  sessions: OpenSession[],
  preferredActiveId: string | null | undefined,
  state: SessionState,
): string | null {
  if (preferredActiveId && live.some((session) => session.id === preferredActiveId)) {
    return preferredActiveId;
  }
  if (state.activeId && live.some((session) => session.id === state.activeId)) {
    return state.activeId;
  }
  return live[0]?.id ?? sessions[0]?.id ?? null;
}

// Reconcile against the current rows instead of replacing them wholesale. The
// 2500 ms catalog poll is authoritative but usually byte-identical between beats; the old
// wholesale swap gave every row a fresh reference each beat, so the always-mounted (hidden)
// SessionsView re-rendered per beat (~150–200 ms measured on the Operations view). Reusing
// the previous object for each content-identical row keeps selector/memo identity, and a
// beat that changed NOTHING returns the same state — zustand then notifies nobody and an
// unchanged payload produces zero UI work. Semantics are untouched: any row whose content
// actually diverged from the catalog (incl. an unconfirmed seat-event pre-apply) is still
// replaced on the very next beat.
function reconcileHydrated(
  state: SessionState,
  sessions: OpenSession[],
  preferredActiveId: string | null | undefined,
): Partial<SessionState> {
  const live = sessions.filter(isLiveSession);
  const activeId = resolveHydratedActiveId(live, sessions, preferredActiveId, state);
  let rowsChanged = state.sessions.length !== sessions.length;
  const nextSessions = sessions.map((session, index) => {
    const current = state.sessions[index];
    if (current?.id === session.id && sameSessionRow(current, session)) {
      return current;
    }
    rowsChanged = true;
    return session;
  });
  const count = trackedOrdinal(nextSessions);
  if (!rowsChanged && state.activeId === activeId && state.count === count) return state;
  return {
    sessions: nextSessions,
    count,
    activeId,
  };
}

function hydrateSessionState(set: SessionStoreSet): Pick<SessionState, "hydrate"> {
  return {
    hydrate: (sessions, preferredActiveId) =>
      set((state) => reconcileHydrated(state, sessions, preferredActiveId)),
  };
}

function closeSessionState(set: SessionStoreSet): Pick<SessionState, "close"> {
  return {
    close: (id) =>
      set((state) => {
        const sessions = state.sessions.filter((session) => session.id !== id);
        return {
          sessions,
          count: trackedOrdinal(sessions),
          activeId: state.activeId === id ? null : state.activeId,
        };
      }),
  };
}

function statusSessionState(set: SessionStoreSet): Pick<SessionState, "setStatus"> {
  return {
    setStatus: (id, status) =>
      set((state) => {
        const sessions = state.sessions.map((session) =>
          session.id === id ? { ...session, status } : session,
        );
        return {
          sessions,
          count: trackedOrdinal(sessions),
          activeId:
            state.activeId === id && status !== "running"
              ? (state.sessions.find((session) => session.id !== id && isLiveSession(session))
                  ?.id ?? null)
              : state.activeId,
        };
      }),
  };
}

function patchSessionState(set: SessionStoreSet): Pick<SessionState, "patch"> {
  return {
    patch: (id, partial) =>
      set((state) => ({
        sessions: state.sessions.map((session) =>
          session.id === id ? { ...session, ...partial } : session,
        ),
      })),
  };
}

function lifecycleSessionState(set: SessionStoreSet): Pick<SessionState, "setLifecycle"> {
  return {
    setLifecycle: (id, lifecycleId) =>
      set((state) => ({
        sessions: state.sessions.map((session) => {
          if (session.id === id)
            return lifecycleId ? { ...session, lifecycleId } : clearLifecycle(session);
          if (lifecycleId && session.lifecycleId === lifecycleId) {
            return clearLifecycle(session);
          }
          return session;
        }),
      })),
  };
}

function taskSessionState(set: SessionStoreSet): Pick<SessionState, "setTask"> {
  return {
    setTask: (id, taskDocumentRef) =>
      set((state) => {
        if (taskDocumentRef) {
          // Advisory same-role guard; the server remains the real structural-seat arbiter.
          const role = sessionSeatRole(state.sessions.find((session) => session.id === id) ?? {});
          const owner = state.sessions.find(
            (session) =>
              session.id !== id &&
              sameTaskDocument(session.taskDocumentRef, taskDocumentRef) &&
              isLiveSession(session) &&
              sessionSeatRole(session) === role,
          );
          if (owner) return state;
        }
        return {
          sessions: state.sessions.map((session) =>
            session.id === id
              ? taskDocumentRef
                ? { ...session, taskDocumentRef }
                : clearTask(session)
              : session,
          ),
        };
      }),
  };
}

function assignmentSessionState(set: SessionStoreSet): Pick<SessionState, "applyTaskAssignment"> {
  return {
    applyTaskAssignment: (id, taskDocumentRef, seatRole) =>
      set((state) => {
        const target = state.sessions.find((session) => session.id === id);
        if (!target) return state;
        return {
          sessions: state.sessions.map((session) => {
            if (session.id === id) {
              return taskDocumentRef
                ? { ...session, taskDocumentRef, seatRole }
                : clearTask(session);
            }
            if (
              taskDocumentRef &&
              sameTaskDocument(session.taskDocumentRef, taskDocumentRef) &&
              isLiveSession(session) &&
              sessionSeatRole(session) === seatRole
            ) {
              return clearTask(session);
            }
            return session;
          }),
        };
      }),
  };
}

export const sessionStore = createStore<SessionState>((set) => ({
  sessions: [],
  activeId: null,
  count: 0,
  ...addSessionState(set),
  ...upsertSessionState(set),
  ...hydrateSessionState(set),
  ...closeSessionState(set),
  ...statusSessionState(set),
  ...patchSessionState(set),
  setActive: (id) => set({ activeId: id }),
  ...lifecycleSessionState(set),
  ...taskSessionState(set),
  ...assignmentSessionState(set),
}));

export const useSessions = <T>(selector: (state: SessionState) => T): T =>
  useStore(sessionStore, selector);

export function findSessionForLifecycle(lifecycleId: string): OpenSession | undefined {
  return sessionStore
    .getState()
    .sessions.find((session) => session.lifecycleId === lifecycleId && isLiveSession(session));
}

/**
 * ANY pending interaction on the seat: the parent's
 * singular slot OR a multiplexed sub-agent entry. Every attention surface (rail badge,
 * announcer, visual grammar, question triage) derives from this — never from the singular
 * slot alone, or a seat blocked SOLELY on a sub-agent approval goes dark.
 */
export function sessionHasPendingInteraction(
  session: Pick<OpenSession, "controlPendingInteraction" | "controlPendingInteractions">,
): boolean {
  return (
    session.controlPendingInteraction !== undefined ||
    (session.controlPendingInteractions ?? []).length > 0
  );
}

/**
 * The payload attention chrome previews: the parent's singular slot first,
 * else the first multiplexed sub-agent entry.
 */
export function sessionPendingInteractionPayload(
  session: Pick<OpenSession, "controlPendingInteraction" | "controlPendingInteractions">,
): Record<string, unknown> | undefined {
  return session.controlPendingInteraction ?? session.controlPendingInteractions?.[0];
}

/**
 * The single LIVE session bound to a task document (mirrors {@link findSessionForLifecycle}).
 */
export function findSessionForTask(
  taskDocumentRef: TaskDocumentRef,
  role?: SessionRole,
): OpenSession | undefined {
  return sessionStore
    .getState()
    .sessions.find(
      (session) =>
        sameTaskDocument(session.taskDocumentRef, taskDocumentRef) &&
        isLiveSession(session) &&
        (role === undefined || sessionRole(session) === role),
    );
}

// The optional catalog→store field map: every field copied only when the server supplied it,
// except the two counters that may legitimately be zero/empty and therefore use `!== undefined`.
const OPTIONAL_SESSION_FIELDS: {
  from: keyof TerminalSessionInfo;
  to: keyof OpenSession;
  keepWhenFalsy?: boolean;
}[] = [
  { from: "harness", to: "harness" },
  { from: "lifecycleId", to: "lifecycleId" },
  { from: "taskDocumentRef", to: "taskDocumentRef" },
  { from: "spawnRole", to: "spawnRole" },
  { from: "seatRole", to: "seatRole" },
  { from: "createdAt", to: "createdAt" },
  { from: "landedAt", to: "landedAt" },
  { from: "landedReason", to: "landedReason" },
  { from: "landedEdge", to: "landedEdge" },
  { from: "retiredAt", to: "retiredAt" },
  { from: "retiredBySession", to: "retiredBySession" },
  { from: "retiredReason", to: "retiredReason" },
  { from: "retiredEdge", to: "retiredEdge" },
  { from: "spawnedBySession", to: "spawnedBySession" },
  { from: "spawnedByLifecycle", to: "spawnedByLifecycle" },
  { from: "structuralParentTaskDocumentRef", to: "structuralParentTaskDocumentRef" },
  { from: "structuralParentRole", to: "structuralParentRole" },
  { from: "spawnedLabel", to: "spawnedLabel" },
  { from: "spawnLevel", to: "spawnLevel" },
  { from: "spawnLevelSource", to: "spawnLevelSource" },
  { from: "resolvedModel", to: "resolvedModel" },
  { from: "resolvedEffort", to: "resolvedEffort" },
  { from: "turnState", to: "turnState" },
  { from: "turnStateChangedAt", to: "turnStateChangedAt" },
  { from: "controlState", to: "controlState" },
  { from: "controlProtocol", to: "controlProtocol" },
  { from: "controlActivity", to: "controlActivity" },
  { from: "controlAcceptance", to: "controlAcceptance" },
  { from: "controlVendorSessionId", to: "controlVendorSessionId" },
  { from: "controlPendingInteraction", to: "controlPendingInteraction" },
  { from: "controlPendingInteractions", to: "controlPendingInteractions" },
  { from: "controlLastEventSequence", to: "controlLastEventSequence", keepWhenFalsy: true },
  { from: "controlRaw", to: "controlRaw" },
  { from: "livenessFailures", to: "livenessFailures", keepWhenFalsy: true },
  { from: "livenessFirstFailedAt", to: "livenessFirstFailedAt" },
  { from: "livenessLastFailedAt", to: "livenessLastFailedAt" },
  { from: "livenessEvidence", to: "livenessEvidence" },
  { from: "exitEvidence", to: "exitEvidence" },
];

function optionalSessionFields(info: TerminalSessionInfo): Partial<OpenSession> {
  const out: Partial<OpenSession> = {};
  for (const { from, to, keepWhenFalsy } of OPTIONAL_SESSION_FIELDS) {
    const value = info[from];
    if (keepWhenFalsy ? value !== undefined : value) {
      (out as Record<string, unknown>)[to] = value;
    }
  }
  return out;
}

export function fromTerminalSessionInfo(info: TerminalSessionInfo): OpenSession {
  return {
    id: info.id,
    label: info.label,
    kind: info.kind,
    ...optionalSessionFields(info),
    status: info.status,
  };
}

// --- Live connections (slice 6f): the per-session `TerminalConnection` registry, exposed cockpit-wide
// so a surface outside the canonical Chats stage (the highlight composer) can reach a session. Non-reactive
// (module-level maps, not store state) so a registration never re-renders. `pending` queues an injection
// for a session whose <Terminal> has not mounted/registered yet (the create-then-send race); the live
// connection itself buffers anything sent before its WebSocket opens (see `data/terminal.ts`).
const connections = new Map<string, TerminalConnection>();
const pending = new Map<string, string[]>();
// A surface that just created a session (the highlight composer) waits here for its terminal to
// register before injecting; resolved in `registerConnection`.
const connectionWaiters = new Map<string, ((conn: TerminalConnection | null) => void)[]>();

/**
 * Clear non-reactive connection registries at a dev-bench scenario boundary. Descendant terminals
 * are unmounted first; resolving parked waiters with null prevents promises from the old authority
 * lingering until their 12-second timeout, while queued input must never cross into the next fixture.
 */
export function resetSessionConnectionRegistriesForDev(): void {
  connections.clear();
  pending.clear();
  for (const waiters of connectionWaiters.values()) {
    for (const resolve of waiters) resolve(null);
  }
  connectionWaiters.clear();
}

/** Each keep-alive PTY registers its live connection here (and `null` on teardown). */
export function registerConnection(id: string, conn: TerminalConnection | null): void {
  if (!conn) {
    connections.delete(id);
    return;
  }
  connections.set(id, conn);
  const queued = pending.get(id);
  if (queued) {
    pending.delete(id);
    for (const text of queued) conn.sendInput(text);
  }
  const waiters = connectionWaiters.get(id);
  if (waiters) {
    connectionWaiters.delete(id);
    for (const resolve of waiters) resolve(conn);
  }
}

/** Inject `text` into a session's stdin from anywhere; queues if its terminal has not registered yet. */
export function sendToSession(id: string, text: string): void {
  const conn = connections.get(id);
  if (conn) {
    conn.sendInput(text);
    return;
  }
  const queue = pending.get(id) ?? [];
  queue.push(text);
  pending.set(id, queue);
}

const CONNECTION_TIMEOUT_MS = 12000; // a terminal that never mounts/registers must not hang delivery

/** Resolve a session's live connection now, or once its `<Terminal>` registers (the create-then-send
 *  race); resolves `null` after {@link CONNECTION_TIMEOUT_MS} if no registration arrives — so a terminal
 *  that fails to mount (chunk-load error, xterm init throw) surfaces a retry instead of hanging forever.
 *  The parked resolver is removed on timeout so it cannot leak in `connectionWaiters`. */
function waitForConnection(id: string): Promise<TerminalConnection | null> {
  const existing = connections.get(id);
  if (existing) return Promise.resolve(existing);
  return new Promise<TerminalConnection | null>((resolve) => {
    let settled = false;
    const settle = (conn: TerminalConnection | null) => {
      if (settled) return; // register and timeout can both fire; first wins, second is a no-op
      settled = true;
      resolve(conn);
    };
    const list = connectionWaiters.get(id) ?? [];
    list.push(settle);
    connectionWaiters.set(id, list);
    setTimeout(() => {
      if (settled) return;
      const waiters = connectionWaiters.get(id);
      if (waiters) {
        const at = waiters.indexOf(settle);
        if (at >= 0) waiters.splice(at, 1);
        if (waiters.length === 0) connectionWaiters.delete(id);
      }
      settle(null); // no terminal registered in time — surface a retry instead of hanging
    }, CONNECTION_TIMEOUT_MS);
  });
}

/** The status the highlight composer surfaces after a Send (slice 6f hardening). */
export type DeliveryStatus = "delivered" | "unconfirmed";

/**
 * Paste a context package into a session without submitting it. Used by the leaf-bind handoff where the
 * operator needs to add their own instruction before pressing Enter. Delivery is confirmed, not assumed:
 * {@link pasteAndConfirm} retries through the harness boot window (Claude Code discards stdin while
 * booting) and only reports "delivered" once the composer echoed the draft.
 */
export async function pasteDraftToSession(
  id: string,
  packageText: string,
): Promise<DeliveryStatus> {
  const conn = await waitForConnection(id);
  if (!conn) return "unconfirmed";
  return (await pasteAndConfirm(conn, packageText)) ? "delivered" : "unconfirmed";
}

/**
 * Deliver a context package to a session and confirm it submitted (slice 6f hardening). Waits for the
 * session's terminal to register (bounded — see {@link waitForConnection}) and its harness to settle,
 * injects the package as ONE *sanitized* bracketed paste (control bytes — incl. the `0x1a` suspend byte —
 * and stray paste markers stripped), then drives the {@link submitAndConfirm} retry loop. Returns
 * whether submission was observed so the composer can show success vs a retry-able failure instead of
 * dropping the message silently — including when the terminal never registered.
 */
export async function deliverToSession(id: string, packageText: string): Promise<DeliveryStatus> {
  const session = sessionStore.getState().sessions.find((candidate) => candidate.id === id);
  if (session?.kind === "harness") {
    try {
      const response = await fetch(`/api/terminal/${encodeURIComponent(id)}/paste`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: packageText, submit: true }),
      });
      if (!response.ok) return "unconfirmed";
      const body = (await response.json()) as { delivered?: boolean; acceptance?: string };
      return body.delivered === true &&
        (body.acceptance === "immediate" || body.acceptance === "queued")
        ? "delivered"
        : "unconfirmed";
    } catch {
      return "unconfirmed";
    }
  }
  const conn = await waitForConnection(id);
  if (!conn) return "unconfirmed"; // terminal never registered — surface a retry, never hang on "Sending…"
  await conn.whenReady();
  conn.sendInput(bracketedPaste(sanitizeForInjection(packageText)));
  return (await submitAndConfirm(conn)) ? "delivered" : "unconfirmed";
}

export type CreateSessionResult = TerminalOpenResult;

/**
 * Spawn + own a dashboard session. The server response is the mutation authority: no local row,
 * active id, focus candidate, or catalog broadcast exists until the exact open identity is accepted.
 */
export async function createSession(
  prefix: string,
  kind: "terminal" | "harness" = "terminal",
  harness?: string,
  lifecycleId?: string,
  taskDocumentRef?: TaskDocumentRef,
  role?: string,
): Promise<CreateSessionResult> {
  const id = crypto.randomUUID();
  const label = nextSessionLabel(prefix, sessionStore.getState().sessions);
  const result = await openTerminalSession(id, kind, "", harness, {
    label,
    lifecycleId,
    taskDocumentRef,
    role,
  });
  if (result.outcome === "failed") return result;
  sessionStore.getState().upsert(result.session, true);
  notifySessionCatalogChanged("create", result.session.id);
  return result;
}
