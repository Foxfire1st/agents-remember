import { taskReferenceKey } from "../../data/taskReference";
import { dashboardStore } from '../../data/store';
// MIK-R127 rules 3, 10 and 11 on a sprint's Order block: the masters that are
// not finished stand in rows by wave, using task entries for state. The whole
// graph is one link away, and a mark by pointing appears only after a rest
// (evidence 7, with a controlled clock). The documents are made up.
import { act, fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { GRAPH_REST_MS } from '../sprint-graph/SprintGraphView';
import type { TaskExecutionNodeView } from '../../types/projection';
import { cells, seedTaskEntries, taskEntry, taskSummary, taskReference } from './test-utils';

const REPO = 'repo-a';
const SPRINT = `${REPO}/sprint/task.json`;
const ref = (dir: string) => ({ repository: REPO, path: `${dir}/task.json` });

function unit(
  dir: string,
  title: string,
  waveIndex: number,
  frontierState: TaskExecutionNodeView['frontierState'],
  waitsFor: Array<[string, string]> = [],
): TaskExecutionNodeView {
  return {
    nodeId: `${REPO}/${dir}/task.json`,
    kind: 'lump',
    masterRef: ref(dir),
    masterTitle: title,
    leafIds: [],
    leafTitles: [],
    waveIndex,
    frontierState,
    predecessors: waitsFor.map(([from, name]) => ({
      predecessorRef: ref(from),
      predecessorTitle: name,
      reason: `needs [${name}](https://example.org/${from})`,
    })),
  };
}

function seedSprint() {
  seedTaskEntries([
    taskEntry({ reference: ref('seed'), stage: 'completed' }),
    taskEntry({ reference: ref('paths'), stage: 'planned' }),
    taskEntry({ reference: ref('cli'), stage: 'planned' }),
    taskEntry({ reference: ref('book'), stage: 'planned' }),
    taskEntry({
      reference: taskReference(`${REPO}/text/01_one.json`),
      parent: taskReferenceKey(ref('text')),
      id: '1',
      title: 'One',
      stage: 'completed',
    }),
    taskEntry({
      reference: taskReference(`${REPO}/text/02_two.json`),
      parent: taskReferenceKey(ref('text')),
      id: '2',
      title: 'Two',
      stage: 'started',
      activity: 'works',
    }),
    taskEntry({
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference(SPRINT),
      id: 'SPRINT',
      title: 'Sprint',
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'sprint',
        kind: 'master',
        orchestrates: ['seed', 'text', 'paths', 'cli', 'book'],
        executionGraphView: {
          nodes: [
            unit('seed', 'Seed', 0, 'landed'),
            unit('text', 'Text helpers', 1, 'in-flight', [['seed', 'Seed']]),
            unit('cli', 'Command line', 2, 'waiting', [['text', 'Text helpers']]),
            unit('paths', 'Path helpers', 2, 'ready', [['seed', 'Seed']]),
            unit('book', 'Handbook', 3, 'waiting', [['cli', 'Command line']]),
          ],
        },
      }),
    }),
    taskEntry({
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference(`${REPO}/text/task.json`),
      children: [taskReferenceKey(taskReference(`${REPO}/text/01_one.json`)), taskReferenceKey(taskReference(`${REPO}/text/02_two.json`))],
      counts: { planned: 0, started: 1, completed: 1, aborted: 0, works: 1, paused: 0, unknown: 0 },
      id: 'TEXT',
      title: 'Text helpers',
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'master',
        kind: 'master',
      }),
    }),
  ]);
}

