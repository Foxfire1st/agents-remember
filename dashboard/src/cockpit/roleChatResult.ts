import { readRoleReport, type RoleReportContent } from "../data/roleReport";
import {
  isUncertainRoleExecution,
  roleRequestKey,
  usesRequestReceipts,
  type RoleDocumentScope,
  type RoleExecutionReceipt,
  type RoleTasklessActiveRequest,
} from "./roleLaunchModel";

export interface ExecutionErrorState {
  scope: RoleDocumentScope;
  message: string;
  sticky?: boolean;
}

export type SetExecutionErrorState = (
  state:
    | ExecutionErrorState
    | null
    | ((current: ExecutionErrorState | null) => ExecutionErrorState | null),
) => void;

export type ActiveRequestsRef = {
  current: Record<string, RoleTasklessActiveRequest | undefined>;
};

// What a result read needs from the pane's live run state: the current-target check,
// the request and receipt writers, the launch-reread timer, and the error writer.
export interface ResultFlowState {
  activeRequestsRef: ActiveRequestsRef;
  isCurrentExecutionTarget: (scope: RoleDocumentScope, requestId?: string) => boolean;
  updateActiveRequest: (scope: RoleDocumentScope, request: RoleTasklessActiveRequest | undefined) => void;
  rememberExecution: (scope: RoleDocumentScope, value: RoleExecutionReceipt) => void;
  waitForLaunch: () => void;
  setExecutionErrorState: SetExecutionErrorState;
}

function readActiveRequest(state: ResultFlowState, scope: RoleDocumentScope) {
  return usesRequestReceipts(scope) ? state.activeRequestsRef.current[roleRequestKey(scope)] : undefined;
}

// A 404 answers no receipt: a still-pending request keeps an unknown receipt so the
// run can be retried, and an explicit read (not a background poll) reports it.
function handleMissingResult(
  state: ResultFlowState,
  requestScope: RoleDocumentScope,
  reportNotFound: boolean,
  requestId: string | undefined,
): void {
  if (!state.isCurrentExecutionTarget(requestScope, requestId)) return;
  const activeRequest = readActiveRequest(state, requestScope);
  if (activeRequest?.pending && activeRequest.requestId === requestId) {
    const retryPayload = activeRequest.retryPayload;
    state.rememberExecution(requestScope, {
      status: "unknown",
      detail: "No saved role execution receipt was found for this request yet.",
      requestId,
      ...(retryPayload ? { retryPayload } : {}),
      canStart: false,
      canRevive: false,
      canRetry: Boolean(retryPayload),
    });
  }
  if (reportNotFound) {
    state.setExecutionErrorState({
      scope: requestScope,
      message: activeRequest?.pending
        ? "No receipt was found yet. Retry will reuse the same saved request."
        : "No saved role result is available for this request yet.",
    });
  }
}

// A launch-in-progress answer is no error of the selection: an explicit read says to
// wait, and the options are read again shortly either way.
function handleResultInProgress(
  state: ResultFlowState,
  requestScope: RoleDocumentScope,
  reportNotFound: boolean,
): void {
  if (reportNotFound) {
    state.setExecutionErrorState({
      scope: requestScope,
      message: "The report could not be opened while a role launch or result check is in progress. Wait for it to finish, then press Result again.",
    });
  }
  state.waitForLaunch();
}

function handleResultError(
  state: ResultFlowState,
  requestScope: RoleDocumentScope,
  status: number,
  value: { detail?: unknown },
): void {
  state.setExecutionErrorState({
    scope: requestScope,
    message: typeof value.detail === "string"
      ? "Could not read the role result (HTTP " + status + "): " + value.detail
      : "Could not read the role result (HTTP " + status + ").",
  });
}

// A returned receipt is adopted for its request: the pending mark and the retry
// selection stay while the execution is uncertain.
function adoptResultRequest(
  state: ResultFlowState,
  requestScope: RoleDocumentScope,
  receipt: RoleExecutionReceipt,
): void {
  if (!usesRequestReceipts(requestScope) || !receipt.requestId) return;
  const current = readActiveRequest(state, requestScope);
  const pending = isUncertainRoleExecution(receipt);
  state.updateActiveRequest(
    requestScope,
    pending
      ? {
          requestId: receipt.requestId,
          pending: true,
          ...(receipt.retryPayload ?? current?.retryPayload
            ? { retryPayload: receipt.retryPayload ?? current?.retryPayload }
            : {}),
        }
      : { requestId: receipt.requestId },
  );
}

