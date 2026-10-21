// The one change-set model the review page's tree and reading column both render (MIK-R89 v2 rules 2,
// 3, 5, 11 and 13; MIK-R86 v2). Pure: no React, no fetch. Every value is a fact of the reads it is
// given, put in order; nothing here decides a fact.
//
// API
//   changeSetModel(inputs, sort, view, keep) -> ChangeSetModel
//     groups             the tree's and the column's order: one heading per family, or "Without a
//                        family" (family undefined), with its subjects (invariants) beneath it.
//     subjects           the reading column's subjects in display order, each invariant once (a member
//                        of two families shows its locations at its first place, `first`).
//     blocks             the `j`/`k` stops: the drawn changed locations of `subjects` in display order,
//                        one per shared region. The reading column appends its lane hunks (W21).
//                        Locations in groups are no stops.
//     shownInvariantIds  the selector ids read 5 asks for (`/api/review/trees?invariants=`), sorted.
//     sorts              every deterministic sort with whether it is available and why not.
//     counts             the numbers the summary line and the tree heading show.
//   filterTree(model, query) -> TreeFilterResult   the tree's rows under the filter (W12; tree only).
//   laneFileMatches(path, query)                   whether a lane file row passes the filter.
//   lineCounts(entry)                              a location's line counts (W10).
//
// Units (W1, W2). A location is one card-read entry, keyed by `blockKey(blockIdentityOf(entry,
// comparison))`. A subject's changed entries at one region on both sides (`sharedRegion`) are drawn
// as one location row and one card, which lists each of them (MIK-R86 v2 normative bullet 1: one line
// per changed location; MIK-R89 v2's kept v1 rule 1, MIK-R31's shared card). The first of them in the
// subject's order draws them (`draws`); each keeps its own key, identity and ⊕, and every count and
// sort still counts entries. Subjects are invariants. Families and "Without a family" are headings:
// each takes the place of its first member under the sort, and its members keep the sort inside it. A
// family heading holds no locations of its own; a family whose own record changed shows its badge on
// its heading.
//
// Order (rule 3's table). Priority: the recorded effect (several: the highest), the change kind, the
// changed locations, the summed spans of the changed entries, the id. Authored: the outline's order.
// Memory currentness: the subject's shown currentness, the least current of its read-4 state and its
// entries' states (read 5; an entry: the least current side that records it), in the order stale,
// unverifiable, unrealized, current, then not measured; ties in Priority's order (MIK-R89 v2 rule 3,
// e9b9d515). The four memory sorts rank a subject by its highest value over all its entries, ties in
// Priority's order (W8). Inside a subject (W9): changed locations, ordered by role (app.js:120), then
// the largest span; unchanged and not comparable locations go to the subject's two groups in the same
// order.
import type {
  ReviewEntry,
  ReviewFamilyContextEntry,
  ReviewOutline,
  ReviewOutlineMeaning,
} from '../../data/review';
import type {
  InvariantCurrentnessState,
  ReviewCodeChange,
  ReviewEntryMemory,
  ReviewEntryMemoryState,
  ReviewTreeComparison,
  ReviewTreeEntry,
  ReviewTreeEntrySide,
  ReviewTreesResult,
} from '../../data/reviewTrees';
import { blockIdentityOf, blockKey, type BlockIdentity } from './blockIdentity';
import { outlineCounts, outlineGroups, realChange, type OutlineBadge, type OutlineRow } from './changeTriage';
import type { CardChange, ExpressionCard } from './focusedCards';
import { meaningBadgeText } from './MeaningEffects';
import {
  MEMORY_SORTS,
  REVIEW_SORTS,
  SORT_LABELS,
  type ReviewSelection,
  type ReviewSort,
} from './reviewSelection';
import type { TreeView } from './triageViewPreference';

// ---- types (stable names: the reading column codes against them) ----

// Read 5, the cards of the shown subjects, merged over its chunks.
export type ChangeSetCards =
  | { state: 'reading' }
  | { state: 'unavailable'; reason: string }
  | {
      state: 'ready';
      entries: readonly ReviewTreeEntry[];
      comparison: ReviewTreeComparison;
      // The card read's memory values state (`entry_memory`); absent when the answer carries none.
      memory?: ReviewEntryMemoryState;
    };

export interface ChangeSetInputs {
  // Read 3: the outline, and the catalogue for a comparison without one (MIK-R86 v1 rule 11).
  outline: ReviewOutline | undefined;
  catalogue: readonly ReviewEntry[];
  // Read 4's code facts by `invariant_key` (codeFacts.ts `codeFacts`): `null` when read 4 answered
  // without them, `undefined` when it was not read (the walked families' own facts then apply).
  code?: ReadonlyMap<string, ReviewCodeChange> | null;
  // Read 4's leaf-wide currentness of the comparison shown.
  currentness?: ReviewTreesResult['currentness'];
  cards: ChangeSetCards;
}

