// What every part of the reading column shares: where the review is, the comparison on screen, how
// code is drawn, what is selected and opened, and how a part selects. One context, so the sections do
// not hand a dozen props down to each block.
import { createContext, useContext } from 'react';

import type { ReviewPayload } from '../../../data/review';
import type { ReviewTreeComparison } from '../../../data/reviewTrees';
import type { DiffMode } from '../../changeset/DiffPane';
import type { ReviewSubjectRef } from '../blockIdentity';
import type { ReviewSelection, ReviewSort } from '../reviewSelection';

// Where the review is: its target, and the repository id the tray's references name (the trees
// answer's `repository_id`, W29).
export interface ReviewPlace {
  repo: string;
  master: string;
  leaf: string;
  repository: string;
}

export interface ReadingContextValue {
  place: ReviewPlace;
  // The comparison the cards were read at (the shown one).
  comparison: ReviewTreeComparison | null;
  // The task context's last admitted answer.
  payload: ReviewPayload | null;
  planning: Map<string, string>;
  layout: DiffMode;
  collapse: boolean;
  sort: ReviewSort;
  selection: ReviewSelection | null;
  // The selected block's key, when a block is selected.
  selectedBlock: string | null;
  openGroups: ReadonlySet<string>;
  toggleGroup: (id: string) => void;
  select: (selection: ReviewSelection) => void;
  // The selected subject's own reading (its read's pending state, failure or answer), or null when
  // the subject is not the selected one.
  slot: (subject: ReviewSubjectRef) => React.ReactNode | null;
  // Each family's members (invariant selector ids) in the model's order, so a family's own member
  // list in its slot is the tree's and the column's order (rule 3, one order).
  familyMembers: ReadonlyMap<string, readonly string[]>;
}

export const ReadingContext = createContext<ReadingContextValue | null>(null);

export function useReading(): ReadingContextValue {
  const value = useContext(ReadingContext);
  if (!value) throw new Error('the reading column provides its context');
  return value;
}
