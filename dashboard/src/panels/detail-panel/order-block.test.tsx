// MIK-R127 rules 3, 10 and 11 on a sprint's Order block: the masters that are
// not finished stand in rows by wave, what has landed is a count, the whole
// graph is one link away, and a mark by pointing appears only after a rest
// (evidence 7, with a controlled clock). The documents are made up.
import { act, fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { GRAPH_REST_MS } from '../sprint-graph/SprintGraphView';
import type { TaskExecutionNodeView } from '../../types/projection';
import { cells, seedTaskDocuments, taskDoc } from './test-utils';

const REPO = 'repo-a';
const SPRINT = `/tasks/${REPO}/sprint/task.json`;
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
  seedTaskDocuments([
    taskDoc({
      id: 'SPRINT',
      lifecycleId: undefined,
      kind: 'master',
      form: 'sprint',
      docPath: SPRINT,
      title: 'Sprint',
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
    taskDoc({
      id: 'TEXT',
      lifecycleId: undefined,
      kind: 'master',
      docPath: `/tasks/${REPO}/text/task.json`,
      title: 'Text helpers',
      subTasks: [
        { number: '1', name: 'One', file: '01_one.md', status: 'Completed', scope: '' },
        { number: '2', name: 'Two', file: '02_two.md', status: 'inProgress', scope: '' },
      ],
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
  it('draws what is not finished in rows by wave and what has landed as a count', () => {
    seedSprint();
    const onOpenLifecycle = vi.fn();
    const { container, getByTestId, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${SPRINT}`} onOpenLifecycle={onOpenLifecycle} />,
    );

    expect(cells(getByTestId('order-done').firstElementChild)).toEqual(['done', '1']);
    expect(cells(getByTestId('order-now').firstElementChild)).toEqual(['now', '1']);
    // a projected master shows its id and how many of its tasks are completed
    expect(cells(unitOf(container, 'text'))).toEqual(['TEXT', 'Text helpers', '1/2']);
    // within a wave, what waits for nothing unfinished stands first
    const wave = [...getByTestId('order-wave-2').querySelectorAll('[data-testid="order-node"]')];
    expect(wave.map((node) => node.getAttribute('data-state'))).toEqual(['ready', 'waiting']);
    expect(cells(getByTestId('order-wave-3').firstElementChild)).toEqual(['wave 3', '1']);
    // a landed unit is counted, not drawn; the card grid is not on the page
    expect(unitOf(container, 'seed')).toBeNull();
    expect(queryByTestId('sprint-graph')).toBeNull();

    fireEvent.click(unitOf(container, 'text'));
    expect(onOpenLifecycle).toHaveBeenCalledWith(`taskdoc:/tasks/${REPO}/text/task.json`);
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
  const masterPath = `/tasks/${REPO}/owner/task.json`;
  const data = [['L1', 'in-flight', 0], ['L2', 'ready', 1], ['L3', 'waiting', 1], ['L4', 'landed', -1]] as const;
  const leafRef = (id: string) => ({ repository: REPO, path: `owner/${id}.json` });
  const nodes = data.map(([id, state, wave]) => ({
    ...unit(id, id, wave, state), nodeId: `${REPO}/owner/${id}.json`, masterRef: leafRef(id),
    predecessors: id === 'L3' ? [{ predecessorRef: leafRef('L1'), predecessorTitle: 'L1', reason: 'waitsFor' }] : [],
  }));
  seedTaskDocuments([
    taskDoc({ id: 'M', repository: REPO, lifecycleId: undefined, kind: 'master', form: 'master', docPath: masterPath,
      title: 'Owner', executionGraphView: { nodes }, subTasks: data.map(([id, state]) => ({
        number: id, name: id, file: `${id}.md`, scope: '',
        status: state === 'landed' ? 'Completed' : state === 'in-flight' ? 'inProgress' : 'planning',
      })),
    }),
    ...data.map(([id]) => taskDoc({ id, repository: REPO, lifecycleId: undefined, kind: 'subTask',
      docPath: `/tasks/${REPO}/owner/${id}.json`, title: id, stepsDone: 1, stepsTotal: 5,
    })),
  ]);
  const open = vi.fn();
  const view = render(<DetailPanel selectedId={`taskdoc:${masterPath}`} onOpenLifecycle={open} />);
  const blocks = [...view.container.querySelectorAll('[data-block]')].map((block) => block.getAttribute('data-block'));
  expect(blocks.indexOf('order')).toBe(blocks.indexOf('now') + 1);
  const next = within(view.getByTestId('can-start-next')).getAllByTestId('order-node');
  expect(next.map((node) => node.getAttribute('title')?.split(' — ')[0])).toEqual(['L2']);
  expect(cells(view.getByTestId('order-done').firstElementChild)).toEqual(['done', '1']);
  const rows = view.getByTestId('order-rows');
  const waiting = rows.querySelector(`[data-node="${REPO}/owner/L3.json"]`) as HTMLElement;
  expect(cells(waiting)).toEqual(['L3', 'L3', '1/5']);
  fireEvent.mouseOver(waiting);
  act(() => { vi.advanceTimersByTime(GRAPH_REST_MS - 1); });
  expect(waiting.getAttribute('data-mark')).toBe('none');
  act(() => { vi.advanceTimersByTime(1); });
  expect(waiting.getAttribute('data-mark')).toBe('self');
  expect(rows.querySelector(`[data-node="${REPO}/owner/L1.json"]`)?.getAttribute('data-mark')).toBe('related');
  expect(waiting.getAttribute('data-fade')).toBe('true');
  expect(view.getByTestId('order-read').textContent).toBe('L3waits for: L1needed by: no open task');
  fireEvent.click(waiting);
  expect(open).toHaveBeenCalledWith(`taskdoc:/tasks/${REPO}/owner/L3.json`);
});

it.each([false, true])('names every legacy cycle member while counting completed nodes (%s)', async (allDone) => {
  stubMotion(false);
  const masterPath = `/tasks/${REPO}/cycle/task.json`;
  const nodes = [
    { ...unit('L1', 'Completed member', 1, 'landed', [['L2', 'Unfinished member']]), cycle: true },
    { ...unit('L2', 'Unfinished member', 1, allDone ? 'landed' : 'waiting', [['L1', 'Completed member']]), cycle: true },
  ];
  seedTaskDocuments([taskDoc({ id: 'CYCLE', repository: REPO, lifecycleId: undefined,
    kind: 'master', form: 'master', docPath: masterPath, executionGraphView: { nodes } })]);
  const view = await act(async () => render(<DetailPanel selectedId={`taskdoc:${masterPath}`} />));
  expect(cells(view.getByTestId('order-done').firstElementChild)).toEqual(['done', allDone ? '2' : '1']);
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
});
