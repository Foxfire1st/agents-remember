// The review's scope facts, in the places MIK-R89 v2 gives them: the record label in the reading
// column's head and the comparison's identities in Technical details (rule 6). The selected subject's
// family context is its meta line's chip and its provenance fold (9dde2b09). The inventory and the
// code trees are the task's whichever subject is selected; the comparison identity describes one
// subject's read and is replaced by a statement about that subject while it is pending or could not
// be read, so nothing of the previous subject's read stands under the new heading.
import { css } from '../../../styled-system/css';
import type { ReviewPayload } from '../../data/review';

const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.35rem 0' });

export type SubjectReadState = 'pending' | 'unavailable' | null;

// The top line's record label: a historical review says so, and one reconstructed from recorded
// endpoints is labelled as a reconstruction (INV-S26EBC8P); a live review needs no label.
export function recordLabelOf(payload: Pick<ReviewPayload, 'limitations'> | null, history?: 'recorded'): string | null {
  if (payload?.limitations.includes('history:reconstructed-recorded-endpoints'))
    return 'Reconstructed from recorded endpoints';
  return history === 'recorded' ? 'Historical task comparison' : null;
}

function inventoryLabel(payload: Pick<ReviewPayload, 'source'>): string {
  const inventory = payload.source.inventory;
  if (inventory.state === 'unavailable') return 'Source inventory unavailable';
  return `${inventory.listed_total} changed files${inventory.partial ? ' · partial inventory' : ''}`;
}

// The comparison's identities, as Technical details lists them.
export function ScopeRecords({
  payload,
  repo,
  master,
  leaf,
  status,
}: {
  payload: ReviewPayload;
  repo: string;
  master: string;
  leaf: string;
  status: SubjectReadState;
}) {
  const inventory = payload.source.inventory;
  const comparison = comparisonOf(payload, status);
  return (
    <div data-testid="review-scope-records">
      <p className={muted}>
        {repo} · {master} · <span data-testid="review-scope-task">{leaf}</span>
      </p>
      <p className={muted} data-testid="review-scope-comparison">
        {status === null && payload.comparison ? comparison : 'Comparison identity not available'}
      </p>
      <p className={muted} data-testid="review-scope-record">
        {inventoryLabel(payload)}
      </p>
      <p className={muted}>
        Before {inventory.before_code_tree_id ?? 'not recorded'} → after{' '}
        {inventory.after_code_tree_id ?? 'not recorded'}
      </p>
      <p className={muted}>{inventory.command}</p>
    </div>
  );
}

function comparisonOf(payload: ReviewPayload, status: SubjectReadState): string {
  if (status === 'pending') return 'The comparison for the selected subject is being read.';
  if (status === 'unavailable') return 'The comparison for the selected subject could not be read.';
  const { comparison } = payload;
  return comparison
    ? `Comparison ${comparison.reference} · policy ${comparison.policy_version}`
    : 'No knowledge comparison was made for this read.';
}
