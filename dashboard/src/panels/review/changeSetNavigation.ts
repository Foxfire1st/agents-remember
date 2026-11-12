import { createContext } from 'react';
import type { ReviewChangeFilter } from '../changeset/reviewChangeSet';
export type OpenReviewChanges = (
  filter: ReviewChangeFilter,
  path?: string,
  kind?: 'code' | 'memory',
) => void;
export const ReviewChangeSetNavigation = createContext<OpenReviewChanges | null>(null);
