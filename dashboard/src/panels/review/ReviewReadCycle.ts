// The review surface's read cycle: one question, one in-flight read, and no superseded answer ever
// writing the panes (ICR-R17@v1).
//
// WHY THIS IS ITS OWN MODULE. `ReviewSurface.tsx` is over the repository's file-size rail and the
// component was over the per-function rail; the read cycle is a responsibility of its own (it owns a
// request sequence, a retained generation and a refresh), so it lives here and the surface renders
// what it returns. The outcome states stay in `ReviewOutcome.tsx` and the refresh control in
// `ReviewRefresh.tsx`: one owner each, no second copy of either.
//
// THE THREE RULES THIS MODULE ENFORCES
//
//   * ONE READ PER QUESTION. `targetKey` -- task, subject, page position, record -- is the question's
//     identity, and a read is started for the question on screen when it starts. Changing any part of
//     the question re-asks it, and the answer is retained under the key it was asked for, so a payload
//     can never be rendered under a header it was not read for.
//   * THE NEWEST READ WINS. Every read takes the next sequence number, and only the newest may write
//     the read state. A slow answer for the subject/task that was selected BEFORE the current one
//     therefore lands with a smaller number and is dropped: it cannot replace the comparison on
//     screen, and the failure it might have reported is not reported either. The effect's own cleanup
//     flag covers unmount; the sequence covers the case that flag cannot see -- the same mounted
//     surface with two requests in flight.
//   * A REFRESH REPLACES, IT DOES NOT PATCH. The reader's control re-asks the SAME question carrying
//     the identity of the comparison they are looking at, which is read from the payload the panes
//     actually render -- never from a request or a response that arrived for something else. The
//     answer replaces the read state whole, and the server's own staleness answer (compared against
//     the identity that was carried) is what tells the reader whether it is still the same generation.
//   * THE CARRIED IDENTITY BELONGS TO ONE READ (L17-F1). It is the previous input of the comparison
//     that was DISPLAYED, so it travels on the single read the refresh asked for and on no other:
//     {readNumber, key, digest} is captured together in `refresh`, and `startRead` sends the digest
//     only when that read IS the one the refresh asked for. A different subject, a different leaf, the
//     same subject read from the leaf's record instead (`history="recorded"`), or a LATER read of the
//     same question all carry nothing -- otherwise the server would answer `stale` against an identity
//     that read never replaced, and the notice would announce a candidate publication that never
//     happened. The alternative -- clearing the identity when the question key changes -- is not
//     enough: switching away from a question and back again restores the key, and the stale identity
//     with it.
//
// WHAT IT DOES NOT DO. No timer, no retry ladder, no polling: a read happens when the question changes
// or when the reader asks. A read that fails leaves the last coherent payload retained and lets the
// outcome region state the failure beside it, which is the packet's "a failed refresh retains the
// labeled old generation with its error".

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
  ReviewFamilyMember,
  ReviewFamilyRevisionContext,
  ReviewFailure,
  ReviewHistory,
  ReviewPagedCollection,
  ReviewPayload,
  ReviewSelectorKind,
} from '../../data/review';
import { intentReview, reviewProblemFromCause, reviewProblemFromRefusal } from '../../data/review';
import { type ReviewRead, readFrom } from './ReviewOutcome';

// The bounded collection this surface is paging and the cursor it continues (ICR-R10). It is part of
// the question rather than a decoration on the answer, so it participates in the target key.
//
// The union is the server's own `ReviewPagedCollection` rather than a narrowed copy: a request naming
// `family_members` must be representable, because that is how a family's truncated roster is
// continued (with the cursor that family context published). Which collections the surface OFFERS
// with no cursor is a separate decision, in `REVIEW_WALKABLE_COLLECTIONS`.
export interface ReviewPageRequest {
  of: ReviewPagedCollection;
  continuation?: string | null;
  size?: number;
}

