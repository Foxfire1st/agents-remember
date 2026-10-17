/**
 * MIK-R117 rule 11 and MIK-R172: the end of a turn reaches whoever waits.
 *
 * Pure decision, no host calls: every function here takes plain data and a
 * tracker the caller owns, so the tests run without a host. The wiring in
 * `turn_events.ts` feeds host events in and sends through the host client.
 *
 * The rule, one line per ended turn: the line names the agent (role and id),
 * its task, the last line of the agent's own text from that turn, shortened to
 * 300 characters, and where its newest report lies. It carries no document. A
 * turn whose timeline holds a delivered `role_message` call to the parent (a
 * return) gives no line; a refused call gives the line. The line goes out at
 * once when the parent is free; a parent in a turn or waiting for a permission
 * gets it when free, once. An agent with one of its own in a turn gives none
 * until that one ends (a held line drops if one of its own begins again). An
 * agent waiting for a permission gives none. A leaf agent whose recorded parent
 * is archived or missing gives its line to the current Manager of its master;
 * an agent the product's agent list does not hold is named in the host log
 * once, with the reason. Held lines for one parent may go out together as one
 * message, one line per ended turn. The line keeps the limit of 1000 characters.
 */

export interface TurnEndedAgent {
  readonly id: string;
  readonly parentAgentId: string | null;
  /** The provider the host runs this agent on; it decides whether a steer is safe. */
  readonly provider?: string | null;
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
  /** Turn-end order: the order lines are sent in (MIK-R172@v2 rule 6). */
  readonly seq?: number;
  /** The subject's task, so a flush can find the current Manager when the parent goes. */
  readonly task?: string;
  /** The current Manager of the subject's master, when the parent itself goes. */
  readonly alternate?: string | null;
}

export interface TurnEndedTracker {
  readonly inTurn: Set<string>;
  readonly permissionWait: Set<string>;
  readonly parentOf: Map<string, string>;
  /** The provider each agent runs on, so a steer is offered only where it is known to be safe. */
  readonly providerOf: Map<string, string>;
  /** The providers the product knows to take a steer mid-turn (MIK-R172@v2 rule 6). */
  readonly steeringProviders: ReadonlySet<string>;
  /** The last turn-end sequence number handed out. */
  seq: number;
  /** The flush in flight, so a later one waits for it and order is delivery order. */
  flushing: Promise<void> | null;
  readonly held: HeldLine[];
  readonly claimed: Set<HeldLine>;
  /** Agents the host reported archived: a line for them has nowhere to go as it is. */
  readonly archived: Set<string>;
  /** Agents seen to start a turn here, oldest first, so the newest Manager is current. */
  readonly started: string[];
  /** Agents named in the host log once for a missing agent-list record. */
  readonly notedMissing: Set<string>;
}

export interface TurnEndedSender {
  sendMessage(agentId: string, text: string): Promise<void>;
}

/** The line keeps the limit of 1000 characters. */
export const TURN_ENDED_LINE_LIMIT = 1000;

/** The agent's last line travels shortened to 300 characters (MIK-R172 rule 3). */
export const TURN_ENDED_LAST_TEXT_LIMIT = 300;

/**
 * Bounds on the tracker's historical stores (MIK-R172 rule 5, L172-R1-F04).
 * The stores exist only to answer "who is current" and "whom did I already name
 * in the log"; an agent that departed the product's list is reclamable the moment
 * it is re-derived, so each store keeps a bounded recent window and drops its
 * oldest entry first. None of them holds an owed line: `held` does, and it is
 * bounded by who is waiting, not by history.
 */
export const TURN_ENDED_STARTED_LIMIT = 64;
/**
 * The bound on provider history (L172-R4-F03). An entry is reclaimed when its
 * agent is archived, and the map keeps a bounded recent window besides, oldest
 * entry evicted first. Eviction is safe by construction: an absent provider
 * reads as unknown, and an unknown provider holds the line, so a lost entry can
 * delay a wake but can never send into a running turn.
 */
export const TURN_ENDED_PROVIDER_LIMIT = 256;
export const TURN_ENDED_DEPARTED_LIMIT = 256;
export const TURN_ENDED_PARENT_LIMIT = 512;

