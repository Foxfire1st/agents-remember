// A task the server says is started, working on nothing, with a seat whose declared wait has its
// check still to come, is shown as waiting — what for, since when, and the check due — on the
// operations rows, the task page and the header count, never as paused. The wait is served; the
// dashboard only shows it.
import { fireEvent, render } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { CockpitShell } from '../../cockpit/Cockpit';
import { dashboardStore } from '../../data/store';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { projection } from '../../test/fixtures/wire';
import type { TaskEntry, WaitingSeat } from '../../types/projection';
import { DetailPanel } from '../detail-panel/DetailPanel';
import { formatLimitTime } from '../detail-panel/limitTime';
import { taskStatusKey } from '../detail-panel/pageParts';
import { waitLines } from '../detail-panel/waitWords';
import { LifecycleList } from './LifecycleList';
import { rowHover } from './rowState';
import { installListCleanup, task } from './test-utils';

installListCleanup();

const SEAT: WaitingSeat = {
  agentId: '0f0c1d2e-0000-4000-8000-000000000001',
  role: 'worker',
  waitsFor: ['background'],
  since: '2026-11-11T10:00:00+01:00',
  checkDueAt: '2026-11-11T10:03:00+01:00',
  detail: 'pytest lanes, pid 4242, log /var/tmp/run/lanes.log, ends about 10:20',
};

function waiting(id: string, over: Partial<TaskEntry> = {}): TaskEntry {
  return task(id, { activity: 'waiting', eventAt: SEAT.since, waitingSeats: [SEAT], ...over });
}

function clockAt(iso: string) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(iso));
}

it('names what the seat waits for and since when on the row and the page, and the check in the hover', () => {
  clockAt('2026-11-11T10:02:00+01:00');
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>(() => {})),
  );
  try {
    const entry = waiting('W');
    dashboardStore.getState().applySnapshot(projection({ tasks: [entry] }));
    const row = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    const page = render(<DetailPanel selectedId={taskEntrySelectionKey(entry.key)} />);
    const word = row.getByTestId('task-word');
    expect(word.textContent).toBe('waiting for background 2m');
    expect(word.getAttribute('data-status')).toBe('waiting');
    expect(page.getByTestId('doc-status').textContent).toBe('waiting for background 2m');
    const line =
      `worker · ${SEAT.agentId} · waits for background since ${formatLimitTime(SEAT.since)} · ` +
      `check due ${formatLimitTime(SEAT.checkDueAt!)} · ${SEAT.detail}`;
    expect(word.getAttribute('title')).toBe(line);
    expect(waitLines(entry.waitingSeats)).toEqual([line]);
    expect(rowHover(entry, Date.parse('2026-11-11T10:02:00+01:00'))).toContain(`State: waiting\n${line}`);
    page.unmount();
    row.unmount();
  } finally {
    vi.useRealTimers();
    vi.restoreAllMocks();
  }
});

it('says both waits when a seat declared both', () => {
  const entry = waiting('B', { waitingSeats: [{ ...SEAT, waitsFor: ['subagent', 'background'] }] });
  dashboardStore.getState().applySnapshot(projection({ tasks: [entry] }));
  const row = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  expect(row.getByTestId('task-word').textContent).toMatch(/^waiting for subagents and background/);
  expect(taskStatusKey(entry)).toBe('waiting');
});

it('groups the task under waiting, not paused, and the header counts it as waiting', () => {
  const entry = waiting('W');
  const counts = { planned: 0, started: 1, completed: 0, aborted: 0, works: 0, waiting: 1, paused: 0, unknown: 0 };
  dashboardStore.getState().applySnapshot(projection({ tasks: [entry], taskCounts: counts }));
  const list = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  fireEvent.click(list.getByText('BY PHASE'));
  expect([...list.container.querySelectorAll('header')].map((node) => node.textContent)).toEqual(['waiting1']);
  const cockpit = render(<CockpitShell initialView="operations" />);
  const header = cockpit.getByTestId('task-counts').textContent ?? '';
  expect(header).toContain('1 waiting');
  expect(header).toContain('0 paused');
});

it('keeps the header line as it was while no task waits', () => {
  const entry = task('P');
  const counts = { planned: 0, started: 1, completed: 0, aborted: 0, works: 0, waiting: 0, paused: 1, unknown: 0 };
  dashboardStore.getState().applySnapshot(projection({ tasks: [entry], taskCounts: counts }));
  const cockpit = render(<CockpitShell initialView="operations" />);
  expect(cockpit.getByTestId('task-counts').textContent ?? '').not.toContain('waiting');
});
