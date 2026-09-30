// The changed-intent summary the task entry shows beside the Intent review button
// (models/knowledge/review_intent_summary.py, served by serving/review_summary.py).
//
// It is the ONE read the entry makes for the reviewer before the reviewer is opened: the subject
// catalogue is the reviewer's own navigation and is not loaded here. The counts are the comparison
// owner's (heads only the after side holds / heads only the before side holds, for invariants and
// joint guarantees); this client never derives a count from line totals or from catalogue size.
//
// Three answers are kept apart all the way to the screen:
//   * `counted` -- the comparison was read; `+N −N` is a measurement, and a measured zero is `+0 −0`;
//   * `partial` -- some identity had no single head; the counts describe the rest and say so;
//   * `unavailable` -- the comparison's knowledge could not be read (no dataset yet, an unreadable
//     half, an unresolved candidate); there are NO counts, only the owner's refusal.
// A body that is none of these, and a request that never reached the server, are failures in the
// shared review vocabulary (`ReviewFailure`), so the entry can say which happened.
//
// A tree comparison's answer also carries `attribution`: the unexplained-changes lane's file-level
// count (data/reviewLane.ts). It arrives in this same response -- the entry never asks for it more
// eagerly than for the intent counts -- and is kept apart from `+N −N` all the way to the screen.

import { useEffect, useRef, useState } from "react";

import { qs } from "./files";
import {
  type ReviewFailure,
  type ReviewRefusal,
  getReviewJson,
  reviewProblemFromCause,
  reviewProblemFromRefusal,
  unreadableAnswer,
} from "./review";
import type { ReviewLaneSummary } from "./reviewLane";

interface IntentHeadChanges {
  after_only: number;
  before_only: number;
}

interface ReviewIntentCounts {
  added: number;
  removed: number;
  invariants: IntentHeadChanges;
  guarantees: IntentHeadChanges;
  realization_only: number;
  membership_only: number;
  unresolved: number;
}

interface ReviewIntentSummaryResult {
  state: "counted" | "partial" | "unavailable";
  operation: string;
  repository_id: string;
  master: string;
  leaf_id: string;
  counts?: ReviewIntentCounts;
  refusal?: ReviewRefusal;
  attribution?: ReviewLaneSummary;
}

const intentReviewSummary = (
  repo: string,
  master: string,
  leaf: string,
  base = "",
): Promise<ReviewIntentSummaryResult> =>
  getReviewJson<ReviewIntentSummaryResult>(
    `${base}/api/review/intent/summary?${qs({ repo, master, leaf })}`,
  );

// What the entry renders. `counts` and `problem` are never both present; `attribution` is present for
// a tree comparison whatever the intent counts' own state.
export type IntentSummaryRead =
  | { phase: "loading" }
  | { phase: "counted" | "partial"; counts: ReviewIntentCounts; attribution?: ReviewLaneSummary }
  | { phase: "unavailable"; problem: ReviewFailure; attribution?: ReviewLaneSummary };

// One answer as the read state it is. A counted/partial body without counts, and an unavailable body
// without its refusal, are not this route's answer and are reported as unreadable rather than drawn.
function summaryRead(result: ReviewIntentSummaryResult): IntentSummaryRead {
  const attribution = result.attribution ? { attribution: result.attribution } : {};
  if ((result.state === "counted" || result.state === "partial") && result.counts)
    return { phase: result.state, counts: result.counts, ...attribution };
  if (result.state === "unavailable" && result.refusal)
    return {
      phase: "unavailable",
      problem: reviewProblemFromRefusal(result.refusal),
      ...attribution,
    };
  return { phase: "unavailable", problem: unreadableAnswer(String(result.state)) };
}

// The entry's summary read. It asks once per task context and again only when `facts` -- the leaf's
// OWN projection facts, not the global analytics document -- move, so an unrelated publication
// elsewhere in the workspace never re-reads it. A superseded answer (the props moved while it was
// in flight) is dropped by sequence number, so it cannot land on the leaf shown now.
export function useIntentReviewSummary(
  repo: string,
  master: string,
  leaf: string,
  facts: string,
): IntentSummaryRead {
  const key = `${repo}/${master}/${leaf}`;
  const [read, setRead] = useState<{ key: string; read: IntentSummaryRead } | null>(null);
  const reads = useRef(0);
  useEffect(() => {
    const seq = ++reads.current;
    let mounted = true;
    const apply = (next: IntentSummaryRead) => {
      if (mounted && reads.current === seq) setRead({ key, read: next });
    };
    void intentReviewSummary(repo, master, leaf).then(
      (result) => apply(summaryRead(result)),
      (cause: unknown) =>
        apply({ phase: "unavailable", problem: reviewProblemFromCause(cause) }),
    );
    return () => {
      mounted = false;
    };
  }, [repo, master, leaf, key, facts]);
  return read?.key === key ? read.read : { phase: "loading" };
}
