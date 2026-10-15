// MIK-R127 evidence 1: per form, the order of the blocks on a document that
// has everything, and on a document that has only the required fields. The
// objective follows the complete head and actual series line on every form
// (MIK-R128 rule 15), before Now or Steps.
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedTaskDocuments, taskDoc } from './test-utils';
import { block } from './pageStyles';
import { TaskContent } from './taskReader';
import { fireEvent } from '@testing-library/react';
import { vi } from 'vitest';

function blockOrder(container: HTMLElement, skeleton: string): string[] {
  const root = container.querySelector(`[data-testid="${skeleton}"]`);
  if (!root) throw new Error(`skeleton not rendered: ${skeleton}`);
  return [...root.querySelectorAll('[data-block]')].map(
    (element) => element.getAttribute('data-block') ?? '',
  );
}

const LEAF = '/tasks/repo-a/m/01_leaf.json';
const MASTER = '/tasks/repo-a/m/task.json';

function leafFull() {
  return taskDoc({
    id: 'L1',
    kind: 'subTask',
    docPath: LEAF,
    title: 'Leaf one',
    status: 'inProgress',
    type: 'SubTask (role instructions)',
    statusNote: 'Leaf status note text.',
    headerNotes: [
      { label: 'Master', value: 'task.md' },
      { label: 'Stage', value: 'Run 3' },
    ],
    requirements: ['Requirement one.', 'Requirement two.'],
    steps: [
      {
        id: 'S1',
        title: 'Done step',
        status: 'done',
        outcome: 'The deliverable.',
        note: 'Done prose.',
        substeps: [],
        noteTruncated: false,
        outcomeTruncated: false,
      },
      {
        id: 'S2',
        title: 'Open step',
        status: 'pending',
        substeps: [],
        noteTruncated: false,
        outcomeTruncated: false,
      },
    ],
    stepsDone: 1,
    stepsTotal: 2,
    reviewState: {
      round: 1,
      pending: false,
      additionalRounds: 0,
      findings: [
        { findingId: 'F1', description: 'Open finding.', open: true, descriptionTruncated: false },
      ],
      totalFindings: 1,
      openFindings: 1,
    },
    objective: 'Leaf objective.',
    design: 'Leaf design.',
    codeExamples: [
      {
        id: 'E1',
        title: 'Example',
        distinctChange: 'The change.',
        why: 'The reason.',
        language: 'tsx',
        snippet: 'const example = true;',
      },
    ],
    decisions: [
      { at: '2026-10-01', decision: 'Decided.', rationale: 'Because.', product_record: false },
    ],
    openQuestions: ['A question?'],
    references: ['notes/a.md'],
    sections: [{ kind: 'freeform', heading: 'Extra', body: 'Extra body.' }],
  });
}

function masterDoc() {
  return taskDoc({
    id: 'M',
    kind: 'master',
    docPath: MASTER,
    title: 'Master',
    status: 'inProgress',
    form: 'master',
    type: 'Feature',
    executionNature: 'atomic',
    statusNote: 'Master status note.',
    headerNotes: [{ label: 'Authority', value: 'D1' }],
  });
}

