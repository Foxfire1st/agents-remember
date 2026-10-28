// MIK-R127 evidence 1: per form, the order of the blocks on a document that
// has everything, and on a document that has only the required fields. The
// objective follows the complete head and actual series line on every form
// (MIK-R128 rule 15), before Now or Steps.
import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { TaskEntry, TaskSummary } from '../../types/projection';
import { DetailPanel } from './DetailPanel';
import { TaskReader } from './taskReader';
import { block } from './pageStyles';
import { seedTaskEntries, taskEntry, taskSummary, taskReference, taskTree } from './test-utils';

function blockOrder(container: HTMLElement, skeleton: string): string[] {
  const root = container.querySelector(`[data-testid="${skeleton}"]`);
  if (!root) throw new Error(`skeleton not rendered: ${skeleton}`);
  return [...root.querySelectorAll('[data-block]')].map(
    (element) => element.getAttribute('data-block') ?? '',
  );
}

const LEAF = 'repo-a/m/01_leaf.json';
const MASTER = 'repo-a/m/task.json';

function leafFull() {
  return taskEntry({
    reference: taskReference(LEAF),
    id: 'L1',
    title: 'Leaf one',
    stage: 'started',
    activity: 'works',
    stepsDone: 1,
    stepsTotal: 2,
    summary: taskSummary({
      createdAt: '2026-06-20T09:00:00+00:00',
      form: 'task',
      kind: 'subTask',
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
      reviewState: {
        round: 1,
        pending: false,
        additionalRounds: 0,
        findings: [
          {
            findingId: 'F1',
            description: 'Open finding.',
            open: true,
            descriptionTruncated: false,
          },
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
    }),
  });
}

function masterDoc() {
  return taskEntry({
    stepsDone: 0,
    stepsTotal: 0,
    reference: taskReference(MASTER),
    id: 'M',
    title: 'Master',
    stage: 'started',
    activity: 'works',
    summary: taskSummary({
      objective: '',
      createdAt: '2026-06-20T09:00:00+00:00',
      form: 'master',
      kind: 'master',
      type: 'Feature',
      executionNature: 'atomic',
      statusNote: 'Master status note.',
      headerNotes: [{ label: 'Authority', value: 'D1' }],
    }),
  });
}

describe('task skeleton order (rule 5)', () => {
  it('draws every block in the fixed order on a document that has everything', () => {
    seedTaskEntries([masterDoc(), leafFull()]);
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
    seedTaskEntries([
      taskEntry({
        id: '1',
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(LEAF),
        title: 'Bare leaf',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'task',
          kind: 'subTask',
        }),
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    expect(getByTestId('doc-form').textContent).toBe('task');
    expect(blockOrder(container, 'skeleton-task')).toEqual(['head']);
  });
});

describe('master skeleton order (rule 4)', () => {
  function masterFull() {
    return taskEntry({
      stepsDone: 0,
      stepsTotal: 0,
      reference: taskReference(MASTER),
      id: 'M',
      title: 'Master',
      stage: 'started',
      activity: 'works',
      summary: taskSummary({
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'master',
        kind: 'master',
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
      }),
    });
  }

  function leafOne() {
    return taskEntry({
      reference: taskReference(LEAF),
      id: 'L1',
      title: 'Leaf one',
      stage: 'started',
      activity: 'works',
      stepsDone: 1,
      stepsTotal: 2,
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
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
      }),
    });
  }

  it('draws every block in the fixed order on a document that has everything', () => {
    seedTaskEntries(
      taskTree(masterFull(), [
        leafOne(),
        taskEntry({
          stepsDone: 0,
          stepsTotal: 0,
          reference: taskReference('repo-a/m/02_leaf.json'),
          id: 'L2',
          title: 'Leaf two',
          stage: 'planned',
          summary: undefined,
        }),
        taskEntry({
          stepsDone: 0,
          stepsTotal: 0,
          reference: taskReference('repo-a/m/03_leaf.json'),
          id: 'L3',
          title: 'Leaf three',
          stage: 'completed',
          summary: undefined,
        }),
        taskEntry({
          stepsDone: 0,
          stepsTotal: 0,
          reference: taskReference('repo-a/m/04_leaf.json'),
          id: 'L4',
          title: 'Leaf four',
          stage: 'aborted',
          summary: undefined,
        }),
      ]),
    );
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
    expect(getByTestId('now-split').textContent).toContain('1 started');
    expect(getByTestId('now-steps-1').textContent).toBe('1/2');
    expect(getByTestId('now-review-1').textContent).toBe('round 2 · 1 of 2 findings open');
  });

  it('draws head alone on a bare document: empty Now and Tasks stay out', () => {
    seedTaskEntries([
      taskEntry({
        id: '1',
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(MASTER),
        title: 'Bare master',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'master',
          kind: 'master',
        }),
      }),
    ]);
    const { container, getByTestId, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${MASTER}`} />,
    );

    expect(getByTestId('doc-form').textContent).toBe('master');
    expect(blockOrder(container, 'skeleton-master')).toEqual(['head']);
    expect(queryByTestId('now-split')).toBeNull();
  });

  it('keeps Tasks with introducing prose but no rows', () => {
    seedTaskEntries([
      taskEntry({
        id: '1',
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(MASTER),
        title: 'Intro master',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'master',
          kind: 'master',
          sections: [{ kind: 'subTasks', heading: 'Tasks', body: 'How this master tracks work.' }],
        }),
      }),
    ]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${MASTER}`} />);

    expect(blockOrder(container, 'skeleton-master')).toEqual(['head', 'tasks']);
    expect(getByTestId('tasks-intro').textContent).toContain('How this master tracks work.');
  });
});

