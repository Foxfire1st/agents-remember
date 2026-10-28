import { useReviewSearch } from './useReviewSearch';
// The review page's reads and its one change-set model (MIK-R89 v2 rule 2, W5, W6). The opening makes
// exactly these review reads (the read cycle makes read 2 and the navigation read 3):
//   1. the summary, `/api/review/intent/summary?comparison=<n>` once read 2 names n (the number on
//      screen, the latest or an older one), so it is never the task entry's own request;
//   2. `/api/review/intent`, the task context (the read cycle);
//   3. the outline, `/api/review/intent/entries` (the navigation's catalogue);
//   4. the leaf-wide trees answer, `/api/review/trees?comparison=<n>`, once read 2 names n;
//   5. only the selected invariant's cards; a family/overview reads no code. Memory-based sorts
//      explicitly ask for the shown invariants' values. A block link reads its bound owner only.
//   6. the lane, `/api/review/trees?comparison=<n>&lane=files`, eager.
import { useEffect, useLayoutEffect, useMemo, useRef, useSyncExternalStore } from 'react';

import type { ReviewPayload } from '../../../data/review';
import { useIntentReviewSummary, type IntentSummaryRead } from '../../../data/reviewIntentSummary';
import { sendDrawnOrder } from '../../../data/reviewOpen';
import { useReviewLane, type LaneRead, type ReviewUnexplainedLane } from '../../../data/reviewLane';
import { invariantScope, useMoveSettled, useStaleInvariants } from '../../../data/staleInvariants';
import {
  adoptCarriedCards,
  followInvariantMove,
  treeComparisonNumber,
  useChangeSetCards,
  useReviewTrees,
  type ChangeSetCardsRead,
  type ReviewDeliveredComparison,
  type ReviewTreeComparison,
  type ReviewTreesRead,
} from '../../../data/reviewTrees';
import type { ReviewCatalogueRead } from '../../../data/useReviewCatalogue';
import {
  changeSetModel,
  type ChangeSetCards,
  type ChangeSetInputs,
  type ChangeSetKeep,
  type ChangeSetModel,
} from '../changeSetOrder';
import { codeFacts, matchingTreeRead } from '../codeFacts';
import type { ExplainerCategory, ExplainerGrade } from '../explainer/explainerApi';
import { MEMORY_SORTS, type ReviewSelection } from '../reviewSelection';
import { useTreeOrder } from '../triageOrderPreference';
import { useTreeView } from '../triageViewPreference';

interface LeafJudgmentData {
  categories: readonly ExplainerCategory[];
  grades: readonly ExplainerGrade[];
}

const EMPTY_JUDGMENTS: LeafJudgmentData = { categories: [], grades: [] };
const leafJudgments = new Map<string, LeafJudgmentData>();
const leafMoveGenerations = new Map<string, number>();
const judgmentListeners = new Set<() => void>();

const judgmentKey = (repo: string, master: string, leaf: string) => `${repo}/${master}/${leaf}`;

export function bumpLeafMoveGeneration(repo: string, master: string, leaf: string): void {
  const key = judgmentKey(repo, master, leaf);
  leafMoveGenerations.set(key, (leafMoveGenerations.get(key) ?? 0) + 1);
  judgmentListeners.forEach((fn) => fn());
}

export function publishLeafJudgments(
  repo: string,
  master: string,
  leaf: string,
  categories?: readonly ExplainerCategory[],
  grades?: readonly ExplainerGrade[],
): void {
  const key = judgmentKey(repo, master, leaf);
  const nextCats = categories ?? [];
  const nextGrades = grades ?? [];
  const prev = leafJudgments.get(key);
  if (
    prev &&
    JSON.stringify(prev.categories) === JSON.stringify(nextCats) &&
    JSON.stringify(prev.grades) === JSON.stringify(nextGrades)
  ) {
    return;
  }
  if (nextCats.length === 0 && nextGrades.length === 0) {
    leafJudgments.delete(key);
  } else {
    leafJudgments.set(key, { categories: nextCats, grades: nextGrades });
  }
  judgmentListeners.forEach((fn) => fn());
}

