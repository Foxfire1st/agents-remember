/**
 * MIK-R117 rule 11: a role agent's silent turn end wakes its parent.
 *
 * Pure decision, no host calls: every function here takes plain data and a
 * tracker the caller owns, so the tests run without a host. The wiring in
 * `index.server.ts` feeds host events in and sends through the host client.
 *
 * The rule, one line per ended turn: the line names the agent (role and id),
 * its task, that the turn ended, and where its newest report lies. It never
 * carries agent text. A turn whose timeline holds a `role_message` call to
 * the parent (a return) gives no line. The line goes out at once when the
 * parent is free; a parent in a turn or waiting for a permission gets it
 * when free, once. An agent with one of its own in a turn gives none until
 * that one ends (a held line drops if one of its own begins again). An agent
 * waiting for a permission, and an agent whose parent the product did not
 * start as a role agent, give no line. Held lines for one parent may go out
 * together as one message, one line per ended turn. The line keeps the limit
 * of 1000 characters.
 */

export interface TurnEndedAgent {
  readonly id: string;
  readonly parentAgentId: string | null;
}

export interface TurnEndedFacts {
  readonly role: string;
  readonly task: string;
  readonly reportPath: string;
}

export interface HeldLine {
  readonly subject: string;
  readonly parent: string;
  readonly line: string;
}

export interface TurnEndedTracker {
  readonly inTurn: Set<string>;
  readonly permissionWait: Set<string>;
  readonly parentOf: Map<string, string>;
  readonly held: HeldLine[];
  readonly claimed: Set<HeldLine>;
}

export interface TurnEndedSender {
  sendMessage(agentId: string, text: string): Promise<void>;
}

/** The line keeps the limit of 1000 characters. */
export const TURN_ENDED_LINE_LIMIT = 1000;

export function createTurnEndedTracker(): TurnEndedTracker {
  return { inTurn: new Set(), permissionWait: new Set(), parentOf: new Map(), held: [], claimed: new Set() };
}