describe('sprint skeleton order (rule 3)', () => {
  const SPRINT = 'repo-a/sprint/task.json';

  function commanded(id: string, folder: string, stage: TaskEntry['stage'], rows: number) {
    return taskTree(
      taskEntry({
        stepsDone: 0,
        stepsTotal: 0,
        reference: taskReference(`repo-a/${folder}/task.json`),
        id,
        title: `Master ${id}`,
        stage,
        summary: taskSummary({ form: 'master', kind: 'master', objective: '' }),
      }),
      Array.from({ length: rows }, (_, index) =>
        taskEntry({
          stepsDone: 0,
          stepsTotal: 0,
          reference: taskReference(`repo-a/${folder}/0${index + 1}_leaf.json`),
          id: `L${index + 1}`,
          title: `Leaf ${index + 1}`,
          stage: index === 0 ? 'completed' : 'started',
          activity: index === 0 ? undefined : 'works',
          summary: undefined,
        }),
      ),
    );
  }

  function sprintFull() {
    return taskEntry({
      stepsDone: 0,
      stepsTotal: 0,
      reference: taskReference(SPRINT),
      id: 'S',
      title: 'Sprint',
      stage: 'started',
      activity: 'works',
      summary: taskSummary({
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'sprint',
        kind: 'master',
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
      }),
    });
  }

  it('draws every block in the fixed order on a document that has everything', () => {
    const a = commanded('MA', 'master-a', 'started', 2);
    const b = commanded('MB', 'master-b', 'planned', 1);
    const c = commanded('MC', 'master-c', 'completed', 1);
    seedTaskEntries([
      ...taskTree(sprintFull(), [a[0], b[0], c[0]]),
      ...a.slice(1),
      ...b.slice(1),
      ...c.slice(1),
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
    expect(getByTestId('now-split').textContent).toContain('1 started');
    expect(getByTestId('now-progress-1').textContent).toBe('1/2');
  });

  it('draws head alone on a bare document: empty Now and Masters stay out', () => {
    seedTaskEntries([
      taskEntry({
        id: '1',
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(SPRINT),
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
    const { container, getByTestId, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${SPRINT}`} />,
    );

    expect(getByTestId('doc-form').textContent).toBe('sprint');
    expect(blockOrder(container, 'skeleton-sprint')).toEqual(['head']);
    expect(queryByTestId('now-split')).toBeNull();
  });
});

describe('objective after the rendered series line (MIK-R128 rule 15, evidence 12)', () => {
  function pageFor(
    form: 'sprint' | 'master' | 'task',
    summary: Partial<TaskSummary>,
    state: 'loading' | 'unavailable' = 'unavailable',
    children = false,
  ) {
    const entry = taskEntry({
      id: form === 'task' ? 'L1' : 'M',
      reference: taskReference(form === 'task' ? LEAF : MASTER),
      summary: taskSummary({ kind: form === 'task' ? 'subTask' : 'master', form, ...summary }),
    });
    const list = children
      ? taskTree(entry, [
          taskEntry({ reference: taskReference('repo-a/m/child.json'), stage: 'started' }),
        ])
      : [entry];
    const tasks = Object.fromEntries(
      list.map((item) => [`${item.key}`, item]),
    );
    return render(
      <TaskReader
        entry={list[0]}
        doc={entry.summary!}
        tasks={tasks}
        bodyState={state}
        onJump={vi.fn()}
        onOpenChangeSet={vi.fn()}
      />,
    );
  }
  it.each(['sprint', 'master', 'task'] as const)(
    'orders actual reader chrome before the objective on %s',
    (form) => {
      const view = pageFor(
        form,
        {
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
        },
        'unavailable',
        form !== 'task',
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
    'keeps %s summary spacing while its document body loads',
    (form) => {
      const view = pageFor(form, { objective: 'An objective.' }, 'loading');
      if (form === 'master') expect(view.getByTestId('open-changeset').textContent).toContain('series');
      else expect(view.queryByTestId('open-changeset')).toBeNull();
      const objective = view.getByTestId('objective-lead');
      expect(before(view.getByTestId('doc-head'), objective)).toBe(true);
      for (const className of block.split(' '))
        expect(objective.classList.contains(className)).toBe(true);
    },
  );
  it.each(['master', 'task'] as const)(
    'places structured prose without replacing old notes on %s',
    (form) => {
      const view = pageFor(form, {
        objective: 'An objective.',
        nonGoals: 'Not **in scope**.',
        design: 'A design.',
        acceptance: 'Evidence **required**.',
        sections: [
          { kind: 'freeform', heading: 'Historic acceptance', body: 'Preserved old prose.' },
        ],
      });
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
    const view = pageFor('task', { nonGoals: text, acceptance: text });
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
    seedTaskEntries([
      taskEntry({
        reference: taskReference(LEAF),
        id: 'L1',
        title: 'Leaf one',
        stage: 'started',
        activity: 'works',
        stepsDone: 1,
        stepsTotal: 1,
        summary: taskSummary({
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'task',
          kind: 'subTask',
          statusNote: 'Leaf status note.',
          headerNotes: [
            { label: 'Stage', value: 'Run 3, a long enough line for a note of its own.' },
          ],
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
          requirements: ['Requirement one.'],
        }),
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
    seedTaskEntries(
      taskTree(
        taskEntry({
          stepsDone: 0,
          stepsTotal: 0,
          reference: taskReference(MASTER),
          id: 'M',
          title: 'Master',
          stage: 'started',
          activity: 'works',
          summary: taskSummary({
            type: '',
            createdAt: '2026-06-20T09:00:00+00:00',
            form: 'master',
            kind: 'master',
            statusNote: 'Master status note.',
            headerNotes: [
              { label: 'Authority', value: 'D1, a long enough line for a note of its own.' },
            ],
            objective: 'Master objective.',
            requirements: ['Master requirement.'],
          }),
        }),
        [
          taskEntry({
            stepsDone: 0,
            stepsTotal: 0,
            reference: taskReference('repo-a/m/01_leaf.json'),
            id: '1',
            title: 'Leaf one',
            stage: 'started',
            activity: 'works',
            summary: undefined,
          }),
        ],
      ),
    );
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
    const sprintPath = 'repo-a/sprint/task.json';
    seedTaskEntries(
      taskTree(
        taskEntry({
          stepsDone: 0,
          stepsTotal: 0,
          reference: taskReference(sprintPath),
          id: 'S',
          title: 'Sprint',
          stage: 'started',
          activity: 'works',
          summary: taskSummary({
            type: '',
            createdAt: '2026-06-20T09:00:00+00:00',
            form: 'sprint',
            kind: 'master',
            statusNote: 'Sprint status note.',
            headerNotes: [
              { label: 'Portfolio gate', value: 'Gate, a long enough line for a note of its own.' },
            ],
            objective: 'Sprint objective.',
            orchestrates: ['master-a'],
            requirements: ['Sprint requirement.'],
          }),
        }),
        [
          taskEntry({
            stepsDone: 0,
            stepsTotal: 0,
            reference: taskReference('repo-a/master-a/task.json'),
            id: '1',
            title: 'Master A',
            stage: 'started',
            activity: 'works',
            summary: undefined,
          }),
        ],
      ),
    );
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
