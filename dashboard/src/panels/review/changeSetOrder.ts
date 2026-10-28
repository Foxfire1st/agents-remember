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
// A per-area read keeps Priority ties by id, and Currentness from read 4, so visiting an invariant
// never moves the tree. Complete comparison reads retain the location-count and span ties below.
// Memory currentness: the subject's shown currentness, the least current of its read-4 state and its
// entries' states (read 5; an entry: the least current side that records it), in the order stale,
// unverifiable, unrealized, current, then not measured; ties in Priority's order (MIK-R89 v2 rule 3,
// e9b9d515). The four memory sorts rank a subject by its highest value over all its entries, ties in
// Priority's order (W8). Inside a subject (W9): changed locations, ordered by role (app.js:120), then
// the largest span; unchanged and not comparable locations go to the subject's two groups in the same
// order.
import type { ReviewOutline } from '../../data/review';
import type { ReviewTreeComparison, ReviewTreeEntry, ReviewTreeEntrySide, ReviewSubjectSummary } from '../../data/reviewTrees';
import { blockIdentityOf, blockKey, matchesBlockReference, type BlockIdentity } from './blockIdentity';
import { outlineCounts, outlineGroups, realChange, type OutlineRow } from './changeTriage';
import type { ExplainerCategory, ExplainerGrade } from './explainer/explainerApi';
import type { ExpressionCard } from './focusedCards';
import { meaningBadgeText } from './MeaningEffects';
import { isJudgmentSort, judgmentCategoryOf, judgmentSortFor, MEMORY_SORTS, REVIEW_SORTS, SORT_LABELS, type ReviewSelection, type ReviewSort } from './reviewSelection';
import type { TreeView } from './triageViewPreference';
export type * from './changeSetTypes';
import type { ChangeSetCards, ChangeSetInputs, LocationGradeEntry, LocationJudgment, ChangeSetKeep, LocationRole, EntryCurrentness, SubjectCurrentness, LocationNode, SubjectNode, SubjectGroup, SortOption, ChangeSetCounts, ChangeSetModel } from './changeSetTypes';
export { filterTree, laneFileMatches } from './changeSetFilter';
export { EFFECT_RANK, ROLE_LABEL, ROLE_ORDER, SORT_HINTS, effectRank, changeRank } from './changeSetRanks';
import { EFFECT_ORDER, EFFECT_RANK, NO_EFFECT, CURRENTNESS_RANK, ROLE_LABEL, ROLE_ORDER, SORT_HINTS, effectRank, changeRank } from './changeSetRanks';
import { completeSummary, entrySummary } from './changeSetOverview';

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

// W10: show the producer's full-range line changes, including for bounded/truncated excerpts.
// When that measurement is unavailable, show the named range; excerpts are never a count source.
export function lineCounts(entry: ReviewTreeEntry, identity?: BlockIdentity): string {
  const side = identity ? namedSide(entry, identity) : entry.after;
  const range =
    identity?.range ??
    (side.state === 'resolved' && side.start_line !== undefined && side.end_line !== undefined
      ? { start: side.start_line, end: side.end_line }
      : null);
  const counted = entry.line_changes;
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
  summary?: ReviewSubjectSummary;
}

function memoryValue(sort: ReviewSort, node: LocationNode): number {
  const memory = node.memory;
  if (!memory) return -1;
  if (sort === 'invariants-at-location') return memory.invariants_at_path;
  if (sort === 'incoming-links') return memory.incoming_links;
  if (sort === 'without-proof') return memory.without_proof ? 1 : 0;
  return memory.open_weaknesses;
}

function priorityKey(facts: SubjectFacts, scoped: boolean): number[] {
  const changed = scoped ? [] : facts.entries.filter((node) => node.change === 'changed');
  return [
    effectRank(facts.row.meaning),
    changeRank(facts.row.badge),
    -(facts.summary?.changed ?? changed.length),
    -(facts.summary?.changed_span ?? changed.reduce((total, node) => total + node.span, 0)),
  ];
}

