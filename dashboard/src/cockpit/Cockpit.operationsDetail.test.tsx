// The Operations detail with nothing selected, and the first selection made from it.
//
// With no selection the panel says what a selected task shows. It names no gate: the task page
// has no gate place. Selecting a task from that state still loads the task page — the steps and
// the review of the projected document, the requirements of the document body, and the reports of
// its role agents from the reports list.
// The documents here are made up.
import { fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  enclosure,
  seedTaskEntries,
  taskTree,
  taskEntry,
  taskSummary,
  taskReference,
} from '../panels/detail-panel/test-utils';
import type { TaskEntry } from '../types/projection';
import { CockpitShell } from './Cockpit';

const REPO = 'repo-a';
const MASTER = 'ops';
const LEAF_PATH = `${REPO}/${MASTER}/01_leaf.json`;
const LIFECYCLE = 'LC-LEAF';
const CONTRACT = '/contracts/leaf-one';
const REPORT_ID = 'aaaa1111-2222-4333-8444-555566667777';
const PROMPT = 'Select a task to inspect its steps, review, requirements and reports.';
const REQUIREMENT = 'The page keeps its blocks.';

// The projected summary of the leaf: steps and review, no requirement yet.
function leafEntry(): TaskEntry {
  return taskEntry({
    id: 'leaf-one',
    reference: taskReference(LEAF_PATH),
    stage: 'started',
    title: 'Leaf One',
    stepsDone: 1,
    stepsTotal: 2,
    summary: taskSummary({
      bodyRevision: 'leaf-r1',
      objective: 'Leaf objective.',
      steps: [
        {
          id: 'S1',
          title: 'Build the page',
          status: 'done',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
        {
          id: 'S2',
          title: 'Hand it to review',
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
            description: 'A block is missing.',
            open: true,
            descriptionTruncated: false,
          },
        ],
        totalFindings: 1,
        openFindings: 1,
      },
    }),
    worktrees: [
      enclosure({
        enclosure: CONTRACT,
        lifecycleId: LIFECYCLE,
        leafId: 'leaf-one',
        taskRoot: `/tasks/${REPO}/${MASTER}`,
        taskId: 'ops-master',
        taskName: MASTER,
        repoName: REPO,
        worktreeGroup: '/worktrees/leaf-one',
      }),
    ],
  });
}

// A master and its leaf in work: the leaf's worktree admits its row to the tasks column.
function seedOperations(): void {
  seedTaskEntries(
    taskTree(
      taskEntry({
        id: 'ops-master',
        reference: taskReference(`${REPO}/${MASTER}/task.json`),
        stage: 'started',
        title: 'Ops Master',
        summary: taskSummary({ kind: 'master', form: 'master' }),
      }),
      [leafEntry()],
    ),
  );
}

function answer(payload: unknown): Response {
  return new Response(JSON.stringify(payload), { headers: { 'Content-Type': 'application/json' } });
}

// What the server answers for the leaf: its body with the requirement, one recorded report, an
// existing empty notes folder and an empty change set. Every other address answers an empty object.
function stubTaskPage() {
  const routes: Array<[string, unknown]> = [
    ['/api/task-document', { ...leafEntry().summary, requirements: [REQUIREMENT] }],
    [
      '/api/role-launch/reports',
      {
        repo: REPO,
        master: MASTER,
        doc: '01_leaf.json',
        reports: [
          {
            role: 'worker',
            requestId: REPORT_ID,
            createdAt: '2026-10-01T10:00:00+00:00',
            status: 'done',
            path: null,
            canonicalPath: null,
            available: true,
          },
        ],
        total: 1,
        explorer: null,
      },
    ],
    [
      '/api/notes/folder',
      {
        repo: REPO,
        master: MASTER,
        path: '',
        entries: [],
        total: 0,
        offset: 0,
        limit: 200,
        remaining: 0,
        exists: true,
        reason: null,
      },
    ],
    [
      '/api/changeset/',
      {
        counters: {
          code: { files: 0, insertions: 0, deletions: 0 },
          memory: { files: 0, insertions: 0, deletions: 0 },
        },
      },
    ],
  ];
  const fetchMock = vi.fn(async (url: string) => {
    const route = routes.find(([prefix]) => url.startsWith(prefix));
    return answer(route ? route[1] : {});
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const requestsTo = (fetchMock: ReturnType<typeof stubTaskPage>, prefix: string): string[] =>
  fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith(prefix));

beforeEach(() => vi.stubGlobal('CSS', { escape: (value: string) => value }));

afterEach(() => {
  window.localStorage.clear(); // the shell persists its rail choices; isolate them between tests
});

describe('Operations detail without a selection', () => {
  it('says what a selected task shows and names no gate', () => {
    seedOperations();
    stubTaskPage();
    const view = render(<CockpitShell initialView="operations" />);

    const detail = view.getByTestId('detail-panel');
    expect(within(detail).getByRole('heading', { name: 'Detail' })).toBeTruthy();
    expect(within(detail).getByText(PROMPT)).toBeTruthy();
    expect(detail.textContent).not.toMatch(/gate/i);
    expect(view.queryByTestId('skeleton-task')).toBeNull();
  });

  it('loads the steps, review, requirements and reports of the task selected from it', async () => {
    seedOperations();
    const fetchMock = stubTaskPage();
    const view = render(<CockpitShell initialView="operations" />);
    expect(within(view.getByTestId('detail-panel')).getByText(PROMPT)).toBeTruthy();
    expect(requestsTo(fetchMock, '/api/task-document')).toHaveLength(0);

    fireEvent.click(view.getByText('Leaf One'));

    const page = await view.findByTestId('skeleton-task');
    const block = (name: string): HTMLElement => {
      const found = page.querySelector<HTMLElement>(`[data-block="${name}"]`);
      if (!found) throw new Error(`block not drawn: ${name}`);
      return found;
    };
    await waitFor(() => expect(block('requirements').textContent).toContain(REQUIREMENT));
    expect(block('steps').textContent).toContain('Build the page');
    expect(block('steps').textContent).toContain('Hand it to review');
    expect(block('review').textContent).toContain('A block is missing.');
    const report = await within(page).findByTestId(`task-report-${REPORT_ID}`);
    expect(block('references').contains(report)).toBe(true);
    expect(report.textContent).toContain('worker');

    const detail = view.getByTestId('detail-panel');
    expect(within(detail).queryByText(PROMPT)).toBeNull();
    const bodyRequests = requestsTo(fetchMock, '/api/task-document');
    expect(bodyRequests).toHaveLength(1);
    expect(bodyRequests[0]).toContain(encodeURIComponent(LEAF_PATH));
    expect(requestsTo(fetchMock, '/api/role-launch/reports')).toHaveLength(1);
  });
});
