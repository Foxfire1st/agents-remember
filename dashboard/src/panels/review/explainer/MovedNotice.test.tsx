// The moved notice and the Update offer (MIK-R186 rule 7; MIK-R187 rule 9).
//
// The promises here are the ones a reader can check on screen: the watcher's published line, the
// master tip pair for a planning leaf, the Update offer it carries, and the moved-no-explainer state
// where no control is offered. Assertions are plain DOM assertions: this suite loads no jest-dom
// matchers.
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { MovedNotice, type WatcherNotice } from './MovedNotice';

const text = (element: Element | null): string => element?.textContent ?? '';

const watcherNotice = (overrides: Partial<WatcherNotice> = {}): WatcherNotice => ({
  kind: 'comparison-moved',
  from: 7,
  to: 9,
  blocksChanged: 4,
  updateOffered: true,
  text: 'task moved: #7 -> #9, 4 blocks changed',
  ...overrides,
});

describe('the moved notice', () => {
  it('shows the line the watcher published, with its block count', () => {
    render(<MovedNotice moved={watcherNotice()} hasWork update={null} />);

    expect(screen.getByTestId('moved-notice').getAttribute('data-moved')).toBe('moved');
    expect(text(screen.getByTestId('moved-line'))).toBe('task moved: #7 -> #9, 4 blocks changed');
    expect(text(screen.getByTestId('moved-note'))).toContain('the explainer was not woken');
  });

  it('offers Update and presses only the route it was given', () => {
    const onUpdate = vi.fn();
    render(<MovedNotice moved={watcherNotice()} hasWork update={null} onUpdate={onUpdate} />);

    fireEvent.click(screen.getByTestId('update'));

    expect(onUpdate).toHaveBeenCalledTimes(1);
  });

  it('renders the page model it has always rendered', () => {
    render(<MovedNotice moved={{ from: 8, to: 9, blocks: 4 }} hasWork update={null} />);

    expect(text(screen.getByTestId('moved-line'))).toBe('Task moved: #8 → #9 · 4 blocks changed');
    expect(screen.getByTestId('update')).toBeTruthy();
  });

  it('shows a planning leaf master move with the Update offer even before a page exists', () => {
    render(
      <MovedNotice
        moved={watcherNotice({
          kind: 'master-moved',
          from: null,
          to: null,
          blocksChanged: 0,
          commitFrom: 'a'.repeat(40),
          commitTo: 'b'.repeat(40),
          text: `master moved: ${'a'.repeat(40)} -> ${'b'.repeat(40)}`,
        })}
        hasWork={false}
        update={null}
        onUpdate={vi.fn()}
      />,
    );

    expect(text(screen.getByTestId('moved-line'))).toBe(
      `master moved: ${'a'.repeat(40)} -> ${'b'.repeat(40)}`,
    );
    expect(screen.getByTestId('update')).toBeTruthy();
  });

  it('says the view renewed itself, and offers no Update, when no explainer has worked', () => {
    render(
      <MovedNotice
        moved={watcherNotice({ updateOffered: false })}
        hasWork={false}
        update={null}
        onUpdate={vi.fn()}
      />,
    );

    expect(screen.getByTestId('moved-notice').getAttribute('data-moved')).toBe('moved-no-explainer');
    expect(text(screen.getByTestId('moved-no-explainer-note'))).toContain('the view renewed itself');
    expect(screen.queryByTestId('update')).toBeNull();
  });

  it('names an Update in flight and never doubles the press', () => {
    const onUpdate = vi.fn();
    render(
      <MovedNotice
        moved={watcherNotice()}
        hasWork
        update={{ state: 'updating' }}
        onUpdate={onUpdate}
      />,
    );

    const button = screen.getByTestId('update') as HTMLButtonElement;
    expect(text(button)).toBe('Updating…');
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(onUpdate).not.toHaveBeenCalled();
  });
});
