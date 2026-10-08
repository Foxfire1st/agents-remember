import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { css } from "../../styled-system/css";
import type { SeriesNode, TaskDocNode } from "../types/projection";


import { readRoleReport, type RoleReportContent } from "../data/roleReport";
import { NotesReaderViewer } from "../panels/notes-reader/NotesReaderViewer";
import { boundTasklessRequest, isTasklessRole, isUncertainRoleExecution, launchSelectionComplete, mergeRoleAgentInventory, roleDocumentScope, roleOptionsScope, readTasklessActiveRequests, roleDefaultsCacheKey, sameRoleDocumentScope, sameRoleLaunchSelection, sameRoleOptionsScope, tasklessRequestStorageKey, type RoleAction, type RoleAgentInventory, type RoleDocumentScope, type RoleExecutionReceipt, type RoleLaunchSelection, type RoleLauncherOptions, type RoleOptionsScope, type LauncherRole, type RoleDefaults, type RoleTasklessActiveRequest } from "./roleLaunchModel";
import { PaseoChatFrame } from "./PaseoChatFrame";
import { paseoAgentTarget } from "./paseoFrameModel";

import { RoleLauncher } from "./role-launcher/RoleLauncher";
import { useRoleLaunchProgress } from "./role-launcher/useRoleLaunchProgress";

const rolePane = css({
  display: "flex",
  flex: "1",
  flexDirection: "column",
  minHeight: "0",
  minWidth: "0",
  overflow: "hidden",
  border: "1px solid var(--grid)",
  background: "var(--bg-panel)",
});
// How soon the options are read again after the backend answered that a launch is in progress.
const LAUNCH_REREAD_MS = 1500;

function adoptBoundRequest(
  bound: boolean, role: LauncherRole, executions: RoleExecutionReceipt[],
  active: RoleTasklessActiveRequest | undefined,
  update: (role: LauncherRole, request: RoleTasklessActiveRequest | undefined) => void,
) {
  if (!bound) return active;
  const request = boundTasklessRequest(executions, active);
  if (request?.requestId !== active?.requestId) update(role, request);
  return request;
}