function rememberProvider(tracker: TurnEndedTracker, id: string, provider: string): void {
  tracker.providerOf.delete(id);
  tracker.providerOf.set(id, provider);
  while (tracker.providerOf.size > TURN_ENDED_PROVIDER_LIMIT) {
    const oldest = tracker.providerOf.keys().next();
    if (oldest.done === true) break;
    tracker.providerOf.delete(oldest.value);
  }
}

function rememberDeparted(store: Set<string>, id: string): void {
  store.delete(id);
  store.add(id);
  while (store.size > TURN_ENDED_DEPARTED_LIMIT) {
    const oldest = store.values().next();
    if (oldest.done === true) break;
    store.delete(oldest.value);
  }
}

/** What the turn's own text lets the line say. */
export type TurnLastText =
  | { readonly kind: "text"; readonly text: string }
  | { readonly kind: "empty" }
  | { readonly kind: "unreadable" };

/**
 * The providers the product knows to take a steer while they are in a turn.
 *
 * MIK-R172@v2 rule 6: the host's `steer` is steer-or-replace, the protocol knows
 * only `interrupt` and `steer`, and the host's capabilities do not say which
 * providers steer, so nothing may be sent to a parent that is mid-turn unless
 * this set names its provider. The set is empty until evidence names a provider:
 * every parent is then informed when its turn ends, which is the rule's default
 * and the only choice that cannot replace a turn. A caller may pass its own set
 * (the tests do) without touching the wire.
 */
export const TURN_ENDED_STEERING_PROVIDERS: ReadonlySet<string> = new Set<string>();

const UNKNOWN = "";

export function createTurnEndedTracker(
  steeringProviders: ReadonlySet<string> = TURN_ENDED_STEERING_PROVIDERS,
): TurnEndedTracker {
  return {
    inTurn: new Set(),
    permissionWait: new Set(),
    parentOf: new Map(),
    providerOf: new Map(),
    steeringProviders,
    seq: 0,
    flushing: null,
    held: [],
    claimed: new Set(),
    archived: new Set(),
    started: [],
    notedMissing: new Set(),
  };
}