// What keeps a row shown in Changed beside its real change (MIK-R86 v1 rule 5).
export interface ChangeSetKeep {
  selection: ReviewSelection | null;
  // The walked tree (MIK-R39): walked families stay, a kept one with its `kept` tag.
  walked?: { entries: readonly ReviewFamilyContextEntry[]; kept: ReadonlyMap<string, string> };
}

export type LocationRole =
  | 'primary-authority'
  | 'enforcement'
  | 'propagation-persistence'
  | 'support'
  | 'presentation'
  | 'unclassified'
  | 'proof';

// A location's MIK-R03 currentness: the least current side that records it.
export type EntryCurrentness = 'stale' | 'unverifiable' | 'current' | 'not measured';
export type SubjectCurrentness = InvariantCurrentnessState | 'not measured';

export interface LocationNode {
  // blockKey(identity): one block, one key (URL selection, j/k, ⊕, DOM ids, React keys).
  key: string;
  identity: BlockIdentity;
  entry: ReviewTreeEntry;
  // The card that draws the entry, for the existing card renderer: the entry alone (W1), or every
  // entry of its shared region, the drawing location's first.
  card: ExpressionCard;
  // The keys of the locations this one draws as one row and one card: its own, then those of the
  // entries at its region; empty when the first location of its region draws it.
  draws: string[];
  // The subject invariant's selector id (`invariant_key`).
  subject: string;
  change: CardChange;
  role: LocationRole;
  // The short role word of the row ("enforce"; mockup app.js:24).
  roleLabel: string;
  // The identity side's path, range label ("L12–40", or its state) and span in lines (0 without a
  // range).
  path: string;
  range: string;
  span: number;
  symbol?: string;
  // W10: "+a −r" from a line diff of the two excerpts (a side with no file has no lines), else the
  // range.
  lines: string;
  currentness: EntryCurrentness;
  memory?: ReviewEntryMemory;
  // The tree row's one reason (MIK-R86 v2 rule 3).
  reason: string;
}

export interface SubjectNode {
  // The occurrence: `<family selector id or ->/<invariant selector id>`.
  key: string;
  // The outline row: kind 'invariant', id, selectorId, title, badge, meaning, familyId.
  row: OutlineRow;
  // The invariant's first place in display order: the reading column renders it there, and only
  // there do its locations, groups and blocks appear.
  first: boolean;
  // The recorded effect labels (MIK-R43) when its meaning has steps.
  effects: string[];
  cards: ChangeSetCards['state'];
  changed: LocationNode[];
  unchanged: LocationNode[];
  // The not-comparable group: entries whose change is `undetermined`.
  notComparable: LocationNode[];
  currentness: SubjectCurrentness;
  reason: string;
}

export interface FamilyHeading {
  row: OutlineRow;
  reason: string;
  // The subject the family was last read for, when the walk keeps it (MIK-R39).
  kept?: string;
}

export interface SubjectGroup {
  key: string;
  // Undefined: "Without a family".
  family?: FamilyHeading;
  subjects: SubjectNode[];
}

export interface SortOption {
  sort: ReviewSort;
  label: string;
  hint: string;
  available: boolean;
  reason?: string;
}

export interface ChangeSetCounts {
  // Outline invariants whose own change is added, removed or changed; undefined without an outline.
  invariantsChanged: number | undefined;
  // W11: the distinct effects of the changed invariants, in Priority order.
  effects: string[];
  // Over the shown subjects, from read 5; undefined until it answered.
  changedLocations: number | undefined;
  changedFiles: number | undefined;
  // Unchanged plus not comparable locations of the shown subjects.
  folded: number | undefined;
  subjects: number;
}

export interface ChangeSetModel {
  // The sort applied: a memory sort falls back to Priority while its values are unavailable.
  sort: ReviewSort;
  // The stored choice.
  requested: ReviewSort;
  // The view applied: All without an outline (MIK-R86 v1 rule 11).
  view: TreeView;
  outlined: boolean;
  cards: ChangeSetCards['state'];
  groups: SubjectGroup[];
  subjects: SubjectNode[];
  blocks: LocationNode[];
  shownInvariantIds: string[];
  sorts: SortOption[];
  counts: ChangeSetCounts;
  // Changed view of an outline in which nothing changed: "This leaf changed no recorded meaning."
  noChange: boolean;
}

// A subject's two groups: its unchanged locations and its not comparable (`undetermined`) ones.
export type FoldedGroup = 'unchanged' | 'undetermined';

export interface FilteredSubject {
  node: SubjectNode;
  // The location rows the tree draws under the subject row: one per drawn location (`draws`).
  locations: LocationNode[];
  // The group rows drawn (only where a count is above 0): both, and while filtering only a group
  // holding a location the filter matches.
  groups: FoldedGroup[];
}

