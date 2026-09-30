// The unexplained-changes lane's presentation rules (MIK-R32), kept apart from its components.
//
// Nothing here classifies: every bucket, hunk and class comes from the server's per-file response.
// These functions only choose which of those hunks a destination focuses on, and cut the text window
// the focused diff shows around a hunk, on the side line numbers the server named -- so a mark always
// sits on the owner's hunk, never on the renderer's own change region.
import type {
  LaneBucket,
  LaneDestinationName,
  LaneHunkClass,
  LaneRead,
  ReviewFileClassification,
  ReviewLaneDestination,
  ReviewLaneHunk,
  ReviewLaneSpan,
  ReviewUnexplainedLane,
} from '../../data/reviewLane';

export const HUNK_CONTEXT_LINES = 3;
// A focused view draws at most this many hunk diffs; the full file shows every one.
export const FOCUSED_HUNK_LIMIT = 20;

export const DESTINATION_TITLES: Record<LaneDestinationName, string> = {
  unexplained: 'Unexplained changes',
  unknown: 'Unknown attribution',
};

const DESTINATION_CLASS: Record<LaneDestinationName, LaneHunkClass> = {
  unexplained: 'unexplained',
  unknown: 'attribution_unknown',
};

const DESTINATION_BUCKET: Record<LaneDestinationName, ReviewFileClassification['bucket']> = {
  unexplained: 'unexplained',
  unknown: 'attribution_unknown',
};

export const GROUP_TITLES: Record<LaneDestinationName, [string, string]> = {
  unexplained: ['Unexplained files', 'Attributed files with unexplained changes'],
  unknown: ['Files of unknown attribution', 'Attributed files with attribution-unknown changes'],
};

// One line under each group title: what puts a file in the group, said once rather than per row.
export const GROUP_NOTES: Record<LaneDestinationName, [string, string]> = {
  unexplained: [
    'Neither memory tree records a realization or proof entry for these files, and both were read.',
    'A recorded entry supplies a range in these files; the listed hunks, or the non-text change the gate holds, meet none.',
  ],
  unknown: [
    'Whether an entry explains these files is not established: a knowledge side was not read, or the entries recorded here supply no range at this blob.',
    'These files are attributed; the listed hunks change lines on a side where an entry supplies no range.',
  ],
};

export function destinationOf(
  lane: ReviewUnexplainedLane,
  name: LaneDestinationName,
): ReviewLaneDestination | undefined {
  return name === 'unexplained' ? lane.unexplained_changes : lane.unknown_attribution;
}

// The destination's two groups, in the server's order: its bucket's files, then attributed files.
export function destinationGroups(destination: ReviewLaneDestination) {
  return [
    destination.files.slice(0, destination.bucket_files),
    destination.files.slice(destination.bucket_files),
  ] as const;
}

// "5 files · 12 hunks · 1 non-text": the file and hunk totals, reported separately.
export function destinationTotals(destination: ReviewLaneDestination): string {
  const files = destination.bucket_files + destination.attributed_files;
  const parts = [
    `${files} file${files === 1 ? '' : 's'}`,
    `${destination.hunks} hunk${destination.hunks === 1 ? '' : 's'}`,
  ];
  if (destination.non_text) parts.push(`${destination.non_text} non-text`);
  return parts.join(' · ');
}

// The hunks a destination opens a file on: every hunk of a file in its own bucket, else the hunks of
// its class in an attributed file.
export function focusedHunks(
  file: ReviewFileClassification,
  destination: LaneDestinationName,
): ReviewLaneHunk[] {
  if (file.bucket === DESTINATION_BUCKET[destination]) return file.hunks;
  return file.hunks.filter((hunk) => hunk.classification === DESTINATION_CLASS[destination]);
}

export const CLASS_LABELS: Record<LaneHunkClass, string> = {
  linked: 'linked',
  unexplained: 'unexplained',
  attribution_unknown: 'attribution unknown',
};

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

export interface HunkWindow {
  // The first line of the window, in the side's own numbering.
  first: number;
  text: string;
  // The hunk lies past the text held (a bounded prefix of the side): the window cannot show it.
  beyond: boolean;
}

// The window of one side around one hunk: its changed lines (or, where it changes none, the point
// between two lines where the other side's lines sit) plus `context` lines on each side.
export function hunkWindow(
  span: ReviewLaneSpan,
  lines: string[],
  context = HUNK_CONTEXT_LINES,
): HunkWindow {
  const low = span.count > 0 ? span.start : span.start + 1;
  const high = span.count > 0 ? span.start + span.count - 1 : span.start;
  const first = Math.max(1, low - context);
  const last = Math.min(lines.length, high + context);
  const text = last >= first ? `${lines.slice(first - 1, last).join('\n')}\n` : '';
  return { first, text, beyond: high > lines.length };
}

// The source explorer's label for each bucket, in the explorer's landed words: on a tree comparison
// the explorer and the lane show one classification.
export const EXPLORER_LABELS: Record<LaneBucket, string> = {
  attributed: 'Mapped',
  unexplained: 'Unmapped',
  attribution_unknown: 'Attribution unknown',
};
export const EXPLORER_PENDING = 'Attribution pending';

// The explorer's label for each changed path of a tree comparison, from the lane: pending while the
// lane is read, and of unknown attribution when the lane could not be read (never a guessed bucket).
export function explorerAttribution(
  read: LaneRead<ReviewUnexplainedLane>,
  paths: string[],
): Record<string, string> {
  if (read.phase === 'loading')
    return Object.fromEntries(paths.map((path) => [path, EXPLORER_PENDING]));
  const buckets = new Map(
    read.phase === 'ready' ? read.value.paths.map((one) => [one.path, one.bucket] as const) : [],
  );
  return Object.fromEntries(
    paths.map((path) => [path, EXPLORER_LABELS[buckets.get(path) ?? 'attribution_unknown']]),
  );
}

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
  const of = (bucket: LaneBucket) =>
    read.value.paths.filter((one) => one.bucket === bucket).map((one) => one.path);
  return {
    state: 'read',
    partial: read.value.state === 'partial',
    unexplained: of('unexplained'),
    unknown: of('attribution_unknown'),
  };
}

// The landed count each lane bucket answers in the technical details' remaining counts.
const LANE_COUNT_NAMES: Record<string, 'unexplained' | 'unknown'> = {
  unattributed_changed_paths: 'unexplained',
  unknown_attribution_changed_paths: 'unknown',
};

// One remaining count as the lane states it, or `undefined` for a count the lane does not answer.
export function laneCountText(name: string, facts: LaneAttributionFacts): string | undefined {
  const which = LANE_COUNT_NAMES[name];
  if (which === undefined) return undefined;
  if (facts.state === 'pending') return `${name}: ${EXPLORER_PENDING}`;
  if (facts.state === 'unknown')
    return `${name}: ${EXPLORER_LABELS.attribution_unknown} (the lane could not be read)`;
  return `${name}: ${facts[which].length}${facts.partial ? ' (partial)' : ''}`;
}