export function composeTurnEndedLine(
  agentId: string,
  facts: TurnEndedFacts,
  lastText: TurnLastText,
): string {
  const said =
    lastText.kind === "text"
      ? `last line: ${lastText.text}`
      : lastText.kind === "empty"
        ? "last line: no text"
        : "last line: unreadable";
  const line =
    `turn ended: ${facts.role} ${agentId} · ${facts.task}; ` +
    `${said}; newest report: ${facts.reportPath}`;
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

/** `repo` and `path` of a task address written as `repo:path`, `repo/path` or a bare path. */
export function taskAddress(task: string): { readonly repo: string; readonly path: string } {
  const colon = task.indexOf(":");
  if (colon > 0) return { repo: task.slice(0, colon), path: task.slice(colon + 1) };
  const slash = task.indexOf("/");
  if (slash > 0) return { repo: task.slice(0, slash), path: task.slice(slash + 1) };
  return { repo: "", path: task };
}

/** Every string a candidate recipient names, at any depth. */
function stringsOf(value: unknown, found: string[] = []): string[] {
  if (typeof value === "string") {
    found.push(value);
    return found;
  }
  if (Array.isArray(value)) {
    for (const entry of value) stringsOf(entry, found);
    return found;
  }
  const record = asRecord(value);
  if (record !== null) {
    for (const entry of Object.values(record)) stringsOf(entry, found);
  }
  return found;
}

/**
 * Whether one candidate recipient names the parent. A supported role_message
 * names its recipient by agent id, or by role plus the task reference in any of
 * the shapes the product and its callers use: the canonical
 * `repository`/`path` pair, `repo:path`, `repo/path`, or the bare path
 * (L172-R1-F03). A near match that shares only the path tail, or names another
 * role, is not the parent.
 */
function recipientIsParent(candidate: unknown, parentId: string, parentRole: string, parentTask: string): boolean {
  const record = asRecord(candidate);
  if (record === null) {
    return typeof candidate === "string" && candidate === parentId;
  }
  for (const key of ["agent_id", "agentId", "recipientAgentId", "recipient_agent_id", "to"]) {
    if (record[key] === parentId) return true;
  }
  const named = stringsOf(candidate);
  const role = record["role"] ?? record["recipientRole"] ?? record["recipient_role"] ?? record["recipient"];
  const roleMatches = role === parentRole || named.includes(parentRole);
  if (roleMatches) {
    const address = taskAddress(parentTask);
    // A full address string names the parent exactly.
    if (parentTask !== "" && named.includes(parentTask)) return true;
    // A path names the parent only together with the repository that path lives
    // in: a same-path reference under a different repository is a foreign task
    // and never a return (L172-R1-F03, R2).
    if (candidateNamesParentTask(candidate, address, parentTask)) return true;
  }
  for (const value of Object.values(record)) {
    if (typeof value === "object" && value !== null) {
      if (recipientIsParent(value, parentId, parentRole, parentTask)) return true;
    }
  }
  return false;
}

/**
 * Whether one record names the parent's task by its `path`, with the repository
 * that record states — or with no repository stated, in which case the path
 * alone names the task. The pairing is per record, so a message that carries
 * several references (a sprint and a master of different repositories) cannot
 * lend one reference's repository to another reference's path.
 */
function recordNamesTask(
  record: Record<string, unknown>,
  address: { readonly repo: string; readonly path: string },
  parentTask: string,
): boolean {
  const path =
    record["path"] ?? record["document_path"] ?? record["taskDocumentPath"] ?? record["task_document_path"];
  if (typeof path !== "string") return false;
  if (address.path !== "" ? path !== address.path : path !== parentTask) return false;
  const repository = record["repository"] ?? record["repo_id"] ?? record["repoId"] ?? record["repositoryId"];
  if (typeof repository === "string" && repository !== "") {
    return address.repo === "" || repository === address.repo;
  }
  return true;
}

function candidateNamesParentTask(
  candidate: unknown,
  address: { readonly repo: string; readonly path: string },
  parentTask: string,
): boolean {
  if (Array.isArray(candidate)) {
    return candidate.some((entry) => candidateNamesParentTask(entry, address, parentTask));
  }
  const record = asRecord(candidate);
  if (record === null) return false;
  if (recordNamesTask(record, address, parentTask)) return true;
  return Object.values(record).some(
    (value) => typeof value === "object" && value !== null && candidateNamesParentTask(value, address, parentTask),
  );
}

/** A refusal answer the message tool returns rather than throws. */
const REFUSAL_STATUS = /"status"\s*:\s*"refused"/;

function holdsRefusal(value: unknown): boolean {
  if (typeof value === "string") return REFUSAL_STATUS.test(value);
  if (Array.isArray(value)) return value.some(holdsRefusal);
  const record = asRecord(value);
  if (record === null) return false;
  if (record["status"] === "refused") return true;
  return Object.values(record).some(holdsRefusal);
}

function roleMessageWasRefused(item: Record<string, unknown>): boolean {
  const status = item["status"];
  if (status === "failed" || status === "canceled") return true;
  if (status === "running") return true;
  for (const key of ["detail", "metadata", "error", "output", "result", "answer"]) {
    const value = item[key];
    if (value === undefined || value === null) continue;
    if (holdsRefusal(value)) return true;
  }
  return false;
}

/**
 * Whether the ended turn holds a delivered return: a `role_message` tool call
 * whose recipient is the parent, by agent id or by the parent's role and task,
 * that was not refused. A refused call gives the line (MIK-R172 rule 4). A
 * `role_message` to anyone else (a sibling handover, a developer answer) is not
 * a return, and an opaque call names no recipient, so neither silences the
 * parent's line.
 */
export function turnHasReturnToParent(
  timeline: readonly unknown[],
  parentId: string,
  parent: TurnEndedFacts,
): boolean {
  for (const entry of timeline) {
    const item = asRecord(entry);
    if (item === null || item["type"] !== "tool_call" || item["name"] !== "role_message") continue;
    if (roleMessageWasRefused(item)) continue;
    for (const candidate of candidateRecipients(item)) {
      if (recipientIsParent(candidate, parentId, parent.role, parent.task)) return true;
    }
  }
  return false;
}

function lastTextOf(item: Record<string, unknown>): TurnLastText | null {
  if (item["type"] !== "assistant_message") return null;
  const text = item["text"];
  if (typeof text !== "string") return null;
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "");
  if (lines.length === 0) return { kind: "empty" };
  const last = lines[lines.length - 1];
  return { kind: "text", text: last.length <= TURN_ENDED_LAST_TEXT_LIMIT ? last : last.slice(0, TURN_ENDED_LAST_TEXT_LIMIT) };
}

