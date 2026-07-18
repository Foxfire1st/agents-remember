import type { CapabilitySnapshotWire, SetResultWire } from "../types/harnessCapabilities";
import {
  applyPairReadback,
  applyPairRouteError,
  applyPairStepResult,
  startPairChange,
  type PairChangeState,
} from "./pairChange";
import { announcePolite } from "./announcer";
import {
  cycleEffortTarget,
  deriveEffortMenu,
  effectiveSelection,
  fetchSessionCapabilities,
  type SessionSnapshotOutcome,
} from "./sessionCapabilities";
import { sessionCockpitStore, type SetRouteErrorInfo } from "./sessionCockpitStore";
import { sessionStore, type OpenSession } from "./sessions";
import {
  classifySetResponse,
  reduceSetResult,
  resolvePendingsByReadback,
  shouldRefetchOnTurnEnded,
  toResultSnapshot,
  type SetKind,
  type SetRouteOutcome,
} from "./setAcceptance";
import {
  cycleNoControlAnnouncement,
  cycleNoSnapshotAnnouncement,
  NO_SELECTED_MODEL_COPY,
  promotionAnnouncement,
  readbackKeptPriorAnnouncement,
  setRouteErrorCopy,
  setResultAnnouncement,
} from "./setControlsCopy";

// The set-controls driver (260715-FEUI-L4 S1/S3/S4): the ONLY module that talks to the
// set-model/set-effort routes and the exact-session capability GET, wiring the pure machines
// (setAcceptance, pairChange, sessionCapabilities) into the cockpit store. Every write here
// mirrors server truth; the honesty rules live in the pure modules — this file only sequences IO.

const store = sessionCockpitStore;

function isFocused(sessionId: string): boolean {
  return store.getState().focusedSessionId === sessionId;
}

// ── The exact-session snapshot (single-flight per session) ─────────────────────────────────────

const inflightSnapshots = new Map<string, Promise<SessionSnapshotOutcome>>();
let snapshotGeneration = 0;

/**
 * Dev-bench scenario boundary for exact-session reads. A prior scenario's unresolved snapshot
 * must neither satisfy nor write into a later scenario that happens to reuse the same session id.
 */
export function resetSetClientForDev(): void {
  snapshotGeneration += 1;
  inflightSnapshots.clear();
}

/**
 * GET the exact live session's snapshot, mirror the outcome into the store, and resolve pending
 * sets by readback (R4). Single-flight per session so the popover, the promotion watcher, and
 * the unknown auto-re-GET never stack concurrent GETs. Never throws.
 */
export function refreshSessionSnapshot(
  sessionId: string,
  base = "",
): Promise<SessionSnapshotOutcome> {
  const pending = inflightSnapshots.get(sessionId);
  if (pending) return pending;
  store.getState().setSnapshotLoading(sessionId, true);
  const generation = snapshotGeneration;
  const run = (async (): Promise<SessionSnapshotOutcome> => {
    const outcome = await fetchSessionCapabilities(sessionId, base);
    if (generation !== snapshotGeneration) return outcome;
    if (outcome.kind === "snapshot") {
      store.getState().setLiveSnapshot(sessionId, {
        sessionId,
        fetchedAt: Date.now(),
        payload: outcome.snapshot,
      });
      applySnapshotReadback(sessionId, outcome.snapshot, base);
    } else {
      const status =
        outcome.kind === "session-gone"
          ? "unknown-session"
          : outcome.kind === "no-native-control"
            ? "unsupported"
            : outcome.kind === "outage"
              ? "control-unavailable"
              : "transport";
      store.getState().setSnapshotError(sessionId, {
        httpStatus:
          outcome.kind === "session-gone"
            ? 404
            : outcome.kind === "no-native-control"
              ? 409
              : outcome.kind === "outage"
                ? 503
                : null,
        status,
        detail: outcome.kind === "session-gone" ? "unknown-session" : outcome.detail,
        at: Date.now(),
      });
    }
    return outcome;
  })().finally(() => {
    if (inflightSnapshots.get(sessionId) === run) inflightSnapshots.delete(sessionId);
  });
  inflightSnapshots.set(sessionId, run);
  return run;
}