// The identity one read answers for: the task context AND the question asked of it. A comparison is
// only ever shown under the header it was read for, so this key is what a retained generation is
// stored with and checked against.
export function targetKeyOf(
  repo: string,
  master: string,
  leaf: string,
  instead: ReviewFailure | null,
  selectorKind?: ReviewSelectorKind,
  selectorId?: string,
  page?: ReviewPageRequest,
  history?: ReviewHistory,
): string {
  const question = instead !== null ? 'task-context' : `${selectorKind ?? ''}:${selectorId ?? ''}`;
  // The page is part of the question, not a decoration on it: page 3 of one collection is a different
  // answer from page 1 of it, and a retained page must never be rendered under another page's header.
  const position =
    page === undefined
      ? 'whole'
      : `${page.of}:${page.continuation === undefined || page.continuation === null ? 'first' : page.continuation}`;
  return `${repo}/${master}/${leaf}/${history ?? 'live'}/${question}/${position}`;
}

// One read of one question, handed to a callback only when it is the answer's turn to be applied. The
// caller's `current()` guard is what decides that; this function never writes state itself, so there is
// exactly one place where a read reaches the surface and exactly one place where it is dropped.
function askReview(
  question: {
    repo: string;
    master: string;
    leaf: string;
    selectorKind?: ReviewSelectorKind;
    selectorId?: string;
    selection: ReviewPageRequest | undefined;
    history?: ReviewHistory;
    previous: string | null;
  },
  apply: (answered: ReviewRead) => void,
): void {
  void (async () => {
    try {
      const result = await intentReview(
        question.repo,
        question.master,
        question.leaf,
        question.selectorKind,
        question.selectorId,
        '',
        question.selection,
        question.history,
        question.previous ?? undefined,
      );
      apply(readFrom(result));
    } catch (cause) {
      apply({ phase: 'failed', problem: reviewProblemFromCause(cause) });
    }
  })();
}

// One read, started for the question that is on screen now, returning the cleanup that supersedes it.
// The sequence number and the cleanup flag are the two reasons an answer may be dropped, and they are
// decided here rather than in the hook so the hook reads as the cycle it is.
function startRead(
  reads: { current: number },
  context: {
    targetKey: string;
    questionKey: string;
    retainedRef: { current: RetainedReview | null };
    carriedRef: { current: CarriedBinding | null };
    setRead: (read: ReviewRead) => void;
    setRetained: (update: (previous: RetainedReview | null) => RetainedReview | null) => void;
    request: {
      repo: string;
      master: string;
      leaf: string;
      selectorKind?: ReviewSelectorKind;
      selectorId?: string;
      selection: ReviewPageRequest | undefined;
      history?: ReviewHistory;
    };
  },
): () => void {
  let superseded = false;
  const askedFor = context.targetKey;
  const seq = ++reads.current;
  const current = () => !superseded && reads.current === seq;
  // THE IDENTITY BELONGS TO ONE READ (L17-F1). It is sent only when this read is the one the refresh
  // asked to replace that display with: the same question key AND the read number `refresh` captured.
  // Every other read -- another subject, another leaf, the same subject read from the leaf's record,
  // or a later read of the same question -- replaces nothing, and a carried identity there would make
  // the server answer `stale` about a comparison nobody replaced.
  const carried = context.carriedRef.current;
  const previous =
    carried !== null && carried.key === askedFor && carried.readNumber === seq
      ? carried.digest
      : null;
  const shown = context.retainedRef.current;
  const cursor =
    context.request.selection?.of === 'family_members'
      ? context.request.selection.continuation
      : undefined;
  const continuing = continuedReview(shown, context.questionKey, cursor, previous);
  // Keep the coherent reading path mounted while a continuation is checked. Every other question
  // and ordinary refresh retains the existing replacement and newest-answer rules.
  context.setRead(
    continuing ? { phase: 'reviewed', payload: continuing.payload } : { phase: 'loading' },
  );
  context.setRetained((retained) =>
    continuing ? { ...continuing, key: askedFor } : retained?.key === askedFor ? retained : null,
  );
  askReview({ ...context.request, previous }, (answered) => {
    if (!current()) return;
    const admitted = familyContinuationRead(answered, continuing, cursor);
    context.setRead(admitted);
    if (admitted.phase === 'reviewed')
      context.setRetained(() => ({
        key: askedFor,
        questionKey: context.questionKey,
        payload: admitted.payload,
      }));
  });
  return () => {
    superseded = true;
  };
}

