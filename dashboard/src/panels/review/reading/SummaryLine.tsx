// The reading column's summary line (MIK-R89 v2 rule 2; MIK-R86 v2 rule 6), in the approved mockup's
// words (data-authored.js:173, rendered at app.js:321): "1 invariant changed · weaken · 13 changed
// locations in 5 files · 2 files without invariant · 58 unchanged or not comparable, folded · sorted:
// Priority". It counts changed invariant records once (from the outline), the shown subjects' locations
// (from the card read, W1: a location is an entry), and the files without an invariant (W14: the
// lane's two buckets). A part still being read shows "…", and a part that cannot be measured names
// itself as unavailable ("changed invariants: unavailable"), never as a count: nothing here is ever
// an invented zero (INV-QAR0F3).
import { css } from '../../../../styled-system/css';
import type { IntentSummaryRead } from '../../../data/reviewIntentSummary';
import type { LaneRead, ReviewUnexplainedLane } from '../../../data/reviewLane';
import type { ReviewCatalogueRead } from '../../../data/useReviewCatalogue';
import type { ChangeSetModel } from '../changeSetOrder';
import { SORT_LABELS } from '../reviewSelection';
import { laneFiles } from './LaneSection';

const line = css({
  display: 'flex',
  flexWrap: 'wrap',
  gap: '0.3rem 1.2rem',
  paddingBlock: '0.9rem 0.2rem',
  fontSize: '0.78rem',
  color: 'muted',
  '& b': { color: 'ink', fontWeight: 600 },
});

type Count = number | '…' | 'unavailable';

const plural = (count: Count, one: string, many: string) => (count === 1 ? one : many);

// A part that cannot be measured names what is unavailable and is never written as a count.
function Part({
  count,
  children,
  unavailable,
  testId,
}: {
  count: Count;
  children: React.ReactNode;
  unavailable: string;
  testId: string;
}) {
  if (count === 'unavailable')
    return (
      <span data-testid={testId} data-unavailable="">
        {unavailable}: <b>unavailable</b>
      </span>
    );
  return (
    <span data-testid={testId}>
      <b>{count}</b> {children}
    </span>
  );
}

function invariantsCount(model: ChangeSetModel, catalogue: ReviewCatalogueRead): Count {
  if (catalogue.loading) return '…';
  return model.counts.invariantsChanged ?? 'unavailable';
}

function cardCount(model: ChangeSetModel, value: number | undefined): Count {
  if (value !== undefined) return value;
  return model.cards === 'unavailable' ? 'unavailable' : '…';
}

function summaryLaneFiles(summary: IntentSummaryRead): number | undefined {
  const attribution = summary.phase === 'loading' ? undefined : summary.attribution;
  if (attribution?.state !== 'counted' || attribution.unexplained === undefined) return undefined;
  return attribution.unexplained + (attribution.attribution_unknown ?? 0);
}

// W14: the files of the lane's two buckets. The summary read carries the same two counts and answers
// first; the lane read's files stand in when it does not carry them.
function filesWithoutInvariant(
  summary: IntentSummaryRead,
  lane: LaneRead<ReviewUnexplainedLane> | null,
): Count {
  const counted = summaryLaneFiles(summary);
  if (counted !== undefined) return counted;
  if (lane?.phase === 'ready') return laneFiles(lane.value).length;
  if (lane?.phase === 'unavailable' || summary.phase === 'unavailable') return 'unavailable';
  return '…';
}

export function SummaryLine({
  model,
  catalogue,
  summary,
  lane,
}: {
  model: ChangeSetModel;
  catalogue: ReviewCatalogueRead;
  summary: IntentSummaryRead;
  lane: LaneRead<ReviewUnexplainedLane> | null;
}) {
  const invariants = invariantsCount(model, catalogue);
  const files = cardCount(model, model.counts.changedFiles);
  const located = cardCount(model, model.counts.changedLocations);
  const locations = files === 'unavailable' ? files : located;
  const folded = cardCount(model, model.counts.folded);
  const without = filesWithoutInvariant(summary, lane);
  const effects = model.counts.effects.length ? ` · ${model.counts.effects.join(', ')}` : '';
  return (
    <p className={line} data-testid="review-summary">
      <Part count={invariants} unavailable="changed invariants" testId="review-summary-invariants">
        {plural(invariants, 'invariant', 'invariants')} changed{effects}
      </Part>
      <Part count={locations} unavailable="changed locations" testId="review-summary-locations">
        changed {plural(locations, 'location', 'locations')} in {files} {plural(files, 'file', 'files')}
      </Part>
      <Part count={without} unavailable="files without invariant" testId="review-summary-lane">
        {plural(without, 'file', 'files')} without invariant
      </Part>
      <Part count={folded} unavailable="unchanged or not comparable" testId="review-summary-folded">
        unchanged or not comparable, folded
      </Part>
      <span className={css({ opacity: 0.75 })} data-testid="review-summary-sort">
        sorted: {SORT_LABELS[model.sort]}
      </span>
    </p>
  );
}