export interface FilteredGroup {
  group: SubjectGroup;
  subjects: FilteredSubject[];
}

export interface TreeFilterResult {
  query: string;
  groups: FilteredGroup[];
  // A query is set and nothing matches: "Nothing matches “q”."
  empty: boolean;
}

// ---- ranks ----

export const EFFECT_RANK: Readonly<Record<string, number>> = {
  retire: 0,
  weaken: 0,
  replace: 0,
  strengthen: 1,
  restore: 1,
  introduce: 1,
  split: 1,
  merge: 1,
  clarify: 2,
};
const EFFECT_ORDER = Object.keys(EFFECT_RANK);
const NO_EFFECT = 3;

const CHANGE_RANK: Readonly<Record<string, number>> = {
  removed: 0,
  changed: 1,
  added: 2,
  'code changed': 3,
  joined: 4,
  left: 4,
  unknown: 5,
  unchanged: 6,
};

export const ROLE_ORDER: readonly LocationRole[] = [
  'primary-authority',
  'enforcement',
  'propagation-persistence',
  'support',
  'presentation',
  'unclassified',
  'proof',
];
export const ROLE_LABEL: Readonly<Record<LocationRole, string>> = {
  'primary-authority': 'primary',
  enforcement: 'enforce',
  'propagation-persistence': 'persist',
  support: 'support',
  presentation: 'present',
  unclassified: '—',
  proof: 'proof',
};

const CURRENTNESS_RANK: Readonly<Record<SubjectCurrentness, number>> = {
  stale: 0,
  unverifiable: 1,
  unrealized: 2,
  current: 3,
  'not measured': 4,
};

// The Sort popup's short reasons (mockup app.js:243-246). Each of the first three ends its one-line row
// in the popup's 350 px (d9a1ec8f item 4); Memory currentness's order is `CURRENTNESS_RANK`.
export const SORT_HINTS: Readonly<Record<ReviewSort, string>> = {
  priority: 'retire/weaken first, then size',
  authored: 'order the curator wrote',
  currentness: 'least current first',
  'invariants-at-location': 'invariants recorded at the location’s path',
  'incoming-links': 'links into the location’s invariant',
  'without-proof': 'invariants the index lists without proof',
  'open-weaknesses': 'open weaknesses recorded at the location’s path',
};

export function effectRank(meaning: ReviewOutlineMeaning | undefined): number {
  if (meaning?.state !== 'steps') return NO_EFFECT;
  return Math.min(NO_EFFECT, ...meaning.labels.map((label) => EFFECT_RANK[label] ?? NO_EFFECT));
}

export function changeRank(badge: OutlineBadge | undefined): number {
  if (badge === undefined) return CHANGE_RANK.unknown;
  if (badge in CHANGE_RANK) return CHANGE_RANK[badge];
  // A family's "N changed" ranks as changed.
  return /^\d+ changed$/.test(badge) ? CHANGE_RANK.changed : CHANGE_RANK.unknown;
}

// ---- locations ----

function namedSide(entry: ReviewTreeEntry, identity: BlockIdentity): ReviewTreeEntrySide {
  return entry[identity.side];
}

export function locationRole(entry: ReviewTreeEntry, side: ReviewTreeEntrySide): LocationRole {
  if (entry.kind === 'proof') return 'proof';
  const role = side.role === 'incidental' ? 'support' : side.role;
  return ROLE_ORDER.includes(role as LocationRole) && role !== 'proof'
    ? (role as LocationRole)
    : 'unclassified';
}

function rangeText(range: BlockIdentity['range'], side: ReviewTreeEntrySide): string {
  if (!range) return side.state === 'absent' ? 'no file' : side.state;
  return range.start === range.end ? `L${range.start}` : `L${range.start}–${range.end}`;
}

// A side's lines: none where it has no file, the excerpt's lines where the whole range was read, and
// undefined where its text is not known (unresolved, unreadable, not read or cut).
function excerptLines(side: ReviewTreeEntrySide): string[] | undefined {
  if (side.state === 'absent') return [];
  if (side.state !== 'resolved' || side.excerpt === undefined || side.excerpt_truncated) return undefined;
  const text = side.excerpt.endsWith('\n') ? side.excerpt.slice(0, -1) : side.excerpt;
  return text === '' ? [] : text.split('\n');
}

// The two lists without their common head and tail.
function trimCommon(before: readonly string[], after: readonly string[]): [string[], string[]] {
  let head = 0;
  while (head < before.length && head < after.length && before[head] === after[head]) head += 1;
  let tail = 0;
  const rest = Math.min(before.length, after.length) - head;
  while (tail < rest && before[before.length - 1 - tail] === after[after.length - 1 - tail]) tail += 1;
  return [before.slice(head, before.length - tail), after.slice(head, after.length - tail)];
}

