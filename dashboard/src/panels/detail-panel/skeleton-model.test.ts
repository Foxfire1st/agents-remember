// MIK-R127 skeleton model: form, groups, tables, further notes, chain.
import { describe, expect, it } from 'vitest';

import type { SeriesNode, SubTaskRow, TaskDocNode } from '../../types/projection';
import {
  docChain,
  docForm,
  firstOpenStepId,
  furtherSections,
  groupSubTaskRows,
  masterTaskCounts,
  parseMarkdownTable,
  registerTables,
  reviewSummary,
  sectionIntro,
  sprintRowStatus,
} from './model';
import { taskDoc } from './test-utils';

function row(over: Partial<SubTaskRow> & Pick<SubTaskRow, 'number' | 'name'>): SubTaskRow {
  return { file: '', status: 'planning', scope: '', ...over };
}

describe('docForm (rule 1)', () => {
  it("shows the projection's own word when it carries one", () => {
    expect(docForm(taskDoc({ kind: 'master', docPath: '/t/s/task.json', form: 'sprint' }))).toBe(
      'sprint',
    );
    expect(docForm(taskDoc({ kind: 'master', docPath: '/t/m/task.json', form: 'master' }))).toBe(
      'master',
    );
    expect(docForm(taskDoc({ kind: 'subTask', docPath: '/t/m/01_a.json', form: 'task' }))).toBe(
      'task',
    );
  });

  it("derives the form by the model's rule when an older cache lacks it", () => {
    const aged = (doc: TaskDocNode): { kind: string; orchestrates: string[] } => ({
      kind: doc.kind,
      orchestrates: doc.orchestrates,
    });
    expect(
      docForm({
        ...aged(taskDoc({ kind: 'master', docPath: '/t/s/task.json' })),
        orchestrates: ['m'],
      }),
    ).toBe('sprint');
    expect(docForm(aged(taskDoc({ kind: 'master', docPath: '/t/m/task.json' })))).toBe('master');
    expect(docForm(aged(taskDoc({ kind: 'subTask', docPath: '/t/m/01_a.json' })))).toBe('task');
    expect(docForm(aged(taskDoc({ kind: 'light', docPath: '/t/l.json' })))).toBe('task');
  });
});

describe('groupSubTaskRows', () => {
  it('groups by declared status and never hides an unknown one', () => {
    const groups = groupSubTaskRows([
      row({ number: '1', name: 'work', status: 'inProgress' }),
      row({ number: '2', name: 'wait', status: 'planning' }),
      row({ number: '3', name: 'done', status: 'Completed' }),
      row({ number: '4', name: 'gone', status: 'abandoned' }),
      row({ number: '5', name: 'strange', status: 'mystery' }),
    ]);
    expect(groups.work.map((entry) => entry.number)).toEqual(['1']);
    expect(groups.waiting.map((entry) => entry.number)).toEqual(['2', '5']);
    expect(groups.done.map((entry) => entry.number)).toEqual(['3']);
    expect(groups.gone.map((entry) => entry.number)).toEqual(['4']);
  });
});

describe('masterTaskCounts', () => {
  it('counts completed over live rows, excluding abandoned', () => {
    expect(
      masterTaskCounts(
        taskDoc({
          kind: 'master',
          docPath: '/t/m/task.json',
          subTasks: [
            row({ number: '1', name: 'a', status: 'Completed' }),
            row({ number: '2', name: 'b', status: 'inProgress' }),
            row({ number: '3', name: 'c', status: 'abandoned' }),
          ],
        }),
      ),
    ).toEqual({ done: 1, total: 2 });
  });
});

