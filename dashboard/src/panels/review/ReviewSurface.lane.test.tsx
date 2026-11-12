// R86 replaces the lane/explorer's file lists with the actual comparison viewer; MIK-R89 v2 rule 7
// reaches it from the tree: the one "All changes" link and "Code without invariant"'s "Full page".
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ReviewSurface } from './ReviewSurface';
import { dashboardStore } from '../../data/store';
import { captured, serve, target } from './l86.test-utils';
import type { ReviewUnexplainedLane } from '../../data/reviewLane';
import { treeViewStore } from './triageViewPreference';
beforeEach(() => {
  dashboardStore.getState().reset();
  treeViewStore.getState().setView('changed');
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const lane = captured<{ lane: ReviewUnexplainedLane }>('l86.lane.captured.json').lane;
const laneRow = (view: ReturnType<typeof render>) => view.getByTestId('review-tree-lane');

it('draws each destination in the existing page in the lane answer order, with its one reason sentence', async () => {
  serve();
  const view = render(<ReviewSurface {...target} />);
  await waitFor(() => expect(laneRow(view).getAttribute('aria-label')).toBe(`Code without invariant ${lane.unexplained_changes!.bucket_files} files`));
  const full = view.getAllByTestId('review-tree-full-page');
  expect(full.map((link) => link.dataset.destination)).toEqual(['unexplained']);
  fireEvent.click(full[0]);
  const page = await view.findByTestId('changeset-viewer');
  await waitFor(() => expect(page.querySelector('[data-active="true"]')).not.toBeNull());
  const code = within(page).getByText('changed code (167)').parentElement!;
  const paths = [...code.querySelectorAll('[data-active]')].map((row) => row.textContent);
  const expected = lane.unexplained_changes!.files.map((file) => file.path);
  expect(expected.every((path, index) => paths[index]?.includes(path))).toBe(true);
  expect(
    within(page).getAllByText(/^No recorded realization or proof entry explains these changes/),
  ).toHaveLength(1);
  expect(within(page).queryByText(/changed onboarding/)).toBeNull();
  fireEvent.click(within(page).getByRole('button', { name: /^Unknown attribution/ }));
  await waitFor(() => expect(within(page).getByText('changed code (2)')).toBeTruthy());
  for (const file of lane.unknown_attribution!.files)
    expect(within(page).getByText(file.path)).toBeTruthy();
  expect(
    within(page).getAllByText(/^Whether an entry explains these changes is not established/),
  ).toHaveLength(1);
  expect(page.querySelectorAll('details')).toHaveLength(0);
});

it('keeps All working while lane counts are being read, and never supplies guessed buckets', async () => {
  const { release } = serve(undefined, 'lane');
  const view = render(<ReviewSurface {...target} />);
  // The tree's lane row reads "…" while the lane is read; the summary's count is read 1's own (W14).
  await waitFor(() => expect(laneRow(view).getAttribute('aria-label')).toBe('Code without invariant …'));
  await waitFor(() =>
    expect(view.getByTestId('review-summary-lane').textContent).toContain(`${lane.unexplained} files without invariant`),
  );
  expect(view.queryAllByTestId('review-tree-full-page')).toHaveLength(0);
  fireEvent.click(view.getByTestId('review-all-changes'));
  const page = await view.findByTestId('changeset-viewer');
  expect(
    within(page)
      .getByRole('button', { name: /^Changes without an invariant/ })
      .hasAttribute('disabled'),
  ).toBe(true);
  expect(
    within(page)
      .getByRole('button', { name: /^Unknown attribution/ })
      .hasAttribute('disabled'),
  ).toBe(true);
  await waitFor(() => expect(within(page).getByText('changed code (179)')).toBeTruthy());
  expect(within(page).getByRole('button', { name: /^All / }).hasAttribute('disabled')).toBe(false);
  await act(async () => release());
  await waitFor(() =>
    expect(
      within(page)
        .getByRole('button', { name: /^Changes without an invariant/ })
        .hasAttribute('disabled'),
    ).toBe(false),
  );
});

it('states an unanswered leaf worklist as reading in its own view, never as no gate item', async () => {
  const { release } = serve(undefined, 'worklist');
  const view = render(<ReviewSurface {...target} />);
  const worklist = await view.findByTestId('review-tree-worklist');
  await waitFor(() => expect(worklist.getAttribute('aria-label')).toBe('Worklist …'));
  fireEvent.click(worklist);
  expect(view.getByTestId('review-worklist-view').textContent).toContain(
    "Reading the leaf's worklist",
  );
  expect(view.queryByText('No worklist item names this record.')).toBeNull();
  await act(async () => release());
  await waitFor(() => expect(worklist.textContent).toMatch(/Worklist\s*518 open/));
});
