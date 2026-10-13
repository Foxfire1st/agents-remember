import { css } from "../../styled-system/css";
import type { SeriesNode, TaskDocNode } from "../types/projection";
import type { RoleReportContent } from "../data/roleReport";
import { PaseoChatFrame } from "./PaseoChatFrame";
import { paseoAgentTarget } from "./paseoFrameModel";
import { RoleLauncher } from "./role-launcher/RoleLauncher";
import { useRoleLaunchProgress } from "./role-launcher/useRoleLaunchProgress";
import { NotesReaderViewer } from "../panels/notes-reader/NotesReaderViewer";
import {
  roleDefaultsCacheKey,
  usesRequestReceipts,
  type LauncherRole,
  type RoleDocumentScope,
  type RoleLaunchSelection,
  type RoleOptionsScope,
  type RoleTasklessActiveRequest,
} from "./roleLaunchModel";
import type { useRoleChatControls } from "./roleChatControls";
import type { OptionsFlowState } from "./roleChatOptionsLoader";
import {
  resolveBusy,
  resolveExecutionErrorText,
  resolveExecutionReceipt,
  resolveFailureExecution,
  resolveLauncherCatalog,
  resolveOptionsDisplay,
  resolveOptionsErrorText,
  resolvePendingExecution,
  resolveRequestExecution,
  resolveRetryRequestId,
  resolveScopedExecution,
} from "./roleChatExecutionView";

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

function rolePanePresentation(report: RoleReportContent | null, bound: boolean) {
  return report ? { display: "none" } : bound ? { flex: "0 0 auto" } : undefined;
}

function unboundChatFrame(bound: boolean, props: Parameters<typeof PaseoChatFrame>[0]) {
  return bound ? null : <PaseoChatFrame {...props} />;
}

type Controls = ReturnType<typeof useRoleChatControls>;

export interface RoleChatsViewProps {
  active: boolean;
  taskDocuments: TaskDocNode[];
  series: SeriesNode[];
  boundSelection?: RoleLaunchSelection;
  boundRoles?: LauncherRole[];
  selection: RoleLaunchSelection;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
  documentScope: RoleDocumentScope;
  optionsScope: RoleOptionsScope;
  selectionComplete: boolean;
  controls: Controls;
  options: OptionsFlowState;
  activeRequests: Record<string, RoleTasklessActiveRequest | undefined>;
  report: RoleReportContent | null;
  onCloseReport: () => void;
  onRefreshOptions: () => void;
  onRefreshCatalog: () => void;
}

// The chat pane's rendering: the launcher row with the executions behind the
// selection, the chat frame, and the open report. Pure rendering over the pane's
// state — every derivation below names what the row shows.
export function RoleChatsView(props: RoleChatsViewProps) {
  const {
    active, taskDocuments, series, boundSelection, boundRoles, selection, onSelectionChange,
    documentScope, optionsScope, selectionComplete, controls, options, activeRequests,
    report, onCloseReport, onRefreshOptions, onRefreshCatalog,
  } = props;
  const { optionsState, optionsLoadingScope, optionsErrorState, roleDefaultsCache, agentInventory } = options;
  const { options: optionsValue, optionsLoading } = resolveOptionsDisplay(optionsState, optionsScope, optionsLoadingScope);
  const busy = resolveBusy(controls.busyScope, documentScope);
  const progress = useRoleLaunchProgress(controls.launchRequestId);
  const optionsError = resolveOptionsErrorText(optionsErrorState, optionsScope);
  const { activeRequest, savedRequestExecution } = resolveRequestExecution(selection, optionsValue, activeRequests);
  const executionReceipt = resolveExecutionReceipt(controls.executionState, documentScope, selection, activeRequest);
  const pendingRequestExecution = resolvePendingExecution(activeRequest, executionReceipt, savedRequestExecution, busy);
  const currentScopedExecution = resolveScopedExecution(
    selection, optionsValue, optionsLoading, executionReceipt, savedRequestExecution, activeRequest, pendingRequestExecution,
  );
  const lastFailureExecution = controls.failureExecutionRef.current;
  const failureExecution = resolveFailureExecution(
    lastFailureExecution,
    controls.isCurrentExecutionTarget,
    currentScopedExecution,
  );
  const retryRequestId = resolveRetryRequestId(selection, currentScopedExecution);
  const { roleDefaults, agents, optionsReady } = resolveLauncherCatalog(
    optionsValue, roleDefaultsCache, documentScope, agentInventory, optionsLoading, roleDefaultsCacheKey,
  );
  const executionError = resolveExecutionErrorText(controls.executionErrorState, documentScope);
  const frameScope = JSON.stringify(documentScope);
  const frameTarget = paseoAgentTarget(currentScopedExecution);
  const live = { active, busy, options: optionsValue, currentScopedExecution };
  const onLaunch = (launchSelection: RoleLaunchSelection) => controls.dispatch(live, "start", launchSelection);
  const onRevive = (launchSelection: RoleLaunchSelection) => controls.dispatch(
    live,
    "revive",
    launchSelection,
    usesRequestReceipts(launchSelection) ? currentScopedExecution?.requestId : undefined,
  );
  const onRetry = (launchSelection: RoleLaunchSelection, requestId: string) =>
    controls.dispatch(live, "start", launchSelection, requestId);
  const onRefreshResult = () => controls.refreshResult(selection, selectionComplete, busy, optionsLoading, currentScopedExecution);
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
        onRefreshOptions={onRefreshOptions}
        onRefreshCatalog={onRefreshCatalog}
        onRefreshResult={onRefreshResult}
      />
      {unboundChatFrame(Boolean(boundSelection), { active, scope: frameScope, target: frameTarget })}
    </section>
    {report ? <NotesReaderViewer kind="role-report" report={report} onBack={onCloseReport} /> : null}
    </>
  );
}
