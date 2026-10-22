// MIK-R190 rule 13: the sidebar row is one display instant everywhere. When the served window's
// end is reached, the word, its mark and every hover cell of the row say paused together — with no
// new server snapshot, no recomputed counts and no server-side timer.
import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { dashboardStore } from '../../data/store';
import { formatLimitTime } from '../detail-panel/pageParts';
import type { ProviderLimit, TaskEntry } from '../../types/projection';
import { LifecycleList } from './LifecycleList';
import { installListCleanup, rowOf, seed, task } from './test-utils';

installListCleanup();

const UNTIL = '2026-10-21T12:00:00+02:00';

const WINDOW: ProviderLimit = {
  until: UNTIL,
  seats: [
    {
      role: 'worker',
      agentId: '0f0c1d2e-0000-4000-8000-000000000001',
      source: 'Claude',
      account: 'Max 20x',
      window: 'Session',
      resetsAt: UNTIL,
    },
  ],
};

function limited(id: string): TaskEntry {
  return task(id, { activity: 'paused', providerLimit: WINDOW });
}

afterEach(() => vi.useRealTimers());

it('expires the word, the mark and every hover cell of the row on one instant', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-21T10:00:00+02:00'));
  const entry = limited('L');
  seed([entry]);
  const list = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  const row = () => rowOf(list.getByText('L', { selector: '[data-part="title"]' }));
  const word = () => list.getByTestId('task-word');
  const mark = () => row().querySelector('[data-testid="task-state"]');
  // Every cell that carries the row's hover carries the same one (the word itself keeps its own
  // hover, one line per served seat).
  const hovers = () =>
    [...row().querySelectorAll('[title]')]
      .filter((node) => node !== word())
      .map((node) => node.getAttribute('title') ?? '');

  expect(word().textContent).toBe('rate limited: 2h');
  expect(mark()?.getAttribute('aria-label')).toBe(
    `Task progress: rate limited, until ${formatLimitTime(UNTIL)}`,
  );
  // Every cell that carries the row's hover carries the same one.
  expect(hovers().length).toBeGreaterThan(1);
  for (const hover of hovers()) {
    expect(hover).toContain('State: rate limited');
    expect(hover).toContain(`resets ${formatLimitTime(UNTIL)}`);
  }
  // The row line itself still carries no title, so a step dot keeps its own hover (MIK-R175).
  expect(
    row().querySelector('[data-part="title"]')?.parentElement?.getAttribute('title'),
  ).toBeNull();

  // No snapshot arrives; only the shared display clock moves past the served end.
  act(() => vi.advanceTimersByTime(7_200_000));

  expect(word().textContent).toBe('paused');
  expect(mark()?.getAttribute('aria-label')).toBe('Task progress: paused');
  for (const hover of hovers()) {
    expect(hover).toContain('State: paused');
    expect(hover).not.toContain('rate limited');
    expect(hover).not.toContain('resets ');
  }
  // Nothing was re-served or recomputed: the entry still holds the fact the server sent.
  expect(dashboardStore.getState().tasks[entry.key].providerLimit).toEqual(WINDOW);
  expect(dashboardStore.getState().tasks[entry.key].activity).toBe('paused');
  list.unmount();
});
