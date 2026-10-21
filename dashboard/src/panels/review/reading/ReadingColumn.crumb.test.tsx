// The reading head's crumb as the mockup writes it (ruling 9498d6e4; app.js `updateCrumb`,
// styles.css `.rhead .crumb b`): "Change set · comparison #n", then the subject's id in bold ink, then
// for a block its file's name and its after range; for the lane "Code without invariant" in bold, then
// a file's name with no range. It follows the selection (MIK-R89 v2 rule 9), not the scroll.
// On L181 #8 (reviewPage.test-utils.tsx): INV-1ZKP4R, its first location turn_ended.ts L1697–1817.
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { LANE_PATH, PLACE, renderPage, servePage, stubIntersection } from './reviewPage.test-utils';

const HASH = `#review?${new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf })}`;

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', `/${HASH}`);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function opened() {
  await waitFor(() => expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
  await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
}

const crumb = () => screen.getByTestId('review-crumb');

// The crumb's bold part: one <b>, its text, drawn in ink at weight 600.
function bold(): string {
  const parts = crumb().querySelectorAll('b');
  expect(parts).toHaveLength(1);
  expect([...parts[0].classList]).toEqual(expect.arrayContaining(['c_ink', 'fw_600']));
  return parts[0].textContent ?? '';
}

it("names a selected subject's id in bold after the change set", async () => {
  stubIntersection();
  servePage();
  renderPage();
  await opened();
  expect(crumb().textContent).toBe('Change set · comparison #8');
  const row = screen.getAllByTestId('review-family-member-open').find((node) => node.textContent?.includes('INV-1ZKP4R'))!;
  fireEvent.click(row);
  await waitFor(() => expect(crumb().textContent).toBe('Change set · comparison #8 › INV-1ZKP4R'));
  expect(bold()).toBe('INV-1ZKP4R');
});

it("names a selected block's subject in bold, then its file's name and its after range", async () => {
  stubIntersection();
  servePage();
  renderPage();
  await opened();
  fireEvent.click(screen.getAllByTestId('review-tree-location')[0]);
  await waitFor(() => expect(crumb().textContent).toBe('Change set · comparison #8 › INV-1ZKP4R › turn_ended.ts L1697–1817'));
  expect(bold()).toBe('INV-1ZKP4R');
  // The selected card stands in the section the crumb names.
  const card = screen.getAllByTestId('review-expression-card').find((node) => node.dataset.selected === 'true')!;
  expect(card.closest<HTMLElement>('[data-testid="review-subject-section"]')!.querySelector('span')!.textContent).toBe('INV-1ZKP4R');
});

it("names the lane in bold, then a selected lane file's name with no range", async () => {
  stubIntersection();
  servePage();
  renderPage();
  await opened();
  const name = LANE_PATH.split('/').pop()!;
  const row = within(screen.getByTestId('review-tree-lane-files'))
    .getAllByRole('button')
    .find((node) => node.textContent?.includes(name))!;
  fireEvent.click(row);
  await waitFor(() => expect(crumb().textContent).toBe(`Change set · comparison #8 › Code without invariant › ${name}`));
  expect(bold()).toBe('Code without invariant');
});
