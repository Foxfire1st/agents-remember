import type {
  ReconciliationResultWire,
  SubmissionLifecycleState,
  SubmissionReceiptWire,
} from "../types/harnessCapabilities";

// The reliable-submit state machine (260715-FEUI-L5 S2/S3). This module is deliberately pure:
// the five-value receipt alphabet and four-value reconciliation alphabet are wire truth, while
// timers, fetch, clipboard, and store writes live in submitClient / the React surfaces.

export const RECONCILE_BACKOFF_MS = [1_000, 2_000, 5_000] as const;
export const RECONCILE_WINDOW_MS = 120_000;

export type SubmitSource = "composer" | "leaf-context" | "highlight" | "background";

export type SubmitPhase =
  | "sending"
  | "accepted"
  | "queued"
  | "delivering"
  | "withdrawn"
  | "rejected"
  | "unsupported"
  | "ambiguous"
  | "reconciling"
  | "endgame"
  | "released"
  | "generation-lost"
  | "not-found"
  | "route-error";

export interface SubmitRouteFailure {
  httpStatus: number | null;
  status: string;
  detail: string;
}

export interface SubmitRecord {
  requestId: string;
  text: string;
  /** Runner generation captured before submit; every lifecycle request is bound to it. */
  expectedBridgeEpoch: string;
  /** Provenance controls which surface may render/restore this record. */
  source: SubmitSource;
  submittedRevision: number;
  /** True only when this record owns the visible per-session composer revision. */
  clearDraftOnAccept: boolean;
  startedAt: number;
  updatedAt: number;
  phase: SubmitPhase;
  receipt?: SubmissionReceiptWire;
  reconciliation?: ReconciliationResultWire;
  routeFailure?: SubmitRouteFailure;
  reconcileAttempts: number;
  /** Wall-clock elapsed inside the CURRENT bounded wait window. Keep-waiting starts a fresh window. */
  reconcileWindowElapsedMs: number;
  reconcileWindowStartedAt?: number;
  detail?: string;
  releasedAt?: number;
  restoredAt?: number;
  /** Explicit choice to keep a newer draft and discard the one-slot withdrawn-text recovery. */
  recoveryDismissedAt?: number;
  serverLifecycleState?: SubmissionLifecycleState;
  /** Monotonic per-request authority observation used to reject overlapping stale responses. */
  lifecycleObservationVersion: number;
}

export type SubmissionLifecycleObservation =
  | { kind: "server"; state: SubmissionLifecycleState }
  | { kind: "not-found" }
  | { kind: "generation-lost" };

export interface SubmissionObservationSnapshot {
  observation: SubmissionLifecycleObservation;
  version: number;
}

export type SubmissionObservationSettlement =
  | { action: "preserve" }
  | {
      action: "apply";
      observation: SubmissionLifecycleObservation;
      reason: "incoming" | "possible-send-join";
    };

type ObservationEvidence =
  | "queued"
  | "availability-loss"
  | "dispatching"
  | "unknown"
  | "definitive";

const DEFINITIVE_SERVER_STATES = new Set<SubmissionLifecycleState>([
  "delivered",
  "withdrawn",
  "rejected",
  "unsupported",
]);

const OBSERVATION_EVIDENCE_RANK: Record<ObservationEvidence, number> = {
  queued: 0,
  "availability-loss": 1,
  dispatching: 2,
  unknown: 3,
  definitive: 4,
};

function observationEvidence(observation: SubmissionLifecycleObservation): ObservationEvidence {
  if (observation.kind !== "server") return "availability-loss";
  if (observation.state === "queued") return "queued";
  if (observation.state === "dispatching") return "dispatching";
  if (observation.state === "unknown") return "unknown";
  return DEFINITIVE_SERVER_STATES.has(observation.state) ? "definitive" : "queued";
}

