// MIK-R127 evidence 7: the order-graph mark with a controlled clock — a
// pointer that passes over marks nothing, a pointer that rests marks, focus
// marks at once, and reduced motion drops the fade.
import { act, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { TaskExecutionGraphView } from '../../types/projection';
import { GRAPH_REST_MS, SprintGraphView } from './SprintGraphView';

const MASTER_A = { repository: 'repo-a', path: 'master-a/task.json' };
const ATOMIC_F = { repository: 'repo-a', path: 'atomic-f/task.json' };

const graph: TaskExecutionGraphView = {
  nodes: [
    {
      nodeId: 'repo-a/master-a/task.json',
      kind: 'segment',
      masterRef: MASTER_A,
      masterTitle: 'Master A',
      leafIds: ['A-L1'],
      leafTitles: ['Leaf one'],
      waveIndex: 1,
      frontierState: 'in-flight',
      predecessors: [],
    },
    {
      nodeId: 'repo-a/atomic-f/task.json',
      kind: 'lump',
      masterRef: ATOMIC_F,
      masterTitle: 'Atomic F',
      leafIds: [],
      leafTitles: [],
      waveIndex: 2,
      frontierState: 'waiting',
      predecessors: [
        {
          predecessorRef: MASTER_A,
          predecessorTitle: 'Master A',
          reason: 'the framework lands first',
        },
      ],
    },
  ],
};

function stubMotion(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockReturnValue({
      matches,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  );
}

function marked(container: HTMLElement): string[] {
  return [...container.querySelectorAll('[data-testid="graph-box"]')]
    .filter((box) => box.getAttribute('data-marked') === 'true')
    .map((box) => box.getAttribute('data-node') ?? '');
}

describe('SprintGraphView rest highlight (rule 11)', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('marks nothing when the pointer passes over', () => {
    vi.useFakeTimers();
    stubMotion(false);
    const { container } = render(<SprintGraphView graphView={graph} />);
    const boxes = container.querySelectorAll('[data-testid="graph-box"]');

    fireEvent.mouseOver(boxes[0]);
    fireEvent.mouseOver(container.querySelector('[data-testid="sprint-graph"]')!);
    act(() => {
      vi.advanceTimersByTime(GRAPH_REST_MS * 2);
    });
    expect(marked(container)).toEqual([]);
  });

  it('marks the rested node and its relations, and never blanks between neighbours', () => {
    vi.useFakeTimers();
    stubMotion(false);
    const { container } = render(<SprintGraphView graphView={graph} />);
    const boxes = container.querySelectorAll('[data-testid="graph-box"]');

    fireEvent.mouseOver(boxes[0]);
    expect(marked(container)).toEqual([]);
    act(() => {
      vi.advanceTimersByTime(GRAPH_REST_MS);
    });
    expect(marked(container)).toEqual(['repo-a/master-a/task.json', 'repo-a/atomic-f/task.json']);

    // moving to the neighbour: what is shown stays until the rest fires there
    fireEvent.mouseOver(boxes[1]);
    expect(marked(container)).toEqual(['repo-a/master-a/task.json', 'repo-a/atomic-f/task.json']);
    act(() => {
      vi.advanceTimersByTime(GRAPH_REST_MS);
    });
    expect(marked(container)).toEqual(['repo-a/master-a/task.json', 'repo-a/atomic-f/task.json']);

    // leaving the graph clears after the rest, not at once
    fireEvent.mouseLeave(container.querySelector('[data-testid="sprint-graph"]')!);
    expect(marked(container)).not.toEqual([]);
    act(() => {
      vi.advanceTimersByTime(GRAPH_REST_MS);
    });
    expect(marked(container)).toEqual([]);
  });

  it('marks at once on keyboard focus', () => {
    vi.useFakeTimers();
    stubMotion(false);
    const { container } = render(<SprintGraphView graphView={graph} />);
    const boxes = container.querySelectorAll('[data-testid="graph-box"]');

    fireEvent.focus(boxes[1]);
    expect(marked(container)).toEqual(['repo-a/master-a/task.json', 'repo-a/atomic-f/task.json']);
  });

  it('drops the fade where the reader asks for reduced motion, and keeps the rest', () => {
    vi.useFakeTimers();
    stubMotion(true);
    const { container } = render(<SprintGraphView graphView={graph} />);

    expect(
      container.querySelector('[data-testid="sprint-graph"]')?.getAttribute('data-reduced-motion'),
    ).toBe('true');
    for (const box of container.querySelectorAll('[data-testid="graph-box"]')) {
      expect(box.getAttribute('data-fade')).toBe('false');
    }

    const boxes = container.querySelectorAll('[data-testid="graph-box"]');
    fireEvent.mouseOver(boxes[0]);
    act(() => {
      vi.advanceTimersByTime(GRAPH_REST_MS);
    });
    expect(marked(container)).toEqual(['repo-a/master-a/task.json', 'repo-a/atomic-f/task.json']);
  });

  it('keeps the fade where no reduced motion is asked', () => {
    stubMotion(false);
    const { container } = render(<SprintGraphView graphView={graph} />);
    for (const box of container.querySelectorAll('[data-testid="graph-box"]')) {
      expect(box.getAttribute('data-fade')).toBe('true');
    }
  });
});
