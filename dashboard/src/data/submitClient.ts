import type { OpenSession } from "./sessions";
import { announcePolite } from "./announcer";
import { sessionStore } from "./sessions";
import { sessionCockpitStore } from "./sessionCockpitStore";
import {
  BridgeEpochMismatchError,
  clearSubmissionAuthorityCache,
  createFetchSubmissionLifecycleTransport,
  ensureSubmissionLifecyclePolling,
  readSubmissionAuthority,
  type SubmissionLifecycleTransport,
} from "./submissionLifecycleClient";
import {
  enterEndgame,
  enterReconcileWindow,
  latestActiveSubmit,
  lifecycleWatchState,
  projectSubmissionLifecycle,
  RECONCILE_WINDOW_MS,
  reconcileDelay,
  recordReconcileDelay,
  reduceReceipt,
  reduceReconciliation,
  refreshReconcileElapsed,
  releaseDraft,
  retryPayload,
  serverConfirmedQueued,
  startSubmitRecord,
  type SubmitRecord,
  type SubmitRouteFailure,
  type SubmitSource,
} from "./submitMachine";
import type {
  ReconciliationResultWire,
  SubmissionReceiptWire,
} from "../types/harnessCapabilities";

// Browser transport + store driver for the reliable submit path. A browser-level fetch
// rejection carries no mayHaveSent evidence, so it is ALWAYS ambiguous here. The only safe resend
// arm accepts an explicit PreDispatchTransportError from an injected transport or the server's
// exact control-IPC pre-dispatch certificate, then reuses the same request id + immutable text.
// This boundary is intentional, not HTTP-status or error-string guessing.

export class PreDispatchTransportError extends Error {}
export class AmbiguousSubmitTransportError extends Error {}
export class SubmitDeadlineError extends Error {
  constructor(readonly stage: "submit" | "reconcile") {
    super(`${stage} attempt reached the reliable-submit wall-clock deadline`);
  }
}

export class SubmitRouteError extends Error {
  constructor(readonly failure: SubmitRouteFailure) {
    super(failure.detail);
  }
}

export interface ReliableSubmitTransport {
  submit(
    sessionId: string,
    request: { requestId: string; text: string; expectedBridgeEpoch: string },
    options?: { signal?: AbortSignal },
  ): Promise<SubmissionReceiptWire>;
  reconcile(
    sessionId: string,
    requestId: string,
    expectedBridgeEpoch: string,
    options?: { signal?: AbortSignal },
  ): Promise<ReconciliationResultWire>;
}

type FetchLike = typeof fetch;

function optionalText(value: unknown): string | null | undefined {
  return value === null ? null : typeof value === "string" ? value : undefined;
}

function parseReceipt(value: unknown, requestId: string): SubmissionReceiptWire | null {
  if (typeof value !== "object" || value === null) return null;
  const raw = value as Record<string, unknown>;
  const acceptance = raw.acceptance;
  const vendorCorrelationId = optionalText(raw.vendorCorrelationId);
  const acceptedAt = optionalText(raw.acceptedAt);
  const detail = optionalText(raw.detail);
  const bridgeEpoch = optionalText(raw.bridgeEpoch);
  if (
    raw.requestId !== requestId ||
    !["immediate", "queued", "rejected", "unknown", "unsupported"].includes(
      String(acceptance),
    ) ||
    typeof raw.submittedAt !== "string" ||
    vendorCorrelationId === undefined ||
    acceptedAt === undefined ||
    detail === undefined ||
    typeof bridgeEpoch !== "string" ||
    bridgeEpoch.length === 0
  ) {
    return null;
  }
  return {
    requestId,
    acceptance: acceptance as SubmissionReceiptWire["acceptance"],
    submittedAt: raw.submittedAt,
    vendorCorrelationId,
    acceptedAt,
    detail,
    bridgeEpoch,
  };
}

