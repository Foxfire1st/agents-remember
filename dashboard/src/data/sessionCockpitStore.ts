import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import type { CapabilitySnapshotWire, SetAcceptance } from "../types/harnessCapabilities";
import type { PairChangeState } from "./pairChange";
import { sessionStore } from "./sessions";
import type { SubmitRecord } from "./submitMachine";
import { compactSubmitHistory, compactSubmitQueue } from "./submitRetention";

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

/** The set-route outcome as the server reported it (requested ≠ effective, words as text).
 *  acceptance is the SET vocabulary (harness_capabilities.py SET_ACCEPTANCE_VALUES) — it includes
 *  'echo-verified'; the submit-receipt vocabulary ('rejected') never appears here. */
export interface SetResultSnapshot {
  acceptance: SetAcceptance;
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
  payload: CapabilitySnapshotWire;
}

/** An exact-session capability GET failure, verbatim (F16) — the control disables on it. */
export interface SnapshotErrorInfo {
  /** null = the fetch itself threw (network loss / malformed body). */
  httpStatus: number | null;
  /** The server's own status word (`unknown-session` / `unsupported` / `control-unavailable`)
   *  or `transport` when the server never said one. */
  status: string;
  detail: string;
  at: number;
}

/** A set-model/set-effort HTTP-boundary failure (R3) — NEVER a 200 unknown/unsupported. */
export interface SetRouteErrorInfo {
  kind: "model" | "effort";
  /** The value the failed POST carried — the retry affordance resends exactly it. */
  requestedValue: string;
  httpStatus: number | null;
  status: string;
  detail: string;
  /** 503 outage — the only route error with a retry affordance (R3). */
  retryable: boolean;
  at: number;
}

/** One echo-verified effective value (SetResult evidence) — server truth, timestamped so the
 *  freshest of {snapshot, echo} wins; never a client inference. */
export interface EchoEvidence {
  value: string;
  at: number;
}

export interface QueuedSubmit {
  requestId: string;
  /** The immutable message accepted under requestId; alt+up restores this exact text. */
  text: string;
  preview: string;
  queuedAt: number;
  expectedBridgeEpoch: string;
  /** QueuePreview renders only the authority's latest explicit queued state. */
  state: "queued";
}

export type WithdrawalState =
  | {
      phase: "pending";
      requestId: string;
      text: string;
      expectedBridgeEpoch: string;
      /** Composer revision observed before the first withdrawal request crossed the network. */
      draftRevision: number;
      requestedAt: number;
    }
  | {
      phase: "recovery";
      requestId: string;
      /** Exact withdrawn text retained outside the bounded submit-history tail until recovery. */
      text: string;
      withdrawnAt: number;
    };

export interface PerSessionCockpit {
  liveSnapshot?: CapabilitySnapshot;
  /** The exact-session GET is in flight (popover spinner / chrome chip honesty). */
  snapshotLoading: boolean;
  /** Last exact-session GET failure — cleared by the next successful snapshot (F16). */
  snapshotError?: SnapshotErrorInfo;
  /** Per-kind echo-verified effective values (SetResult evidence) — see EchoEvidence. */
  echoEvidence: { model?: EchoEvidence; effort?: EchoEvidence };
  /** Last set-route HTTP failure (R3: 404/409/503/transport) — cleared on the next set. */
  setRouteError?: SetRouteErrorInfo;
  /** The serialized model+effort pair change in progress (R5) — absent when none. */
  pairChange?: PairChangeState;
  /** Per-kind so a pair change never clobbers the other knob's in-flight set. */
  pendingSets: { model?: PendingSet; effort?: PendingSet };
  setLedger: SetLedgerEntry[];
  launchEvidence: {
    retainedModel?: string;
    retainedEffort?: string;
    tier: EvidenceTier;
  };
  composer: {
    draft: string;
    draftRevision: number /* submit lifecycle lands in L5 */;
  };
  surfaceTab: "terminal"; // 'transcript' joins when UA-1 lands
  /** Client-measured turn clock (~-labeled, sweep-bounded honesty): observed transitions only. */
  turnClock: { workingSince: number | null; lastObservedTurnState?: string };
  /** Per-pane freshness (R15). 'none' = no PTY attached in this cockpit yet (L6 writes it). */
  freshness: {
    ptyWs: "none" | "connected" | "reconnecting" | "dropped";
    lastOutputAt: number | null;
  };
  /** The cockpit's OWN queued submits (a list, not a chip — F13); whole truth is UA-8-gated. */
  queue: QueuedSubmit[];
  /** Durable-for-the-view receipt/reconciliation ledger; L7 renders this in the inspector. */
  submitHistory: SubmitRecord[];
  /** One bounded Alt+Up transaction: response-loss convergence or revision-safe recovery. */
  withdrawal?: WithdrawalState;
  /** InteractionBar round-trip state (L6 R4 / design §7.3 F7) — absent when nothing in flight. */
  interactionAnswer?: InteractionAnswerState;
}

