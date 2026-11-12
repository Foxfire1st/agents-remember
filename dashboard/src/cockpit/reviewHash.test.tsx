import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { readReviewHashState, useReviewHash, writeReviewHashState } from './reviewHash';

const target = '#review?repo=agents-remember&master=master&leaf=leaf';

beforeEach(() => window.history.replaceState(null, '', `/${target}`));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('review selection history', () => {
  it('pushes a new choice once and replaces only when explicitly restoring', () => {
    const push = vi.spyOn(window.history, 'pushState');
    const replace = vi.spyOn(window.history, 'replaceState');
    writeReviewHashState({ sel: 's:invariant:first', comparison: null });
    writeReviewHashState({ sel: 's:invariant:first', comparison: null });
    expect(push).toHaveBeenCalledTimes(1);
    writeReviewHashState({ sel: 'l:', comparison: 3 });
    expect(push).toHaveBeenCalledTimes(2);
    expect(readReviewHashState()).toEqual({ sel: 'l:', comparison: 3 });
    writeReviewHashState({ sel: null, comparison: 3 }, 'replace');
    expect(replace).toHaveBeenCalledTimes(1);
    expect(window.location.hash).toContain('repo=agents-remember&master=master&leaf=leaf');
  });

  it('retains the mounted target on selection history and navigates when the target changes', async () => {
    const setView = vi.fn();
    const closeNotes = vi.fn();
    const { result } = renderHook(() => useReviewHash(setView, closeNotes));
    const mounted = result.current[0];
    act(() => {
      window.history.pushState(null, '', `${target}&sel=s%3Ainvariant%3Afirst`);
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(result.current[0]).toBe(mounted);
    act(() => {
      window.history.pushState(null, '', '#review?repo=agents-remember&master=master&leaf=other');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await waitFor(() => expect(result.current[0]?.leaf).toBe('other'));
  });
});
