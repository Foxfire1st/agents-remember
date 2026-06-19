import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import {
  bracketedPaste,
  openTerminalSession,
  sanitizeForInjection,
  submitAndConfirm,
  type TerminalConnection,
} from "./terminal";

// The open terminal/chat sessions (slice 6e hardening): the session registry as a module-level store
// — shared, testable client state, the same pattern as the observer projection store (`data/store.ts`)
// but deliberately kept separate from it (ephemeral UI state, not projected truth). Terminal
// *persistence* across a cockpit view switch is owned elsewhere — `Cockpit` keeps <Chats> mounted
// (hidden via CSS) so the xterm instance + its buffer + the live WebSocket survive — so this store
// only has to hold which sessions exist and which one is active.
export interface OpenSession {
  id: string;
  label: string;
}

interface SessionState {
  sessions: OpenSession[];
  activeId: string | null;
  count: number;
  /** Append a session labelled `{prefix} {n}`, bump the ordinal, and make it active. */
  add: (prefix: string, id: string) => void;
  /** Drop a session; clear `activeId` if it was the one removed (the tmux session persists). */
  close: (id: string) => void;
  setActive: (id: string) => void;
}

export const sessionStore = createStore<SessionState>((set) => ({
  sessions: [],
  activeId: null,
  count: 0,
  add: (prefix, id) =>
    set((state) => {
      const ordinal = state.count + 1;
      return {
        count: ordinal,
        sessions: [...state.sessions, { id, label: `${prefix} ${ordinal}` }],
        activeId: id,
      };
    }),
  close: (id) =>
    set((state) => ({
      sessions: state.sessions.filter((session) => session.id !== id),
      activeId: state.activeId === id ? null : state.activeId,
    })),
  setActive: (id) => set({ activeId: id }),
}));

export const useSessions = <T>(selector: (state: SessionState) => T): T =>
  useStore(sessionStore, selector);

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
): Promise<string> {
  const id = crypto.randomUUID();
  // Best-effort: the dev bench has no backend, but its mock socket renders the terminal anyway.
  await openTerminalSession(id, kind, "", harness);
  sessionStore.getState().add(prefix, id);
  return id;
}
