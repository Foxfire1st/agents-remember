// The reviewer's tree view of one leaf (MIK-R25): models/knowledge/review_trees.py, served by
// serving/review_trees.py at /api/review/trees.
//
// For a leaf whose memory is converted, a review comparison is four Git trees -- code base, code
// candidate, memory base and memory candidate -- and each memory side is read through the derived
// index of its tree. The landed review (data/review.ts) keeps its shape and its behaviour: only its
// data source changed. This adapter carries what that payload does not:
//   * the comparison itself: the four trees and the refs that pin an uncommitted candidate;
//   * each knowledge side's state -- `available`, `unavailable-history` (Git can no longer produce
//     the tree; it is named, never replaced) or `legacy-unavailable` -- and its index state
//     (`partial` names the files that failed);
//   * the Git diff of the two memory trees, grouped by record and by source path;
//   * each invariant's MIK-R03 currentness per side, against that side's own code tree;
//   * the MIK-R08 worklist view: items, the history rows about their subjects (no current/stale mark
//     until MIK-R09), and the gate linkage of every changed hunk.
//
// Three answers are kept apart: `trees`, `not-converted` (the leaf's review is the dataset review;
// nothing here applies) and `refused` (the owner's refusal). A body that is none of these, and a
// request that never reached the server, are failures in the shared review vocabulary.

import { useEffect, useRef, useState } from 'react';

import { qs } from './files';
import {
  type ReviewFailure,
  type ReviewRefusal,
  getReviewJson,
  reviewProblemFromCause,
  reviewProblemFromRefusal,
  unreadableAnswer,
} from './review';

export type ReviewTreeSideState = 'available' | 'unavailable-history' | 'legacy-unavailable';

export interface ReviewTreeSide {
  repository: string;
  tree: string;
  commit?: string;
  ref?: string;
}

export interface ReviewConvertedBase {
  commit: string;
  version: string;
  code_commit: string;
  tree: string;
}

export interface ReviewTreeComparison {
  schema: 'ar-review-tree-comparison/v1';
  task_id: string;
  leaf_id: string;
  number: number;
  code_base: ReviewTreeSide;
  code_candidate: ReviewTreeSide;
  memory_base: ReviewTreeSide;
  memory_candidate: ReviewTreeSide;
  converted_base?: ReviewConvertedBase;
  recorded_at: string;
}

export interface ReviewKnowledgeSide {
  side: 'before' | 'after';
  state: ReviewTreeSideState;
  tree: string;
  index_state?: 'complete' | 'partial';
  problems: [string, string][];
  detail?: string;
}

// One code tree of a reopened comparison: a tree Git can no longer produce is named, never replaced.
export interface ReviewCodeSide {
  side: 'base' | 'candidate';
  state: 'available' | 'unavailable-history';
  tree: string;
  detail?: string;
}

export interface ReviewKnowledgeFileChange {
  path: string;
  old_path?: string;
  status: 'added' | 'deleted' | 'modified' | 'renamed' | 'type_changed';
  patch: string;
  truncated: boolean;
}

export interface ReviewKnowledgeRecordGroup {
  record_id: string;
  kind: string;
  files: ReviewKnowledgeFileChange[];
  // [entry id, source path, "added" | "removed" | "changed"]
  entries: [string, string, string][];
}

export interface ReviewKnowledgeSourceGroup {
  source_path: string;
  files: ReviewKnowledgeFileChange[];
  records: string[];
}

export interface ReviewKnowledgeTreeDiff {
  before_tree: string;
  after_tree: string;
  changed_files: number;
  records: ReviewKnowledgeRecordGroup[];
  sources: ReviewKnowledgeSourceGroup[];
  history: ReviewKnowledgeFileChange[];
  other: ReviewKnowledgeFileChange[];
}

export type InvariantCurrentnessState = 'stale' | 'unverifiable' | 'unrealized' | 'current';

export interface ReviewEntryCurrentness {
  id: string;
  kind: string;
  path: string;
  state: InvariantCurrentnessState;
  reason?: string;
}

export interface ReviewInvariantCurrentness {
  id: string;
  state: InvariantCurrentnessState;
  entries: ReviewEntryCurrentness[];
}

export interface ReviewSideCurrentness {
  codeTree?: { repositoryRoot: string; treeId: string } | null;
  counts?: Record<InvariantCurrentnessState, number>;
  invariants?: ReviewInvariantCurrentness[];
  families?: { id: string; members: number; staleMembers: number; stale: string[] }[];
  indexState?: 'complete' | 'partial';
  unverifiableReason?: string;
}

export interface ReviewWorklistHunk {
  linked: boolean;
  [fact: string]: unknown;
}

export interface ReviewWorklistChange {
  path: string;
  status: string;
  hunks?: ReviewWorklistHunk[];
  fileLevel?: { linked: boolean };
  [fact: string]: unknown;
}

