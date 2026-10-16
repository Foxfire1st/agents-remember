// Reader content helpers. Task membership, stage and counts come from TaskEntry.
import { requirementAddressFromReference } from '../../data/requirements';
import { taskEntrySelectionKey } from '../../data/taskReference';
import type {
TaskDecisionNode,
TaskEntry,
TaskHeaderNoteNode,
TaskReviewStateNode,
TaskSectionNode,
TaskStepNode,
TaskSummary,
} from '../../types/projection';

export const dirName = (path: string): string =>
  path.split('/').slice(0, -1).filter(Boolean).pop() ?? '';

// Decisions newest-first by their recorded times (MIK-R126 rule 6 under the time-order
// ruling): writers stamp `at` as ISO-8601, the finalizer prepends its newest record, and
// storage order carries no invariant — so both the preview and the whole-log page order
// by parsed time. An unparseable stamp keeps its input position among its peers (stable),
// never inventing an order.
export function orderDecisionsNewestFirst<T extends Pick<TaskDecisionNode, 'at'>>(items: T[]): T[] {
  const stamped = items.map((item, index) => ({ item, index, time: Date.parse(item.at) }));
  stamped.sort((left, right) => {
    const leftBad = Number.isNaN(left.time);
    const rightBad = Number.isNaN(right.time);
    if (leftBad && rightBad) return left.index - right.index;
    if (leftBad) return 1;
    if (rightBad) return -1;
    if (right.time !== left.time) return right.time - left.time;
    return left.index - right.index;
  });
  return stamped.map((entry) => entry.item);
}

export function labelWithId(id: string, title: string): string {
  return id ? `${id} — ${title}` : title;
}

// ---------------------------------------------------------------------------
// MIK-R127 fixed skeletons: one skeleton per form (rule 1), bounds with views
// (rule 6), further notes (rule 7), nothing twice (rule 8). All pure mappings
// over projection data; views and navigation stay in the reader components.
// ---------------------------------------------------------------------------

export type DocForm = 'sprint' | 'master' | 'task';

export const docForm = (doc: Pick<TaskSummary, 'form'>): DocForm => doc.form;

// The first step that is not done (rule 5 marks it); undefined when all are done.
export function firstOpenStepId(steps: Pick<TaskSummary, 'steps'>['steps']): string | undefined {
  return steps.find((step) => step.status !== 'done')?.id;
}

// The review line of a Now row or Review block (rules 4-5): the round and the
// open findings, or null where the document records no review. The open count is
// the projection's count of the whole round: a summary carries only the first
// findings, so the open flags it holds are not the round's.
export function reviewSummary(review: TaskReviewStateNode | undefined): string | null {
  if (!review) return null;
  const findings =
    review.totalFindings > 0
      ? `, ${review.openFindings} of ${review.totalFindings} findings open`
      : '';
  return `round ${review.round}${findings}`;
}

// The intro prose of a structured (`subTasks` / `sharedDecisions`) section: it
// stands with the block it introduces (rule 7), never as a second list.
export function sectionIntro(
  sections: TaskSectionNode[],
  kind: 'subTasks' | 'sharedDecisions',
): string | undefined {
  const body = sections.find((section) => section.kind === kind)?.body;
  return body ? body : undefined;
}

export interface RegisterTable {
  heading: string;
  cols: string[];
  rows: string[][];
}

const TABLE_SEPARATOR = /^\|\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function tableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((cell) => cell.trim());
}

// The first markdown table of a section body in written order, or null. A table
// is a run of `|` lines; the second line may be the `---` separator.
export function parseMarkdownTable(body: string): { cols: string[]; rows: string[][] } | null {
  const lines = body.replace(/\r/g, '').split('\n');
  for (let start = 0; start < lines.length; start += 1) {
    if (!lines[start].trim().startsWith('|')) continue;
    const run: string[] = [];
    for (let index = start; index < lines.length; index += 1) {
      if (!lines[index].trim().startsWith('|')) break;
      run.push(lines[index]);
    }
    if (run.length === 0) continue;
    const cols = tableCells(run[0]);
    const rest = run.slice(1);
    const data = rest.length > 0 && TABLE_SEPARATOR.test(rest[0].trim()) ? rest.slice(1) : rest;
    if (cols.length === 0 || data.length === 0) {
      start += run.length - 1;
      continue;
    }
    return { cols, rows: data.map(tableCells) };
  }
  return null;
}