function parseReconciliation(value: unknown, requestId: string): ReconciliationResultWire | null {
  if (typeof value !== "object" || value === null) return null;
  const raw = value as Record<string, unknown>;
  const state = raw.state;
  const vendorCorrelationId = optionalText(raw.vendorCorrelationId);
  const detail = optionalText(raw.detail);
  const bridgeEpoch = optionalText(raw.bridgeEpoch);
  const submissionState = raw.submissionState;
  if (
    raw.requestId !== requestId ||
    !["accepted", "rejected", "unresolved", "unsupported"].includes(String(state)) ||
    typeof raw.reconciledAt !== "string" ||
    vendorCorrelationId === undefined ||
    detail === undefined ||
    typeof bridgeEpoch !== "string" ||
    bridgeEpoch.length === 0 ||
    !(
      submissionState === null ||
      [
        "queued",
        "dispatching",
        "delivered",
        "withdrawn",
        "unknown",
        "rejected",
        "unsupported",
      ].includes(String(submissionState))
    )
  ) {
    return null;
  }
  return {
    requestId,
    state: state as ReconciliationResultWire["state"],
    reconciledAt: raw.reconciledAt,
    vendorCorrelationId,
    detail,
    bridgeEpoch,
    submissionState: submissionState as ReconciliationResultWire["submissionState"],
  };
}

async function routeFailure(
  response: Response,
  allowCertifiedPreDispatch = false,
): Promise<SubmitRouteError | PreDispatchTransportError | BridgeEpochMismatchError> {
  let status = `HTTP ${response.status}`;
  let detail = status;
  try {
    const body = (await response.json()) as {
      status?: unknown;
      detail?: unknown;
      retrySafe?: unknown;
      stage?: unknown;
      expectedBridgeEpoch?: unknown;
      actualBridgeEpoch?: unknown;
    };
    if (typeof body.status === "string" && body.status) status = body.status;
    if (typeof body.detail === "string" && body.detail) detail = body.detail;
    if (
      response.status === 409 &&
      body.status === "bridge-epoch-mismatch" &&
      typeof body.expectedBridgeEpoch === "string" &&
      typeof body.actualBridgeEpoch === "string"
    ) {
      return new BridgeEpochMismatchError(
        body.expectedBridgeEpoch,
        body.actualBridgeEpoch,
        detail,
      );
    }
    if (
      allowCertifiedPreDispatch &&
      response.status === 503 &&
      body.status === "pre-dispatch-failed" &&
      body.retrySafe === true &&
      body.stage === "control-ipc"
    ) {
      return new PreDispatchTransportError(detail);
    }
  } catch {
    // The HTTP status is still definitive even when the error body is not JSON.
  }
  return new SubmitRouteError({ httpStatus: response.status, status, detail });
}

