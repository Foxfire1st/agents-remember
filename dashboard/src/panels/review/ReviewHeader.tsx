// The review's task identity and controls, independent of subject reads.
import { css } from '../../../styled-system/css';
import type { ReviewSelectorKind, ReviewHistory, ReviewFailure } from '../../data/review';
import { ReviewKeys } from './ReviewKeys';

export function ReviewHeader({
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  instead,
  history,
  refresh,
  onBack,
}: {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  instead: ReviewFailure | null;
  history?: ReviewHistory;
  refresh?: React.ReactNode;
  onBack: () => void;
}) {
  return (
    <>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '0.5rem',
          alignItems: 'center',
          marginBottom: '0.75rem',
        }}
      >
        <button type="button" onClick={onBack} data-testid="review-back">
          ← back
        </button>
        <strong>Intent review · {leaf}</strong>
        <div
          style={{ color: 'var(--muted)', minWidth: 0, overflowWrap: 'anywhere' }}
          data-testid="review-subject"
        >
          <details data-disclosure="identifiers">
            <summary>Task and subject identifiers</summary>
            {repo} · {master} · {leaf} · {subjectLabel(selectorKind, selectorId, instead)}
          </details>
        </div>
        {refresh}
        <ReviewKeys />
      </div>
      {history === 'recorded' ? (
        <span data-testid="review-history" className={css({ color: 'muted', fontSize: '0.75rem' })}>
          Historical task comparison
        </span>
      ) : null}
    </>
  );
}

function subjectLabel(
  selectorKind: ReviewSelectorKind | undefined,
  selectorId: string | undefined,
  instead: ReviewFailure | null,
): string {
  if (instead !== null) return 'whole task (no subject selected)';
  if (selectorKind && selectorId) return `${selectorKind} ${selectorId}`;
  return 'whole task (no subject selected)';
}
