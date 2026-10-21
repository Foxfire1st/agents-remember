// Showing or hiding the tree keeps the reader's line (MIK-R89 v2 rule 9; ruling 8450399b). The sticky
// head stands inside the column it heads, and Chrome scrolls a sticky element into view at its place
// in the flow, the column's top: a keyboard focus on the head's tree toggle, a script focus or a
// scroll into view moved the column to 0 (scratch harness, before-BE-e.log). The head and its parts
// take a scroll area that runs past the column's end, so such a scroll finds them in view and moves
// nothing. jsdom does not scroll on focus: this case pins the declaration; the browser measures it.
import { cleanup, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { renderPage, servePage, stubIntersection } from './reviewPage.test-utils';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("gives the sticky head and its parts a scroll area past the column's end", async () => {
  stubIntersection();
  servePage();
  renderPage();
  await waitFor(() => expect(screen.getByTestId('review-tree-toggle')).toBeTruthy());
  const head = document.querySelector<HTMLElement>('[data-reading-head]')!;
  expect(head.contains(screen.getByTestId('review-tree-toggle'))).toBe(true);
  expect([...head.classList]).toEqual(expect.arrayContaining(['scr-mb_1000000px', '[&_*]:scr-mb_1000000px']));
});
