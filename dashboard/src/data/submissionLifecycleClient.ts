import {
  sessionCockpitStore,
  type PerSessionCockpit,
  type WithdrawalState,
} from "./sessionCockpitStore";
import {
  enterEndgame,
  lifecycleWatchState,
  projectSubmissionLifecycle,
  settleSubmissionObservation,
  submissionObservationSnapshot,
  type SubmissionLifecycleObservation,
  type SubmissionObservationSettlement,
  type SubmitRecord,
} from "./submitMachine";
import type { SubmissionLifecycleState } from "../types/harnessCapabilities";

export const VISIBLE_STATUS_POLL_MS = 750;
export const HIDDEN_STATUS_POLL_MS = 2_500;
export const STATUS_FAILURE_BACKOFF_MS = [1_000, 2_000, 5_000] as const;

export interface SubmissionAuthorityWire {
  bridgeEpoch: string;
}

export interface SubmissionStatusWire {
  state: SubmissionLifecycleState;
  submittedAt: string;
  updatedAt: string;
  acceptedAt: string | null;
  withdrawable: boolean;
  detail: string | null;
}

export type SubmissionLookupWire =
  | { requestId: string; outcome: "not-found" }
  | { requestId: string; outcome: "found"; submission: SubmissionStatusWire };

export interface SubmissionStatusBatchWire {
  bridgeEpoch: string;
  submissions: SubmissionLookupWire[];
}

export interface WithdrawalResultWire {
  requestId: string;
  outcome: "withdrawn" | "not-withdrawable" | "not-found";
  state: SubmissionLifecycleState | null;
  withdrawnAt: string | null;
  detail: string | null;
}

export class SubmissionLifecycleRouteError extends Error {
  constructor(
    readonly httpStatus: number | null,
    readonly status: string,
    detail: string,
  ) {
    super(detail);
  }
}

export class BridgeEpochMismatchError extends SubmissionLifecycleRouteError {
  constructor(
    readonly expectedBridgeEpoch: string,
    readonly actualBridgeEpoch: string,
    detail: string,
  ) {
    super(409, "bridge-epoch-mismatch", detail);
  }
}

export interface SubmissionLifecycleTransport {
  authority(
    sessionId: string,
    options?: { signal?: AbortSignal },
  ): Promise<SubmissionAuthorityWire>;
  status(
    sessionId: string,
    expectedBridgeEpoch: string,
    requestIds: readonly string[],
    options?: { signal?: AbortSignal },
  ): Promise<SubmissionStatusBatchWire>;
  withdraw(
    sessionId: string,
    expectedBridgeEpoch: string,
    requestId: string,
    options?: { signal?: AbortSignal },
  ): Promise<WithdrawalResultWire>;
}

type FetchLike = typeof fetch;

