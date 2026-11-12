// MIK-R89 v2 rule 1 as amended by 719ade09 (D401; mockup app.js:213 and :227, styles.css:95-96 and
// 153): on the review page, the tree's "+N −N" line counts of a location row and of a lane file row
// draw the added count in mint and the removed count in alarm red, at the row reason's 0.7rem, each
// with its sign in the text, so colour is never the only signal. On L181 #8 (reviewPage.test-utils.tsx);
// the lane row's counts arrive once the reading column has read the file's diff (LaneSection.tsx).
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { engage, renderPage, servePage, stubInViewAtFirstPaint } from '../reading/reviewPage.test-utils';
import { serveMeasuredTrees } from '../lineChanges.test-utils';

beforeEach(() => window.localStorage.clear());

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const COUNTS = /\+\d+ −\d+/;
const classes = (node: Element) => [...node.classList];

// The two counts inside a row's reason: each its own element, its sign and number its whole text, in
// its colour, together one element whose text is the counts; the reason keeps the tree's 0.7rem.
function expectCounts(reason: HTMLElement) {
  expect(classes(reason)).toContain('fs_0.7rem');
  const added = within(reason).getByText(/^\+\d+$/);
  const removed = within(reason).getByText(/^−\d+$/);
  expect(classes(added)).toContain('c_mint');
  expect(classes(removed)).toContain('c_alarm');
  expect(added.parentElement).toBe(removed.parentElement);
  expect(added.parentElement?.textContent).toMatch(new RegExp(`^${COUNTS.source}$`));
}

it("draws the tree's location and lane-file counts in mint and alarm red on the page", async () => {
  stubInViewAtFirstPaint();
  servePage();
  serveMeasuredTrees();
  renderPage();
  await waitFor(() => expect(screen.getAllByTestId('review-tree-location').length).toBeGreaterThan(0));

  // A location row: "enforce · +95 −20", the role kept as text.
  const row = screen.getAllByTestId('review-tree-location').find((node) => COUNTS.test(node.textContent ?? ''))!;
  const reason = row.lastElementChild as HTMLElement;
  expect(reason.textContent).toMatch(new RegExp(`^\\w+ · ${COUNTS.source}$`));
  expectCounts(reason);
  expect(row.getAttribute('aria-label')).toMatch(COUNTS);

  // The lane file's tree row, once the reading column has read its diff: the lane is drawn in the
  // column when it is selected (MIK-R211 v4 rule 10).
  fireEvent.click(screen.getByTestId('review-tree-lane'));
  await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
  engage();
  const laneRow = within(screen.getByTestId('review-tree-lane-files')).getAllByRole('button')[0];
  await waitFor(() => expect(laneRow.textContent).toContain('+45 −0'));
  expectCounts(laneRow.lastElementChild as HTMLElement);
  expect(laneRow.getAttribute('aria-label')).toContain('+45 −0');
});
