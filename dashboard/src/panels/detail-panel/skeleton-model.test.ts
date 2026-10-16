// MIK-R127 skeleton model: form, groups, tables, further notes, chain.
import { describe,expect,it } from 'vitest';

import { taskEntrySelectionKey } from '../../data/taskReference';
import { chatLeaf,chatMaster,chatSprint } from '../../test/fixtures/documentChat';
import {
docChain,
docForm,
firstOpenStepId,
furtherSections,
parseMarkdownTable,
registerTables,
reviewSummary,
sectionIntro,
} from './model';
import { taskSummary } from './test-utils';

describe('docForm (rule 1)', () => {
  it.each(['sprint', 'master', 'task'] as const)("shows the projection's %s form", (form) => {
    expect(docForm(taskSummary({ form }))).toBe(form);
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
    const doc = taskSummary({
      kind: 'master',
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
  const tasks = Object.fromEntries(
    [chatSprint, chatMaster, chatLeaf].map((entry) => [entry.key, entry]),
  );
  it('follows served parents through master to sprint', () => {
    expect(docChain(chatLeaf, tasks)).toEqual([
      {
        form: 'sprint',
        text: chatSprint.id,
        title: chatSprint.title,
        targetKey: taskEntrySelectionKey(chatSprint.key),
      },
      {
        form: 'master',
        text: chatMaster.id,
        title: chatMaster.title,
        targetKey: taskEntrySelectionKey(chatMaster.key),
      },
    ]);
    expect(docChain(chatMaster, tasks)).toEqual([
      {
        form: 'sprint',
        text: chatSprint.id,
        title: chatSprint.title,
        targetKey: taskEntrySelectionKey(chatSprint.key),
      },
    ]);
    expect(docChain(chatSprint, tasks)).toEqual([]);
  });
  it('stops at an absent parent and at a repeated reference', () => {
    expect(docChain(chatLeaf, {})).toEqual([]);
    expect(docChain({ ...chatMaster, parent: chatLeaf.key }, tasks)).toEqual([
      {
        form: 'master',
        text: chatLeaf.id,
        title: chatLeaf.title,
        targetKey: taskEntrySelectionKey(chatLeaf.key),
      },
    ]);
  });
});
