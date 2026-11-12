// The review's scope facts, in the places MIK-R89 v2 gives them: the record label in the reading
// column's head and the comparison's identities in Technical details (rule 6). The selected subject's
// family context is its meta line's chip and its provenance fold (9dde2b09). The inventory and the
// code trees are the task's whichever subject is selected; the comparison identity describes one
// subject's read and is replaced by a statement about that subject while it is pending or could not
// be read, so nothing of the previous subject's read stands under the new heading.
import { css } from '../../../styled-system/css';
import type { ReviewPayload } from '../../data/review';
import type { ReviewDeliveredComparison } from '../../data/reviewTrees';

const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.35rem 0' });

// The line's own link, in the page's label style: a button, so following it is the page's own
// comparison choice and never a navigation the server has to interpret (MIK-R195 rule 4).
const link = css({
  background: 'transparent',
  border: 'none',
  color: 'cyan',
  font: 'inherit',
  padding: 0,
  cursor: 'pointer',
  textDecoration: 'underline',
  _hover: { color: 'amber' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber' },
});

export type SubjectReadState = 'pending' | 'unavailable' | null;

// The one line a review states when the comparison on screen is not the pair the leaf delivered
// (MIK-R195 rule 4). It names both identities and nothing else: an earlier record's own count and
// statement were correct for its own pair, so this says which pair is shown, never that it was
// wrong. `unavailable` is a different fact: what the enclosure records cannot be established, so
// the line says that instead of claiming a match. A matching pair states nothing.
export function DeliveredLine({
  delivered,
  onOpen,
}: {
  delivered: Pick<ReviewDeliveredComparison, 'state' | 'statement' | 'comparison'> | null;
  onOpen: () => void;
}) {
  if (delivered === null || delivered.state === 'delivered') return null;
  return (
    <p className={muted} role="status" data-testid="review-delivered-line" data-delivered={delivered.state}>
      {delivered.statement}{' '}
      {delivered.state === 'differs' ? (
        <button type="button" className={link} data-testid="review-delivered-open" onClick={onOpen}>
          {delivered.comparison === undefined
            ? 'Open the delivered comparison'
            : `Open the delivered comparison #${delivered.comparison}`}
        </button>
      ) : null}
    </p>
  );
}

// The delivered pair's exact identities, where the page lists the comparison's own (rule 2: the
// delivered view is identifiable in the technical details). It is a fact about the delivery, not a
// verdict about the pair on screen, so it sits beside the shown comparison rather than replacing it.
export function DeliveredFacts({ delivered }: { delivered: ReviewDeliveredComparison | null | undefined }) {
  if (delivered === null || delivered === undefined) return null;
  if (delivered.state === 'unavailable')
    return (
      <p className={muted} data-testid="review-scope-delivered" data-delivered="unavailable">
        Delivered pair unavailable: {delivered.detail ?? delivered.statement}
      </p>
    );
  return (
    <p className={muted} data-testid="review-scope-delivered" data-delivered={delivered.state}>
      {delivered.state === 'delivered' ? 'This comparison is the delivered pair' : 'Delivered pair'}
      {delivered.comparison === undefined ? '' : ` · #${delivered.comparison}`} · code{' '}
      {shortTree(delivered.code_candidate_tree)} over {shortTree(delivered.code_base_tree)} · memory{' '}
      {shortTree(delivered.memory_candidate_tree)} over {shortTree(delivered.memory_base_tree)}
    </p>
  );
}

// A tree id in the page's short form: its first eight characters, or the absence it recorded.
export function shortTree(tree: string | undefined): string {
  return tree === undefined ? 'not recorded' : tree.slice(0, 8);
}

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