/**
 * The rows that belong to the ended turn (L172-R1-F01). The host hands
 * `agent.turn_ended` the agent's timeline, which reaches back over earlier
 * turns; a return or a last line from an earlier turn must not answer for this
 * one. Rows that carry a `turnId` are filtered by it. When the host does not
 * annotate rows, the turn begins at the last `user_message`, which is where a
 * turn's prompt (or a delivered message, this plugin's own line included)
 * arrives. A timeline with neither is a single-turn timeline and is taken whole.
 */
export function turnRows(timeline: readonly unknown[], turnId: string | null | undefined): readonly unknown[] {
  if (!Array.isArray(timeline)) return timeline;
  if (typeof turnId === "string" && turnId !== "") {
    const tagged = timeline.filter((entry) => {
      const item = asRecord(entry);
      return item !== null && item["turnId"] === turnId;
    });
    if (tagged.length > 0) return tagged;
  }
  let boundary = -1;
  for (let index = timeline.length - 1; index >= 0; index -= 1) {
    const item = asRecord(timeline[index]);
    if (item !== null && item["type"] === "user_message") {
      boundary = index;
      break;
    }
  }
  return boundary > 0 ? timeline.slice(boundary) : timeline;
}

/**
 * The last line of the agent's own text in the ended turn (MIK-R172 rule 3).
 * A turn with no such text says "no text"; a timeline this code cannot read
 * says "unreadable", so the line still goes out.
 */
