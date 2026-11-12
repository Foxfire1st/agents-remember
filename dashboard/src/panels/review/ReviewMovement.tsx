// The two movement owners' complete facts (sync movement and external Git movement), as the raw record
// that Technical details shows (MIK-R89 v2 rule 6). Their measured states are the status chip's
// popover rows (top-line/statusChipModel.ts); the JSON wall that once opened the page is gone (UX F5).
// An absent owner value is "not measured" (`exclude_none` omits it), never an empty object.
import type { ReviewPayload } from '../../data/review';

export function movementRecord(
  payload: Pick<ReviewPayload, 'sync_movement' | 'external_git_movement' | 'staleness'> | null,
): Record<string, unknown> {
  if (!payload) return { sync_movement: 'not read', external_git_movement: 'not read' };
  return {
    sync_movement: payload.sync_movement ?? 'not measured',
    external_git_movement: payload.external_git_movement ?? 'not measured',
    staleness: payload.staleness,
  };
}