export function usePublishLeafJudgments(
  repo: string,
  master: string,
  leaf: string,
  categories?: readonly ExplainerCategory[],
  grades?: readonly ExplainerGrade[],
): void {
  useEffect(() => {
    publishLeafJudgments(repo, master, leaf, categories, grades);
  }, [repo, master, leaf, categories, grades]);
}

function useLeafJudgments(repo: string, master: string, leaf: string): LeafJudgmentData {
  const key = judgmentKey(repo, master, leaf);
  return useSyncExternalStore(
    (listener) => {
      judgmentListeners.add(listener);
      return () => judgmentListeners.delete(listener);
    },
    () => leafJudgments.get(key) ?? EMPTY_JUDGMENTS,
  );
}

// The summary read's facts key: the review page's own, apart from the task entry's projection facts.
const REVIEW_SUMMARY_FACTS = 'review-page';

export interface ChangeSetReads {
  // The comparison on screen, the number the review opens by default (a landed leaf's delivered
  // pair, MIK-R195), the leaf's highest recorded number, and whether another one is shown read-only.
  number: number | undefined;
  resolved: number | undefined;
  latest: number | undefined;
  older: boolean;
  // Whether the comparison on screen is what the leaf delivered, and that pair's identities.
  delivered: ReviewDeliveredComparison | null;
  summary: IntentSummaryRead;
  leafTrees: ReviewTreesRead | null;
  comparison: ReviewTreeComparison | null;
  lane: LaneRead<ReviewUnexplainedLane> | null;
  cards: ChangeSetCards;
  model: ChangeSetModel;
  searching: boolean;
  searchProblem?: string;
  // The invariants the last move changed whose detail at this comparison has not loaded (D576).
  stale: ReadonlySet<string>;
}

export interface ChangeSetInput {
  repo: string;
  master: string;
  leaf: string;
  // The task context's last admitted answer: its `review:trees:<n>` names the latest comparison.
  frame: ReviewPayload | null;
  // The number the reader chose in the comparison selector; null = the latest.
  chosen: number | null;
  catalogue: ReviewCatalogueRead;
  selection: ReviewSelection | null;
  restoring?: ReviewSelection | null;
  query?: string;
  walked: ChangeSetKeep['walked'];
  categories?: readonly ExplainerCategory[];
  grades?: readonly ExplainerGrade[];
}

const settledCatalogue = (catalogue: ReviewCatalogueRead) =>
  !catalogue.loading && (catalogue.entries !== undefined || catalogue.problem !== undefined);

function problemText(problem: { code: string; detail: string }): string {
  return `${problem.code}: ${problem.detail}`;
}

// Read 5 as the model reads it. A read that names no comparison (no invariant was asked) takes the
// leaf-wide answer's; a review that is no tree comparison has no cards at all.
function modelCards(
  read: ChangeSetCardsRead | null,
  comparison: ReviewTreeComparison | null,
  framed: boolean,
  number: number | undefined,
): ChangeSetCards {
  if (number === undefined && framed)
    return { state: 'unavailable', reason: 'Code locations require a measured tree comparison.' };
  if (read === null || read.phase === 'loading') return { state: 'reading' };
  if (read.phase === 'unavailable') return { state: 'unavailable', reason: problemText(read.problem) };
  if (read.phase === 'not-converted')
    return { state: 'unavailable', reason: 'This review is not a tree comparison; no cards apply.' };
  const named = read.comparison ?? comparison;
  if (!named) return { state: 'unavailable', reason: 'The card read named no comparison.' };
  return { state: 'ready', entries: read.entries, comparison: named, memory: read.memory, coverage: read.coverage };
}

