// Synthetic read scheduling over genuine comparison operands; no classification is invented.
import { act, renderHook } from '@testing-library/react';
import { expect, it } from 'vitest';
import captured from './l86.member.captured.json';
import capturedTrees from './l86.trees.captured.json';
import type { ReviewPayload } from '../../data/review';
import type { ReviewTreesRead } from '../../data/reviewTrees';
import { treeComparisonNumber } from '../../data/reviewTrees';
import { useReviewChangePage } from './ReviewSurface';

const payload = captured.payload as unknown as ReviewPayload;
const comparison = treeComparisonNumber(payload.limitations)!;
const ready: ReviewTreesRead = { phase: 'trees', trees: capturedTrees as unknown as Extract<ReviewTreesRead, { phase: 'trees' }>['trees'] };
const current = { repo: payload.candidate.repository_id, master: payload.candidate.master,
  leaf: payload.candidate.leaf_id, comparison, payload, laneRead: null };

it('enriches an already open change page when its same-comparison worklist arrives', () => {
  const { result, rerender } = renderHook(({ read }: { read: ReviewTreesRead }) => useReviewChangePage({ ...current, leafTrees: read }),
    { initialProps: { read: { phase: 'loading' } as ReviewTreesRead } });
  act(() => result.current.openChanges('unexplained'));
  expect(result.current.changePage?.kind).toBe('comparison');
  rerender({ read: ready });
  const page = result.current.changePage;
  expect(page?.kind === 'comparison' && page.context.worklist === capturedTrees.worklist).toBe(true);
});

it('never borrows an answered worklist from a different comparison', () => {
  const other = structuredClone(ready);
  if (other.phase === 'trees' && other.trees.comparison) other.trees.comparison.number += 1;
  const { result } = renderHook(() => useReviewChangePage({ ...current, leafTrees: other }));
  act(() => result.current.openChanges('unexplained'));
  const page = result.current.changePage;
  expect(page?.kind === 'comparison' && page.context.worklist === undefined).toBe(true);
});
