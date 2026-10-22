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
//   * each invariant's MIK-R03 currentness per side, against that side's own code tree;
//   * the MIK-R08 worklist view: items, their gate verdicts and witnesses, the subjects' history rows,
//     and the gate linkage of every changed hunk;
//   * the leaf-wide implementation facts, with explicit unavailable state;
//   * every realization and proof entry located on both code sides (MIK-R31), for the focused
//     expression cards (panels/review/focusedCards.ts).
//
// Every key is snake_case, the review surface's convention: the server re-keys the currentness and
// worklist documents its owners spell in camelCase (MIK-L25 review F9), so no key here is camelCase.
//
// Three answers are kept apart: `trees`, `refused` (the owner's refusal; a leaf whose memory is
// unconverted is refused, with each knowledge side named `legacy-unavailable`) and `not-converted`
// (no tree comparison and no unreadable side; nothing here applies). A body that is none of these,
// and a request that never reached the server, are failures in the shared review vocabulary.

import { useEffect, useMemo, useRef, useState } from 'react';

import { qs } from './files';
import { useIntentEntryGeneration } from './intentEntryRevalidation';
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
  item_states?: Record<string, ReviewItemVerdict>;
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
  // The locator's symbol name on this side, when the locator names one (MIK-R89 v2 rule 3's filter).
  symbol?: string;
  currentness?: 'current' | 'stale' | 'unverifiable';
  currentness_reason?: string;
}

// The four memory values of one entry, read at the bound after-side index (MIK-R89 v2 rule 3). The
// per-path values were read at `path`, the entry's after-side path.
export interface ReviewEntryMemory {
  path: string;
  invariants_at_path: number;
  incoming_links: number;
  without_proof: boolean;
  open_weaknesses: number;
}

// Whether the card read could give the memory values; absent on every other trees read.
export interface ReviewEntryMemoryState {
  state: 'available' | 'unavailable';
  tree: string;
  index_state?: 'complete' | 'partial';
  reason?: string;
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
  memory?: ReviewEntryMemory;
}

export interface ReviewCodeChange {
  invariant: string;
  invariant_key: string;
  implementation: 'established' | 'unknown' | 'not_established';
  proof: boolean;
  range_unresolved: boolean;
  evidence: string[];
  unknown_reasons: string[];
}
export interface ReviewCodeChanges {
  state: 'facts' | 'unavailable';
  detail?: string;
  invariants: ReviewCodeChange[];
}
export interface ReviewItemVerdict {
  state: 'answered' | 'open' | 'undecided';
  witness?: string;
  row_id?: string;
  reason?: string;
}

// Whether the comparison on screen is what the leaf delivered, and what the leaf delivered when it
// is not (MIK-R195). `comparison` is the number of the recorded comparison whose four trees are the
// delivered pair, when the leaf holds one; `statement` is the one line the page states, and it says
// nothing about whether an earlier record's own count or statement was right.
export type DeliveredComparisonState = 'delivered' | 'differs' | 'unavailable';

export interface ReviewDeliveredComparison {
  state: DeliveredComparisonState;
  statement: string;
  comparison?: number;
  shown?: number;
  code_base_tree?: string;
  code_candidate_tree?: string;
  code_candidate_commit?: string;
  memory_base_tree?: string;
  memory_candidate_tree?: string;
  memory_candidate_commit?: string;
  detail?: string;
}

export interface ReviewTreesResult {
  state: 'trees' | 'not-converted' | 'refused';
  repository_id: string;
  master: string;
  leaf_id: string;
  comparison?: ReviewTreeComparison;
  // The highest comparison number the leaf recorded (the comparison menu's list). A landed leaf's
  // resolved comparison is its delivered pair, which need not be the highest number (MIK-R195).
  latest?: number;
  // Whether the comparison on screen is what the leaf delivered, with that pair's exact identities.
  delivered?: ReviewDeliveredComparison;
  knowledge_sides: ReviewKnowledgeSide[];
  // Present on a reopened comparison; a live one was just captured.
  code_sides?: ReviewCodeSide[];
  currentness?: Partial<Record<'before' | 'after', ReviewSideCurrentness>>;
  worklist?: ReviewWorklistView;
  code_changes?: ReviewCodeChanges;
  entries?: ReviewTreeEntry[];
  entry_memory?: ReviewEntryMemoryState;
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
  signal?: AbortSignal,
): Promise<ReviewTreesResult> => {
  const params: Record<string, string> = { repo, master, leaf };
  if (address.comparison !== undefined) params.comparison = String(address.comparison);
  if (address.recorded) params.history = 'recorded';
  if (address.invariants?.length) params.invariants = address.invariants.join(',');
  return getReviewJson<ReviewTreesResult>(`${base}/api/review/trees?${qs(params)}`, signal);
};

