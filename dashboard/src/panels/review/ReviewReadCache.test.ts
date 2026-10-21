// The reviewer's per-comparison cache: bounded, least-recently-used, emptied by another generation.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, it } from 'vitest';
import type { ReviewPayload, ReviewResult } from '../../data/review';
import { REVIEW_CACHE_LIMIT, ReviewReadCache, sameGeneration } from './ReviewReadCache';

const recorded = (
  JSON.parse(
    readFileSync(
      path.join(
        path.dirname(new URL(import.meta.url).pathname),
        'familyReview.complete.captured.json',
      ),
      'utf8',
    ),
  ) as ReviewResult
).payload as ReviewPayload;

function payloadAt(afterTree: string, afterSnapshot?: string): ReviewPayload {
  return {
    ...recorded,
    source: {
      ...recorded.source,
      inventory: { ...recorded.source.inventory, after_code_tree_id: afterTree },
    },
    comparison:
      afterSnapshot === undefined
        ? undefined
        : { ...recorded.comparison!, after_snapshot_digest: afterSnapshot },
  };
}

it.each([REVIEW_CACHE_LIMIT + 5, REVIEW_CACHE_LIMIT * 3])(
  'keeps at most the bound of reviews and evicts the least recently used (%i inserted)',
  (inserted) => {
    const cache = new ReviewReadCache();
    const payload = payloadAt('tree', 'snap');
    cache.keepReview('subject-0', payload);
    for (let index = 1; index < inserted; index += 1) {
      // Reading the first subject keeps it recent while the others push the bound.
      expect(cache.review('subject-0')).toBe(payload);
      cache.keepReview(`subject-${index}`, payload);
    }
    expect(cache.sizes.reviews).toBe(REVIEW_CACHE_LIMIT);
    expect(cache.review('subject-0')).toBe(payload);
    expect(cache.review('subject-1')).toBeUndefined();
    expect(cache.review(`subject-${inserted - 1}`)).toBe(payload);
  },
);

it('empties the store when an answer belongs to another comparison generation', () => {
  const cache = new ReviewReadCache();
  cache.keepReview('family-a', payloadAt('tree-1', 'snap-1'));
  // A task-context answer compares no knowledge: it cannot move the snapshot pair.
  cache.keepReview('task', payloadAt('tree-1'));
  expect(cache.review('family-a')).toBeDefined();
  // A paged or refreshed answer is observed without being kept, and still invalidates.
  cache.observe(payloadAt('tree-1', 'snap-2'));
  expect(cache.review('family-a')).toBeUndefined();
  expect(cache.review('task')).toBeUndefined();
  cache.keepReview('family-b', payloadAt('tree-1', 'snap-2'));
  cache.keepReview('family-c', payloadAt('tree-2', 'snap-2'));
  expect(cache.review('family-b')).toBeUndefined();
  expect(cache.review('family-c')).toBeDefined();
});

it('treats code trees and compared snapshots as the generation', () => {
  expect(sameGeneration({ trees: 'a:b', snapshots: 'x:y' }, { trees: 'a:b' })).toBe(true);
  expect(sameGeneration({ trees: 'a:b', snapshots: 'x:y' }, { trees: 'a:c' })).toBe(false);
  expect(
    sameGeneration({ trees: 'a:b', snapshots: 'x:y' }, { trees: 'a:b', snapshots: 'x:z' }),
  ).toBe(false);
});

it('keeps an unshown answer only for the generation on screen and never lets it move that generation', () => {
  const cache = new ReviewReadCache();
  // Nothing was admitted yet: the unshown answer is kept, and the first admitted answer of another
  // generation still empties the cache, as it always did.
  cache.keepUnshown('task', payloadAt('tree-1'));
  expect(cache.review('task')).toBeDefined();
  cache.keepReview('family-a', payloadAt('tree-2', 'snap-2'));
  expect(cache.review('task')).toBeUndefined();
  // An unshown answer of the generation on screen is kept beside what was read for it.
  const current = payloadAt('tree-2');
  cache.keepUnshown('task', current);
  expect(cache.review('task')).toBe(current);
  expect(cache.review('family-a')).toBeDefined();
  // An unshown answer of an older generation is dropped: it empties nothing and moves nothing.
  cache.keepUnshown('late', payloadAt('tree-1'));
  expect(cache.review('late')).toBeUndefined();
  expect(cache.review('family-a')).toBeDefined();
  cache.keepReview('family-b', payloadAt('tree-2', 'snap-2'));
  expect(cache.sizes.reviews).toBe(3);
});
