// The workspace's scope header: which task comparison this is, its source inventory and, behind a
// disclosure, its identities. The inventory and code trees are the task's whichever subject is
// selected; the comparison identity, currentness and family-context lines describe one subject's read
// and are replaced by a statement about the selected subject while it is pending or could not be read.
import { css } from '../../../styled-system/css';
import type { ReviewPayload } from '../../data/review';

const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.35rem 0' });

export function ReviewScopeHeader({
  payload,
  repo,
  master,
  leaf,
  history,
  status,
}: {
  payload: ReviewPayload;
  repo: string;
  master: string;
  leaf: string;
  history?: 'recorded';
  // The selected subject's read when it has not answered; `null` when the payload answers it.
  status: 'pending' | 'unavailable' | null;
}) {
  const inventory = payload.source.inventory;
  const recordLabel = recordLabelOf(payload, history);
  const scope = subjectScopeOf(payload, status);
  return (
    <header
      className={css({
        gridColumn: '1 / -1',
        borderBottom: '1px solid var(--grid)',
        paddingBottom: '0.75rem',
        minWidth: 0,
      })}
      data-testid="review-scope-header"
    >
      <p className={muted} data-testid="review-scope-record">
        {recordLabel}·{' '}
        {inventory.state === 'unavailable'
          ? 'Source inventory unavailable'
          : `${inventory.listed_total} changed files${inventory.partial ? ' · partial inventory' : ''}`}{' '}
        · Read-only
      </p>
      {scope.currentness ? (
        <p className={muted} data-testid="review-currentness-status">
          {scope.currentness}
        </p>
      ) : null}
      <details>
        <summary>Comparison details</summary>
        <p className={muted}>
          {repo} · {master} · <span data-testid="review-scope-task">{leaf}</span>
        </p>
        <p className={muted} data-testid="review-scope-comparison">
          {scope.comparison}
        </p>
        <p className={muted}>
          Before {inventory.before_code_tree_id ?? 'not recorded'} → after{' '}
          {inventory.after_code_tree_id ?? 'not recorded'}
        </p>
        <p className={muted} data-testid="review-scope-families">
          {scope.families}
        </p>
      </details>
    </header>
  );
}

// The scope lines that describe one subject's read: its comparison identity, its currentness and its
// family context. While the selected subject is pending or could not be read they say so instead of
// describing the previous subject's read under the new selection. The inventory and the code trees
// above them are the task's, whichever subject is selected.
function subjectScopeOf(
  payload: ReviewPayload,
  status: 'pending' | 'unavailable' | null,
): { currentness: string | null; comparison: string; families: string } {
  if (status === 'pending')
    return {
      currentness: null,
      comparison: 'The comparison for the selected subject is being read.',
      families: 'Family context is being read for the selected subject.',
    };
  if (status === 'unavailable')
    return {
      currentness: null,
      comparison: 'The comparison for the selected subject could not be read.',
      families: 'No family context was read for the selected subject.',
    };
  const { comparison, family_context: families, staleness } = payload;
  return {
    currentness:
      staleness.state === 'current'
        ? null
        : staleness.state === 'stale'
          ? 'Comparison has changed · refresh before relying on this view.'
          : 'Currentness not measured · inspect the comparison details.',
    comparison: comparison
      ? `Comparison ${comparison.reference} · policy ${comparison.policy_version}`
      : 'No knowledge comparison was made for this read.',
    families: families
      ? `${families.families_returned} of ${families.families_total} family contexts read · ${families.state}`
      : 'Family context was not supplied.',
  };
}

function recordLabelOf(payload: ReviewPayload, history?: 'recorded'): string {
  if (payload.limitations.includes('history:reconstructed-recorded-endpoints'))
    return 'Reconstructed from recorded endpoints';
  return history === 'recorded' ? 'Recorded task comparison' : 'Live task comparison';
}