function requiredText(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function optionalText(value: unknown): string | null | undefined {
  return value === null ? null : typeof value === "string" ? value : undefined;
}

function isLifecycleState(value: unknown): value is SubmissionLifecycleState {
  return [
    "queued",
    "dispatching",
    "delivered",
    "withdrawn",
    "unknown",
    "rejected",
    "unsupported",
  ].includes(String(value));
}

async function routeError(response: Response): Promise<SubmissionLifecycleRouteError> {
  let status = `HTTP ${response.status}`;
  let detail = status;
  let expected: string | null = null;
  let actual: string | null = null;
  try {
    const body = (await response.json()) as Record<string, unknown>;
    if (requiredText(body.status)) status = String(body.status);
    if (requiredText(body.detail)) detail = String(body.detail);
    expected = requiredText(body.expectedBridgeEpoch);
    actual = requiredText(body.actualBridgeEpoch);
  } catch {
    // HTTP status remains authoritative when a proxy returns a non-JSON error page.
  }
  if (response.status === 409 && status === "bridge-epoch-mismatch" && expected && actual) {
    return new BridgeEpochMismatchError(expected, actual, detail);
  }
  return new SubmissionLifecycleRouteError(response.status, status, detail);
}

// A submit can stick on "delivering…" forever: a request against a half-dead socket
// (observed live after daemon/proxy restarts) never resolves OR rejects, which silently killed
// the status-polling loop — no retry was ever scheduled again. Every lifecycle request now
// carries a hard timeout so a hung transport REJECTS into the existing bounded-backoff retry.
const LIFECYCLE_REQUEST_TIMEOUT_MS = 15_000;

async function jsonRequest(
  fetchImpl: FetchLike,
  path: string,
  init: RequestInit,
): Promise<unknown> {
  let response: Response;
  const timeout = AbortSignal.timeout(LIFECYCLE_REQUEST_TIMEOUT_MS);
  const signal = init.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
  try {
    response = await fetchImpl(path, { ...init, signal });
  } catch (error) {
    throw new SubmissionLifecycleRouteError(
      null,
      "transport",
      error instanceof Error ? error.message : String(error),
    );
  }
  if (!response.ok) throw await routeError(response);
  try {
    return await response.json();
  } catch {
    throw new SubmissionLifecycleRouteError(
      response.status,
      "malformed-response",
      "submission lifecycle route returned malformed JSON",
    );
  }
}

function parseAuthority(value: unknown): SubmissionAuthorityWire {
  if (typeof value !== "object" || value === null) throw new Error("invalid authority descriptor");
  const bridgeEpoch = requiredText((value as Record<string, unknown>).bridgeEpoch);
  if (!bridgeEpoch) throw new Error("invalid authority descriptor");
  return { bridgeEpoch };
}

function parseStatus(value: unknown, requestIds: readonly string[]): SubmissionStatusBatchWire {
  if (typeof value !== "object" || value === null) throw new Error("invalid status response");
  const raw = value as Record<string, unknown>;
  const bridgeEpoch = requiredText(raw.bridgeEpoch);
  if (
    !bridgeEpoch ||
    !Array.isArray(raw.submissions) ||
    raw.submissions.length !== requestIds.length
  ) {
    throw new Error("invalid status response");
  }
  const submissions = raw.submissions.map(
    (item, index): SubmissionLookupWire => parseSubmissionLookup(item, requestIds, index),
  );
  return { bridgeEpoch, submissions };
}

function parseSubmissionLookup(
  item: unknown,
  requestIds: readonly string[],
  index: number,
): SubmissionLookupWire {
  if (typeof item !== "object" || item === null) throw new Error("invalid status lookup");
  const lookup = item as Record<string, unknown>;
  if (lookup.requestId !== requestIds[index])
    throw new Error("status response reordered request ids");
  if (lookup.outcome === "not-found")
    return { requestId: requestIds[index], outcome: "not-found" };
  if (
    lookup.outcome !== "found" ||
    typeof lookup.submission !== "object" ||
    lookup.submission === null
  ) {
    throw new Error("invalid status lookup");
  }
  const submission = lookup.submission as Record<string, unknown>;
  const acceptedAt = optionalText(submission.acceptedAt);
  const detail = optionalText(submission.detail);
  if (!validSubmissionShape(submission, acceptedAt, detail)) {
    throw new Error("invalid submission status");
  }
  const state = submission.state;
  if (!isLifecycleState(state)) throw new Error("invalid submission status");
  return {
    requestId: requestIds[index],
    outcome: "found",
    submission: {
      state,
      submittedAt: submission.submittedAt as string,
      updatedAt: submission.updatedAt as string,
      acceptedAt: acceptedAt as string | null,
      withdrawable: submission.withdrawable as boolean,
      detail: detail as string | null,
    },
  };
}

function validSubmissionShape(
  submission: Record<string, unknown>,
  acceptedAt: string | null | undefined,
  detail: string | null | undefined,
): boolean {
  return (
    isLifecycleState(submission.state) &&
    typeof submission.submittedAt === "string" &&
    typeof submission.updatedAt === "string" &&
    typeof submission.withdrawable === "boolean" &&
    acceptedAt !== undefined &&
    detail !== undefined
  );
}

function parseWithdrawal(value: unknown, requestId: string): WithdrawalResultWire {
  if (typeof value !== "object" || value === null) throw new Error("invalid withdrawal response");
  const raw = value as Record<string, unknown>;
  const state = raw.state;
  const withdrawnAt = optionalText(raw.withdrawnAt);
  const detail = optionalText(raw.detail);
  if (
    raw.requestId !== requestId ||
    !["withdrawn", "not-withdrawable", "not-found"].includes(String(raw.outcome)) ||
    !(state === null || isLifecycleState(state)) ||
    withdrawnAt === undefined ||
    detail === undefined
  ) {
    throw new Error("invalid withdrawal response");
  }
  return {
    requestId,
    outcome: raw.outcome as WithdrawalResultWire["outcome"],
    state,
    withdrawnAt,
    detail,
  };
}

export function createFetchSubmissionLifecycleTransport(
  fetchImpl: FetchLike = fetch,
): SubmissionLifecycleTransport {
  return {
    async authority(sessionId, options) {
      const value = await jsonRequest(
        fetchImpl,
        `/api/terminal/${encodeURIComponent(sessionId)}/submission-authority`,
        { method: "GET", signal: options?.signal },
      );
      try {
        return parseAuthority(value);
      } catch (error) {
        throw new SubmissionLifecycleRouteError(200, "malformed-response", String(error));
      }
    },
    async status(sessionId, expectedBridgeEpoch, requestIds, options) {
      const value = await jsonRequest(
        fetchImpl,
        `/api/terminal/${encodeURIComponent(sessionId)}/submission-status`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expectedBridgeEpoch, requestIds }),
          signal: options?.signal,
        },
      );
      try {
        return parseStatus(value, requestIds);
      } catch (error) {
        throw new SubmissionLifecycleRouteError(200, "malformed-response", String(error));
      }
    },
    async withdraw(sessionId, expectedBridgeEpoch, requestId, options) {
      const value = await jsonRequest(
        fetchImpl,
        `/api/terminal/${encodeURIComponent(sessionId)}/withdraw`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expectedBridgeEpoch, requestId }),
          signal: options?.signal,
        },
      );
      try {
        return parseWithdrawal(value, requestId);
      } catch (error) {
        throw new SubmissionLifecycleRouteError(200, "malformed-response", String(error));
      }
    },
  };
}