export function lastLineOfTurn(timeline: readonly unknown[]): TurnLastText {
  if (!Array.isArray(timeline)) return { kind: "unreadable" };
  try {
    for (let index = timeline.length - 1; index >= 0; index -= 1) {
      const item = asRecord(timeline[index]);
      if (item === null) continue;
      const found = lastTextOf(item);
      if (found !== null && found.kind === "text") return found;
    }
    return { kind: "empty" };
  } catch {
    return { kind: "unreadable" };
  }
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

/**
 * Whether the line may be sent to this parent now (MIK-R172@v2 rule 6). A free
 * parent takes it at once. A parent in a turn takes it at once only when the
 * product knows its provider keeps the running turn under a steer; otherwise the
 * line waits for that turn to end, because the host's steer would replace it.
 */
function parentMayTakeLineNow(parent: string, tracker: TurnEndedTracker): boolean {
  if (tracker.permissionWait.has(parent)) return false;
  if (!tracker.inTurn.has(parent)) return true;
  const provider = tracker.providerOf.get(parent) ?? UNKNOWN;
  return provider !== UNKNOWN && tracker.steeringProviders.has(provider);
}

/** Turn-end order across requeues: the order the turns ended (rule 6). */
function keepTurnEndOrder(tracker: TurnEndedTracker): void {
  const placed = tracker.held.map((line, index) => ({ line, index }));
  placed.sort((left, right) => (left.line.seq ?? left.index) - (right.line.seq ?? right.index));
  tracker.held.splice(0, tracker.held.length, ...placed.map((entry) => entry.line));
}

/** `repo/master` for both a leaf task (`repo/master/leaf.json`) and its master. */
function masterOf(task: string): string {
  const parts = task.split("/");
  if (parts.length < 2) return task;
  return `${parts[0]}/${parts[1]}`;
}

/**
 * The current Manager of the agent's master (MIK-R117 rule 10): a recorded
 * manager of the same master that is not archived and not the agent or its
 * recorded parent. When more than one remains, the one this plugin saw start
 * last is current.
 */
function currentManagerOf(
  task: string,
  records: Readonly<Record<string, TurnEndedFacts>>,
  tracker: TurnEndedTracker,
  excluded: readonly string[],
): string | null {
  const master = masterOf(task);
  let current: string | null = null;
  for (const [id, facts] of Object.entries(records)) {
    if (facts.role !== "manager" || masterOf(facts.task) !== master) continue;
    if (tracker.archived.has(id) || excluded.includes(id)) continue;
    if (current === null || tracker.started.lastIndexOf(id) >= tracker.started.lastIndexOf(current)) {
      current = id;
    }
  }
  return current;
}

/** Who learns of this agent's ended turn, and the facts of that recipient. */
function resolveWakeParent(
  agent: TurnEndedAgent,
  facts: TurnEndedFacts,
  records: Readonly<Record<string, TurnEndedFacts>>,
  tracker: TurnEndedTracker,
): { readonly id: string; readonly facts: TurnEndedFacts } | null {
  const parentId = agent.parentAgentId;
  if (parentId === null) return null;
  const recorded = records[parentId];
  if (recorded !== undefined && !tracker.archived.has(parentId)) {
    return { id: parentId, facts: recorded };
  }
  const manager = currentManagerOf(facts.task, records, tracker, [agent.id, parentId]);
  if (manager === null) return null;
  const managerFacts = records[manager];
  if (managerFacts === undefined) return null;
  return { id: manager, facts: managerFacts };
}

/** A turn begins: the agent is in a turn and not waiting; a held or claimed
 * line about one of its own that begins again is dropped (it has not
 * stopped). A claim in flight is invalidated the same way a held line is. */
export function noteTurnStarted(agent: TurnEndedAgent, tracker: TurnEndedTracker): void {
  tracker.inTurn.add(agent.id);
  if (typeof agent.provider === "string" && agent.provider !== "") {
    rememberProvider(tracker, agent.id, agent.provider);
  }
  tracker.permissionWait.delete(agent.id);
  tracker.archived.delete(agent.id);
  if (tracker.started[tracker.started.length - 1] !== agent.id) {
    const seen = tracker.started.indexOf(agent.id);
    if (seen >= 0) tracker.started.splice(seen, 1);
    tracker.started.push(agent.id);
    while (tracker.started.length > TURN_ENDED_STARTED_LIMIT) tracker.started.shift();
  }
  if (agent.parentAgentId !== null) {
    tracker.parentOf.delete(agent.id);
    tracker.parentOf.set(agent.id, agent.parentAgentId);
    while (tracker.parentOf.size > TURN_ENDED_PARENT_LIMIT) {
      const oldest = tracker.parentOf.keys().next();
      if (oldest.done === true) break;
      tracker.parentOf.delete(oldest.value);
    }
  }
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

/**
 * An agent is archived. What waits for it moves to the alternate its line
 * names (MIK-R172 rule 5), and is dropped when that too is gone.
 */
export function noteAgentArchived(agentId: string, tracker: TurnEndedTracker): void {
  rememberDeparted(tracker.archived, agentId);
  tracker.providerOf.delete(agentId);
  tracker.inTurn.delete(agentId);
  tracker.permissionWait.delete(agentId);
  const kept: HeldLine[] = [];
  for (const line of tracker.held) {
    if (line.parent !== agentId && line.subject !== agentId) {
      kept.push(line);
      continue;
    }
    if (line.subject === agentId) continue;
    const alternate = line.alternate ?? null;
    if (alternate !== null && !tracker.archived.has(alternate)) {
      kept.push({
        subject: line.subject,
        parent: alternate,
        line: line.line,
        task: line.task,
        seq: line.seq,
        alternate: null,
      });
      continue;
    }
    // No recorded successor yet: the line stays owed and a flush re-resolves it
    // to whatever Manager is current then (L172-R1-F02).
    kept.push(line);
  }
  tracker.held.splice(0, tracker.held.length, ...kept);
}

/** Whole lines partitioned into messages that each fit the cap. */
export function partitionHeldLines(
  lines: readonly HeldLine[],
): { readonly messages: { readonly text: string; readonly lines: HeldLine[] }[]; readonly count: number } {
  const messages: { text: string; lines: HeldLine[] }[] = [];
  let current: HeldLine[] = [];
  let count = 0;
  for (const line of lines) {
    if (line.line.length > TURN_ENDED_LINE_LIMIT) break;
    const candidate = current.length === 0 ? line.line : `${current.map((held) => held.line).join("\n")}\n${line.line}`;
    if (current.length > 0 && candidate.length > TURN_ENDED_LINE_LIMIT) {
      messages.push({ text: current.map((held) => held.line).join("\n"), lines: current });
      current = [line];
    } else {
      current.push(line);
    }
    count += 1;
  }
  if (current.length > 0) messages.push({ text: current.map((held) => held.line).join("\n"), lines: current });
  return { messages, count };
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
  records?: Readonly<Record<string, TurnEndedFacts>>,
): Promise<FlushOutcome> {
  // L172-R4-F02: an earlier turn-end callback may still be sending when a later
  // one arrives. The later flush waits for the earlier one, so delivery order is
  // the turn-end order and a later line can never overtake an earlier one that is
  // already in flight.
  const previous = tracker.flushing;
  let release!: () => void;
  const mine = new Promise<void>((resolve) => {
    release = resolve;
  });
  tracker.flushing = mine;
  if (previous !== null) await previous;
  try {
    return await flushOnce(tracker, sender, records);
  } finally {
    if (tracker.flushing === mine) tracker.flushing = null;
    release();
  }
}

async function flushOnce(
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
  records?: Readonly<Record<string, TurnEndedFacts>>,
): Promise<FlushOutcome> {
  // A held line whose parent has gone moves to the current Manager of its
  // master, at the moment of sending rather than at the moment it was owed, so
  // an ordinary Manager replacement cannot swallow it (L172-R1-F02).
  if (records !== undefined) {
    const rebound: HeldLine[] = [];
    for (const line of tracker.held) {
      const parentKnown = records[line.parent] !== undefined && !tracker.archived.has(line.parent);
      if (parentKnown) {
        rebound.push(line);
        continue;
      }
      const task = line.task ?? "";
      const alternate = line.alternate ?? null;
      const alternateKnown =
        alternate !== null && records[alternate] !== undefined && !tracker.archived.has(alternate);
      const manager =
        (alternateKnown ? alternate : null) ??
        currentManagerOf(task, records, tracker, [line.subject, line.parent]);
      if (manager === null) {
        // No current Manager yet: the line stays owed, not dropped.
        rebound.push(line);
        continue;
      }
      rebound.push({
        subject: line.subject,
        parent: manager,
        line: line.line,
        task: line.task,
        seq: line.seq,
        alternate: currentManagerOf(task, records, tracker, [line.subject, manager]),
      });
    }
    tracker.held.splice(0, tracker.held.length, ...rebound);
  }
  keepTurnEndOrder(tracker);
  const claimed = tracker.held.filter(
    (line) => !childrenInTurn(line.subject, tracker) && parentMayTakeLineNow(line.parent, tracker),
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
        (line) => !childrenInTurn(line.subject, tracker) && parentMayTakeLineNow(parent, tracker),
      ),
    );
    requeue.push(...valid.filter((line) => !readyNow.has(line)));
    if (readyNow.size === 0) continue;
    // Whole lines only: the ready lines are partitioned into messages that each
    // fit the cap, and no line is ever cut (L172-R1-F05). A line that alone
    // cannot fit is a fault of the line, not of the batch: it stays owed.
    const ready = valid.filter((line) => readyNow.has(line));
    const batches = partitionHeldLines(ready);
    requeue.push(...ready.slice(batches.count));
    for (const [index, batch] of batches.messages.entries()) {
      // L172-R4-F01: the previous message of this same batch may have started a
      // turn in a parent that does not steer. The gate is asked again at every
      // actual send, and what could not be sent stays owed, in order.
      if (!parentMayTakeLineNow(parent, tracker)) {
        for (const unsent of batches.messages.slice(index)) requeue.push(...unsent.lines);
        break;
      }
      try {
        await sender.sendMessage(parent, batch.text);
        sent.push({ parent, text: batch.text });
      } catch (error) {
        for (const unsent of batches.messages.slice(index)) requeue.push(...unsent.lines);
        failed.push({ parent, error: error instanceof Error ? error.message : String(error) });
        break;
      }
    }
  }
  for (const line of claimed) tracker.claimed.delete(line);
  if (requeue.length > 0) tracker.held.push(...requeue);
  keepTurnEndOrder(tracker);
  return { sent, failed, dropped };
}

