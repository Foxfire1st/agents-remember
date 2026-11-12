// Ruling 719ade09 (D401, the page compared with the mockup by eye): a lane file's head draws its
// "+N −N" line counts as the mockup's head does (app.js:314, styles.css:94-95 and 207): the added count
// in mint and the removed count in alarm red, each with its sign in its text so colour is never the only
// signal, at the head's 0.72rem. Until the file's hunks are read the head keeps its muted hunk count.
// On L181 #8 (reviewPage.test-utils.tsx) the lane holds one added file of 45 lines.
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { engage, renderPage, servePage, stubInViewAtFirstPaint, waitForOpening } from './reviewPage.test-utils';

beforeEach(() => window.localStorage.clear());

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const classes = (node: Element) => [...node.classList];

it("draws a lane file head's line counts in mint and alarm red at the head's size", async () => {
  stubInViewAtFirstPaint();
  servePage();
  renderPage();
  // The lane is drawn in the column when it is selected (MIK-R211 v4 rule 10).
  await waitForOpening();
  fireEvent.click(screen.getByTestId('review-tree-lane'));
  await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
  engage();
  await waitFor(() => expect(screen.getByTestId('review-lane-file').dataset.laneRead).toBe('ready'));

  const counts = within(screen.getByTestId('review-lane-file')).getByTestId('review-lane-file-lines');
  expect(counts.textContent).toBe('+45 −0');
  expect(classes(counts)).toContain('fs_0.72rem');
  const added = within(counts).getByText('+45');
  const removed = within(counts).getByText('−0');
  expect(classes(added)).toContain('c_mint');
  expect(classes(removed)).toContain('c_alarm');
  expect(added.parentElement).toBe(counts);
  expect(removed.parentElement).toBe(counts);
});