const authorityCache = new Map<string, SubmissionAuthorityWire>();
export const withdrawalsInFlight = new Map<string, Promise<string>>();
export let scenarioGeneration = 0;

export function scenarioGenerationIsCurrent(generation: number): boolean {
  return generation === scenarioGeneration;
}

export async function readSubmissionAuthority(
  sessionId: string,
  transport: SubmissionLifecycleTransport = createFetchSubmissionLifecycleTransport(),
  options: { refresh?: boolean } = {},
): Promise<SubmissionAuthorityWire> {
  const cached = authorityCache.get(sessionId);
  if (cached && !options.refresh) return cached;
  const generation = scenarioGeneration;
  const descriptor = await transport.authority(sessionId);
  if (scenarioGenerationIsCurrent(generation)) authorityCache.set(sessionId, descriptor);
  return descriptor;
}

export function clearSubmissionAuthorityCache(sessionId?: string): void {
  if (sessionId === undefined) authorityCache.clear();
  else authorityCache.delete(sessionId);
}

export function submissionRecord(sessionId: string, requestId: string): SubmitRecord | undefined {
  return sessionCockpitStore
    .getState()
    .perSession[sessionId]?.submitHistory.find((record) => record.requestId === requestId);
}

export function observationVersion(record: SubmitRecord | undefined): number {
  return record?.lifecycleObservationVersion ?? -1;
}

export function matchingWithdrawalRecovery(sessionId: string, requestId: string): boolean {
  const withdrawal = sessionCockpitStore.getState().perSession[sessionId]?.withdrawal;
  return withdrawal?.phase === "recovery" && withdrawal.requestId === requestId;
}

interface ClientObservationSettlement {
  settlement: SubmissionObservationSettlement;
  record: SubmitRecord | undefined;
}

