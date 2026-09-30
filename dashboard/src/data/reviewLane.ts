// The unexplained-changes lane of a tree comparison (MIK-R32): models/knowledge/review_lane.py,
// served through the tree view route (serving/review_trees.py) as `lane=files` and `file=<path>`.
//
// Changed source that no recorded entry's range intersects is a review destination of its own. The
// server classifies once and this adapter only carries the answer:
//   * each changed file's bucket -- `attributed` (an entry supplies a range at its recorded blob),
//     `unexplained` (no entry on either side, both read) or `attribution_unknown` (a side unread, or
//     entries that supply no range) -- with the owner's reason;
//   * each zero-context hunk's class, keyed by side line numbers: `linked` (its changed lines meet a
//     supplied range; intersection only, never coverage), `unexplained` or `attribution_unknown`
//     (with the reason per side);
//   * the two tree destinations: `Unexplained changes` (unexplained files, then attributed files with
//     unexplained hunks or a non-text change the gate holds unexplained) and `Unknown attribution`.
//
// The entry's own count (`ReviewLaneSummary`) travels on the changed-intent summary, read together
// with it (data/reviewIntentSummary.ts). A dataset review asks this adapter nothing.

import { useEffect, useState } from 'react';

import { qs } from './files';
import {
  type ReviewFailure,
  type ReviewRefusal,
  getReviewJson,
  reviewProblemFromCause,
  reviewProblemFromRefusal,
  unreadableAnswer,
} from './review';
import type { ReviewTreeComparison } from './reviewTrees';

export type LaneBucket = 'attributed' | 'unexplained' | 'attribution_unknown';
export type LaneHunkClass = 'linked' | 'unexplained' | 'attribution_unknown';
export type LaneSideName = 'before' | 'after';
export type LaneMembershipState =
  'member' | 'before_only' | 'removed_or_reassigned' | 'confirmed_no_family' | 'membership_unknown';

// The entry's attribution count: file buckets only. `counted` carries the totals; `partial` and
// `unavailable` carry none and say why -- neither is ever a zero.
export interface ReviewLaneSummary {
  state: 'counted' | 'partial' | 'unavailable';
  changed_total?: number;
  attributed?: number;
  unexplained?: number;
  attribution_unknown?: number;
  detail?: string;
  unmeasured: string[];
}

export interface ReviewLaneSpan {
  start: number;
  count: number;
}

export interface ReviewLaneFamilyOccurrence {
  family?: string;
  family_key?: string;
  family_revision?: number;
  state: LaneMembershipState;
}

export interface ReviewLaneLink {
  side: LaneSideName;
  id: string;
  kind: 'realization' | 'proof';
  facet?: string;
  invariant: string;
  invariant_key: string;
  invariant_revision?: number;
  invariant_revision_key?: string;
  start_line: number;
  end_line: number;
  families: ReviewLaneFamilyOccurrence[];
}

export interface ReviewLaneUnknown {
  side: LaneSideName;
  reason: 'knowledge_unavailable' | 'range_not_supplied';
  detail: string;
  entries: string[];
}

export interface ReviewLaneHunk {
  before: ReviewLaneSpan;
  after: ReviewLaneSpan;
  classification: LaneHunkClass;
  links: ReviewLaneLink[];
  unknown: ReviewLaneUnknown[];
}

export interface ReviewLaneEntryRange {
  id: string;
  kind: 'realization' | 'proof';
  invariant: string;
  start_line?: number;
  end_line?: number;
  reason?: string;
  detail?: string;
}

export interface ReviewLaneSide {
  side: LaneSideName;
  path?: string;
  blob?: string;
  knowledge: 'available' | 'unavailable';
  detail?: string;
  entries: ReviewLaneEntryRange[];
}

export interface ReviewLaneNonText {
  content: string;
  mode_change: boolean;
  gate: 'linked' | 'unexplained' | 'unknown';
}

export interface ReviewLaneCounts {
  hunks: number;
  linked: number;
  unexplained: number;
  attribution_unknown: number;
}

// The one per-file response: the source the triage badges and hunk markers read too.
export interface ReviewFileClassification {
  path: string;
  status: string;
  content: string;
  mode_change: boolean;
  bucket: LaneBucket;
  reason: string;
  sides: [ReviewLaneSide, ReviewLaneSide];
  hunks: ReviewLaneHunk[];
  non_text?: ReviewLaneNonText;
  counts: ReviewLaneCounts;
}

export interface ReviewLaneFile {
  path: string;
  status: string;
  content: string;
  bucket: LaneBucket;
  reason: string;
  counts: ReviewLaneCounts;
  non_text?: ReviewLaneNonText;
  unknown_reasons: string[];
}

