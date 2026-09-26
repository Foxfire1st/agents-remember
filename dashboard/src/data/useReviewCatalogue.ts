// One catalogue read cycle, shared by the task entry and the review workspace.
import { useEffect, useRef, useState } from 'react';
import {
  type ReviewEntry,
  type ReviewEntryListResult,
  type ReviewFailure,
  intentReviewEntries,
  reviewProblemFromCause,
  reviewProblemFromRefusal,
  unreadableAnswer,
} from './review';

export interface ReviewCatalogueRead {
  loading: boolean;
  targetKey?: string;
  entries?: ReviewEntry[];
  totalSubjects?: number;
  invariantTotal?: number;
  familyTotal?: number;
  empty?: boolean;
  problem?: ReviewFailure;
  facts: string;
  stale: boolean;
}

function catalogueAnswer(result: ReviewEntryListResult, facts: string): ReviewCatalogueRead {
  if (result.state === 'entries') {
    const entries = result.entries ?? [];
    const totalSubjects =
      typeof result.total_subjects === 'number' ? result.total_subjects : entries.length;
    const invariantTotal =
      typeof result.invariant_total === 'number'
        ? result.invariant_total
        : entries.filter((entry) => entry.selector_kind === 'invariant').length;
    const familyTotal =
      typeof result.family_total === 'number'
        ? result.family_total
        : entries.filter((entry) => entry.selector_kind === 'family').length;
    return {
      loading: false,
      entries,
      totalSubjects,
      invariantTotal,
      familyTotal,
      empty: entries.length === 0,
      facts,
      stale: false,
    };
  }
  if (result.state === 'refused') {
    return {
      loading: false,
      problem: result.refusal
        ? reviewProblemFromRefusal(result.refusal)
        : unreadableAnswer('refused'),
      facts,
      stale: false,
    };
  }
  return { loading: false, problem: unreadableAnswer(result.state), facts, stale: false };
}

export function useReviewCatalogue(
  repo: string,
  master: string,
  leaf: string | undefined,
  facts: string,
): ReviewCatalogueRead & { refresh: () => void } {
  const targetKey = `${repo}/${master}/${leaf ?? ''}`;
  const [read, setRead] = useState<ReviewCatalogueRead>({ loading: false, facts, stale: false });
  const [nonce, setNonce] = useState(0);
  const reads = useRef(0);
  const factsRef = useRef(facts);
  factsRef.current = facts;
  const stale = read.facts !== facts;
  useEffect(() => {
    let mounted = true;
    const askedFor = factsRef.current;
    const seq = ++reads.current;
    const live = () => mounted && reads.current === seq;
    setRead((previous) => ({ ...previous, loading: true, stale: false }));
    if (!leaf) {
      setRead({ loading: false, facts: askedFor, stale: false, targetKey });
      return () => void (mounted = false);
    }
    void intentReviewEntries(repo, master, leaf).then(
      (result) => {
        if (live()) setRead({ ...catalogueAnswer(result, askedFor), targetKey });
      },
      (cause: unknown) => {
        if (live())
          setRead({
            loading: false,
            problem: reviewProblemFromCause(cause),
            targetKey,
            facts: askedFor,
            stale: false,
          });
      },
    );
    return () => {
      mounted = false;
    };
  }, [repo, master, leaf, facts, nonce, targetKey]);
  const refresh = () => {
    setNonce((value) => value + 1);
  };
  if (read.targetKey !== targetKey) return { loading: true, facts, stale: false, refresh };
  return { ...read, stale, refresh };
}