function settleLifecycleObservation(
  sessionId: string,
  requestId: string,
  incoming: SubmissionLifecycleObservation,
  observedVersion: number,
): ClientObservationSettlement {
  const record = submissionRecord(sessionId, requestId);
  const current = matchingWithdrawalRecovery(sessionId, requestId)
    ? {
        observation: { kind: "server" as const, state: "withdrawn" as const },
        version: Number.POSITIVE_INFINITY,
      }
    : submissionObservationSnapshot(record);
  return {
    settlement: settleSubmissionObservation(current, incoming, observedVersion),
    record,
  };
}

function possibleSendJoinDetail(): string {
  return "an earlier dispatching observation proves possible send; current authority can no longer resolve delivery";
}

export function preservedObservationNotice(
  sessionId: string,
  requestId: string,
  record: SubmitRecord | null,
): string {
  if (matchingWithdrawalRecovery(sessionId, requestId)) {
    return "withdrawn before dispatch · newer draft preserved; withdrawn text retained";
  }
  if (!record) return "a stronger submission result was preserved";
  if (record.serverLifecycleState === "withdrawn" || record.serverLifecycleState === "delivered") {
    return terminalNotice(sessionId, record);
  }
  if (record.serverLifecycleState) return noticeForState(record.serverLifecycleState);
  if (record.phase === "generation-lost") {
    return "runner generation changed — retained text was not automatically resent or restored";
  }
  return record.detail ?? "a stronger submission result was preserved";
}

function terminalNotice(sessionId: string, record: SubmitRecord): string {
  if (record.serverLifecycleState === "withdrawn") {
    const withdrawal = sessionCockpitStore.getState().perSession[sessionId]?.withdrawal;
    if (withdrawal?.phase === "recovery" && withdrawal.requestId === record.requestId) {
      return "withdrawn before dispatch · newer draft preserved; withdrawn text retained";
    }
    if (record.restoredAt !== undefined) {
      return "withdrawn before dispatch · restored for editing under a new requestId";
    }
    if (record.recoveryDismissedAt !== undefined) {
      return "withdrawn before dispatch · current draft kept; withdrawn text dismissed";
    }
    return "withdrawn before dispatch";
  }
  if (record.serverLifecycleState === "delivered") {
    return "already delivered — the draft was not restored";
  }
  return record.detail ?? `submission is already terminal: ${record.serverLifecycleState}`;
}

export function noticeForState(state: SubmissionLifecycleState | null): string {
  if (state === "dispatching") return "already dispatching — the draft was not restored";
  if (state === "delivered") return "already delivered — the draft was not restored";
  if (state === "unknown") return "delivery unknown — cannot restore";
  return "the queued message is no longer withdrawable";
}

function clearWithdrawalIfMatches(sessionId: string, requestId: string): void {
  const store = sessionCockpitStore.getState();
  if (store.perSession[sessionId]?.withdrawal?.requestId === requestId) {
    store.setWithdrawal(sessionId, undefined);
  }
}

export type PendingWithdrawal = Extract<WithdrawalState, { phase: "pending" }>;

function pendingWithdrawalSelection(
  cockpit: PerSessionCockpit | undefined,
  requestId: string,
  issuedWithdrawal?: PendingWithdrawal,
): PendingWithdrawal | undefined {
  const storedPending =
    cockpit?.withdrawal?.phase === "pending" ? cockpit.withdrawal : undefined;
  return issuedWithdrawal?.requestId === requestId ? issuedWithdrawal : storedPending;
}

function restoreOutcome(
  store: ReturnType<typeof sessionCockpitStore.getState>,
  sessionId: string,
  projected: SubmitRecord | null,
  at: number,
): { record: SubmitRecord | null; notice: string } {
  const restored = projected ? { ...projected, restoredAt: at } : null;
  if (restored) store.upsertSubmitRecord(sessionId, restored);
  store.setWithdrawal(sessionId, undefined);
  return {
    record: restored,
    notice: "withdrawn before dispatch · restored for editing under a new requestId",
  };
}

function projectWithdrawnRecord(
  record: SubmitRecord | undefined,
  detail: string | null,
  at: number,
): SubmitRecord | null {
  return record
    ? projectSubmissionLifecycle(record, "withdrawn", detail ?? "withdrawn before dispatch", at)
    : null;
}