// One tree destination: its bucket's files first (`bucket_files` of them), then the attributed files
// it lists. `hunks` counts the destination's class across the listed files; it never changes a file
// total.
export interface ReviewLaneDestination {
  files: ReviewLaneFile[];
  bucket_files: number;
  attributed_files: number;
  hunks: number;
  non_text: number;
}

// One measured changed path and its bucket.
export interface ReviewLanePath {
  path: string;
  bucket: LaneBucket;
}

export interface ReviewUnexplainedLane {
  state: 'measured' | 'partial' | 'unavailable';
  detail: string;
  changed_total?: number;
  attributed?: number;
  unexplained?: number;
  attribution_unknown?: number;
  unexplained_changes?: ReviewLaneDestination;
  unknown_attribution?: ReviewLaneDestination;
  unmeasured: string[];
  // Every measured changed path with its bucket: the source explorer's labels on a tree comparison.
  paths: ReviewLanePath[];
}

// The tree view route's answer to a lane or file question (the fields this adapter reads).
interface ReviewLaneAnswer {
  state: 'trees' | 'not-converted' | 'refused';
  comparison?: ReviewTreeComparison;
  lane?: ReviewUnexplainedLane;
  file_classification?: ReviewFileClassification;
  refusal?: ReviewRefusal;
}

export type LaneDestinationName = 'unexplained' | 'unknown';

// What a view renders; `value` and `problem` are never both present.
export type LaneRead<T> =
  | { phase: 'loading' }
  | { phase: 'ready'; value: T }
  | { phase: 'unavailable'; problem: ReviewFailure };

const laneUrl = (
  repo: string,
  master: string,
  leaf: string,
  comparison: number,
  question: { lane: 'files' } | { file: string },
): string =>
  `/api/review/trees?${qs({ repo, master, leaf, comparison: String(comparison), ...question })}`;

function readOf<T>(answer: ReviewLaneAnswer, value: T | undefined): LaneRead<T> {
  if (answer.state === 'trees' && value !== undefined) return { phase: 'ready', value };
  if (answer.state === 'refused' && answer.refusal)
    return { phase: 'unavailable', problem: reviewProblemFromRefusal(answer.refusal) };
  return { phase: 'unavailable', problem: unreadableAnswer(String(answer.state)) };
}

type AnswerRead =
  | { phase: 'loading' }
  | { phase: 'answered'; answer: ReviewLaneAnswer }
  | { phase: 'unavailable'; problem: ReviewFailure };

// One question's answer, kept with the URL it answers so a superseded answer never draws. `null`
// asks nothing.
function useLaneAnswer(url: string | null): AnswerRead | null {
  const [read, setRead] = useState<{ url: string; read: AnswerRead } | null>(null);
  useEffect(() => {
    if (url === null) return undefined;
    let mounted = true;
    const apply = (next: AnswerRead) => {
      if (mounted) setRead({ url, read: next });
    };
    void getReviewJson<ReviewLaneAnswer>(url).then(
      (answer) => apply({ phase: 'answered', answer }),
      (cause: unknown) => apply({ phase: 'unavailable', problem: reviewProblemFromCause(cause) }),
    );
    return () => {
      mounted = false;
    };
  }, [url]);
  if (url === null) return null;
  return read?.url === url ? read.read : { phase: 'loading' };
}

function picked<T>(
  read: AnswerRead | null,
  pick: (answer: ReviewLaneAnswer) => T | undefined,
): LaneRead<T> | null {
  if (read === null || read.phase !== 'answered') return read;
  return readOf(read.answer, pick(read.answer));
}

// The lane's two destinations for the comparison a review payload names; `null` (and no request) for a
// dataset review, which names no tree comparison.
export function useReviewLane(
  repo: string,
  master: string,
  leaf: string,
  comparison: number | undefined,
): LaneRead<ReviewUnexplainedLane> | null {
  const url =
    comparison === undefined ? null : laneUrl(repo, master, leaf, comparison, { lane: 'files' });
  return picked(useLaneAnswer(url), (answer) => answer.lane);
}

// One changed path's classification, read when the lane opens it.
export function useReviewFileClassification(
  repo: string,
  master: string,
  leaf: string,
  comparison: number | undefined,
  path: string | null,
): LaneRead<ReviewFileClassification> | null {
  const url =
    comparison === undefined || path === null
      ? null
      : laneUrl(repo, master, leaf, comparison, { file: path });
  return picked(useLaneAnswer(url), (answer) => answer.file_classification);
}
