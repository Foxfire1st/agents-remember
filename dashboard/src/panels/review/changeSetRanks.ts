import { isJudgmentSort, type DeterministicSort, type ReviewSort } from './reviewSelection';
import type { ReviewOutlineMeaning } from '../../data/review';
import type { OutlineBadge } from './changeTriage';
import type { LocationRole, SubjectCurrentness } from './changeSetTypes';

// ---- ranks ----

export const EFFECT_RANK: Readonly<Record<string, number>> = {
  retire: 0,
  weaken: 0,
  replace: 0,
  strengthen: 1,
  restore: 1,
  introduce: 1,
  split: 1,
  merge: 1,
  clarify: 2,
};
export const EFFECT_ORDER = Object.keys(EFFECT_RANK);
export const NO_EFFECT = 3;

const CHANGE_RANK: Readonly<Record<string, number>> = {
  removed: 0,
  changed: 1,
  added: 2,
  'code changed': 3,
  joined: 4,
  left: 4,
  unknown: 5,
  unchanged: 6,
};

export const ROLE_ORDER: readonly LocationRole[] = [
  'primary-authority',
  'enforcement',
  'propagation-persistence',
  'support',
  'presentation',
  'unclassified',
  'proof',
];
export const ROLE_LABEL: Readonly<Record<LocationRole, string>> = {
  'primary-authority': 'primary',
  enforcement: 'enforce',
  'propagation-persistence': 'persist',
  support: 'support',
  presentation: 'present',
  unclassified: '—',
  proof: 'proof',
};

export const CURRENTNESS_RANK: Readonly<Record<SubjectCurrentness, number>> = {
  stale: 0,
  unverifiable: 1,
  unrealized: 2,
  current: 3,
  'not measured': 4,
};

// The Sort popup's short reasons (mockup app.js:243-246). Each of the first three ends its one-line row
// in the popup's 350 px (d9a1ec8f item 4); Memory currentness's order is `CURRENTNESS_RANK`.
const DETERMINISTIC_SORT_HINTS: Readonly<Record<DeterministicSort, string>> = {
  priority: 'retire/weaken first, then size',
  authored: 'order the curator wrote',
  currentness: 'least current first',
  'invariants-at-location': 'invariants recorded at the location’s path',
  'incoming-links': 'links into the location’s invariant',
  'without-proof': 'invariants the index lists without proof',
  'open-weaknesses': 'open weaknesses recorded at the location’s path',
};

export const SORT_HINTS: Readonly<Record<ReviewSort, string>> = new Proxy(
  DETERMINISTIC_SORT_HINTS as Record<ReviewSort, string>,
  {
    get(target, prop) {
      if (typeof prop === 'string') {
        if (prop in target) return target[prop as DeterministicSort];
        if (isJudgmentSort(prop)) return 'highest grade first, then Priority';
      }
      return Reflect.get(target, prop);
    },
  },
);

export function effectRank(meaning: ReviewOutlineMeaning | undefined): number {
  if (meaning?.state !== 'steps') return NO_EFFECT;
  return Math.min(NO_EFFECT, ...meaning.labels.map((label) => EFFECT_RANK[label] ?? NO_EFFECT));
}

export function changeRank(badge: OutlineBadge | undefined): number {
  if (badge === undefined) return CHANGE_RANK.unknown;
  if (badge in CHANGE_RANK) return CHANGE_RANK[badge];
  // A family's "N changed" ranks as changed.
  return /^\d+ changed$/.test(badge) ? CHANGE_RANK.changed : CHANGE_RANK.unknown;
}