function leastCurrent(facts: SubjectFacts): number {
  if (facts.summary) return CURRENTNESS_RANK[facts.summary.entry_currentness];
  return Math.min(
    CURRENTNESS_RANK['not measured'],
    ...facts.entries.map((node) => CURRENTNESS_RANK[node.currentness]),
  );
}

function sortKey(sort: ReviewSort, facts: SubjectFacts, scoped: boolean): number[] {
  if (sort === 'authored') return [facts.authored];
  const priority = priorityKey(facts, scoped);
  if (sort === 'priority' || isJudgmentSort(sort)) return priority;
  // The state the subject's row shows, then Priority (MIK-R89 v2 rule 3, e9b9d515).
  if (sort === 'currentness') return [CURRENTNESS_RANK[scoped ? facts.invariantState : subjectCurrentness(facts)], ...priority];
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
  cards: SubjectNode['cards'],
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

function readSort(requested: ReviewSort, sorts: readonly SortOption[], complete: boolean): ReviewSort {
  if (MEMORY_SORTS.has(requested) && !complete) return 'priority';
  return sorts.find((option) => option.sort === requested)?.available ? requested : 'priority';
}

function memorySortReason(cards: ChangeSetCards, complete: boolean): string | undefined {
  if (cards.state === 'unavailable') return cards.reason;
  if (cards.state === 'reading') return undefined;
  if (cards.memory?.state === 'unavailable') return cards.memory.reason;
  if (complete && cards.memory?.state !== 'available') return 'the card read gave no memory values';
}

export function sortOptions(
  cards: ChangeSetCards,
  categories: readonly ExplainerCategory[] = [],
  complete = cards.state === 'ready' && !cards.coverage,
  scoped = cards.state === 'ready' && cards.coverage !== undefined,
): SortOption[] {
  const memoryReason = memorySortReason(cards, complete);
  const hints = scoped
    ? { ...SORT_HINTS, priority: 'retire/weaken first, then change kind and id' }
    : SORT_HINTS;
  const deterministic: SortOption[] = REVIEW_SORTS.map((sort) => {
    const unavailable = MEMORY_SORTS.has(sort) ? memoryReason : undefined;
    const pending = MEMORY_SORTS.has(sort) && !complete
      ? cards.state === 'reading' || (cards.state === 'ready' && cards.coverage?.pending.length)
        ? 'Reading memory values for the shown invariants'
        : 'Loads location values for this comparison'
      : undefined;
    const reason = unavailable ?? pending;
    return {
      sort,
      label: SORT_LABELS[sort],
      hint: hints[sort],
      available: unavailable === undefined,
      ...(reason === undefined ? {} : { reason }),
    };
  });
  const judgment: SortOption[] = categories.map((category) => {
    const sort = judgmentSortFor(category.name);
    const scaleHint = category.scale.map((point) => point.grade).join(' › ');
    return {
      sort,
      label: `✦ ${category.name}`,
      hint: category.meaning ? `${category.meaning} (${scaleHint})` : scaleHint || SORT_HINTS[sort],
      available: true,
    };
  });
  return [...deterministic, ...judgment];
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

function entriesOf(inputs: ChangeSetInputs): Map<string, ReviewTreeEntry> {
  const cards = inputs.cards;
  const entries = new Map((inputs.inventory ?? []).map((entry) => [entry.id, entry]));
  if (cards.state === 'ready') {
    for (const entry of cards.entries) entries.set(entry.id, entry);
  }
  return entries;
}

function locationsBySubject(
  inputs: ChangeSetInputs,
  entries: ReadonlyMap<string, ReviewTreeEntry>,
  sort: ReviewSort,
): Map<string, LocationNode[]> {
  const bySubject = new Map<string, LocationNode[]>();
  const cards = inputs.cards;
  const comparison = cards.state === 'ready' ? cards.comparison : inputs.comparison;
  if (!comparison) return bySubject;
  const seen = new Set<string>();
  for (const entry of entries.values()) {
    const node = locationNode(entry, comparison, sort);
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
      summary: completeSummary(inputs.overview, row.selectorId),
    })),
  }));
}

