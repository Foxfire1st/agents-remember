import {
  sessionCockpitStore,
  type PerSessionCockpit,
} from "./sessionCockpitStore";
import {
  BridgeEpochMismatchError,
  applyLifecycleObservation,
  clearSubmissionAuthorityCache,
  createFetchSubmissionLifecycleTransport,
  markGenerationLost,
  markNotRetained,
  matchingWithdrawalRecovery,
  noticeForState,
  observationVersion,
  preservedObservationNotice,
  scenarioGeneration,
  scenarioGenerationIsCurrent,
  submissionRecord,
  withdrawalsInFlight,
  type AppliedLifecycleObservation,
  type PendingWithdrawal,
  type SubmissionLifecycleTransport,
  type SubmissionLookupWire,
  type WithdrawalResultWire,
} from "./submissionLifecycleClient";

export interface WithdrawQueuedOptions {
  transport?: SubmissionLifecycleTransport;
  now?: () => number;
}

function withdrawalTargetFor(
  sessionId: string,
  store: ReturnType<typeof sessionCockpitStore.getState>,
  now: () => number,
): { target?: PendingWithdrawal; blocked?: string } {
  const cockpit = store.perSession[sessionId];
  if (cockpit?.withdrawal?.phase === "recovery") {
    return {
      blocked: "withdrawn text is retained for recovery; choose replace or keep current draft",
    };
  }
  let target = queuedWithdrawalTarget(cockpit);
  if (!target) {
    target = queuedEntryTarget(cockpit, now);
    if (target) store.setWithdrawal(sessionId, target);
  }
  if (!target) {
    return { blocked: latestSubmitBlock(sessionId) };
  }
  return { target };
}

function queuedWithdrawalTarget(cockpit: PerSessionCockpit | undefined): PendingWithdrawal | undefined {
  return cockpit?.withdrawal?.phase === "pending" ? cockpit.withdrawal : undefined;
}

function queuedEntryTarget(
  cockpit: PerSessionCockpit | undefined,
  now: () => number,
): PendingWithdrawal | undefined {
  const entry = [...(cockpit?.queue ?? [])]
    .reverse()
    .find((candidate) => candidate.state === "queued");
  if (!entry) return undefined;
  return {
    phase: "pending",
    requestId: entry.requestId,
    text: entry.text,
    expectedBridgeEpoch: entry.expectedBridgeEpoch,
    draftRevision: cockpit?.composer.draftRevision ?? 0,
    requestedAt: now(),
  };
}

function latestSubmitBlock(sessionId: string): string {
  const latest = sessionCockpitStore.getState().perSession[sessionId]?.submitHistory.at(-1);
  return latest?.phase === "accepted" || latest?.phase === "delivering"
    ? "already delivered or dispatching — there is no queued message to edit"
    : "no authoritatively queued message to edit";
}

function appliedWithdrawalNotice(
  sessionId: string,
  requestId: string,
  applied: AppliedLifecycleObservation,
): string {
  return applied.preserved
    ? preservedObservationNotice(sessionId, requestId, applied.record)
    : (applied.notice ?? "withdrawn before dispatch");
}

function stateAppliedNotice(
  sessionId: string,
  requestId: string,
  applied: AppliedLifecycleObservation,
): string {
  return applied.preserved
    ? preservedObservationNotice(sessionId, requestId, applied.record)
    : applied.observation.kind === "server"
      ? noticeForState(applied.observation.state)
      : "the queued message is no longer withdrawable";
}

function lostAfterWithdraw(sessionId: string, target: PendingWithdrawal, now: () => number): string {
  const marked = markNotRetained(
    sessionId,
    target.requestId,
    "withdraw response was lost and the request is not retained; withdrawal is unavailable and the draft was not restored",
    now(),
    observationVersion(submissionRecord(sessionId, target.requestId)),
    { withdrawalOwner: true, issuedWithdrawal: target },
  );
  if (marked.preserved) {
    return preservedObservationNotice(sessionId, target.requestId, marked.record);
  }
  return "withdraw response was lost and the request is not retained; the draft was not restored";
}

function withdrawnAfterStatus(
  sessionId: string,
  target: PendingWithdrawal,
  lookup: Extract<SubmissionLookupWire, { outcome: "found" }>,
  now: () => number,
): string {
  const applied = applyLifecycleObservation(
    sessionId,
    target.requestId,
    { kind: "server", state: "withdrawn" },
    lookup.submission.detail,
    now(),
    observationVersion(submissionRecord(sessionId, target.requestId)),
    { withdrawalOwner: true, issuedWithdrawal: target },
  );
  return appliedWithdrawalNotice(sessionId, target.requestId, applied);
}

