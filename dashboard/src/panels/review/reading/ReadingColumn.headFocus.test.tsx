// The review controls live outside the reading scroller. Focusing them cannot scroll its content.
import { act, cleanup, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { renderPage, servePage, stubIntersection } from './reviewPage.test-utils';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("keeps focusable header controls outside the reading scroller", async () => {
  stubIntersection();
  servePage();
  renderPage();
  await waitFor(() => expect(screen.getByTestId('review-tree-toggle')).toBeTruthy());
  const column = screen.getByTestId('review-center-column');
  const toolbar = screen.getByTestId('review-top-line');
  expect(column.querySelector('[data-reading-head]')).toBeNull();
  column.scrollTop = 125;
  for (const id of ['review-tree-toggle', 'review-code-context']) {
    const control = screen.getByTestId(id);
    expect(toolbar.contains(control)).toBe(true);
    expect(column.contains(control)).toBe(false);
    const button = control.matches('button') ? control : control.querySelector<HTMLElement>('button')!;
    act(() => button.focus());
    expect(document.activeElement).toBe(button);
    expect(column.scrollTop).toBe(125);
  }
});