interface RetainedReview {
  key: string;
  questionKey: string;
  payload: ReviewPayload;
}

function continuedReview(
  shown: RetainedReview | null,
  questionKey: string,
  cursor: string | null | undefined,
  refreshBinding: string | null,
): RetainedReview | null {
  if (refreshBinding !== null || !cursor || shown?.questionKey !== questionKey) return null;
  return shown;
}

function familyContinuationRead(
  answered: ReviewRead,
  continuing: RetainedReview | null,
  cursor: string | null | undefined,
): ReviewRead {
  if (!continuing || !cursor || answered.phase !== 'reviewed') return answered;
  const payload = mergeFamilyContinuation(continuing.payload, answered.payload, cursor);
  if (payload) return { phase: 'reviewed', payload };
  // A rejected page is a failed read, never a replacement for the admitted accumulated context.
  return {
    phase: 'failed',
    problem: reviewProblemFromRefusal(
      answered.payload.page_refusal ?? {
        code: 'comparison_page_unreadable',
        detail:
          'The response failed the displayed family continuation checks for comparison, subject, cursor or immutable content. The last coherent review remains displayed.',
        offending_input: cursor,
        next_action: 'Open the whole review to start a new family walk before continuing.',
      },
    ),
  };
}

function mergeMember(
  previous: ReviewFamilyMember | undefined,
  next: ReviewFamilyMember,
): ReviewFamilyMember | null {
  if (!previous) return next;
  if (
    previous.invariant_revision_id !== next.invariant_revision_id ||
    (previous.state === 'recorded' &&
      next.state === 'recorded' &&
      previous.payload_digest !== next.payload_digest)
  )
    return null;
  const sources = new Map(previous.sources.map((source) => [source.claim_id, source]));
  for (const source of next.sources) {
    const known = sources.get(source.claim_id);
    if (known && JSON.stringify(known) !== JSON.stringify(source)) return null;
    sources.set(source.claim_id, source);
  }
  // Sparse later claims never erase the exact content already delivered for this membership.
  return { ...(previous.state === 'recorded' ? previous : next), sources: [...sources.values()] };
}

function mergeFamilySide(
  previous: ReviewFamilyRevisionContext,
  next: ReviewFamilyRevisionContext,
  cursor: string,
): ReviewFamilyRevisionContext | null {
  if (!sameFamilyWalk(previous, next)) return null;
  // Other walks are resent at page one by the server; they cannot reset an already advanced walk.
  if (next.page?.state !== 'continued') return previous;
  if (next.page.continued_from !== cursor) return null;
  if (previous.page?.continuation !== cursor) {
    return JSON.stringify(previous.page) === JSON.stringify(next.page) ? previous : null;
  }
  const members = new Map(previous.members.map((member) => [member.member_id, member]));
  for (const member of next.members) {
    const merged = mergeMember(members.get(member.member_id), member);
    if (!merged) return null;
    members.set(member.member_id, merged);
  }
  return {
    ...next,
    members: [...members.values()],
    detail: `Loaded ${members.size} exact member context(s) of ${next.members_total} recorded memberships across this roster walk.`,
  };
}

function sameFamilyWalk(
  previous: ReviewFamilyRevisionContext,
  next: ReviewFamilyRevisionContext,
): boolean {
  return [
    [previous.family_id, next.family_id],
    [previous.side, next.side],
    [previous.state, next.state],
    [previous.family_revision_id, next.family_revision_id],
    [previous.guarantee?.payload_digest, next.guarantee?.payload_digest],
    [JSON.stringify(previous.page?.scope), JSON.stringify(next.page?.scope)],
  ].every(([known, supplied]) => known === supplied);
}