export function composeTurnEndedLine(
  agentId: string,
  facts: TurnEndedFacts,
): string {
  const line =
    `turn ended: ${facts.role} ${agentId} · ${facts.task}; ` +
    `newest report: ${facts.reportPath}`;
  return line.length <= TURN_ENDED_LINE_LIMIT ? line : line.slice(0, TURN_ENDED_LINE_LIMIT);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function candidateRecipients(item: Record<string, unknown>): unknown[] {
  const found: unknown[] = [];
  const detail = asRecord(item["detail"]);
  if (detail !== null) found.push(detail);
  for (const holder of [item, detail]) {
    if (holder === null) continue;
    for (const key of ["arguments", "input", "metadata", "params", "parameters"]) {
      if (holder[key] !== undefined) found.push(holder[key]);
    }
  }
  return found;
}

function recipientIsParent(candidate: unknown, parentId: string, parentRole: string, parentTask: string): boolean {
  const record = asRecord(candidate);
  if (record === null) {
    return typeof candidate === "string" && candidate === parentId;
  }
  for (const key of ["agent_id", "agentId", "recipientAgentId", "recipient_agent_id", "to"]) {
    if (record[key] === parentId) return true;
  }
  const role = record["role"] ?? record["recipientRole"] ?? record["recipient_role"];
  if (role === parentRole) {
    const text = JSON.stringify(candidate);
    if (parentTask !== "" && text.includes(parentTask)) return true;
    const pathPart = parentTask.includes(":") ? parentTask.split(":").slice(1).join(":") : parentTask;
    if (pathPart !== "" && text.includes(pathPart)) return true;
  }
  for (const value of Object.values(record)) {
    if (typeof value === "object" && value !== null) {
      if (recipientIsParent(value, parentId, parentRole, parentTask)) return true;
    }
  }
  return false;
}

/**
 * Whether the ended turn holds a return: a `role_message` tool call whose
 * recipient is the parent, by agent id or by the parent's role and task.
 * A `role_message` to anyone else (a sibling handover, a developer answer)
 * is not a return, and an opaque call names no recipient, so neither
 * silences the parent's line.
 */
export function turnHasReturnToParent(
  timeline: readonly unknown[],
  parentId: string,
  parent: TurnEndedFacts,
): boolean {
  for (const entry of timeline) {
    const item = asRecord(entry);
    if (item === null || item["type"] !== "tool_call" || item["name"] !== "role_message") continue;
    for (const candidate of candidateRecipients(item)) {
      if (recipientIsParent(candidate, parentId, parent.role, parent.task)) return true;
    }
  }
  return false;
}

function ancestorsOf(agentId: string, tracker: TurnEndedTracker): Set<string> {
  const ancestors = new Set<string>();
  let current = tracker.parentOf.get(agentId);
  while (current !== undefined && !ancestors.has(current)) {
    ancestors.add(current);
    current = tracker.parentOf.get(current);
  }
  return ancestors;
}

function childrenInTurn(subject: string, tracker: TurnEndedTracker): boolean {
  for (const [child, parent] of tracker.parentOf) {
    if (parent === subject && tracker.inTurn.has(child)) return true;
  }
  return false;
}

function parentIsFree(parent: string, tracker: TurnEndedTracker): boolean {
  return !tracker.inTurn.has(parent) && !tracker.permissionWait.has(parent);
}

/** A turn begins: the agent is in a turn and not waiting; a held or claimed
 * line about one of its own that begins again is dropped (it has not
 * stopped). A claim in flight is invalidated the same way a held line is. */
export function noteTurnStarted(agent: TurnEndedAgent, tracker: TurnEndedTracker): void {
  tracker.inTurn.add(agent.id);
  tracker.permissionWait.delete(agent.id);
  if (agent.parentAgentId !== null) tracker.parentOf.set(agent.id, agent.parentAgentId);
  const ancestors = ancestorsOf(agent.id, tracker);
  tracker.held.splice(
    0,
    tracker.held.length,
    ...tracker.held.filter((line) => !ancestors.has(line.subject)),
  );
  for (const line of tracker.claimed) {
    if (ancestors.has(line.subject)) tracker.claimed.delete(line);
  }
}

export function notePermissionRequested(agentId: string, tracker: TurnEndedTracker): void {
  tracker.permissionWait.add(agentId);
}

export function noteAgentArchived(agentId: string, tracker: TurnEndedTracker): void {
  tracker.inTurn.delete(agentId);
  tracker.permissionWait.delete(agentId);
  tracker.held.splice(
    0,
    tracker.held.length,
    ...tracker.held.filter((line) => line.parent !== agentId && line.subject !== agentId),
  );
}

export interface FlushOutcome {
  readonly sent: { readonly parent: string; readonly text: string }[];
  readonly failed: { readonly parent: string; readonly error: string }[];
  readonly dropped: HeldLine[];
}

/** Send every held line whose subject has stopped and whose parent is free,
 * batched one message per parent. Claimed lines join the tracker's claim
 * registry synchronously, before any await, so overlapping flushes cannot own
 * the same line: one held line is delivered once. A restart that begins one
 * of the subject's own invalidates the claim exactly like a held line; an
 * invalidated claim is dropped, never sent or requeued. A failed send, and a
 * line whose parent grew busy or whose own began again mid-flush, re-queues
 * held. */
export async function flushHeldLines(
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
): Promise<FlushOutcome> {
  const claimed = tracker.held.filter(
    (line) => !childrenInTurn(line.subject, tracker) && parentIsFree(line.parent, tracker),
  );
  if (claimed.length === 0) return { sent: [], failed: [], dropped: [] };
  const owned = new Set<HeldLine>(claimed);
  tracker.held.splice(
    0,
    tracker.held.length,
    ...tracker.held.filter((line) => !owned.has(line)),
  );
  for (const line of claimed) tracker.claimed.add(line);
  const byParent = new Map<string, HeldLine[]>();
  for (const line of claimed) {
    const lines = byParent.get(line.parent) ?? [];
    lines.push(line);
    byParent.set(line.parent, lines);
  }
  const sent: { readonly parent: string; readonly text: string }[] = [];
  const failed: { readonly parent: string; readonly error: string }[] = [];
  const dropped: HeldLine[] = [];
  const requeue: HeldLine[] = [];
  for (const [parent, lines] of byParent) {
    // Recheck at the send point: the claim may have been invalidated, the
    // parent may have grown busy, or one of the subject's own may have begun
    // again, while an earlier send awaited.
    const valid = lines.filter((line) => tracker.claimed.has(line));
    dropped.push(...lines.filter((line) => !tracker.claimed.has(line)));
    const readyNow = new Set(
      valid.filter(
        (line) => !childrenInTurn(line.subject, tracker) && parentIsFree(parent, tracker),
      ),
    );
    requeue.push(...valid.filter((line) => !readyNow.has(line)));
    if (readyNow.size === 0) continue;
    const text = valid.filter((line) => readyNow.has(line)).map((line) => line.line).join("\n");
    const message = text.length <= TURN_ENDED_LINE_LIMIT ? text : text.slice(0, TURN_ENDED_LINE_LIMIT);
    try {
      await sender.sendMessage(parent, message);
      sent.push({ parent, text: message });
    } catch (error) {
      requeue.push(...valid.filter((line) => readyNow.has(line)));
      failed.push({ parent, error: error instanceof Error ? error.message : String(error) });
    }
  }
  for (const line of claimed) tracker.claimed.delete(line);
  if (requeue.length > 0) tracker.held.unshift(...requeue);
  return { sent, failed, dropped };
}

export type TurnEndedOutcome =
  | { readonly kind: "sent"; readonly text: string }
  | { readonly kind: "held" }
  | { readonly kind: "none" };

/**
 * A turn ended: hold one line for the parent unless the turn holds a return,
 * the agent waits for a permission, or the parent is not a recorded role
 * agent. Then deliver whatever is ready. One line per ended turn, delivered
 * once: a failed send keeps its lines held for the next free moment.
 */
export async function noteTurnEnded(
  agent: TurnEndedAgent,
  timeline: readonly unknown[],
  records: Readonly<Record<string, TurnEndedFacts>>,
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
): Promise<TurnEndedOutcome> {
  tracker.inTurn.delete(agent.id);
  const facts = records[agent.id];
  if (facts === undefined) return { kind: "none" };
  if (agent.parentAgentId !== null) tracker.parentOf.set(agent.id, agent.parentAgentId);
  await flushHeldLines(tracker, sender);
  if (tracker.permissionWait.has(agent.id)) return { kind: "none" };
  const parentId = agent.parentAgentId;
  if (parentId === null) return { kind: "none" };
  const parent = records[parentId];
  if (parent === undefined) return { kind: "none" };
  if (turnHasReturnToParent(timeline, parentId, parent)) return { kind: "none" };
  const line = composeTurnEndedLine(agent.id, facts);
  const pushed: HeldLine = { subject: agent.id, parent: parentId, line };
  tracker.held.push(pushed);
  const outcome = await flushHeldLines(tracker, sender);
  const stillHeld = tracker.held.some((held) => held === pushed);
  if (stillHeld) return { kind: "held" };
  // A restart that began one of the agent's own while the line waited behind
  // another send drops the line: no line is owed anymore.
  if (outcome.dropped.some((dropped) => dropped === pushed)) return { kind: "none" };
  return { kind: "sent", text: line };
}

/** A permission resolved: the agent is free, so deliver what waits for it. */
export async function notePermissionResolved(
  agentId: string,
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
): Promise<FlushOutcome> {
  tracker.permissionWait.delete(agentId);
  return flushHeldLines(tracker, sender);
}