// The server answers `reviewer_busy` at once when too many computations are queued, and a full
// queue drains in about twenty seconds; `inputs_changing` comes after it computed twice over inputs
// that moved. The reader therefore asks again with a growing delay, for about half a minute (a
// bounded number of times), before it says "unavailable" (MIK-R42). The "computing" notice stays up
// throughout, because the read state only changes with the final answer. Aborting the signal ends
// the wait and the request in flight.
export const TRANSIENT_CODES = ['reviewer_busy', 'inputs_changing'];
export const BUSY_DELAYS_MS = [1500, 2000, 3000, 4000, 5000, 6000, 8000];
export const BUSY_RETRIES = BUSY_DELAYS_MS.length;

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      window.clearTimeout(timer);
      reject(new DOMException('superseded', 'AbortError'));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, ms);
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
  });
}

export async function reviewTreesRetryingBusy(
  repo: string,
  master: string,
  leaf: string,
  address: ReviewTreesAddress,
  signal: AbortSignal,
): Promise<ReviewTreesResult> {
  for (let attempt = 0; ; attempt += 1) {
    const result = await reviewTrees(repo, master, leaf, address, '', signal);
    const busy = result.state === 'refused' && TRANSIENT_CODES.includes(result.refusal?.code ?? '');
    if (!busy || attempt >= BUSY_RETRIES) return result;
    await pause(BUSY_DELAYS_MS[attempt], signal);
  }
}

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
// the limitation token `review:trees:<n>`. Without the token no comparison number
// has been admitted; a caller must not guess a tree identity.
export function treeComparisonNumber(limitations: string[]): number | undefined {
  for (const token of limitations) {
    const match = /^review:trees:(\d+)$/.exec(token);
    if (match) return Number(match[1]);
  }
  return undefined;
}

// The kept card answers (MIK-R31's cards, read 5 of the review page's opening, MIK-R89 v2 rule 2).
// They live outside the hook, in a bounded store keyed by the whole question, so a remount with an
// unchanged set finds the kept answer -- or the shared request still in flight -- and sends no second
// card request (L81-R1-F18).
const CARD_ANSWERS_MAX = 20;
const cardAnswers = new Map<string, ReviewTreesRead>();
const cardInflight = new Map<string, Promise<ReviewTreesRead>>();

function cardsKey(repo: string, master: string, leaf: string, comparison: number, wanted: string) {
  return `${repo}/${master}/${leaf}/${comparison}/${wanted}`;
}

// Whether a card answer is the record's own answer, to keep (MIK-R89 v2 rule 3, Failure and
// Recovery). A side the server names `unavailable-history` over trees it found readable, and memory
// values unavailable while the after side is available, name a read that failed: such an answer is
// this mount's to show and is asked again, never kept. An unconverted side is the record's own state.
function keepableCardAnswer(result: ReviewTreesResult): boolean {
  const sides = [...result.knowledge_sides, ...(result.code_sides ?? [])];
  if (sides.some((side) => side.state === 'unavailable-history')) return false;
  if (result.entry_memory === undefined || result.entry_memory.state === 'available') return true;
  return !result.knowledge_sides.some((side) => side.side === 'after' && side.state === 'available');
}

function keepCardAnswer(key: string, read: ReviewTreesRead): void {
  if (read.phase !== 'trees' || !keepableCardAnswer(read.trees)) return;
  cardAnswers.set(key, read);
  while (cardAnswers.size > CARD_ANSWERS_MAX) {
    const oldest = cardAnswers.keys().next();
    if (oldest.done) break;
    cardAnswers.delete(oldest.value);
  }
}

function fetchCardEntries(
  key: string,
  repo: string,
  master: string,
  leaf: string,
  comparison: number,
  invariants: string[],
): Promise<ReviewTreesRead> {
  const held = cardInflight.get(key);
  if (held) return held;
  // No abort: the fetch is shared across mounts, and aborting on one unmount would cancel the
  // answer a remount is about to reuse. A superseded selection never draws it -- the hook only
  // ever shows the answer whose key matches its own question.
  const fetch = reviewTrees(repo, master, leaf, { comparison, invariants }).then(
    (result) => reviewTreesRead(result),
    (cause: unknown) =>
      ({ phase: 'unavailable', problem: reviewProblemFromCause(cause) }) as ReviewTreesRead,
  );
  const tracked = fetch.then((read) => {
    cardInflight.delete(key);
    // Only an answered read is kept: a failed fetch, or an answer that names a failed read, stays
    // uncached, so a remount or a refresh retries it instead of showing one transient failure
    // forever.
    keepCardAnswer(key, read);
    return read;
  });
  cardInflight.set(key, tracked);
  return tracked;
}

// A new test (or dev session) drops the kept answers: each test renders a fresh session, and a
// kept answer from another test's fetch would hide the request the test asserts.
export function resetCardCachesForTests(): void {
  cardAnswers.clear();
  cardInflight.clear();
}

// The cards of the review page's shown subjects: the opening's read 5 (MIK-R89 v2 rule 2). The route
// takes at most 500 invariants per read, counting invariants, not subjects, so i invariants are
// ⌈i / 500⌉ reads. Each chunk goes through the kept-answer store above, so a remount asks nothing
// again, and the chunks' entries are one list in the order of the chunks.
export const CARD_CHUNK = 500;

