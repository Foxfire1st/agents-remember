import {
  isUncertainRoleExecution,
  launchSelectionComplete,
  roleDocumentScope,
  roleRequestKey,
  sameRoleDocumentScope,
  usesRequestReceipts,
  type RoleAction,
  type RoleDocumentScope,
  type RoleExecutionReceipt,
  type RoleLaunchSelection,
  type RoleLauncherOptions,
  type RoleScopedExecution,
  type RoleTasklessActiveRequest,
} from "./roleLaunchModel";
import { paseoAgentTarget } from "./paseoFrameModel";
import type { ActiveRequestsRef, SetExecutionErrorState } from "./roleChatResult";

// The pane's live run state a dispatch reads: the activity flags, the loaded options,
// the current execution, the saved requests, the live selection, and the notify hook.
// The notify callback travels as its live ref: the ref is dereferenced when a receipt
// is accepted, never when the dispatch starts, so a callback replaced or removed
// while the answer is pending is honored at acceptance time.
export interface DispatchLiveState {
  active: boolean;
  busy: boolean;
  options: RoleLauncherOptions | null;
  currentScopedExecution: RoleScopedExecution | undefined;
  activeRequestsRef: ActiveRequestsRef;
  currentSelectionRef: { current: RoleLaunchSelection };
  notify: ((receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void) | undefined;
  notifyRef?: { current: ((receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void) | undefined };
}

export type FetchExecutionResult = (
  scope: RoleDocumentScope,
  reportNotFound: boolean,
  requestId?: string,
) => Promise<RoleExecutionReceipt | undefined>;

// The writers a dispatch drives: the request and receipt stores, the busy marks, the
// error writer, the result poll, and the options reread at the end.
export interface DispatchWriters {
  updateActiveRequest: (scope: RoleDocumentScope, request: RoleTasklessActiveRequest | undefined) => void;
  rememberExecution: (scope: RoleDocumentScope, value: RoleExecutionReceipt) => void;
  setBusyScope: (selection: RoleLaunchSelection | null) => void;
  setLaunchRequestId: (requestId: string | null) => void;
  setExecutionErrorState: SetExecutionErrorState;
  fetchExecutionResult: FetchExecutionResult;
  requestOptionsRefresh: () => void;
}

// A launch that is already running, settled, or unselected never dispatches.
function dispatchRunnable(active: boolean, busy: boolean, options: RoleLauncherOptions | null, complete: boolean): boolean {
  return active && !busy && options !== null && complete;
}

// A start reuses its saved request on retry, else refuses while the receipt is
// starting or unknown; a task-bound start refuses an explicit cannot-start.
function startDispatchBlocked(args: {
  perRequest: boolean;
  replayRequestId: string | undefined;
  activeRequestId: string | undefined;
  canRetry: boolean | undefined;
  currentStatus: string | undefined;
  canStart: boolean | undefined;
}): boolean {
  const { perRequest, replayRequestId, activeRequestId, canRetry, currentStatus, canStart } = args;
  if (perRequest) {
    if (replayRequestId) return activeRequestId !== replayRequestId || canRetry !== true;
    return ["starting", "unknown"].includes(currentStatus ?? "");
  }
  return canStart === false && !replayRequestId;
}

// A revive needs a revivable receipt, and a request-scoped revive reuses its request.
function reviveDispatchBlocked(args: {
  perRequest: boolean;
  replayRequestId: string | undefined;
  canRevive: boolean | undefined;
  requestId: string | undefined;
}): boolean {
  const { perRequest, replayRequestId, canRevive, requestId } = args;
  if (canRevive !== true) return true;
  return perRequest && (!replayRequestId || requestId !== replayRequestId);
}

function readActiveRequestId(
  activeRequestsRef: ActiveRequestsRef,
  launchSelection: RoleLaunchSelection,
): string | undefined {
  return activeRequestsRef.current[roleRequestKey(launchSelection)]?.requestId;
}

function notifyStarted(
  accepted: boolean,
  receipt: RoleExecutionReceipt,
  scope: RoleDocumentScope,
  live: Pick<DispatchLiveState, "notify" | "notifyRef">,
): void {
  // The current callback at acceptance time: the live ref where the pane supplies
  // one, else the notifier the caller passed. A callback removed mid-flight stays
  // silent; a replaced one is the only one called.
  const notify = live.notifyRef ? live.notifyRef.current : live.notify;
  if (accepted && paseoAgentTarget(receipt)) notify?.(receipt, scope);
}

interface DispatchRequest {
  requestScope: RoleDocumentScope;
  requestId: string;
  savedLaunchSelection: RoleLaunchSelection;
  requestBody: Record<string, unknown>;
}

function stepUpRequestFields(selection: RoleLaunchSelection, action: RoleAction): { stepUp?: string } {
  return selection.stepUp && action === "start" ? { stepUp: selection.stepUp } : {};
}

function mayRequestStart(selection: RoleLaunchSelection, execution: RoleScopedExecution | undefined): boolean | undefined {
  return selection.stepUp ? true : execution?.canStart;
}

// The request behind one dispatch: a fresh id (or the replayed one), the saved
// selection for a later retry, and the body. A request-scoped start records its
// pending request before sending.
function buildDispatchRequest(
  updateActiveRequest: DispatchWriters["updateActiveRequest"],
  action: RoleAction,
  launchSelection: RoleLaunchSelection,
  perRequest: boolean,
  replayRequestId: string | undefined,
): DispatchRequest {
  const selected = launchSelection;
  const requestScope = roleDocumentScope(selected);
  const requestId = replayRequestId ?? crypto.randomUUID();
  const savedLaunchSelection: RoleLaunchSelection = {
    ...requestScope,
    ...stepUpRequestFields(selected, action),
    ...(selected.agentOverride ? { agentOverride: selected.agentOverride } : {}),
  };
  if (perRequest && action === "start" && !replayRequestId) {
    updateActiveRequest(requestScope, {
      requestId,
      pending: true,
      retryPayload: savedLaunchSelection,
    });
  }
  const requestBody = {
    requestId,
    action,
    ...requestScope,
    ...stepUpRequestFields(selected, action),
    ...(selected.agentOverride && !(perRequest && action === "revive")
      ? { agentOverride: selected.agentOverride }
      : {}),
  };
  return { requestScope, requestId, savedLaunchSelection, requestBody };
}

// A 409 answers a refusal with its reason: a sticky error the next start clears.
function applyDispatchConflict(
  setExecutionErrorState: SetExecutionErrorState,
  requestScope: RoleDocumentScope,
  value: { detail?: unknown },
): void {
  setExecutionErrorState({
    scope: requestScope,
    message: "The role launch was not accepted (HTTP 409): " + value.detail,
    sticky: true,
  });
}

// A returned receipt becomes the scoped execution; a request-scoped role adopts its
// request id, keeping the pending mark and the retry selection while uncertain.
function adoptDispatchReceipt(
  writers: Pick<DispatchWriters, "updateActiveRequest" | "rememberExecution">,
  live: Pick<DispatchLiveState, "notify" | "notifyRef">,
  activeRequestsRef: ActiveRequestsRef,
  responseOk: boolean,
  receipt: RoleExecutionReceipt,
  requestScope: RoleDocumentScope,
  launchSelection: RoleLaunchSelection,
  perRequest: boolean,
): void {
  const { updateActiveRequest, rememberExecution } = writers;
  rememberExecution(requestScope, receipt);
  notifyStarted(responseOk, receipt, requestScope, live);
  if (perRequest && receipt.requestId) {
    const pending = isUncertainRoleExecution(receipt);
    const kept = activeRequestsRef.current[roleRequestKey(launchSelection)]?.retryPayload;
    updateActiveRequest(
      requestScope,
      pending
        ? {
            requestId: receipt.requestId,
            pending: true,
            ...(receipt.retryPayload ?? kept ? { retryPayload: receipt.retryPayload ?? kept } : {}),
          }
        : { requestId: receipt.requestId },
    );
  }
}

// A receipt that answers the dispatch: a mismatched request id is an error that
// terminates the dispatch (no adoption, no immediate result poll — the run's
// cleanup still runs), else the receipt is adopted and a refused handover stays a
// sticky error. Answers whether the dispatch continues to the result poll.
function applyDispatchReceipt(
  writers: DispatchWriters,
  live: Pick<DispatchLiveState, "notify" | "notifyRef">,
  activeRequestsRef: ActiveRequestsRef,
  response: Response,
  value: RoleExecutionReceipt,
  requestScope: RoleDocumentScope,
  launchSelection: RoleLaunchSelection,
  perRequest: boolean,
  requestId: string,
): boolean {
  const receipt = value;
  if (perRequest && receipt.requestId !== requestId) {
    writers.setExecutionErrorState({ scope: requestScope, message: "The role execution receipt did not match this request." });
    return false;
  }
  adoptDispatchReceipt(writers, live, activeRequestsRef, response.ok, receipt, requestScope, launchSelection, perRequest);
  writers.setExecutionErrorState(
    response.ok || ["accepted", "completed"].includes(receipt.status.toLowerCase())
      ? null
      : {
          scope: requestScope,
          sticky: true,
          message: typeof value.detail === "string"
            ? "The role handover was not accepted (HTTP " + response.status + "): " + value.detail
            : "The role handover was not accepted (HTTP " + response.status + ").",
        },
  );
  return true;
}

// An answer without a receipt settles the run as unknown (server side) or rejected,
// keeping the saved selection for a retry.
function applyDispatchRejection(
  writers: Pick<DispatchWriters, "rememberExecution" | "setExecutionErrorState">,
  response: Response,
  requestScope: RoleDocumentScope,
  requestId: string,
  perRequest: boolean,
  savedLaunchSelection: RoleLaunchSelection,
): void {
  const status = response.status >= 500 ? "unknown" : "rejected";
  writers.rememberExecution(requestScope, {
    status,
    detail: "HTTP " + response.status,
    ...(perRequest
      ? {
          requestId,
          retryPayload: savedLaunchSelection,
          canRetry: status === "unknown",
        }
      : {}),
    canStart: false,
    canRevive: false,
  });
  writers.setExecutionErrorState({
    scope: requestScope,
    message: response.status >= 500
      ? "The role launch returned HTTP " + response.status + "; execution state is unknown. Refresh the AR result before retrying."
      : "The role handover was rejected (HTTP " + response.status + ").",
  });
}

function applyDispatchResponse(
  writers: DispatchWriters,
  live: DispatchLiveState,
  response: Response,
  value: RoleExecutionReceipt | { detail?: unknown },
  requestScope: RoleDocumentScope,
  launchSelection: RoleLaunchSelection,
  perRequest: boolean,
  requestId: string,
  savedLaunchSelection: RoleLaunchSelection,
): boolean {
  if (response.status === 409 && "detail" in value && typeof value.detail === "string") {
    applyDispatchConflict(writers.setExecutionErrorState, requestScope, value);
  } else if ("status" in value && typeof value.status === "string") {
    return applyDispatchReceipt(writers, live, live.activeRequestsRef, response, value, requestScope, launchSelection, perRequest, requestId);
  } else {
    applyDispatchRejection(writers, response, requestScope, requestId, perRequest, savedLaunchSelection);
  }
  return true;
}

// A dispatch that never answered settles its run as unknown, keeping the saved
// selection so the result refresh can recover it. A request-scoped role rereads its
// own request; a task-bound role rereads the role/document address, where the valid
// prior receipt waits — never the newly minted request id.
async function failDispatchOffline(
  writers: DispatchWriters,
  live: DispatchLiveState,
  requestScope: RoleDocumentScope,
  requestId: string,
  perRequest: boolean,
  savedLaunchSelection: RoleLaunchSelection,
): Promise<void> {
  if (!sameRoleDocumentScope(live.currentSelectionRef.current, requestScope)) return;
  writers.rememberExecution(requestScope, {
    status: "unknown",
    detail: "No dispatch receipt was returned.",
    requestId,
    retryPayload: savedLaunchSelection,
    canStart: false,
    canRevive: false,
    canRetry: true,
  });
  writers.setExecutionErrorState({
    scope: requestScope,
    message: "The role launch request did not return. Its state is unknown; refresh the AR result before retrying.",
  });
  await writers.fetchExecutionResult(requestScope, false, perRequest ? requestId : undefined);
}

// The guard behind one dispatch: runnable, then the action's own block. Answers the
// request-scoped flag with the current status, or undefined where it may not send.
function guardDispatchAction(
  live: DispatchLiveState,
  launchSelection: RoleLaunchSelection,
  action: RoleAction,
  replayRequestId: string | undefined,
): { perRequest: boolean } | undefined {
  if (!dispatchRunnable(live.active, live.busy, live.options, launchSelectionComplete(launchSelection))) {
    return undefined;
  }
  const perRequest = usesRequestReceipts(launchSelection);
  const currentStatus = live.currentScopedExecution?.status.toLowerCase();
  const blocked = action === "start"
    ? startDispatchBlocked({
        perRequest,
        replayRequestId,
        activeRequestId: perRequest ? readActiveRequestId(live.activeRequestsRef, launchSelection) : undefined,
        canRetry: live.currentScopedExecution?.canRetry,
        currentStatus,
        canStart: mayRequestStart(launchSelection, live.currentScopedExecution),
      })
    : reviveDispatchBlocked({
        perRequest,
        replayRequestId,
        canRevive: live.currentScopedExecution?.canRevive,
        requestId: live.currentScopedExecution?.requestId,
      });
  if (blocked) return undefined;
  return { perRequest };
}

// Sends one role action (start or revive) for a launch selection: guarded, recorded,
// and polled, with the run settled and the options reread at the end.
export async function dispatchRoleAction(
  live: DispatchLiveState,
  writers: DispatchWriters,
  action: RoleAction,
  launchSelection: RoleLaunchSelection,
  replayRequestId?: string,
): Promise<void> {
  const guard = guardDispatchAction(live, launchSelection, action, replayRequestId);
  if (!guard) return;
  const { perRequest } = guard;
  const { requestScope, requestId, savedLaunchSelection, requestBody } = buildDispatchRequest(
    writers.updateActiveRequest,
    action,
    launchSelection,
    perRequest,
    replayRequestId,
  );
  writers.setBusyScope(launchSelection);
  writers.setLaunchRequestId(requestId);
  writers.setExecutionErrorState(null);
  try {
    const response = await fetch("/api/role-launch/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    const value = (await response.json()) as RoleExecutionReceipt | { detail?: unknown };
    if (!sameRoleDocumentScope(roleDocumentScope(live.currentSelectionRef.current), requestScope)) return;
    const continues = applyDispatchResponse(writers, live, response, value, requestScope, launchSelection, perRequest, requestId, savedLaunchSelection);
    if (continues) await writers.fetchExecutionResult(requestScope, false, perRequest ? requestId : undefined);
  } catch {
    await failDispatchOffline(writers, live, requestScope, requestId, perRequest, savedLaunchSelection);
  } finally {
    if (sameRoleDocumentScope(roleDocumentScope(live.currentSelectionRef.current), requestScope)) {
      writers.setBusyScope(null);
      writers.setLaunchRequestId(null);
      writers.requestOptionsRefresh();
    }
  }
}
