// The unexplained-changes lane's presentation rules (MIK-R32), kept apart from its components.
//
// Nothing here classifies: every bucket, hunk and class comes from the server's per-file response.
// These functions only choose which of those hunks a destination focuses on, and cut the text window
// the focused diff shows around a hunk, on the side line numbers the server named -- so a mark always
// sits on the owner's hunk, never on the renderer's own change region.
import type {
  LaneBucket,
  LaneRead,
  ReviewLaneSpan,
  ReviewUnexplainedLane,
} from '../../data/reviewLane';

// "L4", "L4–6", or "none (after L5)" for a side where the hunk changes nothing.
export function spanLabel(span: ReviewLaneSpan): string {
  if (span.count === 0) return `none (after L${span.start})`;
  if (span.count === 1) return `L${span.start}`;
  return `L${span.start}–${span.start + span.count - 1}`;
}

export function sideLines(text: string): string[] {
  const lines = text.split('\n');
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

// The source explorer's label for each bucket, in the explorer's landed words: on a tree comparison
// the explorer and the lane show one classification.
export const EXPLORER_LABELS: Record<LaneBucket, string> = {
  attributed: 'Mapped',
  unexplained: 'Unmapped',
  attribution_unknown: 'Attribution unknown',
};
export const EXPLORER_PENDING = 'Attribution pending';

// The technical details' attribution facts on a tree comparison, from the lane: pending while it is
// read, unknown when it could not be, else the paths of each bucket. They replace the landed
// accounting's lists there, so no surface of a tree comparison classifies a file differently.
export type LaneAttributionFacts =
  | { state: 'pending' }
  | { state: 'unknown' }
  | { state: 'read'; partial: boolean; unexplained: string[]; unknown: string[] };

export function laneAttributionFacts(read: LaneRead<ReviewUnexplainedLane>): LaneAttributionFacts {
  if (read.phase === 'loading') return { state: 'pending' };
  if (read.phase !== 'ready' || read.value.state === 'unavailable') return { state: 'unknown' };
  const { unexplained_changes, unknown_attribution } = read.value;
  if (!unexplained_changes || !unknown_attribution) return { state: 'unknown' };
  return {
    state: 'read',
    partial: read.value.state === 'partial',
    unexplained: unexplained_changes.files.map((file) => file.path),
    unknown: unknown_attribution.files.map((file) => file.path),
  };
}

// The destination count the held lane answers in the technical details' remaining counts.
const LANE_COUNT_NAMES: Record<string, 'unexplained' | 'unknown'> = {
  unattributed_changed_paths: 'unexplained',
  unknown_attribution_changed_paths: 'unknown',
};

// One remaining count as the lane states it, or `undefined` for a count the lane does not answer.
export function laneCountText(name: string, facts: LaneAttributionFacts): string | undefined {
  const which = LANE_COUNT_NAMES[name];
  if (which === undefined) return undefined;
  const label = which === 'unexplained'
    ? 'changed files without an invariant'
    : 'changed files of unknown attribution';
  if (facts.state === 'pending') return `${label}: ${EXPLORER_PENDING}`;
  if (facts.state === 'unknown') return `${label}: the change set could not be measured`;
  return `${label}: ${facts[which].length}${facts.partial ? ' (partial)' : ''}`;
}
