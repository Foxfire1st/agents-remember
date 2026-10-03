// The review surface's read cycle: one question, one in-flight read, and no superseded answer ever
// writing the panes (ICR-R17@v1).
//
// WHY THIS IS ITS OWN MODULE. `ReviewSurface.tsx` is over the repository's file-size rail and the
// component was over the per-function rail; the read cycle is a responsibility of its own (it owns a
// request sequence, a retained generation and a refresh), so it lives here and the surface renders
// what it returns. The outcome states stay in `ReviewOutcome.tsx`, the refresh control in
// `ReviewRefresh.tsx`, the admitted roster-walk merge in `familyWalkMerge.ts` and the kept answers in
// `ReviewReadCache.ts`: one owner each, no second copy of any.
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
//   * AN ANSWER IS BOUND TO ITS QUESTION. The read state carries the key it answers, and the surface is
//     handed a read only for the question on screen: until the new question's answer (or its kept
//     copy) exists, the read is `loading` for THAT question, never the previous subject's payload.
//   * A WHOLE SUBJECT IS READ ONCE PER COMPARISON. An admitted whole-subject answer is kept in the
//     surface's `ReviewReadCache` under its key, and returning to that question renders the kept
//     answer without a request. A refresh forgets the question it re-asks; an answer from another
//     comparison generation empties the cache (see that module).
//   * NO READ ON OPENING IS THROWN AWAY (MIK-R40 rule 5). The reviewer's first read waits for the
//     catalogue to choose a subject, for a bounded time; when the catalogue is slower than the bound
//     the task-context review is read meanwhile. If the catalogue then answers first, that read is
//     superseded: its answer never writes the panes, but it is kept in the cache (same generation
//     only), so "All source changes" opens from it without a second request. Only the task-context
//     answer is kept this way; a superseded subject's answer is dropped, as before.
//
// WHAT IT DOES NOT DO. No timer, no retry ladder, no polling: a read happens when the question changes
// or when the reader asks. A read that fails leaves the last coherent payload retained and lets the
// outcome region state the failure beside it, which is the packet's "a failed refresh retains the
// labeled old generation with its error".

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
  ReviewFailure,
  ReviewHistory,
  ReviewPagedCollection,
  ReviewPayload,
  ReviewSelectorKind,
} from '../../data/review';
import { intentReview, reviewProblemFromCause, reviewProblemFromRefusal } from '../../data/review';
import { type ReviewRead, readFrom } from './ReviewOutcome';
import type { ReviewReadCache } from './ReviewReadCache';
import { mergeFamilyContinuation } from './familyWalkMerge';

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
interface ReadContext {
  targetKey: string;
  questionKey: string;
  retainedRef: { current: RetainedReview | null };
  carriedRef: { current: CarriedBinding | null };
  cache: ReviewReadCache;
  setAnswer: (answer: KeyedRead) => void;
  setRetained: (update: (previous: RetainedReview | null) => RetainedReview | null) => void;
  // Called with every admitted payload: the shell the surface keeps while a newly selected subject is
  // pending, failed or refused.
  setFrame: (payload: ReviewPayload) => void;
  request: {
    repo: string;
    master: string;
    leaf: string;
    selectorKind?: ReviewSelectorKind;
    selectorId?: string;
    selection: ReviewPageRequest | undefined;
    history?: ReviewHistory;
  };
}

function startRead(reads: { current: number }, context: ReadContext): () => void {
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
  const whole = context.request.selection === undefined;
  const kept = whole && previous === null ? context.cache.review(askedFor) : undefined;
  if (kept !== undefined) {
    // The question was answered for this comparison already: render that answer, ask nothing. The
    // read number is still consumed, so a later read of this question cannot pose as a refresh.
    admit(context, askedFor, kept);
    return () => undefined;
  }
  const shown = context.retainedRef.current;
  const cursor =
    context.request.selection?.of === 'family_members'
      ? context.request.selection.continuation
      : undefined;
  const continuing = continuedReview(shown, context.questionKey, cursor, previous);
  // Keep the coherent reading path mounted while a continuation is checked. Every other question
  // and ordinary refresh is pending for its own key until its answer arrives.
  context.setAnswer({
    key: askedFor,
    read: continuing ? { phase: 'reviewed', payload: continuing.payload } : { phase: 'loading' },
  });
  context.setRetained((retained) =>
    continuing ? { ...continuing, key: askedFor } : retained?.key === askedFor ? retained : null,
  );
  askReview({ ...context.request, previous }, (answered) => {
    if (!current()) {
      // Superseded: it never writes the read state. The task-context answer is kept unshown.
      const taskContext = context.request.selectorKind === undefined;
      if (taskContext && whole && previous === null && answered.phase === 'reviewed')
        context.cache.keepUnshown(askedFor, answered.payload);
      return;
    }
    const admitted = familyContinuationRead(answered, continuing, cursor);
    if (admitted.phase === 'reviewed') {
      // A refresh's answer states its staleness against the identity it carried; it is shown once,
      // for that refresh, and never re-rendered later as the plain answer to a returning selection.
      if (whole && previous === null) context.cache.keepReview(askedFor, admitted.payload);
      else context.cache.observe(admitted.payload);
      admit(context, askedFor, admitted.payload);
      return;
    }
    context.setAnswer({ key: askedFor, read: admitted });
  });
  return () => {
    superseded = true;
  };
}

