// D401 (the page compared with the mockup by eye; mockup `groups`, app.js `groupBox`, styles.css
// `.group > button.gh`, `.lrow`, `.dim`): an opened group's heading is the mockup's full-width bar, and
// its "… N more, one line each" reads as the mockup's quiet line. It stays a button that opens the rest
// in place (MIK-R89 v2 rule 5; MIK-R86 v2's class `unchanged`). On L181 #8 (reviewPage.test-utils.tsx)
// the unchanged group holds 31 locations.
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { L181_SUBJECT, renderPage, servePage, stubIntersection } from './reviewPage.test-utils';

beforeEach(() => window.localStorage.clear());

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const classes = (node: Element) => [...node.classList];

// The review's shared control look (ReviewLayout.tsx `reviewRoot`), read back from its class as
// selectors. jsdom has no cascade, so a case asks which buttons that rule reaches.
function reachedBySharedLook(node: Element): boolean {
  const surface = screen.getByTestId('review-surface');
  return [...surface.classList]
    .filter((name) => name.startsWith('[&_button:not('))
    .map((name) => name.slice(1, name.lastIndexOf(']:')).replaceAll('_', ' ').replaceAll('&', '[data-testid="review-surface"]'))
    .some((selector) => node.matches(selector));
}

it("draws an opened group as the mockup's, its '… N more' a quiet line that still opens the rest in place", async () => {
  stubIntersection();
  servePage();
  renderPage();
  await waitFor(() => expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
  const section = screen.getAllByTestId('review-subject-section').find((node) => node.dataset.subject === L181_SUBJECT)!;
  const group = within(section).getAllByTestId('review-location-group').find((node) => node.dataset.group === 'unchanged')!;

  const toggle = within(group).getByTestId('review-location-group-toggle');
  expect(reachedBySharedLook(toggle)).toBe(false);
  expect(classes(toggle)).toEqual(expect.arrayContaining(['bg_bgPanel', 'bd_none', 'py_0.45rem', 'px_0.7rem']));
  fireEvent.click(toggle);

  const more = within(group).getByTestId('review-location-group-more');
  expect(more.tagName).toBe('BUTTON');
  expect(more.textContent).toBe('… 27 more, one line each');
  expect(reachedBySharedLook(more)).toBe(false);
  expect(classes(more)).toEqual(expect.arrayContaining(['bg_transparent', 'w_100%', 'ta_left']));
  expect(classes(more).some((name) => name.startsWith('c_color-mix(in_oklab,_token(colors.muted)'))).toBe(true);

  fireEvent.click(more);
  expect(within(group).getAllByTestId('review-location-line')).toHaveLength(31);
  expect(within(group).queryByTestId('review-location-group-more')).toBeNull();
});

// Ruling 9498d6e4 (mockup app.js `groupBox`: `l.role`): an opened group's rows name each location's
// full role ("primary-authority", "enforcement", "proof"); the tree keeps the short word.
it("names each location's full role in an opened group's rows", async () => {
  stubIntersection();
  servePage();
  renderPage();
  await waitFor(() => expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
  const section = screen.getAllByTestId('review-subject-section').find((node) => node.dataset.subject === L181_SUBJECT)!;
  const roles = new Set(['primary-authority', 'enforcement', 'propagation-persistence', 'support', 'presentation', 'unclassified', 'proof']);
  for (const group of within(section).getAllByTestId('review-location-group')) {
    fireEvent.click(within(group).getByTestId('review-location-group-toggle'));
    fireEvent.click(within(group).getByTestId('review-location-group-more'));
    const shown = within(group).getAllByTestId('review-location-line').map((line) => line.children[1].textContent ?? '');
    expect(shown.filter((role) => !roles.has(role))).toEqual([]);
  }
  const unchanged = within(section).getAllByTestId('review-location-group').find((node) => node.dataset.group === 'unchanged')!;
  const first = within(unchanged).getAllByTestId('review-location-line')[0];
  expect(first.children[1].textContent).toBe('primary-authority');
});
