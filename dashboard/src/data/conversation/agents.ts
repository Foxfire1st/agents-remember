// Sub-agent roster derivation + timeline focus model. Everything here is
// computed from projection evidence ONLY: the backend mints ONE roster item per sub-agent on the
// parent timeline (codex `codex-agent-<threadId>`, claude `claude-agent-<taskId>`; kind "notice",
// role "system", `agent` set), and the agents area/focus cycle render exactly that roster — no
// optimistic rows, no polling, no fabricated identity.

import type {
  ConversationAgentRef,
  ConversationAgentStatus,
  ConversationItem,
} from "./types";

/** Roster detection: only the backend's explicit one-row-per-agent identities. */
export function isAgentRosterItem(item: ConversationItem): boolean {
  const rosterIdentity =
    item.itemId.startsWith("codex-agent-") || item.itemId.startsWith("claude-agent-");
  return rosterIdentity && item.kind === "notice" && item.role === "system" && item.agent != null;
}

/** The short-id fallback fragment: the first 8 chars of the native agent id. */
export function shortAgentId(agentId: string): string {
  return agentId.slice(0, 8);
}

/**
 * The display label for one agent (R7): nickname ?? role ?? the last agentPath segment ??
 * `agent <short-id>`. Every fallback is bound evidence; the last resort names the id, never a
 * fabricated name.
 */
export function agentLabel(agent: ConversationAgentRef): string {
  if (typeof agent.nickname === "string" && agent.nickname.length > 0) return agent.nickname;
  if (typeof agent.role === "string" && agent.role.length > 0) return agent.role;
  if (typeof agent.agentPath === "string" && agent.agentPath.length > 0) {
    const segment = agent.agentPath.split("/").filter((part) => part.length > 0).pop();
    if (segment !== undefined) return segment;
  }
  return `agent ${shortAgentId(agent.agentId)}`;
}

/** Terminal agent lifecycle states — only these may surface a final-message preview. */
export function isTerminalAgentStatus(status: ConversationAgentStatus): boolean {
  return status === "completed" || status === "interrupted" || status === "failed";
}

/**
 * The roster item's final report preview. Codex carries it as a `final-message` TextBlock; the
 * claude task_notification's terminal summary lands as a `summary` TextBlock. Only a terminal
 * roster row yields a preview — an in-flight roster's transient labels are not a report.
 */
function finalMessageOf(item: ConversationItem): string | undefined {
  for (const blockId of ["final-message", "summary"]) {
    const block = item.blocks.find((candidate) => candidate.blockId === blockId);
    if (block !== undefined && block.type === "text" && block.text.length > 0) return block.text;
  }
  return undefined;
}

export interface ConversationAgentView {
  agentId: string;
  label: string;
  status: ConversationAgentStatus;
  /** The terminal roster's final report preview (absent while running/registered/unknown). */
  finalMessage?: string;
}

/**
 * Derive the agents area rows from the projection's roster items. One row per agent, in first-
 * evidence order; later roster upserts for the same agent replace the row (the projection holds
 * one item per agent id, so this is ordinarily a single pass).
 */
export function deriveAgents(items: readonly ConversationItem[]): ConversationAgentView[] {
  const byId = new Map<string, ConversationAgentView>();
  for (const item of items) {
    if (!isAgentRosterItem(item) || item.agent == null) continue;
    const agent = item.agent;
    const finalMessage = isTerminalAgentStatus(agent.status) ? finalMessageOf(item) : undefined;
    const existing = byId.get(agent.agentId);
    byId.set(agent.agentId, {
      agentId: agent.agentId,
      label: agentLabel(agent),
      status: agent.status,
      finalMessage: finalMessage ?? existing?.finalMessage,
    });
  }
  return [...byId.values()];
}

/**
 * The focus cycle (the Claude Code agents-view precedent): parent → agent 1 → … → agent N →
 * parent. `null` is the parent. A focus naming an agent the roster no longer carries (a stale
 * survivor of an LRU eviction/rehydrate) resolves to the parent — the honest recompute.
 */
export function cycleAgentFocus(
  current: string | null,
  agentIds: readonly string[],
  direction: 1 | -1,
): string | null {
  if (agentIds.length === 0) return null;
  const size = agentIds.length + 1;
  const position = current === null ? 0 : agentIds.indexOf(current) + 1; // stale id → 0 (parent)
  const next = (((position + direction) % size) + size) % size;
  return next === 0 ? null : agentIds[next - 1];
}

/** The stored focus, recomputed against the live roster: an unknown agent id falls back to parent. */
export function effectiveAgentFocus(
  stored: string | null | undefined,
  agents: readonly ConversationAgentView[],
): string | null {
  if (stored === null || stored === undefined) return null;
  return agents.some((agent) => agent.agentId === stored) ? stored : null;
}

/**
 * The timeline's item filter for a focus (R7): the parent view keeps parent items plus the roster
 * rows; an agent view keeps that agent's own items (`agent.agentId` match — which includes its
 * roster row, so the focused lane still shows its status/final report).
 */
export function filterItemsForFocus(
  items: readonly ConversationItem[],
  focus: string | null,
): ConversationItem[] {
  if (focus === null) {
    return items.filter((item) => item.agent == null || isAgentRosterItem(item));
  }
  return items.filter((item) => item.agent?.agentId === focus);
}
