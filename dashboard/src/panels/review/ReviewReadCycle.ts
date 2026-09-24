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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  ReviewFailure,
  ReviewHistory,
  ReviewPagedCollection,
  ReviewPayload,
  ReviewSelectorKind,
} from "../../data/review";
import { intentReview, reviewProblemFromCause } from "../../data/review";
import { type ReviewRead, readFrom } from "./ReviewOutcome";

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
  const question = instead !== null ? "task-context" : `${selectorKind ?? ""}:${selectorId ?? ""}`;
  // The page is part of the question, not a decoration on it: page 3 of one collection is a different
  // answer from page 1 of it, and a retained page must never be rendered under another page's header.
  const position =
    page === undefined
      ? "whole"
      : `${page.of}:${page.continuation === undefined || page.continuation === null ? "first" : page.continuation}`;
  return `${repo}/${master}/${leaf}/${history ?? "live"}/${question}/${position}`;
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
  apply: (answered: ReviewRead, payload: ReviewPayload | undefined) => void,
): void {
  void (async () => {
    try {
      const result = await intentReview(
        question.repo,
        question.master,
        question.leaf,
        question.selectorKind,
        question.selectorId,
        "",
        question.selection,
        question.history,
        question.previous ?? undefined,
      );
      apply(readFrom(result), result.state === "review" ? result.payload : undefined);
    } catch (cause) {
      apply({ phase: "failed", problem: reviewProblemFromCause(cause) }, undefined);
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
    carriedRef: { current: CarriedBinding | null };
    setRead: (read: ReviewRead) => void;
    setRetained: (
      update: (previous: { key: string; payload: ReviewPayload } | null) =>
        | { key: string; payload: ReviewPayload }
        | null,
    ) => void;
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
  context.setRead({ phase: "loading" });
  // A comparison read for another target is dropped the moment this question is asked: it belongs to
  // the header it was read for, and one read's answer is never rendered under another's.
  context.setRetained((previousRetained) =>
    previousRetained !== null && previousRetained.key !== askedFor ? null : previousRetained,
  );
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
  askReview({ ...context.request, previous }, (answered, payload) => {
    if (!current()) return;
    context.setRead(answered);
    if (payload !== undefined) context.setRetained(() => ({ key: askedFor, payload }));
  });
  return () => {
    superseded = true;
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
  retained: { key: string; payload: ReviewPayload } | null;
  // The identity a read carried FOR THE QUESTION ON SCREEN NOW, or `null` when no read of this
  // question has carried one. It is what the generation notice describes: nothing has been compared
  // against a displayed identity until a read answers for one, and an identity captured under another
  // question is not this question's previous input (L17-F1).
  carried: string | null;
  // The reader's own request that the displayed comparison be re-read against the candidate as it is
  // now: the same question, carrying the identity of what is on screen.
  refresh: () => void;
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
}: {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: ReviewHistory;
  instead: ReviewFailure | null;
  selection: ReviewPageRequest | undefined;
}): ReviewReadCycle {
  const [read, setRead] = useState<ReviewRead>({ phase: "loading" });
  const [retained, setRetained] = useState<{ key: string; payload: ReviewPayload } | null>(null);
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
  const targetKey = targetKeyOf(repo, master, leaf, instead, selectorKind, selectorId, selection, history);
  const asked = useMemo(
    () => ({ repo, master, leaf, selectorKind: instead ? undefined : selectorKind, selectorId: instead ? undefined : selectorId, selection, history }),
    [repo, master, leaf, selectorKind, selectorId, instead, selection, history],
  );
  // Every read the surface starts takes the next number, and only the newest one may write the read
  // state (see the module header).
  const reads = useRef(0);
  // The displayed payload and the carried identity, as refs: the refresh callback reads both without
  // restarting a read, and `startRead` decides from them whether the identity may be sent.
  const retainedRef = useRef(retained);
  retainedRef.current = retained;
  const carriedRef = useRef(carried);
  carriedRef.current = carried;

  // One read, for the question that is on screen when it starts.
  useEffect(() => {
    return startRead(reads, { targetKey, carriedRef, setRead, setRetained, request: asked });
  }, [targetKey, refreshNonce, asked]);

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
  }, []);

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
