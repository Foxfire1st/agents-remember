// Whole mounted review page (layout B); only fetch is replaced by exact replies of this leaf's L37
// build. The page opens with nothing selected (MIK-R89 v2 rule 2); the tree is WP5's (tree/ReviewTree).
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ReviewSurface } from './ReviewSurface';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import { dashboardStore } from '../../data/store';
import { firstOutlineChange, outlineCounts } from './changeTriage';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';

import { catalogue, serve, target } from './l86.test-utils';
beforeEach(() => {
  resetCardCachesForTests();
  dashboardStore.getState().reset();
  localStorage.clear();
  treeViewStore.getState().setView('changed');
  treeOrderStore.getState().setOrder('priority');
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
// A family opens its members' overview (MIK-R211 v4 rule 10), drawn from the catalogue before the
// family's own answer; the disclosures are counted once that answer fills the overview's slot.
async function familyAnswered(view: ReturnType<typeof render>, familyId: string) {
  await waitFor(() => {
    const overview = view.getByTestId('review-family-overview');
    expect(overview.dataset.family).toBe(familyId);
    expect(within(overview).getByTestId('review-subject-slot').dataset.slotState).toBe('answered');
    expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
  });
}
// The family and subject rows the tree shows (its pinned rows and location rows aside).
const subjectRows = (rail: HTMLElement) =>
  [...rail.querySelectorAll<HTMLElement>('[data-tree-node]')].filter((node) =>
    ['family', 'member'].includes(node.dataset.treeNode ?? ''),
  );
it('opens with nothing selected, shows compact changed/all views and filters without disclosure', async () => {
  const { asked } = serve();
  const view = render(<ReviewSurface {...target} />);
  const rail = await view.findByTestId('review-tree');
  const counts = outlineCounts(catalogue.outline!)!;
  await waitFor(() =>
    expect(view.getByTestId('review-summary').textContent).toContain(`${counts.invariants} invariants changed`),
  );
  expect(asked.filter((url) => url.pathname === '/api/review/intent').map((url) => url.searchParams.has('selectorId'))).toEqual([false]);
  expect(rail.querySelector('[aria-current]')).toBeNull();
  expect(view.queryByTestId('review-subject-slot')).toBeNull();
  expect(rail.querySelectorAll('details')).toHaveLength(0);
  const views = within(view.getByTestId('review-tree-view'));
  expect(views.getByRole('button', { name: 'Changed' }).getAttribute('aria-pressed')).toBe('true');
  expect(subjectRows(rail)).toHaveLength(counts.families + counts.invariants);
  fireEvent.click(views.getByRole('button', { name: 'All' }));
  expect(views.getByRole('button', { name: 'All' }).getAttribute('aria-pressed')).toBe('true');
  const all = subjectRows(rail);
  expect(all).toHaveLength(catalogue.outline!.families.length + catalogue.outline!.invariants.length);
  expect(all).toHaveLength(116);
  const titled = rail.querySelectorAll<HTMLElement>('[data-tree-node="family"], [data-tree-node="member"], [data-tree-node="location"]');
  expect([...titled].every((node) => node.getAttribute('title'))).toBe(true);
  const reads = asked.length;
  fireEvent.change(view.getByTestId('review-family-filter'), { target: { value: 'INV-1XKX9ERN' } });
  expect(subjectRows(rail).length).toBeLessThan(116);
  expect(asked).toHaveLength(reads);
  disclosureContract(view.getByTestId('review-surface'));
});
// The tree offers a lane's Full page only for a destination with files (WP5, ReviewDestinations.tsx
// `TreePinned`); this leaf's lane has no file of unknown attribution.
it('opens each offered viewer filter, draws its first diff, and returns without reading or resetting state', async () => {
  const { asked } = serve();
  const view = render(<ReviewSurface {...target} />);
  const rail = await view.findByTestId('review-tree');
  await waitFor(() => expect(view.getByTestId('review-tree-lane').getAttribute('aria-label')).toBe('Code without invariant 138 files'));
  expect(view.getAllByTestId('review-tree-full-page').map((link) => link.dataset.destination)).toEqual(['unexplained']);
  const column = view.getByTestId('review-center-column');
  for (const trigger of [view.getByTestId('review-tree-full-page'), view.getByTestId('review-all-changes')]) {
    trigger.focus();
    column.scrollTop = 91;
    fireEvent.click(trigger);
    const page = await view.findByTestId('changeset-viewer');
    await waitFor(() => expect(within(page).queryByTestId('changeset-pane')).not.toBeNull());
    expect(page.querySelectorAll('details')).toHaveLength(0);
    expect(
      asked
        .filter((url) => url.pathname.startsWith('/api/changeset/'))
        .every((url) => url.searchParams.get('comparison') === '5' && !url.searchParams.has('mode')),
    ).toBe(true);
    const count = asked.length;
    fireEvent.keyDown(page, { key: 'j' });
    expect(rail.querySelector('[aria-current]')).toBeNull();
    fireEvent.click(within(page).getByRole('button', { name: '← Back to intent review' }));
    await waitFor(() => expect(view.queryByTestId('changeset-viewer')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(column.scrollTop).toBe(91);
    expect(asked).toHaveLength(count);
  }
});
it('shows the persisted worklist as its own open view and preserves the named subject', async () => {
  serve();
  const familyId = firstOutlineChange(catalogue.outline!, 'priority')!.selectorId;
  const view = render(
    <ReviewSurface
      {...target}
      selectorKind="family"
      selectorId={familyId}
    />,
  );
  await familyAnswered(view, familyId);
  const rail = view.getByTestId('review-tree');
  const worklist = view.getByTestId('review-tree-worklist');
  await waitFor(() => expect(worklist.getAttribute('aria-label')).toBe('Worklist 518 open'));
  const selection = rail.querySelector('[aria-current="true"]');
  fireEvent.click(worklist);
  await view.findByText(/Changed knowledge files are read in the change-set viewer/);
  expect(worklist.getAttribute('aria-pressed')).toBe('true');
  expect(view.queryByText('Item facts')).toBeNull();
  expect(view.queryByTestId('review-knowledge-patch')).toBeNull();
  disclosureContract(view.getByTestId('review-surface'));
  expect(rail.querySelector('[aria-current="true"]')).toBe(selection);
  fireEvent.click(selection!);
  await familyAnswered(view, familyId);
  expect(view.queryByTestId('review-worklist-view')).toBeNull();
  disclosureContract(view.getByTestId('review-surface'));
});

