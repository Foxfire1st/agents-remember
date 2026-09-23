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
//
// WHAT IT DOES NOT DO. No timer, no retry ladder, no polling: a read happens when the question changes
// or when the reader asks. A read that fails leaves the last coherent payload retained and lets the
// outcome region state the failure beside it, which is the packet's "a failed refresh retains the
// labeled old generation with its error".

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ReviewFailure,
  ReviewHistory,
  ReviewPayload,
  ReviewSelectorKind,
} from "../../data/review";
import { intentReview, reviewProblemFromCause } from "../../data/review";
import { type ReviewRead, readFrom } from "./ReviewOutcome";

// The bounded collection this surface is paging and the cursor it continues (ICR-R10). It is part of
// the question rather than a decoration on the answer, so it participates in the target key.
export interface ReviewPageRequest {
  of: "knowledge" | "records";
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
    carriedRef: { current: string | null };
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
  askReview(
    { ...context.request, previous: context.carriedRef.current },
    (answered, payload) => {
      if (!current()) return;
      context.setRead(answered);
      if (payload !== undefined) context.setRetained(() => ({ key: askedFor, payload }));
    },
  );
  return () => {
    superseded = true;
  };
}

export interface ReviewReadCycle {
  read: ReviewRead;
  // The last comparison this surface really read, with the key it was read for. A read that fails
  // without an answer must not erase it: it stays on screen, labelled, and the failure is stated
  // beside it -- but only while the surface is still asking that same question.
  retained: { key: string; payload: ReviewPayload } | null;
  // The identity a refresh carried, or `null` when nothing has been re-read yet. It is what the
  // generation notice describes: nothing has been compared against a displayed identity until the
  // reader asks for one.
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
  const [carried, setCarried] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);
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
  // Every read the surface starts takes the next number, and only the newest one may write the read
  // state (see the module header).
  const reads = useRef(0);
  // The two values the effect reads without being re-run by them: the displayed comparison's own
  // identity, and the identity already carried by the read in flight. They are refs because a read
  // must not restart when the payload it is replacing is written -- the request and the guard it uses
  // must agree about the generation it was asked for.
  const retainedRef = useRef(retained);
  retainedRef.current = retained;
  const carriedRef = useRef(carried);
  carriedRef.current = carried;

  // One read, for the question that is on screen when it starts.
  useEffect(() => {
    return startRead(reads, {
      targetKey,
      carriedRef,
      setRead,
      setRetained,
      request: {
        repo,
        master,
        leaf,
        selectorKind: instead ? undefined : selectorKind,
        selectorId: instead ? undefined : selectorId,
        selection,
        history,
      },
    });
  }, [
    targetKey,
    refreshNonce,
    carried,
    repo,
    master,
    leaf,
    selectorKind,
    selectorId,
    instead,
    selection,
    history,
  ]);

  const refresh = useCallback(() => {
    const shownBinding = retainedRef.current?.payload.comparison?.binding_digest;
    if (shownBinding !== undefined) setCarried(shownBinding);
    setRefreshNonce((nonce) => nonce + 1);
  }, []);

  return { read, retained, carried, refresh };
}
