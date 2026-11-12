import {
  launchChoiceProblem,
  launchSelectionComplete,
  roleDocumentScope,
  sameRoleDocumentScope,
  usesRequestReceipts,
  type CatalogDiscoveryState,
  type RoleAgentChoice,
  type RoleDefaults,
  type RoleLaunchSelection,
  type RoleScopedExecution,
} from "../roleLaunchModel";

export interface LaunchReadiness {
  launchSelection: RoleLaunchSelection;
  complete: boolean;
  status: string | undefined;
  liveOccupant: boolean;
  canRetry: boolean;
  retrySelection: RoleLaunchSelection;
  canRetrySelection: boolean;
  choiceProblem: string | null;
  canStart: boolean;
  canRefreshResult: boolean;
}

// A held seat counts as occupied: Start stays disabled while a live occupant runs.
function liveOccupantFor(status: string | undefined): boolean {
  return ["accepted", "held", "unknown", "running", "starting"].includes(status ?? "");
}

// Retry reuses the saved launch request, so it needs the request id and the receipt's leave.
function retryAllowed(
  optionsReady: boolean,
  complete: boolean,
  busy: boolean,
  currentScopedExecution: RoleScopedExecution | undefined,
  retryRequestId: string | undefined,
): boolean {
  return optionsReady && complete && !busy &&
    currentScopedExecution?.canRetry === true && Boolean(retryRequestId);
}

// Start is offered only with no blocking choice problem and no run in flight. A
// request-scoped role restarts while its receipt is settled; a task-bound role also
// waits out a live occupant, an offered retry and an explicit cannot-start.
function startAllowed(
  choiceProblem: string | null,
  selection: RoleLaunchSelection,
  optionsReady: boolean,
  complete: boolean,
  busy: boolean,
  status: string | undefined,
  liveOccupant: boolean,
  canRetry: boolean,
  currentScopedExecution: RoleScopedExecution | undefined,
): boolean {
  if (choiceProblem) return false;
  if (!optionsReady || !complete || busy) return false;
  if (usesRequestReceipts(selection)) return !["starting", "unknown"].includes(status ?? "");
  return !liveOccupant && !canRetry && currentScopedExecution?.canStart !== false;
}

// What the row's action bar may do: the normalized launch selection with its
// completeness, the occupant and retry state, and the three offered actions.
export function resolveLaunchReadiness(args: {
  selection: RoleLaunchSelection;
  roleDefaults: RoleDefaults;
  agents: RoleAgentChoice[];
  catalogDiscovery?: CatalogDiscoveryState;
  currentScopedExecution: RoleScopedExecution | undefined;
  optionsReady: boolean;
  busy: boolean;
  retryRequestId: string | undefined;
}): LaunchReadiness {
  const { selection, roleDefaults, agents, currentScopedExecution, optionsReady, busy, retryRequestId } = args;
  const launchSelection: RoleLaunchSelection = {
    ...roleDocumentScope(selection),
    ...(selection.agentOverride ? { agentOverride: selection.agentOverride } : {}),
  };
  const complete = launchSelectionComplete(launchSelection);
  const status = currentScopedExecution?.status.toLowerCase();
  const liveOccupant = liveOccupantFor(status);
  const canRetry = retryAllowed(optionsReady, complete, busy, currentScopedExecution, retryRequestId);
  const retrySelection = currentScopedExecution?.retryPayload ?? launchSelection;
  const canRetrySelection = sameRoleDocumentScope(
    roleDocumentScope(retrySelection),
    roleDocumentScope(launchSelection),
  );
  const choiceProblem = optionsReady ? launchChoiceProblem(roleDefaults, agents, selection.agentOverride, args.catalogDiscovery) : null;
  const canStart = startAllowed(
    choiceProblem,
    selection,
    optionsReady,
    complete,
    busy,
    status,
    liveOccupant,
    canRetry,
    currentScopedExecution,
  );
  // Every refresh re-reads the agent, so Result is offered for any execution, closed ones included.
  const canRefreshResult = complete && Boolean(status);
  return {
    launchSelection,
    complete,
    status,
    liveOccupant,
    canRetry,
    retrySelection,
    canRetrySelection,
    choiceProblem,
    canStart,
    canRefreshResult,
  };
}
