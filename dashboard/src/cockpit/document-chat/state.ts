import { useEffect, useMemo, useRef, useState } from "react";
import type { SeriesNode, TaskDocNode } from "../../types/projection";
import type { TaskDocumentRef } from "../../types/terminalCatalog";
import type { RoleDocumentScope, RoleExecutionReceipt } from "../roleLaunchModel";
import { documentChatAgent, documentChatBinding, type DocumentAgent } from "./model";
import { useDocumentAgents } from "./useDocumentAgents";

export interface DocumentChatProps {
  taskDocumentRef?: TaskDocumentRef;
  taskDocuments: TaskDocNode[];
  series: SeriesNode[];
  active: boolean;
}

export function useDocumentChat({ taskDocumentRef, taskDocuments, series, active }: DocumentChatProps) {
  const scope = JSON.stringify(taskDocumentRef ?? null);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const binding = useMemo(() => documentChatBinding(taskDocumentRef, taskDocuments, series), [taskDocumentRef, taskDocuments, series]);
  const [revision, setRevision] = useState(0);
  const [launched, setLaunched] = useState<{ scope: string; selection: RoleDocumentScope; requestId: string } | null>(null);
  const currentLaunch = launchedForScope(launched, scope);
  const readSelection = currentLaunch?.selection ?? binding.selection;
  const answer = useDocumentAgents(readSelection, active && binding.roles.length > 0, revision, currentLaunch?.requestId);
  useEffect(() => setLaunched(null), [scope]);
  useEffect(() => {
    if (currentLaunch && answer?.agents?.length === 0) setLaunched(null);
  }, [currentLaunch, answer]);
  const agents = answer?.agents ?? null;
  const [available, setAvailable] = useState(false);
  const [launcherScope, setLauncherScope] = useState<string | null>(null);
  const agent = displayedAgent(currentLaunch, taskDocumentRef, agents);
  const target = agentTarget(agent);
  const { showLauncher, hideFrame, hasControl } = chatPresentation(scope, available, agents !== null, Boolean(target), launcherScope);
  const onExecution = (receipt: RoleExecutionReceipt, selection: RoleDocumentScope) => {
    const next = launchedRequest(receipt, selection, scope, currentScope.current);
    if (next) { setLaunched(next); setLauncherScope(null); setRevision((current) => current + 1); }
  };
  return { scope, binding, answer, target, available,
    showLauncher: showLauncher && binding.roles.length > 0, hideFrame, hasControl,
    onAvailability: setAvailable, onExecution,
    onToggleLauncher: () => setLauncherScope(launcherScope === scope ? null : scope),
    onRetry: () => setRevision((current) => current + 1),
  };
}

type LaunchedRequest = { scope: string; selection: RoleDocumentScope; requestId: string };

function launchedForScope(launched: LaunchedRequest | null, scope: string) {
  return launched?.scope === scope ? launched : null;
}

function launchedRequest(receipt: RoleExecutionReceipt, selection: RoleDocumentScope, scope: string, currentScope: string): LaunchedRequest | null {
  if (scope !== currentScope || !receipt.execution?.agentId || !receipt.requestId) return null;
  return { scope, selection, requestId: receipt.requestId };
}

function displayedAgent(launch: LaunchedRequest | null, ref: TaskDocumentRef | undefined, agents: DocumentAgent[] | null) {
  return launch ? agents?.[0] ?? null : documentChatAgent(ref, agents ?? []);
}

function agentTarget(agent: DocumentAgent | null) {
  return agent ? { agentId: agent.agentId, workspaceId: agent.workspaceId! } : null;
}

function chatPresentation(scope: string, available: boolean, known: boolean, hasTarget: boolean, launcherScope: string | null) {
  return { showLauncher: available && known && (!hasTarget || launcherScope === scope),
    hideFrame: available && !hasTarget, hasControl: available && hasTarget };
}