function applyWithdrawalResult(
  sessionId: string,
  target: PendingWithdrawal,
  result: WithdrawalResultWire,
  observedVersion: number,
  now: () => number,
): string {
  if (result.outcome === "withdrawn") {
    const applied = applyLifecycleObservation(
      sessionId,
      target.requestId,
      { kind: "server", state: "withdrawn" },
      result.detail,
      now(),
      observedVersion,
      { withdrawalOwner: true, issuedWithdrawal: target },
    );
    return appliedWithdrawalNotice(sessionId, target.requestId, applied);
  }
  if (result.outcome === "not-withdrawable" && result.state) {
    const applied = applyLifecycleObservation(
      sessionId,
      target.requestId,
      { kind: "server", state: result.state },
      result.detail,
      now(),
      observedVersion,
      { withdrawalOwner: true, issuedWithdrawal: target },
    );
    if (applied.preserved) {
      return preservedObservationNotice(sessionId, target.requestId, applied.record);
    }
    return applied.observation.kind === "server"
      ? noticeForState(applied.observation.state)
      : "the queued message is no longer withdrawable";
  }
  if (result.outcome === "not-found") {
    const marked = markNotRetained(
      sessionId,
      target.requestId,
      "submission is not retained by this authority; withdrawal is unavailable and the draft was not restored",
      now(),
      observedVersion,
      { withdrawalOwner: true, issuedWithdrawal: target },
    );
    if (marked.preserved) {
      return preservedObservationNotice(sessionId, target.requestId, marked.record);
    }
  }
  return "submission is not retained by this authority; the draft was not restored";
}

async function statusFollowUp(
  sessionId: string,
  target: PendingWithdrawal,
  transport: SubmissionLifecycleTransport,
  now: () => number,
  generation: number,
  revokedNotice: string,
): Promise<string> {
  if (matchingWithdrawalRecovery(sessionId, target.requestId)) {
    return preservedObservationNotice(sessionId, target.requestId, null);
  }
  const status = await transport.status(sessionId, target.expectedBridgeEpoch, [
    target.requestId,
  ]);
  if (!scenarioGenerationIsCurrent(generation)) return revokedNotice;
  const lookup = status.submissions[0];
  if (matchingWithdrawalRecovery(sessionId, target.requestId)) {
    return preservedObservationNotice(sessionId, target.requestId, null);
  }
  if (lookup.outcome === "not-found") {
    return lostAfterWithdraw(sessionId, target, now);
  }
  if (lookup.submission.state === "withdrawn") {
    return withdrawnAfterStatus(sessionId, target, lookup, now);
  }
  if (lookup.submission.state === "queued") {
    const retryResult = await transport.withdraw(
      sessionId,
      target.expectedBridgeEpoch,
      target.requestId,
    );
    if (!scenarioGenerationIsCurrent(generation)) return revokedNotice;
    return applyWithdrawalResult(
      sessionId,
      target,
      retryResult,
      observationVersion(submissionRecord(sessionId, target.requestId)),
      now,
    );
  }
  const applied = applyLifecycleObservation(
    sessionId,
    target.requestId,
    { kind: "server", state: lookup.submission.state },
    lookup.submission.detail,
    now(),
    observationVersion(submissionRecord(sessionId, target.requestId)),
    { withdrawalOwner: true, issuedWithdrawal: target },
  );
  return stateAppliedNotice(sessionId, target.requestId, applied);
}

function convergenceCatch(
  sessionId: string,
  target: PendingWithdrawal,
  convergenceError: unknown,
  activeObservedVersion: number,
  generation: number,
  revokedNotice: string,
): string {
  if (!scenarioGenerationIsCurrent(generation)) return revokedNotice;
  if (convergenceError instanceof BridgeEpochMismatchError) {
    const marked = markGenerationLost(
      sessionId,
      target.requestId,
      convergenceError.message,
      Date.now(),
      activeObservedVersion,
      { withdrawalOwner: true, issuedWithdrawal: target },
    );
    clearSubmissionAuthorityCache(sessionId);
    if (marked.preserved) {
      return preservedObservationNotice(sessionId, target.requestId, marked.record);
    }
    return "runner generation changed — retained text was not automatically resent or restored";
  }
  if (matchingWithdrawalRecovery(sessionId, target.requestId)) {
    return preservedObservationNotice(sessionId, target.requestId, null);
  }
  const current = submissionRecord(sessionId, target.requestId);
  if (current && current.lifecycleObservationVersion > activeObservedVersion) {
    return preservedObservationNotice(sessionId, target.requestId, current);
  }
  return "withdrawal status is unavailable — the draft was not restored";
}