function admittedFamilyContinuation(
  previous: ReviewPayload,
  next: ReviewPayload,
  cursor: string,
): boolean {
  const page = next.page;
  if (
    !previous.comparison ||
    !next.comparison ||
    !previous.family_context ||
    !next.family_context ||
    !page
  )
    return false;
  return [
    page.collection === 'family_members',
    page.state === 'continued',
    page.continued_from === cursor,
    !next.page_refusal,
    next.staleness.state !== 'stale',
    JSON.stringify(previous.comparison) === JSON.stringify(next.comparison),
    JSON.stringify(previous.candidate) === JSON.stringify(next.candidate),
    JSON.stringify(previous.knowledge.revision_selection) ===
      JSON.stringify(next.knowledge.revision_selection),
    previous.family_context.entries.length === next.family_context.entries.length,
  ].every(Boolean);
}

// This is presentation of one admitted walk, not another dataset or selection authority. The
// latest response still owns the primary statements, source inventory, evidence and assessments.
function mergeFamilyContinuation(
  previous: ReviewPayload,
  next: ReviewPayload,
  cursor: string,
): ReviewPayload | null {
  if (!admittedFamilyContinuation(previous, next, cursor)) return null;
  const families = previous.family_context!;
  const incoming = next.family_context!;
  const byFamily = new Map(families.entries.map((entry) => [entry.family_id, entry]));
  const entries = [];
  let continued = 0;
  for (const entry of incoming.entries) {
    const known = byFamily.get(entry.family_id);
    if (!known || JSON.stringify(known.selection) !== JSON.stringify(entry.selection)) return null;
    const before = mergeFamilySide(known.before, entry.before, cursor);
    const after = mergeFamilySide(known.after, entry.after, cursor);
    if (!before || !after) return null;
    continued += [entry.before, entry.after].filter(
      (side) => side.page?.continued_from === cursor,
    ).length;
    const complete = [before, after].every(
      (side) =>
        side.state === 'not_recorded' ||
        (side.page?.complete &&
          side.members.length === side.members_total &&
          side.members.every((member) => member.state === 'recorded')),
    );
    entries.push({
      ...entry,
      before,
      after,
      state: complete ? ('recorded' as const) : entry.state,
      detail: `${entry.selection.statement}; before: ${before.detail}; after: ${after.detail}`,
    });
  }
  if (continued !== 1) return null;
  return {
    ...next,
    family_context: {
      ...incoming,
      entries,
      state: entries.every((entry) => entry.state === 'recorded') ? 'recorded' : incoming.state,
      unique_member_revision_total: new Set(
        entries.flatMap((entry) =>
          [entry.before, entry.after].flatMap((side) =>
            side.members.map((member) => member.invariant_revision_id),
          ),
        ),
      ).size,
      detail:
        'Loaded family context retains the exact members and source claims from these bounded roster walks.',
    },
  };
}

// A comparison identity together with the ONE read it is the previous input of (L17-F1). The three
// fields are one value because an identity alone cannot answer "is this read replacing the display it
// names?": the question key says which display it came from, and the read number says which read was
// asked to replace it. An identity carried into a read that replaces nothing is what made the server
// report a moved comparison for a subject the reader had merely selected.
interface CarriedBinding {
  readNumber: number;
  key: string;
  digest: string;
}

export interface ReviewReadCycle {
  read: ReviewRead;
  // The last comparison this surface really read, with the key it was read for. A read that fails
  // without an answer must not erase it: it stays on screen, labelled, and the failure is stated
  // beside it -- but only while the surface is still asking that same question.
  retained: RetainedReview | null;
  // The identity a read carried FOR THE QUESTION ON SCREEN NOW, or `null` when no read of this
  // question has carried one. It is what the generation notice describes: nothing has been compared
  // against a displayed identity until a read answers for one, and an identity captured under another
  // question is not this question's previous input (L17-F1).
  carried: string | null;
  // The reader's own request that the displayed comparison be re-read against the candidate as it is
  // now: the same question, carrying the identity of what is on screen.
  refresh: () => void;
}

interface ReviewReadQuestion {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: ReviewHistory;
  instead: ReviewFailure | null;
  selection: ReviewPageRequest | undefined;
  // True while the question is not settled yet (the reviewer's first subject is still being chosen):
  // no read is started, so the surface does not ask for a question it is about to replace.
  hold?: boolean;
}

