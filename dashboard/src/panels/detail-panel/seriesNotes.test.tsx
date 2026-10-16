import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedTaskEntries, stubNotes, taskEntry, taskSummary, taskReference } from './test-utils';

describe('DetailPanel task notes (L126)', () => {
  const leafPath = 'agents-remember/260703_agent-orchestration/09_notes-dashboard.json';

  it("shows the Notes line for the doc's OWN series and resolves a notes reference to a link", async () => {
    const fn = stubNotes(
      [
        { name: 'friction-ledger.md', path: 'friction-ledger.md', size: 10, language: 'markdown' },
        {
          name: '260703-L1-worker-report.md',
          path: 'reports/260703-L1-worker-report.md',
          size: 10,
          language: 'markdown',
        },
      ],
      'the **ledger** body',
    );
    const doc = taskEntry({
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference(leafPath),
      id: '260703-L9',
      title: 'Notes on the dashboard',
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        references: [
          'notes/friction-ledger.md (F-M — the finding this leaf closes)',
          'mcp/src/agents_remember/serving/files.py (the confinement idiom to reuse)',
        ],
      }),
    });
    seedTaskEntries([doc]);
    const onOpenNotes = vi.fn();
    const view = render(
      <DetailPanel selectedId={`taskdoc:${leafPath}`} onOpenNotes={onOpenNotes} />,
    );

    // The folder is fetched for the doc's OWN series (repo + master derived from the doc node);
    // one resolution request carries the doc's references. No list is ever drawn.
    await view.findByTestId('notes-root-count');
    const urls = (fn.mock.calls as unknown as string[][]).map((c) => c[0]);
    expect(
      urls.some(
        (url) =>
          url ===
          '/api/notes/folder?repo=agents-remember&master=260703_agent-orchestration&path=&offset=0',
      ),
    ).toBe(true);
    expect(view.queryByText('reports/260703-L1-worker-report.md')).toBeNull();

    // The notes-file reference is an openable link into the reader; the code-path reference stays
    // plain text. Reading itself happens in the reader takeover, so the click opens it.
    fireEvent.click(await view.findByTestId('note-ref-1'));
    expect(onOpenNotes).toHaveBeenCalledWith({
      kind: 'notes',
      repo: 'agents-remember',
      master: '260703_agent-orchestration',
      path: 'friction-ledger.md',
    });
    expect(view.queryByTestId('note-ref-2')).toBeNull();
  });

  it("shows the Notes line with the doc's own references on a master overview", async () => {
    stubNotes([{ name: 'design.md', path: 'design.md', size: 10, language: 'markdown' }]);
    const master = taskEntry({
      id: '1',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference('agents-remember/260703_agent-orchestration/task.json'),
      title: 'Agent Orchestration',
      summary: taskSummary({
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'master',
        kind: 'master',
        objective: 'Master objective.',
      }),
    });
    seedTaskEntries([master]);
    const view = render(
      <DetailPanel selectedId="taskdoc:agents-remember/260703_agent-orchestration/task.json" />,
    );
    expect((await view.findByTestId('notes-root-count')).textContent).toContain(
      '1 entry in the top folder',
    );
    // A master page shows no report list.
    expect(view.queryByTestId('task-reports-empty')).toBeNull();
  });
});
