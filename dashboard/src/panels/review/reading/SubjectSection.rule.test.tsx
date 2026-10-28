// Selected-area review supersedes consecutive central sections: every selected invariant or lane
// starts its own area. Switching area must not retain a preceding subject's separator or content.
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { treeViewStore } from '../triageViewPreference';
import { subjectBox } from './readingStyles';
import { PLACE, renderPage, selectInvariant, servePage, stubIntersection } from './reviewPage.test-utils';

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

it('starts each selected area without a separator left by the previous subject', async () => {
  stubIntersection();
  treeViewStore.getState().setView('all');
  servePage();
  renderPage();
  await selectInvariant();
  const sections = screen.getAllByTestId('review-subject-section');
  expect(sections).toHaveLength(1);
  expect(sections[0].hasAttribute('data-ruled')).toBe(false);
  expect(subjectBox.split(' ')).toEqual(expect.arrayContaining(ruleClasses('[&[data-ruled]]')));
  expect(screen.queryByTestId('review-lane-section')).toBeNull();

  fireEvent.click(screen.getByTestId('review-tree-lane'));
  await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
  expect(screen.queryByTestId('review-subject-section')).toBeNull();
  expect(screen.getByTestId('review-subjects').hasAttribute('data-subjects-drawn')).toBe(false);
  const lane = screen.getByTestId('review-lane-section');
  expect(lane.matches('[data-subjects-drawn] + *')).toBe(false);
});