// A ref that always holds the latest render's value: read by callbacks and effects that must see the
// current value without re-running when it changes.
function useLatest<T>(value: T): { current: T } {
  const ref = useRef(value);
  ref.current = value;
  return ref;
}

export function useReviewReadCycle({
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  history,
  instead,
  selection,
  hold = false,
}: ReviewReadQuestion): ReviewReadCycle {
  const [read, setRead] = useState<ReviewRead>({ phase: 'loading' });
  const [retained, setRetained] = useState<RetainedReview | null>(null);
  // The displayed comparison's identity, WITH the question it was displayed under and the read asked
  // to replace it (L17-F1): an identity alone is what let a digest be carried into a read that never
  // replaced the display it names.
  const [carried, setCarried] = useState<CarriedBinding | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);
  // The question's identity, and the request fields it is asked with. The fields are memoised on their
  // own values rather than passed to the effect as a fresh object, so the effect's dependencies are the
  // ones it really reads and no lint suppression is needed to say so. The two `instead`-selected
  // fields are part of the question: a refusal answered by the task's own source inventory asks no
  // subject, so the request names none.
  const targetKey = targetKeyOf(
    repo,
    master,
    leaf,
    instead,
    selectorKind,
    selectorId,
    selection,
    history,
  );
  const questionKey = targetKeyOf(
    repo,
    master,
    leaf,
    instead,
    selectorKind,
    selectorId,
    undefined,
    history,
  );
  const asked = useMemo(
    () => ({
      repo,
      master,
      leaf,
      selectorKind: instead ? undefined : selectorKind,
      selectorId: instead ? undefined : selectorId,
      selection,
      history,
    }),
    [repo, master, leaf, selectorKind, selectorId, instead, selection, history],
  );
  // Every read the surface starts takes the next number, and only the newest one may write the read
  // state (see the module header).
  const reads = useRef(0);
  // The displayed payload and the carried identity, as refs: the refresh callback reads both without
  // restarting a read, and `startRead` decides from them whether the identity may be sent.
  const retainedRef = useLatest(retained);
  const carriedRef = useLatest(carried);

  // One read, for the question that is on screen when it starts.
  useEffect(() => {
    if (hold) return undefined;
    return startRead(reads, {
      targetKey,
      questionKey,
      retainedRef,
      carriedRef,
      setRead,
      setRetained,
      request: asked,
    });
  }, [targetKey, questionKey, refreshNonce, asked, hold, retainedRef, carriedRef]);

  const refresh = useCallback(() => {
    const shown = retainedRef.current;
    const shownBinding = shown?.payload.comparison?.binding_digest;
    // The identity is filed with the question it was displayed for AND the read that will replace it,
    // so the read it starts can tell whether it is that read (L17-F1). The nonce is incremented in the
    // same update, so the read the identity names is the one the effect starts next.
    if (shown !== null && shownBinding !== undefined) {
      setCarried({
        readNumber: reads.current + 1,
        key: shown.key,
        digest: shownBinding,
      });
    }
    setRefreshNonce((nonce) => nonce + 1);
  }, [retainedRef]);

  // The identity the notice may describe: the one THIS read carried, for the question being asked NOW,
  // and only when this read is the one the refresh asked to replace that display. All THREE conjuncts
  // are needed, and the key is not redundant with the read number (L17-R2-F1): when the subject change
  // and the reader's refresh land in the same React flush, the refresh captures the PREVIOUS subject's
  // identity while the one effect run that follows asks for the new subject and consumes exactly that
  // read number -- a read-number check alone would then describe another subject's identity, and the
  // surface would announce a candidate publication the request never carried and the server never
  // answered. `startRead` checks the same pair before sending, so the value described and the value
  // sent cannot disagree. `read` is a `reviewed` phase here exactly when a read answered, so a carried
  // identity reaching this value has an answer behind it by construction (L17-F2's rule).
  const carriedHere =
    carried !== null && carried.readNumber === reads.current && carried.key === targetKey
      ? carried.digest
      : null;

  return { read, retained, carried: carriedHere, refresh };
}
