// D401 (the page compared with the mockup by eye; mockup `groups`, app.js `laneSection`, styles.css
// `.shead .sid`, `.badge.muted`, `.attach`, `.smeta`, `.sublabel`): the lane's heading boxes "lane"
// and its file count, and its "Full page" links are the mockup's quiet bordered controls; its files
// carry the "Changed files" label; a file's head boxes its status. A hunk's row stays the selectable
// unit MIK-R89 v2 rule 13 names (⊕, URL selection, j and k), drawn as quiet text, not as a box.
// On L181 #8 (reviewPage.test-utils.tsx) the lane holds one added file of 45 lines, one hunk.
import { cleanup, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { badge, subjectId, sublabel } from './readingStyles';
import { engage, renderPage, servePage, stubInViewAtFirstPaint } from './reviewPage.test-utils';

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

it("draws the lane's heading, its files' label and its file heads with the mockup's badges and quiet controls", async () => {
  stubInViewAtFirstPaint();
  servePage();
  renderPage();
  await waitFor(() => expect(screen.getByTestId('review-lane-section').dataset.laneState).toBe('ready'));
  engage();
  await waitFor(() => expect(screen.getByTestId('review-lane-file').dataset.laneRead).toBe('ready'));
  const section = screen.getByTestId('review-lane-section');

  // The heading: "lane" and the count boxed, the link a bordered quiet control with its own words.
  const head = section.querySelector('h3')!.parentElement!;
  expect(head.firstElementChild!.textContent).toBe('lane');
  expect(head.firstElementChild!.className).toBe(subjectId);
  expect(within(section).getByTestId('review-lane-count').className).toBe(badge);
  const links = within(section).getAllByTestId('review-lane-full-page');
  expect(links.map((link) => link.textContent)).toEqual(['Full page · Unexplained ↗']);
  for (const link of links) {
    expect(reachedBySharedLook(link)).toBe(false);
    expect(classes(link)).toEqual(expect.arrayContaining(['bg_transparent', 'fs_0.72rem', 'c_muted']));
  }
  expect(classes(within(section).getByTestId('review-lane-meta'))).toContain('fs_0.72rem');

  // The files' label, as the mockup's, before the first file.
  const label = within(section).getByTestId('review-lane-files-label');
  expect(label.className).toBe(sublabel);
  expect(label.textContent).toBe('Changed files no recorded intent claims these hunks');
  expect(label.nextElementSibling).toBe(screen.getByTestId('review-lane-file'));

  // A file's head: its status boxed; its hunk rows quiet text, still buttons.
  const file = screen.getByTestId('review-lane-file');
  expect(within(file).getByText('added').className).toBe(badge);
  const hunks = within(file).getAllByTestId('review-lane-hunk');
  expect(hunks.length).toBeGreaterThan(0);
  for (const hunk of hunks) {
    const button = within(hunk).getByRole('button', { name: /^L\d/ });
    expect(reachedBySharedLook(button)).toBe(false);
    expect(classes(button)).toEqual(expect.arrayContaining(['bg_transparent', 'bd_none']));
  }
});