function applyProjectedRecord(
  store: ReturnType<typeof sessionCockpitStore.getState>,
  sessionId: string,
  projected: SubmitRecord | null,
): void {
  if (projected) store.upsertSubmitRecord(sessionId, projected);
}

function restoreOrRecoverWithdrawal(
  store: ReturnType<typeof sessionCockpitStore.getState>,
  sessionId: string,
  requestId: string,
  pending: PendingWithdrawal,
  projected: SubmitRecord | null,
  at: number,
): { record: SubmitRecord | null; notice: string } {
  const currentRevision = store.perSession[sessionId]?.composer.draftRevision ?? 0;
  if (
    currentRevision === pending.draftRevision &&
    store.replaceComposerDraftIfRevision(sessionId, pending.draftRevision, pending.text)
  ) {
    return restoreOutcome(store, sessionId, projected, at);
  }
  const recovery: WithdrawalState = {
    phase: "recovery",
    requestId,
    text: pending.text,
    withdrawnAt: at,
  };
  applyProjectedRecord(store, sessionId, projected);
  store.setWithdrawal(sessionId, recovery);
  return {
    record: projected,
    notice: "withdrawn before dispatch · newer draft preserved; withdrawn text retained",
  };
}

function resolveAuthoritativeWithdrawal(
  sessionId: string,
  requestId: string,
  detail: string | null,
  at: number,
  issuedWithdrawal?: PendingWithdrawal,
): { record: SubmitRecord | null; notice: string } {
  const store = sessionCockpitStore.getState();
  const cockpit = store.perSession[sessionId];
  const record = cockpit?.submitHistory.find((candidate) => candidate.requestId === requestId);
  const pending = pendingWithdrawalSelection(cockpit, requestId, issuedWithdrawal);
  const projected = projectWithdrawnRecord(record, detail, at);
  store.dequeueSubmit(sessionId, requestId);

  if (!pending || pending.requestId !== requestId) {
    applyProjectedRecord(store, sessionId, projected);
    return { record: projected, notice: "withdrawn before dispatch" };
  }
  if (record?.restoredAt !== undefined) {
    applyProjectedRecord(store, sessionId, projected);
    store.setWithdrawal(sessionId, undefined);
    return { record: projected, notice: "withdrawn message was already restored" };
  }
  return restoreOrRecoverWithdrawal(store, sessionId, requestId, pending, projected, at);
}

export interface AppliedLifecycleObservation {
  record: SubmitRecord | null;
  preserved: boolean;
  observation: SubmissionLifecycleObservation;
  notice?: string;
}

interface LifecycleObservationOptions {
  withdrawalOwner?: boolean;
  issuedWithdrawal?: PendingWithdrawal;
}

function clearSettledWithdrawal(
  sessionId: string,
  requestId: string,
  withdrawalOwner: boolean,
): void {
  const withdrawal = sessionCockpitStore.getState().perSession[sessionId]?.withdrawal;
  if (withdrawal?.phase !== "pending" || withdrawal.requestId !== requestId) return;
  // A background response may settle while the already-issued atomic withdrawal is still on the
  // wire. Preserve that exact text/revision until the request that owns it reaches a result.
  if (!withdrawalOwner && withdrawalsInFlight.has(sessionId)) return;
  clearWithdrawalIfMatches(sessionId, requestId);
}

function withdrawnResolution(
  sessionId: string,
  requestId: string,
  observation: SubmissionLifecycleObservation,
  settledDetail: string | null,
  at: number,
  options: LifecycleObservationOptions,
): AppliedLifecycleObservation {
  const resolved = resolveAuthoritativeWithdrawal(
    sessionId,
    requestId,
    settledDetail,
    at,
    options.issuedWithdrawal,
  );
  return {
    record: resolved.record,
    preserved: false,
    observation,
    notice: resolved.notice,
  };
}

