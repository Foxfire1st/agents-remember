import { useCallback, useRef, useState } from "react";

import {
  readTasklessActiveRequests,
  roleDocumentScope,
  roleRequestKey,
  roleRequestStorageKey,
  sameRoleDocumentScope,
  sameRoleLaunchSelection,
  usesRequestReceipts,
  type RoleAction,
  type RoleDocumentScope,
  type RoleExecutionReceipt,
  type RoleLaunchSelection,
  type RoleScopedExecution,
  type RoleTasklessActiveRequest,
} from "./roleLaunchModel";
import {
  fetchRoleExecutionResult,
  openRoleExecutionReport,
  type ActiveRequestsRef,
  type ExecutionErrorState,
  type ResultFlowState,
  type SetExecutionErrorState,
} from "./roleChatResult";
import { dispatchRoleAction, type DispatchLiveState, type DispatchWriters } from "./roleChatDispatch";
import type { RoleReportContent } from "../data/roleReport";

// The pane's live run state: the saved per-request launches, the scoped receipts and
// errors, and the busy marks. The writers touch only stable refs and state setters,
// so updateActiveRequest, rememberExecution, isCurrentExecutionTarget and
// fetchExecutionResult are stable for the pane's lifetime, and the options effect can
// list them without refiring. dispatch and refreshResult run from event handlers, so
// they read the live render values through plain closures.

// Mirrors one request update into state and session storage. Answers whether the
// request id turned over, clearing the execution error then.
function applyActiveRequestUpdate(
  activeRequestsRef: ActiveRequestsRef,
  setActiveRequests: (update: (current: Record<string, RoleTasklessActiveRequest | undefined>) => Record<string, RoleTasklessActiveRequest | undefined>) => void,
  scope: RoleDocumentScope,
  request: RoleTasklessActiveRequest | undefined,
): boolean {
  const key = roleRequestKey(scope);
  const current = activeRequestsRef.current;
  const next = { ...current };
  if (request) {
    next[key] = request;
    sessionStorage.setItem(roleRequestStorageKey(scope), JSON.stringify(request));
  } else {
    delete next[key];
    sessionStorage.removeItem(roleRequestStorageKey(scope));
  }
  activeRequestsRef.current = next;
  setActiveRequests(() => next);
  return current[key]?.requestId !== request?.requestId;
}

// The run's stored state: the saved requests, receipts, errors and busy marks with
// their stable writers.
function useRoleChatRunState(currentSelectionRef: { current: RoleLaunchSelection }): {
  activeRequestsRef: ActiveRequestsRef;
  executionState: { scope: RoleDocumentScope; value: RoleExecutionReceipt } | null;
  executionErrorState: ExecutionErrorState | null;
  failureExecutionRef: { current: { scope: RoleDocumentScope; value: RoleExecutionReceipt } | null };
  busyScope: RoleLaunchSelection | null;
  launchRequestId: string | null;
  updateActiveRequest: ResultFlowState["updateActiveRequest"];
  rememberExecution: ResultFlowState["rememberExecution"];
  rememberFailureExecution: ResultFlowState["rememberExecution"];
  isCurrentExecutionTarget: ResultFlowState["isCurrentExecutionTarget"];
  setExecutionState: (state: { scope: RoleDocumentScope; value: RoleExecutionReceipt } | null | ((current: { scope: RoleDocumentScope; value: RoleExecutionReceipt } | null) => { scope: RoleDocumentScope; value: RoleExecutionReceipt } | null)) => void;
  setExecutionErrorState: SetExecutionErrorState;
  setBusyScope: (selection: RoleLaunchSelection | null) => void;
  setLaunchRequestId: (requestId: string | null) => void;
} {
  const [executionState, setExecutionState] = useState<{
    scope: RoleDocumentScope;
    value: RoleExecutionReceipt;
  } | null>(null);
  const [executionErrorState, setExecutionErrorState] = useState<ExecutionErrorState | null>(null);
  const [launchRequestId, setLaunchRequestId] = useState<string | null>(null);
  const [busyScope, setBusyScope] = useState<RoleLaunchSelection | null>(null);
  const failureExecutionRef = useRef<{ scope: RoleDocumentScope; value: RoleExecutionReceipt } | null>(null);
  const [activeRequests, setActiveRequests] = useState<Record<string, RoleTasklessActiveRequest | undefined>>(
    readTasklessActiveRequests,
  );
  const activeRequestsRef: ActiveRequestsRef = useRef(activeRequests);
  activeRequestsRef.current = activeRequests;
  const updateActiveRequest = useCallback(
    (scope: RoleDocumentScope, request: RoleTasklessActiveRequest | undefined) => {
      if (!usesRequestReceipts(scope)) return;
      const turnedOver = applyActiveRequestUpdate(activeRequestsRef, setActiveRequests, scope, request);
      if (turnedOver) {
        setExecutionErrorState((error) => (error && sameRoleDocumentScope(error.scope, scope) ? null : error));
      }
    },
    [activeRequestsRef],
  );
  const rememberExecution = useCallback(
    (scope: RoleDocumentScope, value: RoleExecutionReceipt) => {
      // Controls may clear their receipt on refresh; failure rendering keeps the last scoped answer.
      failureExecutionRef.current = { scope, value };
      setExecutionState({ scope, value });
    },
    [],
  );
  // The options answer's failure reference only: it never becomes the active
  // execution authority, so a delayed options reply cannot overwrite a newer Result
  // receipt. The launcher still renders the options answer's own execution value.
  const rememberFailureExecution = useCallback(
    (scope: RoleDocumentScope, value: RoleExecutionReceipt) => {
      failureExecutionRef.current = { scope, value };
    },
    [],
  );
  const isCurrentExecutionTarget = useCallback(
    (scope: RoleDocumentScope, requestId?: string) => {
      if (!sameRoleDocumentScope(roleDocumentScope(currentSelectionRef.current), scope)) return false;
      return (
        !usesRequestReceipts(scope) || activeRequestsRef.current[roleRequestKey(scope)]?.requestId === requestId
      );
    },
    [activeRequestsRef, currentSelectionRef],
  );
  return {
    activeRequestsRef,
    executionState,
    executionErrorState,
    failureExecutionRef,
    busyScope,
    launchRequestId,
    updateActiveRequest,
    rememberExecution,
    rememberFailureExecution,
    isCurrentExecutionTarget,
    setExecutionState,
    setExecutionErrorState,
    setBusyScope,
    setLaunchRequestId,
  };
}

