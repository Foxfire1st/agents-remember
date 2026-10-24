/**
 * The planned-mode page's payload and its deterministic orders (MIK-R189 rule 5).
 *
 * A planning leaf has no change set, so its review page shows what the task is *meant* to produce:
 * the declared knowledge effects as `planned · <effect>` rows, the packets, the Prepared context and
 * the reference files. This module is the payload's shape plus the pure decisions the view makes --
 * which order the rows are read in, what one row's second line is, and how a scope path is shown --
 * so the component draws and never decides.
 *
 * Only deterministic orders exist here: declaration order, the declared subject, the effect label.
 * A judgment or grade order is deliberately absent; a planned page has nothing to grade
 * (MIK-R187; MIK-R189 rule 5).
 */

/** The payload's one top line, as the product writes it. */
export const PLANNED_TOP_LINE = 'planned · no changes';

/** Every diagram or claim of planned behaviour carries this notice (MIK-R189 rule 6). */
export const INTENDED_NOTICE = 'Intended, not a fact about the code';

/** The page's parts, in reading order; the product's part map admits these ids. */
export const PLANNED_PARTS: ReadonlyArray<{ id: string; title: string }> = [
  { id: 'effects', title: 'Planned effects' },
  { id: 'packets', title: 'Packets' },
  { id: 'prepared-context', title: 'Prepared context' },
  { id: 'reference-files', title: 'Reference files' },
];

export type PlannedSortId = 'declared' | 'subject' | 'effect';

export interface PlannedSort {
  id: PlannedSortId;
  label: string;
  order: string;
}

/** The deterministic sorts the page offers, and only those. */
export const PLANNED_SORTS: ReadonlyArray<PlannedSort> = [
  { id: 'declared', label: 'as declared', order: "the task document's own declaration order" },
  { id: 'subject', label: 'by subject', order: 'the declared subject, alphabetically' },
  { id: 'effect', label: 'by effect', order: 'the effect label, alphabetically' },
];

export interface PlannedRow {
  subject: string;
  kind: 'existing' | 'new';
  effect: string;
  /** `planned · <effect>` — the row's own title. */
  title: string;
  /** An existing subject's current statement at the memory tip. */
  statement: string | null;
  /** A `new:` subject's hand-off label. */
  label: string | null;
  statementState: 'statement' | 'new' | 'unread';
  requirement: string;
  part: string;
}

export interface PlannedScopePath {
  /** The path as the packet wrote it. */
  path: string;
  state: 'resolved' | 'text';
  /** The code tip's own path that answered, when one did. */
  resolvedPath?: string | null;
  /** Whether the resolved path exists at the bound code tip. */
  exists?: boolean;
  /** Navigable address at the bound code tip. */
  tipAddress?: string | null;
}

export interface PlannedPacket {
  ref: string;
  path: string;
  title: string;
  body?: string;
  state: 'rendered' | 'unread';
  part: string;
  scope: PlannedScopePath[];
}

export interface PlannedContext {
  heading: string;
  state: 'present' | 'absent';
  body: string;
  part: string;
}

export interface PlannedFile {
  path: string;
  name: string;
  part: string;
}

export interface PlannedPage {
  mode: 'planned';
  topLine: string;
  intendedNotice: string;
  authority: string;
  binding: { owner: 'planned'; number: number; reference: string; label: string };
  tips: {
    code: string;
    codeTree: string;
    memory: string;
    memoryTree: string;
    recordedAt: string;
  };
  rows: PlannedRow[];
  packets: PlannedPacket[];
  preparedContext: PlannedContext;
  referenceFiles: PlannedFile[];
  sorts: PlannedSort[];
  parts: Array<{ id: string; title: string }>;
  /** Always empty: a planned page offers no action that could start anything. */
  actions: string[];
  modelTurns: 0;
  agentStarts: 0;
  reads: {
    taskDocuments: number;
    knowledgeStatements: number;
    packets: number;
    scopePaths: number;
  };
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const text = (value: unknown): string => (typeof value === 'string' ? value : '');

const count = (value: unknown): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : 0;

const optionalText = (value: unknown): string | null => text(value) || null;

function readStatementState(value: unknown): PlannedRow['statementState'] {
  return value === 'new' || value === 'statement' ? value : 'unread';
}

function readRow(value: unknown): PlannedRow | null {
  if (!isRecord(value)) return null;
  const effect = text(value.effect);
  const subject = text(value.subject);
  if (!effect || !subject) return null;
  return {
    subject,
    kind: value.kind === 'new' ? 'new' : 'existing',
    effect,
    title: text(value.title) || `planned · ${effect}`,
    statement: optionalText(value.statement),
    label: optionalText(value.label),
    statementState: readStatementState(value.statementState),
    requirement: text(value.requirement),
    part: text(value.part) || 'effects',
  };
}

function readRows(value: unknown): PlannedRow[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((row) => {
    const read = readRow(row);
    return read ? [read] : [];
  });
}

function isScopeEntryExisting(entry: Record<string, unknown>, resolvedPath: string | null): boolean {
  if (typeof entry.exists === 'boolean') return entry.exists;
  return entry.state === 'resolved' && Boolean(resolvedPath);
}

function resolveTipAddress(
  entry: Record<string, unknown>,
  resolved: boolean,
  resolvedPath: string | null,
): string | null {
  const explicit = optionalText(entry.tipAddress);
  if (explicit) return explicit;
  return resolved && resolvedPath ? `code-tip:${resolvedPath}` : null;
}

function readScopeEntry(entry: unknown): PlannedScopePath | null {
  if (!isRecord(entry) || !text(entry.path)) return null;
  const path = text(entry.path);
  const resolvedPath =
    optionalText(entry.resolvedPath) ?? (entry.state === 'resolved' ? path : null);
  const resolved = entry.state === 'resolved' && isScopeEntryExisting(entry, resolvedPath);
  return {
    path,
    state: resolved ? 'resolved' : 'text',
    resolvedPath: resolved ? resolvedPath : null,
    exists: resolved,
    tipAddress: resolveTipAddress(entry, resolved, resolvedPath),
  };
}

function readScope(value: unknown): PlannedScopePath[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    const parsed = readScopeEntry(entry);
    return parsed ? [parsed] : [];
  });
}

