// MIK-R127 rule 10: the page follows the proposal's look. One test per thing
// the look owes: the head with the objective first (D219) and the labelled
// facts, notes that open in place, the steps table, the tables of work in
// progress on a master and on a sprint, nothing drawn twice, and a link drawn
// as a link. All documents here are made up.
import { fireEvent, render, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import type { TaskEntry, TaskSummary, TaskStepNode } from '../../types/projection';
import {
  cells,
  seedTaskEntries,
  stubNotes,
  taskEntry,
  taskTree,
  taskSummary,
  taskReference,
} from './test-utils';

const REPO = 'agents-remember';
const SPRINT = `${REPO}/look-sprint/task.json`;
const MASTER = `${REPO}/look-master/task.json`;
const LEAF = `${REPO}/look-master/01_helper.json`;
const PACKET = 'LOOK-R01-v1.md';
const REQUIREMENT_TEXT = 'The helper returns an empty string for a text of punctuation alone.';
const LONG_NOTE =
  'The leaf owns one module and one requirement. A change that needs a second module is a second leaf, and the order of the two is written into the master before either of them starts.';

function step(id: string, title: string, status: string, over: Partial<TaskStepNode> = {}) {
  return {
    id,
    title,
    status,
    substeps: [],
    noteTruncated: false,
    outcomeTruncated: false,
    ...over,
  };
}

function leaf(summary: Partial<TaskSummary> = {}, entry: Partial<TaskEntry> = {}): TaskEntry {
  return taskEntry({
    reference: taskReference(LEAF),
    id: 'LOOK-M1-L1',
    title: 'Add the helper',
    stage: 'started',
    activity: 'paused',
    stepsDone: 0,
    stepsTotal: 0,
    ...entry,
    summary: taskSummary({
      form: 'task',
      kind: 'subTask',
      objective: 'One pure helper with its test.',
      type: '',
      createdAt: '2026-06-20T09:00:00+00:00',
      ...summary,
    }),
  });
}
function master(summary: Partial<TaskSummary> = {}, entry: Partial<TaskEntry> = {}): TaskEntry {
  return taskEntry({
    reference: taskReference(MASTER),
    id: 'LOOK-M1',
    title: 'Helpers',
    stage: 'started',
    activity: 'paused',
    stepsDone: 0,
    stepsTotal: 0,
    ...entry,
    summary: taskSummary({
      form: 'master',
      kind: 'master',
      objective: '',
      type: '',
      createdAt: '2026-06-20T09:00:00+00:00',
      ...summary,
    }),
  });
}

// True where `first` stands before `second` in the document.
function before(first: Element, second: Element): boolean {
  return (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
}

describe('the head (rule 2, with MIK-R150 rule 14)', () => {
  it('leads with the title, then the labelled facts, then the notes, then the objective below the head', () => {
    seedTaskEntries(
      taskTree(master(), [
        leaf(
          {
            statusNote: 'In work.',
            type: 'SubTask (made up)',
            headerNotes: [{ label: 'Execution boundary', value: LONG_NOTE }],
          },
          {},
        ),
      ]),
    );
    const { container, getByTestId, getAllByText } = render(
      <DetailPanel selectedId={`taskdoc:${LEAF}`} />,
    );

    // the objective stands under a heading, as every block of the page does (D223)
    expect(within(getByTestId('objective-lead')).getByRole('heading').textContent).toBe(
      'Objective',
    );
    const order = ['doc-title', 'doc-facts', 'header-notes', 'objective-lead'].map((id) =>
      getByTestId(id),
    );
    order.slice(1).forEach((element, index) => expect(before(order[index], element)).toBe(true));
    // the objective stands once, below the head and nowhere further down
    expect(container.querySelectorAll('[data-block="objective"]')).toHaveLength(1);
    expect(getByTestId('doc-head').querySelector('[data-block="objective"]')).toBeNull();
    expect(getAllByText('One pure helper with its test.')).toHaveLength(1);
    // every fact of the strip is a label with its value under it
    const facts = Array.from(getByTestId('doc-facts').children).map((fact) => cells(fact));
    expect(facts.map((fact) => fact[0])).toEqual(['chain', 'type', 'created']);
    expect(facts.every((fact) => fact.length === 2 && fact[1] !== '')).toBe(true);
    expect(getByTestId('doc-chain').textContent).toBe('chainmasterLOOK-M1→taskL1');
  });

  it('draws the primary requirement as its link and its text once, under Requirements', async () => {
    const line = `[LOOK-R01@v1](requirements/${PACKET}) — ${REQUIREMENT_TEXT}`;
    seedTaskEntries(
      taskTree(master(), [
        leaf(
          { headerNotes: [{ label: 'Primary requirement', value: line }], requirements: [line] },
          {},
        ),
      ]),
    );
    stubNotes([], 'note body', [
      { name: PACKET, path: PACKET, address: `requirements/${PACKET}`, size: 1, sha256: 'a' },
    ]);
    const view = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    const fact = view.getByTestId('doc-primary-requirement');
    const link = await within(fact).findByTestId('requirement-link');
    expect(link.tagName.toLowerCase()).toBe('button');
    expect(cells(fact)).toEqual(['primary requirement', 'LOOK-R01@v1']);
    // its text is no head note and stands once on the page
    expect(view.queryByTestId('header-note-Primary requirement')).toBeNull();
    expect(view.getAllByText(new RegExp(REQUIREMENT_TEXT))).toHaveLength(1);
  });

  it('keeps a short note in the strip and opens a long one in place', () => {
    seedTaskEntries(
      taskTree(master(), [
        leaf(
          {
            statusNote: LONG_NOTE,
            headerNotes: [
              { label: 'Stage', value: 'First wave' },
              { label: 'Execution boundary', value: LONG_NOTE },
            ],
          },
          {},
        ),
      ]),
    );
    const { getByTestId, queryByTestId } = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    expect(cells(within(getByTestId('doc-facts')).getByTestId('header-note-Stage'))).toEqual([
      'Stage',
      'First wave',
    ]);
    const notes = within(getByTestId('header-notes'));
    expect(cells(notes.getByTestId('status-note')).slice(0, 2)).toEqual(['status note', LONG_NOTE]);
    const line = notes.getByTestId('header-note-Execution boundary');
    expect(cells(line).slice(0, 2)).toEqual(['Execution boundary', LONG_NOTE]);

    const toggle = getByTestId('header-note-Execution boundary-toggle');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.textContent).toBe('less');
    // in place: the page stays, no view of its own opens
    expect(queryByTestId('doc-view')).toBeNull();
  });

  it('holds a long objective to its first lines and opens it in place', () => {
    const objective = Array.from({ length: 6 }, (_, i) => `- Point ${i + 1} of the objective.`);
    seedTaskEntries(taskTree(master(), [leaf({ objective: objective.join('\n') }, {})]));
    const { getByTestId, queryByTestId } = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    expect(getByTestId('objective-text').getAttribute('data-open')).toBe('false');
    fireEvent.click(getByTestId('objective-toggle'));
    expect(getByTestId('objective-text').getAttribute('data-open')).toBe('true');
    expect(getByTestId('objective-toggle').textContent).toBe('less');
    // six lines are within the long-text bound: no view of its own is offered
    expect(queryByTestId('objective-all-link')).toBeNull();
    expect(queryByTestId('doc-view')).toBeNull();
  });

  it('draws a short objective whole without a control', () => {
    seedTaskEntries(taskTree(master(), [leaf()]));
    const { getByTestId, queryByTestId } = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    expect(getByTestId('objective-text').getAttribute('data-open')).toBe('true');
    expect(queryByTestId('objective-toggle')).toBeNull();
  });
});

describe('the steps table (rule 5)', () => {
  it('draws mark, number, title and state per step, with the progress in the heading', () => {
    seedTaskEntries(
      taskTree(master(), [
        leaf(
          {
            steps: [
              step('S1', 'Write `slug.py`', 'done', { note: 'Lowercases first.' }),
              step('S2', 'Run the tests', 'inProgress', {
                substeps: [
                  { id: 'S2.1', title: 'On a clean tree', status: 'done' },
                  { id: 'S2.2', title: 'Attach the output', status: 'pending' },
                ],
              }),
              step('S3', 'Review', 'blocked'),
              step('S4', 'Land', 'pending'),
            ],
          },
          { stepsDone: 1, stepsTotal: 4 },
        ),
      ]),
    );
    const { getByTestId } = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    expect(cells(getByTestId('step-S1')).slice(0, 4)).toEqual(['', 'S1', 'Write slug.py', 'done']);
    expect(cells(getByTestId('step-S2')).slice(0, 4)).toEqual([
      '',
      'S2',
      'Run the tests',
      'current',
    ]);
    expect(getByTestId('step-state-S3').textContent).toBe('blocked');
    expect(getByTestId('step-state-S4').textContent).toBe('pending');
    // Markdown in a title is drawn, never printed
    expect(getByTestId('step-S1').querySelector('code')?.textContent).toBe('slug.py');
    expect(getByTestId('step-note-S1').textContent).toBe('Lowercases first.');
    expect(cells(getByTestId('substep-S2-S2.2')).slice(1)).toEqual(['S2.2', 'Attach the output']);
    // MIK-R128: the heading counts and marks major steps only.
    const heading = getByTestId('steps-count');
    expect(getByTestId('steps-progress').textContent).toBe('1 of 4 done');
    expect(heading.querySelectorAll('[data-step-dot]')).toHaveLength(4);
  });

  it('marks the first step that is not done as the next one', () => {
    seedTaskEntries(
      taskTree(master(), [
        leaf(
          {
            steps: [
              step('S1', 'One', 'done'),
              step('S2', 'Two', 'pending'),
              step('S3', 'Three', 'pending'),
            ],
          },
          { stepsDone: 1, stepsTotal: 3 },
        ),
      ]),
    );
    const { getByTestId } = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    expect(getByTestId('step-next-S2').textContent).toBe('next');
    expect(getByTestId('step-state-S3').textContent).toBe('pending');
  });
});

describe('the tables of work in progress (rules 3-4)', () => {
  function seedMasterInWork() {
    const second = `${REPO}/look-master/02_second.json`;
    seedTaskEntries(
      taskTree(master({}, {}), [
        leaf(
          {
            steps: [step('S1', 'Write it', 'done'), step('S2', 'Run the `tests`', 'inProgress')],
            reviewState: {
              round: 2,
              pending: false,
              additionalRounds: 0,
              totalFindings: 3,
              openFindings: 1,
              findings: [
                {
                  findingId: 'F1',
                  description: 'Open one',
                  open: true,
                  descriptionTruncated: false,
                },
                {
                  findingId: 'F2',
                  description: 'Closed one',
                  open: false,
                  descriptionTruncated: false,
                },
                {
                  findingId: 'F3',
                  description: 'Closed two',
                  open: false,
                  descriptionTruncated: false,
                },
              ],
            },
          },
          { stepsDone: 1, stepsTotal: 2 },
        ),
        leaf(
          {
            steps: [step('S1', 'Write it', 'pending')],
            reviewState: {
              round: 1,
              pending: true,
              additionalRounds: 0,
              totalFindings: 0,
              openFindings: 0,
              findings: [],
            },
          },
          {
            id: 'LOOK-M1-L2',
            reference: taskReference(second),
            title: 'Second helper',
            stepsDone: 0,
            stepsTotal: 1,
          },
        ),
        taskEntry({
          reference: taskReference(`${REPO}/look-master/03_third.json`),
          id: 'LOOK-M1-L3',
          title: 'Third helper',
          stage: 'planned',
          summary: undefined,
        }),
      ]),
    );
  }

  it('lists a master’s tasks in work with steps done, standing step and review', () => {
    seedMasterInWork();
    const { getAllByTestId, getByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${MASTER}`} />,
    );

    expect(cells(getByTestId('now-table').firstElementChild)).toEqual([
      'task',
      'steps done',
      'step',
      'review',
    ]);
    // the row names the task by the short id its master's page needs
    expect(cells(getByTestId('subtask-open-1'))).toEqual(['L1', 'Add the helper']);
    expect(getByTestId('now-steps-1').textContent).toBe('1/2');
    expect(getByTestId('now-step-1').textContent).toBe('current S2 Run the tests');
    // Markdown in a step's title is drawn here too, never printed
    expect(getByTestId('now-step-1').querySelector('code')?.textContent).toBe('tests');
    expect(getByTestId('now-review-1').textContent).toBe('round 2 · 1 of 3 findings open');
    expect(getByTestId('now-step-2').textContent).toBe('next S1 Write it');
    expect(getByTestId('now-review-2').textContent).toBe('round 1 · verdict open');
    // a task in work stands once: Tasks points at Now instead of listing it again
    expect(getAllByTestId('subtask-open-1')).toHaveLength(1);
    expect(getByTestId('tasks-now-link')).toBeTruthy();
    expect(cells(getByTestId('waiting-table').firstElementChild)[0]).toBe('task');
  });

  it('counts the open findings of the whole round where the row carries only the first', () => {
    // A round of 26 findings with the last one open: the summary a master's row reads
    // carries the first 25, all closed, and the projection's count of the round.
    seedTaskEntries(
      taskTree(master({}, {}), [
        leaf(
          {
            reviewState: {
              round: 2,
              pending: false,
              additionalRounds: 0,
              totalFindings: 26,
              openFindings: 1,
              findings: Array.from({ length: 25 }, (_, index) => ({
                findingId: `F${index + 1}`,
                description: `Closed finding ${index + 1}`,
                open: false,
                descriptionTruncated: false,
              })),
            },
          },
          { stage: 'started' },
        ),
      ]),
    );
    stubNotes([], '');
    const now = render(<DetailPanel selectedId={`taskdoc:${MASTER}`} />);
    expect(now.getByTestId('now-review-1').textContent).toBe('round 2 · 1 of 26 findings open');
    now.unmount();

    // the task's own Review block says the same and keeps the whole round one link away
    const task = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);
    expect(task.getByTestId('review-open-count').textContent).toBe('1 of 26 findings open');
    expect(task.getByTestId('findings-all-link').textContent).toContain('all 26 findings');
  });

  it('lists a sprint’s masters with completed tasks and served activity counts', () => {
    const helpers = taskTree(master(), [
      taskEntry({
        reference: taskReference(`${REPO}/look-master/01_one.json`),
        id: 'L1',
        title: 'One',
        stage: 'completed',
        summary: undefined,
      }),
      taskEntry({
        reference: taskReference(`${REPO}/look-master/02_two.json`),
        id: 'L2',
        title: 'Two',
        stage: 'started',
        activity: 'works',
        summary: undefined,
      }),
      taskEntry({
        reference: taskReference(`${REPO}/look-master/03_three.json`),
        id: 'L3',
        title: 'Three',
        stage: 'planned',
        summary: undefined,
      }),
    ]);
    const sprint = taskEntry({
      reference: taskReference(SPRINT),
      id: 'LOOK-SPRINT',
      title: 'Look sprint',
      stage: 'started',
      activity: 'works',
      stepsDone: 0,
      stepsTotal: 0,
      summary: taskSummary({ form: 'sprint', kind: 'master', objective: '' }),
    });
    seedTaskEntries([...taskTree(sprint, [helpers[0]]), ...helpers.slice(1)]);
    const { getByTestId } = render(<DetailPanel selectedId={`taskdoc:${SPRINT}`} />);

    expect(cells(getByTestId('now-table').firstElementChild)).toEqual([
      'master',
      'tasks completed',
      'activity',
    ]);
    expect(cells(getByTestId('now-row-1'))).toEqual([
      'LOOK-M1Helpers',
      '1/3',
      '1 works · 0 paused paused',
    ]);
    expect(getByTestId('subtask-open-master-1').tagName.toLowerCase()).toBe('button');
  });
});

describe('the reports of a task (rule 5)', () => {
  it('asks for them by the path inside the canonical qualified task reference', async () => {
    seedTaskEntries(taskTree(master(), [leaf()]));
    const fetched = stubNotes([], '');
    const view = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    await view.findByTestId('task-reports-empty');
    const asked = fetched.mock.calls
      .map(([url]) => url)
      .filter((url) => url.startsWith('/api/role-launch/reports'));
    expect(asked).toEqual([
      '/api/role-launch/reports?repo=agents-remember&master=look-master&doc=look-master%2F01_helper.json',
    ]);
  });
});

describe('links (rule 10)', () => {
  function seedSprintWithRegister(choice: string, evidence: string, scope = '') {
    const sprint = taskEntry({
      reference: taskReference(SPRINT),
      id: 'LOOK-SPRINT',
      title: 'Look sprint',
      stage: 'started',
      activity: 'paused',
      stepsDone: 0,
      stepsTotal: 0,
      summary: taskSummary({
        form: 'sprint',
        kind: 'master',
        objective: '',
        sections: [
          {
            kind: 'freeform',
            heading: 'Judgment Register',
            body: [
              '| ID | Date | Choice | Owner | Evidence |',
              '| --- | --- | --- | --- | --- |',
              `| J1 | 2026-10-11 | ${choice} | Architect | ${evidence} |`,
            ].join('\n'),
          },
        ],
      }),
    });
    seedTaskEntries(taskTree(sprint, [master({}, { scope })]));
    stubNotes([], '');
  }

  it('draws a link in a register cell as a link, in the shown columns and in the opened rest', () => {
    seedSprintWithRegister(
      '[the ruling](https://example.org/ruling)',
      '[the proof](https://example.org/proof)',
    );
    const view = render(<DetailPanel selectedId={`taskdoc:${SPRINT}`} />);
    const row = view.getByTestId('register-row-0');
    const shown = within(row).getByRole('link', { name: 'the ruling' });
    expect(shown.getAttribute('href')).toBe('https://example.org/ruling');

    // a click on the link is not a wish to open the row; a click beside it is
    shown.addEventListener('click', (event) => event.preventDefault());
    fireEvent.click(shown);
    expect(row.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(within(row).getByText('Architect'));
    expect(row.getAttribute('aria-expanded')).toBe('true');

    const rest = view.getByTestId('register-row-0-detail');
    const hidden = within(rest).getByRole('link', { name: 'the proof' });
    expect(hidden.getAttribute('href')).toBe('https://example.org/proof');
    expect(view.getByTestId('register-table').textContent).not.toContain('](');
  });

  it('keeps a plain register cell as its text', () => {
    seedSprintWithRegister('a plain choice', 'a plain proof');
    const view = render(<DetailPanel selectedId={`taskdoc:${SPRINT}`} />);
    const row = view.getByTestId('register-row-0');
    expect(cells(row)).toEqual(['J1', '2026-10-11', 'a plain choice', 'Architect']);
    fireEvent.click(row);
    expect(view.getByTestId('register-row-0-detail').textContent).toBe('Evidence: a plain proof');
  });

  it('draws a link in the scope of a row of the sprint document as a link', () => {
    seedSprintWithRegister(
      'a plain choice',
      'a plain proof',
      'See [the plan](https://example.org/plan)',
    );
    const view = render(<DetailPanel selectedId={`taskdoc:${SPRINT}`} />);
    fireEvent.click(view.getByTestId('rows-all-link'));
    const scope = within(view.getByTestId('sprint-row-1')).getByRole('link', { name: 'the plan' });
    expect(scope.getAttribute('href')).toBe('https://example.org/plan');
    expect(view.getByTestId('sprint-rows-table').textContent).not.toContain('](');
  });

  it('draws a link in the skip reason of a nested step as in a step’s own', () => {
    const disposition = {
      kind: 'intentionalSkip' as const,
      recordedAt: '2026-10-11T00:00:00Z',
      recordedVia: 'task_doc.skip_step' as const,
      reason: 'See [Direction](https://example.org/direction)',
    };
    seedTaskEntries(
      taskTree(master(), [
        leaf(
          {
            steps: [
              step('S1', 'Parent', 'done', {
                disposition,
                substeps: [{ id: 'S1.1', title: 'Child', status: 'done', disposition }],
              }),
            ],
          },
          { stepsDone: 1, stepsTotal: 1 },
        ),
      ]),
    );
    stubNotes([], '');
    const view = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);
    const own = within(view.getByTestId('skipped-disposition'));
    expect(own.getByRole('link', { name: 'Direction' }).getAttribute('href')).toBe(
      'https://example.org/direction',
    );
    const nested = within(view.getByTestId('substep-S1-S1.1'));
    expect(nested.getByRole('link', { name: 'Direction' }).getAttribute('href')).toBe(
      'https://example.org/direction',
    );
    expect(view.getByTestId('substep-S1-S1.1').textContent).not.toContain('](');
  });

  it('draws a Markdown link as a link and a reference as its words and its place', async () => {
    seedTaskEntries(
      taskTree(master(), [
        leaf(
          {
            headerNotes: [{ label: 'Authority', value: '[Direction](notes/direction.md) S1' }],
            references: [
              `[Primary packet](requirements/${PACKET})`,
              '[Direction](notes/direction.md)',
              'A plain line without a target',
            ],
          },
          {},
        ),
      ]),
    );
    stubNotes([{ name: 'direction.md', path: 'direction.md', size: 1, language: 'markdown' }], '', [
      { name: PACKET, path: PACKET, address: `requirements/${PACKET}`, size: 1, sha256: 'a' },
    ]);
    const view = render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);

    const authority = view.getByTestId('header-note-Authority');
    expect(within(authority).getByText('Direction').closest('a, button')).toBeTruthy();
    const packet = await view.findByTestId('requirement-ref-1');
    expect(cells(packet.parentElement)).toEqual(['Primary packet', `requirements/${PACKET}`]);
    const note = await view.findByTestId('note-ref-2');
    expect(cells(note.parentElement)).toEqual(['Direction', 'notes/direction.md']);
    expect(view.getByTestId('plain-ref-3').textContent).toBe('A plain line without a target');
    // no bracket-and-parenthesis source is left anywhere on the page
    expect(view.container.textContent).not.toContain('](');
  });
});