export type ChangeSetCardsRead =
  | { phase: 'loading' }
  | {
      phase: 'cards';
      // The comparison every chunk answered for; null only when no invariant was asked.
      comparison: ReviewTreeComparison | null;
      entries: ReviewTreeEntry[];
      memory?: ReviewEntryMemoryState;
    }
  | { phase: 'not-converted' }
  | { phase: 'unavailable'; problem: ReviewFailure };

// The sorted, distinct invariant ids, cut into reads of at most CARD_CHUNK each.
function cardChunks(invariants: readonly string[]): string[][] {
  const sorted = [...new Set(invariants)].sort();
  const chunks: string[][] = [];
  for (let start = 0; start < sorted.length; start += CARD_CHUNK)
    chunks.push(sorted.slice(start, start + CARD_CHUNK));
  return chunks;
}

// A chunk whose memory values are unavailable makes the whole read's values unavailable, with the
// first reason: a sort over half the entries' values would not be the sort the reader chose.
function mergedMemory(results: ReviewTreesResult[]): ReviewEntryMemoryState | undefined {
  const states = results.map((result) => result.entry_memory).filter((one) => one !== undefined);
  return states.find((one) => one.state === 'unavailable') ?? states[0];
}

function mergedCards(reads: ReviewTreesRead[]): ChangeSetCardsRead {
  const failed = reads.find((read) => read.phase === 'unavailable');
  if (failed?.phase === 'unavailable') return failed;
  if (reads.some((read) => read.phase === 'not-converted')) return { phase: 'not-converted' };
  const results = reads.flatMap((read) => (read.phase === 'trees' ? [read.trees] : []));
  return {
    phase: 'cards',
    comparison: results[0]?.comparison ?? null,
    entries: results.flatMap((result) => result.entries ?? []),
    memory: mergedMemory(results),
  };
}

export function useChangeSetCards(
  repo: string,
  master: string,
  leaf: string,
  comparison: number | undefined,
  invariants: readonly string[],
  enabled: boolean,
): ChangeSetCardsRead | null {
  const signature = cardChunks(invariants)
    .map((chunk) => chunk.join(','))
    .join('|');
  // The chunk answers this mount saw. A failed chunk is not kept in the shared store (a remount asks
  // again); this mount shows it. An answer kept in the store from an earlier mount needs no read.
  const [seen, setSeen] = useState<ReadonlyMap<string, ReviewTreesRead>>(() => new Map());
  // The reviewer's Refresh (the shared re-validation generation) asks every chunk that is not kept
  // again; a kept answer is reused. Until its new answer arrives, the failed one stays shown.
  const refreshed = useIntentEntryGeneration();
  useEffect(() => {
    if (!enabled || comparison === undefined) return undefined;
    let alive = true;
    for (const joined of signature ? signature.split('|') : []) {
      const key = cardsKey(repo, master, leaf, comparison, joined);
      if (cardAnswers.has(key)) continue;
      void fetchCardEntries(key, repo, master, leaf, comparison, joined.split(',')).then((read) => {
        if (alive) setSeen((previous) => new Map(previous).set(key, read));
      });
    }
    return () => {
      alive = false;
    };
  }, [repo, master, leaf, comparison, signature, enabled, refreshed]);
  // One merged answer per set of chunk answers, so a render that changes nothing here hands the same
  // object on (the page's model is derived from it).
  return useMemo(() => {
    if (!enabled || comparison === undefined) return null;
    const reads = (signature ? signature.split('|') : []).map((joined) => {
      const key = cardsKey(repo, master, leaf, comparison, joined);
      return cardAnswers.get(key) ?? seen.get(key);
    });
    if (reads.some((read) => read === undefined)) return { phase: 'loading' };
    return mergedCards(reads as ReviewTreesRead[]);
  }, [enabled, comparison, signature, repo, master, leaf, seen]);
}

// The tree view read for one task context. A superseded read (the props moved while it was in
// flight) is aborted, so the server stops computing it, and its answer is dropped by sequence
// number as well, so it cannot land on the leaf shown now. A `reviewer_busy` answer is retried a
// bounded number of times before it is shown as unavailable.
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
    const superseded = new AbortController();
    const apply = (next: ReviewTreesRead) => {
      if (!superseded.signal.aborted && reads.current === seq) setRead({ key, read: next });
    };
    void reviewTreesRetryingBusy(
      repo,
      master,
      leaf,
      { comparison, recorded },
      superseded.signal,
    ).then(
      (result) => apply(reviewTreesRead(result)),
      (cause: unknown) => apply({ phase: 'unavailable', problem: reviewProblemFromCause(cause) }),
    );
    return () => superseded.abort();
  }, [repo, master, leaf, comparison, recorded, key, enabled]);
  if (!enabled) return null;
  return read?.key === key ? read.read : { phase: 'loading' };
}
