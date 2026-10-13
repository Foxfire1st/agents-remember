// L127-C-F02: the head names the own ID once, panel chrome never repeats the
// same document's title, and bare index blocks stay out while introducing
// prose or discarded history keep their block. Direct, series and lifecycle
// paths on all three forms.
import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedProjection, seedTaskDocuments, seriesNode, taskDoc } from './test-utils';
import { lifecycle } from '../lifecycle-list/test-utils';

function blockOrder(container: HTMLElement, skeleton: string): string[] {
  const root = container.querySelector(`[data-testid="${skeleton}"]`);
  if (!root) return [];
  return Array.from(root.querySelectorAll('[data-block]')).map(
    (el) => el.getAttribute('data-block') ?? '',
  );
}

// Every element whose whole text is the title: the head's own title and nothing else.
function titled(container: HTMLElement, title: string): Element[] {
  return Array.from(container.querySelectorAll('*')).filter((el) => el.textContent === title);
}

// The panel chrome's headings: every h2 that is not the document's own title.
function chromeHeadings(container: HTMLElement): Element[] {
  return Array.from(container.querySelectorAll('h2:not([data-testid="doc-title"])'));
}

describe('head identity and title chrome (L127-C-F02)', () => {
  it('shows the own ID once in the head on a direct task document', () => {
    const docPath = '/tasks/repo-a/m/01_unique.json';
    seedTaskDocuments([
      taskDoc({ id: 'UNIQUE-L127-ID', kind: 'subTask', docPath, title: 'Unique Head Title L127' }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(getByTestId('doc-id').textContent).toContain('UNIQUE-L127-ID');
    // The document title stands exactly once: the head itself. The panel
    // chrome keeps only navigation, never a repeated title.
    expect(titled(container, 'Unique Head Title L127')).toHaveLength(1);
    expect(chromeHeadings(container)).toHaveLength(0);
  });

  it('keeps no duplicate title on a direct master panel', () => {
    const docPath = '/tasks/repo-a/m/task.json';
    seedTaskDocuments([taskDoc({ id: 'M', kind: 'master', docPath, title: 'Master Chrome L127' })]);
    const { container } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(titled(container, 'Master Chrome L127')).toHaveLength(1);
    expect(chromeHeadings(container)).toHaveLength(0);
  });

  it('keeps no duplicate title on a series panel', () => {
    const series = seriesNode({
      seriesId: 's1',
      title: 'Series Chrome L127',
      docPath: '/tasks/repo-a/s1/task.json',
    });
    seedProjection({
      analytics: {
        driftSnapshots: [],
        stalestSidecars: [],
        routeCoverage: [],
        toolReports: [],
        ledgers: [],
        setupSummaries: [],
        setupProgress: [],
        taskDocuments: [
          taskDoc({
            id: 'S1M',
            kind: 'master',
            docPath: '/tasks/repo-a/s1/task.json',
            title: 'Series Chrome L127',
          }),
        ],
        series: [series],
        attentionQueue: [],
        engineProcesses: [],
        agentPickups: [],
        expectationRows: [],
      },
    });
    const { container } = render(<DetailPanel selectedId="series:s1" />);

    expect(titled(container, 'Series Chrome L127')).toHaveLength(1);
    expect(chromeHeadings(container)).toHaveLength(0);
  });

  it('hides the lifecycle heading where a drilled slice owns the title', () => {
    const master = taskDoc({
      kind: 'master',
      lifecycleId: 'LC-H2',
      docPath: '/tasks/repo-a/m/task.json',
      title: 'Lifecycle Master L127',
      subTasks: [
        { number: '1', name: 'Leaf one', file: '01_leaf.md', status: 'planning', scope: '' },
      ],
    });
    const leaf = taskDoc({
      id: 'L1',
      kind: 'subTask',
      docPath: '/tasks/repo-a/m/01_leaf.json',
      title: 'Leaf one',
      lifecycleId: 'LC-H2',
    });
    seedProjection({
      lifecycles: [lifecycle({ id: 'LC-H2' })],
      analytics: {
        driftSnapshots: [],
        stalestSidecars: [],
        routeCoverage: [],
        toolReports: [],
        ledgers: [],
        setupSummaries: [],
        setupProgress: [],
        taskDocuments: [master, leaf],
        series: [],
        attentionQueue: [],
        engineProcesses: [],
        agentPickups: [],
        expectationRows: [],
      },
    });
    const { container, getByTestId } = render(<DetailPanel selectedId="LC-H2" />);

    // The lifecycle context names the panel while no single document owns it.
    expect(chromeHeadings(container)).toHaveLength(1);
    fireEvent.click(getByTestId('subtask-open-1'));
    // The drilled slice draws its own title; the panel heading stands back.
    expect(chromeHeadings(container)).toHaveLength(0);
    expect(titled(container, 'Leaf one')).toHaveLength(1);
    expect(getByTestId('series-breadcrumb')).toBeTruthy();
    expect(getByTestId('doc-form').textContent).toBe('task');
  });
});

describe('empty index blocks (L127-C-F02)', () => {
  it('omits empty Now and Tasks on a bare master', () => {
    const docPath = '/tasks/repo-a/m/task.json';
    seedTaskDocuments([taskDoc({ kind: 'master', docPath, title: 'Bare' })]);
    const { container, queryByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(blockOrder(container, 'skeleton-master')).toEqual(['head']);
    expect(queryByTestId('now-split')).toBeNull();
  });

  it('omits empty Now and Masters on a bare sprint', () => {
    const docPath = '/tasks/repo-a/sprint/task.json';
    seedTaskDocuments([
      taskDoc({
        kind: 'master',
        form: 'sprint',
        orchestrates: ['master-a'],
        docPath,
        title: 'Bare sprint',
      }),
    ]);
    const { container, queryByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(blockOrder(container, 'skeleton-sprint')).toEqual(['head']);
    expect(queryByTestId('now-split')).toBeNull();
  });

  it('draws head alone on a bare task', () => {
    const docPath = '/tasks/repo-a/m/01_bare.json';
    seedTaskDocuments([taskDoc({ kind: 'subTask', docPath, title: 'Bare task' })]);
    const { container } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(blockOrder(container, 'skeleton-task')).toEqual(['head']);
  });

  it('keeps Tasks for discarded history with no rows', () => {
    const docPath = '/tasks/repo-a/m/task.json';
    seedTaskDocuments([
      taskDoc({
        kind: 'master',
        docPath,
        title: 'Audited',
        discardedSubTasks: [
          {
            number: '2',
            name: 'Never started',
            file: '02_never_started.md',
            scope: 'retired planning work',
            disposition: 'discard-unstarted',
            reason: 'No implementation was needed',
            discardedAt: '2026-08-24T12:00:00+00:00',
            proof: {
              childJson: { state: 'missing' },
              childMarkdown: { state: 'missing' },
              version: 'task-unstarted-evidence/v1',
              taskDocumentRef: {
                repository: 'repo-a',
                path: 'planning/02_never_started.json',
              },
              taskState: 'planning-unstarted',
              enclosureState: 'absent',
              locatorState: 'absent',
              doorState: 'absent',
              operationState: 'absent',
              seatState: 'absent',
              reviewState: 'absent',
              commitState: 'absent',
              fingerprint: 'a'.repeat(64),
            },
          },
        ],
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(blockOrder(container, 'skeleton-master')).toContain('tasks');
    expect(getByTestId('group-gone-link').textContent).toContain('discarded before start');
  });

  it('keeps Masters for discarded history with no rows', () => {
    const docPath = '/tasks/repo-a/sprint/task.json';
    seedTaskDocuments([
      taskDoc({
        kind: 'master',
        form: 'sprint',
        orchestrates: ['master-a'],
        docPath,
        title: 'Audited sprint',
        discardedSubTasks: [
          {
            number: '2',
            name: 'Never started',
            file: '02_never_started.md',
            scope: 'retired planning work',
            disposition: 'discard-unstarted',
            reason: 'No implementation was needed',
            discardedAt: '2026-08-24T12:00:00+00:00',
            proof: {
              childJson: { state: 'missing' },
              childMarkdown: { state: 'missing' },
              version: 'task-unstarted-evidence/v1',
              taskDocumentRef: {
                repository: 'repo-a',
                path: 'planning/02_never_started.json',
              },
              taskState: 'planning-unstarted',
              enclosureState: 'absent',
              locatorState: 'absent',
              doorState: 'absent',
              operationState: 'absent',
              seatState: 'absent',
              reviewState: 'absent',
              commitState: 'absent',
              fingerprint: 'a'.repeat(64),
            },
          },
        ],
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(blockOrder(container, 'skeleton-sprint')).toContain('masters');
    expect(getByTestId('group-gone-link').textContent).toContain('discarded before start');
  });
});
