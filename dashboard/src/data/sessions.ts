import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import {
  bracketedPaste,
  openTerminalSession,
  pasteAndConfirm,
  sanitizeForInjection,
  submitAndConfirm,
  type TerminalConnection,
  type TerminalOpenKind,
  type TerminalSessionInfo,
  type TerminalSessionStatus,
} from "./terminal";

// The open terminal/chat sessions (slice 6e hardening): the session registry as a module-level store
// — shared, testable client state, the same pattern as the observer projection store (`data/store.ts`)
// but deliberately kept separate from it (ephemeral UI state, not projected truth). Terminal
// *persistence* across cockpit refresh/view/session switches is owned by the backend catalog + tmux.
// The store only has to hold which sessions exist and which one is active; <Chats> attaches the active
// session's visible terminal and lets inactive rows reattach when selected.
export interface OpenSession {
  id: string;
  label: string;
  kind?: TerminalOpenKind;
  harness?: string;
  lifecycleId?: string;
  /** The durable leaf-identity key (qualified leaf id `repo/master/leaf-id`) this chat is bound to. */
  leafKey?: string;
  /** The AR_SPAWN_ROLE recorded at spawn (L14) — the Chats command-tree grouping key. */
  spawnRole?: string;
  status?: TerminalSessionStatus;
}

type SessionCatalogChangeReason = "create" | "terminate" | "leaf";

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
    (message.reason === "create" || message.reason === "terminate" || message.reason === "leaf") &&
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
  setActive: (id: string) => void;
  /** Attach a hosted session to one lifecycle; latest attachment owns that lifecycle route. */
  setLifecycle: (id: string, lifecycleId: string | null) => void;
  /**
   * Bind a hosted session to one durable leaf (qualified leaf id), or clear it (`null`). Advisory
   * uniqueness: a non-null bind is REJECTED (no-op) if another LIVE session already owns that leaf —
   * the server's `409 leaf-taken` is the real arbiter, this just avoids an obvious local double-claim.
   */
  setLeaf: (id: string, leafKey: string | null) => void;
  /**
   * Apply a server/catalog-authoritative leaf assignment after a successful backend attach or hydrate.
   * Same-role local owners of the destination leaf are cleared because the catalog result wins.
   */
  applyLeafAssignment: (id: string, leafKey: string | null) => void;
}

/** A session's leaf-uniqueness role: a plain shell is a TERMINAL, any agent harness is a CHAT. */
export type SessionRole = "chat" | "terminal";

/** Derive a session's role from its kind (mirrors the backend `role_for_kind`). */
export function sessionRole(session: Pick<OpenSession, "kind">): SessionRole {
  return session.kind === "terminal" ? "terminal" : "chat";
}

function clearLifecycle(session: OpenSession): OpenSession {
  const next = { ...session };
  delete next.lifecycleId;
  return next;
}

function clearLeaf(session: OpenSession): OpenSession {
  const next = { ...session };
  delete next.leafKey;
  return next;
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
  return session.status !== "exited" && session.status !== "terminated";
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

export const sessionStore = createStore<SessionState>((set) => ({
  sessions: [],
  activeId: null,
  count: 0,
  add: (prefix, id, lifecycleId) =>
    set((state) => {
      const next = {
        id,
        label: nextSessionLabel(prefix, state.sessions),
        ...(lifecycleId ? { lifecycleId } : {}),
      };
      const sessions = [
        ...state.sessions.map((session) =>
          lifecycleId && session.lifecycleId === lifecycleId
            ? clearLifecycle(session)
            : session,
        ),
        next,
      ];
      return {
        count: trackedOrdinal(sessions),
        sessions,
        activeId: id,
      };
    }),
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
  hydrate: (sessions, preferredActiveId) =>
    set((state) => {
      const live = sessions.filter(isLiveSession);
      const preferred = preferredActiveId && live.some((session) => session.id === preferredActiveId);
      const retainedActive = state.activeId && live.some((session) => session.id === state.activeId);
      return {
        sessions,
        count: trackedOrdinal(sessions),
        activeId: preferred
          ? preferredActiveId
          : retainedActive
            ? state.activeId
            : (live[0]?.id ?? sessions[0]?.id ?? null),
      };
    }),
  close: (id) =>
    set((state) => {
      const sessions = state.sessions.filter((session) => session.id !== id);
      return {
        sessions,
        count: trackedOrdinal(sessions),
        activeId: state.activeId === id ? null : state.activeId,
      };
    }),
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
            ? (state.sessions.find((session) => session.id !== id && isLiveSession(session))?.id ?? null)
            : state.activeId,
      };
    }),
  setActive: (id) => set({ activeId: id }),
  setLifecycle: (id, lifecycleId) =>
    set((state) => ({
      sessions: state.sessions.map((session) => {
        if (session.id === id) return lifecycleId ? { ...session, lifecycleId } : clearLifecycle(session);
        if (lifecycleId && session.lifecycleId === lifecycleId) {
          return clearLifecycle(session);
        }
        return session;
      }),
    })),
  setLeaf: (id, leafKey) =>
    set((state) => {
      if (leafKey) {
        // Advisory guard, scoped to the binding session's role (chat vs. terminal): a live session of
        // the SAME role already owning this leaf wins — the new bind is a no-op. A chat and a terminal
        // can both bind one leaf, so they never block each other. The server's 409 is the real arbiter.
        const role = sessionRole(state.sessions.find((session) => session.id === id) ?? {});
        const owner = state.sessions.find(
          (session) =>
            session.id !== id &&
            session.leafKey === leafKey &&
            isLiveSession(session) &&
            sessionRole(session) === role,
        );
        if (owner) return state;
      }
      return {
        sessions: state.sessions.map((session) =>
          session.id === id
            ? leafKey
              ? { ...session, leafKey }
              : clearLeaf(session)
            : session,
        ),
      };
    }),
  applyLeafAssignment: (id, leafKey) =>
    set((state) => {
      const target = state.sessions.find((session) => session.id === id);
      if (!target) return state;
      const role = sessionRole(target);
      return {
        sessions: state.sessions.map((session) => {
          if (session.id === id) {
            return leafKey ? { ...session, leafKey } : clearLeaf(session);
          }
          if (
            leafKey &&
            session.leafKey === leafKey &&
            isLiveSession(session) &&
            sessionRole(session) === role
          ) {
            return clearLeaf(session);
          }
          return session;
        }),
      };
    }),
}));

