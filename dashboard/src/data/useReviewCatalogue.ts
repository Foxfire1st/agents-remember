// The reviewer's subject catalogue read: every recorded subject of the comparison being reviewed.
//
// It belongs to the reviewer, not to the task entry: the entry shows only the changed-intent summary
// and the catalogue is read when the reviewer is opened. Its key is the COMPARISON's identity alone --
// the task context and record (`repo/master/leaf/history`) plus the generation of the compared
// code trees and knowledge snapshots once the reviewer has read them -- so it is read once on entry, again when that identity
// moves (a refresh that reached a new candidate generation, ICR-R17) or when the reader asks, and
// never because an unrelated part of the workspace was republished.
//
// A superseded answer is dropped: each read takes the next sequence number and only the newest may
// write state, so a response for a previous comparison cannot overwrite the one on screen now.
import { useEffect, useRef, useState } from 'react';
import {
  comparisonGenerationOf,
  sameGeneration,
  type ComparisonGeneration,
} from '../panels/review/ReviewReadCache';
import {
  type ReviewEntry,
  type ReviewPayload,
  type ReviewOutline,
  type ReviewEntryListResult,
  type ReviewFailure,
  intentReviewEntries,
  reviewProblemFromCause,
  reviewProblemFromRefusal,
  unreadableAnswer,
} from './review';

export interface ReviewCatalogueRead {
  loading: boolean;
  // The task context and record this answer belongs to. A generation re-read of the same comparison
  // quarantines the prior semantic outline until its own answer lands.
  comparisonKey?: string;
  targetKey?: string;
  generation?: ComparisonGeneration;
  entries?: ReviewEntry[];
  outline?: ReviewOutline;
  totalSubjects?: number;
  invariantTotal?: number;
  familyTotal?: number;
  empty?: boolean;
  problem?: ReviewFailure;
}

// The requested generation counter and its admitted code-tree/knowledge-snapshot pair. The first
// observed pair is the one the entry catalogue already describes.
export interface ReviewCatalogueKey {
  repo: string;
  master: string;
  leaf: string;
  history?: string;
  generation?: number;
  comparisonGeneration?: ComparisonGeneration;
  // One recorded comparison by its number (MIK-R89 v2 rule 10), read-only; absent = the bound one.
  comparison?: number;
}

function catalogueAnswer(result: ReviewEntryListResult): ReviewCatalogueRead {
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
      outline: result.outline ?? undefined,
      totalSubjects,
      invariantTotal,
      familyTotal,
      empty: entries.length === 0,
    };
  }
  if (result.state === 'refused') {
    return {
      loading: false,
      problem: result.refusal
        ? reviewProblemFromRefusal(result.refusal)
        : unreadableAnswer('refused'),
    };
  }
  return { loading: false, problem: unreadableAnswer(result.state) };
}

export function useReviewCatalogue(
  comparison: ReviewCatalogueKey,
): ReviewCatalogueRead & { refresh: () => void } {
  const { repo, master, leaf, history, generation, comparisonGeneration } = comparison;
  const number = comparison.comparison;
  const comparisonKey = `${repo}/${master}/${leaf}/${history ?? 'live'}${number === undefined ? '' : `#${number}`}`;
  const targetKey = `${comparisonKey}/${generation ?? 0}`;
  const [read, setRead] = useState<ReviewCatalogueRead>({ loading: false });
  const [nonce, setNonce] = useState(0);
  const reads = useRef(0);
  const observed = useRef(comparisonGeneration);
  useEffect(() => {
    observed.current = comparisonGeneration;
  }, [comparisonGeneration]);
  useEffect(() => {
    let mounted = true;
    const seq = ++reads.current;
    const live = () => mounted && reads.current === seq;
    const boundGeneration = observed.current;
    setRead({ loading: true, comparisonKey, targetKey, generation: boundGeneration });
    void intentReviewEntries(repo, master, leaf, '', number).then(
      (result) => {
        if (live())
          setRead({
            ...catalogueAnswer(result),
            comparisonKey,
            targetKey,
            generation: boundGeneration,
          });
      },
      (cause: unknown) => {
        if (live())
          setRead({
            loading: false,
            problem: reviewProblemFromCause(cause),
            comparisonKey,
            targetKey,
            generation: boundGeneration,
          });
      },
    );
    return () => {
      mounted = false;
    };
  }, [repo, master, leaf, number, nonce, targetKey, comparisonKey]);
  const refresh = () => {
    setNonce((value) => value + 1);
  };
  if (read.targetKey !== targetKey) return { loading: true, refresh };
  // The initial catalogue describes the first observed pair: bind it without a redundant read.
  return { ...read, generation: boundGeneration(read.generation, comparisonGeneration), refresh };
}

// Reject old semantic fields synchronously, including the render before the observer starts a reread.
export function catalogueForComparison<Read extends ReviewCatalogueRead>(
  catalogue: Read,
  payload: ReviewPayload,
): Read {
  if (
    !catalogue.generation ||
    sameGeneration(catalogue.generation, comparisonGenerationOf(payload))
  )
    return catalogue;
  return {
    ...catalogue,
    loading: true,
    entries: undefined,
    outline: undefined,
    problem: undefined,
  };
}

function boundGeneration(
  stored: ComparisonGeneration | undefined,
  observed: ComparisonGeneration | undefined,
): ComparisonGeneration | undefined {
  if (!stored) return observed;
  if (stored.snapshots !== undefined || stored.trees !== observed?.trees) return stored;
  return observed;
}