function stubMotion(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockReturnValue({ matches, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  );
}

// The mark of every unit on the page, by its title.
function marks(container: HTMLElement): Record<string, string | null> {
  return Object.fromEntries(
    [...container.querySelectorAll('[data-testid="order-node"]')].map((node) => [
      node.getAttribute('title')?.split(' — ')[0] ?? '',
      node.getAttribute('data-mark'),
    ]),
  );
}

const unitOf = (container: HTMLElement, dir: string): HTMLElement =>
  container.querySelector(`[data-node="${REPO}/${dir}/task.json"]`) as HTMLElement;

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('the order of a sprint (rules 3 and 10)', () => {
  it.each(['works', 'paused', 'unknown'] as const)('keeps every started task once, using entry activity %s despite graph in-flight', (activity) => {
    seedSprint();
    const key = taskReferenceKey(ref('text'));
    dashboardStore.getState().applyDelta('task', {
      ...dashboardStore.getState().tasks[key], activity,
    });
    const onOpenLifecycle = vi.fn();
    const { container, getByTestId, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${SPRINT}`} onOpenLifecycle={onOpenLifecycle} />,
    );

    expect(queryByTestId('order-done')).toBeNull();
    const row = getByTestId(activity === 'works' ? 'order-now' : 'order-wave-1');
    expect(cells(row.firstElementChild)).toEqual([activity === 'works' ? 'now' : 'wave 1', '1']);
    if (activity !== 'works') expect(queryByTestId('order-now')).toBeNull();
    const textNodes = getByTestId('order-rows').querySelectorAll(`[data-node="${REPO}/text/task.json"]`);
    expect(textNodes).toHaveLength(1);
    expect(row.contains(textNodes[0])).toBe(true);
    // a projected master shows its id and how many of its tasks are completed
    const word = activity === 'unknown' ? 'started' : activity;
    expect(cells(unitOf(container, 'text'))).toEqual(['TEXT', 'Text helpers', word, '1/2']);
    expect(unitOf(container, 'text').querySelector('[data-status]')?.getAttribute('data-status')).toBe(activity);
    // A started task wears the golden border in every activity, and the legend names it.
    expect(unitOf(container, 'text').getAttribute('data-tone')).toBe('work');
    expect(getByTestId('order-read').textContent).toContain('in work');
    // within a wave, what waits for nothing unfinished stands first
    const wave = [...getByTestId('order-wave-2').querySelectorAll('[data-testid="order-node"]')];
    expect(wave.map((node) => node.getAttribute('data-readiness'))).toEqual(['ready', 'waiting']);
    expect(wave.map((node) => node.getAttribute('data-tone'))).toEqual(['ready', 'waits']);
    expect(cells(getByTestId('order-wave-3').firstElementChild)).toEqual(['wave 3', '1']);
    // A completed entry is not drawn; the graph remains available separately.
    expect(unitOf(container, 'seed')).toBeNull();
    expect(queryByTestId('sprint-graph')).toBeNull();

    fireEvent.click(unitOf(container, 'text'));
    expect(onOpenLifecycle).toHaveBeenCalledWith(`taskdoc:${REPO}/text/task.json`);
  });

  it('keeps the whole graph one link away and returns', () => {
    seedSprint();
    const { getByTestId, getAllByTestId, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${SPRINT}`} />,
    );

    fireEvent.click(getByTestId('order-graph-link'));
    expect(getByTestId('doc-view-title').textContent).toBe('Order graph');
    // every unit, the landed one too, with the reason of each edge
    expect(getAllByTestId('graph-box')).toHaveLength(5);
    const reason = getAllByTestId('graph-predecessor')[0];
    expect(reason.textContent).toContain('needs Seed');
    // the reason of an edge is authored text: a link in it is a link
    expect(within(reason).getByRole('link', { name: 'Seed' }).getAttribute('href')).toBe(
      'https://example.org/seed',
    );

    fireEvent.click(getByTestId('doc-view-back'));
    expect(queryByTestId('doc-view')).toBeNull();
    expect(getByTestId('order-rows')).toBeTruthy();
  });
});

