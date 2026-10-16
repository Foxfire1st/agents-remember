import { useCallback, useRef, useState } from 'react';
import type { TaskEntry } from '../types/projection';

import type { RoleReportContent } from '../data/roleReport';
import { useRoleChatControls } from './roleChatControls';
import { useRoleChatScopes } from './roleChatScopes';
import { useLaunchReread, useRoleChatOptionsLoader } from './roleChatOptionsLoader';
import { resolveSelectionComplete } from './roleChatExecutionView';
import { RoleChatsView } from './RoleChatsView';
import type {
  LauncherRole,
  RoleDocumentScope,
  RoleExecutionReceipt,
  RoleLaunchSelection,
} from './roleLaunchModel';

export function RoleChatsPane({
  active,
  tasks,
  boundSelection,
  boundRoles,
  onExecution,
}: {
  active: boolean;
  tasks: TaskEntry[];
  boundSelection?: RoleLaunchSelection;
  boundRoles?: LauncherRole[];
  onExecution?: (receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void;
}) {
  const [report, setReport] = useState<RoleReportContent | null>(null);
  const [selection, setSelection] = useState<RoleLaunchSelection>(
    () => boundSelection ?? { role: 'architect' },
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
  const onSelectionChange = useCallback((next: RoleLaunchSelection) => {
    setSelection(next);
  }, []);
  const onCloseReport = useCallback(() => setReport(null), []);
  return (
    <RoleChatsView
      active={active}
      tasks={tasks}
      boundSelection={boundSelection}
      boundRoles={boundRoles}
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
