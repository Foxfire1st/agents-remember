// Uses the lane answer already held by the reviewer; this view reads no review route.
import type { ChangedFile, TaskChangeset } from '../../data/changeset';
import type { ReviewSourceInventory } from '../../data/review';
import type { ReviewWorklistView, ReviewWorklistSummary } from '../../data/reviewTrees';
import { fileAnswers } from '../review/worklistState';
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
  worklist?: ReviewWorklistView;
  worklistSummary?: ReviewWorklistSummary;
}
export const FILTER_NOTES = {
  unexplained:
    'No recorded realization or proof entry explains these changes: a file has no entry on either side, or the counted hunks meet none.',
  unknown:
    'Whether an entry explains these changes is not established: a memory side was not read, or an entry supplies no range at this content. No entry covers these lines yet; curation attaches them to an invariant or records that none applies.',
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
  return reviewFileBadges(context).get(file.path);
}

// Build once per rendered list, not once per file: both populations may be the whole leaf.
export function reviewFileBadges(context: ReviewChangeContext): Map<string, string> {
  const badges = new Map<string, string>();
  const answers = context.page.filter === 'unexplained' ? fileAnswers(context.worklistSummary ?? context.worklist) : new Map<string, string>();
  for (const row of badgeLaneFiles(context)) {
    const badge = [badgeForLaneFile(row), answers.get(row.path)].filter(Boolean).join(' · ');
    if (badge) badges.set(row.path, badge);
  }
  for (const entry of context.inventory.entries)
    if (entry.mode_change || ['binary', 'symlink', 'submodule'].includes(entry.content))
      badges.set(entry.path, ['non-text', answers.get(entry.path)].filter(Boolean).join(' · '));
  return badges;
}
function badgeLaneFiles(context: ReviewChangeContext): ReviewLaneFile[] {
  if (context.lane?.phase !== 'ready' || context.lane.value.state === 'unavailable') return [];
  const lane = context.lane.value;
  return [...(lane.unexplained_changes?.files ?? []), ...(lane.unknown_attribution?.files ?? [])];
}
function badgeForLaneFile(row: ReviewLaneFile): string | undefined {
  if (row.content !== 'text' || row.non_text) return 'non-text';
  if (row.bucket === 'unexplained') return 'no invariant';
  if (row.bucket === 'attribution_unknown') return 'unknown';
  if (row.counts.unexplained) return `${row.counts.unexplained} hunks, no invariant`;
  if (row.counts.attribution_unknown) return `${row.counts.attribution_unknown} unknown hunks`;
  return undefined;
}