function groupKey(sort: ReviewSort, draft: GroupDraft, members: SubjectFacts[], scoped: boolean): number[] {
  if (sort === 'authored') return [draft.family?.authored ?? Number.MAX_SAFE_INTEGER];
  const keys = members.map((member) => sortKey(sort, member, scoped));
  const family = draft.family;
  // A family whose own record changed ranks by its own change too; "N changed" is its members'.
  if (family && ['added', 'removed', 'changed'].includes(family.row.badge ?? ''))
    keys.push(sortKey(sort, { row: family.row, entries: [], invariantState: 'not measured', authored: 0 }, scoped));
  return keys.length
    ? keys.reduce((best, key) => (compareKeys(key, best) < 0 ? key : best))
    : [Number.MAX_SAFE_INTEGER];
}

function groupTie(draft: GroupDraft, members: SubjectFacts[]): string {
  return members[0]?.row.id ?? draft.family?.row.id ?? '';
}

function scaleIndexOf(
  categories: ReadonlyMap<string, ExplainerCategory>,
  categoryName: string,
  gradeName: string,
): number {
  const category = categories.get(categoryName);
  if (!category) return Number.MAX_SAFE_INTEGER;
  const idx = category.scale.findIndex((point) => point.grade === gradeName);
  return idx >= 0 ? idx : Number.MAX_SAFE_INTEGER;
}

function expandMatchedGrades(matched: readonly ExplainerGrade[]): LocationGradeEntry[] {
  return matched.flatMap((grade) => {
    if (grade.carried && grade.carried.length > 0) {
      return grade.carried.map((item) => ({
        category: item.category || grade.category,
        grade: item.grade,
        reason: item.reason,
        stale: item.stale !== false,
        staleReason: item.staleReason ?? null,
      }));
    }
    return [
      {
        category: grade.category,
        grade: grade.grade,
        reason: grade.reason,
        stale: grade.stale,
        staleReason: grade.staleReason ?? null,
      },
    ];
  });
}

function orderGradeEntries(
  expanded: readonly LocationGradeEntry[],
  categories: ReadonlyMap<string, ExplainerCategory>,
  activeCategory: string | null,
): LocationGradeEntry[] {
  return expanded
    .map((entry, index) => ({ entry, index }))
    .sort((left, right) => {
      const leftActive = left.entry.category === activeCategory ? 0 : 1;
      const rightActive = right.entry.category === activeCategory ? 0 : 1;
      if (leftActive !== rightActive) return leftActive - rightActive;
      if (left.entry.category === right.entry.category) {
        const leftScale = scaleIndexOf(categories, left.entry.category, left.entry.grade);
        const rightScale = scaleIndexOf(categories, right.entry.category, right.entry.grade);
        if (leftScale !== rightScale) return leftScale - rightScale;
      }
      return left.index - right.index;
    })
    .map((item) => item.entry);
}

function formatJudgmentHover(ordered: readonly LocationGradeEntry[]): string {
  return [
    "Explainer's judgment (presenter inference):",
    ...ordered.map(
      (item) =>
        `${item.category}: ${item.grade}${item.stale ? ' (stale)' : ''}${item.reason ? ` — ${item.reason}` : ''}`,
    ),
  ].join('\n');
}

