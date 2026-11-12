import {
  launchSelectionComplete,
  roleDocumentScope,
  roleRequestKey,
  sameRoleDocumentScope,
  sameRoleOptionsScope,
  usesRequestReceipts,
  type RoleDefaults,
  type RoleDocumentScope,
  type RoleExecutionReceipt,
  type RoleLaunchSelection,
  type RoleLauncherOptions,
  type RoleOptionsScope,
  type RoleScopedExecution,
  type SavedRoleCatalog,
  type RoleTasklessActiveRequest,
} from "./roleLaunchModel";
import type { ExecutionErrorState } from "./roleChatResult";

// A pending request displays only its disk snapshot, never a previous fresh answer.
// Settled options must match the selected scope.
export function resolveOptionsDisplay(
  optionsState: { scope: RoleOptionsScope; value: RoleLauncherOptions } | null,
  currentOptionsScope: RoleOptionsScope,
  optionsLoadingScope: RoleOptionsScope | null,
): { options: RoleLauncherOptions | null; optionsLoading: boolean } {
  const optionsLoading = Boolean(
    optionsLoadingScope && sameRoleOptionsScope(optionsLoadingScope, currentOptionsScope),
  );
  const options = !optionsLoading && optionsState &&
    sameRoleOptionsScope(optionsState.scope, currentOptionsScope)
    ? optionsState.value : null;
  return { options, optionsLoading };
}

// The run's busy mark follows the busy selection's scope.
export function resolveBusy(
  busyScope: RoleLaunchSelection | null,
  currentDocumentScope: RoleDocumentScope,
): boolean {
  return Boolean(busyScope && sameRoleDocumentScope(roleDocumentScope(busyScope), currentDocumentScope));
}

// The options error behind the selected scope, if any.
export function resolveOptionsErrorText(
  optionsErrorState: { scope: RoleOptionsScope; message: string } | null,
  currentOptionsScope: RoleOptionsScope,
): string | null {
  return optionsErrorState && sameRoleOptionsScope(optionsErrorState.scope, currentOptionsScope)
    ? optionsErrorState.message
    : null;
}

// The saved request behind the selection with the execution the options answer holds
// for it, if any.
export function resolveRequestExecution(
  selection: RoleLaunchSelection,
  options: RoleLauncherOptions | null,
  activeRequests: Record<string, RoleTasklessActiveRequest | undefined>,
): {
  activeRequest: RoleTasklessActiveRequest | undefined;
  savedRequestExecution: RoleExecutionReceipt | undefined;
} {
  const activeRequest = usesRequestReceipts(selection)
    ? activeRequests[roleRequestKey(selection)]
    : undefined;
  const savedRequestExecution =
    usesRequestReceipts(selection) && activeRequest
      ? options?.executions?.find((execution) => execution.requestId === activeRequest.requestId)
      : undefined;
  return { activeRequest, savedRequestExecution };
}

// The stored receipt while it still matches the selection and its request.
export function resolveExecutionReceipt(
  executionState: { scope: RoleDocumentScope; value: RoleExecutionReceipt } | null,
  currentDocumentScope: RoleDocumentScope,
  selection: RoleLaunchSelection,
  activeRequest: RoleTasklessActiveRequest | undefined,
): RoleExecutionReceipt | null {
  return executionState && sameRoleDocumentScope(executionState.scope, currentDocumentScope) &&
      (!usesRequestReceipts(selection) || executionState.value.requestId === activeRequest?.requestId)
    ? executionState.value
    : null;
}

// A saved request with no receipt yet reads as a pending execution: starting while
// the run is busy, else unknown with a retry where the request kept its selection.
export function resolvePendingExecution(
  activeRequest: RoleTasklessActiveRequest | undefined,
  executionReceipt: RoleExecutionReceipt | null,
  savedRequestExecution: RoleExecutionReceipt | undefined,
  busy: boolean,
): RoleScopedExecution | undefined {
  if (!activeRequest?.pending || executionReceipt || savedRequestExecution) return undefined;
  return {
    status: busy ? "starting" : "unknown",
    detail: busy ? undefined : "The saved request has not returned a receipt yet.",
    requestId: activeRequest.requestId,
    retryPayload: activeRequest.retryPayload,
    canStart: false,
    canRevive: false,
    canRetry: !busy && Boolean(activeRequest.retryPayload),
  };
}