// A returned receipt becomes the scoped execution, and a request-scoped role adopts
// its request id (keeping the pending mark while the execution is uncertain).
function applyResultReceipt(
  state: ResultFlowState,
  requestScope: RoleDocumentScope,
  receipt: RoleExecutionReceipt,
  requestId: string | undefined,
): RoleExecutionReceipt | undefined {
  if (requestId && receipt.requestId !== requestId) {
    state.setExecutionErrorState({ scope: requestScope, message: "The role result did not match the selected request." });
    return undefined;
  }
  state.rememberExecution(requestScope, receipt);
  adoptResultRequest(state, requestScope, receipt);
  // Automatic reads retain a refused dispatch; a successful explicit action clears it.
  state.setExecutionErrorState((current) => (current?.sticky ? current : null));
  return receipt;
}

// A parsed result answer becomes the scoped execution: a reread mark while a launch
// is in progress, an error without a receipt, else the returned receipt.
function handleResultResponse(
  state: ResultFlowState,
  response: Response,
  value: RoleExecutionReceipt | { detail?: unknown; launchInProgress?: unknown },
  requestScope: RoleDocumentScope,
  reportNotFound: boolean,
  requestId: string | undefined,
): RoleExecutionReceipt | undefined {
  if (!state.isCurrentExecutionTarget(requestScope, requestId)) return undefined;
  if (!response.ok && "launchInProgress" in value && value.launchInProgress === true) {
    handleResultInProgress(state, requestScope, reportNotFound);
    return undefined;
  }
  if (!response.ok) {
    handleResultError(state, requestScope, response.status, value);
    return undefined;
  }
  if (!("status" in value) || typeof value.status !== "string") {
    state.setExecutionErrorState({ scope: requestScope, message: "The role result did not contain an execution status." });
    return undefined;
  }
  return applyResultReceipt(state, requestScope, value as RoleExecutionReceipt, requestId);
}

// Reads one role result and folds it into the scoped execution. Answers the receipt,
// or undefined where there is none to show.
export async function fetchRoleExecutionResult(
  state: ResultFlowState,
  requestScope: RoleDocumentScope,
  reportNotFound: boolean,
  requestId?: string,
): Promise<RoleExecutionReceipt | undefined> {
  if (usesRequestReceipts(requestScope) && !requestId) return undefined;
  try {
    const response = await fetch("/api/role-launch/result", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...requestScope, ...(requestId ? { requestId } : {}) }),
    });
    if (response.status === 404) {
      handleMissingResult(state, requestScope, reportNotFound, requestId);
      return undefined;
    }
    const value = (await response.json()) as RoleExecutionReceipt | { detail?: unknown; launchInProgress?: unknown };
    return handleResultResponse(state, response, value, requestScope, reportNotFound, requestId);
  } catch {
    if (state.isCurrentExecutionTarget(requestScope, requestId)) {
      state.setExecutionErrorState({
        scope: requestScope,
        message: "Could not read the role result. Check the dashboard connection and refresh it.",
      });
    }
    return undefined;
  }
}

// Opens the recorded report behind a receipt in the notes reader. A report read that
// loses its race, or a receipt without a request id, leaves the pane as it is.
export async function openRoleExecutionReport(
  state: Pick<ResultFlowState, "isCurrentExecutionTarget" | "setExecutionErrorState">,
  setReport: (content: RoleReportContent | null) => void,
  requestScope: RoleDocumentScope,
  requestId: string | undefined,
  receipt: RoleExecutionReceipt | undefined,
): Promise<void> {
  if (!receipt || !state.isCurrentExecutionTarget(requestScope, requestId)) return;
  try {
    if (!receipt.requestId) throw new Error("The execution has no recorded request ID for its report. Refresh and retry.");
    const content = await readRoleReport(requestScope, receipt.requestId);
    if (state.isCurrentExecutionTarget(requestScope, receipt.requestId)) {
      state.setExecutionErrorState(null);
      setReport(content);
    }
  } catch (error) {
    if (state.isCurrentExecutionTarget(requestScope, requestId)) {
      state.setExecutionErrorState({
        scope: requestScope,
        sticky: true,
        message: error instanceof Error ? error.message : "The report could not be opened. Refresh and retry.",
      });
    }
  }
}