describe('firstOpenStepId and reviewSummary', () => {
  it('marks the first step that is not done', () => {
    expect(
      firstOpenStepId([
        {
          id: 'S1',
          title: 'done',
          status: 'done',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
        {
          id: 'S2',
          title: 'open',
          status: 'pending',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
      ]),
    ).toBe('S2');
    expect(
      firstOpenStepId([
        {
          id: 'S1',
          title: 'done',
          status: 'done',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
      ]),
    ).toBeUndefined();
  });

  it('names the round and the open findings, or null without a review', () => {
    expect(reviewSummary(undefined)).toBeNull();
    expect(
      reviewSummary({
        round: 4,
        pending: false,
        additionalRounds: 0,
        findings: [],
        totalFindings: 0,
        openFindings: 0,
      }),
    ).toBe('round 4');
    expect(
      reviewSummary({
        round: 4,
        pending: true,
        additionalRounds: 0,
        findings: [
          { findingId: 'F1', description: 'open', open: true, descriptionTruncated: false },
          { findingId: 'F2', description: 'closed', open: false, descriptionTruncated: false },
        ],
        totalFindings: 2,
        openFindings: 1,
      }),
    ).toBe('round 4, 1 of 2 findings open');
  });
});

describe('parseMarkdownTable and registerTables', () => {
  it('parses the first table with its separator', () => {
    const table = parseMarkdownTable(
      'Intro prose.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n\nAfter.',
    );
    expect(table).toEqual({
      cols: ['A', 'B'],
      rows: [
        ['1', '2'],
        ['3', '4'],
      ],
    });
  });

  it('parses a table without a separator and returns null without one', () => {
    expect(parseMarkdownTable('No pipes here.\nJust prose.')).toBeNull();
    expect(parseMarkdownTable('| A | B |\n| 1 | 2 |')).toEqual({
      cols: ['A', 'B'],
      rows: [['1', '2']],
    });
  });

  it('treats only register-headed sections with a table as registers', () => {
    const sections = [
      { kind: 'freeform', heading: 'Judgment Register', body: '| A |\n| --- |\n| 1 |' },
      { kind: 'freeform', heading: 'Prose register', body: 'No table here.' },
      { kind: 'freeform', heading: 'Random table', body: '| A |\n| --- |\n| 1 |' },
    ];
    const registers = registerTables(sections);
    expect(registers.map((entry) => entry.heading)).toEqual(['Judgment Register']);
    expect(registers[0]).toEqual({ heading: 'Judgment Register', cols: ['A'], rows: [['1']] });
  });

  it('reads the section intro of a structured block', () => {
    expect(
      sectionIntro(
        [
          { kind: 'subTasks', heading: 'Sub-tasks', body: 'Intro text.' },
          { kind: 'freeform', heading: 'Other', body: 'Body.' },
        ],
        'subTasks',
      ),
    ).toBe('Intro text.');
    expect(
      sectionIntro([{ kind: 'subTasks', heading: 'Sub-tasks', body: '' }], 'subTasks'),
    ).toBeUndefined();
  });
});

describe('furtherSections (rules 7-8)', () => {
  it('lists every free-form section except blocks, registers and objective repeats', () => {
    const doc = taskDoc({
      kind: 'master',
      docPath: '/t/m/task.json',
      objective: 'Same text.',
      sections: [
        { kind: 'subTasks', heading: 'Sub-tasks', body: 'Intro.' },
        { kind: 'sharedDecisions', heading: 'Shared', body: 'Intro.' },
        { kind: 'freeform', heading: 'Judgment Register', body: '| A |\n| --- |\n| 1 |' },
        { kind: 'freeform', heading: 'Echo', body: 'Same text.' },
        { kind: 'freeform', heading: 'Other', body: 'Body with | table |\n| --- |\n| x |' },
      ],
    });
    const registers = registerTables(doc.sections);
    const further = furtherSections(doc, registers);
    expect(further.map((entry) => entry.heading)).toEqual(['Other']);
    expect(further[0].chars).toBe('Body with | table |\n| --- |\n| x |'.length);
    expect(further[0].tables).toBe(1);
    expect(further[0].rows).toBe(1);
  });
});

describe('docChain (rule 2)', () => {
  const sprint = taskDoc({
    id: 'SPRINT',
    kind: 'master',
    docPath: '/tasks/repo-a/sprint/task.json',
    title: 'Sprint',
    orchestrates: ['master-a'],
    form: 'sprint',
  });
  const master = taskDoc({
    id: 'MASTER-A',
    kind: 'master',
    docPath: '/tasks/repo-a/master-a/task.json',
    title: 'Master A',
    form: 'master',
  });
  const leaf = taskDoc({
    id: 'L1',
    kind: 'subTask',
    docPath: '/tasks/repo-a/master-a/01_leaf.json',
    title: 'Leaf',
  });
  const series: SeriesNode[] = [];

  it('chains a task through its master to the commanding sprint', () => {
    expect(docChain(leaf, [sprint, master, leaf], series)).toEqual([
      {
        form: 'sprint',
        text: 'SPRINT',
        title: 'Sprint',
        targetKey: 'taskdoc:/tasks/repo-a/sprint/task.json',
      },
      {
        form: 'master',
        text: 'MASTER-A',
        title: 'Master A',
        targetKey: 'taskdoc:/tasks/repo-a/master-a/task.json',
      },
    ]);
  });

  it('chains a master to its sprint and leaves a sprint alone', () => {
    expect(docChain(master, [sprint, master], series)).toEqual([
      {
        form: 'sprint',
        text: 'SPRINT',
        title: 'Sprint',
        targetKey: 'taskdoc:/tasks/repo-a/sprint/task.json',
      },
    ]);
    expect(docChain(sprint, [sprint], series)).toEqual([]);
    expect(docChain(master, [master], series)).toEqual([]);
  });

  it('falls back to the series link when no master document resolves', () => {
    const orphan = taskDoc({
      id: 'L9',
      kind: 'subTask',
      docPath: '/tasks/repo-a/nodoc/09_leaf.json',
      title: 'Orphan',
    });
    expect(docChain(orphan, [orphan], series)).toEqual([]);
  });
});

describe('sprintRowStatus (open truth gap 3)', () => {
  it("prefers the commanded master's own document over the row as written", () => {
    const ref = row({ number: '1', name: 'Master', status: 'planning' });
    expect(sprintRowStatus(ref, undefined)).toBe('planning');
    expect(
      sprintRowStatus(
        ref,
        taskDoc({ kind: 'master', docPath: '/t/m/task.json', status: 'inProgress' }),
      ),
    ).toBe('inProgress');
  });
});
