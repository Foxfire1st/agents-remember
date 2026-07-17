import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import type { HarnessAcceptanceState } from "../types/terminalCatalog";
import { sessionStore } from "./sessions";

// The sessions-cockpit client store (260715-FEUI-L2 S3, design §4.3). HONESTY INVARIANTS live in
// this shape: server truth is mirrored, never invented — `requested` and `effective` are separate
// fields everywhere, a queued set NEVER moves the effective marker, and evidence tiers start at
// 'pending' until control state proves better (L4 wires the promotion paths). Kept separate from
// `sessionStore` (the catalog mirror) and `dashboardStore` (the projection): this is per-seat
// cockpit state — drafts, ledgers, clocks, freshness — not catalog truth.

/** The five launch-evidence tiers (weakest wins, never promoted without proof — design §6). */
export type EvidenceTier = "pending" | "readback" | "model-validated" | "defaults" | "refused";

export interface PendingSet {
  requestedValue: string;
  sentAt: number;
  phase: "inflight" | "queued-awaiting-turn" | "unknown-verifying";
}

/** The set-route outcome as the server reported it (requested ≠ effective, words as text). */
export interface SetResultSnapshot {
  acceptance: HarnessAcceptanceState;
  requestedValue: string;
  /** Present only when the server proved the value took effect — never inferred client-side. */
  effectiveValue?: string;
  detail?: string;
}

export interface SetLedgerEntry {
  at: number;
  kind: "model" | "effort";
  requestedValue: string;
  result: SetResultSnapshot;
  /** Explicit operator act (design §9.8 F22) — feeds the unacknowledged-outcomes attention count. */
  acknowledged: boolean;
}

/** The exact-session capability GET (L4) — never the pre-session cache (design §6.1). */
export interface CapabilitySnapshot {
  sessionId: string;
  fetchedAt: number;
  payload: Record<string, unknown>;
}

export interface QueuedSubmit {
  requestId: string;
  preview: string;
  queuedAt: number;
  /** Popped back into the composer (alt+↑, L5) — client-side supersession, requestId never resent. */
  superseded: boolean;
}

export interface PerSessionCockpit {
  liveSnapshot?: CapabilitySnapshot;
  /** Per-kind so a pair change never clobbers the other knob's in-flight set. */
  pendingSets: { model?: PendingSet; effort?: PendingSet };
  setLedger: SetLedgerEntry[];
  launchEvidence: { retainedModel?: string; retainedEffort?: string; tier: EvidenceTier };
  composer: { draft: string; draftRevision: number /* submit lifecycle lands in L5 */ };
  surfaceTab: "terminal"; // 'transcript' joins when UA-1 lands
  /** Client-measured turn clock (~-labeled, sweep-bounded honesty): observed transitions only. */
  turnClock: { workingSince: number | null; lastObservedTurnState?: string };
  /** Per-pane freshness (R15). 'none' = no PTY attached in this cockpit yet (L6 writes it). */
  freshness: { ptyWs: "none" | "connected" | "reconnecting" | "dropped"; lastOutputAt: number | null };
  /** The cockpit's OWN queued submits (a list, not a chip — F13); whole truth is UA-8-gated. */
  queue: QueuedSubmit[];
  /** InteractionBar round-trip state (L6 R4 / design §7.3 F7) — absent when nothing in flight. */
  interactionAnswer?: InteractionAnswerState;
}

/** One pending-interaction answer round-trip: in-flight → verbatim error | answered-waiting. */
export interface InteractionAnswerState {
  interactionId: string;
  inflight: boolean;
  /** The POST failure, verbatim (never silent) — cleared by retry. */
  error?: string;
  /** Set on 202: the bar renders "answered — waiting for the agent" until the row clears. */
  answeredAt?: number;
}

const emptyPerSession = (): PerSessionCockpit => ({
  pendingSets: {},
  setLedger: [],
  launchEvidence: { tier: "pending" },
  composer: { draft: "", draftRevision: 0 },
  surfaceTab: "terminal",
  turnClock: { workingSince: null },
  freshness: { ptyWs: "none", lastOutputAt: null },
  queue: [],
});

/** Catalog-poll beats missed before the rail shows the stale banner (R15/F3). */
export const POLL_STALE_MISSED_BEATS = 3;

