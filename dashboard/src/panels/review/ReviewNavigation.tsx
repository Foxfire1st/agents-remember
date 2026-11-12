// Recorded subject navigation. The catalogue supplies identity; the review supplies family content.
// The review page's tree is tree/ReviewTree.tsx; this module keeps the catalogue read, the subject the
// target or the reader names, and the comparison generation the surface observes.
import { useCallback, useEffect, useRef, useState } from 'react';
import { useReviewCatalogue } from '../../data/useReviewCatalogue';
import type { FamilySelection } from './FamilyTree';
import type { ReviewPageRequest } from './ReviewReadCycle';
import {
  comparisonGenerationOf,
  sameGeneration,
  type ComparisonGeneration,
} from './ReviewReadCache';
import type { ReviewPayload, ReviewSelectorKind } from '../../data/review';
import type { ReviewCatalogueRead } from '../../data/useReviewCatalogue';

export interface ReviewSubject {
  kind: ReviewSelectorKind;
  id: string;
}
// How a selection treats the family tree the reader has walked (MIK-R39). By default a selection
// starts the tree afresh; `keepTree` marks the selection of a row the tree shows. `page` asks for a
// page of the subject's review (a kept family's roster continuation).
export interface SelectionOptions {
  keepTree?: boolean;
  page?: ReviewPageRequest;
}

export interface ReviewNavigationState {
  catalogue: ReviewCatalogueRead & { refresh: () => void };
  subject?: ReviewSubject;
  onSelect: (
    subject: ReviewSubject | undefined,
    context?: FamilySelection,
    options?: SelectionOptions,
  ) => void;
}

// The navigation the surface drives: the state above plus the comparison it observes.
export interface ReviewNavigation extends ReviewNavigationState {
  // Report the comparison generation the surface is showing (code trees and known snapshots).
  // The first generation it reports is the one
  // the entry catalogue describes; a different pair later (a refresh that reached a new candidate
  // generation) re-reads the catalogue for it.
  observeComparison: (snapshots: ComparisonGeneration | undefined) => void;
}

// Why the change facts are unavailable, for the line that says so where the outline is missing
// (MIK-R86 v1 rule 11): the outline's own reason, the catalogue's, or the unconverted memory.
export function outlineUnavailableReason(catalogue: ReviewNavigationState['catalogue']): string | undefined {
  const reading = catalogue.loading || (!catalogue.entries && !catalogue.problem);
  if (reading || catalogue.outline?.state === 'outlined') return undefined;
  return (
    catalogue.outline?.detail ??
    catalogue.problem?.detail ??
    'The memory is not converted; change facts are unavailable.'
  );
}

// The page opens with nothing selected (MIK-R89 v2 rule 2; MIK-R86 v2 rule 7): the subject is the
// one the target names, or the reader's own choice. A catalogue that answers selects nothing.
export function useReviewNavigation(target: {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: 'recorded';
  // An older recorded comparison chosen in the selector (MIK-R89 v2 rule 10): its outline is read
  // with `comparison=<n>`. Absent: the bound comparison.
  comparison?: number;
}): ReviewNavigation {
  const [generation, setGeneration] = useState(0);
  const [identity, setIdentity] = useState<{ comparison: string; value: ComparisonGeneration }>();
  // The generation first reported for this task context and record; a generation reported under
  // another context is that context's first, not a new generation of this one.
  const comparison = `${target.repo}/${target.master}/${target.leaf}/${target.history ?? 'live'}`;
  const seen = useRef<{ comparison: string; snapshots: ComparisonGeneration } | undefined>(
    undefined,
  );
  const catalogue = useReviewCatalogue({
    repo: target.repo,
    master: target.master,
    leaf: target.leaf,
    history: target.history,
    generation,
    comparisonGeneration: identityForTask(identity, comparison),
    comparison: target.comparison,
  });
  const key = JSON.stringify(target);
  const [choice, setChoice] = useState<{ key: string; subject?: ReviewSubject }>();
  const subject = choice?.key === key ? choice.subject : initialSubject(target);
  const observeComparison = useCallback(
    (snapshots: ComparisonGeneration | undefined) => {
      const previous = seen.current;
      if (snapshots === undefined) return;
      if (previous?.comparison === comparison && sameGeneration(previous.snapshots, snapshots)) {
        if (previous.snapshots.snapshots === undefined && snapshots.snapshots !== undefined) {
          seen.current = { comparison, snapshots };
          setIdentity({ comparison, value: snapshots });
        }
        return;
      }
      seen.current = { comparison, snapshots };
      setIdentity({ comparison, value: snapshots });
      if (previous?.comparison === comparison) setGeneration((value) => value + 1);
    },
    [comparison],
  );
  return {
    catalogue,
    subject,
    onSelect: (subject) => setChoice({ key, subject }),
    observeComparison,
  };
}

// Report the complete generation to the navigation; code-only moves also invalidate the catalogue.
export function useObservedComparison(
  observeComparison: ReviewNavigation['observeComparison'],
  shown: ReviewPayload | null,
): void {
  const trees = shown?.source.inventory;
  const identity = shown?.comparison;
  const beforeTree = trees?.before_code_tree_id;
  const afterTree = trees?.after_code_tree_id;
  const beforeSnapshot = identity?.before_snapshot_digest;
  const afterSnapshot = identity?.after_snapshot_digest;
  const compared = identity?.knowledge_compared;
  useEffect(() => {
    if (!shown) return;
    observeComparison(comparisonGenerationOf(shown));
  }, [observeComparison, shown, beforeTree, afterTree, beforeSnapshot, afterSnapshot, compared]);
}

// A target that names a subject opens it (MIK-R86 v2 rule 7); otherwise nothing is selected.
export function initialSubject(target: {
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
}): ReviewSubject | undefined {
  return target.selectorKind && target.selectorId
    ? { kind: target.selectorKind, id: target.selectorId }
    : undefined;
}

function identityForTask(
  identity: { comparison: string; value: ComparisonGeneration } | undefined,
  comparison: string,
): ComparisonGeneration | undefined {
  return identity?.comparison === comparison ? identity.value : undefined;
}