function computeLocationJudgment(
  node: LocationNode,
  categories: ReadonlyMap<string, ExplainerCategory>,
  grades: readonly ExplainerGrade[],
  sort: ReviewSort,
): LocationJudgment | undefined {
  if (!grades.length) return undefined;
  const entryIds = node.card.entries.map((entry) => entry.id);
  const drawnKeys = node.draws.length ? node.draws : [node.key];
  const matched = grades.filter((grade) =>
    matchesBlockReference(node.identity, drawnKeys, entryIds, grade.reference),
  );
  if (!matched.length) return undefined;
  const activeCategory = judgmentCategoryOf(sort);
  const ordered = orderGradeEntries(expandMatchedGrades(matched), categories, activeCategory);
  const hoverText = formatJudgmentHover(ordered);
  const activeEntries =
    activeCategory !== null ? ordered.filter((item) => item.category === activeCategory) : [];

  if (activeCategory !== null && activeEntries.length > 0) {
    const best = activeEntries[0];
    const rank = scaleIndexOf(categories, activeCategory, best.grade);
    const stale = activeEntries.some((item) => item.stale);
    return {
      chipLabel: `✦ ${activeCategory}: ${best.grade}${stale ? ' · stale' : ''}`,
      activeCategory,
      activeGrade: best.grade,
      scaleRank: rank === Number.MAX_SAFE_INTEGER ? 0 : rank,
      stale,
      grades: ordered,
      hoverText,
    };
  }

  const stale = ordered.some((item) => item.stale);
  return {
    chipLabel: `✦ graded${stale ? ' · stale' : ''}`,
    activeCategory,
    activeGrade: null,
    scaleRank: null,
    stale,
    grades: ordered,
    hoverText,
  };
}

function attachJudgmentsAndOrder(
  nodes: readonly LocationNode[],
  categories: ReadonlyMap<string, ExplainerCategory>,
  grades: readonly ExplainerGrade[],
  sort: ReviewSort,
): { changed: LocationNode[]; ungraded: LocationNode[] } {
  const withJudgments = nodes.map((node) => {
    if (!drawsItself(node)) return node;
    const judgment = computeLocationJudgment(node, categories, grades, sort);
    return judgment ? { ...node, judgment } : node;
  });
  if (!isJudgmentSort(sort)) {
    return { changed: withJudgments, ungraded: [] };
  }
  const drawing = withJudgments.filter(drawsItself);
  const followers = withJudgments.filter((node) => !drawsItself(node));
  const graded = drawing
    .map((node, index) => ({ node, index }))
    .filter((item) => item.node.judgment?.scaleRank !== null && item.node.judgment?.scaleRank !== undefined)
    .sort(
      (left, right) =>
        left.node.judgment!.scaleRank! - right.node.judgment!.scaleRank! ||
        left.index - right.index,
    )
    .map((item) => item.node);
  const ungradedRaw = drawing.filter(
    (node) => node.judgment?.scaleRank === null || node.judgment?.scaleRank === undefined,
  );
  const ungraded = ungradedRaw.map((node, index) =>
    index === 0 ? { ...node, ungradedGroupCount: ungradedRaw.length } : node,
  );
  return { changed: [...graded, ...ungraded, ...followers], ungraded };
}

function subjectCountFacts(facts: SubjectFacts, known: boolean, cards: SubjectNode['cards'], count: number) {
  return {
    counts: facts.summary ?? (known ? entrySummary(facts.row.selectorId, facts.entries) : undefined),
    cards: facts.summary || known ? 'ready' as const : cards,
    count: facts.summary?.changed ?? count,
  };
}