function projectObservation(
  record: SubmitRecord,
  observation: SubmissionLifecycleObservation,
  settledDetail: string | null,
  at: number,
): SubmitRecord {
  if (observation.kind === "server") {
    return projectSubmissionLifecycle(record, observation.state, settledDetail, at);
  }
  return {
    ...record,
    phase: observation.kind,
    serverLifecycleState: undefined,
    detail: settledDetail ?? undefined,
    updatedAt: at,
    lifecycleObservationVersion: record.lifecycleObservationVersion + 1,
  };
}

function dequeueUnlessQueued(
  store: ReturnType<typeof sessionCockpitStore.getState>,
  sessionId: string,
  requestId: string,
  observation: SubmissionLifecycleObservation,
  options: LifecycleObservationOptions,
): void {
  const remainsQueued = observation.kind === "server" && observation.state === "queued";
  if (!remainsQueued) {
    store.dequeueSubmit(sessionId, requestId);
    clearSettledWithdrawal(sessionId, requestId, options.withdrawalOwner === true);
  }
}

export function applyLifecycleObservation(
  sessionId: string,
  requestId: string,
  incoming: SubmissionLifecycleObservation,
  detail: string | null,
  at: number,
  observedVersion: number,
  options: LifecycleObservationOptions = {},
): AppliedLifecycleObservation {
  const { settlement, record } = settleLifecycleObservation(
    sessionId,
    requestId,
    incoming,
    observedVersion,
  );
  if (settlement.action === "preserve") {
    if (options.withdrawalOwner) clearSettledWithdrawal(sessionId, requestId, true);
    return { record: record ?? null, preserved: true, observation: incoming };
  }

  const observation = settlement.observation;
  const settledDetail =
    settlement.reason === "possible-send-join" ? possibleSendJoinDetail() : detail;
  if (observation.kind === "server" && observation.state === "withdrawn") {
    return withdrawnResolution(
      sessionId,
      requestId,
      observation,
      settledDetail,
      at,
      options,
    );
  }

  const store = sessionCockpitStore.getState();
  let projected: SubmitRecord | null = null;
  if (record) {
    projected = projectObservation(record, observation, settledDetail, at);
    store.upsertSubmitRecord(sessionId, projected);
  }

  dequeueUnlessQueued(store, sessionId, requestId, observation, options);
  return { record: projected, preserved: false, observation };
}

export function applySubmissionLifecycle(
  sessionId: string,
  requestId: string,
  state: SubmissionLifecycleState,
  detail: string | null,
  at = Date.now(),
  observedVersion?: number,
): SubmitRecord | null {
  const currentVersion = observationVersion(submissionRecord(sessionId, requestId));
  return applyLifecycleObservation(
    sessionId,
    requestId,
    { kind: "server", state },
    detail,
    at,
    observedVersion ?? currentVersion,
  ).record;
}

export function markNotRetained(
  sessionId: string,
  requestId: string,
  detail: string,
  at: number,
  observedVersion: number,
  options: LifecycleObservationOptions = {},
): { record: SubmitRecord | null; preserved: boolean } {
  const applied = applyLifecycleObservation(
    sessionId,
    requestId,
    { kind: "not-found" },
    detail,
    at,
    observedVersion,
    options,
  );
  return { record: applied.record, preserved: applied.preserved };
}

export function markGenerationLost(
  sessionId: string,
  requestId: string,
  detail: string,
  at: number,
  observedVersion: number,
  options: LifecycleObservationOptions = {},
): { record: SubmitRecord | null; preserved: boolean } {
  const applied = applyLifecycleObservation(
    sessionId,
    requestId,
    { kind: "generation-lost" },
    detail,
    at,
    observedVersion,
    options,
  );
  return { record: applied.record, preserved: applied.preserved };
}

function lifecyclePollTargets(
  sessionId: string,
  cockpit: PerSessionCockpit | undefined,
  at: number,
): Array<{ requestId: string; expectedBridgeEpoch: string }> {
  const targets: Array<{ requestId: string; expectedBridgeEpoch: string }> = (
    cockpit?.queue ?? []
  ).filter((entry) => entry.state === "queued");
  const pending = cockpit?.withdrawal?.phase === "pending" ? cockpit.withdrawal : undefined;
  if (pending && !targets.some((entry) => entry.requestId === pending.requestId)) {
    targets.push({
      requestId: pending.requestId,
      expectedBridgeEpoch: pending.expectedBridgeEpoch,
    });
  }
  appendWatchedTargets(sessionId, cockpit, targets, at);
  return targets;
}