/** Resolve queued/unknown pendings (and a readback-held pair step) against one snapshot. */
export function applySnapshotReadback(
  sessionId: string,
  snapshot: CapabilitySnapshotWire,
  base = "",
): void {
  const state = store.getState();
  const cockpit = state.perSession[sessionId];
  if (!cockpit) return;
  const resolutions = resolvePendingsByReadback(cockpit.pendingSets, snapshot);
  for (const resolution of resolutions) {
    state.clearPendingSet(sessionId, resolution.kind);
    if (resolution.fromPhase === "unknown-verifying" && resolution.resolution === "confirmed") {
      // The uncertainty resolved as APPLIED — its unknown ledger entry no longer needs
      // attention (a not-applied verdict stays unacknowledged and keeps the marker).
      state.acknowledgeMatchingOutcomes(sessionId, resolution.kind, resolution.requestedValue);
    }
    if (isFocused(sessionId)) {
      announcePolite(
        resolution.resolution === "confirmed"
          ? promotionAnnouncement(resolution.kind, resolution.requestedValue)
          : readbackKeptPriorAnnouncement(resolution.kind, resolution.requestedValue),
      );
    }
    // A readback-held pair step resolves by the SAME verdict (pairChange.ts).
    const pair = store.getState().perSession[sessionId]?.pairChange;
    if (pair && pair.phase === "awaiting-readback" && pair.step === resolution.kind) {
      const directive = applyPairReadback(pair, resolution.resolution === "confirmed");
      commitPairDirective(sessionId, directive.state, directive.sendEffort, base);
    }
  }
}

// ── set-model / set-effort (R2/R3) ─────────────────────────────────────────────────────────────

/**
 * POST one set and mirror the classified outcome. A 200 SetResult — including unknown and
 * unsupported — is EVIDENCE (never a transport failure); 404/409/503/transport land in
 * `setRouteError`. Returns the classified outcome for callers (the pair driver).
 */