function readPacket(value: unknown): PlannedPacket | null {
  if (!isRecord(value) || !text(value.path)) return null;
  const body = text(value.body) || text(value.text) || text(value.content);
  return {
    ref: text(value.ref),
    path: text(value.path),
    title: text(value.title),
    ...(body ? { body } : {}),
    state: value.state === 'rendered' ? 'rendered' : 'unread',
    part: text(value.part) || 'packets',
    scope: readScope(value.scope),
  };
}

function readPackets(value: unknown): PlannedPacket[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((packet) => {
    const read = readPacket(packet);
    return read ? [read] : [];
  });
}

function readContext(value: unknown): PlannedContext {
  const context = isRecord(value) ? value : {};
  return {
    heading: text(context.heading) || 'Prepared context',
    state: context.state === 'present' ? 'present' : 'absent',
    body: text(context.body),
    part: text(context.part) || 'prepared-context',
  };
}

function readFile(value: unknown): PlannedFile | null {
  if (!isRecord(value) || !text(value.path)) return null;
  const path = text(value.path);
  return {
    path,
    name: text(value.name) || path.split('/').pop() || '',
    part: text(value.part) || 'reference-files',
  };
}

function readFiles(value: unknown): PlannedFile[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((file) => {
    const read = readFile(file);
    return read ? [read] : [];
  });
}

function readParts(value: unknown): Array<{ id: string; title: string }> {
  if (!Array.isArray(value)) return PLANNED_PARTS.map((part) => ({ ...part }));
  return value.flatMap((part) =>
    isRecord(part) && text(part.id) ? [{ id: text(part.id), title: text(part.title) }] : [],
  );
}

function readBinding(value: unknown): PlannedPage['binding'] {
  const binding = isRecord(value) ? value : {};
  return {
    owner: 'planned',
    number: count(binding.number),
    reference: text(binding.reference),
    label: text(binding.label) || 'planned',
  };
}

function readTips(value: unknown): PlannedPage['tips'] {
  const tips = isRecord(value) ? value : {};
  return {
    code: text(tips.code),
    codeTree: text(tips.codeTree),
    memory: text(tips.memory),
    memoryTree: text(tips.memoryTree),
    recordedAt: text(tips.recordedAt),
  };
}

function readReads(value: unknown): PlannedPage['reads'] {
  const reads = isRecord(value) ? value : {};
  return {
    taskDocuments: count(reads.taskDocuments),
    knowledgeStatements: count(reads.knowledgeStatements),
    packets: count(reads.packets),
    scopePaths: count(reads.scopePaths),
  };
}

/**
 * One host answer as the planned page it claims to be, or null when it is not one.
 *
 * The route answers `{state: "ok", planned: <page>}`; a caller that already unwrapped it may pass
 * the page itself. A refusal (`state: "refused"`) is not a page, so it reads as null and the caller
 * shows its own named problem rather than an empty page.
 */
export function readPlannedPage(answer: unknown): PlannedPage | null {
  const body = isRecord(answer) && isRecord(answer.planned) ? answer.planned : answer;
  if (!isRecord(body) || body.mode !== 'planned') return null;
  return {
    mode: 'planned',
    topLine: text(body.topLine) || PLANNED_TOP_LINE,
    intendedNotice: text(body.intendedNotice) || INTENDED_NOTICE,
    authority: text(body.authority) || 'intended',
    binding: readBinding(body.binding),
    tips: readTips(body.tips),
    rows: readRows(body.rows),
    packets: readPackets(body.packets),
    preparedContext: readContext(body.preparedContext),
    referenceFiles: readFiles(body.referenceFiles),
    // The control is the product's own list, never the host's: a payload cannot add a sort the
    // page does not offer, and a judgment order has no way to reach a planned page.
    sorts: PLANNED_SORTS.map((entry) => ({ ...entry })),
    parts: readParts(body.parts),
    actions: Array.isArray(body.actions) ? body.actions.map((entry) => String(entry)) : [],
    modelTurns: 0,
    agentStarts: 0,
    reads: readReads(body.reads),
  };
}

/** The rows in one deterministic order; a declaration-order sort is the payload's own order. */
export function sortRows(rows: readonly PlannedRow[], sort: PlannedSortId): PlannedRow[] {
  if (sort === 'declared') return [...rows];
  const key = (row: PlannedRow): string => (sort === 'subject' ? row.subject : row.effect);
  // A stable comparison on the key alone: equal keys keep the task document's own order.
  return [...rows]
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const order = key(left.row).localeCompare(key(right.row));
      return order === 0 ? left.index - right.index : order;
    })
    .map((entry) => entry.row);
}

/** A row's second line: the current statement, the hand-off label, or a measured absence. */
export function rowDetail(row: PlannedRow): string {
  if (row.kind === 'new') return row.label ?? row.subject.replace(/^new:/, '');
  if (row.statement) return row.statement;
  return 'no statement was read at the memory tip';
}

/** A scope path as it is shown: an address at the code tip, or the path as plain text. */
export function scopeCaption(entry: PlannedScopePath): string {
  if (entry.state !== 'resolved') return `not at the code tip · ${entry.path}`;
  return `code · ${entry.resolvedPath ?? entry.path}`;
}
