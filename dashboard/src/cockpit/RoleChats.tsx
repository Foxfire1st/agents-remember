import { useCallback, useEffect, useRef, useState } from 'react';
import type { TaskEntry } from '../types/projection';

import type { RoleReportContent } from '../data/roleReport';
import { useRoleChatControls } from './roleChatControls';
import { useRoleChatScopes } from './roleChatScopes';
import { useLaunchReread, useRoleChatOptionsLoader } from './roleChatOptionsLoader';
import { resolveSelectionComplete } from './roleChatExecutionView';
import { seatChatSelection, selectionEndsNamedChat, type AgentChatTarget } from './roleChatTarget';
import { RoleChatsView } from './RoleChatsView';
import type {
  LauncherRole,
  RoleDocumentScope,
  RoleExecutionReceipt,
  RoleLaunchSelection,
} from './roleLaunchModel';

/**
 * The pane's launcher selection, and the exact chat a surface named (L190-R1-F09). A bell row's
 * seat becomes the selection while its chat is the pane's; the developer's own selection takes
 * both back, because the launcher then shows something the named chat no longer is.
 */
function useChatSelection(
  boundSelection: RoleLaunchSelection | undefined,
  openChat: AgentChatTarget | null | undefined,
): {
  selection: RoleLaunchSelection;
  requestedChat: AgentChatTarget | null;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
} {
  const [selection, setSelection] = useState<RoleLaunchSelection>(
    () => boundSelection ?? seatChatSelection(openChat ?? null) ?? { role: 'architect' },
  );
  const [requestedChat, setRequestedChat] = useState<AgentChatTarget | null>(openChat ?? null);
  useEffect(() => {
    if (!openChat) return;
    setRequestedChat(openChat);
    const seat = seatChatSelection(openChat);
    if (seat) setSelection(seat);
  }, [openChat]);
  const onSelectionChange = useCallback((next: RoleLaunchSelection) => {
    setRequestedChat((current) => (selectionEndsNamedChat(next, current) ? null : current));
    setSelection(next);
  }, []);
  return { selection, requestedChat, onSelectionChange };
}

export function RoleChatsPane({
  active,
  tasks,
  boundSelection,
  boundRoles,
  onExecution,
  openChat,
}: {
  active: boolean;
  tasks: TaskEntry[];
  boundSelection?: RoleLaunchSelection;
  boundRoles?: LauncherRole[];
  onExecution?: (receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void;
  /**
   * A chat the shell was asked for by its exact agent (the attention bell, L190-R1-F09). The pane
   * shows that agent at once, and the seat's role becomes the launcher's selection so the bar is
   * the seat's own. Its document cannot become the launcher's (a leaf is resolvable only through
   * its sprint and master, which the row does not carry, and the launcher prunes what it cannot
   * resolve), so the seat's document is the frame's scope instead — see `chatFrameScope`.
   */
  openChat?: AgentChatTarget | null;
}) {
  const [report, setReport] = useState<RoleReportContent | null>(null);
  const { selection, requestedChat, onSelectionChange } = useChatSelection(
    boundSelection,
    openChat,
  );
  const [optionsRefresh, setOptionsRefresh] = useState(0);
  const [catalogRefresh, setCatalogRefresh] = useState(0);
  const requestOptionsRefresh = useCallback(() => setOptionsRefresh((current) => current + 1), []);
  const bumpCatalogRefresh = useCallback(() => setCatalogRefresh((current) => current + 1), []);
  const currentSelectionRef = useRef(selection);
  currentSelectionRef.current = selection;
  const { documentScope, optionsScope } = useRoleChatScopes(selection);
  const selectionComplete = resolveSelectionComplete(selection);
  const { waitForLaunch } = useLaunchReread(requestOptionsRefresh);
  const controls = useRoleChatControls({
    waitForLaunch,
    requestOptionsRefresh,
    currentSelectionRef,
    onExecution,
    setReport,
  });
  const loader = useRoleChatOptionsLoader({
    active,
    boundSelection,
    selectionComplete,
    documentScope,
    optionsScope,
    optionsRefresh,
    catalogRefresh,
    bumpOptionsRefresh: requestOptionsRefresh,
    bumpCatalogRefresh,
    currentSelectionRef,
    activeRequestsRef: controls.activeRequestsRef,
    waitForLaunch,
    setExecutionState: controls.setExecutionState,
    setExecutionErrorState: controls.setExecutionErrorState,
    updateActiveRequest: controls.updateActiveRequest,
    rememberExecution: controls.rememberExecution,
    rememberFailureExecution: controls.rememberFailureExecution,
    fetchExecutionResult: controls.fetchExecutionResult,
  });
  const onCloseReport = useCallback(() => setReport(null), []);
  return (
    <RoleChatsView
      active={active}
      tasks={tasks}
      boundSelection={boundSelection}
      boundRoles={boundRoles}
      requestedChat={requestedChat}
      selection={selection}
      onSelectionChange={onSelectionChange}
      documentScope={documentScope}
      optionsScope={optionsScope}
      selectionComplete={selectionComplete}
      controls={controls}
      options={loader}
      activeRequests={controls.activeRequestsRef.current}
      report={report}
      onCloseReport={onCloseReport}
      onRefreshOptions={loader.requestExplicitOptionsRefresh}
      onRefreshCatalog={loader.requestCatalogRefresh}
    />
  );
}