// The furthest point on diagonal k after one more edit, then along its run of common lines.
function extend(furthest: number[], offset: number, k: number, d: number, a: readonly string[], b: readonly string[]): number {
  const down = k === -d || (k !== d && furthest[offset + k - 1] < furthest[offset + k + 1]);
  let x = down ? furthest[offset + k + 1] : furthest[offset + k - 1] + 1;
  while (x < a.length && x - k < b.length && a[x] === b[x - k]) x += 1;
  return x;
}

// Myers' O(ND) walk: the least number of inserted plus removed lines, or undefined past `limit`.
function editDistance(a: readonly string[], b: readonly string[], limit: number): number | undefined {
  const offset = a.length + b.length;
  const furthest = new Array<number>(2 * offset + 2).fill(0);
  for (let d = 0; d <= Math.min(offset, limit); d += 1) {
    for (let k = -d; k <= d; k += 2) {
      const x = extend(furthest, offset, k, d, a, b);
      furthest[offset + k] = x;
      if (x >= a.length && x - k >= b.length) return d;
    }
  }
  return undefined;
}

// Inserted and removed lines between two line lists. `undefined` when the edit distance exceeds
// `limit`, which no bounded excerpt meets.
export function lineChanges(
  before: readonly string[],
  after: readonly string[],
  limit = 4000,
): { added: number; removed: number } | undefined {
  const [a, b] = trimCommon(before, after);
  if (a.length === 0 || b.length === 0) return { added: b.length, removed: a.length };
  const distance = editDistance(a, b, limit);
  if (distance === undefined) return undefined;
  // d edits: every common line is matched, so the rest of each side is the change.
  const common = (a.length + b.length - distance) / 2;
  return { added: b.length - common, removed: a.length - common };
}

// W10: a location row's line counts (MIK-R86 v2 rule 2). "+a −r" from a line diff of the entry's two
// excerpts, a side with no file counting as no lines (an added file's lines are all added); when a
// side's text is not known (`excerptLines`), the span of the named side ("L s–e").
export function lineCounts(entry: ReviewTreeEntry, identity?: BlockIdentity): string {
  const side = identity ? namedSide(entry, identity) : entry.after;
  const range =
    identity?.range ??
    (side.state === 'resolved' && side.start_line !== undefined && side.end_line !== undefined
      ? { start: side.start_line, end: side.end_line }
      : null);
  const before = excerptLines(entry.before);
  // An unchanged entry carries its text on the after side only.
  const after = excerptLines(entry.after);
  const counted = before && after ? lineChanges(before, after) : undefined;
  return counted ? `+${counted.added} −${counted.removed}` : rangeText(range, side);
}

function entryCurrentness(entry: ReviewTreeEntry): EntryCurrentness {
  let least: EntryCurrentness = 'not measured';
  for (const side of [entry.before, entry.after]) {
    if (!side.recorded || !side.currentness) continue;
    if (CURRENTNESS_RANK[side.currentness] < CURRENTNESS_RANK[least]) least = side.currentness;
  }
  return least;
}

function oneEntryCard(key: string, entry: ReviewTreeEntry, path: string): ExpressionCard {
  return {
    key,
    kind: entry.kind,
    path,
    change: entry.change,
    before: entry.before,
    after: entry.after,
    entries: [entry],
  };
}

function locationNode(
  entry: ReviewTreeEntry,
  comparison: ReviewTreeComparison,
  sort: ReviewSort,
): LocationNode {
  const identity = blockIdentityOf(entry, comparison);
  const key = blockKey(identity);
  const side = namedSide(entry, identity);
  const role = locationRole(entry, side);
  const lines = lineCounts(entry, identity);
  const currentness = entryCurrentness(entry);
  return {
    key,
    identity,
    entry,
    card: oneEntryCard(key, entry, identity.path),
    draws: [key],
    subject: entry.invariant_key,
    change: entry.change,
    role,
    roleLabel: ROLE_LABEL[role],
    path: identity.path,
    range: rangeText(identity.range, side),
    span: identity.range ? identity.range.end - identity.range.start + 1 : 0,
    symbol: side.symbol ?? entry.after.symbol ?? entry.before.symbol,
    lines,
    currentness,
    memory: entry.memory,
    reason: sort === 'currentness' ? currentness : `${ROLE_LABEL[role]} · ${lines}`,
  };
}

// The region a changed entry names on both sides: its kind and each side's path with its resolved
// lines, or the path where the side has no file. A side that is no region (unresolved, unreadable)
// names nothing that another entry could share, so its entry is drawn alone.
function sharedRegion(entry: ReviewTreeEntry): string | undefined {
  const region = (side: ReviewTreeEntrySide) => {
    if (side.state === 'absent') return `${side.path}:absent`;
    if (side.state !== 'resolved' || side.start_line === undefined || side.end_line === undefined) return undefined;
    return `${side.path}:${side.start_line}-${side.end_line}`;
  };
  const before = region(entry.before);
  const after = region(entry.after);
  return before && after ? [entry.kind, before, after].join('\u0000') : undefined;
}