function subjectNode(
  facts: SubjectFacts,
  family: string | undefined,
  first: boolean,
  sort: ReviewSort,
  cards: SubjectNode['cards'],
  categories: ReadonlyMap<string, ExplainerCategory>,
  grades: readonly ExplainerGrade[],
  locationsKnown: boolean,
  failure?: string,
): SubjectNode {
  const ordered = [...facts.entries].sort(compareLocations);
  const rawChanged = drawnRegions(ordered.filter((node) => node.change === 'changed'));
  const { changed, ungraded } = first
    ? attachJudgmentsAndOrder(rawChanged, categories, grades, sort)
    : { changed: [], ungraded: [] };
  const unchanged = first
    ? ordered
        .filter((node) => node.change === 'unchanged')
        .map((node) => {
          const judgment = computeLocationJudgment(node, categories, grades, sort);
          return judgment ? { ...node, judgment } : node;
        })
    : [];
  const notComparable = first
    ? ordered
        .filter((node) => node.change === 'undetermined')
        .map((node) => {
          const judgment = computeLocationJudgment(node, categories, grades, sort);
          return judgment ? { ...node, judgment } : node;
        })
    : [];
  const currentness = subjectCurrentness(facts);
  const counted = subjectCountFacts(facts, locationsKnown, cards, rawChanged.length);
  return {
    key: `${family ?? '-'}/${facts.row.selectorId}`,
    row: facts.row,
    first,
    effects: effectsOf(facts.row),
    cards,
    locationsKnown,
    counts: counted.counts,
    changed,
    unchanged,
    notComparable,
    currentness,
    reason: failure ?? subjectReason(facts.row, sort, counted.cards, counted.count, currentness),
    ...(isJudgmentSort(sort)
      ? {
          ungradedChanged: ungraded,
          ...(ungraded.length > 0 ? { ungradedLabel: `Ungraded (${ungraded.length})` } : {}),
        }
      : {}),
  };
}

function familyReason(row: OutlineRow): string {
  return row.badge ?? '';
}

function shownRules(
  view: TreeView,
  keep: ChangeSetKeep,
  entries: ReadonlyMap<string, ReviewTreeEntry>,
): ShownRules {
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
function orderedDrafts(drafts: readonly GroupDraft[], sort: ReviewSort, rules: ShownRules, scoped: boolean): ShownDraft[] {
  const compare = (left: SubjectFacts, right: SubjectFacts) =>
    compareKeys(sortKey(sort, left, scoped), sortKey(sort, right, scoped)) || compareText(left.row.id, right.row.id);
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
      compareKeys(groupKey(sort, left.draft, left.members, scoped), groupKey(sort, right.draft, right.members, scoped)) ||
      compareText(groupTie(left.draft, left.members), groupTie(right.draft, right.members)),
  );
}

function subjectGroups(
  shown: readonly ShownDraft[],
  keep: ChangeSetKeep,
  sort: ReviewSort,
  cards: ChangeSetCards,
  categories: ReadonlyMap<string, ExplainerCategory>,
  grades: readonly ExplainerGrade[],
  inventoryKnown: boolean,
): SubjectGroup[] {
  const placed = new Set<string>();
  const coverage = cards.state === 'ready' ? cards.coverage : undefined;
  const loaded = new Set(coverage?.loaded);
  const pending = new Set(coverage?.pending);
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
      const id = facts.row.selectorId;
      const failure = coverage && Object.hasOwn(coverage.failures, id) ? coverage.failures[id] : undefined;
      const state = !coverage ? cards.state : loaded.has(id) ? 'ready' : pending.has(id) ? 'reading' : failure !== undefined ? 'unavailable' : 'unread';
      return subjectNode(facts, draft.family?.row.selectorId, first, sort, state, categories, grades, inventoryKnown || state === 'ready', state === 'unavailable' ? failure : undefined);
    }),
  }));
}

function modelCounts(
  outline: ReviewOutline | undefined,
  subjects: readonly SubjectNode[],
  ready: boolean,
): ChangeSetCounts {
  const summaries = subjects.map((node) => node.counts);
  const known = ready && summaries.every((summary) => summary !== undefined);
  const changed = summaries.reduce((total, item) => total + (item?.changed ?? 0), 0);
  const folded = summaries.reduce((total, item) => total + (item?.unchanged ?? 0) + (item?.not_comparable ?? 0), 0);
  const paths = summaries.flatMap((item) => item?.changed_paths ?? []);
  return {
    invariantsChanged: outlineCounts(outline)?.invariants,
    effects: changedEffects(outline),
    changedLocations: known ? changed : undefined,
    changedFiles: known ? new Set(paths).size : undefined,
    folded: known ? folded : undefined,
    subjects: subjects.length,
  };
}

