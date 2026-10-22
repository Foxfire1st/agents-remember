// MIK-R190 rule 13: a task the server says is started, working on nothing, with a seat held by
// its provider limit, says "rate limited: <time left>" in place of "paused" — and counts, groups
// and the header keep saying paused underneath. The limit is served; the dashboard only shows it.
import { act, fireEvent, render } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { CockpitShell } from '../../cockpit/Cockpit';
import { dashboardStore } from '../../data/store';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { projection } from '../../test/fixtures/wire';
import type { ProviderLimit, TaskEntry } from '../../types/projection';
import { DetailPanel } from '../detail-panel/DetailPanel';
import {
  StatusWord,
  TaskStatusClock,
  TaskStatusWord,
  formatLimitTime,
  statusTitle,
  taskStatusKey,
  underlyingStatusKey,
} from '../detail-panel/pageParts';
import { LifecycleList } from './LifecycleList';
import { installListCleanup, rowOf, seed, task } from './test-utils';

installListCleanup();

const WINDOW: ProviderLimit = {
  until: '2026-10-21T12:00:00+02:00',
  seats: [
    {
      role: 'worker',
      agentId: '0f0c1d2e-0000-4000-8000-000000000001',
      source: 'Claude',
      account: 'Max 20x',
      window: 'Session',
      resetsAt: '2026-10-21T12:00:00+02:00',
    },
    {
      role: 'reviewer',
      agentId: '0f0c1d2e-0000-4000-8000-000000000002',
      source: 'Codex',
      account: 'Pro',
      window: 'Weekly',
      resetsAt: '2026-10-24T09:30:00+02:00',
    },
  ],
};

function limited(id: string, over: Partial<TaskEntry> = {}): TaskEntry {
  return task(id, { activity: 'paused', providerLimit: WINDOW, ...over });
}

/** One line per served seat: role, agent, provider as `source · account`, window, exact reset. */
function seatDetail(): string[] {
  return WINDOW.seats.map((seat) =>
    [
      seat.role,
      seat.agentId,
      `${seat.source} · ${seat.account}`,
      seat.window,
      `resets ${formatLimitTime(seat.resetsAt)}`,
    ].join(' · '),
  );
}

function clockAt(iso: string) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(iso));
}

it('counts the served window down on the row and the page, then hands the task back to paused', () => {
  clockAt('2026-10-21T10:00:00+02:00');
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>(() => {})),
  );
  try {
    const entry = limited('L', { eventAt: '2026-10-21T11:00:00+02:00' });
    seed([entry]);
    const row = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    const page = render(<DetailPanel selectedId={taskEntrySelectionKey(entry.key)} />);
    const word = () => row.getByTestId('task-word');
    expect(word().textContent).toBe('rate limited: 2h');
    expect(page.getByTestId('doc-status').textContent).toBe('rate limited: 2h');
    expect(word().getAttribute('data-status')).toBe('limited');
    // The hover of the word carries every seat, one per line: role, agent, provider, window,
    // and the exact reset with its date and time zone.
    expect(word().getAttribute('title')).toBe(seatDetail().join('\n'));
    // The row's mark speaks the same word, with the exact served end beside it.
    expect(
      rowOf(row.getByText('L', { selector: '[data-part="title"]' }))
        .querySelector('[data-testid="task-state"]')
        ?.getAttribute('aria-label'),
    ).toBe(`Task progress: rate limited, until ${formatLimitTime(WINDOW.until)}`);
    act(() => vi.advanceTimersByTime(3_600_000));
    expect(word().textContent).toBe('rate limited: 1h');
    // The display clock has reached the served end: the task is paused again, with its age and no
    // countdown.
    act(() => vi.advanceTimersByTime(3_600_000));
    expect(word().textContent).toBe('paused 1h');
    expect(word().getAttribute('data-status')).toBe('paused');
    expect(page.getByTestId('doc-status').textContent).toBe('paused 1h');
    page.unmount();
    row.unmount();
  } finally {
    vi.useRealTimers();
    vi.restoreAllMocks();
  }
});

it('keeps the grouping, the counts and the header saying paused underneath the word', () => {
  clockAt('2026-10-21T10:00:00+02:00');
  try {
    const entry = limited('L');
    const counts = {
      planned: 0,
      started: 1,
      completed: 0,
      aborted: 0,
      works: 0,
      paused: 1,
      unknown: 0,
    };
    dashboardStore.getState().applySnapshot(projection({ tasks: [entry], taskCounts: counts }));
    const list = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    fireEvent.click(list.getByText('BY PHASE'));
    expect([...list.container.querySelectorAll('header')].map((node) => node.textContent)).toEqual([
      'paused1',
    ]);
    expect(list.getByTestId('task-word').textContent).toBe('rate limited: 2h');
    // The word is a word: the activity the server published and its counts do not move.
    expect(entry.activity).toBe('paused');
    expect(taskStatusKey(entry)).toBe('limited');
    expect(underlyingStatusKey(entry)).toBe('paused');
    expect(dashboardStore.getState().tasks[entry.key].activity).toBe('paused');
    expect(dashboardStore.getState().taskCounts).toEqual(counts);
    const cockpit = render(<CockpitShell initialView="operations" />);
    const header = cockpit.getByTestId('task-counts').textContent ?? '';
    expect(header).toContain('1 paused');
    expect(header).not.toContain('rate limited');
    cockpit.unmount();
  } finally {
    vi.useRealTimers();
    vi.restoreAllMocks();
  }
});

it('says the words and the exact end where a display clock cannot count down', () => {
  // orderGraph and the sprint graph get a status key and the served end, never a clock.
  const graph = render(<StatusWord status="limited" until={WINDOW.until} testid="graph-word" />);
  const word = graph.getByTestId('graph-word');
  expect(word.textContent).toBe('rate limited');
  expect(word.getAttribute('title')).toBe(`until ${formatLimitTime(WINDOW.until)}`);
  expect(statusTitle('limited', WINDOW.until)).toBe(
    `rate limited, until ${formatLimitTime(WINDOW.until)}`,
  );
  // A task whose word has no clock either shows the words and the served detail.
  const hidden = render(
    <TaskStatusClock active={false}>
      <TaskStatusWord task={limited('H')} testid="hidden-word" />
    </TaskStatusClock>,
  );
  expect(hidden.getByTestId('hidden-word').textContent).toBe('rate limited');
  expect(hidden.getByTestId('hidden-word').getAttribute('title')).toBe(seatDetail().join('\n'));
});

it('writes an exact served instant with its date, its seconds, its milliseconds and its time zone', () => {
  expect(formatLimitTime(WINDOW.until)).toMatch(
    /^[A-Z][a-z]{2} \d{1,2}, \d{4}, \d{2}:\d{2}:\d{2}\.\d{3} (GMT[+-]\d{1,2}|UTC)$/,
  );
  // An unreadable instant is shown as served rather than turned into a time.
  expect(formatLimitTime('soon')).toBe('soon');
  // A word that is not limited never borrows the limit's time.
  expect(statusTitle('paused', WINDOW.until)).toBe('paused');
  expect(statusTitle('limited')).toBe('rate limited');
});
