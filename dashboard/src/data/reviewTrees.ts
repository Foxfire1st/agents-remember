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
//     until MIK-R09), and the gate linkage of every changed hunk;
//   * every realization and proof entry located on both code sides (MIK-R31), for the focused
//     expression cards (panels/review/focusedCards.ts).
//
// Every key is snake_case, the review surface's convention: the server re-keys the currentness and
// worklist documents its owners spell in camelCase (MIK-L25 review F9), so no key here is camelCase.
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
  code_tree?: { repository_root: string; tree_id: string } | null;
  counts?: Record<InvariantCurrentnessState, number>;
  invariants?: ReviewInvariantCurrentness[];
  families?: { id: string; members: number; stale_members: number; stale: string[] }[];
  index_state?: 'complete' | 'partial';
  unverifiable_reason?: string;
}

export interface ReviewWorklistHunk {
  linked: boolean;
  [fact: string]: unknown;
}

export interface ReviewWorklistChange {
  path: string;
  status: string;
  hunks?: ReviewWorklistHunk[];
  file_level?: { linked: boolean };
  [fact: string]: unknown;
}

// One worklist item, verbatim from the worklist (re-keyed). `planning` is MIK-R11's mark on invariant
// and family items; `satisfied_by` names the history row that answers it, when one does.
export interface ReviewWorklistItem {
  id: string;
  kind: string;
  subject: string;
  planning?: 'planned' | 'unplanned';
  satisfied_by?: unknown;
  facts: Record<string, unknown>;
}

export interface ReviewWorklistHistoryRow {
  id: string;
  owner: string;
  owner_kind?: string;
  closed?: boolean;
  path?: string;
  subject: string;
  disposition: string;
  row: Record<string, unknown>;
}

export interface ReviewWorklistView {
  source: 'computed' | 'persisted' | 'absent';
  bound: boolean;
  state?: string;
  detail?: string;
  items: ReviewWorklistItem[];
  history_rows: ReviewWorklistHistoryRow[];
  changes: ReviewWorklistChange[];
  incomplete: Record<string, unknown>[];
}

// One entry on one code side (models/knowledge/review_tree_entries.py). `recorded` is whether that
// side's memory tree holds the entry (`undefined` when that tree could not be read); `state` is where
// the locator landed: `resolved` (a range), `unresolved` (with the reason; no range is guessed),
// `absent` (no file at the path) or `unavailable` (the side could not be read).
export type ReviewEntryRangeState = 'resolved' | 'unresolved' | 'absent' | 'unavailable';

export interface ReviewTreeEntrySide {
  recorded?: boolean;
  state: ReviewEntryRangeState;
  path: string;
  blob?: string;
  start_line?: number;
  end_line?: number;
  content?: string;
  // The resolved range's text from this side's exact blob, bounded; an unchanged entry carries it on
  // the after side only (one content identity, one text).
  excerpt?: string;
  excerpt_truncated?: boolean;
  reason?: string;
  role?: string;
  facet?: string;
  rationale?: string;
  currentness?: 'current' | 'stale' | 'unverifiable';
  currentness_reason?: string;
}

export interface ReviewTreeEntry {
  id: string;
  kind: 'realization' | 'proof';
  invariant: string;
  // The invariant's identity as the landed review payload addresses it: the join key to a member.
  invariant_key: string;
  before: ReviewTreeEntrySide;
  after: ReviewTreeEntrySide;
  change: 'changed' | 'unchanged' | 'undetermined';
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
  entries?: ReviewTreeEntry[];
  refusal?: ReviewRefusal;
}

// Which comparison a read addresses: the live one (nothing named), the leaf's latest record
// (`recorded`), or one recorded comparison by its number -- never a path or a tree id.
export interface ReviewTreesAddress {
  comparison?: number;
  recorded?: boolean;
  // Naming invariants asks for their entries only (MIK-R31's cards), by the identities the landed
  // review payload addresses them with.
  invariants?: string[];
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
  if (address.invariants?.length) params.invariants = address.invariants.join(',');
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

// The recorded tree comparison a landed review payload was composed over: the server declares it as
// the limitation token `review:trees:<n>`. A payload without it is a dataset review, for which no
// tree read is made at all, so an unconverted review behaves exactly as before.
export function treeComparisonNumber(limitations: string[]): number | undefined {
  for (const token of limitations) {
    const match = /^review:trees:(\d+)$/.exec(token);
    if (match) return Number(match[1]);
  }
  return undefined;
}

// The entries of one selection's invariants, from the comparison the review payload names. The
// answer is kept with the question it answers, so a superseded selection never draws its cards.
export function useReviewTreeEntries(
  repo: string,
  master: string,
  leaf: string,
  comparison: number | undefined,
  invariants: string[],
): ReviewTreesRead | null {
  const wanted = invariants.join(',');
  const key = `${repo}/${master}/${leaf}/${comparison ?? ''}/${wanted}`;
  const [read, setRead] = useState<{ key: string; read: ReviewTreesRead } | null>(null);
  useEffect(() => {
    if (comparison === undefined || !wanted) return undefined;
    let mounted = true;
    const apply = (next: ReviewTreesRead) => {
      if (mounted) setRead({ key, read: next });
    };
    void reviewTrees(repo, master, leaf, { comparison, invariants: wanted.split(',') }).then(
      (result) => apply(reviewTreesRead(result)),
      (cause: unknown) => apply({ phase: 'unavailable', problem: reviewProblemFromCause(cause) }),
    );
    return () => {
      mounted = false;
    };
  }, [repo, master, leaf, comparison, wanted, key]);
  if (comparison === undefined || !wanted) return null;
  return read?.key === key ? read.read : { phase: 'loading' };
}

// The tree view read for one task context. A superseded answer (the props moved while it was in
// flight) is dropped by sequence number, so it cannot land on the leaf shown now.
export function useReviewTrees(
  repo: string,
  master: string,
  leaf: string,
  address: ReviewTreesAddress = {},
  enabled = true,
): ReviewTreesRead | null {
  const { comparison, recorded } = address;
  const key = `${repo}/${master}/${leaf}/${comparison ?? ''}/${recorded ? 'recorded' : ''}`;
  const [read, setRead] = useState<{ key: string; read: ReviewTreesRead } | null>(null);
  const reads = useRef(0);
  useEffect(() => {
    if (!enabled) return undefined;
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
  }, [repo, master, leaf, comparison, recorded, key, enabled]);
  if (!enabled) return null;
  return read?.key === key ? read.read : { phase: 'loading' };
}
