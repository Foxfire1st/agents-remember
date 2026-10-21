import { useState } from 'react';
import { css } from '../../styled-system/css';
import type { TaskEntry } from '../types/projection';
import type { RoleReportContent } from '../data/roleReport';
import { PaseoChatFrame } from './PaseoChatFrame';
import { useFrameChat } from './frameTrayChannel';
import { paseoAgentTarget, type PaseoAgentTarget } from './paseoFrameModel';
import { RoleLauncher } from './role-launcher/RoleLauncher';
import { useRoleLaunchProgress } from './role-launcher/useRoleLaunchProgress';
import { NotesReaderViewer } from '../panels/notes-reader/NotesReaderViewer';
import { TrayColumnDock } from '../tray/TrayDock';
import { usePublishShownChat } from '../tray/shownChat';
import {
  roleDefaultsCacheKey,
  usesRequestReceipts,
  type LauncherRole,
  type RoleDocumentScope,
  type RoleLaunchSelection,
  type RoleOptionsScope,
  type RoleTasklessActiveRequest,
} from './roleLaunchModel';
import type { useRoleChatControls } from './roleChatControls';
import type { OptionsFlowState } from './roleChatOptionsLoader';
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
} from './roleChatExecutionView';

const rolePane = css({
  display: 'flex',
  flex: '1',
  flexDirection: 'column',
  minHeight: '0',
  minWidth: '0',
  overflow: 'hidden',
  border: '1px solid var(--grid)',
  background: 'var(--bg-panel)',
});

function rolePanePresentation(report: RoleReportContent | null, bound: boolean) {
  return report ? { display: 'none' } : bound ? { flex: '0 0 auto' } : undefined;
}

function unboundChatFrame(bound: boolean, props: Parameters<typeof PaseoChatFrame>[0]) {
  return bound ? null : <PaseoChatFrame {...props} />;
}

type Controls = ReturnType<typeof useRoleChatControls>;

export interface RoleChatsViewProps {
  active: boolean;
  tasks: TaskEntry[];
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
function chatExecution(
  props: RoleChatsViewProps,
  display: ReturnType<typeof resolveOptionsDisplay>,
) {
  const { controls, documentScope, selection, activeRequests } = props;
  const { options: optionsValue, optionsLoading } = display;
  const busy = resolveBusy(controls.busyScope, documentScope);
  const { activeRequest, savedRequestExecution } = resolveRequestExecution(
    selection,
    optionsValue,
    activeRequests,
  );
  const executionReceipt = resolveExecutionReceipt(
    controls.executionState,
    documentScope,
    selection,
    activeRequest,
  );
  const pendingRequestExecution = resolvePendingExecution(
    activeRequest,
    executionReceipt,
    savedRequestExecution,
    busy,
  );
  const currentScopedExecution = resolveScopedExecution(
    selection,
    optionsValue,
    optionsLoading,
    executionReceipt,
    savedRequestExecution,
    activeRequest,
    pendingRequestExecution,
  );
  return {
    busy,
    currentScopedExecution,
    failureExecution: resolveFailureExecution(
      controls.failureExecutionRef.current,
      controls.isCurrentExecutionTarget,
      currentScopedExecution,
    ),
    retryRequestId: resolveRetryRequestId(selection, currentScopedExecution),
  };
}

function launchActions(
  props: RoleChatsViewProps,
  live: Parameters<Controls['dispatch']>[0],
  optionsLoading: boolean,
) {
  const { controls, selection, selectionComplete } = props;
  return {
    onLaunch: (next: RoleLaunchSelection) => controls.dispatch(live, 'start', next),
    onRevive: (next: RoleLaunchSelection) =>
      controls.dispatch(
        live,
        'revive',
        next,
        usesRequestReceipts(next) ? live.currentScopedExecution?.requestId : undefined,
      ),
    onRetry: (next: RoleLaunchSelection, requestId: string) =>
      controls.dispatch(live, 'start', next, requestId),
    onRefreshResult: () =>
      controls.refreshResult(
        selection,
        selectionComplete,
        live.busy,
        optionsLoading,
        live.currentScopedExecution,
      ),
  };
}

function useRoleChatsPresentation(props: RoleChatsViewProps) {
  const {
    options,
    optionsScope,
    controls,
    documentScope,
    active,
    tasks,
    boundRoles,
    selection,
    onSelectionChange,
  } = props;
  const display = resolveOptionsDisplay(
    options.optionsState,
    optionsScope,
    options.optionsLoadingScope,
  );
  const execution = chatExecution(props, display);
  const progress = useRoleLaunchProgress(controls.launchRequestId);
  const catalog = resolveLauncherCatalog(
    display.options,
    options.roleDefaultsCache,
    documentScope,
    options.agentInventory,
    display.optionsLoading,
    roleDefaultsCacheKey,
  );
  const live = {
    active,
    busy: execution.busy,
    options: display.options,
    currentScopedExecution: execution.currentScopedExecution,
  };
  return {
    frameScope: JSON.stringify(documentScope),
    frameTarget: paseoAgentTarget(execution.currentScopedExecution),
    launcher: {
      tasks,
      boundRoles,
      selection,
      onSelectionChange,
      ...catalog,
      ...execution,
      ...launchActions(props, live, display.optionsLoading),
      progress: execution.busy ? progress : undefined,
      optionsLoading: display.optionsLoading,
      optionsError: resolveOptionsErrorText(options.optionsErrorState, optionsScope),
      executionError: resolveExecutionErrorText(controls.executionErrorState, documentScope),
      onRefreshOptions: props.onRefreshOptions,
      onRefreshCatalog: props.onRefreshCatalog,
    },
  };
}

/**
 * The chat the Chats page's frame shows (MIK-R188 rule 7): the one its plug-in names, which
 * Paseo's own list may have moved away from the launcher's; until the plug-in names one after a
 * load, the launcher's. While a placeholder stands in for the frame (no frame yet, or the
 * embedded chat unavailable) no chat is shown.
 */
function shownAgent(drawn: boolean, frameChat: string | null | undefined, frameTarget: PaseoAgentTarget | null) {
  if (!drawn) return null;
  return frameChat === undefined ? (frameTarget?.agentId ?? null) : frameChat;
}

export function RoleChatsView(props: RoleChatsViewProps) {
  const { active, boundSelection, report, onCloseReport } = props;
  const { frameScope, frameTarget, launcher } = useRoleChatsPresentation(props);
  const bound = Boolean(boundSelection);
  const [drawn, setDrawn] = useState(false);
  const frameChat = useFrameChat('chats');
  // The Chats page's own frame has a message box too: the tray docks above it there (MIK-R188
  // rule 5), and the chat it shows is the default target of an object pick (rule 7).
  const showsChat = active && !bound && !report;
  const shown = showsChat ? shownAgent(drawn, frameChat, frameTarget) : null;
  usePublishShownChat(shown ? { agentId: shown } : null);
  return (
    <>
      <section
        aria-label="Role chats"
        className={rolePane}
        data-testid="role-chats-pane"
        style={rolePanePresentation(report, Boolean(boundSelection))}
      >
        <RoleLauncher {...launcher} />
        {unboundChatFrame(bound, {
          active,
          scope: frameScope,
          target: frameTarget,
          onAvailability: setDrawn,
        })}
        {bound ? null : <TrayColumnDock visible={showsChat} page="chats" />}
      </section>
      {report ? (
        <NotesReaderViewer kind="role-report" report={report} onBack={onCloseReport} />
      ) : null}
    </>
  );
}