type RunState = ReturnType<typeof useRoleChatRunState>;

// Reads the end of a turn: the result behind the selected request, with its report
// opened where one answered. A busy or loading run, or an incomplete selection, stays.
async function refreshRoleResult(
  run: RunState,
  support: {
    currentSelectionRef: { current: RoleLaunchSelection };
    isCurrentExecutionTarget: ResultFlowState["isCurrentExecutionTarget"];
    setExecutionErrorState: SetExecutionErrorState;
    setReport: (content: RoleReportContent | null) => void;
    fetchExecutionResult: (scope: RoleDocumentScope, reportNotFound: boolean, requestId?: string) => Promise<RoleExecutionReceipt | undefined>;
    setBusyScope: (selection: RoleLaunchSelection | null) => void;
  },
  selection: RoleLaunchSelection,
  selectionComplete: boolean,
  busy: boolean,
  optionsLoading: boolean,
  currentScopedExecution: RoleScopedExecution | undefined,
): Promise<void> {
  if (!selectionComplete || busy || optionsLoading) return;
  const selected = selection;
  const requestScope = roleDocumentScope(selected);
  const requestId = usesRequestReceipts(selected)
    ? run.activeRequestsRef.current[roleRequestKey(selected)]?.requestId
    : currentScopedExecution?.requestId;
  if (usesRequestReceipts(selected) && !requestId) return;
  support.setBusyScope(selected);
  try {
    await openRoleExecutionReport(
      { isCurrentExecutionTarget: support.isCurrentExecutionTarget, setExecutionErrorState: support.setExecutionErrorState },
      support.setReport,
      requestScope,
      requestId,
      await support.fetchExecutionResult(requestScope, true, requestId),
    );
  } finally {
    if (sameRoleLaunchSelection(support.currentSelectionRef.current, selected)) support.setBusyScope(null);
  }
}

// The flow state one result poll runs with: the run's stable writers with the reread.
function resultFlowFor(
  run: Pick<RunState, "activeRequestsRef" | "isCurrentExecutionTarget" | "updateActiveRequest" | "rememberExecution" | "setExecutionErrorState">,
  waitForLaunch: () => void,
): ResultFlowState {
  return {
    activeRequestsRef: run.activeRequestsRef,
    isCurrentExecutionTarget: run.isCurrentExecutionTarget,
    updateActiveRequest: run.updateActiveRequest,
    rememberExecution: run.rememberExecution,
    waitForLaunch,
    setExecutionErrorState: run.setExecutionErrorState,
  };
}

