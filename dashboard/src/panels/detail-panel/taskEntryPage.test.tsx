import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../../data/store';
import { taskReferenceKey, taskSelectionKey, taskEntrySelectionKey } from '../../data/taskReference';
import { projection, taskEntry, taskSummary } from '../../test/fixtures/wire';
import { DetailPanel } from './DetailPanel';
import { TaskChildrenBlock, TaskNowBlock } from './task-groups';
import { TaskReader } from './taskReader';
import { LifecycleList } from '../lifecycle-list/LifecycleList';

beforeEach(() => dashboardStore.getState().reset());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const masterRef = { repository: 'repo', path: 'master/task.json' };
const childRef = { repository: 'repo', path: 'master/01.json' };
const masterSummary = taskSummary({
  kind: 'master',
  form: 'master',
  objective: 'Master objective',
});

describe('one served task entry across row, page and counts', () => {
  it('keeps an addressless planning row visible and counted, without navigation or a body request', () => {
    const masterKey = taskReferenceKey(masterRef);
    const child = taskEntry({
      key: masterKey + '#L0', reference: undefined, summary: undefined, parent: masterKey,
      id: 'L0', title: 'Addressless plan', stage: 'planned',
    });
    const master = taskEntry({
      reference: masterRef, children: [child.key], summary: masterSummary,
      counts: { planned: 1, started: 0, completed: 0, aborted: 0, works: 0, paused: 0, unknown: 0 },
    });
    dashboardStore.getState().applySnapshot(projection({ tasks: [master, child] }));
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const jump = vi.fn();
    const ui = render(<>
      <LifecycleList selectedId={null} onSelect={jump} />
      <TaskChildrenBlock entry={master} doc={masterSummary} tasks={dashboardStore.getState().tasks}
        onJump={jump} openView={vi.fn()} />
      <DetailPanel selectedId={taskEntrySelectionKey(child.key)} />
    </>);
    const row = ui.getByTestId('subtask-open-1');
    expect(row.tagName).toBe('SPAN');
    fireEvent.click(row);
    const option = ui.getByRole('option', { name: /L0 Addressless plan/ });
    expect(option.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(option);
    expect(jump).not.toHaveBeenCalled();
    expect(ui.getByTestId('task-row-status-1').textContent).toBe('planned');
    expect(ui.getByTestId('doc-status').textContent).toBe('planned');
    expect(ui.getByTestId('task-not-authored').textContent).toContain('not authored yet');
    expect(dashboardStore.getState().tasks[master.key].counts.planned).toBe(1);
    expect(dashboardStore.getState().tasks[master.key].children).toEqual([child.key]);
    expect(fetch).not.toHaveBeenCalled();
  });
  it('opens a declared unauthored child with its row stage and no document request', () => {
    const child = taskEntry({
      id: 'MASTER-01',
      reference: childRef,
      parent: taskReferenceKey(masterRef),
      title: 'Declared child',
      stage: 'planned',
    });
    const counts = {
      planned: 1,
      started: 0,
      completed: 0,
      aborted: 0,
      works: 0,
      paused: 0,
      unknown: 0,
    };
    const master = taskEntry({
      reference: masterRef,
      children: [taskReferenceKey(childRef)],
      counts,
      summary: masterSummary,
    });
    dashboardStore
      .getState()
      .applySnapshot(projection({ tasks: [master, child], taskCounts: counts }));
    const tasks = dashboardStore.getState().tasks;
    const jump = vi.fn();
    const row = render(
      <TaskChildrenBlock
        entry={master}
        doc={masterSummary}
        tasks={tasks}
        onJump={jump}
        openView={vi.fn()}
      />,
    );
    expect(row.getByTestId('task-row-status-1').textContent).toBe('planned');
    fireEvent.click(row.getByTestId('subtask-open-1'));
    expect(jump).toHaveBeenCalledWith(taskSelectionKey(childRef));
    expect(dashboardStore.getState().taskCounts.planned).toBe(1);
    expect(dashboardStore.getState().tasks[master.key].counts.planned).toBe(1);
    row.unmount();
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const page = render(<DetailPanel selectedId={taskSelectionKey(childRef)} />);
    expect(page.getByTestId('task-not-authored').textContent).toContain('Declared child');
    expect(page.getByTestId('doc-status').textContent).toBe('planned');
    expect(page.getByText('This task document is not authored yet.')).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('shows the same served activity and event age in the Now row and page', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async () => new Response('{}', { status: 503 })),
    );
    const summary = taskSummary({ objective: 'Leaf objective' });
    const child = taskEntry({
      id: 'MASTER-01',
      reference: childRef,
      summary,
      stage: 'started',
      activity: 'paused',
      eventAt: new Date(Date.now() - 30_000).toISOString(),
    });
    const counts = {
      planned: 0,
      started: 1,
      completed: 0,
      aborted: 0,
      works: 0,
      paused: 1,
      unknown: 0,
    };
    const master = taskEntry({
      reference: masterRef,
      children: [taskReferenceKey(childRef)],
      counts,
      summary: masterSummary,
    });
    const tasks = { [taskReferenceKey(masterRef)]: master, [taskReferenceKey(childRef)]: child };
    const rows = render(<TaskNowBlock entry={master} tasks={tasks} onJump={vi.fn()} />);
    const rowStatus = rows.getByTestId('task-row-status-1').textContent;
    expect(rowStatus).toContain('paused');
    rows.unmount();
    const page = render(
      <TaskReader entry={child} doc={summary} bodyState="loading" tasks={tasks} onJump={vi.fn()} />,
    );
    expect(page.getByTestId('doc-status').textContent).toBe(rowStatus);
    expect(page.getByText('Leaf objective')).toBeTruthy();
  });

  it('constructs only the five preview rows before opening the 109-task completed list', () => {
    const titleReads = vi.fn((index: number) => `Completed child ${index}`);
    const children = Array.from({ length: 109 }, (_, index) => {
      const child = taskEntry({
        id: `MASTER-${index}`,
        reference: { repository: 'repo', path: `master/${index}.json` },
        stage: 'completed',
      });
      Object.defineProperty(child, 'title', { get: () => titleReads(index) });
      return child;
    });
    const counts = {
      planned: 0,
      started: 0,
      completed: 109,
      aborted: 0,
      works: 0,
      paused: 0,
      unknown: 0,
    };
    const master = taskEntry({
      reference: masterRef,
      children: children.map((child) => child.key),
      counts,
      summary: masterSummary,
    });
    const tasks = Object.fromEntries(
      children.map((child) => [child.key, child]),
    );
    const open = vi.fn();
    const view = render(
      <TaskChildrenBlock
        entry={master}
        doc={masterSummary}
        tasks={tasks}
        onJump={vi.fn()}
        openView={open}
      />,
    );
    expect(view.getByTestId('group-done-link').textContent).toContain('109 completed tasks');
    expect(within(view.container).queryAllByTestId(/^waiting-row-/)).toHaveLength(0);
    expect(view.getByTestId('completed-preview').querySelectorAll('tbody tr')).toHaveLength(5);
    expect(new Set(titleReads.mock.calls.map(([index]) => index)).size).toBe(5);
    fireEvent.click(view.getByTestId('group-done-link'));
    expect(open).toHaveBeenCalledWith({ kind: 'group', status: 'done' });
  });

  it('keeps the document visible before pending or failed counters', async () => {
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const requests: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        requests.push(url);
        if (url.startsWith('/api/changeset/')) return pending;
        return Promise.resolve(new Response('{}', { status: 503 }));
      }),
    );
    const entry = taskEntry({ reference: masterRef, summary: masterSummary });
    const props = {
      entry,
      doc: masterSummary,
      tasks: {},
      onJump: vi.fn(),
      onOpenChangeSet: vi.fn(),
    };
    const view = render(<TaskReader {...props} bodyState="loading" />);
    expect(view.getByText('Master objective')).toBeTruthy();
    // F8: a master's lean counter loads independently of its body: exactly one counter read for this
    // master, and no other change-set read starts.
    expect(requests.filter((url) => url.startsWith('/api/changeset/'))).toEqual([
      '/api/changeset/master/counters?repo=repo&master=master&includeLeaves=false',
    ]);
    view.rerender(<TaskReader {...props} bodyState="available" />);
    await waitFor(() =>
      expect(view.getByTestId('changeset-state').getAttribute('data-review-state')).toBe('loading'),
    );
    expect(view.getByText('Master objective')).toBeTruthy();
    await act(async () => {
      finish(
        new Response(JSON.stringify({ status: 'unknown-scope', detail: 'counters unavailable' }), {
          status: 503,
        }),
      );
      await pending;
    });
    await waitFor(() =>
      expect(view.getByTestId('changeset-state').getAttribute('data-review-state')).not.toBe(
        'loading',
      ),
    );
    expect(view.getByText('Master objective')).toBeTruthy();
  });
});