// A subject's changed locations, in order, with each shared region drawn by its first location: that
// one's card lists every entry of the region and its `draws` names their keys; the others draw nothing.
function drawnRegions(changed: readonly LocationNode[]): LocationNode[] {
  const regions = new Map<string, LocationNode[]>();
  for (const node of changed) {
    const region = sharedRegion(node.entry);
    if (region === undefined) continue;
    const nodes = regions.get(region);
    if (nodes) nodes.push(node);
    else regions.set(region, [node]);
  }
  const shared = new Map<string, LocationNode>();
  for (const nodes of regions.values()) {
    if (nodes.length < 2) continue;
    const [first] = nodes;
    const card = { ...first.card, entries: nodes.map((node) => node.entry) };
    const lead = { ...first, card, draws: nodes.map((node) => node.key) };
    for (const node of nodes) shared.set(node.key, node === first ? lead : { ...node, card, draws: [] });
  }
  return changed.map((node) => shared.get(node.key) ?? node);
}

// Whether a location draws its own row and card (its region's first location does not draw it).
export const drawsItself = (node: LocationNode): boolean => node.draws.length > 0;

// W9: changed first (the groups hold the rest), then role, then the largest span, then path and id.
function compareLocations(left: LocationNode, right: LocationNode): number {
  return (
    ROLE_ORDER.indexOf(left.role) - ROLE_ORDER.indexOf(right.role) ||
    right.span - left.span ||
    compareText(left.path, right.path) ||
    compareText(left.entry.id, right.entry.id)
  );
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

// ---- subjects ----

interface SubjectFacts {
  row: OutlineRow;
  entries: LocationNode[];
  invariantState: SubjectCurrentness;
  authored: number;
}

function memoryValue(sort: ReviewSort, node: LocationNode): number {
  const memory = node.memory;
  if (!memory) return -1;
  if (sort === 'invariants-at-location') return memory.invariants_at_path;
  if (sort === 'incoming-links') return memory.incoming_links;
  if (sort === 'without-proof') return memory.without_proof ? 1 : 0;
  return memory.open_weaknesses;
}

function priorityKey(facts: SubjectFacts): number[] {
  const changed = facts.entries.filter((node) => node.change === 'changed');
  return [
    effectRank(facts.row.meaning),
    changeRank(facts.row.badge),
    -changed.length,
    -changed.reduce((total, node) => total + node.span, 0),
  ];
}

function leastCurrent(facts: SubjectFacts): number {
  return Math.min(
    CURRENTNESS_RANK['not measured'],
    ...facts.entries.map((node) => CURRENTNESS_RANK[node.currentness]),
  );
}

function sortKey(sort: ReviewSort, facts: SubjectFacts): number[] {
  if (sort === 'authored') return [facts.authored];
  const priority = priorityKey(facts);
  if (sort === 'priority') return priority;
  // The state the subject's row shows, then Priority (MIK-R89 v2 rule 3, e9b9d515).
  if (sort === 'currentness') return [CURRENTNESS_RANK[subjectCurrentness(facts)], ...priority];
  return [-Math.max(-1, ...facts.entries.map((node) => memoryValue(sort, node))), ...priority];
}

function compareKeys(left: readonly number[], right: readonly number[]): number {
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const difference = (left[index] ?? 0) - (right[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return 0;
}

function invariantState(
  currentness: ChangeSetInputs['currentness'],
  id: string,
): SubjectCurrentness {
  let least: SubjectCurrentness = 'not measured';
  for (const side of [currentness?.before, currentness?.after]) {
    const state = side?.invariants?.find((entry) => entry.id === id)?.state;
    if (state && CURRENTNESS_RANK[state] < CURRENTNESS_RANK[least]) least = state;
  }
  return least;
}

function subjectCurrentness(facts: SubjectFacts): SubjectCurrentness {
  const fromEntries = leastCurrent(facts);
  const fromRead = CURRENTNESS_RANK[facts.invariantState];
  if (fromRead <= fromEntries) return facts.invariantState;
  return (Object.keys(CURRENTNESS_RANK) as SubjectCurrentness[])[fromEntries];
}

function effectsOf(row: OutlineRow): string[] {
  return row.meaning?.state === 'steps' ? [...row.meaning.labels] : [];
}

function subjectReason(
  row: OutlineRow,
  sort: ReviewSort,
  cards: ChangeSetCards['state'],
  changed: number,
  currentness: SubjectCurrentness,
): string {
  if (sort === 'currentness') return currentness;
  if (row.meaning?.state === 'steps' && row.meaning.labels.length) {
    const effect = meaningBadgeText(row.meaning.labels);
    if (cards === 'unavailable') return effect;
    return `${effect} · ${cards === 'ready' ? changed : '…'} loc.`;
  }
  return row.badge ?? '';
}

// ---- shown rows (Changed | All) ----

function selectedInvariant(
  selection: ReviewSelection | null,
  entries: ReadonlyMap<string, ReviewTreeEntry>,
): { id?: string; family?: string } {
  if (selection?.kind === 'subject' && selection.subject.kind === 'invariant')
    return { id: selection.subject.id, family: selection.family };
  if (selection?.kind === 'block' && selection.block.kind === 'entry')
    return { id: entries.get(selection.block.entry)?.invariant_key };
  return {};
}

interface ShownRules {
  all: boolean;
  selected: { id?: string; family?: string };
  selectedFamily?: string;
  walked: ChangeSetKeep['walked'];
}

function subjectShown(row: OutlineRow, rules: ShownRules): boolean {
  if (rules.all || realChange(row.badge)) return true;
  if (rules.selected.id !== row.selectorId) return false;
  return rules.selected.family === undefined || rules.selected.family === row.familyId;
}

function familyShown(row: OutlineRow, members: number, rules: ShownRules): boolean {
  if (rules.all || members > 0 || realChange(row.badge)) return true;
  if (rules.selectedFamily === row.selectorId || rules.selected.family === row.selectorId) return true;
  if (rules.walked?.kept.has(row.selectorId)) return true;
  return rules.walked?.entries.some((entry) => entry.family_id === row.selectorId) ?? false;
}

// ---- the model ----

function readSort(requested: ReviewSort, sorts: readonly SortOption[]): ReviewSort {
  return sorts.find((option) => option.sort === requested)?.available ? requested : 'priority';
}

export function sortOptions(cards: ChangeSetCards): SortOption[] {
  const memoryReason =
    cards.state === 'unavailable'
      ? cards.reason
      : cards.state === 'ready' && cards.memory?.state !== 'available'
        ? (cards.memory?.reason ?? 'the card read gave no memory values')
        : undefined;
  return REVIEW_SORTS.map((sort) => {
    const unavailable = MEMORY_SORTS.has(sort) ? memoryReason : undefined;
    return {
      sort,
      label: SORT_LABELS[sort],
      hint: SORT_HINTS[sort],
      available: unavailable === undefined,
      ...(unavailable === undefined ? {} : { reason: unavailable }),
    };
  });
}

function changedEffects(outline: ReviewOutline | undefined): string[] {
  if (outline?.state !== 'outlined') return [];
  const labels = new Set<string>();
  for (const row of outline.invariants) {
    if (!['added', 'removed', 'changed'].includes(row.change) || row.meaning?.state !== 'steps') continue;
    for (const label of row.meaning.labels) labels.add(label);
  }
  const position = (label: string) => {
    const index = EFFECT_ORDER.indexOf(label);
    return index === -1 ? EFFECT_ORDER.length : index;
  };
  return [...labels].sort(
    (left, right) =>
      (EFFECT_RANK[left] ?? NO_EFFECT) - (EFFECT_RANK[right] ?? NO_EFFECT) ||
      position(left) - position(right),
  );
}

function locationsBySubject(
  cards: ChangeSetCards,
  sort: ReviewSort,
): Map<string, LocationNode[]> {
  const bySubject = new Map<string, LocationNode[]>();
  if (cards.state !== 'ready') return bySubject;
  const seen = new Set<string>();
  for (const entry of cards.entries) {
    const node = locationNode(entry, cards.comparison, sort);
    if (seen.has(node.key)) continue;
    seen.add(node.key);
    const list = bySubject.get(node.subject) ?? [];
    list.push(node);
    bySubject.set(node.subject, list);
  }
  return bySubject;
}

interface GroupDraft {
  key: string;
  family?: { row: OutlineRow; authored: number };
  members: SubjectFacts[];
}

function draftGroups(
  inputs: ChangeSetInputs,
  keep: ChangeSetKeep,
  locations: ReadonlyMap<string, LocationNode[]>,
): GroupDraft[] {
  const groups = outlineGroups(
    inputs.outline,
    inputs.catalogue,
    keep.walked?.entries ?? [],
    'authored',
    inputs.code,
  );
  let authored = 0;
  return groups.map((group, index) => ({
    key: group.family ? `family:${group.family.selectorId}` : `unlisted:${index}`,
    family: group.family ? { row: group.family, authored: authored++ } : undefined,
    members: group.members.map((row) => ({
      row,
      entries: locations.get(row.selectorId) ?? [],
      invariantState: invariantState(inputs.currentness, row.id),
      authored: authored++,
    })),
  }));
}

function groupKey(sort: ReviewSort, draft: GroupDraft, members: SubjectFacts[]): number[] {
  if (sort === 'authored') return [draft.family?.authored ?? Number.MAX_SAFE_INTEGER];
  const keys = members.map((member) => sortKey(sort, member));
  const family = draft.family;
  // A family whose own record changed ranks by its own change too; "N changed" is its members'.
  if (family && ['added', 'removed', 'changed'].includes(family.row.badge ?? ''))
    keys.push(sortKey(sort, { row: family.row, entries: [], invariantState: 'not measured', authored: 0 }));
  return keys.length
    ? keys.reduce((best, key) => (compareKeys(key, best) < 0 ? key : best))
    : [Number.MAX_SAFE_INTEGER];
}

function groupTie(draft: GroupDraft, members: SubjectFacts[]): string {
  return members[0]?.row.id ?? draft.family?.row.id ?? '';
}

function subjectNode(
  facts: SubjectFacts,
  family: string | undefined,
  first: boolean,
  sort: ReviewSort,
  cards: ChangeSetCards['state'],
): SubjectNode {
  const ordered = [...facts.entries].sort(compareLocations);
  const changed = drawnRegions(ordered.filter((node) => node.change === 'changed'));
  const currentness = subjectCurrentness(facts);
  return {
    key: `${family ?? '-'}/${facts.row.selectorId}`,
    row: facts.row,
    first,
    effects: effectsOf(facts.row),
    cards,
    changed: first ? changed : [],
    unchanged: first ? ordered.filter((node) => node.change === 'unchanged') : [],
    notComparable: first ? ordered.filter((node) => node.change === 'undetermined') : [],
    currentness,
    reason: subjectReason(facts.row, sort, cards, changed.length, currentness),
  };
}

function familyReason(row: OutlineRow): string {
  return row.badge ?? '';
}

function shownRules(
  view: TreeView,
  keep: ChangeSetKeep,
  cards: ChangeSetCards,
): ShownRules {
  const entries = new Map(cards.state === 'ready' ? cards.entries.map((entry) => [entry.id, entry]) : []);
  const family = keep.selection?.kind === 'subject' && keep.selection.subject.kind === 'family';
  return {
    all: view === 'all',
    selected: selectedInvariant(keep.selection, entries),
    selectedFamily: family && keep.selection?.kind === 'subject' ? keep.selection.subject.id : undefined,
    walked: keep.walked,
  };
}

interface ShownDraft {
  draft: GroupDraft;
  members: SubjectFacts[];
}

// The active view's rows in the active sort: members inside each group, then the groups, each
// taking the place of its first member.
function orderedDrafts(drafts: readonly GroupDraft[], sort: ReviewSort, rules: ShownRules): ShownDraft[] {
  const compare = (left: SubjectFacts, right: SubjectFacts) =>
    compareKeys(sortKey(sort, left), sortKey(sort, right)) || compareText(left.row.id, right.row.id);
  const shown = drafts
    .map((draft) => {
      const members = draft.members.filter((member) => subjectShown(member.row, rules));
      if (sort !== 'authored') members.sort(compare);
      return { draft, members };
    })
    .filter(({ draft, members }) =>
      draft.family ? familyShown(draft.family.row, members.length, rules) : members.length > 0,
    );
  if (sort === 'authored') return shown;
  return shown.sort(
    (left, right) =>
      compareKeys(groupKey(sort, left.draft, left.members), groupKey(sort, right.draft, right.members)) ||
      compareText(groupTie(left.draft, left.members), groupTie(right.draft, right.members)),
  );
}

function subjectGroups(
  shown: readonly ShownDraft[],
  keep: ChangeSetKeep,
  sort: ReviewSort,
  cards: ChangeSetCards['state'],
): SubjectGroup[] {
  const placed = new Set<string>();
  return shown.map(({ draft, members }) => ({
    key: draft.key,
    family: draft.family
      ? {
          row: draft.family.row,
          reason: familyReason(draft.family.row),
          kept: keep.walked?.kept.get(draft.family.row.selectorId),
        }
      : undefined,
    subjects: members.map((facts) => {
      const first = !placed.has(facts.row.selectorId);
      placed.add(facts.row.selectorId);
      return subjectNode(facts, draft.family?.row.selectorId, first, sort, cards);
    }),
  }));
}

function modelCounts(
  outline: ReviewOutline | undefined,
  subjects: readonly SubjectNode[],
  ready: boolean,
): ChangeSetCounts {
  const blocks = subjects.flatMap((node) => node.changed);
  const folded = subjects.reduce((total, node) => total + node.unchanged.length + node.notComparable.length, 0);
  return {
    invariantsChanged: outlineCounts(outline)?.invariants,
    effects: changedEffects(outline),
    changedLocations: ready ? blocks.length : undefined,
    changedFiles: ready ? new Set(blocks.map((node) => node.path)).size : undefined,
    folded: ready ? folded : undefined,
    subjects: subjects.length,
  };
}

const hasChange = (draft: GroupDraft) =>
  realChange(draft.family?.row.badge) || draft.members.some((member) => realChange(member.row.badge));

export function changeSetModel(
  inputs: ChangeSetInputs,
  requested: ReviewSort,
  view: TreeView,
  keep: ChangeSetKeep,
): ChangeSetModel {
  const outlined = inputs.outline?.state === 'outlined';
  const appliedView: TreeView = outlined ? view : 'all';
  const sorts = sortOptions(inputs.cards);
  const sort = readSort(requested, sorts);
  const drafts = draftGroups(inputs, keep, locationsBySubject(inputs.cards, sort));
  const shown = orderedDrafts(drafts, sort, shownRules(appliedView, keep, inputs.cards));
  const groups = subjectGroups(shown, keep, sort, inputs.cards.state);
  const subjects = groups.flatMap((group) => group.subjects.filter((node) => node.first));
  return {
    sort,
    requested,
    view: appliedView,
    outlined,
    cards: inputs.cards.state,
    groups,
    subjects,
    blocks: subjects.flatMap((node) => node.changed.filter(drawsItself)),
    shownInvariantIds: subjects.map((node) => node.row.selectorId).sort(compareText),
    sorts,
    counts: modelCounts(inputs.outline, subjects, inputs.cards.state === 'ready'),
    noChange: outlined && appliedView === 'changed' && !drafts.some(hasChange),
  };
}

// ---- the filter (W12: id, title, symbol, path, role, effect; the tree only) ----

const needleOf = (query: string) => query.trim().toLowerCase();

function subjectText(node: SubjectNode): string {
  return `${node.row.id} ${node.row.title} ${node.effects.join(' ')}`.toLowerCase();
}

function locationText(node: LocationNode): string {
  return `${node.path} ${node.symbol ?? ''} ${node.role} ${node.roleLabel}`.toLowerCase();
}

const FOLDED: Readonly<Record<FoldedGroup, (node: SubjectNode) => LocationNode[]>> = {
  unchanged: (node) => node.unchanged,
  undetermined: (node) => node.notComparable,
};

// A subject's rows: one per drawn location, and each group row whose count is above 0 (W13).
function unfiltered(node: SubjectNode): FilteredSubject {
  return {
    node,
    locations: node.changed.filter(drawsItself),
    groups: (['unchanged', 'undetermined'] as const).filter((group) => FOLDED[group](node).length > 0),
  };
}

// A subject under the filter: its rows without the group rows when its id, title or effect matches;
// else the rows of the locations that match: a changed one by its row (a row matches when an entry it
// draws does), an unchanged or not comparable one by its group's row, which opens that group in the
// reading column, so a folded location stays reachable without a disclosure in the tree (MIK-R89 v2
// rules 3 and 5; MIK-R86 v2 rules 4 and 9).
function filteredSubject(node: SubjectNode, needle: string): FilteredSubject | undefined {
  const rows = unfiltered(node);
  if (subjectText(node).includes(needle)) return { ...rows, groups: [] };
  const matches = (location: LocationNode) => locationText(location).includes(needle);
  const found = new Set(node.changed.filter(matches).map((location) => location.key));
  const locations = rows.locations.filter((location) => location.draws.some((key) => found.has(key)));
  const groups = rows.groups.filter((group) => FOLDED[group](node).some(matches));
  return locations.length || groups.length ? { node, locations, groups } : undefined;
}

export function filterTree(model: ChangeSetModel, query: string): TreeFilterResult {
  const needle = needleOf(query);
  if (needle === '')
    return {
      query,
      groups: model.groups.map((group) => ({ group, subjects: group.subjects.map(unfiltered) })),
      empty: false,
    };
  const groups = model.groups.flatMap((group): FilteredGroup[] => {
    const family = group.family?.row;
    // A family that matches is shown with its members of the view (MIK-R86 v1 rule 9).
    if (family && `${family.id} ${family.title}`.toLowerCase().includes(needle))
      return [{ group, subjects: group.subjects.map((node) => ({ ...unfiltered(node), groups: [] })) }];
    const subjects = group.subjects.flatMap((node) => filteredSubject(node, needle) ?? []);
    return subjects.length ? [{ group, subjects }] : [];
  });
  return { query, groups, empty: groups.length === 0 };
}

// A lane file row matches on its path and on "code without invariant" (mockup app.js:227).
export function laneFileMatches(path: string, query: string): boolean {
  const needle = needleOf(query);
  return needle === '' || `${path} code without invariant`.toLowerCase().includes(needle);
}
