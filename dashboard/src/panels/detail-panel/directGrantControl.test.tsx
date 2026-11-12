import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { TaskEntry, TaskSummary } from '../../types/projection';
import { DetailPanel } from './DetailPanel';
import { taskTree, seedTaskEntries, taskEntry, taskSummary, taskReference } from './test-utils';

afterEach(() => {
  vi.unstubAllGlobals();
});

function seedGrantedLeaf(over: Partial<TaskSummary> = {}, stage: TaskEntry['stage'] = 'planned') {
  const master = taskEntry({
    id: '1',
    stage: 'started',
    stepsDone: 0,
    stepsTotal: 0,
    activity: 'works',
    reference: taskReference('repo-a/direct-master/task.json'),
    title: 'Direct master',
    summary: taskSummary({
      type: '',
      createdAt: '2026-06-20T09:00:00+00:00',
      form: 'master',
      kind: 'master',
      objective: 'Master objective.',
    }),
  });
  const leaf = taskEntry({
    stepsDone: 0,
    stepsTotal: 0,
    reference: taskReference('repo-a/direct-master/leaf.json'),
    id: 'L1',
    title: 'Direct leaf',
    stage,
    summary: taskSummary({
      type: '',
      createdAt: '2026-06-20T09:00:00+00:00',
      form: 'task',
      kind: 'subTask',
      objective: 'Leaf objective.',
      directGrant: { grantedBy: 'developer', grantedAt: '2026-10-06T02:00:00+00:00' },
      ...over,
    }),
  });
  seedTaskEntries(taskTree(master, [leaf]));
}

function seedUngrantedLeaf(over: Partial<TaskSummary> = {}, stage: TaskEntry['stage'] = 'planned') {
  const master = taskEntry({
    id: '1',
    stage: 'started',
    stepsDone: 0,
    stepsTotal: 0,
    activity: 'works',
    reference: taskReference('repo-a/direct-master/task.json'),
    title: 'Direct master',
    summary: taskSummary({
      type: '',
      createdAt: '2026-06-20T09:00:00+00:00',
      form: 'master',
      kind: 'master',
      objective: 'Master objective.',
    }),
  });
  const leaf = taskEntry({
    stepsDone: 0,
    stepsTotal: 0,
    reference: taskReference('repo-a/direct-master/leaf.json'),
    id: 'L1',
    title: 'Direct leaf',
    stage,
    summary: taskSummary({
      type: '',
      createdAt: '2026-06-20T09:00:00+00:00',
      form: 'task',
      kind: 'subTask',
      objective: 'Leaf objective.',
      ...over,
    }),
  });
  seedTaskEntries(taskTree(master, [leaf]));
}

function grantPayload() {
  return JSON.stringify({
    ok: true,
    operation: 'direct_execution_grant',
    state: 'granted',
    summary: 'repo-a:direct-master/leaf.json has its direct-execution grant',
    changed: true,
    grant: { grantedBy: 'developer', grantedAt: '2026-10-06T02:00:00+00:00' },
  });
}

function refusalPayload() {
  return JSON.stringify({
    ok: false,
    operation: 'direct_execution_grant',
    state: 'refused',
    status: 'direct-grant-leaf-started',
    detail: 'the grant is refused: the leaf has started (status=inProgress).',
    changed: false,
    grant: null,
  });
}

function taskSwitch() {
  return screen.getByRole('switch', { name: 'Direct execution' }) as HTMLButtonElement;
}