export async function sendSet(
  sessionId: string,
  kind: SetKind,
  value: string,
  base = "",
): Promise<SetRouteOutcome> {
  const state = store.getState();
  state.setSetRouteError(sessionId, undefined);
  state.recordPendingSet(sessionId, kind, {
    requestedValue: value,
    sentAt: Date.now(),
    phase: "inflight",
  });
  const route = kind === "model" ? "set-model" : "set-effort";
  const body = kind === "model" ? { model: value } : { effort: value };
  let outcome: SetRouteOutcome;
  try {
    const response = await fetch(
      `${base}/api/terminal/${encodeURIComponent(sessionId)}/${route}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    let parsed: unknown;
    try {
      parsed = await response.json();
    } catch {
      parsed = undefined;
    }
    outcome = classifySetResponse(response.status, parsed);
  } catch (error) {
    outcome = classifySetResponse(null, undefined);
    void error;
  }

  if (outcome.kind === "result") {
    applySetResult(sessionId, kind, outcome.result, base);
  } else {
    // HTTP-boundary failure: the request produced NO evidence — clear the in-flight pending
    // (only if it is still ours) and keep the verbatim route error (503 gets the retry).
    const current = store.getState().perSession[sessionId];
    if (current?.pendingSets[kind]?.requestedValue === value) {
      store.getState().clearPendingSet(sessionId, kind);
    }
    const routeError: SetRouteErrorInfo = {
      kind,
      requestedValue: value,
      httpStatus:
        outcome.kind === "session-gone"
          ? 404
          : outcome.kind === "no-native-control"
            ? 409
            : outcome.kind === "outage"
              ? 503
              : null,
      status:
        outcome.kind === "session-gone"
          ? "unknown-session"
          : outcome.kind === "no-native-control"
            ? "unsupported"
            : outcome.kind === "outage"
              ? "control-unavailable"
              : "transport",
      detail:
        outcome.kind === "session-gone"
          ? "unknown-session"
          : outcome.detail,
      retryable: outcome.kind === "outage",
      at: Date.now(),
    };
    store.getState().setSetRouteError(sessionId, routeError);

    // A route failure produced no SetResult for the pair machine to consume. End the matching
    // pair step explicitly: the route chip tells the HTTP story, while the pair outcome replaces
    // the otherwise-eternal progress spinner with aborted/partial truth.
    const pair = store.getState().perSession[sessionId]?.pairChange;
    if (pair && !pair.outcome && pair.step === kind) {
      const expected = kind === "model" ? pair.modelKey : pair.effort;
      if (expected === value) {
        const directive = applyPairRouteError(pair, kind, setRouteErrorCopy(routeError));
        commitPairDirective(sessionId, directive.state, directive.sendEffort, base);
      }
    }
  }
  return outcome;
}

/** Mirror one SetResult into the store via the pure reducer (the honesty table, R2). */
export function applySetResult(
  sessionId: string,
  kind: SetKind,
  result: SetResultWire,
  base = "",
): void {
  const reduction = reduceSetResult(result);
  const state = store.getState();
  state.appendSetLedger(sessionId, {
    at: Date.now(),
    kind,
    requestedValue: result.requestedValue,
    result: toResultSnapshot(result),
    acknowledged: reduction.ledger.acknowledged,
  });
  // Supersede guard: this result moves the pending ONLY if the pending is still for the value
  // it answered — a newer request's pending is never cleared by an older response. The ledger
  // append above is unconditional (every SetResult is evidence).
  const pending = state.perSession[sessionId]?.pendingSets[kind];
  const owns = pending?.requestedValue === result.requestedValue;
  if (owns) {
    if (reduction.pendingPhase === undefined) {
      state.clearPendingSet(sessionId, kind);
    } else {
      state.recordPendingSet(sessionId, kind, {
        requestedValue: result.requestedValue,
        sentAt: pending?.sentAt ?? Date.now(),
        phase: reduction.pendingPhase,
      });
    }
  }
  if (reduction.echoEffective !== null) {
    // Server-proved effective value — the only set path that moves the marker.
    state.recordEchoEvidence(sessionId, kind, {
      value: reduction.echoEffective,
      at: Date.now(),
    });
  }
  if (reduction.autoReGet) {
    // 'unknown' → exactly ONE automatic exact-session re-GET; no blind retry (R2).
    void refreshSessionSnapshot(sessionId, base);
  }
  if (isFocused(sessionId)) announcePolite(setResultAnnouncement(kind, result));

  // Pair-change advancement (R5): the machine consumes the SAME evidence.
  const pair = store.getState().perSession[sessionId]?.pairChange;
  if (pair && !pair.outcome && pair.step === kind) {
    const expected = kind === "model" ? pair.modelKey : pair.effort;
    if (expected === result.requestedValue) {
      const directive = applyPairStepResult(pair, kind, result);
      commitPairDirective(sessionId, directive.state, directive.sendEffort, base);
    }
  }
}

// ── The serialized pair change (R5) ────────────────────────────────────────────────────────────

function commitPairDirective(
  sessionId: string,
  next: PairChangeState,
  sendEffort: boolean,
  base = "",
): void {
  const state = store.getState();
  if (next.outcome?.kind === "completed") {
    // Both requests accepted — the per-kind pendings/chips carry any remaining queued honesty.
    state.setPairChange(sessionId, undefined);
  } else {
    state.setPairChange(sessionId, next);
  }
  if (sendEffort) {
    void sendSet(sessionId, "effort", next.effort, base);
  }
}

/**
 * Start the serialized model+effort pair change: set-model → evidence → set-effort. Partial
 * failure keeps its designed rendering (pairChange.outcome kind 'partial') with both SetResults
 * in the ledger; the aborted/partial banner clears on acknowledgment.
 */
export function startPairChangeFlow(
  sessionId: string,
  modelKey: string,
  effort: string,
  base = "",
): void {
  store.getState().setPairChange(sessionId, startPairChange(modelKey, effort));
  void sendSet(sessionId, "model", modelKey, base);
}

/** Explicit acknowledgment (F22): marks ledger outcomes seen and clears a finished pair banner. */
export function acknowledgeSetAttention(sessionId: string): void {
  const state = store.getState();
  state.acknowledgeSetOutcomes(sessionId);
  const pair = state.perSession[sessionId]?.pairChange;
  if (pair?.outcome) state.setPairChange(sessionId, undefined);
}

// ── Cycle-effort (R7) ──────────────────────────────────────────────────────────────────────────

/**
 * Cycle the REQUESTED effort through the current model's sessionSettable options — no dialog.
 * Requires a live snapshot for the menu: without one, this kicks the fetch and announces why
 * nothing was set (never an invented menu, never a blind set).
 */
export function cycleEffortRequested(sessionId: string, direction: 1 | -1, base = ""): void {
  const cockpit = store.getState().perSession[sessionId];
  const snapshot = cockpit?.liveSnapshot?.payload;
  if (!snapshot) {
    announcePolite(cycleNoSnapshotAnnouncement());
    void refreshSessionSnapshot(sessionId, base);
    return;
  }
  const menu = deriveEffortMenu(snapshot);
  if (menu.kind === "no-selected-model") {
    announcePolite(NO_SELECTED_MODEL_COPY);
    return;
  }
  if (menu.kind === "no-effort-control") {
    announcePolite(cycleNoControlAnnouncement());
    return;
  }
  const current =
    cockpit?.pendingSets.effort?.requestedValue ?? effectiveSelection(cockpit).effort;
  const next = cycleEffortTarget(menu.options, current, direction);
  if (next === null) return;
  void sendSet(sessionId, "effort", next, base);
}

// ── The promotion/drift watcher (R4 + v3 delta) ────────────────────────────────────────────────

function canAutoFetch(session: OpenSession | undefined): boolean {
  return (
    session !== undefined &&
    (session.status ?? "running") === "running" &&
    session.controlState === "ready"
  );
}

let watcherRefs = 0;
let unsubscribeTurns: (() => void) | null = null;
let unsubscribeFocus: (() => void) | null = null;

/**
 * Refcounted watcher wiring the re-GET triggers (design §4):
 *   - EVERY transition into turn-ended of the FOCUSED session re-GETs (vendor drift);
 *   - any turn-ended of a session holding queued/unknown pendings re-GETs (promotion — turns
 *     start from PTY-typed lines and inbox deliveries too, so the trigger reads the observed
 *     turn state, never cockpit submits);
 *   - an unfocused session re-GETs when it GAINS focus.
 */
export function startSetPromotionWatcher(base = ""): () => void {
  watcherRefs += 1;
  if (watcherRefs === 1) {
    let previousTurnStates = new Map<string, string | undefined>();
    for (const session of sessionStore.getState().sessions) {
      previousTurnStates.set(session.id, session.turnState);
    }
    unsubscribeTurns = sessionStore.subscribe((state) => {
      const next = new Map<string, string | undefined>();
      const cockpit = store.getState();
      for (const session of state.sessions) {
        next.set(session.id, session.turnState);
        const before = previousTurnStates.get(session.id);
        const endedNow = session.turnState === "turn-ended" && before !== "turn-ended";
        if (!endedNow) continue;
        const focused = cockpit.focusedSessionId === session.id;
        const pendings = cockpit.perSession[session.id]?.pendingSets;
        if (shouldRefetchOnTurnEnded(focused, pendings) && canAutoFetch(session)) {
          void refreshSessionSnapshot(session.id, base);
        }
      }
      previousTurnStates = next;
    });

    let previousFocus = store.getState().focusedSessionId;
    unsubscribeFocus = store.subscribe((state) => {
      if (state.focusedSessionId === previousFocus) return;
      previousFocus = state.focusedSessionId;
      if (!previousFocus) return;
      const session = sessionStore
        .getState()
        .sessions.find((row) => row.id === previousFocus);
      if (canAutoFetch(session)) void refreshSessionSnapshot(previousFocus, base);
    });
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    watcherRefs -= 1;
    if (watcherRefs === 0) {
      unsubscribeTurns?.();
      unsubscribeTurns = null;
      unsubscribeFocus?.();
      unsubscribeFocus = null;
    }
  };
}