describe('the mark of the order rows (rule 11)', () => {
  it('marks nothing when the pointer passes over', () => {
    vi.useFakeTimers();
    stubMotion(false);
    seedSprint();
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${SPRINT}`} />);

    fireEvent.mouseOver(unitOf(container, 'cli'));
    fireEvent.mouseOver(getByTestId('order-rows'));
    act(() => {
      vi.advanceTimersByTime(GRAPH_REST_MS * 2);
    });
    expect(new Set(Object.values(marks(container)))).toEqual(new Set(['none']));
  });

  it('marks what a unit waits for and what waits for it once the pointer rests', () => {
    vi.useFakeTimers();
    stubMotion(false);
    seedSprint();
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${SPRINT}`} />);

    fireEvent.mouseOver(unitOf(container, 'cli'));
    act(() => {
      vi.advanceTimersByTime(GRAPH_REST_MS - 1);
    });
    expect(marks(container)['Command line']).toBe('none');
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(marks(container)).toEqual({
      'Text helpers': 'related',
      'Path helpers': 'other',
      'Command line': 'self',
      Handbook: 'related',
    });
    expect(getByTestId('order-read').textContent).toBe(
      'Command linewaits for: Text helpersneeded by: Handbook',
    );
    // going on to a neighbour keeps the picture until the pointer rests again
    fireEvent.mouseOver(unitOf(container, 'paths'));
    expect(marks(container)['Command line']).toBe('self');
    act(() => {
      vi.advanceTimersByTime(GRAPH_REST_MS);
    });
    expect(marks(container)['Path helpers']).toBe('self');
    expect(getByTestId('order-read').textContent).toContain('waits for: nothing unfinished');
  });

  it('marks at once on keyboard focus and fades unless motion is reduced', () => {
    vi.useFakeTimers();
    stubMotion(false);
    seedSprint();
    const { container } = render(<DetailPanel selectedId={`taskdoc:${SPRINT}`} />);

    fireEvent.focus(unitOf(container, 'book'));
    expect(marks(container).Handbook).toBe('self');
    expect(marks(container)['Command line']).toBe('related');
    expect(unitOf(container, 'book').getAttribute('data-fade')).toBe('true');
  });

  it('drops the fade and keeps the rest where the reader asks for reduced motion', () => {
    vi.useFakeTimers();
    stubMotion(true);
    seedSprint();
    const { container } = render(<DetailPanel selectedId={`taskdoc:${SPRINT}`} />);

    expect(unitOf(container, 'book').getAttribute('data-fade')).toBe('false');
    fireEvent.mouseOver(unitOf(container, 'book'));
    expect(marks(container).Handbook).toBe('none');
    act(() => {
      vi.advanceTimersByTime(GRAPH_REST_MS);
    });
    expect(marks(container).Handbook).toBe('self');
  });
});