const hasChange = (draft: GroupDraft) =>
  realChange(draft.family?.row.badge) || draft.members.some((member) => realChange(member.row.badge));

function completeCards(cards: ChangeSetCards, drafts: readonly GroupDraft[], rules: ShownRules): boolean {
  if (cards.state !== 'ready') return false;
  if (!cards.coverage) return true;
  const loaded = new Set(cards.coverage.loaded);
  return drafts.every((draft) => draft.members.every((member) =>
    !subjectShown(member.row, rules) || loaded.has(member.row.selectorId),
  ));
}

function inventoryKnown(inputs: ChangeSetInputs): boolean {
  return inputs.inventory !== undefined && (inputs.cards.state === 'ready' || inputs.comparison !== undefined);
}

function locationState(inputs: ChangeSetInputs, complete: boolean): ChangeSetCards['state'] {
  if (inventoryKnown(inputs)) return 'ready';
  if (inputs.inventoryProblem !== undefined) return 'unavailable';
  if (complete) return 'ready';
  return inputs.cards.state === 'unavailable' ? 'unavailable' : 'reading';
}

function overviewKnown(inputs: ChangeSetInputs): boolean { return inputs.overview?.state === 'ready'; }
function scopedInputs(inputs: ChangeSetInputs, inventory: boolean): boolean {
  return inputs.cards.state === 'ready' && inputs.cards.coverage !== undefined && !inventory && !overviewKnown(inputs);
}
function knownCounts(inputs: ChangeSetInputs, inventory: boolean, complete: boolean): boolean {
  return inventory || complete || overviewKnown(inputs);
}

export function changeSetModel(
  inputs: ChangeSetInputs,
  requested: ReviewSort,
  view: TreeView,
  keep: ChangeSetKeep,
): ChangeSetModel {
  const outlined = inputs.outline?.state === 'outlined';
  const appliedView: TreeView = outlined ? view : 'all';
  const categoriesList = inputs.categories ?? [];
  const gradesList = inputs.grades ?? [];
  const categoriesMap = new Map(categoriesList.map((cat) => [cat.name, cat]));
  const inventory = inventoryKnown(inputs);
  const scoped = scopedInputs(inputs, inventory);
  const entries = entriesOf(inputs);
  // Complete inventory keeps the tree order stable before any code details have been read.
  const drafts = draftGroups(inputs, keep, locationsBySubject(inputs, entries, requested));
  const rules = shownRules(appliedView, keep, entries);
  const complete = completeCards(inputs.cards, drafts, rules);
  const sorts = sortOptions(inputs.cards, categoriesList, complete, scoped);
  const sort = readSort(requested, sorts, complete);
  const shown = orderedDrafts(drafts, sort, rules, scoped);
  const groups = subjectGroups(
    shown,
    keep,
    sort,
    inputs.cards,
    categoriesMap,
    gradesList,
    inventory,
  );
  const subjects = groups.flatMap((group) => group.subjects.filter((node) => node.first));
  return {
    sort,
    requested,
    view: appliedView,
    outlined,
    cards: inputs.cards.state,
    locations: locationState(inputs, complete),
    groups,
    subjects,
    blocks: subjects.flatMap((node) => node.changed.filter(drawsItself)),
    shownInvariantIds: subjects.map((node) => node.row.selectorId).sort(compareText),
    sorts,
    counts: modelCounts(inputs.outline, subjects, knownCounts(inputs, inventory, complete)),
    noChange: outlined && appliedView === 'changed' && !drafts.some(hasChange),
  };
}
