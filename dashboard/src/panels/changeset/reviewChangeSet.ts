// Uses the lane answer already held by the reviewer; this view reads no review route.
import type { ChangedFile, TaskChangeset } from '../../data/changeset';
import type { ReviewSourceInventory } from '../../data/review';
import type { LaneRead, ReviewLaneFile, ReviewUnexplainedLane } from '../../data/reviewLane';
export type ReviewChangeFilter = 'all' | 'unexplained' | 'unknown';
export interface ReviewChangePage {
  comparison: number;
  filter: ReviewChangeFilter;
  path?: string;
  kind?: 'code' | 'memory';
}
export interface ReviewChangeContext {
  page: ReviewChangePage;
  lane: LaneRead<ReviewUnexplainedLane> | null;
  inventory: ReviewSourceInventory;
}
export const FILTER_NOTES = {
  unexplained:
    'No recorded realization or proof entry explains these changes: a file has no entry on either side, or the counted hunks meet none.',
  unknown:
    'Whether an entry explains these changes is not established: a memory side was not read, or an entry supplies no range at this content.',
};
export function laneFiles(
  context: ReviewChangeContext,
  filter: ReviewChangeFilter,
): ReviewLaneFile[] | undefined {
  if (context.lane?.phase !== 'ready' || context.lane.value.state === 'unavailable')
    return undefined;
  return filter === 'unexplained'
    ? context.lane.value.unexplained_changes?.files
    : filter === 'unknown'
      ? context.lane.value.unknown_attribution?.files
      : undefined;
}
export function filteredChangeSet(
  data: TaskChangeset,
  context: ReviewChangeContext,
  filter: ReviewChangeFilter,
): TaskChangeset {
  if (filter === 'all') return data;
  const indexed = new Map(data.code.map((file) => [file.path, file]));
  return {
    ...data,
    code: (laneFiles(context, filter) ?? []).flatMap((file) => {
      const changed = indexed.get(file.path);
      return changed ? [changed] : [];
    }),
    memory: [],
  };
}
export function fileBadge(file: ChangedFile, context: ReviewChangeContext): string | undefined {
  if (inventoryNonText(file.path, context.inventory)) return 'non-text';
  if (context.lane?.phase !== 'ready' || context.lane.value.state === 'unavailable')
    return undefined;
  const lane = context.lane.value;
  const row =
    lane.unexplained_changes?.files.find((row) => row.path === file.path) ??
    lane.unknown_attribution?.files.find((row) => row.path === file.path);
  return row ? badgeForLaneFile(row) : undefined;
}
function badgeForLaneFile(row: ReviewLaneFile): string | undefined {
  if (row.content !== 'text' || row.non_text) return 'non-text';
  if (row.bucket === 'unexplained') return 'unexplained';
  if (row.bucket === 'attribution_unknown') return 'unknown';
  if (row.counts.unexplained) return `${row.counts.unexplained} unexplained hunks`;
  if (row.counts.attribution_unknown) return `${row.counts.attribution_unknown} unknown hunks`;
  return undefined;
}

function inventoryNonText(path: string, inventory: ReviewSourceInventory): boolean {
  const entry = inventory.entries.find((entry) => entry.path === path);
  return Boolean(
    entry && (entry.mode_change || ['binary', 'symlink', 'submodule'].includes(entry.content)),
  );
}
