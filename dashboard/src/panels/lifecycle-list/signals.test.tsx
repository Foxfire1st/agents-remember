import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { dashboardStore } from '../../data/store';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { analytics, attentionItem, projection, taskSummary } from '../../test/fixtures/wire';
import { TaskStatusClock, TaskStatusWord } from '../detail-panel/pageParts';
import { DetailPanel } from '../detail-panel/DetailPanel';
import { LifecycleList } from './LifecycleList';
import { installListCleanup, rowOf, seed, task } from './test-utils';

installListCleanup();

it.each(['planned', 'started'] as const)('ages both head dependencies on a %s page, with no hidden clock', (stage) => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-15T10:00:00Z'));
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));
  try {
    const working = task('working', { activity: 'works', eventAt: '2026-10-15T09:55:00Z' });
    const dependency = { id: working.id, title: working.title, reference: working.reference, gone: false };
    const waiting = task('waiting', { stage, activity: stage === 'started' ? 'paused' : undefined,
      summary: taskSummary({ kind: 'subTask', form: 'task', waitsForTasks: [dependency], neededByTasks: [dependency] }),
    });
    seed([working, waiting]);
    const intervals = vi.spyOn(window, 'setInterval');
    const page = render(<DetailPanel selectedId={taskEntrySelectionKey(waiting.key)} active={false} />);
    const words = () => ['doc-waits-for', 'doc-needed-by'].map((id) =>
      page.getByTestId(id).querySelector('[data-status="works"]')?.textContent);
    expect(words()).toEqual(['works', 'works']);
    expect(intervals).not.toHaveBeenCalled();
    page.rerender(<DetailPanel selectedId={taskEntrySelectionKey(waiting.key)} active />);
    const row = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    const rowWord = () => rowOf(row.getByText('working', { selector: '[data-part="title"]' }))
      .querySelector('[data-testid="task-word"]')?.textContent;
    expect(words()).toEqual(['works 5m', 'works 5m']);
    expect(words()).toEqual([rowWord(), rowWord()]);
    act(() => vi.advanceTimersByTime(60_000));
    expect(words()).toEqual(['works 6m', 'works 6m']);
    expect(words()).toEqual([rowWord(), rowWord()]);
    expect(intervals).toHaveBeenCalledTimes(1);
    row.unmount();
    page.rerender(<DetailPanel selectedId={taskEntrySelectionKey(waiting.key)} active={false} />);
    expect(vi.getTimerCount()).toBe(0);
    expect(words()).toEqual(['works', 'works']);
    page.unmount();
  } finally {
    vi.useRealTimers();
    vi.restoreAllMocks();
  }
});

it('an inactive provider stops nested aged words without supplying epoch zero', () => {
  vi.useFakeTimers();
  try {
    const entry = task('working', { activity: 'works', eventAt: '2026-10-15T09:55:00Z' });
    const intervals = vi.spyOn(window, 'setInterval');
    const view = render(<TaskStatusClock active={false}><TaskStatusClock>
      <TaskStatusWord task={entry} testid="nested-word" />
    </TaskStatusClock></TaskStatusClock>);
    expect(view.getByTestId('nested-word').textContent).toBe('works');
    act(() => vi.advanceTimersByTime(60_000));
    expect(view.getByTestId('nested-word').textContent).toBe('works');
    expect(intervals).not.toHaveBeenCalled();
    view.unmount();
  } finally {
    vi.useRealTimers();
    vi.restoreAllMocks();
  }
});

it('row and page share host-time age through deltas and a later page mount', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-15T10:00:00Z'));
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));
  try {
    const entry = task('timed', {
      activity: 'works', eventAt: '2026-10-15T09:59:40Z',
    });
    seed([entry]);
    const intervals = vi.spyOn(window, 'setInterval');
    const row = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    expect(row.getByTestId('task-word').textContent).toBe('works 20s');
    act(() => vi.advanceTimersByTime(10_000));
    const page = render(<DetailPanel selectedId={taskEntrySelectionKey(entry.key)} />);
    const equalAge = (word: string) => {
      expect(row.getByTestId('task-word').textContent).toBe(word);
      expect(page.getByTestId('doc-status').textContent).toBe(word);
    };
    equalAge('works 30s');
    act(() => dashboardStore.getState().applyDelta('task', { ...entry }));
    equalAge('works 30s');
    act(() => vi.advanceTimersByTime(10_000));
    equalAge('works 40s');
    act(() => dashboardStore.getState().applyDelta('task', {
      ...entry, activity: 'paused', eventAt: '2026-10-15T10:00:20Z',
    }));
    equalAge('paused 0s');
    act(() => vi.advanceTimersByTime(10_000));
    equalAge('paused 10s');
    expect(intervals).toHaveBeenCalledTimes(1);
    page.unmount();
    row.unmount();
  } finally {
    vi.useRealTimers();
    vi.restoreAllMocks();
  }
});

