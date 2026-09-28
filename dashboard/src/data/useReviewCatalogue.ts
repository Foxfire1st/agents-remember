// The reviewer's subject catalogue read: every recorded subject of the comparison being reviewed.
//
// It belongs to the reviewer, not to the task entry: the entry shows only the changed-intent summary
// and the catalogue is read when the reviewer is opened. Its key is the COMPARISON's identity alone --
// the task context and record (`repo/master/leaf/history`) plus the generation of the compared
// snapshots once the reviewer has read one -- so it is read once on entry, again when that identity
// moves (a refresh that reached a new candidate generation, ICR-R17) or when the reader asks, and
// never because an unrelated part of the workspace was republished.
//
// A superseded answer is dropped: each read takes the next sequence number and only the newest may
// write state, so a response for a previous comparison cannot overwrite the one on screen now.
import { useEffect, useRef, useState } from "react";
import {
  type ReviewEntry,
  type ReviewEntryListResult,
  type ReviewFailure,
  intentReviewEntries,
  reviewProblemFromCause,
  reviewProblemFromRefusal,
  unreadableAnswer,
} from "./review";

export interface ReviewCatalogueRead {
  loading: boolean;
  // The task context and record this answer belongs to. A generation re-read of the same comparison
  // keeps the listed subjects on screen (marked loading) until its answer lands.
  comparisonKey?: string;
  entries?: ReviewEntry[];
  totalSubjects?: number;
  invariantTotal?: number;
  familyTotal?: number;
  empty?: boolean;
  problem?: ReviewFailure;
}

// The comparison a catalogue read answers for. `generation` is absent until the reviewer has read a
// compared snapshot pair; the first one it reads is the one the entry catalogue already describes.
export interface ReviewCatalogueKey {
  repo: string;
  master: string;
  leaf: string;
  history?: string;
  generation?: number;
}

function catalogueAnswer(result: ReviewEntryListResult): ReviewCatalogueRead {
  if (result.state === "entries") {
    const entries = result.entries ?? [];
    const totalSubjects =
      typeof result.total_subjects === "number" ? result.total_subjects : entries.length;
    const invariantTotal =
      typeof result.invariant_total === "number"
        ? result.invariant_total
        : entries.filter((entry) => entry.selector_kind === "invariant").length;
    const familyTotal =
      typeof result.family_total === "number"
        ? result.family_total
        : entries.filter((entry) => entry.selector_kind === "family").length;
    return {
      loading: false,
      entries,
      totalSubjects,
      invariantTotal,
      familyTotal,
      empty: entries.length === 0,
    };
  }
  if (result.state === "refused") {
    return {
      loading: false,
      problem: result.refusal
        ? reviewProblemFromRefusal(result.refusal)
        : unreadableAnswer("refused"),
    };
  }
  return { loading: false, problem: unreadableAnswer(result.state) };
}

export function useReviewCatalogue(
  comparison: ReviewCatalogueKey,
): ReviewCatalogueRead & { refresh: () => void } {
  const { repo, master, leaf, history, generation } = comparison;
  const comparisonKey = `${repo}/${master}/${leaf}/${history ?? "live"}`;
  const targetKey = `${comparisonKey}/${generation ?? 0}`;
  const [read, setRead] = useState<ReviewCatalogueRead>({ loading: false });
  const [nonce, setNonce] = useState(0);
  const reads = useRef(0);
  useEffect(() => {
    let mounted = true;
    const seq = ++reads.current;
    const live = () => mounted && reads.current === seq;
    setRead((previous) => ({ ...previous, loading: true }));
    void intentReviewEntries(repo, master, leaf).then(
      (result) => {
        if (live()) setRead({ ...catalogueAnswer(result), comparisonKey });
      },
      (cause: unknown) => {
        if (live())
          setRead({ loading: false, problem: reviewProblemFromCause(cause), comparisonKey });
      },
    );
    return () => {
      mounted = false;
    };
  }, [repo, master, leaf, nonce, targetKey, comparisonKey]);
  const refresh = () => {
    setNonce((value) => value + 1);
  };
  if (read.comparisonKey !== comparisonKey) return { loading: true, refresh };
  return { ...read, refresh };
}
