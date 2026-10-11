// MIK-R43 F04 mounted: the real-ingest two-attempt family on the real ReviewSurface.
//
// Bodies are the R2 successor captures (l43r2.f04.*) served by the leaf build over
// scratch memory the real knowledge-ingest command wrote: family FAM-8V48H2 shows
// weaken (attempt 1, own rev 1) then clarify (attempt 2, own rev 2). Only fetch is
// replaced. R1 captures are untouched.
import { cleanup, render, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewEntryListResult, ReviewResult } from '../../data/review';
import type { ReviewTreesResult } from '../../data/reviewTrees';
import { ReviewSurface } from './ReviewSurface';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';
import { captured } from './l43.test-utils';

vi.setConfig({ testTimeout: 30000 });

const entries = captured<ReviewEntryListResult>('l43r2.f04.entries.captured.json');
const family = captured<ReviewResult>('l43r2.f04.family.captured.json');
const trees = captured<ReviewTreesResult>('l43r2.f04.trees.captured.json');
const target = {
  repo: 'agents-remember',
  master: '260928-f04-proof',
  leaf: '260928-MIK-L43-F04',
  onBack: () => undefined,
};
const familySelector = entries.outline!.families.find((one) => one.id === 'FAM-8V48H2')!
  .selector_id;

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
  expect(block.textContent).toContain('ROW-FM83GE');
  expect(block.textContent).toContain('ROW-Z3V0PQ');
  expect(block.textContent).toContain('weaken');
  expect(block.textContent).toContain('clarify');
  expect(block.textContent).toContain('revision 1');
  expect(block.textContent).toContain('revision 2');
  view.unmount();
});