// The writers one dispatch drives: the run's stable writers with the busy marks, the
// poll, and the options reread.
function dispatchWritersFor(
  run: Pick<RunState, "updateActiveRequest" | "rememberExecution" | "setExecutionErrorState" | "setBusyScope" | "setLaunchRequestId">,
  fetchExecutionResult: (scope: RoleDocumentScope, reportNotFound: boolean, requestId?: string) => Promise<RoleExecutionReceipt | undefined>,
  requestOptionsRefresh: () => void,
): DispatchWriters {
  return {
    updateActiveRequest: run.updateActiveRequest,
    rememberExecution: run.rememberExecution,
    setBusyScope: run.setBusyScope,
    setLaunchRequestId: run.setLaunchRequestId,
    setExecutionErrorState: run.setExecutionErrorState,
    fetchExecutionResult,
    requestOptionsRefresh,
  };
}
function useRoleChatRunActions(
  run: RunState,
  waitForLaunch: () => void,
  requestOptionsRefresh: () => void,
  currentSelectionRef: { current: RoleLaunchSelection },
  onExecution: ((receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void) | undefined,
  setReport: (content: RoleReportContent | null) => void,
): {
  fetchExecutionResult: (scope: RoleDocumentScope, reportNotFound: boolean, requestId?: string) => Promise<RoleExecutionReceipt | undefined>;
  dispatch: (live: Omit<DispatchLiveState, "activeRequestsRef" | "currentSelectionRef" | "notify" | "notifyRef">, action: RoleAction, launchSelection: RoleLaunchSelection, replayRequestId?: string) => Promise<void>;
  refreshResult: (selection: RoleLaunchSelection, selectionComplete: boolean, busy: boolean, optionsLoading: boolean, currentScopedExecution: RoleScopedExecution | undefined) => Promise<void>;
} {
  const {
    activeRequestsRef,
    updateActiveRequest,
    rememberExecution,
    isCurrentExecutionTarget,
    setExecutionErrorState,
  } = run;
  const executionCallback = useRef(onExecution);
  executionCallback.current = onExecution;
  const fetchExecutionResult = useCallback(
    (scope: RoleDocumentScope, reportNotFound: boolean, requestId?: string) =>
      fetchRoleExecutionResult(
        resultFlowFor(
          { activeRequestsRef, isCurrentExecutionTarget, updateActiveRequest, rememberExecution, setExecutionErrorState },
          waitForLaunch,
        ),
        scope,
        reportNotFound,
        requestId,
      ),
    [activeRequestsRef, isCurrentExecutionTarget, updateActiveRequest, rememberExecution, waitForLaunch, setExecutionErrorState],
  );
  const dispatch = useCallback(
    (
      live: Omit<DispatchLiveState, "activeRequestsRef" | "currentSelectionRef" | "notify" | "notifyRef">,
      action: RoleAction,
      launchSelection: RoleLaunchSelection,
      replayRequestId?: string,
    ) => {
      const writers = dispatchWritersFor(run, fetchExecutionResult, requestOptionsRefresh);
      return dispatchRoleAction(
        { ...live, activeRequestsRef, currentSelectionRef, notify: executionCallback.current, notifyRef: executionCallback },
        writers,
        action,
        launchSelection,
        replayRequestId,
      );
    },
    [run, activeRequestsRef, currentSelectionRef, fetchExecutionResult, requestOptionsRefresh],
  );
  const refreshResult = useCallback(
    (
      selection: RoleLaunchSelection,
      selectionComplete: boolean,
      busy: boolean,
      optionsLoading: boolean,
      currentScopedExecution: RoleScopedExecution | undefined,
    ) =>
      refreshRoleResult(
        run,
        {
          currentSelectionRef,
          isCurrentExecutionTarget,
          setExecutionErrorState,
          setReport,
          fetchExecutionResult,
          setBusyScope: run.setBusyScope,
        },
        selection,
        selectionComplete,
        busy,
        optionsLoading,
        currentScopedExecution,
      ),
    [run, currentSelectionRef, isCurrentExecutionTarget, setExecutionErrorState, setReport, fetchExecutionResult],
  );
  return { fetchExecutionResult, dispatch, refreshResult };
}

export interface RoleChatControlsArgs {
  waitForLaunch: () => void;
  requestOptionsRefresh: () => void;
  currentSelectionRef: { current: RoleLaunchSelection };
  onExecution: ((receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void) | undefined;
  setReport: (content: RoleReportContent | null) => void;
}

export type RoleChatControls = RunState & ReturnType<typeof useRoleChatRunActions>;

// The pane's live run state with its actions, composed from the stored state.
export function useRoleChatControls(args: RoleChatControlsArgs): RoleChatControls {
  const run = useRoleChatRunState(args.currentSelectionRef);
  const actions = useRoleChatRunActions(
    run,
    args.waitForLaunch,
    args.requestOptionsRefresh,
    args.currentSelectionRef,
    args.onExecution,
    args.setReport,
  );
  return { ...run, ...actions };
}