export type TurnEndedOutcome =
  | { readonly kind: "sent"; readonly text: string; readonly parent: string }
  | { readonly kind: "held"; readonly parent: string }
  | { readonly kind: "none"; readonly log?: string };

/**
 * A turn ended: hold one line for the agent that waits for it unless the turn
 * holds a delivered return, the agent waits for a permission, or no recipient
 * is recorded. Then deliver whatever is ready. One line per ended turn,
 * delivered once: a failed send keeps its lines held for the next free moment.
 * An agent the product's agent list does not hold gives no line and is named
 * in the host log once, with the reason.
 */
export async function noteTurnEnded(
  agent: TurnEndedAgent,
  timeline: readonly unknown[],
  records: Readonly<Record<string, TurnEndedFacts>>,
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
  turnId?: string | null,
): Promise<TurnEndedOutcome> {
  tracker.inTurn.delete(agent.id);
  if (typeof agent.provider === "string" && agent.provider !== "") {
    rememberProvider(tracker, agent.id, agent.provider);
  }
  // L172-R4-F02: the turn-end order is the order the events arrived in, so the
  // number is taken here, before the awaited preflush below can let a later
  // event overtake this one.
  tracker.seq += 1;
  const sequence = tracker.seq;
  const facts = records[agent.id];
  if (facts === undefined) {
    const log = tracker.notedMissing.has(agent.id)
      ? undefined
      : `agents-remember turn_ended: agent ${agent.id} is not in the product's agent list (role-agents.json); no parent line is owed for its ended turn`;
    rememberDeparted(tracker.notedMissing, agent.id);
    return log === undefined ? { kind: "none" } : { kind: "none", log };
  }
  if (agent.parentAgentId !== null) tracker.parentOf.set(agent.id, agent.parentAgentId);
  // The ended turn's own rows only: an earlier turn's return or text answers
  // for this turn no longer (L172-R1-F01).
  const own = turnRows(timeline, turnId);
  await flushHeldLines(tracker, sender, records);
  if (tracker.permissionWait.has(agent.id)) return { kind: "none" };
  const target = resolveWakeParent(agent, facts, records, tracker);
  if (target === null) return { kind: "none" };
  if (turnHasReturnToParent(own, target.id, target.facts)) return { kind: "none" };
  const line = composeTurnEndedLine(agent.id, facts, lastLineOfTurn(own));
  const alternate = currentManagerOf(facts.task, records, tracker, [agent.id, target.id]);
  const pushed: HeldLine = { subject: agent.id, parent: target.id, line, task: facts.task, alternate, seq: sequence };
  tracker.held.push(pushed);
  const outcome = await flushHeldLines(tracker, sender, records);
  const stillHeld = tracker.held.some((held) => held === pushed);
  if (stillHeld) return { kind: "held", parent: target.id };
  // A restart that began one of the agent's own while the line waited behind
  // another send drops the line: no line is owed anymore.
  if (outcome.dropped.some((dropped) => dropped === pushed)) return { kind: "none" };
  return { kind: "sent", text: line, parent: target.id };
}

/** A permission resolved: the agent is free, so deliver what waits for it. */
export async function notePermissionResolved(
  agentId: string,
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
  records?: Readonly<Record<string, TurnEndedFacts>>,
): Promise<FlushOutcome> {
  tracker.permissionWait.delete(agentId);
  return flushHeldLines(tracker, sender, records);
}
