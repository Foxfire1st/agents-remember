import { describe, expect, it } from "vitest";

import {
  L5_EXACT_TEXT,
  L5_DUPLICATE_CONVERGENCE_FIXTURE,
  L5_OTHER_REQUEST_ID,
  L5_REQUEST_ID,
  RECEIPT_ACCEPTANCES,
  RECONCILIATION_STATES,
  reconciliationResult,
  submitReceipt,
} from "../test/fixtures/submitScenarios";
import {
  enterEndgame,
  enterReconcileWindow,
  latestActiveSubmit,
  LIFECYCLE_WATCH_WINDOW_MS,
  lifecycleWatchState,
  projectSubmissionLifecycle,
  RECONCILE_WINDOW_MS,
  reconcileDelay,
  recordReconcileDelay,
  reduceReceipt,
  reduceReconciliation,
  releaseDraft,
  retryPayload,
  serverConfirmedQueued,
  settleSubmissionObservation,
  startSubmitRecord,
  type SubmissionLifecycleObservation,
  type SubmitPhase,
} from "./submitMachine";

const start = () =>
  startSubmitRecord({
    requestId: L5_REQUEST_ID,
    text: L5_EXACT_TEXT,
    expectedBridgeEpoch: "bridge-epoch-l5",
    submittedRevision: 7,
    at: 100,
  });

describe("submit receipt machine", () => {
  it.each(
    RECEIPT_ACCEPTANCES.map((acceptance) => [
      acceptance,
      {
        immediate: "accepted",
        queued: "queued",
        rejected: "rejected",
        unknown: "ambiguous",
        unsupported: "unsupported",
      }[acceptance] as SubmitPhase,
    ]),
  )("maps the %s receipt exhaustively to %s", (acceptance, expected) => {
    const result = reduceReceipt(start(), submitReceipt(acceptance), 200);
    expect(result.phase).toBe(expected);
    expect(result.requestId).toBe(L5_REQUEST_ID);
    expect(result.text).toBe(L5_EXACT_TEXT);
    expect(result.receipt?.acceptance).toBe(acceptance);
    if (acceptance === "rejected" || acceptance === "unsupported") {
      expect(result.detail).toBe(submitReceipt(acceptance).detail);
    }
  });

  it("treats a bare queued receipt as acceptance evidence, never a lifecycle report", () => {
    const queued = reduceReceipt(start(), submitReceipt("queued"), 200);
    expect(queued.phase).toBe("queued");
    expect(queued.serverLifecycleState).toBeUndefined();
    expect(serverConfirmedQueued(queued)).toBe(false);

    const immediate = reduceReceipt(start(), submitReceipt("immediate"), 200);
    expect(immediate.serverLifecycleState).toBe("delivered");

    // Only the lifecycle endpoint's own word earns (or forfeits) the pre-dispatch queued mark.
    expect(serverConfirmedQueued(projectSubmissionLifecycle(queued, "queued", null, 300))).toBe(
      true,
    );
    expect(
      serverConfirmedQueued(projectSubmissionLifecycle(queued, "dispatching", null, 300)),
    ).toBe(false);
  });

  it.each(
    RECONCILIATION_STATES.map((state) => [
      state,
      {
        accepted: "accepted",
        rejected: "rejected",
        unresolved: "reconciling",
        unsupported: "unsupported",
      }[state] as SubmitPhase,
    ]),
  )("maps the %s reconciliation exhaustively to %s", (state, expected) => {
    const ambiguous = reduceReceipt(start(), submitReceipt("unknown"), 150);
    const result = reduceReconciliation(ambiguous, reconciliationResult(state), 200);
    expect(result.phase).toBe(expected);
    expect(result.reconciliation?.state).toBe(state);
    expect(result.requestId).toBe(L5_REQUEST_ID);
  });

  it("backs off 1s → 2s → 5s and stops before crossing the ~2 minute window", () => {
    let now = 100;
    let record = enterReconcileWindow(start(), now);
    const delays: number[] = [];
    while (true) {
      const delay = reconcileDelay(record.reconcileAttempts);
      if (now + delay > 100 + RECONCILE_WINDOW_MS) break;
      delays.push(delay);
      now += delay;
      record = recordReconcileDelay(record, delay, now);
    }
    expect(delays.slice(0, 4)).toEqual([1_000, 2_000, 5_000, 5_000]);
    expect(record.reconcileWindowElapsedMs).toBe(118_000);
    expect(now + 5_000).toBeGreaterThan(100 + RECONCILE_WINDOW_MS);
    const endgame = enterEndgame(record, 300);
    expect(endgame.phase).toBe("endgame");
    expect(endgame.detail).toContain("keep waiting");
    expect(releaseDraft(endgame, 400)).toMatchObject({ phase: "released", releasedAt: 400 });
  });

  it("never puts edited text under an occupied requestId", () => {
    const fixture = L5_DUPLICATE_CONVERGENCE_FIXTURE;
    expect(retryPayload(start(), fixture.originalText)).toEqual({
      requestId: fixture.requestId,
      text: fixture.originalText,
    });
    const duplicate = retryPayload(start(), fixture.editedText);
    expect(duplicate).toMatchObject({
      requestId: fixture.requestId,
      text: fixture.originalText,
    });
    expect(duplicate.notice).toContain("edited text was not sent");
  });

  it("finds only truly resolving submissions as active", () => {
    const accepted = reduceReceipt(start(), submitReceipt("immediate"), 200);
    const routeError = { ...start(), phase: "route-error" as const };
    const endgame = enterEndgame(enterReconcileWindow(start(), 150), 300);
    const other = startSubmitRecord({
      requestId: L5_OTHER_REQUEST_ID,
      text: "other",
      expectedBridgeEpoch: "bridge-epoch-l5",
      submittedRevision: 8,
      at: 400,
    });
    expect(latestActiveSubmit([accepted])).toBeUndefined();
    expect(latestActiveSubmit([accepted, routeError])).toBe(routeError);
    expect(latestActiveSubmit([accepted, endgame])).toBe(endgame);
    expect(latestActiveSubmit([accepted, endgame, other])).toBe(other);
  });

  it.each([
    { kind: "server", state: "dispatching" },
    { kind: "server", state: "unknown" },
    { kind: "not-found" },
    { kind: "generation-lost" },
    { kind: "server", state: "delivered" },
    { kind: "server", state: "withdrawn" },
    { kind: "server", state: "rejected" },
    { kind: "server", state: "unsupported" },
  ] satisfies SubmissionLifecycleObservation[])(
    "never lets stale queued evidence replace $kind $state",
    (observation) => {
      expect(
        settleSubmissionObservation(
          { observation, version: 2 },
          { kind: "server", state: "queued" },
          1,
        ),
      ).toEqual({ action: "preserve" });
    },
  );

  it.each(["delivered", "withdrawn", "rejected", "unsupported"] as const)(
    "admits definitive %s evidence after an intermediate local projection",
    (state) => {
      expect(
        settleSubmissionObservation(
          { observation: { kind: "generation-lost" }, version: 2 },
          { kind: "server", state },
          1,
        ),
      ).toEqual({
        action: "apply",
        observation: { kind: "server", state },
        reason: "incoming",
      });
    },
  );

  it("joins stale dispatching with newer authority loss as possible-send unknown", () => {
    expect(
      settleSubmissionObservation(
        { observation: { kind: "not-found" }, version: 2 },
        { kind: "server", state: "dispatching" },
        1,
      ),
    ).toEqual({
      action: "apply",
      observation: { kind: "server", state: "unknown" },
      reason: "possible-send-join",
    });
    expect(
      settleSubmissionObservation(
        { observation: { kind: "server", state: "dispatching" }, version: 2 },
        { kind: "generation-lost" },
        1,
      ),
    ).toEqual({ action: "preserve" });
  });
});