const TREE_VIEW_KEY = "cockpit.sessions.orchestration-tree";

// Open-question decision (leaf doc): the orchestration-tree toggle persists PER USER via
// localStorage — it is an inspection preference (like the calm-cockpit toggle), not session
// state, and the low-stakes call favors the cheaper, already-idiomatic mechanism.
function readPersistedTreeView(): boolean {
  try {
    return window.localStorage.getItem(TREE_VIEW_KEY) === "true";
  } catch {
    return false;
  }
}

function writePersistedTreeView(value: boolean): void {
  try {
    window.localStorage.setItem(TREE_VIEW_KEY, String(value));
  } catch {
    // a UI preference only — never fail on private contexts
  }
}

export interface SessionCockpitState {
  focusedSessionId: string | null;
  /** Mirrors of the view-owned layout facts (design §4.3) — SessionsView syncs them one-way. */
  layout: { railCollapsed: boolean; inspectorCollapsed: boolean };
  paletteOpen: boolean;
  /** The palette-toggled spawn-edge provenance view (R5) — persisted per user (see above). */
  orchestrationTreeView: boolean;
  /** Catalog-poll health (R15/F3): a dead 2500 ms poll freezes every row — global state. */
  pollHealth: { lastBeatAt: number | null; missedBeats: number; healthy: boolean };
  perSession: Record<string, PerSessionCockpit>;

  setFocusedSession: (id: string | null) => void;
  setLayout: (layout: { railCollapsed: boolean; inspectorCollapsed: boolean }) => void;
  setPaletteOpen: (open: boolean) => void;
  setOrchestrationTreeView: (on: boolean) => void;
  recordPollBeat: (ok: boolean) => void;
  setComposerDraft: (id: string, draft: string) => void;
  recordPendingSet: (id: string, kind: "model" | "effort", pending: PendingSet) => void;
  clearPendingSet: (id: string, kind: "model" | "effort") => void;
  appendSetLedger: (id: string, entry: Omit<SetLedgerEntry, "acknowledged">) => void;
  acknowledgeSetOutcomes: (id: string) => void;
  setLaunchEvidence: (id: string, evidence: PerSessionCockpit["launchEvidence"]) => void;
  setLiveSnapshot: (id: string, snapshot: CapabilitySnapshot) => void;
  enqueueSubmit: (id: string, submit: Omit<QueuedSubmit, "superseded">) => void;
  supersedeLastQueued: (id: string) => QueuedSubmit | null;
  dequeueSubmit: (id: string, requestId: string) => void;
  setPtyWs: (id: string, ptyWs: PerSessionCockpit["freshness"]["ptyWs"]) => void;
  recordPtyOutput: (id: string, at: number) => void;
  recordTurnObservation: (id: string, turnState: string | undefined, at: number) => void;
  setInteractionAnswer: (id: string, answer: InteractionAnswerState | undefined) => void;
}

function withPerSession(
  state: SessionCockpitState,
  id: string,
  update: (current: PerSessionCockpit) => PerSessionCockpit,
): Pick<SessionCockpitState, "perSession"> {
  const current = state.perSession[id] ?? emptyPerSession();
  return { perSession: { ...state.perSession, [id]: update(current) } };
}