export const useSessions = <T>(selector: (state: SessionState) => T): T =>
  useStore(sessionStore, selector);

export function findSessionForLifecycle(lifecycleId: string): OpenSession | undefined {
  return sessionStore
    .getState()
    .sessions.find((session) => session.lifecycleId === lifecycleId && isLiveSession(session));
}

/**
 * The single LIVE session bound to `leafKey` (mirrors {@link findSessionForLifecycle}). Pass `role`
 * to find the leaf's chat vs. its terminal independently — a leaf can hold one of each (L5 fix 2).
 */
export function findSessionForLeaf(leafKey: string, role?: SessionRole): OpenSession | undefined {
  return sessionStore
    .getState()
    .sessions.find(
      (session) =>
        session.leafKey === leafKey &&
        isLiveSession(session) &&
        (role === undefined || sessionRole(session) === role),
    );
}

export function fromTerminalSessionInfo(info: TerminalSessionInfo): OpenSession {
  return {
    id: info.id,
    label: info.label,
    kind: info.kind,
    ...(info.harness ? { harness: info.harness } : {}),
    ...(info.lifecycleId ? { lifecycleId: info.lifecycleId } : {}),
    ...(info.leafKey ? { leafKey: info.leafKey } : {}),
    ...(info.spawnRole ? { spawnRole: info.spawnRole } : {}),
    status: info.status,
  };
}

// --- Live connections (slice 6f): the per-session `TerminalConnection` registry, exposed cockpit-wide
// so a surface outside <Chats> (the highlight composer) can inject into a session's stdin. Non-reactive
// (module-level maps, not store state) so a registration never re-renders. `pending` queues an injection
// for a session whose <Terminal> has not mounted/registered yet (the create-then-send race); the live
// connection itself buffers anything sent before its WebSocket opens (see `data/terminal.ts`).
const connections = new Map<string, TerminalConnection>();
const pending = new Map<string, string[]>();
// A surface that just created a session (the highlight composer) waits here for its terminal to
// register before injecting; resolved in `registerConnection`.
const connectionWaiters = new Map<string, ((conn: TerminalConnection) => void)[]>();

/** <Chats> registers each live connection here (and `null` on teardown); flushes any queued sends. */
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
export async function pasteDraftToSession(id: string, packageText: string): Promise<DeliveryStatus> {
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
  const conn = await waitForConnection(id);
  if (!conn) return "unconfirmed"; // terminal never registered — surface a retry, never hang on "Sending…"
  await conn.whenReady();
  conn.sendInput(bracketedPaste(sanitizeForInjection(packageText)));
  return (await submitAndConfirm(conn)) ? "delivered" : "unconfirmed";
}

/** Spawn + own a dashboard session (a shell or a detected harness) and register it in the store. */
export async function createSession(
  prefix: string,
  kind: "terminal" | "harness" = "terminal",
  harness?: string,
  lifecycleId?: string,
  leafKey?: string,
): Promise<string> {
  const id = crypto.randomUUID();
  const label = nextSessionLabel(prefix, sessionStore.getState().sessions);
  // Best-effort: the dev bench has no backend, but its mock socket renders the terminal anyway.
  const persisted = await openTerminalSession(id, kind, "", harness, { label, lifecycleId, leafKey });
  sessionStore.getState().upsert(
    {
      id,
      label,
      kind,
      ...(harness ? { harness } : {}),
      ...(lifecycleId ? { lifecycleId } : {}),
      ...(leafKey ? { leafKey } : {}),
      status: "running",
    },
    true,
  );
  if (persisted) notifySessionCatalogChanged("create", id);
  return id;
}