async function reconcileWithdrawalAfterLoss(
  sessionId: string,
  target: PendingWithdrawal,
  transport: SubmissionLifecycleTransport,
  now: () => number,
  generation: number,
  revokedNotice: string,
): Promise<string> {
  const activeObservedVersion = observationVersion(
    submissionRecord(sessionId, target.requestId),
  );
  try {
    return await statusFollowUp(sessionId, target, transport, now, generation, revokedNotice);
  } catch (convergenceError) {
    return convergenceCatch(
      sessionId,
      target,
      convergenceError,
      activeObservedVersion,
      generation,
      revokedNotice,
    );
  }
}

async function withdrawLastQueued(
  sessionId: string,
  options: WithdrawQueuedOptions,
): Promise<string> {
  const generation = scenarioGeneration;
  const revokedNotice = "withdrawal result ignored because the dev scenario authority changed";
  const transport = options.transport ?? createFetchSubmissionLifecycleTransport();
  const now = options.now ?? Date.now;
  const store = sessionCockpitStore.getState();
  const selected = withdrawalTargetFor(sessionId, store, now);
  if (selected.blocked) return selected.blocked;
  const withdrawalTarget = selected.target as PendingWithdrawal;
  const activeObservedVersion = observationVersion(
    submissionRecord(sessionId, withdrawalTarget.requestId),
  );
  try {
    const result = await transport.withdraw(
      sessionId,
      withdrawalTarget.expectedBridgeEpoch,
      withdrawalTarget.requestId,
    );
    if (!scenarioGenerationIsCurrent(generation)) return revokedNotice;
    return applyWithdrawalResult(sessionId, withdrawalTarget, result, activeObservedVersion, now);
  } catch (error) {
    if (!scenarioGenerationIsCurrent(generation)) return revokedNotice;
    if (error instanceof BridgeEpochMismatchError) {
      const marked = markGenerationLost(
        sessionId,
        withdrawalTarget.requestId,
        error.message,
        now(),
        activeObservedVersion,
        { withdrawalOwner: true, issuedWithdrawal: withdrawalTarget },
      );
      clearSubmissionAuthorityCache(sessionId);
      if (marked.preserved) {
        return preservedObservationNotice(sessionId, withdrawalTarget.requestId, marked.record);
      }
      return "runner generation changed — retained text was not automatically resent or restored";
    }
    return reconcileWithdrawalAfterLoss(
      sessionId,
      withdrawalTarget,
      transport,
      now,
      generation,
      revokedNotice,
    );
  }
}

export function restoreWithdrawnRecovery(
  sessionId: string,
  requestId: string,
  expectedDraftRevision: number,
  at = Date.now(),
): string {
  const store = sessionCockpitStore.getState();
  const recovery = store.perSession[sessionId]?.withdrawal;
  if (recovery?.phase !== "recovery" || recovery.requestId !== requestId) {
    return "no withdrawn message is awaiting recovery";
  }
  if (!store.replaceComposerDraftIfRevision(sessionId, expectedDraftRevision, recovery.text)) {
    return "draft changed again — current text was preserved; review before replacing";
  }
  const record = store.perSession[sessionId]?.submitHistory.find(
    (candidate) => candidate.requestId === requestId,
  );
  if (record)
    store.upsertSubmitRecord(sessionId, {
      ...record,
      restoredAt: at,
      updatedAt: at,
    });
  store.setWithdrawal(sessionId, undefined);
  return "withdrawn text restored for editing under a new requestId";
}

export function dismissWithdrawnRecovery(
  sessionId: string,
  requestId: string,
  expectedDraftRevision: number,
  at = Date.now(),
): string {
  const store = sessionCockpitStore.getState();
  const recovery = store.perSession[sessionId]?.withdrawal;
  if (recovery?.phase !== "recovery" || recovery.requestId !== requestId) {
    return "no matching withdrawn message is awaiting recovery";
  }
  if (!store.dismissWithdrawalRecoveryIfMatches(sessionId, requestId, expectedDraftRevision)) {
    return "draft changed again — withdrawn text remains available; review before dismissing";
  }
  const record = submissionRecord(sessionId, requestId);
  if (record) {
    store.upsertSubmitRecord(sessionId, {
      ...record,
      detail: "withdrawn before dispatch · current draft kept; withdrawn text dismissed",
      recoveryDismissedAt: at,
      updatedAt: at,
    });
  }
  return "current draft kept · withdrawn text dismissed";
}

export function withdrawLastQueuedSubmission(
  sessionId: string,
  options: WithdrawQueuedOptions = {},
): Promise<string> {
  const existing = withdrawalsInFlight.get(sessionId);
  if (existing) return existing;
  const operation = withdrawLastQueued(sessionId, options);
  const running = operation.finally(() => {
    if (withdrawalsInFlight.get(sessionId) === running) withdrawalsInFlight.delete(sessionId);
  });
  withdrawalsInFlight.set(sessionId, running);
  return running;
}
