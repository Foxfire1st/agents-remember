import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DetailPanel } from './DetailPanel';
import {
  cells,
  nestedProgressSteps,
  seedSeries,
  seedSeriesOrdering,
  seedTaskEntries,
  taskEntry,
  taskSummary,
  taskReference,
  taskTree,
  enclosure,
} from './test-utils';

describe('DetailPanel native task navigation (6g)', () => {
  it('renders an unbound planning leaf task document by typed taskdoc selection', () => {
    const doc = taskEntry({
      id: '1',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/planning/01_plan.json'),
      title: 'Plan Before Worktree',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        objective: 'Plan the document before opening a worktree.',
      }),
    });
    seedTaskEntries([doc]);

    const { getAllByText, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/planning/01_plan.json" />,
    );

    expect(getAllByText('Plan Before Worktree').length).toBeGreaterThan(0);
    expect(getByText('Plan the document before opening a worktree.')).toBeTruthy();
    expect(queryByText('No task document bound to this task.')).toBeNull();
  });

  it('renders an unbound master task document by kind', () => {
    const master = taskEntry({
      id: '1',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/planning/task.json'),
      title: 'Planning Master',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'master',
        kind: 'master',
        objective: 'Master plan objective.',
      }),
    });
    const leaf = taskEntry({
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/planning/01_leaf.json'),
      id: '99',
      title: 'Leaf created first',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        objective: 'Leaf objective.',
      }),
    });
    seedTaskEntries(taskTree(master, [leaf]));

    const { getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/planning/task.json" />,
    );

    expect(getByText('Master plan objective.')).toBeTruthy();
    expect(cells(getByTestId('subtask-open-1'))).toEqual(['99', 'Leaf created first']);
    expect(queryByText('No task document bound to this task.')).toBeNull();
  });

  it('keeps discarded-before-start history behind the gone link beside the live sub-task index', () => {
    const master = taskEntry({
      id: '1',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/planning/task.json'),
      title: 'Audited planning master',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'master',
        kind: 'master',
        objective: 'Keep planning removal visible without inventing completion.',
        discardedCount: 1,
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
    });
    seedTaskEntries(
      taskTree(master, [
        taskEntry({
          reference: taskReference('repo-a/planning/01_live.json'),
          id: '1',
          title: 'Live planning leaf',
          stage: 'planned',
          summary: undefined,
        }),
      ]),
    );

    const { getByTestId, getByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/planning/task.json" />,
    );

    expect(cells(getByTestId('subtask-open-1'))).toEqual(['1', 'Live planning leaf']);
    // no abandoned rows, one discarded audit: the link names the discarded history
    expect(getByTestId('group-gone-link').textContent).toContain('1 discarded before start');
    fireEvent.click(getByTestId('group-gone-link'));
    const discarded = getByText('Never started').parentElement;
    expect(cells(discarded as HTMLElement).slice(0, 2)).toEqual(['2', 'Never started']);
    expect(discarded?.textContent).toContain('No implementation was needed');
    expect(discarded?.textContent).toContain('proof ' + 'a'.repeat(64));
    fireEvent.click(getByTestId('doc-view-back'));
    expect(getByTestId('subtask-open-1')).toBeTruthy();
  });

  it('shows once on the master’s page the records its list and their own master place apart', () => {
    const master = taskEntry({
      reference: taskReference('repo-a/planning/task.json'),
      id: 'MASTER',
      title: 'Planning Master',
      summary: taskSummary({ form: 'master', kind: 'master', objective: 'Master plan objective.' }),
      disagreements: [
        { kind: 'not-listed', record: taskReference('repo-a/planning/03_stray.json') },
        {
          kind: 'names-another-master',
          record: taskReference('repo-a/planning/04_moved.json'),
          row: '4',
          recordMaster: taskReference('repo-a/other/task.json'),
        },
        {
          kind: 'unresolvable-master',
          record: taskReference('repo-a/planning/05_lost.json'),
          row: '5',
          masterLiteral: '../../elsewhere/task.md',
        },
        // Two disputes that differ in one field only stay two rows, each with its own key.
        {
          kind: 'listed-twice',
          record: taskReference('repo-a/planning/01_live.json'),
          row: '1',
          parent: 'repo-a/other/task.json',
        },
        {
          kind: 'listed-twice',
          record: taskReference('repo-a/planning/01_live.json'),
          row: '1',
          parent: 'repo-a/third/task.json',
        },
      ],
    });
    const listed = taskEntry({
      reference: taskReference('repo-a/planning/01_live.json'),
      id: '1',
      title: 'Live planning leaf',
      stage: 'planned',
      summary: undefined,
    });
    const other = taskEntry({
      reference: taskReference('repo-a/other/task.json'),
      id: 'OTHER',
      title: 'Other master',
      summary: taskSummary({ form: 'master', kind: 'master' }),
    });
    const stray = taskEntry({
      reference: taskReference('repo-a/planning/03_stray.json'),
      id: '3',
      title: 'Stray leaf',
      stage: 'planned',
      summary: undefined,
    });
    seedTaskEntries([...taskTree(master, [listed]), other, stray]);
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const ui = render(<DetailPanel selectedId="taskdoc:repo-a/planning/task.json" />);
    const keyWarnings = errors.mock.calls.filter((call) => String(call[0]).includes('same key'));
    errors.mockRestore();
    expect(keyWarnings).toEqual([]);

    expect(ui.getAllByText('Records and list disagree')).toHaveLength(1);
    expect(cells(ui.getByTestId('subtask-open-1'))).toEqual(['1', 'Live planning leaf']);
    const first = ui.getByTestId('list-disagreement-1');
    expect(first.textContent).toBe('3Stray leafnames no master; in no list');
    expect(ui.getByTestId('list-disagreement-2').textContent).toBe(
      'planning/04_moved.jsonlisted as 4; names Other master',
    );
    expect(ui.getByTestId('list-disagreement-3').textContent).toBe(
      'planning/05_lost.jsonlisted as 5; names master “../../elsewhere/task.md”, which is no task',
    );
    expect(ui.getAllByTestId(/^list-disagreement-\d+$/)).toHaveLength(5);
    fireEvent.click(first.querySelector('button') as HTMLElement);
    expect(ui.getByText(/not authored yet/i)).toBeTruthy();
  });

  it('opens the native child of a master with a bound worktree', () => {
    const master = taskEntry({
      id: '1',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/planning/task.json'),
      title: 'Bound Master',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'master',
        kind: 'master',
        objective: 'Master plan objective.',
      }),
    });
    const leaf = taskEntry({
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/planning/01_leaf.json'),
      id: '1',
      title: 'Leaf from projected pool',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        objective: 'Leaf objective from the projected pool.',
      }),
    });
    master.worktrees = [enclosure({ enclosure: '/contracts/master', taskId: master.id })];
    seedTaskEntries(taskTree(master, [leaf]));

    const { getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/planning/task.json" />,
    );

    expect(getByTestId('subtask-open-1').tagName.toLowerCase()).toBe('button');
    fireEvent.click(getByTestId('subtask-open-1'));
    expect(getByText('Leaf objective from the projected pool.')).toBeTruthy();
    expect(queryByText('No task document bound to this task.')).toBeNull();
  });

  it('opens a declared leaf without a document with the not-authored message', () => {
    const master = taskEntry({
      reference: taskReference('repo-a/planning/task.json'),
      id: 'MASTER',
      title: 'Planning Master',
      summary: taskSummary({ form: 'master', kind: 'master', objective: 'Master plan objective.' }),
    });
    const missing = taskEntry({
      reference: taskReference('repo-a/planning/01_missing.json'),
      id: '1',
      title: 'Missing leaf',
      stage: 'planned',
      summary: undefined,
    });
    seedTaskEntries(taskTree(master, [missing]));
    const ui = render(<DetailPanel selectedId="taskdoc:repo-a/planning/task.json" />);
    expect(cells(ui.getByTestId('subtask-open-1'))).toEqual(['1', 'Missing leaf']);
    fireEvent.click(ui.getByTestId('subtask-open-1'));
    expect(ui.getByText(/not authored yet/i)).toBeTruthy();
  });

  it('renders structured ids with step and code example titles', () => {
    const doc = taskEntry({
      id: '1',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/planning/ids.json'),
      title: 'Id Display',
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        steps: [
          {
            id: 'S11',
            title: 'Make Operations disappearance archive/delete-based',
            status: 'pending',
            substeps: [
              {
                id: 'S11.1',
                title: 'Exclude archived task documents',
                status: 'pending',
              },
            ],
            noteTruncated: false,
            outcomeTruncated: false,
          },
        ],
        codeExamples: [
          {
            id: 'E4',
            title: 'Master leaf list uses task-specific numbers',
            distinctChange: 'Series/master reader ordering and enumeration.',
            why: 'The display number is structured presentation.',
            language: 'tsx',
            snippet: '<LeafTaskRow />',
          },
        ],
      }),
    });
    seedTaskEntries([doc]);

    const { getAllByText, getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/planning/ids.json" />,
    );

    // the step row: its number in one cell, its title in the next, each once
    expect(cells(getByTestId('step-S11')).slice(1, 3)).toEqual([
      'S11',
      'Make Operations disappearance archive/delete-based',
    ]);
    expect(getAllByText('Make Operations disappearance archive/delete-based')).toHaveLength(1);
    expect(cells(getByTestId('substep-S11-S11.1')).slice(1)).toEqual([
      'S11.1',
      'Exclude archived task documents',
    ]);
    expect(getByText('E4 — Master leaf list uses task-specific numbers')).toBeTruthy();
    expect(queryByText('Master leaf list uses task-specific numbers')).toBeNull();
  });

  it('labels parent and nested intentional skips without relabeling ordinary done', () => {
    const disposition = {
      kind: 'intentionalSkip' as const,
      reason: 'Superseded by the accepted path.',
      recordedAt: '2026-08-03T12:00:00+00:00',
      recordedVia: 'task_doc.skip_step' as const,
    };
    const doc = taskEntry({
      id: '1',
      stage: 'started',
      activity: 'works',
      reference: taskReference('repo-a/planning/skips.json'),
      title: 'Skip display',
      stepsDone: 3,
      stepsTotal: 3,
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        steps: [
          {
            id: 'S1',
            title: 'Skipped parent',
            status: 'done',
            disposition,
            substeps: [
              {
                id: 'C1',
                title: 'Skipped child',
                status: 'done',
                disposition: { ...disposition, reason: 'Child became unnecessary.' },
              },
            ],
            noteTruncated: false,
            outcomeTruncated: false,
          },
          {
            id: 'S2',
            title: 'Ordinary done',
            status: 'done',
            substeps: [],
            noteTruncated: false,
            outcomeTruncated: false,
          },
        ],
      }),
    });
    seedTaskEntries([doc]);

    const { getByTestId, getByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/planning/skips.json" />,
    );

    expect(getByTestId('step-state-S1').textContent).toBe('skipped');
    expect(getByText(/Superseded by the accepted path/)).toBeTruthy();
    expect(getByTestId('substep-S1-C1').textContent).toContain(
      'skipped: Child became unnecessary.',
    );
    expect(getByTestId('step-state-S2').textContent).toBe('done');
    expect(getByTestId('step-S2').textContent).not.toMatch(/skipped/i);
  });

  it('draws one sub-task index and lists the other sections as further notes', () => {
    seedSeries();
    const { getAllByTestId, getAllByText, getByTestId, getByText, queryByTestId } = render(
      <DetailPanel selectedId="taskdoc:repo-a/series/task.json" />,
    );
    expect(getAllByText('My Series').length).toBeGreaterThan(0);
    const objective = getByText('Series objective text');
    // one index: the waiting row stands once, with no in-section second copy
    expect(getAllByTestId('subtask-open-1')).toHaveLength(1);
    expect(queryByTestId('subtask-mid-1')).toBeNull();
    // the free-form section is one Further-notes line, not drawn whole
    expect(getByTestId('further-open-0').textContent).toContain('Current State');
    expect(objective).toBeTruthy();
  });

  it("drills into a slice's reader and returns via the breadcrumb", () => {
    seedSeries();
    const { getAllByText, getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/series/task.json" />,
    );
    // The master overview is shown, the slice body is not.
    expect(queryByText('Slice objective text')).toBeNull();

    fireEvent.click(getByTestId('subtask-open-1'));
    expect(getByText('Slice objective text')).toBeTruthy(); // the slice's full reader
    expect(getAllByText('do the thing')).toHaveLength(1);
    expect(cells(getByTestId('step-S1')).slice(1, 3)).toEqual(['S1', 'do the thing']);

    fireEvent.click(getByTestId('master-parent-link'));
    expect(getByTestId('subtask-open-1')).toBeTruthy(); // back to the master index
    expect(queryByText('Slice objective text')).toBeNull();
  });

  it('opens the commanded master from a sprint masterRef row, then drills to its leaf (L14-R2)', () => {
    // Native membership points across folders without a browser join.
    const sprint = taskEntry({
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/sprint/task.json'),
      id: 'SPRINT',
      title: 'Commanding Sprint',
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'sprint',
        kind: 'master',
        orchestrates: ['master'],
      }),
    });
    const master = taskEntry({
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/master/task.json'),
      id: 'MASTER',
      title: 'Commanded Master',
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'master',
        kind: 'master',
      }),
    });
    const leaf = taskEntry({
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('repo-a/master/01_leaf.json'),
      id: '1',
      title: 'Leaf one',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        objective: 'Leaf objective.',
      }),
    });
    const children = taskTree(master, [leaf]);
    seedTaskEntries([...taskTree(sprint, [children[0]]), ...children.slice(1)]);

    const { getAllByText, getByTestId, getByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/sprint/task.json" />,
    );

    // sprint → master: the typed row is the master's link — its id and its name — not a
    // slice or static row
    const masterRow = getByTestId('subtask-open-master-1');
    expect(masterRow.tagName.toLowerCase()).toBe('button');
    expect(cells(masterRow)).toEqual(['MASTER', 'Commanded Master']);
    fireEvent.click(masterRow);

    // the master document reader renders, with its leaf rows visible
    expect(getAllByText('Commanded Master').length).toBeGreaterThan(0);
    const leafRow = getByTestId('subtask-open-1');
    expect(leafRow.textContent).toContain('Leaf one');

    // master → leaf: the full sprint → master → leaf drill-down
    fireEvent.click(leafRow);
    expect(getByText('Leaf objective.')).toBeTruthy();
  });

  it('opens the declared commanded master without making a document for it', () => {
    const sprint = taskEntry({
      reference: taskReference('repo-a/sprint/task.json'),
      id: 'SPRINT',
      title: 'Commanding Sprint',
      summary: taskSummary({ form: 'sprint', kind: 'master', objective: '' }),
    });
    const missing = taskEntry({
      reference: taskReference('repo-a/missing/task.json'),
      id: '1',
      title: 'Unprojected Master',
      stage: 'planned',
      summary: undefined,
    });
    seedTaskEntries(taskTree(sprint, [missing]));
    const ui = render(<DetailPanel selectedId="taskdoc:repo-a/sprint/task.json" />);
    const row = ui.getByTestId('subtask-open-master-1');
    expect(cells(row)).toEqual(['1', 'Unprojected Master']);
    fireEvent.click(row);
    expect(ui.getByText(/not authored yet/i)).toBeTruthy();
  });

  it('renders markdown in section views (GFM table + bold), not raw on the page', () => {
    seedSeries();
    const { container, getByTestId, queryByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/series/task.json" />,
    );
    expect(container.querySelector('table')).toBeNull(); // the page holds the line, not the table
    expect(queryByText(/\| Slice \| Status \|/)).toBeNull(); // raw markdown is gone
    fireEvent.click(getByTestId('further-open-0'));
    const view = getByTestId('doc-view');
    expect(view.querySelector('table')).toBeTruthy(); // the view holds a real <table>
    expect(view.querySelector('th')?.textContent).toBe('Slice');
    expect(view.querySelector('strong')?.textContent).toBe('strong');
    fireEvent.click(getByTestId('doc-view-back'));
    expect(getByTestId('further-open-0')).toBeTruthy();
  });

  it('shows no token total on the master reader', () => {
    seedSeries();
    const { container, queryByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/series/task.json" />,
    );

    expect(queryByText('series tokens')).toBeNull();
    expect(container.querySelector("[aria-label$='aggregate series tokens']")).toBeNull();
  });

  it('keeps the server membership order and displays task-specific numbers', () => {
    seedSeriesOrdering();
    const { getByTestId } = render(<DetailPanel selectedId="taskdoc:repo-a/series/task.json" />);

    // the id cell holds the task's own number, never the row's position
    expect(cells(getByTestId('subtask-open-1'))).toEqual(['01', 'Zulu earlier']);
    expect(cells(getByTestId('subtask-open-2'))).toEqual(['99', 'Alpha later']);
  });

  it('summarizes task progress with the projected major-step count', () => {
    seedSeries({
      sliceEntry: { title: 'Parallel Leaf Enclosure Workflow', stepsDone: 6, stepsTotal: 7 },
      sliceSummary: { steps: nestedProgressSteps() },
    });
    const { getByRole, getByTestId, queryByText } = render(
      <DetailPanel selectedId="taskdoc:repo-a/series/task.json" />,
    );

    const row = getByTestId('subtask-open-1');
    // All presentations use the producer's major count, without recounting children.
    expect(getByTestId('now-steps-1').textContent).toBe('6/7');
    expect(getByTestId('now-steps-1').querySelectorAll('[data-step-dot]')).toHaveLength(7);

    fireEvent.click(row);
    expect(getByRole('img', { name: 'steps done' }).textContent).toBe('6 of 7 done');
    expect(queryByText('46/49')).toBeNull();
  });
});
