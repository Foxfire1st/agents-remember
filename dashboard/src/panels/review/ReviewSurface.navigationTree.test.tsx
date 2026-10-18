// Whole mounted R86 surface; only fetch is replaced by exact replies of this leaf's L37 build.
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ReviewSurface } from './ReviewSurface';
import { dashboardStore } from '../../data/store';
import { firstOutlineChange, outlineCounts } from './changeTriage';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';

import { catalogue, selectedMemberId, serve, target } from './l86.test-utils';
beforeEach(() => {
  dashboardStore.getState().reset();
  localStorage.clear();
  treeViewStore.getState().setView('changed');
  treeOrderStore.getState().setOrder('triage');
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function disclosureContract(root: HTMLElement) {
  const details = [...root.querySelectorAll('details')];
  expect(details.length).toBeLessThanOrEqual(20);
  for (const element of details) {
    expect(['identifiers', 'provenance']).toContain(element.dataset.disclosure);
    expect(element.querySelector('[data-review-content]')).toBeNull();
  }
}
it('starts at the actual first change, shows compact changed/all views and filters without disclosure', async () => {
  const { asked } = serve();
  const view = render(<ReviewSurface {...target} />);
  const rail = await view.findByTestId('review-catalogue-navigation');
  await view.findByTestId('review-center-family');
  const first = firstOutlineChange(catalogue.outline!, 'triage')!;
  expect(
    asked.find((url) => url.pathname === '/api/review/intent')?.searchParams.get('selectorId'),
  ).toBe(first.selectorId);
  expect(rail.querySelectorAll('details')).toHaveLength(0);
  const counts = outlineCounts(catalogue.outline!)!;
  await waitFor(() =>
    expect(view.getByTestId('review-navigation-summary').textContent).toContain(
      `${counts.invariants} invariants changed`,
    ),
  );
  fireEvent.click(within(rail).getByRole('button', { name: /View: Changed/ }));
  expect(within(rail).getByRole('button', { name: /View: All/ })).toBeTruthy();
  const all = rail.querySelectorAll('[data-tree-node]');
  expect(all).toHaveLength(116);
  expect([...all].every((node) => node.getAttribute('title'))).toBe(true);
  const reads = asked.length;
  fireEvent.change(within(rail).getByRole('searchbox'), { target: { value: 'INV-1XKX9ERN' } });
  expect(rail.querySelectorAll('[data-tree-node]').length).toBeLessThan(116);
  expect(asked).toHaveLength(reads);
  disclosureContract(view.getByTestId('review-surface'));
});
it('opens each existing viewer filter, draws its first diff, and returns without reading or resetting state', async () => {
  const { asked } = serve();
  const view = render(<ReviewSurface {...target} />);
  const rail = await view.findByTestId('review-catalogue-navigation');
  await view.findByTestId('review-center-family');
  await waitFor(() => expect(rail.textContent).toMatch(/Changes without an invariant\s*167/));
  const surface = view.getByTestId('review-surface');
  for (const destination of ['Changes without an invariant', 'Changes of unknown attribution', 'All changes']) {
    const trigger = within(rail).getByRole('button', { name: new RegExp(`^${destination}`) });
    trigger.focus();
    surface.scrollTop = 91;
    const selected = rail.querySelector('[aria-current="true"]');
    fireEvent.click(trigger);
    const page = await view.findByTestId('changeset-viewer');
    await waitFor(() => expect(within(page).queryByTestId('changeset-pane')).not.toBeNull());
    expect(page.querySelectorAll('details')).toHaveLength(0);
    expect(
      asked
        .filter((url) => url.pathname.startsWith('/api/changeset/'))
        .every(
          (url) => url.searchParams.get('comparison') === '5' && !url.searchParams.has('mode'),
        ),
    ).toBe(true);
    const count = asked.length;
    fireEvent.keyDown(page, { key: 'j' });
    expect(rail.querySelector('[aria-current="true"]')).toBe(selected);
    fireEvent.click(within(page).getByRole('button', { name: '← Back to intent review' }));
    await waitFor(() => expect(view.queryByTestId('changeset-viewer')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(surface.scrollTop).toBe(91);
    expect(asked).toHaveLength(count);
  }
});
it('shows the persisted worklist as its own open view and preserves the named subject', async () => {
  serve();
  const view = render(
    <ReviewSurface
      {...target}
      selectorKind="family"
      selectorId={firstOutlineChange(catalogue.outline!, 'triage')!.selectorId}
    />,
  );
  await view.findByTestId('review-center-family');
  const rail = view.getByTestId('review-catalogue-navigation');
  await waitFor(() => expect(rail.textContent).toMatch(/Worklist\s*518 open/));
  const selection = rail.querySelector('[aria-current="true"]');
  fireEvent.click(within(rail).getByRole('button', { name: /^Worklist/ }));
  await view.findByText(/Changed knowledge files are read in the change-set viewer/);
  expect(view.queryByText('Item facts')).toBeNull();
  expect(view.queryByTestId('review-knowledge-patch')).toBeNull();
  disclosureContract(view.getByTestId('review-surface'));
  expect(rail.querySelector('[aria-current="true"]')).toBe(selection);
  fireEvent.click(selection!);
  await view.findByTestId('review-center-family');
  disclosureContract(view.getByTestId('review-surface'));
});

it('keeps a card Full file in the card with its intent markers rather than opening the page', async () => {
  const { asked } = serve();
  const view = render(
    <ReviewSurface {...target} selectorKind="invariant" selectorId={selectedMemberId} />,
  );
  await view.findByTestId('review-center-member');
  const card = (await view.findAllByTestId('review-expression-card')).find(
    (element) =>
      element.dataset.path === 'mcp/src/agents_remember/application/knowledge_curator_ingest.py',
  );
  expect(card).toBeTruthy();
  fireEvent.click(within(card!).getByRole('button', { name: 'Full file' }));
  await view.findByTestId('review-card-full-file-content');
  expect(view.queryByTestId('changeset-viewer')).toBeNull();
  expect(asked.some((url) => url.pathname.startsWith('/api/changeset/'))).toBe(false);
  disclosureContract(view.getByTestId('review-surface'));
});
