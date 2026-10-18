import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useWorkspaceState } from './ReviewWorkspace';

afterEach(() => vi.unstubAllGlobals());

it('starts narrow expression diffs inline and preserves the reader choice afterwards', () => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })));
  const { result, rerender } = renderHook(() => useWorkspaceState());
  expect(result.current.layout).toBe('inline');
  act(() => result.current.setLayout('split'));
  rerender();
  expect(result.current.layout).toBe('split');
});

it('keeps the narrow review scroll position during an in-tree choice', () => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })));
  const { result } = renderHook(() => useWorkspaceState());
  const surface = document.createElement('div');
  surface.dataset.testid = 'review-surface';
  const center = document.createElement('div');
  surface.append(center);
  surface.scrollTop = 230;
  result.current.center.current = center;
  act(() => result.current.setChosen({ familyId: 'family' }));
  expect(surface.scrollTop).toBe(230);
});

it('starts wide expression diffs side by side', () => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })));
  const { result } = renderHook(() => useWorkspaceState());
  expect(result.current.layout).toBe('split');
});