export function submissionObservationSnapshot(
  record: SubmitRecord | undefined,
): SubmissionObservationSnapshot | undefined {
  if (!record) return undefined;
  if (record.phase === "not-found") {
    return { observation: { kind: "not-found" }, version: record.lifecycleObservationVersion };
  }
  if (record.phase === "generation-lost") {
    return {
      observation: { kind: "generation-lost" },
      version: record.lifecycleObservationVersion,
    };
  }
  return record.serverLifecycleState
    ? {
        observation: { kind: "server", state: record.serverLifecycleState },
        version: record.lifecycleObservationVersion,
      }
    : undefined;
}

/**
 * Fold overlapping status and withdrawal evidence without treating client request order as server
 * truth. A weaker observation never replaces stronger evidence, while a definitive server result
 * remains admissible even when its request began against an older client version. Dispatching and
 * local authority loss join to unknown: possible send is proven, but present delivery state is not.
 */
export function settleSubmissionObservation(
  current: SubmissionObservationSnapshot | undefined,
  incoming: SubmissionLifecycleObservation,
  observedVersion: number,
): SubmissionObservationSettlement {
  if (!current) return { action: "apply", observation: incoming, reason: "incoming" };

  const currentEvidence = observationEvidence(current.observation);
  const incomingEvidence = observationEvidence(incoming);
  const currentIsNewer = current.version > observedVersion;

  if (
    (currentEvidence === "availability-loss" && incomingEvidence === "dispatching") ||
    (currentEvidence === "dispatching" &&
      incomingEvidence === "availability-loss" &&
      !currentIsNewer)
  ) {
    return {
      action: "apply",
      observation: { kind: "server", state: "unknown" },
      reason: "possible-send-join",
    };
  }

  const currentRank = OBSERVATION_EVIDENCE_RANK[currentEvidence];
  const incomingRank = OBSERVATION_EVIDENCE_RANK[incomingEvidence];
  if (incomingRank > currentRank) {
    return { action: "apply", observation: incoming, reason: "incoming" };
  }
  if (incomingRank < currentRank || currentIsNewer) return { action: "preserve" };
  return { action: "apply", observation: incoming, reason: "incoming" };
}

export function startSubmitRecord(input: {
  requestId: string;
  text: string;
  expectedBridgeEpoch: string;
  source?: SubmitSource;
  submittedRevision: number;
  clearDraftOnAccept?: boolean;
  at: number;
}): SubmitRecord {
  const source = input.source ?? "composer";
  return {
    requestId: input.requestId,
    text: input.text,
    expectedBridgeEpoch: input.expectedBridgeEpoch,
    source,
    submittedRevision: input.submittedRevision,
    clearDraftOnAccept: input.clearDraftOnAccept ?? source === "composer",
    startedAt: input.at,
    updatedAt: input.at,
    phase: "sending",
    reconcileAttempts: 0,
    reconcileWindowElapsedMs: 0,
    lifecycleObservationVersion: 0,
  };
}

/** Exhaustive reducer for the server's five-value submission receipt. */
export function reduceReceipt(
  current: SubmitRecord,
  receipt: SubmissionReceiptWire,
  at: number,
): SubmitRecord {
  const common = {
    ...current,
    receipt,
    updatedAt: at,
    detail: receipt.detail ?? undefined,
    expectedBridgeEpoch: receipt.bridgeEpoch,
  };
  switch (receipt.acceptance) {
    case "immediate":
      return {
        ...common,
        phase: "accepted",
        serverLifecycleState: "delivered" as const,
      };
    case "queued":
      return {
        ...common,
        phase: "queued",
        serverLifecycleState: "queued" as const,
      };
    case "rejected":
      return {
        ...common,
        phase: "rejected",
        serverLifecycleState: "rejected" as const,
      };
    case "unsupported":
      return {
        ...common,
        phase: "unsupported",
        serverLifecycleState: "unsupported" as const,
      };
    case "unknown":
      return {
        ...common,
        phase: "ambiguous",
        serverLifecycleState: "unknown" as const,
      };
  }
}

