import { css } from '../../styled-system/css';
import type { TaskEntry } from '../types/projection';
import type { RoleReportContent } from '../data/roleReport';
import { PaseoChatFrame } from './PaseoChatFrame';
import { useFrameChat } from './frameTrayChannel';
import { chatFrameScope, chatFrameTarget, type AgentChatTarget } from './roleChatTarget';
import { RoleLauncher } from './role-launcher/RoleLauncher';
import { useRoleLaunchProgress } from './role-launcher/useRoleLaunchProgress';
import { NotesReaderViewer } from '../panels/notes-reader/NotesReaderViewer';
import { TrayColumnDock } from '../tray/TrayDock';
import { usePublishShownChat } from '../tray/shownChat';
import {
  usesRequestReceipts,
  sameRoleOptionsScope,
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
  /**
   * A chat a surface named by its exact agent (the attention bell, L190-R1-F09). While it stands,
   * the frame is asked for that agent instead of the one the selection's execution names.
   */
  requestedChat?: AgentChatTarget | null;
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
  const savedCatalog = options.savedCatalogState && sameRoleOptionsScope(options.savedCatalogState.scope, optionsScope)
    ? options.savedCatalogState.value : undefined;
  const catalog = resolveLauncherCatalog(display.options, display.optionsLoading, savedCatalog);
  const live = {
    active,
    busy: execution.busy,
    options: display.options,
    currentScopedExecution: execution.currentScopedExecution,
  };
  return {
    frameScope: chatFrameScope(props.requestedChat, JSON.stringify(documentScope)),
    frameTarget: chatFrameTarget(props.requestedChat, execution.currentScopedExecution),
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
 * The chat the Chats page's frame shows (MIK-R188 rule 7, L190-R1-F09): the one its plug-in
 * reports showing, which Paseo's own list may have moved away from the launcher's. A request is
 * not an observation, so the agent the pane asked its frame for is never this chat: until the
 * frame itself names a chat — on a cold column that has not reported once yet, as anywhere else —
 * the pane names none. While a placeholder stands in for the frame (no frame yet, or the
 * embedded chat unavailable) the frame reports nothing, and the pane shows no chat either.
 */
function shownAgent(reported: string | null | undefined) {
  return typeof reported === 'string' ? reported : null;
}

export function RoleChatsView(props: RoleChatsViewProps) {
  const { active, boundSelection, report, onCloseReport } = props;
  const { frameScope, frameTarget, launcher } = useRoleChatsPresentation(props);
  const bound = Boolean(boundSelection);
  const frameChat = useFrameChat('chats');
  // The Chats page's own frame has a message box too: the tray docks above it there (MIK-R188
  // rule 5), and the chat it shows is the default target of an object pick (rule 7).
  const showsChat = active && !bound && !report;
  // The descriptor's availability draws no chat of its own — it answers before any acknowledgement
  // and says only that a frame is mounted. The chat's metadata follows the frame's own report of
  // what it shows, and only that (L190-R1-F09).
  const shown = showsChat ? shownAgent(frameChat) : null;
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
        })}
        {bound ? null : <TrayColumnDock visible={showsChat} page="chats" />}
      </section>
      {report ? (
        <NotesReaderViewer kind="role-report" report={report} onBack={onCloseReport} />
      ) : null}
    </>
  );
}