describe("lifecycle watch", () => {
  it("aligns the per-entry watch window with the reconcile window", () => {
    expect(LIFECYCLE_WATCH_WINDOW_MS).toBe(RECONCILE_WINDOW_MS);
  });

  it("starts the bounded watch on the first non-terminal post-queue word and keeps its start", () => {
    const queued = projectSubmissionLifecycle(start(), "queued", null, 200);
    expect(queued.lifecycleWatchStartedAt).toBeUndefined();
    expect(lifecycleWatchState(queued, 200)).toBe("active");

    const delivering = projectSubmissionLifecycle(queued, "dispatching", null, 300);
    expect(delivering.lifecycleWatchStartedAt).toBe(300);
    expect(lifecycleWatchState(delivering, 300)).toBe("active");

    // Repeated dispatching polls must not re-arm the window, or it could never expire.
    const repolled = projectSubmissionLifecycle(delivering, "dispatching", null, 400);
    expect(repolled.lifecycleWatchStartedAt).toBe(300);

    // A possible-send unknown stays watched until its own terminal word.
    const unknown = projectSubmissionLifecycle(delivering, "unknown", null, 500);
    expect(unknown.lifecycleWatchStartedAt).toBe(300);
    expect(lifecycleWatchState(unknown, 500)).toBe("active");
  });

  it.each(["delivered", "withdrawn", "rejected", "unsupported"] as const)(
    "ends the watch on the terminal %s word",
    (state) => {
      const delivering = projectSubmissionLifecycle(start(), "dispatching", null, 300);
      const terminal = projectSubmissionLifecycle(delivering, state, null, 400);
      expect(terminal.lifecycleWatchStartedAt).toBe(300);
      expect(lifecycleWatchState(terminal, 400)).toBe("inactive");
    },
  );

  it("expires the watch past the bounded window and never watches other flows' phases", () => {
    const delivering = projectSubmissionLifecycle(start(), "dispatching", null, 1_000);
    expect(lifecycleWatchState(delivering, 1_000 + LIFECYCLE_WATCH_WINDOW_MS)).toBe("active");
    expect(lifecycleWatchState(delivering, 1_001 + LIFECYCLE_WATCH_WINDOW_MS)).toBe("expired");

    for (const phase of [
      "sending",
      "reconciling",
      "endgame",
      "route-error",
      "generation-lost",
      "not-found",
      "released",
    ] as const) {
      expect(lifecycleWatchState({ ...delivering, phase }, 1_000)).toBe("inactive");
    }
  });
});