// The request-scoped execution: the pending request, or the stored or saved receipt
// with its retry selection.
function resolveRequestScopedExecution(
  selectedRequestExecution: RoleScopedExecution | null | undefined,
  activeRequest: RoleTasklessActiveRequest | undefined,
  pendingRequestExecution: RoleScopedExecution | undefined,
): RoleScopedExecution | undefined {
  if (!selectedRequestExecution) return pendingRequestExecution;
  return {
    ...selectedRequestExecution,
    ...(selectedRequestExecution.retryPayload ?? activeRequest?.retryPayload
      ? { retryPayload: selectedRequestExecution.retryPayload ?? activeRequest?.retryPayload }
      : {}),
  };
}

// The execution the row shows: the pending request, the stored or saved receipt with
// its retry selection, or the task-bound receipt from the options answer.
export function resolveScopedExecution(
  selection: RoleLaunchSelection,
  options: RoleLauncherOptions | null,
  optionsLoading: boolean,
  executionReceipt: RoleExecutionReceipt | null,
  savedRequestExecution: RoleExecutionReceipt | undefined,
  activeRequest: RoleTasklessActiveRequest | undefined,
  pendingRequestExecution: RoleScopedExecution | undefined,
): RoleScopedExecution | undefined {
  if (usesRequestReceipts(selection)) {
    if (optionsLoading) return undefined;
    return resolveRequestScopedExecution(executionReceipt ?? savedRequestExecution, activeRequest, pendingRequestExecution);
  }
  if (optionsLoading) return undefined;
  return executionReceipt ?? options?.execution ?? undefined;
}

// The failure line keeps the last scoped answer: controls may clear their receipt on
// refresh, while a failure still names the run that failed.
export function resolveFailureExecution(
  lastFailureExecution: { scope: RoleDocumentScope; value: RoleExecutionReceipt } | null,
  isCurrentExecutionTarget: (scope: RoleDocumentScope, requestId?: string) => boolean,
  currentScopedExecution: RoleScopedExecution | undefined,
): RoleScopedExecution | undefined {
  return lastFailureExecution &&
      isCurrentExecutionTarget(lastFailureExecution.scope, lastFailureExecution.value.requestId)
    ? lastFailureExecution.value
    : currentScopedExecution;
}

// The retry reuses the saved request's id while the receipt leaves a retry.
export function resolveRetryRequestId(
  selection: RoleLaunchSelection,
  currentScopedExecution: RoleScopedExecution | undefined,
): string | undefined {
  return currentScopedExecution?.canRetry &&
      (!usesRequestReceipts(selection) || Boolean(currentScopedExecution.retryPayload))
    ? currentScopedExecution.requestId
    : undefined;
}

// The answer on show with the role defaults beside it: the settled answer with its own, else
// Paseo's saved answer with the defaults this page last settled for the scope, else nothing.
function shownCatalog(
  settledOptions: RoleLauncherOptions | null,
  savedCatalog: SavedRoleCatalog | undefined,
  rememberedDefaults: RoleDefaults | undefined,
): { catalog?: SavedRoleCatalog; roleDefaults: RoleDefaults; savedShown: boolean } {
  if (settledOptions) return { catalog: settledOptions, roleDefaults: settledOptions.roleDefaults, savedShown: false };
  if (savedCatalog) return { catalog: savedCatalog, roleDefaults: rememberedDefaults ?? {}, savedShown: true };
  return { roleDefaults: {}, savedShown: false };
}

// Catalog choices are one server response, never a merge with another response. While a fresh
// answer is on its way, Paseo's saved answer is offered as it is, whole and selectable (MIK-R215
// rules 4 and 5); with no defaults known for the scope none are shown, and a start sends the
// role's own unchecked.
export function resolveLauncherCatalog(
  options: RoleLauncherOptions | null,
  optionsLoading: boolean,
  savedCatalog?: SavedRoleCatalog,
  rememberedDefaults?: RoleDefaults,
) {
  const shown = shownCatalog(optionsLoading ? null : options, savedCatalog, rememberedDefaults);
  return {
    roleDefaults: shown.roleDefaults,
    agents: shown.catalog?.agents ?? [],
    catalogDiscovery: shown.catalog?.catalogDiscovery,
    catalogOrigin: shown.catalog?.catalogOrigin,
    optionsReady: Boolean(shown.catalog),
    savedShown: shown.savedShown,
  };
}

// The execution error behind the selected scope, if any.
export function resolveExecutionErrorText(
  executionErrorState: ExecutionErrorState | null,
  currentDocumentScope: RoleDocumentScope,
): string | null {
  return executionErrorState && sameRoleDocumentScope(executionErrorState.scope, currentDocumentScope)
    ? executionErrorState.message
    : null;
}

export function resolveSelectionComplete(selection: RoleLaunchSelection): boolean {
  return launchSelectionComplete(selection);
}