it('draws a master’s tasks after Now with ready-only next tasks and the shared rest mark', () => {
  vi.useFakeTimers();
  stubMotion(false);
  const masterPath = `${REPO}/owner/task.json`;
  const data = [
    ['L1', 'in-flight', 0],
    ['L2', 'ready', 1],
    ['L3', 'waiting', 1],
    ['L4', 'landed', -1],
  ] as const;
  const leafRef = (id: string) => ({ repository: REPO, path: `owner/${id}.json` });
  const nodes = data.map(([id, state, wave]) => ({
    ...unit(id, id, wave, state),
    nodeId: `${REPO}/owner/${id}.json`,
    masterRef: leafRef(id),
    predecessors:
      id === 'L3'
        ? [{ predecessorRef: leafRef('L1'), predecessorTitle: 'L1', reason: 'waitsFor' }]
        : [],
  }));
  const children = data.map(([id, state]) =>
    taskEntry({
      reference: leafRef(id),
      id,
      title: id,
      stage: state === 'landed' ? 'completed' : state === 'in-flight' ? 'started' : 'planned',
      stepsDone: 1,
      stepsTotal: 5,
      summary: taskSummary({ form: 'task' }),
      parent: taskReferenceKey(taskReference(masterPath)),
    }),
  );
  seedTaskEntries([
    taskEntry({
      reference: taskReference(masterPath),
      id: 'M',
      title: 'Owner',
      children: children.map((item) => item.key),
      summary: taskSummary({ kind: 'master', form: 'master', executionGraphView: { nodes } }),
      counts: { started: 1, planned: 2, completed: 1, aborted: 0, works: 0, paused: 0, unknown: 1 },
    }),
    ...children,
  ]);
  const open = vi.fn();
  const view = render(<DetailPanel selectedId={`taskdoc:${masterPath}`} onOpenLifecycle={open} />);
  const blocks = [...view.container.querySelectorAll('[data-block]')].map((block) =>
    block.getAttribute('data-block'),
  );
  expect(blocks.indexOf('order')).toBe(blocks.indexOf('now') + 1);
  const next = within(view.getByTestId('can-start-next')).getAllByTestId('order-node');
  expect(next.map((node) => node.getAttribute('title')?.split(' — ')[0])).toEqual(['L2']);
  expect(view.queryByTestId('order-done')).toBeNull();
  const rows = view.getByTestId('order-rows');
  const waiting = rows.querySelector(`[data-node="${REPO}/owner/L3.json"]`) as HTMLElement;
  expect(cells(waiting)).toEqual(['L3', 'L3', 'planned', '1/5']);
  fireEvent.mouseOver(waiting);
  act(() => {
    vi.advanceTimersByTime(GRAPH_REST_MS - 1);
  });
  expect(waiting.getAttribute('data-mark')).toBe('none');
  act(() => {
    vi.advanceTimersByTime(1);
  });
  expect(waiting.getAttribute('data-mark')).toBe('self');
  expect(rows.querySelector(`[data-node="${REPO}/owner/L1.json"]`)?.getAttribute('data-mark')).toBe(
    'related',
  );
  expect(waiting.getAttribute('data-fade')).toBe('true');
  expect(view.getByTestId('order-read').textContent).toBe('L3waits for: L1needed by: no open task');
  fireEvent.click(waiting);
  expect(open).toHaveBeenCalledWith(`taskdoc:${REPO}/owner/L3.json`);
});

it.each([false, true])(
  'names every legacy cycle member while hiding completed entries (%s)',
  async (allDone) => {
    stubMotion(false);
    const masterPath = `${REPO}/cycle/task.json`;
    const nodes = [
      {
        ...unit('L1', 'Completed member', 1, 'landed', [['L2', 'Unfinished member']]),
        cycle: true,
      },
      {
        ...unit('L2', 'Unfinished member', 1, allDone ? 'landed' : 'waiting', [
          ['L1', 'Completed member'],
        ]),
        cycle: true,
      },
    ];
    seedTaskEntries([
      taskEntry({ reference: ref('L1'), stage: 'completed' }),
      taskEntry({ reference: ref('L2'), stage: allDone ? 'completed' : 'planned' }),
      taskEntry({
        id: 'CYCLE',
        reference: taskReference(masterPath),
        summary: taskSummary({ kind: 'master', form: 'master', executionGraphView: { nodes } }),
      }),
    ]);
    const view = await act(async () =>
      render(<DetailPanel selectedId={`taskdoc:${masterPath}`} />),
    );
    expect(view.queryByTestId('order-done')).toBeNull();
    expect(view.container.querySelector(`[data-node="${REPO}/L1/task.json"]`)).toBeNull();
    expect(view.queryByTestId('can-start-next')).toBeNull();
    if (!allDone) {
      const mark = view.getByTestId('order-wave-1').firstElementChild?.textContent;
      expect(mark).toContain('cycle');
      expect(mark).toContain('Completed member');
      expect(mark).toContain('Unfinished member');
    } else {
      expect(view.queryByTestId('order-wave-1')).toBeNull();
    }
  },
);
