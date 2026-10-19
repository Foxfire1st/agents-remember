/**
 * MIK-R117 rule 11, MIK-R172 and MIK-R183: the end of a turn reaches whoever waits.
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
 *
 * MIK-R183 changes one case of that rule. A leaf seat (Worker, Reviewer or
 * Curator) whose completed turn holds no hand-off, and which nothing the plugin
 * can see holds, is sent a message of its own instead of giving its parent the
 * line — but only after one wait of 60 seconds, at every count, and a turn of the
 * seat beginning inside the wait cancels it without counting. At most two such
 * messages go out in a row; every silent end from the third on sends the seat
 * nothing and gives the parent the line, marked with the count, and that line
 * waits the same 60 seconds. A helper's result never comes back under
 * `hermes acp`, so no end waits longer than another and the message says so. The run goes back to zero when the
 * seat hands off or when one of its turns begins with a message this plugin did
 * not send itself. A turn that hands off only to a sibling keeps MIK-R172's line
 * to the parent, and the Manager, the Orchestrator and the Architect are never
 * sent back.
 *
 * Rule 1 is shared with MIK-R172's return check: one recogniser reads a
 * `tool_call` row of any harness — a bare tool name, `<server>.<tool>`, or
 * Hermes's `other` kind whose `metadata.title` is `<server title> · <tool
 * words>` — and the call's arguments and answer are read from the row's detail
 * as the host's mapper builds it. Every seat message, marked line, cancelled
 * wait and left-alone end is a row in the plugin's own file
 * (`<PASEO_HOME>/agents-remember/turn-ends.jsonl`, turned over at 2 MiB).
 */

import type { TurnEndLogRow } from "./turn_log.ts";

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

/** MIK-R183 rule 4/5: what the wait ends with — the seat's message, or the parent's marked line. */
export type SeatWakeKind = "seat" | "parent-line";

/** MIK-R183 rule 4: the wait owed to one seat, as a caller may observe it. */
export interface SeatWakeWait {
  readonly seat: string;
  readonly ordinal: number;
  readonly dueAt: number;
  /** The message the seat would be sent; empty for a wait that ends in the parent's line. */
  readonly message: string;
  readonly kind: SeatWakeKind;
  /** Resolves when the wait ends: sent, cancelled by a turn start, or dropped. */
  readonly done: Promise<void>;
}

/** The wait's own record, with the timer and the facts its cancellation row needs. */
export interface PendingSeatWake extends SeatWakeWait {
  readonly facts: TurnEndedFacts;
  /** Rule 4: the generations this wait belongs to; a later one voids it. */
  readonly epoch: number;
  readonly seatEpoch: number;
  /** Rule 4/5: the act the wait ends with, run only after the host's state allows it. */
  readonly deliver: () => Promise<void>;
  timer: unknown;
  readonly finish: () => void;
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
  /**
   * Each leaf seat's run of silent ends since its last hand-off or a message
   * this plugin did not send: the ordinal the seat's last message carried, or
   * the parent's last marked line (MIK-R183 rule 5). Bounded like the other
   * historical stores: an entry is dropped when the run ends, and the oldest
   * entry is evicted past its cap.
   */
  readonly silentEnds: Map<string, number>;
  /** The wait after a silent end, one per seat, before the seat is sent back. */
  readonly waits: Map<string, PendingSeatWake>;
  /**
   * Rule 4/8: a shutdown or reload bumps this, and every in-flight intake or
   * delivery compares it before it acts — so no work started before the boundary
   * can send, schedule or restore a count after it.
   */
  epoch: number;
  /** The same generation, per seat: a turn of the seat bumps its own entry. */
  readonly seatEpoch: Map<string, number>;
  /**
   * Rule 3: how far the seat's timeline had grown at the last end this build
   * saw, so a message steered into a running turn does not split that turn.
   */
  readonly turnMarks: Map<string, TurnMark>;
  /** The plugin's own file of rule 7 rows; a no-op when the caller supplies none. */
  readonly append: (row: TurnEndLogRow) => void;
}

export interface TurnEndedSender {
  sendMessage(agentId: string, text: string): Promise<void>;
}

/** The line keeps the limit of 1000 characters. */
export const TURN_ENDED_LINE_LIMIT = 1000;

/** The agent's last line travels shortened to 300 characters (MIK-R172 rule 3). */
export const TURN_ENDED_LAST_TEXT_LIMIT = 300;

/** Rule 4: at most this many waits may exist at once; past it a new end sheds its own wake. */
export const TURN_ENDED_WAIT_LIMIT = 256;

/** MIK-R183 rule 5: at most this many send-back messages in a row. */
export const TURN_ENDED_SENT_BACK_LIMIT = 2;

/**
 * MIK-R183 rule 6: the roles whose hand-off-less turn is sent back to the seat
 * itself. Every other role keeps MIK-R172's line to whoever waits.
 */
export const TURN_ENDED_SEAT_ROLES: ReadonlySet<string> = new Set(["worker", "reviewer", "curator"]);

/** MIK-R183 rule 4: the wait after a silent end. */
export const TURN_ENDED_WAIT_MS = 60_000;

/**
 * MIK-R183 rule 4: the fixed first line of the message a seat is sent back with.
 * The plugin recognises its own message by it, and it is not a
 * `From <role> · … · agent <id>` line, so it is never taken for another agent's
 * message.
 */
export const SEAT_WAKE_FIRST_LINE = "[ar-plugin] turn ended with no hand-off:";

/** MIK-R183 rule 1: the AR tool server, as a harness may spell it in a row's text. */
const AR_SERVER_WORDS: readonly string[] = ["agents remember task"];

/** MIK-R183 rule 4: the `delegate_task` actions that are not a dispatch of a helper. */
const DELEGATE_TASK_QUIET_ACTIONS: ReadonlySet<string> = new Set([
  "list",
  "status",
  "wait",
  "cancel",
  "stop",
  "logs",
  "output",
]);

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
/**
 * MIK-R183 rule 4: the bound on seat runs and on the seat messages remembered
 * as this plugin's own. An evicted entry only lets a run restart or an own
 * message read as another agent's, so it can lose a mark or give a seat one
 * extra message; it can never send into a running turn.
 */