export function projectSubmissionLifecycle(
  current: SubmitRecord,
  state: SubmissionLifecycleState,
  detail: string | null,
  at: number,
): SubmitRecord {
  const phase: SubmitPhase = {
    queued: "queued",
    dispatching: "delivering",
    delivered: "accepted",
    withdrawn: "withdrawn",
    unknown: "ambiguous",
    rejected: "rejected",
    unsupported: "unsupported",
  }[state] as SubmitPhase;
  return {
    ...current,
    phase,
    serverLifecycleState: state,
    detail: detail ?? undefined,
    updatedAt: at,
    lifecycleObservationVersion: current.lifecycleObservationVersion + 1,
  };
}

/** Exhaustive reducer for one same-request-id reconciliation result. */
export function reduceReconciliation(
  current: SubmitRecord,
  result: ReconciliationResultWire,
  at: number,
): SubmitRecord {
  const common = {
    ...current,
    reconciliation: result,
    updatedAt: at,
    detail: result.detail ?? undefined,
    expectedBridgeEpoch: result.bridgeEpoch,
  };
  if (
    result.submissionState &&
    !(result.state === "unresolved" && result.submissionState === "unknown")
  ) {
    return projectSubmissionLifecycle(common, result.submissionState, result.detail, at);
  }
  switch (result.state) {
    case "accepted":
      return { ...common, phase: "accepted" };
    case "rejected":
      return { ...common, phase: "rejected" };
    case "unsupported":
      return { ...common, phase: "unsupported" };
    case "unresolved":
      return { ...common, phase: "reconciling" };
  }
}

export function reconcileDelay(attempt: number): number {
  return RECONCILE_BACKOFF_MS[Math.min(attempt, RECONCILE_BACKOFF_MS.length - 1)];
}

export function enterReconcileWindow(
  record: SubmitRecord,
  at: number,
  windowStartedAt = at,
): SubmitRecord {
  return {
    ...record,
    phase: "reconciling",
    updatedAt: at,
    reconcileWindowStartedAt: windowStartedAt,
    reconcileWindowElapsedMs: Math.max(0, at - windowStartedAt),
  };
}

export function recordReconcileDelay(
  record: SubmitRecord,
  _delayMs: number,
  at: number,
): SubmitRecord {
  const startedAt = record.reconcileWindowStartedAt ?? at;
  return {
    ...record,
    phase: "reconciling",
    updatedAt: at,
    reconcileAttempts: record.reconcileAttempts + 1,
    reconcileWindowElapsedMs: Math.max(record.reconcileWindowElapsedMs, at - startedAt),
  };
}

export function refreshReconcileElapsed(record: SubmitRecord, at: number): SubmitRecord {
  const startedAt = record.reconcileWindowStartedAt ?? at;
  return {
    ...record,
    updatedAt: at,
    reconcileWindowElapsedMs: Math.max(record.reconcileWindowElapsedMs, at - startedAt),
  };
}

export function enterEndgame(record: SubmitRecord, at: number): SubmitRecord {
  return {
    ...record,
    phase: "endgame",
    updatedAt: at,
    detail: "still unresolved — keep waiting, copy requestId, or release the retained draft",
  };
}

export function releaseDraft(record: SubmitRecord, at: number): SubmitRecord {
  return { ...record, phase: "released", updatedAt: at, releasedAt: at };
}

/**
 * The server keys idempotency by the immutable requestId/source/payload tuple and rejects a
 * differing tuple as a conflict. A retry therefore always carries the original immutable text;
 * a differing draft is named to the user and is never sent under the occupied id.
 */
export function retryPayload(
  record: SubmitRecord,
  candidateText: string,
): { requestId: string; text: string; notice?: string } {
  return candidateText === record.text
    ? { requestId: record.requestId, text: record.text }
    : {
        requestId: record.requestId,
        text: record.text,
        notice: `request ${record.requestId} already belongs to its first message; the edited text was not sent`,
      };
}

export function latestActiveSubmit(records: readonly SubmitRecord[]): SubmitRecord | undefined {
  return [...records]
    .reverse()
    .find((record) =>
      ["sending", "ambiguous", "reconciling", "endgame", "route-error"].includes(record.phase),
    );
}
