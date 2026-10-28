// The top line's Review | Explanation switch, and the staleness badge beside it (MIK-R106 v2 rule 6;
// F18).
//
// The label is the top line's own report: plain while there is nothing to say, "◌" while the
// environment is being built, and "retired" once the explainer is retired. The badge is the mark
// itself -- "<n> stale" -- and opening it says which parts are stale, why, whether a newer
// comparison exists, and offers the one press that regenerates them. Switching starts nothing: the
// component has no calls of its own.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ExplanationSwitch } from './ExplanationSwitch';
import type { StaleBrief } from './explainerModel';

const text = (element: Element | null): string => element?.textContent ?? '';

afterEach(() => vi.unstubAllGlobals());

function brief(over: Partial<StaleBrief> = {}): StaleBrief {
  return {
    parts: [
      { id: 'look', title: 'Where to look first', reason: 'changed in #9' },
      { id: 'risks', title: 'Risks I see', reason: 'the grades of 4 changed blocks' },
    ],
    total: 3,
    unavailable: null,
    gradeCount: 0,
    newerComparison: 'Task moved: #8 → #9 · 4 blocks changed',
    regeneration: {
      label: 'Regenerate stale (3 parts · 0 grades)',
      message: 'Regenerate revision 8: 3 parts are stale.',
      partCount: 3,
      gradeCount: 0,
    },
    present: true,
    ...over,
  };
}

describe('ExplanationSwitch', () => {
  it('labels the two views and reports which one is shown', () => {
    render(<ExplanationSwitch view="review" onSwitch={vi.fn()} />);
    expect(screen.getByTestId('switch-review').getAttribute('aria-selected')).toBe('true');
    expect(screen.getByTestId('switch-explanation').getAttribute('aria-selected')).toBe('false');
    expect(text(screen.getByTestId('switch-explanation'))).toBe('Explanation');
    // Nothing is stale: no mark, and nothing to open.
    expect(screen.queryByTestId('explanation-stale-badge')).toBeNull();
  });

  it('names the build, and keeps the stale count as the badge beside the tab, without starting anything', () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    const { unmount } = render(
      <ExplanationSwitch view="explanation" stale={3} brief={brief()} onSwitch={vi.fn()} />,
    );
    // The mark stays visible in the top line, exactly as before: it is the badge's own summary.
    expect(text(screen.getByTestId('explanation-stale-mark'))).toBe('3 stale');
    expect(text(screen.getByTestId('review-explanation-switch'))).toContain('Explanation');
    expect(text(screen.getByTestId('review-explanation-switch'))).toContain('3 stale');
    expect(screen.getByTestId('switch-explanation').getAttribute('data-stale')).toBe('3');
    unmount();

    render(<ExplanationSwitch view="explanation" stale={3} brief={brief()} building onSwitch={vi.fn()} />);
    // A build is the top line's fact, and it outranks the stale count of the page it will replace.
    expect(text(screen.getByTestId('switch-explanation'))).toBe('Explanation ◌');
    expect(screen.getByTestId('switch-explanation').getAttribute('data-building')).toBe('true');
    expect(screen.queryByTestId('explanation-stale-badge')).toBeNull();
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

describe('the stale badge (F18: staleness lives in the top line)', () => {
  it('opens on its own press -- not the tab’s -- and says which parts are stale, why, and what is newer', () => {
    const onSwitch = vi.fn();
    render(<ExplanationSwitch view="explanation" stale={3} brief={brief()} onSwitch={onSwitch} />);
    const badge = screen.getByTestId('explanation-stale-badge') as HTMLDetailsElement;
    expect(badge.open).toBe(false);

    fireEvent.click(screen.getByTestId('explanation-stale-mark'));
    expect(badge.open).toBe(true);
    // Opening it is not switching views: the tab is the switch, the badge is the record.
    expect(onSwitch).not.toHaveBeenCalled();

    expect(badge.getAttribute('data-count')).toBe('3');
    expect(text(screen.getByTestId('stale-summary-line'))).toBe(
      '3 parts of this explanation are stale against the comparison on screen.',
    );
    const rows = screen.getAllByTestId('stale-part');
    expect(rows).toHaveLength(2);
    expect(text(rows[0])).toContain('Where to look first');
    expect(text(rows[0])).toContain('changed in #9');
    expect(text(rows[1])).toContain('Risks I see');
    expect(text(rows[1])).toContain('the grades of 4 changed blocks');
    // The host listed two of the three: the true count is said, and the rest is not pretended.
    expect(text(screen.getByTestId('stale-more'))).toContain('+ 1 more stale part');
    expect(text(screen.getByTestId('newer-comparison'))).toBe(
      'A newer comparison exists: Task moved: #8 → #9 · 4 blocks changed',
    );
  });

  it('offers the host’s own label as the refresh control, presses it once per press, and says a press is in flight', () => {
    const onRegenerate = vi.fn();
    const { rerender } = render(
      <ExplanationSwitch
        view="explanation"
        stale={3}
        brief={brief()}
        onRegenerate={onRegenerate}
        onSwitch={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('explanation-stale-mark'));
    const control = screen.getByTestId('regenerate-stale') as HTMLButtonElement;
    expect(text(control)).toBe('Regenerate stale (3 parts · 0 grades)');
    expect(control.disabled).toBe(false);
    fireEvent.click(control);
    expect(onRegenerate).toHaveBeenCalledTimes(1);

    rerender(
      <ExplanationSwitch
        view="explanation"
        stale={3}
        brief={brief()}
        regenerating
        onRegenerate={onRegenerate}
        onSwitch={vi.fn()}
      />,
    );
    const waiting = screen.getByTestId('regenerate-stale') as HTMLButtonElement;
    expect(text(waiting)).toBe('Regenerating…');
    expect(waiting.disabled).toBe(true);
  });

  it('shows the refusal in the host’s own words, and claims nothing when staleness cannot be read', () => {
    const { unmount } = render(
      <ExplanationSwitch
        view="explanation"
        stale={3}
        brief={brief()}
        regenerationProblem="host-busy: explainer is busy"
        onRegenerate={vi.fn()}
        onSwitch={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('explanation-stale-mark'));
    const refusal = screen.getByTestId('regenerate-stale-problem');
    expect(refusal.getAttribute('role')).toBe('alert');
    expect(text(refusal)).toBe('host-busy: explainer is busy');
    unmount();

    // The host could not compute staleness: the mark is unresolved, and the badge says why.
    render(
      <ExplanationSwitch
        view="explanation"
        stale={0}
        brief={brief({
          parts: [],
          total: 0,
          newerComparison: null,
          regeneration: null,
          unavailable: 'the comparison has no tree',
        })}
        onSwitch={vi.fn()}
      />,
    );
    expect(text(screen.getByTestId('explanation-stale-mark'))).toBe('stale?');
    fireEvent.click(screen.getByTestId('explanation-stale-mark'));
    const details = screen.getByTestId('explanation-stale-details');
    expect(text(details)).toContain('Staleness cannot be read: the comparison has no tree');
    expect(text(details)).toContain('No part is claimed current');
    expect(within(details).queryByTestId('regenerate-stale')).toBeNull();
  });
});
