// Ruling 9498d6e4 (mockup styles.css `.subject + .subject`: a 1px grid rule and 1rem above): a rule
// stands between consecutive sections of the reading column, subject to subject across family groups,
// and above the lane when a subject section precedes it; none above the first. jsdom has no cascade,
// so the case checks which sections carry the rule's mark and that the rule is declared on it. On
// L181 #8 in All (reviewPage.test-utils.tsx; ReviewPage.order.test.tsx) the column holds three
// subject sections, then the lane.
import { cleanup, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { treeViewStore } from '../triageViewPreference';
import { subjectBox } from './readingStyles';
import { PLACE, renderPage, servePage, stubIntersection } from './reviewPage.test-utils';

const HASH = `#review?${new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf })}`;

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', `/${HASH}`);
  treeViewStore.getState().setView('changed');
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  treeViewStore.getState().setView('changed');
});

// The rule as the box declares it, under the condition given (`[&[data-ruled]]`, …).
const ruleClasses = (condition: string) => [
  `${condition}:bd-t-w_1px`,
  `${condition}:border-top-style_solid`,
  `${condition}:bd-t-c_grid`,
  `${condition}:mt_1rem`,
];

it('draws a rule between consecutive sections and above the lane, none above the first', async () => {
  stubIntersection();
  treeViewStore.getState().setView('all');
  servePage();
  renderPage();
  await waitFor(() => expect(screen.getAllByTestId('review-subject-section')).toHaveLength(3));
  await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
  const sections = screen.getAllByTestId('review-subject-section');
  expect(sections.map((section) => section.hasAttribute('data-ruled'))).toEqual([false, true, true]);
  expect(subjectBox.split(' ')).toEqual(expect.arrayContaining(ruleClasses('[&[data-ruled]]')));
  // The lane follows the subjects' list, which marks that it drew a section.
  const lane = screen.getByTestId('review-lane-section');
  expect(lane.previousElementSibling).toBe(screen.getByTestId('review-subjects'));
  expect(screen.getByTestId('review-subjects').hasAttribute('data-subjects-drawn')).toBe(true);
  expect(lane.className.split(' ')).toEqual(expect.arrayContaining(ruleClasses('[[data-subjects-drawn]_+_&]')));
});