export function createFetchSubmitTransport(fetchImpl: FetchLike = fetch): ReliableSubmitTransport {
  return {
    async submit(sessionId, request, options) {
      let response: Response;
      try {
        response = await fetchImpl(`/api/terminal/${encodeURIComponent(sessionId)}/submit`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
          signal: options?.signal,
        });
      } catch (error) {
        throw new AmbiguousSubmitTransportError(
          `submit response was lost after browser dispatch: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
      if (!response.ok) throw await routeFailure(response, true);
      let body: unknown;
      try {
        body = await response.json();
      } catch {
        throw new AmbiguousSubmitTransportError("submit returned malformed JSON after dispatch");
      }
      const receipt = parseReceipt(body, request.requestId);
      if (!receipt) {
        throw new AmbiguousSubmitTransportError(
          "submit returned incoherent evidence after dispatch — reconcile the same requestId",
        );
      }
      return receipt;
    },
    async reconcile(sessionId, requestId, expectedBridgeEpoch, options) {
      let response: Response;
      try {
        response = await fetchImpl(`/api/terminal/${encodeURIComponent(sessionId)}/reconcile`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ requestId, expectedBridgeEpoch }),
          signal: options?.signal,
        });
      } catch (error) {
        throw new AmbiguousSubmitTransportError(
          `reconcile response was lost: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
      if (!response.ok) throw await routeFailure(response);
      let body: unknown;
      try {
        body = await response.json();
      } catch {
        throw new AmbiguousSubmitTransportError("reconcile returned malformed JSON");
      }
      const result = parseReconciliation(body, requestId);
      if (!result) throw new AmbiguousSubmitTransportError("reconcile returned incoherent evidence");
      return result;
    },
  };
}

export interface SubmitExecutionOptions {
  transport?: ReliableSubmitTransport;
  lifecycleTransport?: SubmissionLifecycleTransport;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
  onChange?: (record: SubmitRecord) => void;
  /** Injectable only to make hung/slow deadline regressions fast; production uses 120 seconds. */
  resolutionWindowMs?: number;
}

const wait = (ms: number): Promise<void> =>
  new Promise((resolve) => window.setTimeout(resolve, ms));

function update(record: SubmitRecord, options: SubmitExecutionOptions): SubmitRecord {
  options.onChange?.(record);
  return record;
}

/**
 * Every network attempt is raced against the remaining wall-clock budget and receives an abort
 * signal. The Promise race is required in addition to AbortController because an injected or
 * buggy transport can ignore the signal; without both, one hung request makes the manual endgame
 * unreachable forever.
 */
async function boundedAttempt<T>(
  stage: "submit" | "reconcile",
  deadlineAt: number,
  now: () => number,
  run: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const remainingMs = deadlineAt - now();
  if (remainingMs <= 0) throw new SubmitDeadlineError(stage);
  const controller = new AbortController();
  let timer = 0;
  const deadline = new Promise<never>((_resolve, reject) => {
    timer = window.setTimeout(() => {
      reject(new SubmitDeadlineError(stage));
      controller.abort();
    }, remainingMs);
  });
  try {
    const result = await Promise.race([run(controller.signal), deadline]);
    if (now() >= deadlineAt) throw new SubmitDeadlineError(stage);
    return result;
  } finally {
    window.clearTimeout(timer);
  }
}

interface ReconcileBounds {
  windowStartedAt: number;
  deadlineAt: number;
}

async function reconcileWindow(
  sessionId: string,
  initial: SubmitRecord,
  options: SubmitExecutionOptions,
  existingBounds?: ReconcileBounds,
): Promise<SubmitRecord> {
  const transport = options.transport ?? createFetchSubmitTransport();
  const now = options.now ?? Date.now;
  const sleep = options.sleep ?? wait;
  const windowStartedAt = existingBounds?.windowStartedAt ?? now();
  const deadlineAt =
    existingBounds?.deadlineAt ??
    windowStartedAt + (options.resolutionWindowMs ?? RECONCILE_WINDOW_MS);
  let record = update(enterReconcileWindow(initial, now(), windowStartedAt), options);
  for (;;) {
    const remainingMs = deadlineAt - now();
    if (remainingMs <= 0) {
      return update(enterEndgame(refreshReconcileElapsed(record, now()), now()), options);
    }
    const delayMs = reconcileDelay(record.reconcileAttempts);
    await sleep(Math.min(delayMs, remainingMs));
    if (now() >= deadlineAt) {
      return update(enterEndgame(refreshReconcileElapsed(record, now()), now()), options);
    }
    record = update(recordReconcileDelay(record, delayMs, now()), options);
    try {
      if (options.lifecycleTransport) {
        const status = await boundedAttempt("reconcile", deadlineAt, now, (signal) =>
          options.lifecycleTransport!.status(
            sessionId,
            record.expectedBridgeEpoch,
            [record.requestId],
            { signal },
          ),
        );
        const lookup = status.submissions[0];
        if (lookup?.outcome === "found") {
          record = update(
            projectSubmissionLifecycle(
              record,
              lookup.submission.state,
              lookup.submission.detail,
              now(),
            ),
            options,
          );
          if (record.phase !== "ambiguous") return record;
        }
      }
      const result = await boundedAttempt("reconcile", deadlineAt, now, (signal) =>
        transport.reconcile(
          sessionId,
          record.requestId,
          record.expectedBridgeEpoch,
          { signal },
        ),
      );
      record = update(reduceReconciliation(record, result, now()), options);
      if (record.phase !== "reconciling") return record;
    } catch (error) {
      if (error instanceof BridgeEpochMismatchError) {
        clearSubmissionAuthorityCache(sessionId);
        return update(
          {
            ...record,
            phase: "generation-lost",
            serverLifecycleState: undefined,
            detail: error.message,
            updatedAt: now(),
          },
          options,
        );
      }
      if (error instanceof SubmitDeadlineError || now() >= deadlineAt) {
        return update(enterEndgame(refreshReconcileElapsed(record, now()), now()), options);
      }
      if (error instanceof SubmitRouteError && error.failure.httpStatus !== 503) {
        return update(
          {
            ...record,
            phase: "route-error",
            routeFailure: error.failure,
            detail: error.failure.detail,
            updatedAt: now(),
          },
          options,
        );
      }
      // A transient 503 or unclassified browser loss keeps the same-id reconcile loop alive.
      record = update(
        {
          ...record,
          detail: error instanceof Error ? error.message : String(error),
          updatedAt: now(),
        },
        options,
      );
    }
  }
}

export async function executeReliableSubmit(
  sessionId: string,
  initial: SubmitRecord,
  options: SubmitExecutionOptions = {},
): Promise<SubmitRecord> {
  const transport = options.transport ?? createFetchSubmitTransport();
  const now = options.now ?? Date.now;
  const windowStartedAt = now();
  const deadlineAt = windowStartedAt + (options.resolutionWindowMs ?? RECONCILE_WINDOW_MS);
  let record = update(initial, options);
  let preDispatchRetries = 0;
  for (;;) {
    try {
      const receipt = await boundedAttempt("submit", deadlineAt, now, (signal) =>
        transport.submit(
          sessionId,
          {
            requestId: record.requestId,
            text: record.text,
            expectedBridgeEpoch: record.expectedBridgeEpoch,
          },
          { signal },
        ),
      );
      record = update(reduceReceipt(record, receipt, now()), options);
      break;
    } catch (error) {
      if (error instanceof BridgeEpochMismatchError) {
        clearSubmissionAuthorityCache(sessionId);
        return update(
          {
            ...record,
            phase: "generation-lost",
            serverLifecycleState: undefined,
            detail: error.message,
            updatedAt: now(),
          },
          options,
        );
      }
      if (error instanceof PreDispatchTransportError && preDispatchRetries === 0) {
        preDispatchRetries += 1;
        continue; // same id + immutable text; no request byte was dispatched
      }
      if (error instanceof SubmitRouteError || error instanceof PreDispatchTransportError) {
        const failure =
          error instanceof SubmitRouteError
            ? error.failure
            : { httpStatus: null, status: "pre-dispatch-failed", detail: error.message };
        return update(
          {
            ...record,
            phase: "route-error",
            routeFailure: failure,
            detail: failure.detail,
            updatedAt: now(),
          },
          options,
        );
      }
      record = update(
        {
          ...record,
          phase: "ambiguous",
          detail: error instanceof Error ? error.message : String(error),
          updatedAt: now(),
        },
        options,
      );
      break;
    }
  }
  return record.phase === "ambiguous"
    ? reconcileWindow(
        sessionId,
        record,
        { ...options, transport },
        { windowStartedAt, deadlineAt },
      )
    : record;
}

export async function continueReliableReconcile(
  sessionId: string,
  record: SubmitRecord,
  options: SubmitExecutionOptions = {},
): Promise<SubmitRecord> {
  return reconcileWindow(sessionId, record, options);
}

export interface SubmissionGate {
  ready: boolean;
  editable: boolean;
  reason?: string;
}

export function submissionGate(session: OpenSession | undefined): SubmissionGate {
  if (!session) return { ready: false, editable: false, reason: "no session is focused" };
  if ((session.status ?? "running") !== "running") {
    return { ready: false, editable: false, reason: "this session has ended" };
  }
  if (session.kind !== "harness" || session.controlState === "unsupported") {
    return {
      ready: false,
      editable: false,
      reason: "native control is unsupported — use raw terminal typing",
    };
  }
  if (session.controlState !== "ready") {
    // The sweep marks a bridge "disconnected" when one snapshot
    // read loses to a busy booting/working bridge — including MID-TURN — and the gate then
    // contradicted the seat's own working line and a just-accepted submit. A turn streaming
    // through the conversation projection right now is fresher proof of control life than that
    // sweep-bounded catalog word, so it outranks "disconnected" (the sweep's uncertainty mark) —
    // never "failed" (the terminal diagnosis). This mirrors seatVisualState, which already
    // ranks the same live signal over the lagging catalog turn-state; the send then follows the
    // designed working-turn flow (the authority queues it).
    if (session.controlState === "disconnected" && session.liveTurnWorking === true) {
      return { ready: true, editable: true };
    }
    const reason =
      session.controlState === "failed"
        ? "native control failed — inspect the session evidence"
        : session.controlState === "disconnected"
          ? "native control is disconnected"
          : "native control is not ready yet";
    return { ready: false, editable: true, reason };
  }
  return { ready: true, editable: true };
}

export type SubmitStartOutcome =
  | { status: "started"; record: SubmitRecord }
  | { status: "blocked"; reason: string }
  | { status: "empty" };

interface StartMessageOptions extends SubmitExecutionOptions {
  requestId?: string;
  source?: SubmitSource;
  clearDraftOnAccept?: boolean;
  submittedRevision?: number;
  expectedBridgeEpoch?: string;
}

function preview(text: string): string {
  const twoLines = text.split("\n").slice(0, 2).join(" ↵ ");
  return twoLines.length > 140 ? `${twoLines.slice(0, 137)}…` : twoLines;
}

export function submissionReceiptAnnouncement(record: SubmitRecord): string | null {
  switch (record.phase) {
    case "accepted":
      return "message accepted — delivered";
    case "queued":
      // Withdrawability is the authority's word, not the receipt's: a bare queued receipt is
      // usually already dispatching under the dispatch grace, so it never earns the claim.
      return serverConfirmedQueued(record)
        ? "message queued — withdrawable"
        : "message queued";
    case "rejected":
      return `message rejected${record.detail ? `: ${record.detail}` : ""}`;
    case "unsupported":
      return `message unsupported${record.detail ? `: ${record.detail}` : ""}`;
    default:
      return null;
  }
}

function settleStoredSubmission(
  sessionId: string,
  record: SubmitRecord,
  lifecycleTransport?: SubmissionLifecycleTransport,
): void {
  const cockpit = sessionCockpitStore.getState();
  const receiptAnnouncement = submissionReceiptAnnouncement(record);
  if (cockpit.focusedSessionId === sessionId && receiptAnnouncement) {
    announcePolite(receiptAnnouncement);
  }
  if (record.source === "composer" && record.phase === "queued") {
    const exists = cockpit.perSession[sessionId]?.queue.some(
      (item) => item.requestId === record.requestId,
    );
    if (!exists) {
      cockpit.enqueueSubmit(sessionId, {
        requestId: record.requestId,
        text: record.text,
        preview: preview(record.text),
        queuedAt: record.updatedAt,
        expectedBridgeEpoch: record.expectedBridgeEpoch,
        state: "queued",
      });
    }
  }
  // The queued path polls from enqueue; a record that exited the reconcile loop non-terminal
  // (delivering/unknown) is owed the same terminal word — keep the poller alive for it too, so
  // the composer settles on the server's word instead of "delivering…" forever.
  if (
    lifecycleTransport &&
    ((record.source === "composer" && record.phase === "queued") ||
      lifecycleWatchState(record, Date.now()) === "active")
  ) {
    ensureSubmissionLifecyclePolling(sessionId, lifecycleTransport);
  }
  if (
    record.source === "composer" &&
    record.clearDraftOnAccept &&
    // Sent text once stayed in the composer: a submit observed at
    // "delivering" jumped straight past "queued" (claude's lifecycle can report dispatching
    // first; its delivered transition lands via the bounded lifecycle watch), so dispatch
    // counts as committed: the draft clears on ANY of queued/delivering/accepted.
    (record.phase === "accepted" || record.phase === "queued" || record.phase === "delivering")
  ) {
    cockpit.clearComposerDraftIfRevision(sessionId, record.submittedRevision);
  }
}

export async function submitSessionText(
  sessionId: string,
  text: string,
  options: StartMessageOptions = {},
): Promise<SubmitStartOutcome> {
  if (text.length === 0) return { status: "empty" };
  const session = sessionStore.getState().sessions.find((candidate) => candidate.id === sessionId);
  const gate = submissionGate(session);
  if (!gate.ready) return { status: "blocked", reason: gate.reason ?? "submission is unavailable" };
  const cockpit = sessionCockpitStore.getState();
  const perSession = cockpit.perSession[sessionId];
  if (latestActiveSubmit(perSession?.submitHistory ?? [])) {
    return { status: "blocked", reason: "the previous submit is still resolving" };
  }
  const revision = options.submittedRevision ?? perSession?.composer.draftRevision ?? 0;
  const source = options.source ?? "background";
  const lifecycleTransport =
    options.lifecycleTransport ?? createFetchSubmissionLifecycleTransport();
  let expectedBridgeEpoch = options.expectedBridgeEpoch;
  if (!expectedBridgeEpoch) {
    try {
      expectedBridgeEpoch = (
        await readSubmissionAuthority(sessionId, lifecycleTransport)
      ).bridgeEpoch;
    } catch (error) {
      return {
        status: "blocked",
        reason: `submission authority unavailable: ${error instanceof Error ? error.message : String(error)}`,
      };
    }
  }
  const record = startSubmitRecord({
    requestId: options.requestId ?? crypto.randomUUID(),
    text,
    expectedBridgeEpoch,
    source,
    submittedRevision: revision,
    clearDraftOnAccept: options.clearDraftOnAccept ?? source === "composer",
    at: (options.now ?? Date.now)(),
  });
  const final = await executeReliableSubmit(sessionId, record, {
    ...options,
    lifecycleTransport,
    onChange: (next) => {
      sessionCockpitStore.getState().upsertSubmitRecord(sessionId, next);
      options.onChange?.(next);
    },
  });
  settleStoredSubmission(
    sessionId,
    final,
    options.lifecycleTransport ?? (options.transport ? undefined : lifecycleTransport),
  );
  return { status: "started", record: final };
}

export async function submitSessionDraft(
  sessionId: string,
  options: StartMessageOptions = {},
): Promise<SubmitStartOutcome> {
  const perSession = sessionCockpitStore.getState().perSession[sessionId];
  const draft = perSession?.composer ?? { draft: "", draftRevision: 0 };
  return submitSessionText(sessionId, draft.draft, {
    ...options,
    source: "composer",
    submittedRevision: draft.draftRevision,
    clearDraftOnAccept: true,
  });
}

export async function keepWaitingForSubmit(
  sessionId: string,
  requestId: string,
  options: SubmitExecutionOptions = {},
): Promise<SubmitRecord | null> {
  const record = sessionCockpitStore
    .getState()
    .perSession[sessionId]?.submitHistory.find((item) => item.requestId === requestId);
  if (!record || record.phase !== "endgame") return null;
  const final = await continueReliableReconcile(sessionId, record, {
    ...options,
    onChange: (next) => {
      sessionCockpitStore.getState().upsertSubmitRecord(sessionId, next);
      options.onChange?.(next);
    },
  });
  settleStoredSubmission(sessionId, final, options.lifecycleTransport);
  return final;
}

export function releaseSubmitDraft(sessionId: string, requestId: string, at = Date.now()): boolean {
  const cockpit = sessionCockpitStore.getState();
  const record = cockpit.perSession[sessionId]?.submitHistory.find(
    (item) => item.requestId === requestId,
  );
  if (!record || record.phase !== "endgame") return false;
  cockpit.upsertSubmitRecord(sessionId, releaseDraft(record, at));
  // "Release" detaches the retained draft from this unresolved request; it is not a discard.
  // Keeping the text lets the operator edit or explicitly submit it under a fresh requestId.
  return true;
}

export async function retryRouteFailure(
  sessionId: string,
  requestId: string,
  candidateText: string,
  options: SubmitExecutionOptions = {},
): Promise<{ record: SubmitRecord | null; notice?: string }> {
  const cockpit = sessionCockpitStore.getState();
  const previous = cockpit.perSession[sessionId]?.submitHistory.find(
    (item) => item.requestId === requestId,
  );
  if (!previous || previous.phase !== "route-error") return { record: null };
  const payload = retryPayload(previous, candidateText);
  const restarted: SubmitRecord = {
    ...previous,
    phase: "sending",
    routeFailure: undefined,
    detail: payload.notice,
    updatedAt: (options.now ?? Date.now)(),
  };
  const record = await executeReliableSubmit(sessionId, restarted, {
    ...options,
    onChange: (next) => {
      sessionCockpitStore.getState().upsertSubmitRecord(sessionId, next);
      options.onChange?.(next);
    },
  });
  settleStoredSubmission(sessionId, record, options.lifecycleTransport);
  return { record, notice: payload.notice };
}

/** Wait only for the concrete create-then-submit transition; no polling or default is invented. */
export function waitForSubmissionReady(sessionId: string, timeoutMs = 30_000): Promise<SubmissionGate> {
  const current = sessionStore.getState().sessions.find((session) => session.id === sessionId);
  const first = submissionGate(current);
  if (first.ready || !first.editable) return Promise.resolve(first);
  return new Promise((resolve) => {
    let settled = false;
    let unsubscribe = () => {};
    const finish = (result: SubmissionGate) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      unsubscribe();
      resolve(result);
    };
    unsubscribe = sessionStore.subscribe((state) => {
      const session = state.sessions.find((candidate) => candidate.id === sessionId);
      const gate = submissionGate(session);
      if (gate.ready || !gate.editable) finish(gate);
    });
    const timer = window.setTimeout(
      () => finish({ ready: false, editable: true, reason: "native control did not become ready" }),
      timeoutMs,
    );
  });
}
