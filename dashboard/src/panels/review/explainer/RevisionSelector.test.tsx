// The revision selector (MIK-R106 v2 rule 6; MIK-R103 v2 rule 8).
//
// The promises here are the ones a reader can check on screen: every retained revision with its
// binding, time and kind; the agreed one marked and pinned; Compare offered only when there are two;
// and the removal control that appears at the task total and never offers the pinned revision.
//
// Assertions are plain DOM assertions: this suite loads no jest-dom matchers.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RevisionSelector } from './RevisionSelector';
import type { ExplainerRevision } from './explainerApi';

const text = (element: Element | null): string => element?.textContent ?? '';

const binding = (number: number) => ({
  owner: 'recorded' as const,
  number,
  reference: `review-comparison:task/leaf/${number}`,
  label: `#${number}`,
});

const rows: ExplainerRevision[] = [
  {
    revision: 7,
    binding: binding(7),
    at: '2026-10-19T11:20:00Z',
    kind: 'global',
    agreed: false,
    pinned: false,
  },
  {
    revision: 8,
    binding: binding(8),
    at: '2026-10-19T12:36:00Z',
    kind: 'focused',
    focus: 'Risks I see',
    agreed: true,
    pinned: true,
  },
  {
    revision: 9,
    binding: { owner: 'planned', number: 0, reference: 'review-comparison:task/leaf/planned', label: 'planned' },
    at: '2026-10-19T13:10:00Z',
    kind: 'global',
    agreed: false,
    pinned: false,
  },
];

const props = {
  rows,
  pinned: 8,
  shown: 8,
  compared: null,
  refusal: null,
  onShow: vi.fn(),
  onCompare: vi.fn(),
  onStopCompare: vi.fn(),
  onRemove: vi.fn(),
};

describe('RevisionSelector', () => {
  it('lists each retained revision with its binding, its time and its kind', () => {
    render(<RevisionSelector {...props} />);
    const listed = screen.getAllByTestId('revision-row');
    expect(listed).toHaveLength(3);
    expect(listed[0].getAttribute('data-binding')).toBe('#7');
    expect(listed[1].getAttribute('data-binding')).toBe('#8');
    // A planned revision reads "planned", never "#0" (MIK-R103 v2 item 3).
    expect(listed[2].getAttribute('data-binding')).toBe('planned');
    expect(text(within(listed[1]).getByTestId('revision-kind'))).toContain('focused · Risks I see');
    expect(
      within(listed[0]).getByTestId('revision-time').querySelector('time')?.getAttribute('dateTime'),
    ).toBe('2026-10-19T11:20:00Z');
  });

  it('marks the agreed revision, and marks a revision the task document pins', () => {
    const { unmount } = render(<RevisionSelector {...props} />);
    const agreedRow = screen.getAllByTestId('revision-row')[1];
    expect(agreedRow.getAttribute('data-agreed')).toBe('true');
    expect(agreedRow.getAttribute('data-pinned')).toBe('true');
    expect(text(within(agreedRow).getByTestId('revision-agreed'))).toContain('agreed');
    expect(text(screen.getByTestId('agreed-line'))).toContain('the agreed revision is #8');
    unmount();

    // The pin alone, without the flag, is still the pinned revision.
    render(
      <RevisionSelector
        {...props}
        pinned={7}
        rows={rows.map((row) => ({ ...row, agreed: false, pinned: false }))}
      />,
    );
    const pinnedRow = screen.getAllByTestId('revision-row')[0];
    expect(pinnedRow.getAttribute('data-pinned')).toBe('true');
    expect(text(within(pinnedRow).getByTestId('revision-pinned'))).toContain('pinned');
  });

  it('offers Compare only when two revisions are retained, and reports the press', () => {
    const onCompare = vi.fn();
    const { unmount } = render(<RevisionSelector {...props} onCompare={onCompare} />);
    fireEvent.click(screen.getByTestId('compare'));
    expect(onCompare).toHaveBeenCalledTimes(1);
    unmount();

    render(<RevisionSelector {...props} rows={[rows[1]]} onCompare={onCompare} />);
    expect((screen.getByTestId('compare') as HTMLButtonElement).disabled).toBe(true);
  });

  it('says nothing about removal when no revision was refused', () => {
    render(<RevisionSelector {...props} />);
    expect(screen.queryByTestId('revision-refusal')).toBeNull();
    expect(screen.queryByTestId('remove-old-revisions')).toBeNull();
  });

  it('at the task total, names the refusal and offers only unpinned revisions', () => {
    const onRemove = vi.fn();
    render(
      <RevisionSelector
        {...props}
        refusal={{ reason: 'frame-store-full', detail: 'the task total (512 MiB) is full' }}
        onRemove={onRemove}
      />,
    );
    expect(text(screen.getByTestId('revision-refusal-name'))).toBe(
      'A new revision was refused: frame-store-full: the task total (512 MiB) is full',
    );
    fireEvent.click(screen.getByTestId('remove-old-revisions'));

    const choices = screen.getAllByTestId('remove-choice');
    expect(choices.map((choice) => choice.getAttribute('data-revision'))).toEqual(['7', '9']);
    // The agreed revision is pinned and is never offered.
    expect(screen.queryByText(/remove-8/)).toBeNull();
    expect(choices.some((choice) => choice.querySelector('input#remove-8') !== null)).toBe(false);

    fireEvent.click(within(choices[0]).getByRole('checkbox'));
    fireEvent.click(screen.getByTestId('remove-selected'));
    expect(onRemove).toHaveBeenCalledWith([7]);
  });

  it('a per-revision refusal names it and offers no control: removing others would not help', () => {
    render(
      <RevisionSelector
        {...props}
        refusal={{ reason: 'revision-too-large', detail: 'revision 9 is over the 16 MiB ceiling' }}
      />,
    );
    expect(text(screen.getByTestId('revision-refusal-name'))).toContain('revision-too-large');
    expect(screen.queryByTestId('remove-old-revisions')).toBeNull();
    expect(text(screen.getByTestId('no-removal-line'))).toContain('removing others would not help');
  });
});
