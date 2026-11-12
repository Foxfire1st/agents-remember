// L127-C-F02: the head names the own ID once, panel chrome never repeats the
// same document's title, and bare index blocks stay out while introducing
// prose or discarded history keep their block. Direct, series and lifecycle
// paths on all three forms.
import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedTaskEntries, taskEntry, taskSummary, taskReference, taskTree } from './test-utils';

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
    const docPath = 'repo-a/m/01_unique.json';
    seedTaskEntries([
      taskEntry({
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(docPath),
        id: 'UNIQUE-L127-ID',
        title: 'Unique Head Title L127',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'task',
          kind: 'subTask',
        }),
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(getByTestId('doc-id').textContent).toContain('UNIQUE-L127-ID');
    // The document title stands exactly once: the head itself. The panel
    // chrome keeps only navigation, never a repeated title.
    expect(titled(container, 'Unique Head Title L127')).toHaveLength(1);
    expect(chromeHeadings(container)).toHaveLength(0);
  });

  it('keeps no duplicate title on a direct master panel', () => {
    const docPath = 'repo-a/m/task.json';
    seedTaskEntries([
      taskEntry({
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(docPath),
        id: 'M',
        title: 'Master Chrome L127',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'master',
          kind: 'master',
        }),
      }),
    ]);
    const { container } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(titled(container, 'Master Chrome L127')).toHaveLength(1);
    expect(chromeHeadings(container)).toHaveLength(0);
  });

  it('keeps the title once while drilling an entry child and following its parent', () => {
    const master = taskEntry({
      stepsDone: 0,
      stepsTotal: 0,
      reference: taskReference('repo-a/m/task.json'),
      id: 'M',
      title: 'Master L127',
      stage: 'started',
      summary: taskSummary({ form: 'master', kind: 'master', objective: '' }),
    });
    const leaf = taskEntry({
      stepsDone: 0,
      stepsTotal: 0,
      reference: taskReference('repo-a/m/01_leaf.json'),
      id: 'L1',
      title: 'Leaf one',
      stage: 'planned',
      summary: taskSummary({ form: 'task', objective: '' }),
    });
    seedTaskEntries(taskTree(master, [leaf]));
    const { container, getByTestId } = render(
      <DetailPanel selectedId="taskdoc:repo-a/m/task.json" />,
    );
    expect(chromeHeadings(container)).toHaveLength(0);
    fireEvent.click(getByTestId('subtask-open-1'));
    expect(chromeHeadings(container)).toHaveLength(0);
    expect(titled(container, 'Leaf one')).toHaveLength(1);
    expect(getByTestId('master-parent-link')).toBeTruthy();
    expect(getByTestId('doc-form').textContent).toBe('task');
  });
});

describe('empty index blocks (L127-C-F02)', () => {
  it('omits empty Now and Tasks on a bare master', () => {
    const docPath = 'repo-a/m/task.json';
    seedTaskEntries([
      taskEntry({
        id: '1',
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(docPath),
        title: 'Bare',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'master',
          kind: 'master',
        }),
      }),
    ]);
    const { container, queryByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(blockOrder(container, 'skeleton-master')).toEqual(['head']);
    expect(queryByTestId('now-split')).toBeNull();
  });

  it('omits empty Now and Masters on a bare sprint', () => {
    const docPath = 'repo-a/sprint/task.json';
    seedTaskEntries([
      taskEntry({
        id: '1',
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(docPath),
        title: 'Bare sprint',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'sprint',
          kind: 'master',
          orchestrates: ['master-a'],
        }),
      }),
    ]);
    const { container, queryByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(blockOrder(container, 'skeleton-sprint')).toEqual(['head']);
    expect(queryByTestId('now-split')).toBeNull();
  });

  it('draws head alone on a bare task', () => {
    const docPath = 'repo-a/m/01_bare.json';
    seedTaskEntries([
      taskEntry({
        id: '1',
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(docPath),
        title: 'Bare task',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'task',
          kind: 'subTask',
        }),
      }),
    ]);
    const { container } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(blockOrder(container, 'skeleton-task')).toEqual(['head']);
  });

  it('keeps Tasks for discarded history with no rows', () => {
    const docPath = 'repo-a/m/task.json';
    seedTaskEntries([
      taskEntry({
        id: '1',
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(docPath),
        title: 'Audited',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'master',
          kind: 'master',
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
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(blockOrder(container, 'skeleton-master')).toContain('tasks');
    expect(getByTestId('group-gone-link').textContent).toContain('discarded before start');
  });

  it('keeps Masters for discarded history with no rows', () => {
    const docPath = 'repo-a/sprint/task.json';
    seedTaskEntries([
      taskEntry({
        id: '1',
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(docPath),
        title: 'Audited sprint',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'sprint',
          kind: 'master',
          orchestrates: ['master-a'],
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
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(blockOrder(container, 'skeleton-sprint')).toContain('masters');
    expect(getByTestId('group-gone-link').textContent).toContain('discarded before start');
  });
});
