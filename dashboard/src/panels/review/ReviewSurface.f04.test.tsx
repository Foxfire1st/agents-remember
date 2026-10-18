// F04 preserved through today's native writer in a disposable two-leaf world.
// Leaf A authors family/invariant revision 1 without a judgment (R07); leaf B's two real
// knowledge-ingest calls produce weaken at family revision 2, then clarify at revision 3.
// The first B history's lifecycle-only closed flag is an explicitly disclosed fixture closeout;
// judgment fields are unedited. All response bytes are genuine current public-route captures.
// See l57.f04-native.capture-provenance.json; the obsolete pre-judgment bodies are not used.
import { cleanup, render, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewEntryListResult, ReviewResult } from '../../data/review';
import type { ReviewTreesResult } from '../../data/reviewTrees';
import { ReviewSurface } from './ReviewSurface';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';
import { captured } from './l43.test-utils';

const entries = captured<ReviewEntryListResult>('l57.f04-native.entries.captured.json');
const family = captured<ReviewResult>('l57.f04-native.family.captured.json');
const trees = captured<ReviewTreesResult>('l57.f04-native.trees.captured.json');
const candidate = family.payload!.candidate;
const target = {
  repo: candidate.repository_id,
  master: candidate.master,
  leaf: candidate.leaf_id,
  onBack: () => undefined,
};
const familySelector = family.payload!.knowledge.revision_selection!.record_id;

beforeEach(() => {
  localStorage.clear();
  treeViewStore.getState().setView('changed');
  treeOrderStore.getState().setOrder('triage');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      let body: unknown;
      if (url.pathname === '/api/review/intent/entries') body = entries;
      else if (url.pathname === '/api/review/intent') body = family;
      else if (url.pathname === '/api/review/trees') body = trees;
      else throw new Error(`unexpected request ${url.pathname}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('shows the real-ingest family weaken then clarify in the tree and the block', async () => {
  const view = render(
    <ReviewSurface
      repo={target.repo}
      master={target.master}
      leaf={target.leaf}
      selectorKind="family"
      selectorId={familySelector}
      onBack={() => undefined}
    />,
  );
  // Tree badge from the outline: both recorded labels in attempt order.
  const rail = await view.findByTestId('review-catalogue-navigation');
  const badges = () =>
    within(rail)
      .getAllByTestId('review-meaning-badge')
      .map((node) => node.textContent);
  expect(badges()).toContain('weaken → clarify');
  // Reading-area block: both writer-minted rows with their fields.
  const block = await view.findByTestId('review-recorded-steps');
  expect(block.textContent).toContain("These are the leaf's successive recorded judgments.");
  expect(block.textContent).toContain('ROW-YXKVEC');
  expect(block.textContent).toContain('ROW-NVYMV1');
  expect(block.textContent).toContain('weaken');
  expect(block.textContent).toContain('clarify');
  expect(block.textContent).toContain('revision 2');
  expect(block.textContent).toContain('revision 3');
  view.unmount();
});