describe('task skeleton order (rule 5)', () => {
  it('draws every block in the fixed order on a document that has everything', () => {
    seedTaskDocuments([masterDoc(), leafFull()]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    expect(getByTestId('doc-form').textContent).toBe('task');
    expect(blockOrder(container, 'skeleton-task')).toEqual([
      'head',
      'objective',
      'steps',
      'review',
      'requirements',
      'design',
      'code',
      'decisions',
      'further',
    ]);
  });

  it('draws only the head on a document with the required fields alone', () => {
    seedTaskDocuments([taskDoc({ kind: 'subTask', docPath: LEAF, title: 'Bare leaf' })]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    expect(getByTestId('doc-form').textContent).toBe('task');
    expect(blockOrder(container, 'skeleton-task')).toEqual(['head']);
  });
});

describe('master skeleton order (rule 4)', () => {
  function masterFull() {
    return taskDoc({
      id: 'M',
      kind: 'master',
      docPath: MASTER,
      title: 'Master',
      status: 'inProgress',
      form: 'master',
      type: 'Feature',
      executionNature: 'atomic',
      statusNote: 'Master status note.',
      headerNotes: [{ label: 'Authority', value: 'D1' }],
      objective: 'Master objective.',
      requirements: ['Master requirement.'],
      decisions: [
        { at: '2026-10-01', decision: 'Decided.', rationale: 'Because.', product_record: false },
      ],
      openQuestions: ['A question?'],
      references: ['notes/m.md'],
      sections: [
        { kind: 'subTasks', heading: 'Sub-tasks', body: 'Tasks intro.' },
        { kind: 'sharedDecisions', heading: 'Shared decisions', body: 'Decisions intro.' },
        { kind: 'freeform', heading: 'Extra', body: 'Extra body.' },
      ],
      subTasks: [
        { number: 'L1', name: 'Leaf one', file: '01_leaf.md', status: 'inProgress', scope: '' },
        { number: 'L2', name: 'Leaf two', file: '02_leaf.md', status: 'planning', scope: '' },
        { number: 'L3', name: 'Leaf three', file: '03_leaf.md', status: 'Completed', scope: '' },
        { number: 'L4', name: 'Leaf four', file: '04_leaf.md', status: 'abandoned', scope: '' },
      ],
      discardedSubTasks: [
        {
          number: 'L5',
          name: 'Never started',
          file: '05_never_started.md',
          scope: 'retired planning work',
          disposition: 'discard-unstarted',
          reason: 'No implementation was needed',
          discardedAt: '2026-08-24T12:00:00+00:00',
          proof: {
            childJson: { state: 'missing' },
            childMarkdown: { state: 'missing' },
            version: 'task-unstarted-evidence/v1',
            taskDocumentRef: { repository: 'repo-a', path: 'm/05_never_started.json' },
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
    });
  }

  function leafOne() {
    return taskDoc({
      id: 'L1',
      kind: 'subTask',
      docPath: LEAF,
      title: 'Leaf one',
      status: 'inProgress',
      steps: [
        {
          id: 'S1',
          title: 'Done',
          status: 'done',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
        {
          id: 'S2',
          title: 'Open',
          status: 'pending',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
      ],
      stepsDone: 1,
      stepsTotal: 2,
      reviewState: {
        round: 2,
        pending: false,
        additionalRounds: 0,
        findings: [
          { findingId: 'F1', description: 'Open.', open: true, descriptionTruncated: false },
          { findingId: 'F2', description: 'Closed.', open: false, descriptionTruncated: false },
        ],
        totalFindings: 2,
        openFindings: 1,
      },
    });
  }

  it('draws every block in the fixed order on a document that has everything', () => {
    seedTaskDocuments([masterFull(), leafOne()]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${MASTER}`} />);

    expect(getByTestId('doc-form').textContent).toBe('master');
    expect(blockOrder(container, 'skeleton-master')).toEqual([
      'head',
      'objective',
      'now',
      'tasks',
      'requirements',
      'decisions',
      'further',
    ]);
    // the tasks in work stand under Now with their steps and review round
    expect(getByTestId('now-split').textContent).toContain('1 in work');
    expect(getByTestId('now-steps-1').textContent).toBe('1/2');
    expect(getByTestId('now-review-1').textContent).toBe('round 2 · 1 of 2 findings open');
  });

  it('draws head alone on a bare document: empty Now and Tasks stay out', () => {
    seedTaskDocuments([taskDoc({ kind: 'master', docPath: MASTER, title: 'Bare master' })]);
    const { container, getByTestId, queryByTestId } = render(<DetailPanel selectedId={`taskdoc:${MASTER}`} />);

    expect(getByTestId('doc-form').textContent).toBe('master');
    expect(blockOrder(container, 'skeleton-master')).toEqual(['head']);
    expect(queryByTestId('now-split')).toBeNull();
  });

  it('keeps Tasks with introducing prose but no rows', () => {
    seedTaskDocuments([
      taskDoc({
        kind: 'master',
        docPath: MASTER,
        title: 'Intro master',
        sections: [{ kind: 'subTasks', heading: 'Tasks', body: 'How this master tracks work.' }],
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${MASTER}`} />);

    expect(blockOrder(container, 'skeleton-master')).toEqual(['head', 'tasks']);
    expect(getByTestId('tasks-intro').textContent).toContain('How this master tracks work.');
  });
});

describe('sprint skeleton order (rule 3)', () => {
  const SPRINT = '/tasks/repo-a/sprint/task.json';

  function commanded(id: string, folder: string, status: string, rows: number) {
    return taskDoc({
      id,
      kind: 'master',
      docPath: `/tasks/repo-a/${folder}/task.json`,
      title: `Master ${id}`,
      status,
      form: 'master',
      subTasks: Array.from({ length: rows }, (_, index) => ({
        number: `L${index + 1}`,
        name: `Leaf ${index + 1}`,
        file: `0${index + 1}_leaf.md`,
        status: index === 0 ? 'Completed' : 'inProgress',
        scope: '',
      })),
    });
  }

  function sprintFull() {
    return taskDoc({
      id: 'S',
      kind: 'master',
      docPath: SPRINT,
      title: 'Sprint',
      status: 'inProgress',
      form: 'sprint',
      type: 'Orchestration',
      integrationBranch: 'ar/sprint',
      statusNote: 'Sprint status note.',
      headerNotes: [{ label: 'Portfolio gate', value: 'Gate.' }],
      objective: 'Sprint objective.',
      requirements: ['The masters land in the order of the graph.'],
      orchestrates: ['master-a', 'master-b', 'master-c'],
      executionGraphView: {
        nodes: [
          {
            nodeId: 'repo-a/master-a/task.json',
            kind: 'lump',
            masterRef: { repository: 'repo-a', path: 'master-a/task.json' },
            masterTitle: 'Master A',
            leafIds: [],
            leafTitles: [],
            waveIndex: 0,
            frontierState: 'in-flight',
            predecessors: [],
          },
        ],
      },
      decisions: [
        { at: '2026-10-01', decision: 'Decided.', rationale: 'Because.', product_record: false },
      ],
      references: ['notes/s.md'],
      sections: [
        {
          kind: 'freeform',
          heading: 'Judgment Register',
          body: '| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |',
        },
        { kind: 'freeform', heading: 'Extra', body: 'Extra body.' },
      ],
      subTasks: [
        {
          number: 'M1',
          name: 'Master A',
          file: '',
          status: 'planning',
          scope: '',
          masterRef: { repository: 'repo-a', path: 'master-a/task.json' },
        },
        {
          number: 'M2',
          name: 'Master B',
          file: '',
          status: 'planning',
          scope: '',
          masterRef: { repository: 'repo-a', path: 'master-b/task.json' },
        },
        {
          number: 'M3',
          name: 'Master C',
          file: '',
          status: 'planning',
          scope: '',
          masterRef: { repository: 'repo-a', path: 'master-c/task.json' },
        },
      ],
    });
  }

  it('draws every block in the fixed order on a document that has everything', () => {
    seedTaskDocuments([
      sprintFull(),
      commanded('MA', 'master-a', 'inProgress', 2),
      commanded('MB', 'master-b', 'planning', 1),
      commanded('MC', 'master-c', 'Completed', 1),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${SPRINT}`} />);

    expect(getByTestId('doc-form').textContent).toBe('sprint');
    expect(blockOrder(container, 'skeleton-sprint')).toEqual([
      'head',
      'objective',
      'now',
      'order',
      'masters',
      'requirements',
      'registers',
      'decisions',
      'further',
    ]);
    // the masters in work show how many of their tasks are completed
    expect(getByTestId('now-split').textContent).toContain('1 in work');
    expect(getByTestId('now-progress-1').textContent).toBe('1/2');
  });

  it('draws head alone on a bare document: empty Now and Masters stay out', () => {
    seedTaskDocuments([
      taskDoc({
        kind: 'master',
        docPath: SPRINT,
        title: 'Bare sprint',
        form: 'sprint',
        orchestrates: ['master-a'],
      }),
    ]);
    const { container, getByTestId, queryByTestId } = render(<DetailPanel selectedId={`taskdoc:${SPRINT}`} />);

    expect(getByTestId('doc-form').textContent).toBe('sprint');
    expect(blockOrder(container, 'skeleton-sprint')).toEqual(['head']);
    expect(queryByTestId('now-split')).toBeNull();
  });
});

describe('objective after the rendered series line (MIK-R128 rule 15, evidence 12)', () => {
  it.each(['sprint', 'master', 'task'] as const)(
    'orders actual reader chrome before the objective on %s',
    (form) => {
      const doc = taskDoc({
        id: form === 'task' ? 'L1' : 'M',
        kind: form === 'task' ? 'subTask' : 'master',
        form,
        docPath: form === 'task' ? LEAF : MASTER,
        title: form,
        status: 'inProgress',
        objective: 'The objective.',
        directGrant:
          form === 'task'
            ? { grantedBy: 'developer', grantedAt: '2026-10-01T10:00:00Z' }
            : undefined,
        steps:
          form === 'task'
            ? [
                {
                  id: 'S1',
                  title: 'Work',
                  status: 'pending',
                  substeps: [],
                  noteTruncated: false,
                  outcomeTruncated: false,
                },
              ]
            : [],
        subTasks:
          form !== 'task'
            ? [{ number: 'L1', name: 'Work', file: '01_leaf.md', status: 'inProgress', scope: '' }]
            : [],
      });
      const view = render(
        <TaskContent
          docs={[doc]}
          bodyState="unavailable"
          onOpen={vi.fn()}
          onJump={vi.fn()}
          onOpenChangeSet={vi.fn()}
        />,
      );
      const objective = view.getByTestId('objective-lead');
      const seriesLine = view.getAllByTestId('open-changeset')[0];
      expect(seriesLine.textContent).toContain(form === 'task' ? 'committed' : 'series');
      expect(before(seriesLine, objective)).toBe(true);
      expect(
        before(
          view.getByText(
            'Full task document details are unavailable; showing the available summary.',
          ),
          objective,
        ),
      ).toBe(true);
      if (form === 'task')
        expect(before(view.getByTestId('taskdoc-direct-line'), objective)).toBe(true);
      const following = view.container.querySelector(
        `[data-block="${form === 'task' ? 'steps' : 'now'}"]`,
      );
      if (!following) throw new Error('missing following block');
      expect(before(objective, following)).toBe(true);
      for (const className of block.split(' '))
        expect(objective.classList.contains(className)).toBe(true);
    },
  );

  it.each(['sprint', 'master', 'task'] as const)(
    'uses the shared titled-block spacing when %s has no series line',
    (form) => {
      const doc = taskDoc({
        kind: form === 'task' ? 'subTask' : 'master',
        form,
        docPath: form === 'task' ? LEAF : MASTER,
        objective: 'An objective.',
      });
      const view = render(
        <TaskContent docs={[doc]} bodyState="loading" onOpen={vi.fn()} onJump={vi.fn()} />,
      );
      expect(view.queryByTestId('open-changeset')).toBeNull();
      const objective = view.getByTestId('objective-lead');
      expect(before(view.getByTestId('doc-head'), objective)).toBe(true);
      for (const className of block.split(' '))
        expect(objective.classList.contains(className)).toBe(true);
    },
  );

  it.each(['master', 'task'] as const)(
    'places structured prose without replacing old notes on %s',
    (form) => {
      const doc = taskDoc({
        kind: form === 'task' ? 'subTask' : 'master',
        form,
        docPath: form === 'task' ? LEAF : MASTER,
        objective: 'An objective.',
        nonGoals: 'Not **in scope**.',
        design: 'A design.',
        acceptance: 'Evidence **required**.',
        sections: [
          { kind: 'freeform', heading: 'Historic acceptance', body: 'Preserved old prose.' },
        ],
      });
      const view = render(
        <TaskContent docs={[doc]} bodyState="unavailable" onOpen={vi.fn()} onJump={vi.fn()} />,
      );
      expect(blockOrder(view.container, `skeleton-${form}`)).toEqual([
        'head',
        'objective',
        'nonGoals',
        'design',
        'acceptance',
        'further',
      ]);
      expect(view.getByText('in scope').tagName).toBe('STRONG');
      expect(view.getByText('required').tagName).toBe('STRONG');
      expect(view.getByText('Historic acceptance')).toBeTruthy();
    },
  );

  it('keeps long structured prose reachable without another reader route', () => {
    const text = Array.from({ length: 12 }, (_, index) => `Paragraph ${index}.`).join('\n\n');
    const doc = taskDoc({ kind: 'subTask', docPath: LEAF, nonGoals: text, acceptance: text });
    const view = render(
      <TaskContent docs={[doc]} bodyState="unavailable" onOpen={vi.fn()} onJump={vi.fn()} />,
    );
    expect(view.queryAllByText('Paragraph 11.')).toHaveLength(0);
    fireEvent.click(view.getByTestId('nonGoals-toggle'));
    fireEvent.click(view.getByTestId('acceptance-toggle'));
    expect(view.getAllByText('Paragraph 11.')).toHaveLength(2);
  });
  function before(first: Element, second: Element): boolean {
    return (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
  }

  function headings(container: HTMLElement, skeleton: string): string[] {
    const root = container.querySelector(`[data-testid="${skeleton}"]`);
    if (!root) throw new Error(`skeleton not rendered: ${skeleton}`);
    return Array.from(root.querySelectorAll('h2, h3')).map((el) => el.textContent ?? '');
  }

  // The last element of the head stands before the objective, the objective
  // before Now or Steps: read from the rendered page, one test per form.
  it('stands after the head and before Steps on a task page', () => {
    seedTaskDocuments([
      taskDoc({
        id: 'L1',
        kind: 'subTask',
        docPath: LEAF,
        title: 'Leaf one',
        status: 'inProgress',
        statusNote: 'Leaf status note.',
        headerNotes: [{ label: 'Stage', value: 'Run 3, a long enough line for a note of its own.' }],
        objective: 'Leaf objective.',
        steps: [
          {
            id: 'S1',
            title: 'Done step',
            status: 'done',
            substeps: [],
            noteTruncated: false,
            outcomeTruncated: false,
          },
        ],
        stepsDone: 1,
        stepsTotal: 1,
        requirements: ['Requirement one.'],
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    const head = getByTestId('doc-head');
    const objective = getByTestId('objective-lead');
    const steps = container.querySelector('[data-block="steps"]');
    if (!head.lastElementChild || !steps) throw new Error('task page missing head end or steps');
    expect(head.contains(objective)).toBe(false);
    expect(before(head.lastElementChild, objective)).toBe(true);
    expect(before(objective, steps)).toBe(true);
    expect(headings(container, 'skeleton-task')).toEqual([
      'Leaf one',
      'Objective',
      'Steps',
      'Requirements',
    ]);
  });

  it('stands after the head and before Now on a master page', () => {
    seedTaskDocuments([
      taskDoc({
        id: 'M',
        kind: 'master',
        docPath: MASTER,
        title: 'Master',
        status: 'inProgress',
        form: 'master',
        statusNote: 'Master status note.',
        headerNotes: [{ label: 'Authority', value: 'D1, a long enough line for a note of its own.' }],
        objective: 'Master objective.',
        subTasks: [
          { number: 'L1', name: 'Leaf one', file: '01_leaf.md', status: 'inProgress', scope: '' },
        ],
        requirements: ['Master requirement.'],
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${MASTER}`} />);

    const head = getByTestId('doc-head');
    const objective = getByTestId('objective-lead');
    const now = container.querySelector('[data-block="now"]');
    if (!head.lastElementChild || !now) throw new Error('master page missing head end or now');
    expect(head.contains(objective)).toBe(false);
    expect(before(head.lastElementChild, objective)).toBe(true);
    expect(before(objective, now)).toBe(true);
    expect(headings(container, 'skeleton-master')).toEqual([
      'Master',
      'Objective',
      'Now',
      'Tasks',
      'Requirements',
    ]);
  });

  it('stands after the head and before Now on a sprint page', () => {
    const sprintPath = '/tasks/repo-a/sprint/task.json';
    seedTaskDocuments([
      taskDoc({
        id: 'S',
        kind: 'master',
        docPath: sprintPath,
        title: 'Sprint',
        status: 'inProgress',
        form: 'sprint',
        statusNote: 'Sprint status note.',
        headerNotes: [{ label: 'Portfolio gate', value: 'Gate, a long enough line for a note of its own.' }],
        objective: 'Sprint objective.',
        orchestrates: ['master-a'],
        subTasks: [
          {
            number: 'M1',
            name: 'Master A',
            file: '',
            status: 'inProgress',
            scope: '',
            masterRef: { repository: 'repo-a', path: 'master-a/task.json' },
          },
        ],
        requirements: ['Sprint requirement.'],
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${sprintPath}`} />);

    const head = getByTestId('doc-head');
    const objective = getByTestId('objective-lead');
    const now = container.querySelector('[data-block="now"]');
    if (!head.lastElementChild || !now) throw new Error('sprint page missing head end or now');
    expect(head.contains(objective)).toBe(false);
    expect(before(head.lastElementChild, objective)).toBe(true);
    expect(before(objective, now)).toBe(true);
    expect(headings(container, 'skeleton-sprint')).toEqual([
      'Sprint',
      'Objective',
      'Now',
      'Masters',
      'Requirements',
    ]);
  });
});