// A section is a register when its heading names it one and its body parses a
// table with at least one data row (rule 3: the Judgment and Priority registers;
// a "register" section of prose stays a Further note). Canonical registers
// stand in fixed slots — judgment first, priority second — independent of
// author section order (the proposal's sprint register order); any other
// recognized register follows in written order.
function registerSlot(heading: string): number {
  if (/judgment/i.test(heading)) return 0;
  if (/priority/i.test(heading)) return 1;
  return 2;
}

export function registerTables(sections: TaskSectionNode[]): RegisterTable[] {
  const tables: RegisterTable[] = [];
  for (const section of sections) {
    if (!/register/i.test(section.heading)) continue;
    const table = parseMarkdownTable(section.body);
    if (table) tables.push({ heading: section.heading, cols: table.cols, rows: table.rows });
  }
  return tables
    .map((table, index) => ({ table, index }))
    .sort(
      (left, right) =>
        registerSlot(left.table.heading) - registerSlot(right.table.heading) ||
        left.index - right.index,
    )
    .map((entry) => entry.table);
}

export interface FurtherSection {
  heading: string;
  body: string;
  chars: number;
  tables: number;
  rows: number;
}

function countBodyTables(body: string): { tables: number; rows: number } {
  const lines = body.replace(/\r/g, '').split('\n');
  let tables = 0;
  let rows = 0;
  for (let index = 0; index < lines.length; index += 1) {
    if (!lines[index].trim().startsWith('|')) continue;
    let run = 0;
    while (index < lines.length && lines[index].trim().startsWith('|')) {
      run += 1;
      index += 1;
    }
    tables += 1;
    rows += Math.max(
      0,
      run - (run > 1 && TABLE_SEPARATOR.test(lines[index - run + 1].trim()) ? 2 : 1),
    );
  }
  return { tables, rows };
}

// Every free-form section that is not one of the blocks above (rule 7): neither
// a structured section (its intro stands with the block), nor a register (drawn
// as a table), nor a byte-for-byte repeat of the objective (rule 8: not twice).
export function furtherSections(
  doc: Pick<TaskSummary, 'objective' | 'sections'>,
  registers: RegisterTable[],
): FurtherSection[] {
  const registerHeadings = new Set(registers.map((register) => register.heading));
  return doc.sections
    .filter((section) => section.kind === 'freeform')
    .filter((section) => !registerHeadings.has(section.heading))
    .filter((section) => section.body !== doc.objective)
    .map((section) => ({
      heading: section.heading,
      body: section.body,
      chars: section.body.length,
      ...countBodyTables(section.body),
    }));
}

export interface ChainLink {
  form: 'sprint' | 'master';
  text: string;
  title: string;
  targetKey: string;
}

/** Parent membership is served; the reader follows it without reconstructing a tree. */
export function docChain(entry: TaskEntry, tasks: Record<string, TaskEntry>): ChainLink[] {
  const chain: ChainLink[] = [];
  const seen = new Set([entry.key]);
  let reference = entry.parent;
  while (reference) {
    const key = reference;
    if (seen.has(key)) break;
    seen.add(key);
    const parent = tasks[key];
    if (!parent) break;
    chain.unshift({
      form: parent.summary?.form === 'sprint' ? 'sprint' : 'master',
      text: parent.id,
      title: parent.title,
      targetKey: taskEntrySelectionKey(parent.key),
    });
    reference = parent.parent;
  }
  return chain;
}

// A document's id as its parent's page shows it: the part after the parent's
// own id ("260928-MIK-L127" under "260928-MIK" reads "L127"). An id that does
// not begin with the parent's stays whole.
export function shortDocId(id: string, parentId: string | undefined): string {
  if (parentId && id.startsWith(`${parentId}-`) && id.length > parentId.length + 1) {
    return id.slice(parentId.length + 1);
  }
  return id;
}

export interface PrimaryRequirement {
  /** The requirement's id and version, e.g. "MIK-R127@v1". */
  label: string;
  /** The address of its packet inside the task folder, where a line names one. */
  address?: string;
  /** The line it was read from, for the reader who points at the link. */
  text: string;
}

const LEADING_LINK = /^\[([^\]]+)\]\(([^)\s]+)\)/;
const REQUIREMENT_ID = /\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*@v\d+\b/;

