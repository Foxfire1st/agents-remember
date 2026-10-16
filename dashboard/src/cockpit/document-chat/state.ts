import { useEffect, useMemo, useRef, useState } from 'react';
import type { TaskEntry, TaskDocumentRef } from '../../types/projection';
import {
  canonicalLauncherRole,
  type RoleDocumentScope,
  type RoleExecutionReceipt,
} from '../roleLaunchModel';
import { useKnownChats, useSelectionAgents } from './knownChats';
import { bindingInTree, documentBindings, documentChatAgent, type DocumentAgent } from './model';
import { useDocumentAgents } from './useDocumentAgents';
import { useWarmupStatus } from './warmup';

export interface DocumentChatProps {
  taskDocumentRef?: TaskDocumentRef;
  tasks: TaskEntry[];
  active: boolean;
}

export function useDocumentChat({ taskDocumentRef, tasks, active }: DocumentChatProps) {
  const scope = JSON.stringify(taskDocumentRef ?? null);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const tree = useMemo(() => documentBindings(tasks), [tasks]);
  const binding = useMemo(() => bindingInTree(taskDocumentRef, tree), [taskDocumentRef, tree]);
  const [revision, setRevision] = useState(0);
  const [launched, setLaunched] = useState<{
    scope: string;
    selection: RoleDocumentScope;
    requestId: string;
  } | null>(null);
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
  const agents = useSelectionAgents(
    known,
    binding.selection,
    answer?.agents,
    reading && !currentLaunch,
  );
  const [available, setAvailable] = useState(false);
  const [launcherScope, setLauncherScope] = useState<string | null>(null);
  const agent = displayedAgent(currentLaunch, taskDocumentRef, agents);
  const target = agentTarget(agent);
  // MIK-R145 rule 6: while the host-start loading runs, its one line and whether
  // the shown agent is still pending, so the pane can say it is being loaded now.
  const warmupPicture = useWarmupPicture(active, target);
  const presentation = chatPresentation(
    scope,
    available,
    { agents, roles: binding.roles.length },
    Boolean(target),
    launcherScope,
  );
  const onExecution = (receipt: RoleExecutionReceipt, selection: RoleDocumentScope) => {
    // The explicit Investigator request stays in the launcher/host tabs; the document's primary
    // conversation remains its Manager or Orchestrator even after a successful launch (R98).
    if (
      canonicalLauncherRole(selection.role) === 'investigator' &&
      ['manager', 'orchestrator'].includes(binding.selection.role)
    ) {
      setRevision((current) => current + 1);
      return;
    }
    const next = launchedRequest(receipt, selection, scope, currentScope.current);
    if (next) {
      setLaunched(next);
      setLauncherScope(null);
      setRevision((current) => current + 1);
    }
  };
  return {
    scope,
    binding,
    answer,
    target,
    available,
    ...presentation,
    onAvailability: setAvailable,
    onExecution,
    onToggleLauncher: () => setLauncherScope(launcherScope === scope ? null : scope),
    onRetry: () => setRevision((current) => current + 1),
    ...warmupPicture,
  };
}

/** The shown agent's warmup state for the pane (L81-R1-F28): pending, in flight,
 *  failed and not-loaded-for-memory each render their own truth with the run's
 *  reason. A loaded agent renders nothing; an unknown one falls back to the
 *  older pending/in-flight lists, then to nothing. */
export interface WarmupAgentPicture {
  agentId: string;
  state: string;
  reason: string | null;
}

export function warmupAgentState(
  warmup:
    | {
        pending: string[];
        inFlight: string[];
        agents: Record<string, { state: string; reason: string | null }>;
      }
    | null
    | undefined,
  agentId: string,
): WarmupAgentPicture | null {
  const row = warmup?.agents[agentId];
  if (row && row.state !== 'loaded') return { agentId, state: row.state, reason: row.reason };
  if (row) return null;
  if (warmup?.pending.includes(agentId)) return { agentId, state: 'pending', reason: null };
  if (warmup?.inFlight.includes(agentId)) return { agentId, state: 'loading', reason: null };
  return null;
}

type LaunchedRequest = { scope: string; selection: RoleDocumentScope; requestId: string };

function useWarmupPicture(active: boolean, target: ReturnType<typeof agentTarget>) {
  const warmup = useWarmupStatus(active);
  return {
    warmupLine: warmup?.line ?? null,
    warmupAgent: target ? warmupAgentState(warmup, target.agentId) : null,
  };
}

function launchedForScope(launched: LaunchedRequest | null, scope: string) {
  return launched?.scope === scope ? launched : null;
}

function launchedRequest(
  receipt: RoleExecutionReceipt,
  selection: RoleDocumentScope,
  scope: string,
  currentScope: string,
): LaunchedRequest | null {
  if (scope !== currentScope || !receipt.execution?.agentId || !receipt.requestId) return null;
  return { scope, selection, requestId: receipt.requestId };
}

function displayedAgent(
  launch: LaunchedRequest | null,
  ref: TaskDocumentRef | undefined,
  agents: DocumentAgent[] | null,
) {
  return launch ? (agents?.[0] ?? null) : documentChatAgent(ref, agents ?? []);
}

function agentTarget(agent: DocumentAgent | null) {
  return agent ? { agentId: agent.agentId, workspaceId: agent.workspaceId! } : null;
}

/**
 * The frame is hidden only where the selection is known to have no chat. While
 * the dashboard still asks which chat the document has, the frame keeps what it shows, out of
 * reach. A document no role can start on has no chat and nothing to ask.
 */
function chatPresentation(
  scope: string,
  available: boolean,
  chat: { agents: DocumentAgent[] | null; roles: number },
  hasTarget: boolean,
  launcherScope: string | null,
) {
  const startable = chat.roles > 0;
  const known = chat.agents !== null || !startable;
  return {
    showLauncher: available && startable && known && (!hasTarget || launcherScope === scope),
    hideFrame: available && known && !hasTarget,
    hasControl: available && hasTarget,
    asking: available && !known,
  };
}
