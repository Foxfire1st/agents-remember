import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

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