// What the page says about delivery when the read that carries it did not answer. A refused or
// failed leaf-wide read used to collapse to `null`, which reads on the page exactly like a matching
// pair: nothing said. It is stated instead, with no link, because the delivered default cannot be
// offered from a comparison whose own read failed (MIK-R195 F07).
const UNREADABLE_DELIVERY: string =
  'The delivery this comparison should be checked against could not be read, so whether it is the pair the leaf delivered cannot be said here.';

function deliveredOf(
  read: ReviewTreesRead | null,
  trees: ReturnType<typeof matchingTreeRead>,
  number: number | undefined,
): ReviewDeliveredComparison | null {
  if (trees) return trees.delivered ?? null;
  if (read?.phase === 'unavailable' && number !== undefined)
    return { state: 'unavailable', statement: UNREADABLE_DELIVERY, shown: number };
  return null;
}

function useComparisonNumbers(frame: ReviewPayload | null, chosen: number | null) {
  // The task context names the comparison the review opens by default; on a landed leaf that is the
  // delivered pair its own resolution bound, not necessarily the highest recorded number (MIK-R195).
  const resolved = frame ? treeComparisonNumber(frame.limitations) : undefined;
  const number = chosen ?? resolved;
  return { resolved, number, older: chosen !== null && resolved !== undefined && chosen !== resolved };
}

function inventoryOf(trees: ReturnType<typeof matchingTreeRead>, settled: boolean) {
  if (trees?.overview) return { overview: trees.overview, inventoryProblem: trees.overview.state === 'unavailable' ? trees.overview.reason : undefined };
  const inventory = trees?.entry_inventory;
  if (inventory?.state === 'ready') return { inventory: inventory.entries };
  return { inventoryProblem: settled ? inventory?.reason ?? 'The comparison location inventory could not be read.' : undefined };
}

function cardFocus(identity: string, target: ReviewSelection | null | undefined) {
  if (target?.kind === 'block' && target.block.kind === 'entry')
    return { identity, entry: target.block.entry, side: target.block.side };
  return { identity };
}

function selectedArea(input: ChangeSetInput) {
  return input.selection ?? input.restoring;
}

function cardInputsReady(sort: import('../reviewSelection').ReviewSort, asked: readonly string[], focus: { entry?: string }, catalogue: ReviewCatalogueRead): boolean {
  // A bound exact owner needs no roster; memory sorts still need the complete roster.
  const focused = !MEMORY_SORTS.has(sort) && (asked.length > 0 || focus.entry !== undefined);
  return focused || settledCatalogue(catalogue);
}

// D576: the latest comparison moving from the one shown to a newer one asks, once, which
// invariants the move changed. Until that answer is in no card is read at the newer one, so an
// unchanged invariant's kept answer is carried to it rather than read again. A comparison the
// reader chose is no move.
function useLatestMove(repo: string, master: string, leaf: string, number: number | undefined, chosen: number | null): boolean {
  const latest = chosen === null ? number : undefined;
  const scope = invariantScope(repo, master, leaf);
  const shown = useRef<{ scope: string; number: number } | null>(null);
  const before = shown.current?.scope === scope ? shown.current.number : undefined;
  const from = latest !== undefined && before !== latest ? before : undefined;
  const settled = useMoveSettled(scope, from, latest);
  useEffect(() => {
    if (latest === undefined) return;
    if (from === undefined || settled) shown.current = { scope, number: latest };
    else void followInvariantMove(repo, master, leaf, from, latest);
  }, [repo, master, leaf, scope, from, latest, settled]);
  return settled;
}

// The answers a settled move carries are adopted under the newer comparison's own key before any
// card read of this commit is asked (layout effects run first).
function useCarriedCards(repo: string, master: string, leaf: string, number: number | undefined, identity: string, comparison: ReviewTreeComparison | null, settled: boolean) {
  useLayoutEffect(() => {
    if (settled && number !== undefined && comparison) adoptCarriedCards(repo, master, leaf, number, identity, comparison);
  }, [repo, master, leaf, number, identity, comparison, settled]);
}