function admit(context: ReadContext, key: string, payload: ReviewPayload): void {
  context.setAnswer({ key, read: { phase: 'reviewed', payload } });
  context.setRetained(() => ({ key, questionKey: context.questionKey, payload }));
  context.setFrame(payload);
}

// A read together with the question it answers. The surface is only ever handed the read whose key is
// the question on screen, so a previous subject's answer cannot render under the new subject's header
// even for the render between the selection and the effect that starts the new read.
interface KeyedRead {
  key: string;
  read: ReviewRead;
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
  // The last payload admitted for this task context, whichever subject it answered. The surface keeps
  // its shell (scope, navigation, source explorer) over it while another subject is pending, failed or
  // refused, and never renders it as that subject's reading. `null` before any answer.
  frame: ReviewPayload | null;
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
  // The surface's cache of answers for the comparison on screen.
  cache: ReviewReadCache;
}

function readOnScreen(
  answer: KeyedRead,
  targetKey: string,
  question: {
    retained: RetainedReview | null;
    questionKey: string;
    selection: ReviewPageRequest | undefined;
    hold: boolean;
    cache: ReviewReadCache;
  },
): ReviewRead {
  if (answer.key === targetKey) return answer.read;
  const { retained, questionKey, selection, hold, cache } = question;
  const cursor = selection?.of === 'family_members' ? selection.continuation : undefined;
  const continuing = continuedReview(retained, questionKey, cursor, null);
  if (continuing) return { phase: 'reviewed', payload: continuing.payload };
  const kept = hold || selection !== undefined ? undefined : cache.review(targetKey);
  return kept ? { phase: 'reviewed', payload: kept } : { phase: 'loading' };
}

// The last admitted payload of one task context. A payload admitted under another context (the
// surface re-targeted to another leaf or record) is never that context's frame.
function useFrame(context: string): [ReviewPayload | null, (payload: ReviewPayload) => void] {
  const [frame, setFrameOf] = useState<{ context: string; payload: ReviewPayload } | null>(null);
  const setFrame = useCallback(
    (payload: ReviewPayload) => setFrameOf({ context, payload }),
    [context],
  );
  return [frame?.context === context ? frame.payload : null, setFrame];
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
  cache,
}: ReviewReadQuestion): ReviewReadCycle {
  const [answer, setAnswer] = useState<KeyedRead>({ key: '', read: { phase: 'loading' } });
  const [retained, setRetained] = useState<RetainedReview | null>(null);
  const [frame, setFrame] = useFrame(`${repo}/${master}/${leaf}/${history ?? 'live'}`);
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
  const [targetKey, questionKey] = [selection, undefined].map((page) =>
    targetKeyOf(repo, master, leaf, instead, selectorKind, selectorId, page, history),
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
  const targetRef = useLatest(targetKey);

  // One read, for the question that is on screen when it starts.
  useEffect(() => {
    if (hold) return undefined;
    return startRead(reads, {
      targetKey,
      questionKey,
      retainedRef,
      carriedRef,
      cache,
      setAnswer,
      setRetained,
      setFrame,
      request: asked,
    });
  }, [targetKey, questionKey, refreshNonce, asked, hold, retainedRef, carriedRef, cache, setFrame]);

  const refresh = useCallback(() => {
    // A refresh re-asks the server: the kept answer for the question on screen is not the answer.
    cache.forgetReview(targetRef.current);
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
  }, [retainedRef, targetRef, cache]);

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

  // The read for the question on screen: its own answer; else, in the same pass as the selection, the
  // walk it continues or the answer kept for it (so neither a roster page nor a return to a subject
  // flashes a pending state); else pending for this question.
  const read = useMemo(
    () => readOnScreen(answer, targetKey, { retained, questionKey, selection, hold, cache }),
    [answer, targetKey, retained, questionKey, selection, hold, cache],
  );

  return {
    read,
    retained,
    carried: carriedHere,
    refresh,
    frame,
  };
}