export interface ReviewWorklistView {
  source: 'computed' | 'persisted' | 'absent';
  bound: boolean;
  state?: string;
  detail?: string;
  items: { id: string; kind: string; subject: string; facts: Record<string, unknown> }[];
  history_rows: {
    id: string;
    owner: string;
    subject: string;
    disposition: string;
    row: Record<string, unknown>;
  }[];
  changes: ReviewWorklistChange[];
  incomplete: Record<string, unknown>[];
}

export interface ReviewTreesResult {
  state: 'trees' | 'not-converted' | 'refused';
  repository_id: string;
  master: string;
  leaf_id: string;
  comparison?: ReviewTreeComparison;
  knowledge_sides: ReviewKnowledgeSide[];
  // Present on a reopened comparison; a live one was just captured.
  code_sides?: ReviewCodeSide[];
  knowledge_diff?: ReviewKnowledgeTreeDiff;
  currentness?: Partial<Record<'before' | 'after', ReviewSideCurrentness>>;
  worklist?: ReviewWorklistView;
  refusal?: ReviewRefusal;
}

// Which comparison a read addresses: the live one (nothing named), the leaf's latest record
// (`recorded`), or one recorded comparison by its number -- never a path or a tree id.
export interface ReviewTreesAddress {
  comparison?: number;
  recorded?: boolean;
}

export const reviewTrees = (
  repo: string,
  master: string,
  leaf: string,
  address: ReviewTreesAddress = {},
  base = '',
): Promise<ReviewTreesResult> => {
  const params: Record<string, string> = { repo, master, leaf };
  if (address.comparison !== undefined) params.comparison = String(address.comparison);
  if (address.recorded) params.history = 'recorded';
  return getReviewJson<ReviewTreesResult>(`${base}/api/review/trees?${qs(params)}`);
};

// What a view renders. `trees` and `problem` are never both present.
export type ReviewTreesRead =
  | { phase: 'loading' }
  | { phase: 'trees'; trees: ReviewTreesResult }
  | { phase: 'not-converted' }
  | { phase: 'unavailable'; problem: ReviewFailure };

// One answer as the read state it is. A `trees` body without its comparison, and a refused body
// without its refusal, are not this route's answer and are reported as unreadable, not drawn.
export function reviewTreesRead(result: ReviewTreesResult): ReviewTreesRead {
  if (result.state === 'trees' && result.comparison) return { phase: 'trees', trees: result };
  if (result.state === 'not-converted') return { phase: 'not-converted' };
  if (result.state === 'refused' && result.refusal)
    return { phase: 'unavailable', problem: reviewProblemFromRefusal(result.refusal) };
  return { phase: 'unavailable', problem: unreadableAnswer(String(result.state)) };
}

// The side of a comparison whose knowledge could not be read, with the tree it names. A side that
// is `available` but read from a partial index is returned too: it is never presented as complete.
export function degradedKnowledgeSides(result: ReviewTreesResult): ReviewKnowledgeSide[] {
  return result.knowledge_sides.filter(
    (side) => side.state !== 'available' || side.index_state === 'partial',
  );
}

// One invariant's currentness on each side, for a pane that shows a subject from the landed review.
export function invariantCurrentness(
  result: ReviewTreesResult,
  invariantId: string,
): Partial<Record<'before' | 'after', InvariantCurrentnessState>> {
  const states: Partial<Record<'before' | 'after', InvariantCurrentnessState>> = {};
  for (const side of ['before', 'after'] as const) {
    const found = result.currentness?.[side]?.invariants?.find((one) => one.id === invariantId);
    if (found) states[side] = found.state;
  }
  return states;
}

// The changed hunks the gate linked to no recorded knowledge: the reviewer's unexplained changes.
export function unexplainedHunks(
  worklist: ReviewWorklistView,
): { path: string; hunk: ReviewWorklistHunk }[] {
  return worklist.changes.flatMap((change) =>
    (change.hunks ?? [])
      .filter((hunk) => !hunk.linked)
      .map((hunk) => ({ path: change.path, hunk })),
  );
}

// The tree view read for one task context. A superseded answer (the props moved while it was in
// flight) is dropped by sequence number, so it cannot land on the leaf shown now.
export function useReviewTrees(
  repo: string,
  master: string,
  leaf: string,
  address: ReviewTreesAddress = {},
): ReviewTreesRead {
  const { comparison, recorded } = address;
  const key = `${repo}/${master}/${leaf}/${comparison ?? ''}/${recorded ? 'recorded' : ''}`;
  const [read, setRead] = useState<{ key: string; read: ReviewTreesRead } | null>(null);
  const reads = useRef(0);
  useEffect(() => {
    const seq = ++reads.current;
    let mounted = true;
    const apply = (next: ReviewTreesRead) => {
      if (mounted && reads.current === seq) setRead({ key, read: next });
    };
    void reviewTrees(repo, master, leaf, { comparison, recorded }).then(
      (result) => apply(reviewTreesRead(result)),
      (cause: unknown) => apply({ phase: 'unavailable', problem: reviewProblemFromCause(cause) }),
    );
    return () => {
      mounted = false;
    };
  }, [repo, master, leaf, comparison, recorded, key]);
  return read?.key === key ? read.read : { phase: 'loading' };
}