export const sessionCockpitStore = createStore<SessionCockpitState>((set) => ({
  focusedSessionId: null,
  layout: { railCollapsed: false, inspectorCollapsed: false },
  paletteOpen: false,
  orchestrationTreeView: typeof window === "undefined" ? false : readPersistedTreeView(),
  pollHealth: { lastBeatAt: null, missedBeats: 0, healthy: true },
  perSession: {},

  setFocusedSession: (id) => set({ focusedSessionId: id }),
  setLayout: (layout) => set({ layout }),
  setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
  setOrchestrationTreeView: (on) => {
    writePersistedTreeView(on);
    set({ orchestrationTreeView: on });
  },
  recordPollBeat: (ok) =>
    set((state) => {
      const missedBeats = ok ? 0 : state.pollHealth.missedBeats + 1;
      return {
        pollHealth: {
          lastBeatAt: ok ? Date.now() : state.pollHealth.lastBeatAt,
          missedBeats,
          healthy: missedBeats < POLL_STALE_MISSED_BEATS,
        },
      };
    }),
  setComposerDraft: (id, draft) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        composer: { draft, draftRevision: current.composer.draftRevision + 1 },
      })),
    ),
  recordPendingSet: (id, kind, pending) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        // Per-kind by construction: setting `model` never touches `effort` and vice versa.
        pendingSets: { ...current.pendingSets, [kind]: pending },
      })),
    ),
  clearPendingSet: (id, kind) =>
    set((state) =>
      withPerSession(state, id, (current) => {
        const pendingSets = { ...current.pendingSets };
        delete pendingSets[kind];
        return { ...current, pendingSets };
      }),
    ),
  appendSetLedger: (id, entry) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        setLedger: [...current.setLedger, { ...entry, acknowledged: false }],
        // Deliberately NOT touching launchEvidence here: a set outcome — even `immediate` — is
        // its own ledger fact; the effective marker moves only via setLaunchEvidence with proof.
      })),
    ),
  acknowledgeSetOutcomes: (id) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        setLedger: current.setLedger.map((entry) =>
          entry.acknowledged ? entry : { ...entry, acknowledged: true },
        ),
      })),
    ),
  setLaunchEvidence: (id, launchEvidence) =>
    set((state) => withPerSession(state, id, (current) => ({ ...current, launchEvidence }))),
  setLiveSnapshot: (id, liveSnapshot) =>
    set((state) => withPerSession(state, id, (current) => ({ ...current, liveSnapshot }))),
  enqueueSubmit: (id, submit) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        queue: [...current.queue, { ...submit, superseded: false }],
      })),
    ),
  supersedeLastQueued: (id) => {
    let popped: QueuedSubmit | null = null;
    set((state) =>
      withPerSession(state, id, (current) => {
        const lastLive = [...current.queue].reverse().find((item) => !item.superseded);
        if (!lastLive) return current;
        popped = lastLive;
        return {
          ...current,
          queue: current.queue.map((item) =>
            item.requestId === lastLive.requestId ? { ...item, superseded: true } : item,
          ),
        };
      }),
    );
    return popped;
  },
  dequeueSubmit: (id, requestId) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        queue: current.queue.filter((item) => item.requestId !== requestId),
      })),
    ),
  setPtyWs: (id, ptyWs) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        freshness: { ...current.freshness, ptyWs },
      })),
    ),
  recordPtyOutput: (id, at) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        freshness: { ...current.freshness, lastOutputAt: at },
      })),
    ),
  recordTurnObservation: (id, turnState, at) =>
    set((state) =>
      withPerSession(state, id, (current) => {
        if (current.turnClock.lastObservedTurnState === turnState) return current;
        return {
          ...current,
          turnClock: {
            // `~`-labeled by consumers: the clock starts at the OBSERVED transition, which is
            // poll/sweep-bounded — never a claim about when the harness really started working.
            workingSince: turnState === "working" ? at : null,
            lastObservedTurnState: turnState,
          },
        };
      }),
    ),
  setInteractionAnswer: (id, answer) =>
    set((state) =>
      withPerSession(state, id, (current) => ({ ...current, interactionAnswer: answer })),
    ),
}));

export const useSessionCockpit = <T>(selector: (state: SessionCockpitState) => T): T =>
  useStore(sessionCockpitStore, selector);

// ── The catalog mirror (turn clock) ─────────────────────────────────────────────────────────
// Watches the session registry and records per-seat turn-state transitions into the client turn
// clock. Refcounted so the cockpit view and any future consumer share one subscription.
let mirrorRefs = 0;
let unsubscribeMirror: (() => void) | null = null;

export function startCockpitMirror(): () => void {
  mirrorRefs += 1;
  if (mirrorRefs === 1) {
    unsubscribeMirror = sessionStore.subscribe((state) => {
      const cockpit = sessionCockpitStore.getState();
      for (const session of state.sessions) {
        cockpit.recordTurnObservation(session.id, session.turnState, Date.now());
      }
    });
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    mirrorRefs -= 1;
    if (mirrorRefs === 0) {
      unsubscribeMirror?.();
      unsubscribeMirror = null;
    }
  };
}
