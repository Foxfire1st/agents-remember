import { fireEvent,render,waitFor,within } from '@testing-library/react';
import { describe,expect,it,vi } from 'vitest';

import { taskEntrySelectionKey } from '../../data/taskReference';
import { DetailPanel } from './DetailPanel';
import { enclosure,seedTaskEntries,taskEntry,taskReference,taskSummary } from './test-utils';

describe('DetailPanel on-demand task body (task 11)', () => {
  it('loads the complete task body before mounting reader ancillary requests', async () => {
    const summary = taskEntry({
      id: '1',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('agents-remember/260712_reader-priority/01_reader.json'),
      title: 'Reader priority',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        objective: 'Summary objective.',
        steps: [
          {
            id: 'S1',
            title: 'Keep the summary visible',
            status: 'inProgress',
            substeps: [],
            noteTruncated: false,
            outcomeTruncated: false,
          },
        ],
      }),
    });
    summary.worktrees = [
      enclosure({
        enclosure: '/contracts/reader-priority',
        leafId: summary.id,
        taskId: 'READER-PRIORITY-MASTER',
        taskName: '260712_reader-priority',
        worktreeGroup: '/worktrees/reader-priority-ar',
      }),
    ];
    seedTaskEntries([summary]);
    let resolveBody!: (response: Response) => void;
    const pendingBody = new Promise<Response>((resolve) => {
      resolveBody = resolve;
    });
    const requested: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string): Promise<Response> => {
        requested.push(url);
        if (url.startsWith('/api/task-document')) return pendingBody;
        if (url.startsWith('/api/notes/folder')) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                repo: 'repo-a',
                master: '260712_reader-priority',
                path: '',
                entries: [],
                total: 0,
                offset: 0,
                limit: 200,
                remaining: 0,
                exists: true,
                reason: null,
              }),
              { status: 200 },
            ),
          );
        }
        if (url.startsWith('/api/notes/resolve')) {
          return Promise.resolve(
            new Response(JSON.stringify({ repo: 'repo-a', master: 'm', resolutions: [] }), {
              status: 200,
            }),
          );
        }
        if (url.startsWith('/api/role-launch/reports')) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                repo: 'repo-a',
                master: 'm',
                doc: '',
                reports: [],
                total: 0,
                explorer: null,
              }),
              { status: 200 },
            ),
          );
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({
              counters: {
                code: { files: 0, insertions: 0, deletions: 0 },
                memory: { files: 0, insertions: 0, deletions: 0 },
              },
            }),
            { status: 200 },
          ),
        );
      }),
    );

    const { getByText, queryByText } = render(
      <DetailPanel
        selectedId="taskdoc:agents-remember/260712_reader-priority/01_reader.json"
        onOpenChangeSet={vi.fn()}
      />,
    );

    expect(getByText('Summary objective.')).toBeTruthy();
    expect(getByText('Loading complete task document…')).toBeTruthy();
    await waitFor(() => expect(requested).toHaveLength(1));
    expect(requested[0]).toContain('/api/task-document');
    expect(requested.some((url) => url.startsWith('/api/changeset/'))).toBe(false);
    expect(requested.some((url) => url.startsWith('/api/notes/folder'))).toBe(false);

    resolveBody(
      new Response(JSON.stringify({ ...summary.summary, objective: 'Complete objective.' }), {
        status: 200,
      }),
    );

    await waitFor(() => expect(getByText('Complete objective.')).toBeTruthy());
    expect(queryByText('Loading complete task document…')).toBeNull();
    await waitFor(() => {
      expect(requested.some((url) => url.startsWith('/api/changeset/'))).toBe(true);
      expect(requested.some((url) => url.startsWith('/api/notes/folder'))).toBe(true);
    });
  });

  it('renders the complete on-demand task-document body while retaining its summary', async () => {
    const summary = taskEntry({
      id: '1',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('agents-remember/260628_operations-integration/16_reader.json'),
      title: 'Reader residual',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        objective: 'Summary objective.',
        steps: [
          {
            id: 'S1',
            title: 'Render once',
            status: 'inProgress',
            substeps: [],
            noteTruncated: false,
            outcomeTruncated: false,
          },
        ],
      }),
    });
    seedTaskEntries([summary]);
    const body = {
      ...summary.summary,
      objective: 'Complete objective.',
      requirements: ['Show the complete body.'],
      design: 'Hydrate the visible reader first.',
      codeExamples: [
        {
          id: 'E1',
          title: 'Body priority',
          distinctChange: 'Delay ancillary requests.',
          why: "The complete task is the reader's primary content.",
          language: 'tsx',
          snippet: "bodyState !== 'loading'",
        },
      ],
      decisions: [
        {
          at: '2026-07-10',
          decision: 'Use the on-demand body.',
          rationale: 'It is authoritative.',
          product_record: false,
        },
      ],
      openQuestions: ['Does every reader entry path share this state?'],
      references: ['notes/reader.md'],
      sections: [{ kind: 'freeform', heading: 'Evidence', body: 'The live request was starved.' }],
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.startsWith('/api/task-document')) {
          return new Response(JSON.stringify(body), { status: 200 });
        }
        return new Response(
          JSON.stringify({
            counters: {
              code: { files: 0, insertions: 0, deletions: 0 },
              memory: { files: 0, insertions: 0, deletions: 0 },
            },
          }),
          { status: 200 },
        );
      }),
    );

    const { container, getByTestId, getByText, getAllByText } = render(
      <DetailPanel selectedId={taskEntrySelectionKey(summary.key)} />,
    );
    expect(getByText('Summary objective.')).toBeTruthy();
    await waitFor(() => expect(getByText('Complete objective.')).toBeTruthy());
    // the head strip names the primary requirement; the block holds the whole list
    const requirements = within(
      container.querySelector('[data-block="requirements"]') as HTMLElement,
    );
    expect(requirements.getByText('Show the complete body.')).toBeTruthy();
    expect(getByText('Hydrate the visible reader first.')).toBeTruthy();
    expect(getByText('E1 — Body priority')).toBeTruthy();
    expect(getByText('Use the on-demand body.')).toBeTruthy();
    expect(getByText('Does every reader entry path share this state?')).toBeTruthy();
    // the free-form section stands as one Further-notes line with its own view
    expect(getByText('Evidence')).toBeTruthy();
    fireEvent.click(getByTestId('further-open-0'));
    expect(getByText('The live request was starved.')).toBeTruthy();
    fireEvent.click(getByTestId('doc-view-back'));
    expect(getByText('notes/reader.md')).toBeTruthy();
    expect(getAllByText('Render once')).toHaveLength(1);
  });

  it('shows the available summary when the on-demand task-document body is absent', async () => {
    const doc = taskEntry({
      id: '1',
      title: 'doc',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('agents-remember/260628_operations-integration/17_reader.json'),
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        objective: 'Available summary objective.',
      }),
    });
    seedTaskEntries([doc]);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.startsWith('/api/task-document')) {
          return new Response(JSON.stringify({}), { status: 404 });
        }
        return new Response(JSON.stringify({}), { status: 200 });
      }),
    );

    const { getByText } = render(<DetailPanel selectedId={taskEntrySelectionKey(doc.key)} />);
    await waitFor(() =>
      expect(
        getByText('Full task document details are unavailable; showing the available summary.'),
      ).toBeTruthy(),
    );
    expect(getByText('Available summary objective.')).toBeTruthy();
  });

  it('reuses an unchanged task body and refetches when its revision changes', async () => {
    const summary = taskEntry({
      id: '1',
      title: 'doc',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('agents-remember/260712_reader-priority/02_cache.json'),
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        bodyRevision: 'revision-1',
        objective: 'Summary objective.',
      }),
    });
    seedTaskEntries([summary]);
    const fetchMock = vi.fn(async (url: string) => {
      if (url.startsWith('/api/task-document')) {
        return new Response(
          JSON.stringify({ ...summary.summary, objective: 'Complete objective.' }),
          { status: 200 },
        );
      }
      if (url.includes('/api/role-launch/reports')) {
        // The server contract names the report rows; the generic catch-all
        // below is not a well-formed reports payload.
        return new Response(JSON.stringify({ reports: [], total: 0, explorer: null }), {
          status: 200,
        });
      }
      return new Response(
        JSON.stringify({
          repo: 'repo-a',
          master: '260712_reader-priority',
          notes: [],
          truncated: false,
        }),
        { status: 200 },
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    const selection = taskEntrySelectionKey(summary.key);
    const { getByText, rerender } = render(<DetailPanel selectedId={selection} />);
    await waitFor(() => expect(getByText('Complete objective.')).toBeTruthy());
    rerender(<DetailPanel selectedId={selection} />);
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/task-document')),
    ).toHaveLength(1);

    seedTaskEntries([
      { ...summary, summary: taskSummary({ ...summary.summary, bodyRevision: 'revision-2' }) },
    ]);
    rerender(<DetailPanel selectedId={selection} />);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/task-document')),
      ).toHaveLength(2),
    );
  });
});
