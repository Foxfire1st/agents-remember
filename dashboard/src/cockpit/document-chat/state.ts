import { useEffect, useMemo, useRef, useState } from "react";
import type { SeriesNode, TaskDocNode, TaskDocumentRef } from "../../types/projection";
import { canonicalLauncherRole, type RoleDocumentScope, type RoleExecutionReceipt } from "../roleLaunchModel";
import { useKnownChats, useSelectionAgents } from "./knownChats";
import { bindingInTree, documentBindings, documentChatAgent, type DocumentAgent } from "./model";
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
  const tree = useMemo(() => documentBindings(taskDocuments, series), [taskDocuments, series]);
  const binding = useMemo(() => bindingInTree(taskDocumentRef, tree), [taskDocumentRef, tree]);
  const [revision, setRevision] = useState(0);
  const [launched, setLaunched] = useState<{ scope: string; selection: RoleDocumentScope; requestId: string } | null>(null);
  const currentLaunch = launchedForScope(launched, scope);
  const readSelection = currentLaunch?.selection ?? binding.selection;
  const reading = active && binding.roles.length > 0;
  const answer = useDocumentAgents(readSelection, reading, revision, currentLaunch?.requestId);
  useEffect(() => setLaunched(null), [scope]);
  useEffect(() => {
    if (currentLaunch && answer?.agents?.length === 0) setLaunched(null);
  }, [currentLaunch, answer]);
  // A selection the dashboard already knows switches with the click.
  const known = useKnownChats(tree, taskDocumentRef, active);
  const agents = useSelectionAgents(known, binding.selection, answer?.agents, reading && !currentLaunch);
  const [available, setAvailable] = useState(false);
  const [launcherScope, setLauncherScope] = useState<string | null>(null);
  const agent = displayedAgent(currentLaunch, taskDocumentRef, agents);
  const target = agentTarget(agent);
  const presentation = chatPresentation(scope, available, { agents, roles: binding.roles.length }, Boolean(target), launcherScope);
  const onExecution = (receipt: RoleExecutionReceipt, selection: RoleDocumentScope) => {
    // The explicit Investigator request stays in the launcher/host tabs; the document's primary
    // conversation remains its Manager or Orchestrator even after a successful launch (R98).
    if (canonicalLauncherRole(selection.role) === "investigator" &&
        ["manager", "orchestrator"].includes(binding.selection.role)) {
      setRevision((current) => current + 1);
      return;
    }
    const next = launchedRequest(receipt, selection, scope, currentScope.current);
    if (next) { setLaunched(next); setLauncherScope(null); setRevision((current) => current + 1); }
  };
  return { scope, binding, answer, target, available, ...presentation,
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

/**
 * The frame is hidden only where the selection is known to have no chat. While
 * the dashboard still asks which chat the document has, the frame keeps what it shows, out of
 * reach. A document no role can start on has no chat and nothing to ask.
 */
function chatPresentation(scope: string, available: boolean, chat: { agents: DocumentAgent[] | null; roles: number }, hasTarget: boolean, launcherScope: string | null) {
  const startable = chat.roles > 0;
  const known = chat.agents !== null || !startable;
  return { showLauncher: available && startable && known && (!hasTarget || launcherScope === scope),
    hideFrame: available && known && !hasTarget, hasControl: available && hasTarget, asking: available && !known };
}

