// The review page's reads and its one change-set model (MIK-R89 v2 rule 2, W5, W6). The opening makes
// exactly these review reads (the read cycle makes read 2 and the navigation read 3):
//   1. the summary, `/api/review/intent/summary?comparison=<n>` once read 2 names n (the number on
//      screen, the latest or an older one), so it is never the task entry's own request;
//   2. `/api/review/intent`, the task context (the read cycle);
//   3. the outline, `/api/review/intent/entries` (the navigation's catalogue);
//   4. the leaf-wide trees answer, `/api/review/trees?comparison=<n>`, once read 2 names n;
//   5. the cards of the shown subjects, `/api/review/trees?comparison=<n>&invariants=…`, ⌈i/500⌉
//      reads, started only after read 4 answered or failed (Changed's subjects need its code facts);
//   6. the lane, `/api/review/trees?comparison=<n>&lane=files`, eager.
// The shown subjects of read 5 are the opening's: a selection reads no cards of its own (W4), so the
// set is computed without the selection.
import { useMemo } from 'react';

import type { ReviewPayload } from '../../../data/review';
import { useIntentReviewSummary, type IntentSummaryRead } from '../../../data/reviewIntentSummary';
import { useReviewLane, type LaneRead, type ReviewUnexplainedLane } from '../../../data/reviewLane';
import {
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
import type { ReviewSelection } from '../reviewSelection';
import { useTreeOrder } from '../triageOrderPreference';
import { useTreeView } from '../triageViewPreference';

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
  walked: ChangeSetKeep['walked'];
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
  return { state: 'ready', entries: read.entries, comparison: named, memory: read.memory };
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

export function useChangeSet(input: ChangeSetInput): ChangeSetReads {
  const { repo, master, leaf, catalogue, walked } = input;
  const first = useComparisonNumbers(input.frame, input.chosen);
  const { number, older } = first;
  const summary = useIntentReviewSummary(repo, master, leaf, REVIEW_SUMMARY_FACTS, number, number !== undefined);
  const leafTrees = useReviewTrees(repo, master, leaf, { comparison: number }, number !== undefined);
  const lane = useReviewLane(repo, master, leaf, number);
  const sort = useTreeOrder();
  const view = useTreeView();
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
    }),
    [catalogue.outline, catalogue.entries, read4Settled, leafTrees, number, trees],
  );
  const asked = useMemo(
    () => changeSetModel({ ...base, cards: { state: 'reading' } }, 'priority', view, { selection: null, walked }).shownInvariantIds,
    [base, view, walked],
  );
  const cardRead = useChangeSetCards(repo, master, leaf, number, asked, read4Settled && settledCatalogue(catalogue));
  const cards = useMemo(
    () => modelCards(cardRead, comparison, input.frame !== null, number),
    [cardRead, comparison, input.frame, number],
  );
  const model = useMemo(
    () => changeSetModel({ ...base, cards }, sort, view, { selection: input.selection, walked }),
    [base, cards, sort, view, input.selection, walked],
  );
  return { number, resolved: first.resolved, latest, older, delivered, summary, leafTrees, comparison, lane, cards, model };
}
