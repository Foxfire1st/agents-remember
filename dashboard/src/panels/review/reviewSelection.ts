// What the review page has selected, and the deterministic sorts (MIK-R89 v2 rules 2, 3 and 9).
//
// The page opens with nothing selected (`null`). A tree row selects its subject, a block selects
// itself, and a lane row or hunk selects that part of "Code without invariant". The URL carries the
// same selection (blockIdentity.ts `formatSelection`).
import type { ReviewBlockIdentity, ReviewSubjectRef } from './blockIdentity';

export type ReviewSelection =
  | { kind: 'subject'; subject: ReviewSubjectRef; family?: string }
  | { kind: 'block'; block: ReviewBlockIdentity }
  | { kind: 'lane'; path?: string };

// L89's Sort menu: the deterministic sorts only. L183 adds the judgment group.
export type ReviewSort =
  | 'priority'
  | 'authored'
  | 'currentness'
  | 'invariants-at-location'
  | 'incoming-links'
  | 'without-proof'
  | 'open-weaknesses';

export const REVIEW_SORTS: readonly ReviewSort[] = [
  'priority',
  'authored',
  'currentness',
  'invariants-at-location',
  'incoming-links',
  'without-proof',
  'open-weaknesses',
];

export const SORT_LABELS: Record<ReviewSort, string> = {
  priority: 'Priority',
  authored: 'Authored',
  currentness: 'Memory currentness',
  'invariants-at-location': 'Most invariants on the location',
  'incoming-links': 'Most incoming links',
  'without-proof': 'Invariants without proof first',
  'open-weaknesses': 'Most open weaknesses',
};

// The four sorts fed by the card read's memory values; unavailable together when those are.
export const MEMORY_SORTS: ReadonlySet<ReviewSort> = new Set<ReviewSort>([
  'invariants-at-location',
  'incoming-links',
  'without-proof',
  'open-weaknesses',
]);