it('a hidden task page has no display clock, including a planned page beside active work', () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));
  try {
    const planned = task('planned', { stage: 'planned', activity: undefined });
    const working = task('working', { activity: 'works', eventAt: new Date().toISOString() });
    seed([planned, working]);
    const intervals = vi.spyOn(window, 'setInterval');
    const ui = render(<DetailPanel selectedId={taskEntrySelectionKey(planned.key)} active={false} />);
    act(() => vi.advanceTimersByTime(30_000));
    expect(intervals).not.toHaveBeenCalled();
    ui.rerender(<DetailPanel selectedId={taskEntrySelectionKey(planned.key)} active />);
    expect(intervals).not.toHaveBeenCalled();
    ui.rerender(<DetailPanel selectedId={taskEntrySelectionKey(working.key)} active={false} />);
    act(() => vi.advanceTimersByTime(30_000));
    expect(intervals).not.toHaveBeenCalled();
    ui.rerender(<DetailPanel selectedId={taskEntrySelectionKey(working.key)} active />);
    expect(intervals).toHaveBeenCalledTimes(1);
    ui.rerender(<DetailPanel selectedId={taskEntrySelectionKey(working.key)} active={false} />);
    expect(vi.getTimerCount()).toBe(0);
    ui.unmount();
  } finally {
    vi.useRealTimers();
    vi.restoreAllMocks();
  }
});
it('keeps the paused activity mark apart from an aborted task in the same mounted list', async () => {
  const paused = task('paused');
  const aborted = task('aborted', { stage: 'aborted', activity: undefined });
  seed([paused, aborted]);
  const ui = render(
    <LifecycleList selectedId={taskEntrySelectionKey(aborted.key)} onSelect={vi.fn()} />,
  );
  const mark = (name: string) =>
    rowOf(ui.getByText(name, { selector: '[data-part="title"]' })).querySelector(
      '[data-testid="task-state"] > span',
    )?.className;
  await waitFor(() => {
    expect(mark('paused')).toBeTruthy();
    expect(mark('paused')).not.toBe(mark('aborted'));
  });
});
it.each([
  { stage: 'started' as const, activity: 'works' as const, word: 'works' },
  { stage: 'started' as const, activity: 'paused' as const, word: 'paused' },
  { stage: 'started' as const, activity: 'unknown' as const, word: 'started' },
  { stage: 'planned' as const, activity: undefined, word: 'planned' },
  { stage: 'completed' as const, activity: undefined, word: 'completed' },
  { stage: 'aborted' as const, activity: undefined, word: 'aborted' },
])(
  'one store delta says $word on the real row and page, never as attention status',
  async (next) => {
    const entry = task('L', {
      activity: 'works',
      eventAt: new Date(Date.now() - 60_000).toISOString(),
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => {})),
    );
    dashboardStore.getState().applySnapshot(
      projection({
        tasks: [entry],
        analytics: analytics({
          attentionQueue: [
            attentionItem({
              id: 'provider',
              kind: 'provider-down',
              title: 'Provider unavailable',
              severity: 'warn',
            }),
          ],
        }),
      }),
    );
    const ui = render(
      <>
        <LifecycleList selectedId={taskEntrySelectionKey(entry.key)} onSelect={vi.fn()} />
        <DetailPanel selectedId={taskEntrySelectionKey(entry.key)} />
      </>,
    );
    expect(ui.getByTestId('task-word').textContent).toBe(ui.getByTestId('doc-status').textContent);
    expect(ui.getByTestId('task-word').textContent).toBe('works 1m');
    fireEvent.click(ui.getByTestId('attention-bell'));
    await waitFor(() => expect(ui.getByTestId('attention-queue')).toBeTruthy());
    const attention = ui.getByTestId('attention-queue').textContent;
    expect(attention).not.toMatch(/works|paused|planned|completed|aborted|host activity/);
    act(() =>
      dashboardStore.getState().applyDelta('task', {
        ...entry,
        stage: next.stage,
        activity: next.activity,
        eventAt: undefined,
      }),
    );
    expect(ui.getByTestId('task-word').textContent).toBe(next.word);
    expect(ui.getByTestId('doc-status').textContent).toBe(next.word);
    expect(ui.getByTestId('attention-queue').textContent).toBe(attention);
  },
);

it.each([30, 150])(
  'a hidden rail of %i tasks has no clock or delta rebuild; a visible rail shares one clock',
  (size) => {
    vi.useFakeTimers();
    try {
      const entries = Array.from({ length: size }, (_, index) =>
        task('L' + index, {
          eventAt: new Date().toISOString(),
        }),
      );
      seed(entries);
      const intervals = vi.spyOn(window, 'setInterval');
      const clear = vi.spyOn(window, 'clearInterval');
      const onSelect = vi.fn();
      const ui = render(<LifecycleList selectedId={null} onSelect={onSelect} active={false} />);
      expect(intervals).not.toHaveBeenCalled();
      const html = ui.container.innerHTML;
      act(() => dashboardStore.getState().applyDelta('task', { ...entries[0], activity: 'works' }));
      expect(ui.container.innerHTML).toBe(html);
      act(() => vi.advanceTimersByTime(20_000));
      expect(ui.container.innerHTML).toBe(html);
      ui.rerender(<LifecycleList selectedId={null} onSelect={onSelect} active />);
      expect(ui.getAllByTestId('task-word')[0].textContent).toContain('works');
      expect(intervals).toHaveBeenCalledTimes(1);
      ui.rerender(<LifecycleList selectedId={null} onSelect={onSelect} active={false} />);
      expect(clear).toHaveBeenCalledTimes(1);
      ui.unmount();
    } finally {
      vi.useRealTimers();
      vi.restoreAllMocks();
    }
  },
);