export function useChangeSet(input: ChangeSetInput): ChangeSetReads {
  const { repo, master, leaf, catalogue, walked } = input;
  const first = useComparisonNumbers(input.frame, input.chosen);
  const { number, older } = first;
  const summary = useIntentReviewSummary(repo, master, leaf, REVIEW_SUMMARY_FACTS, number, number !== undefined);
  const moveGen = useSyncExternalStore(
    (listener) => {
      judgmentListeners.add(listener);
      return () => judgmentListeners.delete(listener);
    },
    () => leafMoveGenerations.get(judgmentKey(repo, master, leaf)) ?? 0,
  );
  const leafTrees = useReviewTrees(repo, master, leaf, { comparison: number, view: 'overview' }, number !== undefined, moveGen);
  const search = useReviewSearch(repo, master, leaf, number, input.query, moveGen);
  const lane = useReviewLane(repo, master, leaf, number);
  const sort = useTreeOrder();
  const view = useTreeView();
  const storedJudgments = useLeafJudgments(repo, master, leaf);
  const categories = input.categories ?? storedJudgments.categories;
  const grades = input.grades ?? storedJudgments.grades;
  const trees = matchingTreeRead(leafTrees, number);
  const comparison = trees?.comparison ?? null;
  // The menu lists every number the leaf recorded; the trees answer names the highest of them.
  const latest = trees?.latest ?? first.resolved;
  const delivered = deliveredOf(leafTrees, trees, number);
  const read4Settled = leafTrees !== null && leafTrees.phase !== 'loading';
  const base = useMemo<Omit<ChangeSetInputs, 'cards'>>(
    () => ({
      outline: catalogue.outline,
      catalogue: catalogue.entries ?? [],
      code: read4Settled ? codeFacts(leafTrees, number) : undefined,
      currentness: trees?.currentness,
      comparison: trees?.comparison,
      ...inventoryOf(trees, read4Settled),
      ...search.facts,
      categories,
      grades,
    }),
    [catalogue.outline, catalogue.entries, read4Settled, leafTrees, number, trees, categories, grades, search.facts],
  );
  const target = selectedArea(input);
  const asked = useMemo(() => {
    if (MEMORY_SORTS.has(sort))
      return changeSetModel({ ...base, cards: { state: 'reading' } }, 'priority', view, { selection: null, walked }).shownInvariantIds;
    return target?.kind === 'subject' && target.subject.kind === 'invariant' ? [target.subject.id] : [];
  }, [base, view, walked, sort, target]);
  // The subject payload has a selector-specific digest; it is not the shared code comparison.
  // Only the admitted four-tree binding and explicit movement generation key these card answers.
  const identity = JSON.stringify([comparison, moveGen]);
  const focus = useMemo(() => cardFocus(identity, target), [identity, target]);
  const settled = useLatestMove(repo, master, leaf, number, input.chosen);
  useCarriedCards(repo, master, leaf, number, identity, comparison, settled);
  const cardRead = useChangeSetCards(repo, master, leaf, number, asked, settled && read4Settled && cardInputsReady(sort, asked, focus, catalogue), focus);
  const stale = useStaleInvariants(repo, master, leaf);
  const cards = useMemo(
    () => modelCards(cardRead, comparison, input.frame !== null, number),
    [cardRead, comparison, input.frame, number],
  );
  const model = useMemo(
    () => changeSetModel({ ...base, cards }, sort, view, { selection: input.selection, walked }),
    [base, cards, sort, view, input.selection, walked],
  );
  // The order this page drew the comparison's invariants in steers their background preparation;
  // it is the page's own order, the reader's sort and view included (D585).
  const drawn = model.subjects.map((node) => node.row.selectorId).join('\n');
  useEffect(() => {
    if (number !== undefined && read4Settled && drawn) sendDrawnOrder({ repo, master, leaf }, number, drawn.split('\n'));
  }, [repo, master, leaf, number, read4Settled, drawn]);
  return { number, resolved: first.resolved, latest, older, delivered, summary, leafTrees, comparison, lane, cards, model, searching: search.searching, searchProblem: search.problem, stale };
}
