// The top line's Review | Explanation switch (MIK-R106 v2 rule 6).
//
// The label is the top line's own report: plain while there is nothing to say, "◌" while the
// environment is being built, and "<n> stale" while the trusted list holds stale parts. Switching
// starts nothing -- the component has no calls at all.
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ExplanationSwitch } from './ExplanationSwitch';

const text = (element: Element | null): string => element?.textContent ?? '';

afterEach(() => vi.unstubAllGlobals());

describe('ExplanationSwitch', () => {
  it('labels the two views and reports which one is shown', () => {
    render(<ExplanationSwitch view="review" onSwitch={vi.fn()} />);
    expect(screen.getByTestId('switch-review').getAttribute('aria-selected')).toBe('true');
    expect(screen.getByTestId('switch-explanation').getAttribute('aria-selected')).toBe('false');
    expect(text(screen.getByTestId('switch-explanation'))).toBe('Explanation');
  });

  it('names the build, and the stale count, without starting anything', () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    const { unmount } = render(<ExplanationSwitch view="explanation" stale={3} onSwitch={vi.fn()} />);
    expect(text(screen.getByTestId('switch-explanation'))).toBe('Explanation 3 stale');
    expect(screen.getByTestId('switch-explanation').getAttribute('data-stale')).toBe('3');
    unmount();

    render(<ExplanationSwitch view="explanation" stale={3} building onSwitch={vi.fn()} />);
    // A build is the top line's fact, and it outranks the stale count of the page it will replace.
    expect(text(screen.getByTestId('switch-explanation'))).toBe('Explanation ◌');
    expect(screen.getByTestId('switch-explanation').getAttribute('data-building')).toBe('true');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('switches on the developer’s press, and says when the explainer is retired', () => {
    const onSwitch = vi.fn();
    render(<ExplanationSwitch view="review" retired onSwitch={onSwitch} />);
    fireEvent.click(screen.getByTestId('switch-explanation'));
    expect(onSwitch).toHaveBeenCalledWith('explanation');
    expect(text(screen.getByTestId('switch-explanation'))).toContain('retired');
  });
});
