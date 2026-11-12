import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import { CockpitShell } from './Cockpit';

const originalMedia = window.matchMedia;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.matchMedia = originalMedia;
  window.localStorage.clear();
  dashboardStore.getState().reset();
});

it('stacks Operations at 40rem without gutters and restores the stored rail widths above it', () => {
  const listeners = new Set<() => void>();
  const media = { matches: true, addEventListener: (_: string, fn: () => void) => listeners.add(fn), removeEventListener: (_: string, fn: () => void) => listeners.delete(fn) };
  window.matchMedia = ((query: string) => query === '(max-width: 40rem)' ? media : originalMedia(query)) as typeof window.matchMedia;
  window.localStorage.setItem('cockpit.rail-left-w', '430');
  window.localStorage.setItem('cockpit.rail-right-w', '250');
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ repos: [] }) })));
  const view = render(<CockpitShell initialView="operations" />);
  const body = view.container.querySelector<HTMLElement>('.shell__body')!;
  expect(body.style.gridTemplateColumns).toBe('minmax(0, 1fr)');
  expect([...body.children].map((node) => node.tagName)).toEqual(['ASIDE', 'MAIN', 'ASIDE']);
  expect(view.queryByTestId('rail-resize-left')).toBeNull();
  expect(view.queryByTestId('rail-resize-right')).toBeNull();
  expect(view.container.querySelector('.cockpit--shell')?.className).toContain('[@media_(max-width:_40rem)]:ov-y_auto');
  expect(view.container.querySelector('.cockpit--shell')?.className).toContain('[&_[data-testid=open-intent-review]]:flex-wrap_wrap');
  expect(view.getByRole('radiogroup', { name: 'Views' }).className).toContain('[@media_(max-width:_40rem)]:flex-wrap_wrap');
  act(() => { media.matches = false; listeners.forEach((listener) => listener()); });
  expect(body.style.gridTemplateColumns).toBe('430px minmax(380px, 1fr) 250px');
  expect(view.getByTestId('rail-resize-left')).toBeTruthy();
  expect(view.getByTestId('rail-resize-right')).toBeTruthy();
  expect(window.localStorage.getItem('cockpit.rail-left-w')).toBe('430');
  expect(window.localStorage.getItem('cockpit.rail-right-w')).toBe('250');
});