function appendWatchedTargets(
  sessionId: string,
  cockpit: PerSessionCockpit | undefined,
  targets: Array<{ requestId: string; expectedBridgeEpoch: string }>,
  at: number,
): void {
  // Guards the composer settling on "delivering…" forever: a record the authority
  // reported dispatching leaves the queue, but its terminal word is still owed — keep polling it
  // until a terminal lifecycle state lands or the bounded watch window expires, where the record
  // settles honestly on the existing endgame boundary instead of spinning forever.
  for (const record of cockpit?.submitHistory ?? []) {
    if (targets.some((entry) => entry.requestId === record.requestId)) continue;
    const watch = lifecycleWatchState(record, at);
    if (watch === "inactive") continue;
    if (watch === "expired") {
      sessionCockpitStore.getState().upsertSubmitRecord(sessionId, enterEndgame(record, at));
      continue;
    }
    targets.push({ requestId: record.requestId, expectedBridgeEpoch: record.expectedBridgeEpoch });
  }
}

function groupTargetsByEpoch(
  targets: Array<{ requestId: string; expectedBridgeEpoch: string }>,
): Map<string, Array<{ requestId: string; expectedBridgeEpoch: string }>> {
  const byEpoch = new Map<string, Array<{ requestId: string; expectedBridgeEpoch: string }>>();
  for (const entry of targets) {
    const group = byEpoch.get(entry.expectedBridgeEpoch) ?? [];
    group.push(entry);
    byEpoch.set(entry.expectedBridgeEpoch, group);
  }
  return byEpoch;
}

function observedVersionFor(
  observedVersions: Map<string, number>,
  requestId: string,
): number {
  return observedVersions.get(requestId) ?? -1;
}

function remainingPollTargets(sessionId: string, at: number): number {
  const current = sessionCockpitStore.getState().perSession[sessionId];
  const remaining = new Set(
    (current?.queue ?? [])
      .filter((entry) => entry.state === "queued")
      .map((entry) => entry.requestId),
  );
  if (current?.withdrawal?.phase === "pending") remaining.add(current.withdrawal.requestId);
  // A still-watched record keeps the poller alive after its queue entry dequeues; the watch ends
  // only on the server's terminal word or the bounded window (already settled to endgame above).
  for (const record of current?.submitHistory ?? []) {
    if (lifecycleWatchState(record, at) === "active") remaining.add(record.requestId);
  }
  return remaining.size;
}

async function applyStatusPage(
  sessionId: string,
  page: Array<{ requestId: string; expectedBridgeEpoch: string }>,
  epoch: string,
  transport: SubmissionLifecycleTransport,
  observedVersions: Map<string, number>,
  at: number,
  generation: number,
): Promise<boolean> {
  try {
    const batch = await transport.status(
      sessionId,
      epoch,
      page.map((entry) => entry.requestId),
    );
    if (!scenarioGenerationIsCurrent(generation)) return false;
    for (const lookup of batch.submissions) {
      if (lookup.outcome === "found") {
        applySubmissionLifecycle(
          sessionId,
          lookup.requestId,
          lookup.submission.state,
          lookup.submission.detail,
          at,
          observedVersionFor(observedVersions, lookup.requestId),
        );
      } else {
        markNotRetained(
          sessionId,
          lookup.requestId,
          "submission is not retained by this authority; withdrawal is unavailable and it was not resent",
          at,
          observedVersionFor(observedVersions, lookup.requestId),
        );
      }
    }
    return true;
  } catch (error) {
    if (!scenarioGenerationIsCurrent(generation)) return false;
    if (!(error instanceof BridgeEpochMismatchError)) throw error;
    clearSubmissionAuthorityCache(sessionId);
    for (const entry of page) {
      markGenerationLost(
        sessionId,
        entry.requestId,
        error.message,
        at,
        observedVersionFor(observedVersions, entry.requestId),
      );
    }
    return true;
  }
}

