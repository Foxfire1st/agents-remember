// MIK-R127 evidence 1: per form, the order of the blocks on a document that
// has everything, and on a document that has only the required fields. The
// objective is the first block under the title on every form (D219): it stands
// inside the head, above the facts, and nowhere further down.
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedTaskDocuments, taskDoc } from './test-utils';

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
    const { container, getByTestId, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${MASTER}`} />,
    );

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
    const { container, getByTestId, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${SPRINT}`} />,
    );

    expect(getByTestId('doc-form').textContent).toBe('sprint');
    expect(blockOrder(container, 'skeleton-sprint')).toEqual(['head']);
    expect(queryByTestId('now-split')).toBeNull();
  });
});
