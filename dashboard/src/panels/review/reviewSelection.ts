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

// The Sort menu: the seven deterministic sorts (MIK-R89 v2) plus the judgment categories (MIK-R187).
export type DeterministicSort =
  | 'priority'
  | 'authored'
  | 'currentness'
  | 'invariants-at-location'
  | 'incoming-links'
  | 'without-proof'
  | 'open-weaknesses';

export type JudgmentSort = `judgment:${string}`;

export type ReviewSort = DeterministicSort | JudgmentSort;

export const isJudgmentSort = (sort: string): sort is JudgmentSort =>
  sort.startsWith('judgment:') && sort.length > 'judgment:'.length;

export const judgmentCategoryOf = (sort: string): string | null =>
  isJudgmentSort(sort) ? sort.slice('judgment:'.length) : null;

export const judgmentSortFor = (category: string): JudgmentSort => `judgment:${category}`;

const BASE_REVIEW_SORTS: readonly ReviewSort[] = [
  'priority',
  'authored',
  'currentness',
  'invariants-at-location',
  'incoming-links',
  'without-proof',
  'open-weaknesses',
];

export const REVIEW_SORTS: readonly ReviewSort[] = new Proxy(BASE_REVIEW_SORTS, {
  get(target, prop, receiver) {
    if (prop === 'includes') {
      return (searchElement: ReviewSort, fromIndex?: number): boolean => {
        if (typeof searchElement === 'string' && isJudgmentSort(searchElement)) return true;
        return Array.prototype.includes.call(target, searchElement, fromIndex);
      };
    }
    return Reflect.get(target, prop, receiver);
  },
});

const DETERMINISTIC_SORT_LABELS: Record<DeterministicSort, string> = {
  priority: 'Priority',
  authored: 'Authored',
  currentness: 'Memory currentness',
  'invariants-at-location': 'Most invariants on the location',
  'incoming-links': 'Most incoming links',
  'without-proof': 'Invariants without proof first',
  'open-weaknesses': 'Most open weaknesses',
};

export const SORT_LABELS: Record<ReviewSort, string> = new Proxy(
  DETERMINISTIC_SORT_LABELS as Record<ReviewSort, string>,
  {
    get(target, prop) {
      if (typeof prop === 'string') {
        if (prop in target) return target[prop as DeterministicSort];
        if (isJudgmentSort(prop)) return `✦ ${prop.slice('judgment:'.length)}`;
      }
      return Reflect.get(target, prop);
    },
  },
);

export function sortLabel(sort: ReviewSort): string {
  return SORT_LABELS[sort] ?? String(sort);
}

// The four sorts fed by the card read's memory values; unavailable together when those are.
export const MEMORY_SORTS: ReadonlySet<ReviewSort> = new Set<ReviewSort>([
  'invariants-at-location',
  'incoming-links',
  'without-proof',
  'open-weaknesses',
]);
