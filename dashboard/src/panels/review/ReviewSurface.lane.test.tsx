// R86 replaces the lane/explorer's file lists with the actual comparison viewer.
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
it('draws each destination in the existing page in the lane answer order, with its one reason sentence', async () => {
  serve();
  const view = render(<ReviewSurface {...target} />);
  const rail = await view.findByTestId('review-catalogue-navigation');
  await waitFor(() => expect(rail.textContent).toMatch(/Unexplained changes\s*167/));
  fireEvent.click(within(rail).getByRole('button', { name: /^Unexplained changes/ }));
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
  const rail = await view.findByTestId('review-catalogue-navigation');
  await view.findByTestId('review-center-family');
  expect(view.getByTestId('review-navigation-summary').textContent).toContain(
    '… files unexplained',
  );
  fireEvent.click(within(rail).getByRole('button', { name: /^All changes/ }));
  const page = await view.findByTestId('changeset-viewer');
  expect(
    within(page)
      .getByRole('button', { name: /^Unexplained/ })
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
        .getByRole('button', { name: /^Unexplained/ })
        .hasAttribute('disabled'),
    ).toBe(false),
  );
});
it('states an unanswered leaf worklist as reading in its own view, never as no gate item', async () => {
  const { release } = serve(undefined, 'worklist');
  const view = render(<ReviewSurface {...target} />);
  const rail = await view.findByTestId('review-catalogue-navigation');
  await view.findByTestId('review-center-family');
  fireEvent.click(within(rail).getByRole('button', { name: /^Worklist/ }));
  expect(view.getByTestId('review-worklist-view').textContent).toContain(
    "Reading the leaf's worklist",
  );
  expect(view.queryByText('No worklist item names this record.')).toBeNull();
  await act(async () => release());
  await waitFor(() => expect(rail.textContent).toMatch(/Worklist\s*673/));
});
