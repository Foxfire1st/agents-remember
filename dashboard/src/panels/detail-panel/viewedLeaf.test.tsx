import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedSeries, seedTaskEntries, taskEntry, taskSummary, taskReference } from './test-utils';

describe('DetailPanel viewed-task reporting', () => {
  it("reports the drilled leaf's canonical task document and presentation key", () => {
    seedSeries();
    const onViewTask = vi.fn();
    const { getByTestId } = render(
      <DetailPanel selectedId="taskdoc:repo-a/series/task.json" onViewTask={onViewTask} />,
    );
    // Master overview: no single leaf is shown.
    expect(onViewTask).toHaveBeenLastCalledWith({
      taskDocumentRef: { repository: 'repo-a', path: 'series/task.json' },
    });

    // Drill into the master's sub-task → the leaf's qualified id (repo/master/leafId), not the master.
    fireEvent.click(getByTestId('subtask-open-1'));
    expect(onViewTask).toHaveBeenLastCalledWith(
      expect.objectContaining({
        taskDocumentRef: { repository: 'repo-a', path: 'series/01_first.json' },
        leafKey: 'repo-a/series/1',
      }),
    );

    // Returning to the master index clears the leaf slot again.
    fireEvent.click(getByTestId('master-parent-link'));
    expect(onViewTask).toHaveBeenLastCalledWith({
      taskDocumentRef: { repository: 'repo-a', path: 'series/task.json' },
    });
  });

  it("reports a directly-opened leaf doc's qualified key", () => {
    const leafPath = 'agents-remember/260628_operations-integration/05_sidebar.json';
    const doc = taskEntry({
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference(leafPath),
      id: '260628-L5',
      title: 'Sidebar chat attachment',
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
      }),
    });
    seedTaskEntries([doc]);
    const onViewTask = vi.fn();
    render(<DetailPanel selectedId={`taskdoc:${leafPath}`} onViewTask={onViewTask} />);
    expect(onViewTask).toHaveBeenLastCalledWith({
      taskDocumentRef: {
        repository: 'agents-remember',
        path: '260628_operations-integration/05_sidebar.json',
      },
      leafKey: 'agents-remember/260628_operations-integration/260628-L5',
    });
  });
});

// The notes API stub lives in ./test-utils (stubNotes): /api/notes/folder answers one folder,
// /api/notes/resolve answers the seeded resolutions, /api/notes/read a fixed body.
// Everything else (e.g. change-set counters) answers a bare ok so unrelated fetches stay inert.