describe('DetailPanel direct-execution grant switch (MIK-R150 rules 9 to 13)', () => {
  it("draws nothing of the grant on a master's rows", async () => {
    seedGrantedLeaf();
    const view = render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/task.json" />);
    await view.findByText('Direct master');
    // No row control, no row mark: rule 9 removed the control and the badge.
    expect(view.container.querySelectorAll('[data-testid^="subtask-open-direct"]').length).toBe(0);
    expect(screen.queryByText('Make direct')).toBeNull();
    expect(screen.queryByText(/Direct ·/)).toBeNull();
    expect(screen.queryByRole('switch')).toBeNull();
  });

  it('shows an off switch with no words on an ungranted planning task', async () => {
    seedUngrantedLeaf();
    render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);

    const control = taskSwitch();
    expect(control.getAttribute('aria-checked')).toBe('false');
    expect(screen.queryByText(/given by/)).toBeNull();
    // One control only: no second grant button beside the switch.
    expect(screen.queryAllByRole('switch')).toHaveLength(1);
  });

  it("shows an on switch with the giver's words on a granted planning task", async () => {
    seedGrantedLeaf();
    render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);

    const control = taskSwitch();
    expect(control.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('given by developer')).toBeTruthy();
  });

  it('shows bare words and no switch on a granted task that is not planning', async () => {
    seedGrantedLeaf({}, 'started');
    const view = render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);
    await view.findByText('Direct leaf');

    expect(screen.getByText('Direct · given by developer')).toBeTruthy();
    expect(screen.queryByRole('switch')).toBeNull();
  });

  it('shows nothing of the grant on an ungranted task that is not planning', async () => {
    seedUngrantedLeaf({}, 'completed');
    const view = render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);
    await view.findByText('Direct leaf');

    expect(screen.queryByTestId('taskdoc-direct-line')).toBeNull();
    expect(screen.queryByRole('switch')).toBeNull();
  });

  it("gives the grant through the route and shows the server's words", async () => {
    seedUngrantedLeaf();
    const fetch = vi
      .fn()
      .mockImplementation(() => Promise.resolve(new Response(grantPayload(), { status: 200 })));
    vi.stubGlobal('fetch', fetch);

    render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);
    fireEvent.click(taskSwitch());

    await waitFor(() => expect(screen.getByText('given by developer')).toBeTruthy());
    expect(fetch).toHaveBeenCalledWith('/api/direct-execution-grant', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        repository: 'repo-a',
        path: 'direct-master/leaf.json',
        action: 'give',
      }),
    });
    expect(taskSwitch().getAttribute('aria-checked')).toBe('true');
  });

  it('disables the switch while the request runs and keeps its position', async () => {
    seedUngrantedLeaf();
    let answer: ((value: Response) => void) | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            answer = resolve;
          }),
      ),
    );

    render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);
    fireEvent.click(taskSwitch());

    await waitFor(() => expect(taskSwitch().disabled).toBe(true));
    // Still off while pending: the position does not move before the answer.
    expect(taskSwitch().getAttribute('aria-checked')).toBe('false');
    await waitFor(() => answer?.(new Response(grantPayload(), { status: 200 })));
    await waitFor(() => expect(taskSwitch().getAttribute('aria-checked')).toBe('true'));
  });

  it('shows the refusal words and keeps the real state when the route refuses', async () => {
    seedUngrantedLeaf();
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation(() => Promise.resolve(new Response(refusalPayload(), { status: 200 }))),
    );

    render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);
    fireEvent.click(taskSwitch());

    await waitFor(() =>
      expect(screen.getByTestId('taskdoc-direct-note').textContent).toContain(
        'the leaf has started',
      ),
    );
    expect(taskSwitch().getAttribute('aria-checked')).toBe('false');
  });

  it('follows a live projection change after mount', async () => {
    seedGrantedLeaf();
    const view = render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);
    expect(taskSwitch().getAttribute('aria-checked')).toBe('true');

    seedUngrantedLeaf();
    view.rerender(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);
    await waitFor(() => expect(taskSwitch().getAttribute('aria-checked')).toBe('false'));

    seedGrantedLeaf();
    view.rerender(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);
    await waitFor(() => expect(taskSwitch().getAttribute('aria-checked')).toBe('true'));
  });

  it('keeps the actual grant after a refused revoke', async () => {
    seedGrantedLeaf();
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation(() => Promise.resolve(new Response(refusalPayload(), { status: 200 }))),
    );

    render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf.json" />);
    fireEvent.click(taskSwitch());

    await waitFor(() =>
      expect(screen.getByTestId('taskdoc-direct-note').textContent).toContain(
        'the leaf has started',
      ),
    );
    // The served refusal carries no grant, but the switch keeps the actual one.
    expect(screen.getByText('given by developer')).toBeTruthy();
    expect(taskSwitch().getAttribute('aria-checked')).toBe('true');
  });

  it('does not transfer grant state across selection changes', async () => {
    // L150-S7-TEST-01 repair: the old test re-rendered the SAME selectedId with
    // new seed data and never changed the selection. This one really changes
    // selectedId: leaf one carries a grant and a refusal notice, leaf two is
    // untouched. Rule 13's reset must clear both the grant and the notice.
    const master = taskEntry({
      id: '1',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/direct-master/task.json'),
      title: 'Direct master',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'master',
        kind: 'master',
        objective: 'Master objective.',
      }),
    });
    const leafOne = taskEntry({
      stepsDone: 0,
      stepsTotal: 0,
      reference: taskReference('repo-a/direct-master/leaf1.json'),
      id: 'L1',
      title: 'Direct leaf one',
      stage: 'planned',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        objective: 'Leaf one objective.',
        directGrant: { grantedBy: 'developer', grantedAt: '2026-10-06T02:00:00+00:00' },
      }),
    });
    const leafTwo = taskEntry({
      stepsDone: 0,
      stepsTotal: 0,
      reference: taskReference('repo-a/direct-master/leaf2.json'),
      id: 'L2',
      title: 'Direct leaf two',
      stage: 'planned',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        objective: 'Leaf two objective.',
      }),
    });
    seedTaskEntries(taskTree(master, [leafOne, leafTwo]));
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation(() => Promise.resolve(new Response(refusalPayload(), { status: 200 }))),
    );

    const view = render(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf1.json" />);
    await view.findByText('Direct leaf one');
    expect(taskSwitch().getAttribute('aria-checked')).toBe('true');

    // A refused revoke leaves a notice on leaf one's line.
    fireEvent.click(taskSwitch());
    await waitFor(() =>
      expect(screen.getByTestId('taskdoc-direct-note').textContent).toContain(
        'the leaf has started',
      ),
    );

    // A real selection change onto the untouched leaf two.
    view.rerender(<DetailPanel selectedId="taskdoc:repo-a/direct-master/leaf2.json" />);
    await view.findByText('Direct leaf two');
    await waitFor(() => expect(taskSwitch().getAttribute('aria-checked')).toBe('false'));
    expect(screen.queryByText(/given by/)).toBeNull();
    // Leaf one's notice does not follow the selection.
    expect(screen.queryByTestId('taskdoc-direct-note')).toBeNull();
  });
});