export async function pollSubmissionLifecycleOnce(
  sessionId: string,
  transport: SubmissionLifecycleTransport = createFetchSubmissionLifecycleTransport(),
  at = Date.now(),
): Promise<number> {
  const generation = scenarioGeneration;
  const cockpit = sessionCockpitStore.getState().perSession[sessionId];
  const targets = lifecyclePollTargets(sessionId, cockpit, at);
  const byEpoch = groupTargetsByEpoch(targets);
  for (const [epoch, entries] of byEpoch) {
    for (let start = 0; start < entries.length; start += 64) {
      const page = entries.slice(start, start + 64);
      const observedVersions = new Map(
        page.map((entry) => [
          entry.requestId,
          observationVersion(submissionRecord(sessionId, entry.requestId)),
        ]),
      );
      const applied = await applyStatusPage(
        sessionId,
        page,
        epoch,
        transport,
        observedVersions,
        at,
        generation,
      );
      if (!applied) return 0;
    }
  }
  return remainingPollTargets(sessionId, at);
}

interface PollState {
  timer: number | null;
  failures: number;
  transport: SubmissionLifecycleTransport;
  generation: number;
}

const pollers = new Map<string, PollState>();

function visiblePollDelay(): number {
  return typeof document !== "undefined" && document.visibilityState === "hidden"
    ? HIDDEN_STATUS_POLL_MS
    : VISIBLE_STATUS_POLL_MS;
}

function schedulePoll(sessionId: string, delay: number): void {
  const poller = pollers.get(sessionId);
  if (!poller || poller.timer !== null || !scenarioGenerationIsCurrent(poller.generation)) return;
  const activePoller = poller;
  activePoller.timer = window.setTimeout(async () => {
    activePoller.timer = null;
    if (
      pollers.get(sessionId) !== activePoller ||
      !scenarioGenerationIsCurrent(activePoller.generation)
    )
      return;
    try {
      const remaining = await pollSubmissionLifecycleOnce(sessionId, activePoller.transport);
      if (
        pollers.get(sessionId) !== activePoller ||
        !scenarioGenerationIsCurrent(activePoller.generation)
      )
        return;
      activePoller.failures = 0;
      if (remaining === 0) {
        if (pollers.get(sessionId) === activePoller) pollers.delete(sessionId);
        return;
      }
      schedulePoll(sessionId, visiblePollDelay());
    } catch {
      if (
        pollers.get(sessionId) !== activePoller ||
        !scenarioGenerationIsCurrent(activePoller.generation)
      )
        return;
      const backoff =
        STATUS_FAILURE_BACKOFF_MS[
          Math.min(activePoller.failures, STATUS_FAILURE_BACKOFF_MS.length - 1)
        ];
      activePoller.failures += 1;
      schedulePoll(sessionId, backoff);
    }
  }, delay);
}

export function ensureSubmissionLifecyclePolling(
  sessionId: string,
  transport: SubmissionLifecycleTransport = createFetchSubmissionLifecycleTransport(),
): void {
  if (!pollers.has(sessionId))
    pollers.set(sessionId, {
      timer: null,
      failures: 0,
      transport,
      generation: scenarioGeneration,
    });
  schedulePoll(sessionId, 0);
}

export function stopSubmissionLifecyclePolling(sessionId: string): void {
  const poller = pollers.get(sessionId);
  if (poller && poller.timer !== null) window.clearTimeout(poller.timer);
  if (poller && pollers.get(sessionId) === poller) pollers.delete(sessionId);
}

/** Clear module-level lifecycle state when the dev bench changes its fake HTTP authority. */
export function resetSubmissionLifecycleClientForDev(): void {
  // Reset revokes old selector authority before clearing ownership. Pending promises cannot be
  // synchronously cancelled, so every post-await mutation also validates this generation.
  scenarioGeneration += 1;
  clearSubmissionAuthorityCache();
  for (const poller of pollers.values()) {
    if (poller.timer !== null) window.clearTimeout(poller.timer);
  }
  pollers.clear();
  withdrawalsInFlight.clear();
}
