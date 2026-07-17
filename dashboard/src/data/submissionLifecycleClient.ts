import { sessionCockpitStore, type WithdrawalState } from "./sessionCockpitStore";
import {
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

async function jsonRequest(
  fetchImpl: FetchLike,
  path: string,
  init: RequestInit,
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetchImpl(path, init);
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
  const submissions = raw.submissions.map((item, index): SubmissionLookupWire => {
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
    if (
      !isLifecycleState(submission.state) ||
      typeof submission.submittedAt !== "string" ||
      typeof submission.updatedAt !== "string" ||
      typeof submission.withdrawable !== "boolean" ||
      acceptedAt === undefined ||
      detail === undefined
    ) {
      throw new Error("invalid submission status");
    }
    return {
      requestId: requestIds[index],
      outcome: "found",
      submission: {
        state: submission.state,
        submittedAt: submission.submittedAt,
        updatedAt: submission.updatedAt,
        acceptedAt,
        withdrawable: submission.withdrawable,
        detail,
      },
    };
  });
  return { bridgeEpoch, submissions };
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
const withdrawalsInFlight = new Map<string, Promise<string>>();

export async function readSubmissionAuthority(
  sessionId: string,
  transport: SubmissionLifecycleTransport = createFetchSubmissionLifecycleTransport(),
  options: { refresh?: boolean } = {},
): Promise<SubmissionAuthorityWire> {
  const cached = authorityCache.get(sessionId);
  if (cached && !options.refresh) return cached;
  const descriptor = await transport.authority(sessionId);
  authorityCache.set(sessionId, descriptor);
  return descriptor;
}

export function clearSubmissionAuthorityCache(sessionId?: string): void {
  if (sessionId === undefined) authorityCache.clear();
  else authorityCache.delete(sessionId);
}

function submissionRecord(sessionId: string, requestId: string): SubmitRecord | undefined {
  return sessionCockpitStore
    .getState()
    .perSession[sessionId]?.submitHistory.find((record) => record.requestId === requestId);
}

function observationVersion(record: SubmitRecord | undefined): number {
  return record?.lifecycleObservationVersion ?? -1;
}

function matchingWithdrawalRecovery(sessionId: string, requestId: string): boolean {
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

function preservedObservationNotice(
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

function clearWithdrawalIfMatches(sessionId: string, requestId: string): void {
  const store = sessionCockpitStore.getState();
  if (store.perSession[sessionId]?.withdrawal?.requestId === requestId) {
    store.setWithdrawal(sessionId, undefined);
  }
}

type PendingWithdrawal = Extract<WithdrawalState, { phase: "pending" }>;

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
  const storedPending =
    cockpit?.withdrawal?.phase === "pending" ? cockpit.withdrawal : undefined;
  const pending =
    issuedWithdrawal?.requestId === requestId ? issuedWithdrawal : storedPending;
  const projected = record
    ? projectSubmissionLifecycle(record, "withdrawn", detail ?? "withdrawn before dispatch", at)
    : null;
  store.dequeueSubmit(sessionId, requestId);

  if (!pending || pending.requestId !== requestId) {
    if (projected) store.upsertSubmitRecord(sessionId, projected);
    return { record: projected, notice: "withdrawn before dispatch" };
  }
  if (record?.restoredAt !== undefined) {
    store.setWithdrawal(sessionId, undefined);
    if (projected) store.upsertSubmitRecord(sessionId, projected);
    return {
      record: projected,
      notice: "withdrawn message was already restored",
    };
  }

  const currentRevision = cockpit?.composer.draftRevision ?? 0;
  if (
    currentRevision === pending.draftRevision &&
    store.replaceComposerDraftIfRevision(sessionId, pending.draftRevision, pending.text)
  ) {
    const restored = projected ? { ...projected, restoredAt: at } : null;
    if (restored) store.upsertSubmitRecord(sessionId, restored);
    store.setWithdrawal(sessionId, undefined);
    return {
      record: restored,
      notice: "withdrawn before dispatch · restored for editing under a new requestId",
    };
  }

  const recovery: WithdrawalState = {
    phase: "recovery",
    requestId,
    text: pending.text,
    withdrawnAt: at,
  };
  if (projected) store.upsertSubmitRecord(sessionId, projected);
  store.setWithdrawal(sessionId, recovery);
  return {
    record: projected,
    notice: "withdrawn before dispatch · newer draft preserved; withdrawn text retained",
  };
}

interface AppliedLifecycleObservation {
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

function applyLifecycleObservation(
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

  const store = sessionCockpitStore.getState();
  let projected: SubmitRecord | null = null;
  if (record) {
    if (observation.kind === "server") {
      projected = projectSubmissionLifecycle(record, observation.state, settledDetail, at);
    } else {
      projected = {
        ...record,
        phase: observation.kind,
        serverLifecycleState: undefined,
        detail: settledDetail ?? undefined,
        updatedAt: at,
        lifecycleObservationVersion: record.lifecycleObservationVersion + 1,
      };
    }
    store.upsertSubmitRecord(sessionId, projected);
  }

  const remainsQueued = observation.kind === "server" && observation.state === "queued";
  if (!remainsQueued) {
    store.dequeueSubmit(sessionId, requestId);
    clearSettledWithdrawal(sessionId, requestId, options.withdrawalOwner === true);
  }
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

function markNotRetained(
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

function markGenerationLost(
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

export async function pollSubmissionLifecycleOnce(
  sessionId: string,
  transport: SubmissionLifecycleTransport = createFetchSubmissionLifecycleTransport(),
  at = Date.now(),
): Promise<number> {
  const cockpit = sessionCockpitStore.getState().perSession[sessionId];
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
  const byEpoch = new Map<string, Array<{ requestId: string; expectedBridgeEpoch: string }>>();
  for (const entry of targets) {
    const group = byEpoch.get(entry.expectedBridgeEpoch) ?? [];
    group.push(entry);
    byEpoch.set(entry.expectedBridgeEpoch, group);
  }
  for (const [epoch, entries] of byEpoch) {
    for (let start = 0; start < entries.length; start += 64) {
      const page = entries.slice(start, start + 64);
      const observedVersions = new Map(
        page.map((entry) => [
          entry.requestId,
          observationVersion(submissionRecord(sessionId, entry.requestId)),
        ]),
      );
      try {
        const batch = await transport.status(
          sessionId,
          epoch,
          page.map((entry) => entry.requestId),
        );
        for (const lookup of batch.submissions) {
          if (lookup.outcome === "found") {
            applySubmissionLifecycle(
              sessionId,
              lookup.requestId,
              lookup.submission.state,
              lookup.submission.detail,
              at,
              observedVersions.get(lookup.requestId) ?? -1,
            );
          } else {
            markNotRetained(
              sessionId,
              lookup.requestId,
              "submission is not retained by this authority; withdrawal is unavailable and it was not resent",
              at,
              observedVersions.get(lookup.requestId) ?? -1,
            );
          }
        }
      } catch (error) {
        if (!(error instanceof BridgeEpochMismatchError)) throw error;
        clearSubmissionAuthorityCache(sessionId);
        for (const entry of page) {
          markGenerationLost(
            sessionId,
            entry.requestId,
            error.message,
            at,
            observedVersions.get(entry.requestId) ?? -1,
          );
        }
      }
    }
  }
  const current = sessionCockpitStore.getState().perSession[sessionId];
  const remaining = new Set(
    (current?.queue ?? [])
      .filter((entry) => entry.state === "queued")
      .map((entry) => entry.requestId),
  );
  if (current?.withdrawal?.phase === "pending") remaining.add(current.withdrawal.requestId);
  return remaining.size;
}

interface PollState {
  timer: number | null;
  failures: number;
  transport: SubmissionLifecycleTransport;
}

const pollers = new Map<string, PollState>();

function visiblePollDelay(): number {
  return typeof document !== "undefined" && document.visibilityState === "hidden"
    ? HIDDEN_STATUS_POLL_MS
    : VISIBLE_STATUS_POLL_MS;
}

function schedulePoll(sessionId: string, delay: number): void {
  const poller = pollers.get(sessionId);
  if (!poller || poller.timer !== null) return;
  const activePoller = poller;
  activePoller.timer = window.setTimeout(async () => {
    activePoller.timer = null;
    try {
      const remaining = await pollSubmissionLifecycleOnce(sessionId, activePoller.transport);
      activePoller.failures = 0;
      if (remaining === 0) {
        pollers.delete(sessionId);
        return;
      }
      schedulePoll(sessionId, visiblePollDelay());
    } catch {
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
  if (!pollers.has(sessionId)) pollers.set(sessionId, { timer: null, failures: 0, transport });
  schedulePoll(sessionId, 0);
}

export function stopSubmissionLifecyclePolling(sessionId: string): void {
  const poller = pollers.get(sessionId);
  if (poller && poller.timer !== null) window.clearTimeout(poller.timer);
  pollers.delete(sessionId);
}

function noticeForState(state: SubmissionLifecycleState | null): string {
  if (state === "dispatching") return "already dispatching — the draft was not restored";
  if (state === "delivered") return "already delivered — the draft was not restored";
  if (state === "unknown") return "delivery unknown — cannot restore";
  return "the queued message is no longer withdrawable";
}

export interface WithdrawQueuedOptions {
  transport?: SubmissionLifecycleTransport;
  now?: () => number;
}

async function withdrawLastQueued(
  sessionId: string,
  options: WithdrawQueuedOptions,
): Promise<string> {
  const transport = options.transport ?? createFetchSubmissionLifecycleTransport();
  const now = options.now ?? Date.now;
  const store = sessionCockpitStore.getState();
  const cockpit = store.perSession[sessionId];
  if (cockpit?.withdrawal?.phase === "recovery") {
    return "withdrawn text is retained for recovery; choose replace or keep current draft";
  }
  let target = cockpit?.withdrawal?.phase === "pending" ? cockpit.withdrawal : undefined;
  if (!target) {
    const entry = [...(cockpit?.queue ?? [])]
      .reverse()
      .find((candidate) => candidate.state === "queued");
    if (entry) {
      target = {
        phase: "pending",
        requestId: entry.requestId,
        text: entry.text,
        expectedBridgeEpoch: entry.expectedBridgeEpoch,
        draftRevision: cockpit?.composer.draftRevision ?? 0,
        requestedAt: now(),
      };
      store.setWithdrawal(sessionId, target);
    }
  }
  if (!target) {
    const latest = sessionCockpitStore.getState().perSession[sessionId]?.submitHistory.at(-1);
    return latest?.phase === "accepted" || latest?.phase === "delivering"
      ? "already delivered or dispatching — there is no queued message to edit"
      : "no authoritatively queued message to edit";
  }
  const withdrawalTarget = target;

  const applyResult = (result: WithdrawalResultWire, observedVersion: number): string => {
    if (result.outcome === "withdrawn") {
      const applied = applyLifecycleObservation(
        sessionId,
        withdrawalTarget.requestId,
        { kind: "server", state: "withdrawn" },
        result.detail,
        now(),
        observedVersion,
        { withdrawalOwner: true, issuedWithdrawal: withdrawalTarget },
      );
      return applied.preserved
        ? preservedObservationNotice(sessionId, withdrawalTarget.requestId, applied.record)
        : (applied.notice ?? "withdrawn before dispatch");
    }
    if (result.outcome === "not-withdrawable" && result.state) {
      const applied = applyLifecycleObservation(
        sessionId,
        withdrawalTarget.requestId,
        { kind: "server", state: result.state },
        result.detail,
        now(),
        observedVersion,
        { withdrawalOwner: true, issuedWithdrawal: withdrawalTarget },
      );
      if (applied.preserved) {
        return preservedObservationNotice(sessionId, withdrawalTarget.requestId, applied.record);
      }
      return applied.observation.kind === "server"
        ? noticeForState(applied.observation.state)
        : "the queued message is no longer withdrawable";
    } else if (result.outcome === "not-found") {
      const marked = markNotRetained(
        sessionId,
        withdrawalTarget.requestId,
        "submission is not retained by this authority; withdrawal is unavailable and the draft was not restored",
        now(),
        observedVersion,
        { withdrawalOwner: true, issuedWithdrawal: withdrawalTarget },
      );
      if (marked.preserved) {
        return preservedObservationNotice(sessionId, withdrawalTarget.requestId, marked.record);
      }
    }
    return "submission is not retained by this authority; the draft was not restored";
  };

  let activeObservedVersion = observationVersion(
    submissionRecord(sessionId, withdrawalTarget.requestId),
  );
  try {
    return applyResult(
      await transport.withdraw(
        sessionId,
        withdrawalTarget.expectedBridgeEpoch,
        withdrawalTarget.requestId,
      ),
      activeObservedVersion,
    );
  } catch (error) {
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
    if (matchingWithdrawalRecovery(sessionId, withdrawalTarget.requestId)) {
      return preservedObservationNotice(sessionId, withdrawalTarget.requestId, null);
    }
    try {
      activeObservedVersion = observationVersion(
        submissionRecord(sessionId, withdrawalTarget.requestId),
      );
      const status = await transport.status(sessionId, withdrawalTarget.expectedBridgeEpoch, [
        withdrawalTarget.requestId,
      ]);
      const lookup = status.submissions[0];
      if (matchingWithdrawalRecovery(sessionId, withdrawalTarget.requestId)) {
        return preservedObservationNotice(sessionId, withdrawalTarget.requestId, null);
      }
      if (lookup.outcome === "not-found") {
        const marked = markNotRetained(
          sessionId,
          withdrawalTarget.requestId,
          "withdraw response was lost and the request is not retained; withdrawal is unavailable and the draft was not restored",
          now(),
          activeObservedVersion,
          { withdrawalOwner: true, issuedWithdrawal: withdrawalTarget },
        );
        if (marked.preserved) {
          return preservedObservationNotice(sessionId, withdrawalTarget.requestId, marked.record);
        }
        return "withdraw response was lost and the request is not retained; the draft was not restored";
      }
      if (lookup.submission.state === "withdrawn") {
        const applied = applyLifecycleObservation(
          sessionId,
          withdrawalTarget.requestId,
          { kind: "server", state: "withdrawn" },
          lookup.submission.detail,
          now(),
          activeObservedVersion,
          { withdrawalOwner: true, issuedWithdrawal: withdrawalTarget },
        );
        return applied.preserved
          ? preservedObservationNotice(sessionId, withdrawalTarget.requestId, applied.record)
          : (applied.notice ?? "withdrawn before dispatch");
      }
      if (lookup.submission.state === "queued") {
        activeObservedVersion = observationVersion(
          submissionRecord(sessionId, withdrawalTarget.requestId),
        );
        return applyResult(
          await transport.withdraw(
            sessionId,
            withdrawalTarget.expectedBridgeEpoch,
            withdrawalTarget.requestId,
          ),
          activeObservedVersion,
        );
      }
      const applied = applyLifecycleObservation(
        sessionId,
        withdrawalTarget.requestId,
        { kind: "server", state: lookup.submission.state },
        lookup.submission.detail,
        now(),
        activeObservedVersion,
        { withdrawalOwner: true, issuedWithdrawal: withdrawalTarget },
      );
      return applied.preserved
        ? preservedObservationNotice(sessionId, withdrawalTarget.requestId, applied.record)
        : applied.observation.kind === "server"
          ? noticeForState(applied.observation.state)
          : "the queued message is no longer withdrawable";
    } catch (convergenceError) {
      if (convergenceError instanceof BridgeEpochMismatchError) {
        const marked = markGenerationLost(
          sessionId,
          withdrawalTarget.requestId,
          convergenceError.message,
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
      if (matchingWithdrawalRecovery(sessionId, withdrawalTarget.requestId)) {
        return preservedObservationNotice(sessionId, withdrawalTarget.requestId, null);
      }
      const current = submissionRecord(sessionId, withdrawalTarget.requestId);
      if (current && current.lifecycleObservationVersion > activeObservedVersion) {
        return preservedObservationNotice(sessionId, withdrawalTarget.requestId, current);
      }
      return "withdrawal status is unavailable — the draft was not restored";
    }
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
  const running = withdrawLastQueued(sessionId, options).finally(() => {
    withdrawalsInFlight.delete(sessionId);
  });
  withdrawalsInFlight.set(sessionId, running);
  return running;
}
