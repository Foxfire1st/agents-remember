// The two owners' measured states share one line; their complete facts remain openly readable.
// A pending/failed subject read never shows the previous subject's movement facts as its answer.
import { css } from '../../../styled-system/css';
import type { ReviewMovement, ReviewPayload } from '../../data/review';

type Props = {
  payload: Pick<ReviewPayload, 'sync_movement' | 'external_git_movement'>;
  status: 'pending' | 'unavailable' | null;
};
const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.35rem 0' });
const facts = css({ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: '0.72rem' });

function movementState(movement: ReviewMovement | null | undefined, status: Props['status']): string {
  if (status !== null) return status === 'pending' ? 'being read' : 'unavailable';
  return movement?.binding_state ?? 'not-measured';
}

export function ReviewMovementStatus({ payload, status }: Props) {
  return <p className={muted} data-testid="review-movement-status" data-review-content="movement-state">
    Sync movement: {movementState(payload.sync_movement, status)}{' · '}
    External Git movement: {movementState(payload.external_git_movement, status)}
  </p>;
}

export function ReviewMovementFacts({ payload, status }: Props) {
  if (status !== null || (!payload.sync_movement && !payload.external_git_movement)) return null;
  return <pre className={facts} data-testid="review-movement-facts" data-review-content="movement-facts">
    {JSON.stringify({ sync_movement: payload.sync_movement, external_git_movement: payload.external_git_movement }, null, 2)}
  </pre>;
}