function notifyStarted(
  accepted: boolean, receipt: RoleExecutionReceipt, scope: RoleDocumentScope,
  notify: ((receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void) | undefined,
) {
  if (accepted && paseoAgentTarget(receipt)) notify?.(receipt, scope);
}

function rolePanePresentation(report: RoleReportContent | null, bound: boolean) {
  return report ? { display: "none" } : bound ? { flex: "0 0 auto" } : undefined;
}

function unboundChatFrame(bound: boolean, props: Parameters<typeof PaseoChatFrame>[0]) {
  return bound ? null : <PaseoChatFrame {...props} />;
}

export function RoleChatsPane({
  active,
  taskDocuments,
  series,
  boundSelection,
  boundRoles,
  onExecution,
}: {
  active: boolean;
  taskDocuments: TaskDocNode[];
  series: SeriesNode[];
  boundSelection?: RoleLaunchSelection;
  boundRoles?: LauncherRole[];
  onExecution?: (receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void;
}) {
  const executionCallback = useRef(onExecution);
  executionCallback.current = onExecution;
  const [report, setReport] = useState<RoleReportContent | null>(null);
  const [selection, setSelection] = useState<RoleLaunchSelection>(() => boundSelection ?? { role: "architect" });
  const [optionsRefresh, setOptionsRefresh] = useState(0);
  const [catalogRefresh, setCatalogRefresh] = useState(0);
  const [optionsState, setOptionsState] = useState<{ scope: RoleOptionsScope; value: RoleLauncherOptions } | null>(null);
  const [agentInventory, setAgentInventory] = useState<RoleAgentInventory | null>(null);
  const [roleDefaultsCache, setRoleDefaultsCache] = useState<Record<string, RoleDefaults>>({});
  const [optionsLoadingScope, setOptionsLoadingScope] = useState<RoleOptionsScope | null>(null);
  const [optionsErrorState, setOptionsErrorState] = useState<{ scope: RoleOptionsScope; message: string } | null>(null);
  const [executionState, setExecutionState] = useState<{ scope: RoleDocumentScope; value: RoleExecutionReceipt } | null>(null);
  const [executionErrorState, setExecutionErrorState] = useState<{ scope: RoleDocumentScope; message: string; sticky?: boolean } | null>(null);
  const [launchRequestId, setLaunchRequestId] = useState<string | null>(null);
  const progress = useRoleLaunchProgress(launchRequestId);
  const [busyScope, setBusyScope] = useState<RoleLaunchSelection | null>(null);
  const rereadTimer = useRef<number | undefined>(undefined);
  const failureExecutionRef = useRef<{ scope: RoleDocumentScope; value: RoleExecutionReceipt } | null>(null);
  const [tasklessActiveRequests, setTasklessActiveRequests] = useState(readTasklessActiveRequests);
  const sentCatalogRefresh = useRef(0);
  const explicitOptionsRefresh = useRef(false);
  const currentSelectionRef = useRef(selection);
  currentSelectionRef.current = selection;
  const tasklessActiveRequestsRef = useRef(tasklessActiveRequests);
  tasklessActiveRequestsRef.current = tasklessActiveRequests;
  const currentDocumentScope = useMemo(
    () => roleDocumentScope(selection),
    [
      selection.role,
      selection.sprintDocumentRef?.repository,
      selection.sprintDocumentRef?.path,
      selection.masterDocumentRef?.repository,
      selection.masterDocumentRef?.path,
      selection.taskDocumentRef?.repository,
      selection.taskDocumentRef?.path,
    ],
  );
  const currentOptionsScope = useMemo(
    () => roleOptionsScope(selection),
    [currentDocumentScope, selection.agentOverride?.agentId],
  );
  const isCurrentOptionsScope = useCallback(
    (scope: RoleOptionsScope) => sameRoleOptionsScope(roleOptionsScope(currentSelectionRef.current), scope),
    [],
  );
  const isCurrentExecutionTarget = useCallback((scope: RoleDocumentScope, requestId?: string) => {
    if (!sameRoleDocumentScope(roleDocumentScope(currentSelectionRef.current), scope)) return false;
    return !isTasklessRole(scope.role) || tasklessActiveRequestsRef.current[scope.role]?.requestId === requestId;
  }, []);
  const updateTasklessActiveRequest = useCallback((
    role: LauncherRole,
    request: RoleTasklessActiveRequest | undefined,
  ) => {
    if (!isTasklessRole(role)) return;
    const current = tasklessActiveRequestsRef.current;
    const next = { ...current };
    if (request) {
      next[role] = request;
      sessionStorage.setItem(tasklessRequestStorageKey(role), JSON.stringify(request));
    } else {
      delete next[role];
      sessionStorage.removeItem(tasklessRequestStorageKey(role));
    }
    tasklessActiveRequestsRef.current = next;
    setTasklessActiveRequests(next);
    if (current[role]?.requestId !== request?.requestId) {
      setExecutionErrorState((error) => error?.scope.role === role ? null : error);
    }
  }, []);
  const options = optionsState && sameRoleOptionsScope(optionsState.scope, currentOptionsScope)
    ? optionsState.value
    : null;
  const optionsLoading = Boolean(optionsLoadingScope && sameRoleOptionsScope(optionsLoadingScope, currentOptionsScope));
  const busy = Boolean(busyScope && sameRoleDocumentScope(roleDocumentScope(busyScope), currentDocumentScope));
  // A launch holds the backend's launch lock for as long as it runs, and the options and result
  // routes answer that with a mark of their own. That is no error of the selection shown: the
  // launcher reads again shortly, until the launch has answered.
  const waitForLaunch = useCallback(() => {
    window.clearTimeout(rereadTimer.current);
    rereadTimer.current = window.setTimeout(() => setOptionsRefresh((current) => current + 1), LAUNCH_REREAD_MS);
  }, []);
  useEffect(() => () => window.clearTimeout(rereadTimer.current), []);
  const optionsError = optionsErrorState && sameRoleOptionsScope(optionsErrorState.scope, currentOptionsScope)
    ? optionsErrorState.message
    : null;
  const activeTasklessRequest = isTasklessRole(selection.role)
    ? tasklessActiveRequests[selection.role]
    : undefined;
  const savedTasklessExecution = isTasklessRole(selection.role) && activeTasklessRequest
    ? options?.executions?.find((execution) => execution.requestId === activeTasklessRequest.requestId)
    : undefined;
  const executionReceipt = executionState && sameRoleDocumentScope(executionState.scope, currentDocumentScope) &&
    (!isTasklessRole(selection.role) || executionState.value.requestId === activeTasklessRequest?.requestId)
    ? executionState.value
    : null;
  const pendingTasklessExecution = activeTasklessRequest?.pending && !executionReceipt && !savedTasklessExecution
    ? {
        status: busy ? "starting" : "unknown",
        detail: busy ? undefined : "The saved request has not returned a receipt yet.",
        requestId: activeTasklessRequest.requestId,
        retryPayload: activeTasklessRequest.retryPayload,
        canStart: false,
        canRevive: false,
        canRetry: !busy && Boolean(activeTasklessRequest.retryPayload),
      }
    : undefined;
  const selectedTasklessExecution = executionReceipt ?? savedTasklessExecution;
  const executionError = executionErrorState && sameRoleDocumentScope(executionErrorState.scope, currentDocumentScope)
    ? executionErrorState.message
    : null;
  const currentScopedExecution = !optionsLoading && isTasklessRole(selection.role)
    ? selectedTasklessExecution
      ? {
          ...selectedTasklessExecution,
          ...(selectedTasklessExecution.retryPayload ?? activeTasklessRequest?.retryPayload
            ? { retryPayload: selectedTasklessExecution.retryPayload ?? activeTasklessRequest?.retryPayload }
            : {}),
        }
      : pendingTasklessExecution
    : !optionsLoading ? executionReceipt ?? options?.execution ?? undefined : undefined;
  const lastFailureExecution = failureExecutionRef.current;
  const failureExecution = lastFailureExecution && isCurrentExecutionTarget(lastFailureExecution.scope, lastFailureExecution.value.requestId)
    ? lastFailureExecution.value : currentScopedExecution;
  const retryRequestId = currentScopedExecution?.canRetry &&
    (!isTasklessRole(selection.role) || Boolean(currentScopedExecution.retryPayload))
    ? currentScopedExecution.requestId
    : undefined;
  const selectionComplete = launchSelectionComplete(selection);

  // The frame follows the launcher's selected execution. Its host fields are
  // read from the receipt itself (for a taskless role the options answer puts the saved
  // execution of the active request there), else from the options answer of a task-bound role.
  const frameScope = useMemo(() => JSON.stringify(currentDocumentScope), [currentDocumentScope]);
  const frameTarget = paseoAgentTarget(executionReceipt ?? options?.execution);

  useEffect(() => {
    if (!active || !selectionComplete) return;
    const requestScope = currentOptionsScope;
    const retryOptions = explicitOptionsRefresh.current;
    explicitOptionsRefresh.current = false;
    const refreshAgentCatalog = catalogRefresh > sentCatalogRefresh.current;
    if (refreshAgentCatalog) sentCatalogRefresh.current = catalogRefresh;
    const controller = new AbortController();
    const body = {
      ...currentDocumentScope,
      ...(requestScope.agentId ? { agentId: requestScope.agentId } : {}),
      ...(refreshAgentCatalog ? { refreshCatalog: true } : {}),
    };
    setOptionsLoadingScope(requestScope);
    setExecutionState((current) =>
      current && sameRoleDocumentScope(current.scope, currentDocumentScope) ? null : current,
    );
    void fetch("/api/role-launch/options", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) {
        const value = (await response.json().catch(() => ({}))) as { detail?: unknown; launchInProgress?: unknown };
        if (isCurrentOptionsScope(requestScope) && value.launchInProgress === true) {
          waitForLaunch();
        } else if (isCurrentOptionsScope(requestScope)) {
          setOptionsErrorState({
            scope: requestScope,
            message: typeof value.detail === "string"
              ? "Could not load role launch options (HTTP " + response.status + "): " + value.detail
              : "Could not load role launch options (HTTP " + response.status + ").",
          });
        }
        return;
      }
      const value = (await response.json()) as RoleLauncherOptions;
      if (!isCurrentOptionsScope(requestScope)) return;
      setOptionsErrorState(null);
      if (value.execution) failureExecutionRef.current = { scope: currentDocumentScope, value: value.execution };
      setOptionsState({ scope: requestScope, value });
      setExecutionErrorState((current) =>
        refreshAgentCatalog || retryOptions || !current?.sticky ? null : current,
      );
      setRoleDefaultsCache((current) => ({
        ...current,
        [roleDefaultsCacheKey(currentDocumentScope)]: value.roleDefaults,
      }));
      setAgentInventory((current) => mergeRoleAgentInventory(
        current,
        value,
        requestScope.agentId ?? value.roleDefaults.agent,
        refreshAgentCatalog,
      ));
      if (isTasklessRole(requestScope.role)) {
        const role = requestScope.role;
        const executions = value.executions ?? [];
        const activeRequest = adoptBoundRequest(Boolean(boundSelection), role, executions,
          tasklessActiveRequestsRef.current[role], updateTasklessActiveRequest);
        const savedExecution = activeRequest
          ? executions.find((execution) => execution.requestId === activeRequest?.requestId)
          : undefined;
        if (savedExecution?.requestId) {
          const pending = isUncertainRoleExecution(savedExecution);
          updateTasklessActiveRequest(role, pending
            ? {
                requestId: savedExecution.requestId,
                pending: true,
                ...(activeRequest?.retryPayload ?? savedExecution.retryPayload
                  ? { retryPayload: activeRequest?.retryPayload ?? savedExecution.retryPayload }
                  : {}),
              }
            : { requestId: savedExecution.requestId });
          rememberExecution(requestScope, savedExecution);
        }
        const activeRequestId = tasklessActiveRequestsRef.current[role]?.requestId;
        if (activeRequestId) void fetchExecutionResult({ role }, false, activeRequestId);
      }
    }).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (isCurrentOptionsScope(requestScope)) {
        setOptionsErrorState({
          scope: requestScope,
          message: "Could not load role launch options. Check the dashboard connection and retry.",
        });
      }
    }).finally(() => {
      if (!controller.signal.aborted && isCurrentOptionsScope(requestScope)) setOptionsLoadingScope(null);
    });
    return () => controller.abort();
  }, [active, boundSelection, selectionComplete, currentDocumentScope, currentOptionsScope, optionsRefresh, catalogRefresh, isCurrentOptionsScope, waitForLaunch]);

  function rememberExecution(scope: RoleDocumentScope, value: RoleExecutionReceipt): void {
    // Controls may clear their receipt on refresh; failure rendering keeps the last scoped answer.
    failureExecutionRef.current = { scope, value };
    setExecutionState({ scope, value });
  }

  async function fetchExecutionResult(
    requestScope: RoleDocumentScope,
    reportNotFound: boolean,
    requestId?: string,
  ): Promise<RoleExecutionReceipt | undefined> {
    if (isTasklessRole(requestScope.role) && !requestId) return;
    try {
      const response = await fetch("/api/role-launch/result", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...requestScope, ...(requestId ? { requestId } : {}) }),
      });
      if (response.status === 404) {
        if (!isCurrentExecutionTarget(requestScope, requestId)) return;
        const activeRequest = isTasklessRole(requestScope.role)
          ? tasklessActiveRequestsRef.current[requestScope.role]
          : undefined;
        if (activeRequest?.pending && activeRequest.requestId === requestId) {
          const retryPayload = activeRequest.retryPayload;
          rememberExecution(requestScope, {
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
          setExecutionErrorState({
            scope: requestScope,
            message: activeRequest?.pending
              ? "No receipt was found yet. Retry will reuse the same saved request."
              : "No saved role result is available for this request yet.",
          });
        }
        return;
      }
      const value = (await response.json()) as RoleExecutionReceipt | { detail?: unknown; launchInProgress?: unknown };
      if (!isCurrentExecutionTarget(requestScope, requestId)) return;
      if (!response.ok && "launchInProgress" in value && value.launchInProgress === true) {
        if (reportNotFound) setExecutionErrorState({
          scope: requestScope,
          message: "The report could not be opened while a role launch or result check is in progress. Wait for it to finish, then press Result again.",
        });
        waitForLaunch();
        return;
      }
      if (!response.ok) {
        setExecutionErrorState({
          scope: requestScope,
          message: typeof value.detail === "string"
            ? "Could not read the role result (HTTP " + response.status + "): " + value.detail
            : "Could not read the role result (HTTP " + response.status + ").",
        });
        return;
    }
    if (!("status" in value) || typeof value.status !== "string") {
      setExecutionErrorState({ scope: requestScope, message: "The role result did not contain an execution status." });
      return;
      }
      const receipt = value as RoleExecutionReceipt;
      if (requestId && receipt.requestId !== requestId) {
        setExecutionErrorState({ scope: requestScope, message: "The role result did not match the selected request." });
        return;
      }
      rememberExecution(requestScope, receipt);
      if (isTasklessRole(requestScope.role) && receipt.requestId) {
        const current = tasklessActiveRequestsRef.current[requestScope.role];
        const pending = isUncertainRoleExecution(receipt);
        updateTasklessActiveRequest(requestScope.role, pending
          ? {
              requestId: receipt.requestId,
              pending: true,
              ...(receipt.retryPayload ?? current?.retryPayload
                ? { retryPayload: receipt.retryPayload ?? current?.retryPayload }
                : {}),
            }
          : { requestId: receipt.requestId });
      }
      // Automatic reads retain a refused dispatch; a successful explicit action clears it.
      setExecutionErrorState((current) => (current?.sticky ? current : null));
      return receipt;
    } catch {
      if (isCurrentExecutionTarget(requestScope, requestId)) {
        setExecutionErrorState({
          scope: requestScope,
          message: "Could not read the role result. Check the dashboard connection and refresh it.",
        });
      }
    }
  }

  function refreshOptions(): void {
    explicitOptionsRefresh.current = true;
    setOptionsRefresh((current) => current + 1);
  }

  function refreshCatalog(): void {
    setCatalogRefresh((current) => current + 1);
  }

  async function openExecutionReport(requestScope: RoleDocumentScope, requestId: string | undefined, receipt: RoleExecutionReceipt | undefined): Promise<void> {
    if (!receipt || !isCurrentExecutionTarget(requestScope, requestId)) return;
    try {
      if (!receipt.requestId) throw new Error("The execution has no recorded request ID for its report. Refresh and retry.");
      const content = await readRoleReport(requestScope, receipt.requestId);
      if (isCurrentExecutionTarget(requestScope, receipt.requestId)) {
        setExecutionErrorState(null);
        setReport(content);
      }
    } catch (error) {
      if (isCurrentExecutionTarget(requestScope, requestId)) setExecutionErrorState({
        scope: requestScope,
        sticky: true,
        message: error instanceof Error ? error.message : "The report could not be opened. Refresh and retry.",
      });
    }
  }

  async function refreshResult(): Promise<void> {
    if (!selectionComplete || busy || optionsLoading) return;
    const selected = selection;
    const requestScope = roleDocumentScope(selected);
    const requestId = isTasklessRole(selected.role)
      ? tasklessActiveRequestsRef.current[selected.role]?.requestId
      : currentScopedExecution?.requestId;
    if (isTasklessRole(selected.role) && !requestId) return;
    setBusyScope(selected);
    try {
      await openExecutionReport(requestScope, requestId, await fetchExecutionResult(requestScope, true, requestId));
    } finally {
      if (sameRoleLaunchSelection(currentSelectionRef.current, selected)) setBusyScope(null);
    }
  }

  async function dispatch(
    action: RoleAction,
    launchSelection: RoleLaunchSelection,
    replayRequestId?: string,
  ): Promise<void> {
    if (!active || busy || !options || !launchSelectionComplete(launchSelection)) return;
    const tasklessRole = isTasklessRole(launchSelection.role);
    const activeTasklessRequest = tasklessRole
      ? tasklessActiveRequestsRef.current[launchSelection.role]
      : undefined;
    const currentStatus = currentScopedExecution?.status.toLowerCase();
    if (action === "start" && tasklessRole) {
      if (replayRequestId) {
        if (activeTasklessRequest?.requestId !== replayRequestId || currentScopedExecution?.canRetry !== true) return;
      } else if (["starting", "unknown"].includes(currentStatus ?? "")) {
        return;
      }
    }
    if (action === "start" && !tasklessRole && currentScopedExecution?.canStart === false && !replayRequestId) return;
    if (action === "revive" && currentScopedExecution?.canRevive !== true) return;
    if (action === "revive" && tasklessRole && (!replayRequestId || currentScopedExecution?.requestId !== replayRequestId)) return;
    const selected = launchSelection;
    const requestScope = roleDocumentScope(selected);
    const requestId = replayRequestId ?? crypto.randomUUID();
    const savedLaunchSelection: RoleLaunchSelection = {
      ...requestScope,
      ...(selected.agentOverride ? { agentOverride: selected.agentOverride } : {}),
    };
    if (tasklessRole && action === "start" && !replayRequestId) {
      updateTasklessActiveRequest(selected.role, {
        requestId,
        pending: true,
        retryPayload: savedLaunchSelection,
      });
    }
    const requestBody = {
      requestId,
      action,
      ...requestScope,
      ...(selected.agentOverride && !(tasklessRole && action === "revive")
        ? { agentOverride: selected.agentOverride }
        : {}),
    };
    setBusyScope(selected);
    setLaunchRequestId(requestId);
    setExecutionErrorState(null);
    try {
      const response = await fetch("/api/role-launch/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      const value = (await response.json()) as RoleExecutionReceipt | { detail?: unknown };
      if (!sameRoleDocumentScope(roleDocumentScope(currentSelectionRef.current), requestScope)) return;
      if (response.status === 409 && "detail" in value && typeof value.detail === "string") {
        setExecutionErrorState({
          scope: requestScope,
          message: "The role launch was not accepted (HTTP 409): " + value.detail,
          sticky: true,
        });
      } else if ("status" in value && typeof value.status === "string") {
        const receipt = value as RoleExecutionReceipt;
        if (tasklessRole && receipt.requestId !== requestId) {
          setExecutionErrorState({ scope: requestScope, message: "The role execution receipt did not match this request." });
          return;
        }
        rememberExecution(requestScope, receipt);
        notifyStarted(response.ok, receipt, requestScope, executionCallback.current);
        if (tasklessRole && receipt.requestId) {
          const pending = isUncertainRoleExecution(receipt);
          updateTasklessActiveRequest(selected.role, pending
            ? {
                requestId: receipt.requestId,
                pending: true,
                ...(receipt.retryPayload ?? tasklessActiveRequestsRef.current[selected.role]?.retryPayload
                  ? { retryPayload: receipt.retryPayload ?? tasklessActiveRequestsRef.current[selected.role]?.retryPayload }
                  : {}),
              }
            : { requestId: receipt.requestId });
        }
        setExecutionErrorState(
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
      } else {
        const status = response.status >= 500 ? "unknown" : "rejected";
        rememberExecution(requestScope, {
            status,
            detail: "HTTP " + response.status,
            ...(tasklessRole
              ? {
                  requestId,
                  retryPayload: savedLaunchSelection,
                  canRetry: status === "unknown",
                }
              : {}),
            canStart: false,
            canRevive: false,
        });
        setExecutionErrorState({
          scope: requestScope,
          message: response.status >= 500
            ? "The role launch returned HTTP " + response.status + "; execution state is unknown. Refresh the AR result before retrying."
            : "The role handover was rejected (HTTP " + response.status + ").",
        });
      }
      await fetchExecutionResult(requestScope, false, tasklessRole ? requestId : undefined);
    } catch {
      if (!sameRoleDocumentScope(roleDocumentScope(currentSelectionRef.current), requestScope)) return;
      rememberExecution(requestScope, {
          status: "unknown",
          detail: "No dispatch receipt was returned.",
          requestId,
          retryPayload: savedLaunchSelection,
          canStart: false,
          canRevive: false,
          canRetry: true,
      });
      setExecutionErrorState({
        scope: requestScope,
        message: "The role launch request did not return. Its state is unknown; refresh the AR result before retrying.",
      });
      await fetchExecutionResult(requestScope, false, tasklessRole ? requestId : undefined);
    } finally {
      if (sameRoleDocumentScope(roleDocumentScope(currentSelectionRef.current), requestScope)) {
        setBusyScope(null);
        setLaunchRequestId(null);
        setOptionsRefresh((current) => current + 1);
      }
    }
  }

  const onSelectionChange = useCallback((next: RoleLaunchSelection) => {
    setSelection(next);
  }, []);
  const roleDefaults = options?.roleDefaults ?? roleDefaultsCache[roleDefaultsCacheKey(currentDocumentScope)] ?? {};
  const agents = agentInventory && agentInventory.catalogOrigin === options?.catalogOrigin
    ? agentInventory.agents
    : options?.agents ?? agentInventory?.agents ?? [];
  const optionsReady = Boolean(options && !optionsLoading);
  const onLaunch = (launchSelection: RoleLaunchSelection) => dispatch("start", launchSelection);
  const onRevive = (launchSelection: RoleLaunchSelection) => dispatch(
    "revive",
    launchSelection,
    isTasklessRole(launchSelection.role) ? currentScopedExecution?.requestId : undefined,
  );
  const onRetry = (launchSelection: RoleLaunchSelection, requestId: string) =>
    dispatch("start", launchSelection, requestId);

  return (
    <>
    <section aria-label="Role chats" className={rolePane} data-testid="role-chats-pane" style={rolePanePresentation(report, Boolean(boundSelection))}>
      <RoleLauncher
        boundRoles={boundRoles}
        progress={busy ? progress : undefined}
        taskDocuments={taskDocuments}
        series={series}
        roleDefaults={roleDefaults}
        agents={agents}
        selection={selection}
        currentScopedExecution={currentScopedExecution}
        failureExecution={failureExecution}
        optionsReady={optionsReady}
        optionsLoading={optionsLoading}
        busy={busy}
        retryRequestId={retryRequestId}
        optionsError={optionsError}
        executionError={executionError}
        onSelectionChange={onSelectionChange}
        onLaunch={onLaunch}
        onRevive={onRevive}
        onRetry={onRetry}
        onRefreshOptions={refreshOptions}
        onRefreshCatalog={refreshCatalog}
        onRefreshResult={refreshResult}
      />
      {unboundChatFrame(Boolean(boundSelection), { active, scope: frameScope, target: frameTarget })}
    </section>
    {report ? <NotesReaderViewer kind="role-report" report={report} onBack={() => setReport(null)} /> : null}
    </>
  );
}