export const TURN_ENDED_SILENT_LIMIT = 256;

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
  append: (row: TurnEndLogRow) => void = () => {},
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
    silentEnds: new Map(),
    waits: new Map(),
    epoch: 0,
    seatEpoch: new Map(),
    turnMarks: new Map(),
    append,
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

/**
 * The structured holders a `role_message` row may name its recipient in. A
 * harness that keeps only the call's text in its detail (Hermes's `plain_text`)
 * names no recipient there, so the answer's own field is the evidence instead
 * (`answerRecipient`); the prose is never read as an address.
 */
function candidateRecipients(item: Record<string, unknown>): unknown[] {
  const found: unknown[] = [];
  const detail = asRecord(item["detail"]);
  const textOnlyDetail =
    detail !== null && (typeof detail["text"] === "string" || typeof detail["command"] === "string");
  if (detail !== null && !textOnlyDetail) found.push(detail);
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

/** Letters and digits only, lower-cased: hyphens, dots, separators and titles all read alike. */
function normalisedWords(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** Rule 1: the ACP kinds that never carry an AR call. */
const NON_AR_KINDS: ReadonlySet<string> = new Set([
  "execute",
  "read",
  "edit",
  "search",
  "fetch",
  "think",
  "switch_mode",
]);

/** The ACP kind the host mapped this row to, from `metadata.kind` or a bare `name`. */
function acpKindOf(item: Record<string, unknown>): string | null {
  const metadata = asRecord(item["metadata"]);
  const kind = metadata === null ? undefined : metadata["kind"];
  if (typeof kind === "string" && kind !== "") return kind.toLowerCase();
  const name = item["name"];
  if (typeof name === "string" && NON_AR_KINDS.has(name.toLowerCase())) return name.toLowerCase();
  return null;
}

/** The strings of a row that may carry its identity, in the order the host builds them. */
function identityTexts(item: Record<string, unknown>): string[] {
  const texts: string[] = [];
  const name = item["name"];
  if (typeof name === "string") texts.push(name);
  const metadata = asRecord(item["metadata"]);
  if (metadata !== null) {
    for (const key of ["title", "kind"]) {
      const value = metadata[key];
      if (typeof value === "string") texts.push(value);
    }
  }
  const detail = asRecord(item["detail"]);
  if (detail !== null) {
    const label = detail["label"];
    if (typeof label === "string") texts.push(label);
  }
  return texts;
}

/**
 * Rule 1: the identity part of a row text. A leading `[Other]` bracket and a
 * leading `tool_call: ` are prefixes the host adds; everything after them, up to
 * the first colon, is the tool's identity — so a preview that merely mentions
 * `role_message` never names the tool.
 */
export function identityOf(value: string): string {
  let text = value.trim();
  for (;;) {
    const bracket = /^\[[^\]]*\]\s*/.exec(text);
    if (bracket === null) break;
    text = text.slice(bracket[0].length);
  }
  const prefix = /^tool_call:\s*/i.exec(text);
  if (prefix !== null) text = text.slice(prefix[0].length);
  const colon = text.indexOf(":");
  if (colon >= 0) text = text.slice(0, colon);
  return text.trim();
}

/** The AR tools this rule reads, as the words a row's identity carries. */
const AR_TOOL_WORDS: ReadonlyArray<readonly [string, string]> = [
  ["role_message", "role message"],
  ["task_doc", "task doc"],
];

/**
 * MIK-R183 rule 1: which AR tool a `tool_call` row is, in every shape the
 * harnesses send — the bare name (`role_message`), `<server>.<tool>` and the
 * other separators, and Hermes's `other` kind, whose `metadata.title` is one of
 * `tool_call: Agents Remember Task · role message`,
 * `tool_call: mcp__agents_remember_task__role_message`, or
 * `mcp__agents_remember_task__role_message: <preview>`.
 *
 * The whole title is read, so a leading `tool_call: `, a `[Other]` prefix and a
 * trailing `: <preview>` do not hide the tool. A row the host mapped to a non-AR
 * ACP kind (`execute`, `read`, `edit`, `search`, `fetch`, `think`,
 * `switch_mode`) is never an AR call, so a shell command that only mentions the
 * words never matches.
 */
export function recogniseArTool(item: Record<string, unknown>): string | null {
  if (item["type"] !== "tool_call") return null;
  const kind = acpKindOf(item);
  if (kind !== null && NON_AR_KINDS.has(kind)) return null;
  for (const text of identityTexts(item)) {
    const identity = normalisedWords(identityOf(text));
    if (identity === "") continue;
    const namesServer = AR_SERVER_WORDS.some((server) => identity.includes(server));
    for (const [tool, words] of AR_TOOL_WORDS) {
      if (identity === words || identity === tool) return tool;
      if (namesServer && identity.includes(words)) return tool;
    }
  }
  return null;
}

/**
 * Rule 4: whether a row is a `delegate_task` call. Its title begins
 * `delegate_task:`, so the whole text is read and the message says that a
 * helper's result does not come back.
 */
export function dispatchedDelegateTask(item: Record<string, unknown>): boolean {
  for (const text of identityTexts(item)) {
    const words = normalisedWords(text).split(" ");
    if (words[0] === "delegate" && words[1] === "task") return true;
  }
  return false;
}

/** The answer as the row stores it: Hermes's `text`, or Codex's `output` block. */
function rawAnswerText(item: Record<string, unknown>): string {
  const detail = asRecord(item["detail"]);
  if (detail === null) return "";
  const pieces: string[] = [];
  for (const key of ["text", "output", "result", "stdout"]) {
    const value = detail[key];
    if (typeof value === "string") pieces.push(value);
    else if (value !== null && typeof value === "object") pieces.push(stringsOf(value).join("\n"));
  }
  return pieces.join("\n");
}

/**
 * Rule 1: one unescape pass over an answer, because Hermes wraps an escaped
 * JSON answer in `<untrusted_tool_result …>` and a cut answer is read as it
 * stands.
 */
export function unescapeAnswer(text: string): string {
  if (!text.includes("\\")) return text;
  const escapes: Record<string, string> = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    b: "\b",
    f: "\f",
    n: "\n",
    r: "\r",
    t: "\t",
  };
  return text.replace(/\\(["\\/bfnrt])/g, (_match, code: string) => escapes[code] ?? code);
}

/** The first JSON object a text holds, or null when it holds none. */
function firstJsonObject(text: string): Record<string, unknown> | null {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  try {
    return asRecord(JSON.parse(text.slice(start, end + 1)) as unknown);
  } catch {
    return null;
  }
}

/**
 * Rule 1: the answer's text, read from either wrapper. Hermes's plain form is
 * `"<tool> result\n- **result:** {…}"` and is read as it stands; its
 * `<untrusted_tool_result …>` form wraps an escaped copy of the answer, so the
 * text gets the one unescape and then the wrapper's own JSON is read, whose
 * `result` string is the answer. A cut answer is read as far as it goes.
 */
export function answerText(item: Record<string, unknown>): string {
  const text = unescapeAnswer(rawAnswerText(item));
  const wrapper = firstJsonObject(text);
  if (wrapper === null) return text;
  const inner = wrapper["result"];
  if (typeof inner === "string") return inner;
  return text;
}

/** The answer's `status`, read from the detail of either harness's row. */
function answerStatus(item: Record<string, unknown>): string | null {
  const detail = asRecord(item["detail"]);
  if (detail !== null && typeof detail["status"] === "string") return detail["status"].toLowerCase();
  const matched = /"status"\s*:\s*"([a-z-]+)"/i.exec(answerText(item));
  return matched === null ? null : matched[1].toLowerCase();
}

/** Whether the row's own status says the call did not complete. */
function callUnfinished(item: Record<string, unknown>): boolean {
  const status = item["status"];
  return status === "failed" || status === "canceled" || status === "running";
}

/**
 * Rule 2: whether the answer says the call was refused — `ok: false`,
 * `status: refused` or a `recipient-busy` refusal. Read from the structured
 * detail where the host keeps one, and from the answer's text after the
 * unescape where it does not.
 */
export function answerIsRefusal(item: Record<string, unknown>): boolean {
  const detail = asRecord(item["detail"]);
  if (detail !== null) {
    if (detail["ok"] === false) return true;
    const status = typeof detail["status"] === "string" ? detail["status"].toLowerCase() : null;
    if (status === "refused" || status === "recipient-busy") return true;
  }
  const text = answerText(item).toLowerCase();
  if (/"ok"\s*:\s*false/.test(text)) return true;
  if (/"status"\s*:\s*"(refused|recipient-busy)"/.test(text)) return true;
  if (/"refusal"\s*:\s*"[^"]*"/.test(text)) return true;
  return false;
}

/** Whether a call was refused, failed, canceled or is still running: not delivered. */
export function callWasRefused(item: Record<string, unknown>): boolean {
  if (callUnfinished(item)) return true;
  return answerIsRefusal(item) || holdsRefusal(item["detail"]);
}

/** Rule 2: a finished `role_message` whose answer is not a refusal is a hand-off. */
export function roleMessageHandedOff(item: Record<string, unknown>): boolean {
  return !callUnfinished(item) && !answerIsRefusal(item);
}

/** Rule 1: a `role_message` answered `accepted` or `held`; the return to the parent needs it. */
export function roleMessageWasAnswered(item: Record<string, unknown>): boolean {
  const status = answerStatus(item);
  return status === "accepted" || status === "held";
}

/**
 * Rule 2: a `task_doc` write whose answer is not a refusal. Hermes's row shows
 * no operation, so any such write counts; a row that keeps its arguments counts
 * only an operation of the four writes.
 */
export function taskDocHandedOff(item: Record<string, unknown>): boolean {
  // Rule 2: `task_doc` has no read operation, so every successful write of it is
  // a hand-off — `set_step` and `begin_review` as much as `append_decision`,
  // `set_section`, `set_status`, `set_field` or `add_step`. Only a refusal is not.
  return !callUnfinished(item) && !answerIsRefusal(item);
}

/**
 * Rule 1: the recipient of a `role_message` the answer names, as
 * `"recipientAgentId":"<id>"` in the answer's JSON, for a harness whose row
 * keeps no arguments. Null when the answer names no recipient — and a row that
 * shows no recipient is not a return (rule 1).
 */
export function answerRecipient(item: Record<string, unknown>): string | null {
  const text = answerText(item);
  const matched = /"(?:recipientAgentId|recipient_agent_id|agent_id|agentId)"\s*:\s*"([0-9a-zA-Z-]+)"/i.exec(
    text,
  );
  return matched === null ? null : matched[1];
}

/**
 * Rule 1/2: the operation a `task_doc` call performed, where the row shows it
 * (`detail.input.operation`), else from the call's own text and the answer's
 * wording (`step S1 updated`, `review round 2 opened`).
 */
export function taskDocOperation(item: Record<string, unknown>): string | null {
  const detail = asRecord(item["detail"]);
  const input = detail === null ? null : asRecord(detail["input"]);
  if (input !== null) {
    for (const key of ["operation", "op"]) {
      const value = input[key];
      if (typeof value === "string" && value !== "") return value;
    }
  }
  const text = `\n${answerText(item).toLowerCase()}`;
  for (const operation of ["record_route_review", "begin_review", "record_review", "set_step"]) {
    if (text.includes(operation)) return operation;
  }
  if (/\breview round\b/.test(text) && /\bopen(ed)?\b/.test(text)) return "begin_review";
  if (/\broute review\b/.test(text) && /\brecord(ed)?\b/.test(text)) return "record_route_review";
  if (/\breview\b/.test(text) && /\brecord(ed)?\b/.test(text)) return "record_review";
  if (/\bstep\b/.test(text) && /\bupdated\b/.test(text)) return "set_step";
  return null;
}

/**
 * Whether the ended turn hands off (rule 2): a `role_message` whose answer is
 * not a refusal, or a `task_doc` write whose answer is not a refusal. A leaf
 * seat may start no role, so a `role_start` is no hand-off of theirs. Reads,
 * edits, tests, helpers and chat text never count.
 */
export function turnHasHandoff(timeline: readonly unknown[]): boolean {
  for (const entry of timeline) {
    const item = asRecord(entry);
    if (item === null || item["type"] !== "tool_call") continue;
    const tool = recogniseArTool(item);
    if (tool === "role_message" && roleMessageHandedOff(item)) return true;
    if (tool === "task_doc" && taskDocHandedOff(item)) return true;
  }
  return false;
}

/** Whether the run of silent ends for this seat goes back to zero (rule 5). */
function resetSilentEnds(tracker: TurnEndedTracker, seat: string): void {
  tracker.silentEnds.delete(seat);
}

/** The next ordinal in the seat's run: the last one it was sent, plus one (rule 5). */
function nextSilentEnd(tracker: TurnEndedTracker, seat: string): number {
  return (tracker.silentEnds.get(seat) ?? 0) + 1;
}

/** Remember one confirmed silent end of this seat, bounded like the other stores. */
function rememberSilentEnd(tracker: TurnEndedTracker, seat: string, ordinal: number): void {
  tracker.silentEnds.delete(seat);
  tracker.silentEnds.set(seat, ordinal);
  while (tracker.silentEnds.size > TURN_ENDED_SILENT_LIMIT) {
    const oldest = tracker.silentEnds.keys().next();
    if (oldest.done === true) break;
    tracker.silentEnds.delete(oldest.value);
  }
}

/** The first `user_message` row of the ended turn, or null when it holds none. */
export function firstUserMessage(timeline: readonly unknown[]): string | null {
  for (const entry of timeline) {
    const item = asRecord(entry);
    if (item === null || item["type"] !== "user_message") continue;
    const text = item["text"];
    if (typeof text === "string") return text;
  }
  return null;
}

/** `From <role> · <task> · agent <id>`: a delivered message of another agent. */
const FROM_MESSAGE = /^\s*From\s+[^\n·]*·[^\n]*·\s*agent\s+([0-9a-zA-Z-]+)\s*$/m;

/** The agent id a delivered `From … · agent <id>` message names (rule 3). */
export function messageSenderOf(text: string | null): string | null {
  if (text === null) return null;
  const matched = FROM_MESSAGE.exec(text);
  return matched === null ? null : matched[1];
}

/** Whether a message is this plugin's own send-back (rule 4's fixed first line). */
export function isPluginWakeMessage(text: string | null): boolean {
  // Rule 4: the plugin's own message is recognised by its fixed first line. A
  // message that quotes the line below an external `From …` line is not its own,
  // so an external message still resets the run.
  if (text === null) return false;
  // The contract is the FIRST line: a message whose first line is blank and whose
  // second line carries the marker is not the plugin's own.
  const firstLine = (text.split(/\r?\n/, 1)[0] ?? "").replace(/^[ \t]+/, "");
  return firstLine.startsWith(SEAT_WAKE_FIRST_LINE) && !/^From\s/.test(firstLine);
}

/**
 * Rule 3: the turn may have answered, in chat, a sender that is itself in a
 * turn at this end. Such an end is not silent and gives the parent today's line.
 */
function isReplyToWaitingSender(
  timeline: readonly unknown[],
  tracker: TurnEndedTracker,
  states: Map<string, HostAgentState> | null = null,
): boolean {
  const message = firstUserMessage(timeline);
  if (isPluginWakeMessage(message)) return false;
  const sender = messageSenderOf(message);
  if (sender === null) return false;
  return inTurnNow(sender, states, tracker);
}

/** Rule 3/4: the host's state for one agent, as the send and the end read it. */
export interface HostAgentState {
  readonly id: string;
  /** Rule 3: the `paseo.parent-agent-id` label, the only parent link the host shows. */
  readonly parentAgentId: string | null;
  readonly inTurn: boolean;
  readonly permissionWait: boolean;
  readonly archived: boolean;
}

/**
 * Rule 3/4: the host's agent state at one moment. `null` means it could not be
 * read, and the decision falls back to the events it saw.
 */
export type HostStateReader = () => Promise<readonly HostAgentState[] | null>;

/** The host's state, by agent id; `null` when it could not be read. */
function statesOf(states: readonly HostAgentState[] | null): Map<string, HostAgentState> | null {
  if (states === null) return null;
  const map = new Map<string, HostAgentState>();
  for (const state of states) map.set(state.id, state);
  return map;
}

async function readHostStates(reader: HostStateReader | undefined): Promise<Map<string, HostAgentState> | null> {
  if (reader === undefined) return null;
  try {
    return statesOf(await reader());
  } catch {
    return null;
  }
}

/**
 * Rule 3/4: whether the host's state can be trusted for this seat. Without a
 * reader the events decide; with a reader that failed, returned an incomplete
 * listing or does not hold the seat, the facts are unavailable and no send is
 * made — a partial read is never taken for an authoritative one.
 */
function hostFactsUnavailable(
  reader: HostStateReader | undefined,
  states: Map<string, HostAgentState> | null,
  seat: string,
): boolean {
  if (reader === undefined) return false;
  return states === null || !states.has(seat);
}

/** The host's state where it was read, else what the events said. */
function inTurnNow(agentId: string, states: Map<string, HostAgentState> | null, tracker: TurnEndedTracker): boolean {
  const state = states === null ? undefined : states.get(agentId);
  return state === undefined ? tracker.inTurn.has(agentId) : state.inTurn;
}

function permissionNow(
  agentId: string,
  states: Map<string, HostAgentState> | null,
  tracker: TurnEndedTracker,
): boolean {
  const state = states === null ? undefined : states.get(agentId);
  return state === undefined ? tracker.permissionWait.has(agentId) : state.permissionWait;
}

function archivedNow(
  agentId: string,
  states: Map<string, HostAgentState> | null,
  tracker: TurnEndedTracker,
): boolean {
  const state = states === null ? undefined : states.get(agentId);
  return state === undefined ? tracker.archived.has(agentId) : state.archived;
}

/**
 * Rule 3: whether any child of this seat is in a turn. A child is an agent whose
 * `paseo.parent-agent-id` label names the seat, as `agents.list` reports it — so a
 * child counted even when its turn began before the plugin loaded. The events'
 * parent links stand in only when the host's state could not be read.
 */
function childrenInTurnNow(
  seat: string,
  states: Map<string, HostAgentState> | null,
  tracker: TurnEndedTracker,
): boolean {
  if (states === null) return childrenInTurn(seat, tracker);
  for (const state of states.values()) {
    if (state.parentAgentId !== seat) continue;
    if (state.inTurn) return true;
  }
  return false;
}

/** Rule 3: why an ended seat turn is not silent, or `null` when it is. */
export function notSilentReason(
  agent: TurnEndedAgent,
  own: readonly unknown[],
  handoff: boolean,
  outcome: string | null | undefined,
  tracker: TurnEndedTracker,
  states: Map<string, HostAgentState> | null = null,
): string | null {
  if (outcome !== "completed") return `outcome-${outcome ?? "unknown"}`;
  if (handoff) return "hand-off";
  if (archivedNow(agent.id, states, tracker)) return "archived";
  if (permissionNow(agent.id, states, tracker)) return "permission-wait";
  if (childrenInTurnNow(agent.id, states, tracker)) return "child-in-turn";
  if (isReplyToWaitingSender(own, tracker, states)) return "reply-to-waiting-sender";
  return null;
}

/**
 * The message a seat is sent back with (rule 4): it begins with the fixed first
 * line, names the seat's role and task, quotes the ended turn's own last line
 * of text or says it had none, says the turn ended with no hand-off, and asks it
 * to go on with its stage or to send what blocks it. It holds no document and
 * keeps the limit.
 */
export function composeSeatWakeMessage(
  agentId: string,
  facts: TurnEndedFacts,
  lastText: TurnLastText,
  helperDispatched = false,
): string {
  const said =
    lastText.kind === "text" ? lastText.text : lastText.kind === "empty" ? "no text" : "unreadable";
  const message =
    `${SEAT_WAKE_FIRST_LINE} ${facts.role} ${agentId} · ${facts.task}\n` +
    `you last said: ${said}\n` +
    "Go on with your stage in this turn, or send what blocks you to your parent with role_message." +
    (helperDispatched
      ? " On this host a helper's result does not come back as a message: collect what your helpers finished and go on."
      : "");
  return message.length <= TURN_ENDED_LINE_LIMIT ? message : message.slice(0, TURN_ENDED_LINE_LIMIT);
}

/** The parent's line for a silent end from the third on, marked with its count (rule 5). */
export function markThirdSilentEnd(line: string, ordinal: number): string {
  const mark = ` · marked: silent end ${ordinal} in a row`;
  const room = Math.max(0, TURN_ENDED_LINE_LIMIT - mark.length);
  return `${line.length <= room ? line : line.slice(0, room)}${mark}`;
}

/** ISO time with the machine's offset, as rule 7's rows carry it. */
export function isoWithOffset(date: Date): string {
  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const hours = String(Math.floor(Math.abs(offsetMinutes) / 60)).padStart(2, "0");
  const minutes = String(Math.abs(offsetMinutes) % 60).padStart(2, "0");
  const local = new Date(date.getTime() + offsetMinutes * 60_000);
  return `${local.toISOString().slice(0, -1)}${sign}${hours}:${minutes}`;
}

/** One rule 7 row, for the file the architect counts. */
export function turnEndRow(
  kind: string,
  facts: TurnEndedFacts,
  agentId: string,
  ordinal: number,
  reason: string,
  at: string,
): TurnEndLogRow {
  return { at, agent: agentId, role: facts.role, task: facts.task, kind, count: ordinal, reason };
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
    if (item === null || item["type"] !== "tool_call") continue;
    if (recogniseArTool(item) !== "role_message") continue;
    if (callWasRefused(item)) continue;
    for (const candidate of candidateRecipients(item)) {
      if (recipientIsParent(candidate, parentId, parent.role, parent.task)) return true;
    }
    // A harness that keeps no arguments in the row names the recipient in the
    // answer instead; only that exact field counts, never the message's prose.
    if (answerRecipient(item) === parentId) return true;
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

/** Rule 3: how far a seat's timeline had grown at the last end this build saw. */
export interface TurnMark {
  readonly length: number;
  readonly key: string | null;
}

/** The identity of one row, so a mark can tell whether the view still lines up. */
function rowKeyOf(item: Record<string, unknown>): string | null {
  for (const field of ["id", "callId", "messageId"]) {
    const value = item[field];
    if (typeof value === "string" && value !== "") return `${String(item["type"])}:${value}`;
  }
  const text = item["text"];
  if (typeof text === "string" && text !== "") return `${String(item["type"])}:${text.slice(0, 80)}`;
  return null;
}

/** The mark a timeline leaves for the next end of the same seat. */
export function markOfTimeline(timeline: readonly unknown[]): TurnMark {
  const last = timeline.length === 0 ? null : asRecord(timeline[timeline.length - 1]);
  return { length: timeline.length, key: last === null ? null : rowKeyOf(last) };
}

/**
 * Rule 3: the rows of the ended turn. A message steered into a running turn adds
 * a second `user_message` row to that turn, so the turn is not cut at the last
 * `user_message`: its rows are those after the seat's previous end as this build
 * saw it. The first end this build sees for a seat begins at the last
 * `user_message`, and rows the host tags with a turn id keep MIK-R172's scoping.
 */
export function turnSlice(
  timeline: readonly unknown[],
  mark: TurnMark | undefined,
  turnId?: string | null,
): { readonly rows: readonly unknown[]; readonly mark: TurnMark } {
  if (!Array.isArray(timeline)) return { rows: timeline, mark: { length: 0, key: null } };
  const nextMark = markOfTimeline(timeline);
  if (typeof turnId === "string" && turnId !== "") {
    const tagged = timeline.filter((entry) => {
      const item = asRecord(entry);
      return item !== null && item["turnId"] === turnId;
    });
    if (tagged.length > 0) return { rows: tagged, mark: nextMark };
  }
  if (mark !== undefined && mark.length <= timeline.length) {
    const previous = asRecord(timeline[mark.length - 1]);
    if (previous !== null && mark.key !== null && rowKeyOf(previous) === mark.key) {
      // An intact boundary scopes to the suffix after it, and an empty suffix is
      // still the turn's rows: an end with nothing appended has no rows of its
      // own, so an earlier turn's hand-off can never answer for it.
      return { rows: timeline.slice(mark.length), mark: nextMark };
    }
  }
  return { rows: turnRows(timeline, turnId), mark: nextMark };
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
/** Remember a child's parent link inside its cap, oldest first. */
function rememberParent(tracker: TurnEndedTracker, child: string, parent: string): void {
  tracker.parentOf.delete(child);
  tracker.parentOf.set(child, parent);
  while (tracker.parentOf.size > TURN_ENDED_PARENT_LIMIT) {
    const oldest = tracker.parentOf.keys().next();
    if (oldest.done === true) break;
    tracker.parentOf.delete(oldest.value);
  }
}

/** Bump the seat's generation, inside its cap: rule 4's invalidation for one seat. */
function bumpSeatEpoch(tracker: TurnEndedTracker, seat: string): void {
  tracker.seatEpoch.delete(seat);
  tracker.seatEpoch.set(seat, (tracker.seatEpoch.get(seat) ?? 0) + 1);
  while (tracker.seatEpoch.size > TURN_ENDED_SILENT_LIMIT) {
    const oldest = tracker.seatEpoch.keys().next();
    if (oldest.done === true) break;
    tracker.seatEpoch.delete(oldest.value);
  }
}

export function noteTurnStarted(
  agent: TurnEndedAgent,
  tracker: TurnEndedTracker,
  now: () => Date = () => new Date(),
): void {
  tracker.inTurn.add(agent.id);
  bumpSeatEpoch(tracker, agent.id);
  if (typeof agent.provider === "string" && agent.provider !== "") {
    rememberProvider(tracker, agent.id, agent.provider);
  }
  tracker.permissionWait.delete(agent.id);
  tracker.archived.delete(agent.id);
  // MIK-R183 rule 4: a turn of the seat beginning inside the wait cancels the
  // send-back and the end is not counted; the row is written for rule 7.
  cancelSeatWake(tracker, agent.id, "a turn of the seat started", now);
  if (tracker.started[tracker.started.length - 1] !== agent.id) {
    const seen = tracker.started.indexOf(agent.id);
    if (seen >= 0) tracker.started.splice(seen, 1);
    tracker.started.push(agent.id);
    while (tracker.started.length > TURN_ENDED_STARTED_LIMIT) tracker.started.shift();
  }
  if (agent.parentAgentId !== null) rememberParent(tracker, agent.id, agent.parentAgentId);
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
  | { readonly kind: "waiting"; readonly seat: string; readonly ordinal: number }
  | { readonly kind: "none"; readonly log?: string };

/** The clock and timers a caller lets the decision use, so tests can drive them. */
export interface TurnEndScheduling {
  readonly now?: () => Date;
  readonly setTimeout?: (callback: () => void, delayMs: number) => unknown;
  readonly clearTimeout?: (handle: unknown) => void;
}

/** The options one `agent.turn_ended` event carries into the decision. */
export interface TurnEndOptions extends TurnEndScheduling {
  /** The host's outcome for the ended turn: `completed`, `failed` or `canceled`. */
  readonly outcome?: string | null;
  /** The ended turn's id, which scopes the rows the decision reads. */
  readonly turnId?: string | null;
  /** Rule 3/4: the host's agent state; absent in a test that drives the events alone. */
  readonly readHost?: HostStateReader;
}

function clockOf(scheduling: TurnEndScheduling): () => Date {
  return scheduling.now ?? (() => new Date());
}

function startTimer(scheduling: TurnEndScheduling, callback: () => void, delayMs: number): unknown {
  if (scheduling.setTimeout !== undefined) return scheduling.setTimeout(callback, delayMs);
  return setTimeout(callback, delayMs);
}

function stopTimer(scheduling: TurnEndScheduling, handle: unknown): void {
  if (handle === undefined) return;
  if (scheduling.clearTimeout !== undefined) scheduling.clearTimeout(handle);
  else clearTimeout(handle as ReturnType<typeof setTimeout>);
}

/** The wait owed to this seat, if one is running. */
export function pendingSeatWake(tracker: TurnEndedTracker, seat: string): SeatWakeWait | undefined {
  return tracker.waits.get(seat);
}

/**
 * Rule 4: cancel a running wait. A turn of the seat beginning inside the wait
 * sends nothing and does not count that end; rule 7 wants the row.
 */
function cancelSeatWake(
  tracker: TurnEndedTracker,
  seat: string,
  reason: string,
  now: () => Date,
  scheduling: TurnEndScheduling = {},
): void {
  const wait = tracker.waits.get(seat);
  if (wait === undefined) return;
  tracker.waits.delete(seat);
  stopTimer(scheduling, wait.timer);
  tracker.append(
    turnEndRow("wait-cancelled", wait.facts, seat, wait.ordinal, reason, isoWithOffset(now())),
  );
  wait.finish();
}

/**
 * Rule 4: the act goes out only when the wait ends with the seat out of a turn,
 * not archived and owing no permission. A send skipped there writes its row and
 * does not count the end (rule 5).
 */
async function deliverSeatWake(
  wait: PendingSeatWake,
  tracker: TurnEndedTracker,
  now: () => Date,
  readHost: HostStateReader | undefined,
): Promise<void> {
  if (tracker.waits.get(wait.seat) !== wait) return;
  tracker.waits.delete(wait.seat);
  const states = await readHostStates(readHost);
  // Rule 4: a turn start or a shutdown between the timer and this act voids it.
  if (tracker.epoch !== wait.epoch || (tracker.seatEpoch.get(wait.seat) ?? 0) !== wait.seatEpoch) return;
  const skipped = hostFactsUnavailable(readHost, states, wait.seat)
    ? "host-state-unavailable"
    : archivedNow(wait.seat, states, tracker)
      ? "archived"
      : permissionNow(wait.seat, states, tracker)
        ? "permission-wait"
        : inTurnNow(wait.seat, states, tracker)
          ? "in-a-turn"
          : null;
  if (skipped !== null) {
    tracker.append(turnEndRow("send-skipped", wait.facts, wait.seat, wait.ordinal, skipped, isoWithOffset(now())));
    return;
  }
  try {
    await wait.deliver();
  } catch (error) {
    // The act writes its own row where it knows more; this is the last resort, and
    // the end is not counted because nothing reached anyone.
    tracker.append(
      turnEndRow(
        "delivery-failed",
        wait.facts,
        wait.seat,
        wait.ordinal,
        error instanceof Error ? error.message : String(error),
        isoWithOffset(now()),
      ),
    );
  }
}

/**
 * Rule 4/5: schedule what a silent end does, after one 60-second wait whatever
 * the count — the seat's message for the first two ends, the parent's marked line
 * from the third on. A helper's result never returns under `hermes acp`, so no
 * end waits longer than another. A turn of the seat beginning inside the wait
 * cancels it through `noteTurnStarted`, and the end is not counted.
 */
function scheduleSeatWake(
  agent: TurnEndedAgent,
  facts: TurnEndedFacts,
  own: readonly unknown[],
  ordinal: number,
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
  scheduling: TurnEndScheduling & { readonly readHost?: HostStateReader },
  deliver: (message: string) => Promise<void>,
): SeatWakeWait | undefined {
  const now = clockOf(scheduling);
  // Rule 4's budget: past the cap this end sheds its own wake rather than grow
  // the fleet's waits, and writes the row rule 7 wants.
  const replacing = tracker.waits.get(agent.id) !== undefined;
  if (!replacing && tracker.waits.size >= TURN_ENDED_WAIT_LIMIT) {
    tracker.append(turnEndRow("send-skipped", facts, agent.id, ordinal, "over-capacity", isoWithOffset(now())));
    return undefined;
  }
  // One wait per seat: a later silent end replaces the one already running and
  // the replaced wait is written as cancelled.
  cancelSeatWake(tracker, agent.id, "a later silent end replaced the wait", now, scheduling);
  let finish!: () => void;
  const done = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const helperDispatched = own.some((entry) => {
    const item = asRecord(entry);
    return item !== null && item["type"] === "tool_call" && dispatchedDelegateTask(item);
  });
  const kind: SeatWakeKind = ordinal <= TURN_ENDED_SENT_BACK_LIMIT ? "seat" : "parent-line";
  const delayMs = TURN_ENDED_WAIT_MS;
  const message =
    kind === "seat" ? composeSeatWakeMessage(agent.id, facts, lastLineOfTurn(own), helperDispatched) : "";
  const wait: PendingSeatWake = {
    seat: agent.id,
    ordinal,
    dueAt: now().getTime() + delayMs,
    message,
    kind,
    facts,
    epoch: tracker.epoch,
    seatEpoch: tracker.seatEpoch.get(agent.id) ?? 0,
    deliver: () => deliver(message),
    timer: undefined,
    done,
    finish,
  };
  wait.timer = startTimer(
    scheduling,
    () => {
      void deliverSeatWake(wait, tracker, now, scheduling.readHost).finally(() => wait.finish());
    },
    delayMs,
  );
  tracker.waits.set(agent.id, wait);
  return wait;
}

/**
 * MIK-R172's line to whoever waits, or nothing when the turn returned to the
 * parent. A mark is added for a silent end from the third on (rule 5).
 */
async function noteParentLine(
  agent: TurnEndedAgent,
  facts: TurnEndedFacts,
  own: readonly unknown[],
  records: Readonly<Record<string, TurnEndedFacts>>,
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
  sequence: number,
  mark?: { readonly ordinal: number; readonly at: string },
): Promise<TurnEndedOutcome> {
  const target = resolveWakeParent(agent, facts, records, tracker);
  if (target === null) return { kind: "none" };
  if (turnHasReturnToParent(own, target.id, target.facts)) return { kind: "none" };
  const base = composeTurnEndedLine(agent.id, facts, lastLineOfTurn(own));
  const line = mark === undefined ? base : markThirdSilentEnd(base, mark.ordinal);
  if (mark !== undefined) {
    tracker.append(turnEndRow("marked-parent-line", facts, agent.id, mark.ordinal, "silent end in a row", mark.at));
  }
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

/**
 * A turn ended: hold one line for the agent that waits for it unless the turn
 * holds a delivered return, the agent waits for a permission, or no recipient
 * is recorded. Then deliver whatever is ready. One line per ended turn,
 * delivered once: a failed send keeps its lines held for the next free moment.
 * An agent the product's agent list does not hold gives no line and is named
 * in the host log once, with the reason.
 *
 * MIK-R183: a leaf seat's completed turn with no hand-off, which rule 3 leaves
 * silent, is sent back to its work after a wait, at most twice in a row; every
 * silent end from the third on gives the parent the line, marked with the
 * count. Every seat message, marked line, cancelled wait and left-alone end is
 * a row in the plugin's own file.
 */
export async function noteTurnEnded(
  agent: TurnEndedAgent,
  timeline: readonly unknown[],
  records: Readonly<Record<string, TurnEndedFacts>>,
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
  options: TurnEndOptions = {},
): Promise<TurnEndedOutcome> {
  const now = clockOf(options);
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
  if (agent.parentAgentId !== null) rememberParent(tracker, agent.id, agent.parentAgentId);
  // Rule 4: this intake belongs to a generation of the seat and of the process; a
  // turn start or a shutdown during the awaits below voids everything it would do.
  const epoch = tracker.epoch;
  const seatEpoch = tracker.seatEpoch.get(agent.id) ?? 0;
  const obsolete = (): boolean =>
    tracker.epoch !== epoch || (tracker.seatEpoch.get(agent.id) ?? 0) !== seatEpoch;
  // The host's state at this end (rule 3), read once beside the events.
  const states = await readHostStates(options.readHost);
  if (obsolete()) return { kind: "none" };
  // The ended turn's own rows: those after this build's previous end of the
  // seat, so a steered message does not split the turn, and an earlier turn's
  // return or text answers for this one no longer (L172-R1-F01).
  const slice = turnSlice(timeline, tracker.turnMarks.get(agent.id), options.turnId);
  tracker.turnMarks.set(agent.id, slice.mark);
  while (tracker.turnMarks.size > TURN_ENDED_SILENT_LIMIT) {
    const oldest = tracker.turnMarks.keys().next();
    if (oldest.done === true) break;
    tracker.turnMarks.delete(oldest.value);
  }
  const own = slice.rows;
  await flushHeldLines(tracker, sender, records);
  if (obsolete()) return { kind: "none" };

  const isSeat = TURN_ENDED_SEAT_ROLES.has(facts.role);
  const handoff = turnHasHandoff(own);
  // Rule 5: a hand-off, or a turn begun by a message this plugin did not send,
  // puts the seat's run back to zero.
  const opening = firstUserMessage(own);
  if (handoff || (opening !== null && !isPluginWakeMessage(opening))) resetSilentEnds(tracker, agent.id);

  const unavailable = hostFactsUnavailable(options.readHost, states, agent.id);
  const reason = isSeat
    ? permissionNow(agent.id, states, tracker)
      ? "permission-wait"
      : notSilentReason(agent, own, handoff, options.outcome, tracker, states) ??
        (unavailable ? "host-state-unavailable" : null)
    : null;
  if (reason !== null) {
    tracker.append(
      turnEndRow("left-alone", facts, agent.id, tracker.silentEnds.get(agent.id) ?? 0, reason, isoWithOffset(now())),
    );
    // A permission wait gives no line at all (MIK-R117, MIK-R172), but rule 7
    // still wants the row that says why the end was left alone.
    if (reason === "permission-wait") return { kind: "none" };
    // Rule 4: without the host's facts the plugin does not send; the parent's line
    // is the safe act, as for any end rule 3 leaves alone.
    return noteParentLine(agent, facts, own, records, tracker, sender, sequence);
  }
  if (!isSeat) {
    if (permissionNow(agent.id, states, tracker)) return { kind: "none" };
    return noteParentLine(agent, facts, own, records, tracker, sender, sequence);
  }

  // A silent end: one 60-second wait, whatever the count. The first two ends wake
  // the seat; from the third on the same wait ends in the parent's marked line.
  const ordinal = nextSilentEnd(tracker, agent.id);
  const markedLine = ordinal > TURN_ENDED_SENT_BACK_LIMIT;
  const wait = scheduleSeatWake(agent, facts, own, ordinal, tracker, sender, options, async (message) => {
    if (markedLine) {
      await noteParentLine(agent, facts, own, records, tracker, sender, sequence, {
        ordinal,
        at: isoWithOffset(now()),
      });
      // Rule 4/8: a boundary during the line's own delivery restores no count.
      if (obsolete()) return;
      rememberSilentEnd(tracker, agent.id, ordinal);
      return;
    }
    try {
      await sender.sendMessage(agent.id, message);
    } catch (error) {
      tracker.append(
        turnEndRow(
          "seat-message-failed",
          facts,
          agent.id,
          ordinal,
          error instanceof Error ? error.message : String(error),
          isoWithOffset(now()),
        ),
      );
      return;
    }
    // Rule 4/8: the send may have awaited across a turn start or a shutdown; a
    // later generation restores no count and writes no seat-message row, so a
    // cleared run cannot be brought back by work that began before the boundary.
    if (obsolete()) return;
    rememberSilentEnd(tracker, agent.id, ordinal);
    tracker.append(turnEndRow("seat-message", facts, agent.id, ordinal, "no hand-off", isoWithOffset(now())));
  });
  // Rule 4's budget shed this end's wake; it keeps its ordinal and sends nothing.
  if (wait === undefined) return { kind: "none" };
  return { kind: "waiting", seat: agent.id, ordinal };
}

/**
 * Rule 8: a reload starts afresh. The plugin's cleanup calls this when it stops,
 * so no wait survives it and every seat's run begins again at zero.
 */
export function cleanupSeatWaits(tracker: TurnEndedTracker, scheduling: TurnEndScheduling = {}): void {
  // Rule 8: the boundary voids in-flight work as well as installed timers.
  tracker.epoch += 1;
  tracker.seatEpoch.clear();
  for (const [seat, wait] of [...tracker.waits]) {
    tracker.waits.delete(seat);
    stopTimer(scheduling, wait.timer);
    wait.finish();
  }
  tracker.silentEnds.clear();
  tracker.turnMarks.clear();
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