/** One pending-interaction answer round-trip: in-flight → verbatim error | answered-waiting. */
export interface InteractionAnswerState {
  interactionId: string;
  inflight: boolean;
  /** Exact payload retained for a same-interaction retry. */
  answer: string;
  /** Composer revision that owns the answer; retry success may clear only this revision. */
  draftRevision?: number;
  /** The POST failure, verbatim (never silent) — cleared by retry. */
  error?: string;
  /** Set on 202: the bar renders "answered — waiting for the agent" until the row clears. */
  answeredAt?: number;
}

const emptyPerSession = (): PerSessionCockpit => ({
  snapshotLoading: false,
  echoEvidence: {},
  pendingSets: {},
  setLedger: [],
  launchEvidence: { tier: "pending" },
  composer: { draft: "", draftRevision: 0 },
  surfaceTab: "terminal",
  turnClock: { workingSince: null },
  freshness: { ptyWs: "none", lastOutputAt: null },
  queue: [],
  submitHistory: [],
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
  pollHealth: {
    lastBeatAt: number | null;
    missedBeats: number;
    healthy: boolean;
  };
  perSession: Record<string, PerSessionCockpit>;

  setFocusedSession: (id: string | null) => void;
  setLayout: (layout: { railCollapsed: boolean; inspectorCollapsed: boolean }) => void;
  setPaletteOpen: (open: boolean) => void;
  setOrchestrationTreeView: (on: boolean) => void;
  recordPollBeat: (ok: boolean) => void;
  setComposerDraft: (id: string, draft: string) => void;
  /** Replace only the exact visible revision the caller rendered. */
  replaceComposerDraftIfRevision: (id: string, expectedRevision: number, draft: string) => boolean;
  /** Clear only the exact draft revision that entered an accepted submit. */
  clearComposerDraftIfRevision: (id: string, submittedRevision: number) => boolean;
  recordPendingSet: (id: string, kind: "model" | "effort", pending: PendingSet) => void;
  clearPendingSet: (id: string, kind: "model" | "effort") => void;
  appendSetLedger: (
    id: string,
    entry: Omit<SetLedgerEntry, "acknowledged"> & { acknowledged?: boolean },
  ) => void;
  acknowledgeSetOutcomes: (id: string) => void;
  /** Acknowledge only the entries a definitive readback made moot (kind + requested value). */
  acknowledgeMatchingOutcomes: (
    id: string,
    kind: "model" | "effort",
    requestedValue: string,
  ) => void;
  setLaunchEvidence: (id: string, evidence: PerSessionCockpit["launchEvidence"]) => void;
  setLiveSnapshot: (id: string, snapshot: CapabilitySnapshot) => void;
  setSnapshotLoading: (id: string, loading: boolean) => void;
  setSnapshotError: (id: string, error: SnapshotErrorInfo | undefined) => void;
  recordEchoEvidence: (id: string, kind: "model" | "effort", evidence: EchoEvidence) => void;
  setSetRouteError: (id: string, error: SetRouteErrorInfo | undefined) => void;
  setPairChange: (id: string, pairChange: PairChangeState | undefined) => void;
  enqueueSubmit: (id: string, submit: QueuedSubmit) => void;
  dequeueSubmit: (id: string, requestId: string) => void;
  upsertSubmitRecord: (id: string, record: SubmitRecord) => void;
  setWithdrawal: (id: string, withdrawal: WithdrawalState | undefined) => void;
  /** Clear only the exact recovery choice rendered beside the exact current draft revision. */
  dismissWithdrawalRecoveryIfMatches: (
    id: string,
    requestId: string,
    expectedDraftRevision: number,
  ) => boolean;
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
  layout: { railCollapsed: false, inspectorCollapsed: true },
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
  replaceComposerDraftIfRevision: (id, expectedRevision, draft) => {
    let replaced = false;
    set((state) =>
      withPerSession(state, id, (current) => {
        if (current.composer.draftRevision !== expectedRevision) return current;
        replaced = true;
        return {
          ...current,
          composer: {
            draft,
            draftRevision: current.composer.draftRevision + 1,
          },
        };
      }),
    );
    return replaced;
  },
  clearComposerDraftIfRevision: (id, submittedRevision) => {
    let cleared = false;
    set((state) =>
      withPerSession(state, id, (current) => {
        if (current.composer.draftRevision !== submittedRevision) return current;
        cleared = true;
        return {
          ...current,
          composer: {
            draft: "",
            draftRevision: current.composer.draftRevision + 1,
          },
        };
      }),
    );
    return cleared;
  },
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
        // acknowledged defaults false; benign outcomes (immediate, non-clamp echo-verified,
        // queued) are appended pre-acknowledged by the set client — only outcomes that DEMAND
        // attention (unsupported / clamp / unknown) drive the rail marker (R6).
        setLedger: [...current.setLedger, { acknowledged: false, ...entry }],
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
  acknowledgeMatchingOutcomes: (id, kind, requestedValue) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        setLedger: current.setLedger.map((entry) =>
          !entry.acknowledged && entry.kind === kind && entry.requestedValue === requestedValue
            ? { ...entry, acknowledged: true }
            : entry,
        ),
      })),
    ),
  setLaunchEvidence: (id, launchEvidence) =>
    set((state) => withPerSession(state, id, (current) => ({ ...current, launchEvidence }))),
  setLiveSnapshot: (id, liveSnapshot) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        liveSnapshot,
        // A successful readback clears the fetch-failure state — the two never coexist (F16).
        snapshotLoading: false,
        snapshotError: undefined,
      })),
    ),
  setSnapshotLoading: (id, snapshotLoading) =>
    set((state) => withPerSession(state, id, (current) => ({ ...current, snapshotLoading }))),
  setSnapshotError: (id, snapshotError) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        snapshotError,
        snapshotLoading: false,
      })),
    ),
  recordEchoEvidence: (id, kind, evidence) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        echoEvidence: { ...current.echoEvidence, [kind]: evidence },
      })),
    ),
  setSetRouteError: (id, setRouteError) =>
    set((state) => withPerSession(state, id, (current) => ({ ...current, setRouteError }))),
  setPairChange: (id, pairChange) =>
    set((state) => withPerSession(state, id, (current) => ({ ...current, pairChange }))),
  enqueueSubmit: (id, submit) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        queue: compactSubmitQueue([...current.queue, submit]),
      })),
    ),
  dequeueSubmit: (id, requestId) =>
    set((state) =>
      withPerSession(state, id, (current) => ({
        ...current,
        queue: current.queue.filter((item) => item.requestId !== requestId),
      })),
    ),
  upsertSubmitRecord: (id, record) =>
    set((state) =>
      withPerSession(state, id, (current) => {
        const index = current.submitHistory.findIndex(
          (candidate) => candidate.requestId === record.requestId,
        );
        const submitHistory = [...current.submitHistory];
        if (index === -1) submitHistory.push(record);
        else submitHistory[index] = record;
        return {
          ...current,
          submitHistory: compactSubmitHistory(submitHistory),
        };
      }),
    ),
  setWithdrawal: (id, withdrawal) =>
    set((state) => withPerSession(state, id, (current) => ({ ...current, withdrawal }))),
  dismissWithdrawalRecoveryIfMatches: (id, requestId, expectedDraftRevision) => {
    let dismissed = false;
    set((state) =>
      withPerSession(state, id, (current) => {
        if (
          current.composer.draftRevision !== expectedDraftRevision ||
          current.withdrawal?.phase !== "recovery" ||
          current.withdrawal.requestId !== requestId
        ) {
          return current;
        }
        dismissed = true;
        return { ...current, withdrawal: undefined };
      }),
    );
    return dismissed;
  },
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
      withPerSession(state, id, (current) => ({
        ...current,
        interactionAnswer: answer,
      })),
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