// The primary requirement of a document for the head strip (rule 2): its id as
// a link to its packet, never its text. The header note that names it goes
// first; a task without that note is read from its first requirement line. A
// line that names no requirement id gives no fact: its text stands once, under
// Requirements.
export function primaryRequirement(
  doc: Pick<TaskSummary, 'requirements'> & {
    headerNotes?: TaskHeaderNoteNode[];
    form: DocForm;
    kind: string;
    orchestrates: string[];
  },
): PrimaryRequirement | undefined {
  const note = (doc.headerNotes ?? []).find((entry) => entry.label === 'Primary requirement');
  const first = doc.requirements[0];
  const source = note?.value ?? (docForm(doc) === 'task' ? first : undefined);
  if (!source) return undefined;
  const text = first ?? source;
  const linked = LEADING_LINK.exec(source.trim());
  if (linked) return { label: linked[1], address: linked[2], text };
  const id = REQUIREMENT_ID.exec(source)?.[0];
  if (!id) return undefined;
  const address = doc.requirements
    .map((line) => requirementAddressFromReference(line))
    .find((found) => found !== undefined);
  return { label: id, address, text };
}

// Long prose (rule 6): more than eight lines, or more characters than a screen
// holds — a single paragraph can exceed any viewport without one newline.
export const LONG_TEXT_LINES = 8;
export const LONG_TEXT_CHARS = 1000;

export interface BoundedText {
  long: boolean;
  /** The whole text where it is short, else its first lines cut at a word. */
  preview: string;
  /** What the link to the whole text names: "all 12 lines" or "21,021 characters". */
  extent: string;
}

export function boundedText(text: string): BoundedText {
  const lines = text.split('\n');
  const long = lines.length > LONG_TEXT_LINES || text.length > LONG_TEXT_CHARS;
  if (!long) return { long, preview: text, extent: '' };
  let preview = lines.slice(0, LONG_TEXT_LINES).join('\n');
  if (preview.length > LONG_TEXT_CHARS) {
    const cut = preview.slice(0, LONG_TEXT_CHARS);
    const space = cut.lastIndexOf(' ');
    preview = `${cut.slice(0, space > 0 ? space : LONG_TEXT_CHARS)} …`;
  }
  const extent =
    lines.length > LONG_TEXT_LINES ? `all ${lines.length} lines` : `${text.length} characters`;
  return { long, preview, extent };
}

export type StepDisplayState = 'done' | 'current' | 'blocked' | 'next' | 'pending';

// The state a step shows (rule 5): done, in work, blocked, the first one that
// is not done ("next"), or pending.
export function stepDisplayState(
  step: Pick<TaskStepNode, 'id' | 'status'> & Partial<Pick<TaskStepNode, 'substeps'>>,
  firstOpen: string | undefined,
): StepDisplayState {
  if (step.status === 'done') return 'done';
  if (
    step.status === 'inProgress' ||
    step.substeps?.some((item) => item.status === 'inProgress' || item.status === 'done')
  )
    return 'current';
  if (step.status === 'blocked') return 'blocked';
  if (step.id === firstOpen) return 'next';
  return 'pending';
}

// The state a nested step shows: it is never the page's "next" step.
export function substepDisplayState(status: string): StepDisplayState {
  if (status === 'done') return 'done';
  if (status === 'inProgress') return 'current';
  return status === 'blocked' ? 'blocked' : 'pending';
}

// MIK-R128: one mark per major step, matching the producer's major-only count.
export function stepMarkStates(steps: TaskStepNode[]): StepDisplayState[] {
  const firstOpen = firstOpenStepId(steps);
  return steps.map((step) => stepDisplayState(step, firstOpen));
}

// The step a task in work stands at (rule 4): the one in progress, else the
// first that is not done. Undefined where every step is done or none exists.
export function standingStep(
  steps: Array<Pick<TaskStepNode, 'id' | 'status' | 'title'>>,
): { how: 'current' | 'next'; id: string; title: string } | undefined {
  const current = steps.find((step) => step.status === 'inProgress');
  if (current) return { how: 'current', id: current.id, title: current.title };
  const next = steps.find((step) => step.status !== 'done');
  return next ? { how: 'next', id: next.id, title: next.title } : undefined;
}

// A recorded time as the page prints it: day and minute as written, never
// moved into another zone. A stamp that is no ISO time stays as it is.
export function shortWhen(at: string): string {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(at) ? `${at.slice(0, 10)} ${at.slice(11, 16)}` : at;
}
