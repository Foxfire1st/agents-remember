import { readFileSync } from 'node:fs';
import path from 'node:path';

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import { GALLERY } from '../dev/fixtures';
import { AttentionQueue } from '../panels/AttentionQueue';
import { DetailPanel } from '../panels/detail-panel/DetailPanel';
import { taskTree } from '../panels/detail-panel/test-utils';
import { EngineRoom } from '../panels/EngineRoom';
import { EventRiver } from '../panels/EventRiver';
import { FileViewer } from '../panels/file-viewer/FileViewer';
import { LifecycleList } from '../panels/lifecycle-list/LifecycleList';
import { NotesReaderViewer } from '../panels/notes-reader/NotesReaderViewer';
import {
  analytics,
  taskEntry,
  taskSummary,
  projection as wireProjection,
} from '../test/fixtures/wire';
import type {
  Analytics,
  EnclosureNode,
  TaskEntry,
  TaskSummary,
  WorkspaceProjection,
} from '../types/projection';
import { CockpitShell, type CockpitView } from './Cockpit';
import { DocumentChat } from './document-chat/DocumentChat';

function seed(stateName: string) {
  const fixture = GALLERY.find((entry) => entry.name === stateName);
  if (!fixture) throw new Error(`fixture not found: ${stateName}`);
  dashboardStore.getState().applySnapshot(fixture.projection);
}

function entry(
  id: string,
  title: string,
  file: string,
  summary: Partial<TaskSummary> = {},
  over: Partial<TaskEntry> = {},
): TaskEntry {
  return taskEntry({
    id,
    title,
    reference: { repository: 'repo-a', path: `ops/${file}` },
    stage: 'started',
    activity: 'works',
    summary: taskSummary(summary),
    ...over,
  });
}

function seedDrillableMaster() {
  const master = entry('master-x', 'Ops Master', 'task.json', {
    kind: 'master',
    form: 'master',
    objective: 'Master objective.',
  });
  const leaf = entry('leaf-one', 'Leaf One', '01_leaf.json', { objective: 'Leaf objective.' });
  dashboardStore.getState().applySnapshot(wireProjection({ tasks: taskTree(master, [leaf]) }));
}

beforeEach(() => vi.stubGlobal('CSS', { escape: (value: string) => value }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
  window.localStorage.clear(); // the rail toggle now persists its choice; isolate it between tests
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function liveEnclosure(leafId: string, lifecycleId: string, enclosureId = leafId): EnclosureNode {
  return {
    enclosure: `/contracts/${enclosureId}`,
    enclosureId,
    leafId,
    taskRoot: '/tasks/repo-a/ops',
    taskId: 'ops-master',
    taskName: 'ops',
    repoName: 'repo-a',
    lifecycleId,
    worktreeGroup: `/worktrees/${enclosureId}`,
    humanReviewStatus: 'pending-review',
    closeoutStatus: 'not-started',
    integrationStatus: 'not-started',
    cleanup: 'pending',
    codeWorktreeExists: true,
    memoryWorktreeExists: true,
    actions: [],
  };
}

function taskReaderProjection(): WorkspaceProjection {
  const master = entry('ops-master', 'Body Priority Master', 'task.json', {
    kind: 'master',
    form: 'master',
    bodyRevision: 'master-r1',
    objective: 'Master summary.',
  });
  const directLeaf = entry(
    'direct-leaf',
    'Direct Leaf Reader',
    '01_direct-leaf.json',
    {
      bodyRevision: 'direct-r1',
      objective: 'Direct leaf summary.',
    },
    { worktrees: [liveEnclosure('direct-leaf', 'LC-DIRECT')] },
  );
  const drilledLeaf = entry('drilled-leaf', 'Drilled Reader', '02_drilled.json', {
    bodyRevision: 'drilled-r1',
    objective: 'Drilled summary.',
  });
  const bound = entry(
    'bound-doc',
    'Worktree Bound Reader',
    '03_bound.json',
    {
      bodyRevision: 'bound-r1',
      objective: 'Bound summary.',
    },
    { worktrees: [liveEnclosure('runtime-only', 'LC-BOUND')] },
  );
  return wireProjection({ tasks: [...taskTree(master, [drilledLeaf]), directLeaf, bound] });
}

function refreshTaskSummaries(): void {
  for (const task of Object.values(dashboardStore.getState().tasks)) {
    dashboardStore.getState().applyDelta('task', { ...task, stepsDone: task.stepsDone + 1 });
  }
}

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
  });

function stubTaskReaderFetch(
  projection: WorkspaceProjection,
  gates: Map<string, ReturnType<typeof deferred>>,
  completeObjective: Map<string, string>,
) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.startsWith('/api/task-document')) {
      const path = new URL(url, 'http://dashboard.test').searchParams.get('path') ?? '';
      await gates.get(path)?.promise;
      const summary = projection.tasks.find((task) => task.key === path)?.summary;
      return answer({ ...summary, objective: completeObjective.get(path) });
    }
    if (url === '/api/files/repos') return answer({ repos: [] });
    if (url.startsWith('/api/changeset/'))
      return answer({
        counters: {
          code: { files: 0, insertions: 0, deletions: 0 },
          memory: { files: 0, insertions: 0, deletions: 0 },
        },
      });
    if (url.startsWith('/api/notes/folder'))
      return answer({
        repo: 'repo-a',
        master: 'ops',
        path: '',
        entries: [],
        total: 0,
        offset: 0,
        limit: 200,
        remaining: 0,
        exists: true,
        reason: null,
      });
    return answer({});
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('Operations click-to-detail body hydration', () => {
  it('renders complete bodies for direct, master, drilled, and worktree-bound paths during entry refreshes', async () => {
    const projection = taskReaderProjection();
    dashboardStore.getState().applySnapshot(projection);
    const gates = new Map(projection.tasks.map((doc) => [doc.key, deferred()]));
    const completeObjective = new Map(
      projection.tasks.map((doc) => [doc.key, `Complete ${doc.title} objective.`]),
    );
    const fetchMock = stubTaskReaderFetch(projection, gates, completeObjective);

    const view = render(<CockpitShell initialView="operations" />);
    const assertClickHydratesOnce = async (label: string, path: string) => {
      fireEvent.click(view.getByText(label));
      await waitFor(() =>
        expect(
          fetchMock.mock.calls.filter(
            ([url]) =>
              String(url).startsWith('/api/task-document') &&
              String(url).includes(encodeURIComponent(path)),
          ),
        ).toHaveLength(1),
      );
      act(refreshTaskSummaries);
      await Promise.resolve();
      expect(
        fetchMock.mock.calls.filter(
          ([url]) =>
            String(url).startsWith('/api/task-document') &&
            String(url).includes(encodeURIComponent(path)),
        ),
      ).toHaveLength(1);
      gates.get(path)?.resolve();
      await waitFor(() => expect(view.getByText(completeObjective.get(path) ?? '')).toBeTruthy());
    };

    await assertClickHydratesOnce('Direct Leaf Reader', 'repo-a/ops/01_direct-leaf.json');
    await assertClickHydratesOnce('Body Priority Master', 'repo-a/ops/task.json');

    fireEvent.click(view.getByTestId('subtask-open-1'));
    const drilledPath = 'repo-a/ops/02_drilled.json';
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(
          ([url]) =>
            String(url).startsWith('/api/task-document') &&
            String(url).includes(encodeURIComponent(drilledPath)),
        ),
      ).toHaveLength(1),
    );
    act(refreshTaskSummaries);
    await Promise.resolve();
    expect(
      fetchMock.mock.calls.filter(
        ([url]) =>
          String(url).startsWith('/api/task-document') &&
          String(url).includes(encodeURIComponent(drilledPath)),
      ),
    ).toHaveLength(1);
    gates.get(drilledPath)?.resolve();
    await waitFor(() =>
      expect(view.getByText(completeObjective.get(drilledPath) ?? '')).toBeTruthy(),
    );

    await assertClickHydratesOnce('Worktree Bound Reader', 'repo-a/ops/03_bound.json');
  });

  it("discards task A's late body after selecting task B and hydrates B exactly once", async () => {
    const projection = taskReaderProjection();
    dashboardStore.getState().applySnapshot(projection);
    const gates = new Map(projection.tasks.map((doc) => [doc.key, deferred()]));
    const completeObjective = new Map(
      projection.tasks.map((doc) => [doc.key, `Complete ${doc.title} objective.`]),
    );
    const fetchMock = stubTaskReaderFetch(projection, gates, completeObjective);
    const view = render(<CockpitShell initialView="operations" />);
    const taskAPath = 'repo-a/ops/01_direct-leaf.json';
    const taskBPath = 'repo-a/ops/03_bound.json';
    const requestsFor = (path: string) =>
      fetchMock.mock.calls.filter(
        ([url]) =>
          String(url).startsWith('/api/task-document') &&
          String(url).includes(encodeURIComponent(path)),
      );

    fireEvent.click(view.getByText('Direct Leaf Reader'));
    await waitFor(() => expect(requestsFor(taskAPath)).toHaveLength(1));

    fireEvent.click(view.getByText('Worktree Bound Reader'));
    await waitFor(() => expect(requestsFor(taskBPath)).toHaveLength(1));
    expect(view.getByText('Bound summary.')).toBeTruthy();

    await act(async () => {
      gates.get(taskAPath)?.resolve();
      await Promise.resolve();
    });

    expect(view.queryByText(completeObjective.get(taskAPath) ?? '')).toBeNull();
    expect(view.queryByText(completeObjective.get(taskBPath) ?? '')).toBeNull();
    expect(view.getByText('Bound summary.')).toBeTruthy();
    expect(requestsFor(taskBPath)).toHaveLength(1);

    gates.get(taskBPath)?.resolve();
    await waitFor(() =>
      expect(view.getByText(completeObjective.get(taskBPath) ?? '')).toBeTruthy(),
    );
    expect(view.queryByText(completeObjective.get(taskAPath) ?? '')).toBeNull();
    expect(requestsFor(taskBPath)).toHaveLength(1);
  });
});

describe('workspace rollup reads the native task counts', () => {
  it('shows the published counts, and once that host activity is unavailable', () => {
    dashboardStore.getState().applySnapshot(
      wireProjection({
        taskCounts: {
          planned: 5,
          started: 9,
          completed: 8,
          aborted: 6,
          works: 3,
          paused: 2,
          unknown: 4,
        },
      }),
    );
    const view = render(<CockpitShell initialView="operations" />);
    const text = view.getByTestId('task-counts').textContent ?? '';
    for (const word of [
      '5 planned',
      '9 started',
      '8 completed',
      '6 aborted',
      'host activity unavailable',
    ])
      expect(text).toContain(word);
    // Unknown activity means the whole host observation is unknown: works and paused are not
    // known then, and nothing per task repeats it.
    expect(text).not.toMatch(/works|paused|unknown|running|blocked|awaiting/);
    expect(text.match(/host activity unavailable/g)).toHaveLength(1);
  });

  it('says host activity is unavailable only while an unknown activity is published', () => {
    dashboardStore.getState().applySnapshot(
      wireProjection({
        taskCounts: {
          planned: 0,
          started: 1,
          completed: 0,
          aborted: 0,
          works: 1,
          paused: 0,
          unknown: 0,
        },
      }),
    );
    const view = render(<CockpitShell initialView="operations" />);
    const text = view.getByTestId('task-counts').textContent ?? '';
    expect(text).toContain('1 works');
    expect(text).toContain('0 paused');
    expect(text).not.toContain('host activity unavailable');
    act(() =>
      dashboardStore.setState({
        taskCounts: {
          ...dashboardStore.getState().taskCounts,
          unknown: 1,
        },
      }),
    );
    expect(view.getByTestId('task-counts').textContent).toContain('host activity unavailable');
    expect(view.getByTestId('task-counts').textContent).not.toContain('works');
    act(() =>
      dashboardStore.setState({
        taskCounts: {
          ...dashboardStore.getState().taskCounts,
          unknown: 0,
        },
      }),
    );
    expect(view.getByTestId('task-counts').textContent).not.toContain('host activity unavailable');
  });
});

describe('no scanline layer over the cockpit (MIK-R127, D223)', () => {
  // The fixed layer of dark lines over the whole application cut every letter on each third
  // pixel row. It is removed with its rule and its animation; nothing brings it back.
  it('draws no layer over the application and keeps no rule for one', () => {
    seed('calm');
    const { container } = render(<CockpitShell initialView="operations" />);
    expect(container.querySelector('.crt-overlay')).toBeNull();
    const css = readFileSync(path.resolve(__dirname, '../index.css'), 'utf8');
    expect(css).not.toContain('.crt-overlay');
    expect(css).not.toMatch(/@keyframes\s+flicker/);
    expect(css).not.toContain('repeating-linear-gradient');
    // the alarm pulse and the calm freeze are not the layer's and stay
    expect(css).toMatch(/@keyframes\s+pulse\b/);
    expect(css).toContain('html[data-effects="off"]');
  });
});

describe('serving-build stamp (260703-L15 — the July-4 ghost-process lesson)', () => {
  it("renders the muted stamp from the snapshot's servingBuild (commit-first)", () => {
    const fixture = GALLERY.find((entry) => entry.name === 'engine-fleet');
    if (!fixture) throw new Error('fixture not found: engine-fleet');
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: { version: '9.9.9', commit: 'abc1234', bootedAt: '2026-07-07T05:00:00Z' },
    });
    const { getByTestId } = render(<CockpitShell initialView="operations" />);
    expect(getByTestId('serving-build').textContent).toContain('abc1234');
    expect(getByTestId('serving-build').textContent).toContain('up ');
  });

  it('marks a serving checkout with uncommitted code as hash* (additive wire flag)', () => {
    const fixture = GALLERY.find((entry) => entry.name === 'engine-fleet');
    if (!fixture) throw new Error('fixture not found: engine-fleet');
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: {
        version: '9.9.9',
        commit: '38c3fd8',
        bootedAt: '2026-07-07T05:00:00Z',
        dirty: true,
      },
    });
    const { getByTestId } = render(<CockpitShell initialView="operations" />);
    expect(getByTestId('serving-build').textContent).toContain('38c3fd8*');
    expect(getByTestId('serving-build').textContent).not.toContain('dirty');
    expect(getByTestId('serving-build').title).toContain('@ 38c3fd8*');
    expect(getByTestId('serving-build').title).toContain('dirty — serving uncommitted code');
  });

  it('falls back to the package version off-checkout; renders nothing without a stamp', () => {
    const fixture = GALLERY.find((entry) => entry.name === 'engine-fleet');
    if (!fixture) throw new Error('fixture not found: engine-fleet');
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: { version: '9.9.9', bootedAt: '2026-07-07T05:00:00Z' },
    });
    const first = render(<CockpitShell initialView="operations" />);
    expect(first.getByTestId('serving-build').textContent).toContain('v9.9.9');
    first.unmount();

    dashboardStore.getState().reset();
    seed('engine-fleet'); // no servingBuild on the wire (a legacy server)
    const second = render(<CockpitShell initialView="operations" />);
    expect(second.queryByTestId('serving-build')).toBeNull(); // absent stamp: nothing, never faked
  });

  it('surfaces a stale client bundle in the tooltip (no redundant reload action)', () => {
    const fixture = GALLERY.find((entry) => entry.name === 'engine-fleet');
    if (!fixture) throw new Error('fixture not found: engine-fleet');
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: {
        version: '9.9.9',
        bootedAt: '2026-07-07T05:00:00Z',
        dashboardBuild: 'different-dashboard-build', // ≠ test CLIENT_DASHBOARD_BUILD ("test-dashboard-build")
      },
    });
    const stale = render(<CockpitShell initialView="operations" />);
    expect(stale.getByTestId('serving-build').dataset.clientBuildCurrent).toBe('false');
    // A proven client/serving mismatch must be visible to the operator, not silently swallowed
    // into the invisible data attribute — the hoverable stamp tooltip carries the reload cue.
    expect(stale.getByTestId('serving-build').title).toContain(
      'client bundle differs from the serving build — reload',
    );
    expect(stale.queryByTestId('reload-dashboard-client')).toBeNull();
    stale.unmount();

    dashboardStore.getState().reset();
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: {
        version: '9.9.9',
        bootedAt: '2026-07-07T05:01:00Z',
        dashboardBuild: 'test-dashboard-build',
      },
    });
    const current = render(<CockpitShell initialView="operations" />);
    expect(current.getByTestId('serving-build').dataset.clientBuildCurrent).toBe('true');
    // A real match renders no mismatch cue — never fabricate a discrepancy that is not there.
    expect(current.getByTestId('serving-build').title).not.toContain('client bundle differs');
    expect(current.queryByTestId('reload-dashboard-client')).toBeNull();
  });

  it('adds no client-mismatch cue when a legacy server advertises no comparable bundle identity', () => {
    // `dashboardBuild` absent → clientMatchesServingBuild returns null (unknown). Honesty doctrine:
    // an unknown state must not assert a mismatch any more than it may fabricate a match.
    const fixture = GALLERY.find((entry) => entry.name === 'engine-fleet');
    if (!fixture) throw new Error('fixture not found: engine-fleet');
    dashboardStore.getState().applySnapshot({
      ...fixture.projection,
      servingBuild: { version: '9.9.9', bootedAt: '2026-07-07T05:00:00Z' },
    });
    const { getByTestId } = render(<CockpitShell initialView="operations" />);
    expect(getByTestId('serving-build').dataset.clientBuildCurrent).toBe('unknown');
    expect(getByTestId('serving-build').title).not.toContain('client bundle differs');
  });
});

describe('CockpitShell full-bleed machine-map views (5f S1)', () => {
  it('rails Operations and gives File Viewer the full width', () => {
    seed('engine-fleet');
    const { container, getByRole } = render(<CockpitShell initialView="operations" />);

    // Explicit Operations view: the railed 3-column shell.
    expect(container.querySelector('.shell__body')?.getAttribute('data-fullbleed')).toBe('false');
    expect(container.querySelector('.rail--left')).not.toBeNull();
    expect(container.querySelector('.rail--right')).not.toBeNull();

    // Switch to File Viewer via the mode bar.
    fireEvent.click(getByRole('radio', { name: 'File Viewer' }));

    // Full-bleed: both rails stay mounted but hidden (display:none + aria-hidden), the grid drops
    // to a single full-width column, and the room's own 3-zone layout (header + boot/diagnostics
    // zone) is present. Keep-alive: re-entry no longer remounts the rails.
    expect(container.querySelector('.shell__body')?.getAttribute('data-fullbleed')).toBe('true');
    const railLeft = container.querySelector('.rail--left') as HTMLElement;
    const railRight = container.querySelector('.rail--right') as HTMLElement;
    expect(railLeft).not.toBeNull();
    expect(railRight).not.toBeNull();
    expect(railLeft.style.display).toBe('none');
    expect(railRight.style.display).toBe('none');
    expect(railLeft.getAttribute('aria-hidden')).toBe('true');
    expect(railRight.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('[data-testid="file-viewer"]')).not.toBeNull();
  });

  it.each<[CockpitView, string, boolean]>([
    ['memory', 'memory-mirror', false],
    ['hangar', 'hangar', false],
    ['engine', 'engine-room', true],
    ['topology', 'topology', true],
  ])('preserves the %s layout through initialView', (initialView, testid, fullBleed) => {
    seed('engine-fleet');
    const { container, getByTestId } = render(<CockpitShell initialView={initialView} />);
    expect(getByTestId(testid)).toBeTruthy();
    expect(container.querySelector('main')?.dataset.view).toBe(initialView);
    expect(container.querySelector('.shell__body')?.getAttribute('data-fullbleed')).toBe(
      String(fullBleed),
    );
    for (const selector of ['.rail--left', '.rail--right']) {
      const rail = container.querySelector<HTMLElement>(selector)!;
      expect(rail.style.display).toBe(fullBleed ? 'none' : 'flex');
      expect(rail.getAttribute('aria-hidden')).toBe(String(fullBleed));
    }
  });
});

describe('rail keep-alive across view switches (260721 F1)', () => {
  it('keeps both rail asides mounted (hidden, never unmounted) on a full-bleed view', () => {
    seed('engine-fleet');
    const { container, getByRole } = render(<CockpitShell initialView="operations" />);

    // Railed Operations view: the two aside nodes (and the river inside the right one) that we
    // watch across the switch.
    const railLeft = container.querySelector('.rail--left') as HTMLElement;
    const railRight = container.querySelector('.rail--right') as HTMLElement;
    const river = railRight.querySelector('[data-testid="event-river"]');
    expect(railLeft.style.display).toBe('flex');
    expect(railLeft.getAttribute('aria-hidden')).toBe('false');
    expect(river).not.toBeNull();

    // Switch to the full-width File Viewer: the asides are hidden, NOT unmounted (same nodes, so
    // rail scroll/collapsed state survives); the grid still drops to the single full-width column.
    fireEvent.click(getByRole('radio', { name: 'File Viewer' }));
    expect(container.querySelector('.shell__body')?.getAttribute('data-fullbleed')).toBe('true');
    expect(container.querySelector('.rail--left')).toBe(railLeft);
    expect(container.querySelector('.rail--right')).toBe(railRight);
    expect(railLeft.style.display).toBe('none');
    expect(railRight.style.display).toBe('none');
    expect(railLeft.getAttribute('aria-hidden')).toBe('true');
    expect(railRight.getAttribute('aria-hidden')).toBe('true');
    expect(railRight.querySelector('[data-testid="event-river"]')).toBe(river);

    // Back to Operations: the exact same asides re-show — no remount, so no fresh
    // AttentionQueue/LifecycleList/EventRiver mount cost on entry.
    fireEvent.click(getByRole('radio', { name: 'Operations' }));
    expect(container.querySelector('.shell__body')?.getAttribute('data-fullbleed')).toBe('false');
    expect(container.querySelector('.rail--left')).toBe(railLeft);
    expect(container.querySelector('.rail--right')).toBe(railRight);
    expect(railLeft.style.display).toBe('flex');
    expect(railLeft.getAttribute('aria-hidden')).toBe('false');
    expect(railRight.querySelector('[data-testid="event-river"]')).toBe(river);
  });
});

describe('right-rail River⇄Chat toggle (L5 S2; MIK-R122 keep-alive)', () => {
  it('hides the single-instance chat behind the Event River instead of unmounting it', () => {
    seed('engine-fleet');
    const { container, getByTestId } = render(<CockpitShell initialView="operations" />);

    const railRight = container.querySelector('.rail--right');
    expect(railRight).not.toBeNull();
    // Default = the Event River; the chat surface stays mounted but hidden.
    expect(railRight?.querySelector('[data-testid="event-river"]')).not.toBeNull();
    const chat = railRight?.querySelector('[data-testid="document-chat"]');
    expect(chat).not.toBeNull();
    expect(
      railRight?.querySelector('[data-testid="rail-chat-keepalive"]')?.getAttribute('aria-hidden'),
    ).toBe('true');

    // Toggle to Chat: the river is gone, the same chat instance shows.
    fireEvent.click(getByTestId('rail-toggle-chat'));
    expect(railRight?.querySelector('[data-testid="document-chat"]')).toBe(chat);
    expect(railRight?.querySelector('[data-testid="event-river"]')).toBeNull();

    // Toggle back to River: the river returns, the same chat hides again.
    fireEvent.click(getByTestId('rail-toggle-river'));
    expect(railRight?.querySelector('[data-testid="event-river"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="document-chat"]')).toBe(chat);
    expect(
      railRight?.querySelector('[data-testid="rail-chat-keepalive"]')?.getAttribute('aria-hidden'),
    ).toBe('true');
  });

  it('remembers the rail choice across a window refresh (localStorage)', () => {
    seed('engine-fleet');
    const first = render(<CockpitShell initialView="operations" />);
    // Default = River, then switch to Chat.
    expect(
      first.container.querySelector('.rail--right [data-testid="event-river"]'),
    ).not.toBeNull();
    fireEvent.click(first.getByTestId('rail-toggle-chat'));
    expect(window.localStorage.getItem('cockpit.rail-chat')).toBe('1');
    first.unmount();

    // A fresh mount (the window refresh) restores Chat from localStorage — the river is not shown.
    const second = render(<CockpitShell initialView="operations" />);
    const railRight = second.container.querySelector('.rail--right');
    expect(railRight?.querySelector('[data-testid="document-chat"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="event-river"]')).toBeNull();
  });
});

describe('Operations rails are resizable + persisted', () => {
  it('renders a resize gutter on each rail and applies the persisted widths to the grid', () => {
    window.localStorage.setItem('cockpit.rail-left-w', '430');
    window.localStorage.setItem('cockpit.rail-right-w', '250');
    seed('engine-fleet');
    const { container, getByTestId } = render(<CockpitShell initialView="operations" />);

    // Each rail owns a drag gutter, and the railed grid uses the stored widths (centre takes the rest).
    expect(getByTestId('rail-resize-left')).not.toBeNull();
    expect(getByTestId('rail-resize-right')).not.toBeNull();
    const body = container.querySelector('.shell__body') as HTMLElement;
    expect(body.style.gridTemplateColumns).toBe('430px minmax(380px, 1fr) 250px');
  });

  it('nudges a rail width with the keyboard and persists the new width', () => {
    seed('engine-fleet'); // no stored width -> default 340
    const { getByTestId } = render(<CockpitShell initialView="operations" />);

    fireEvent.keyDown(getByTestId('rail-resize-left'), { key: 'ArrowRight' });
    expect(window.localStorage.getItem('cockpit.rail-left-w')).toBe('364'); // 340 + 24

    // The right rail's gutter is mirror-imaged: ArrowLeft grows it.
    fireEvent.keyDown(getByTestId('rail-resize-right'), { key: 'ArrowLeft' });
    expect(window.localStorage.getItem('cockpit.rail-right-w')).toBe('324'); // 300 + 24
  });
});

describe('rail chat keys by the drilled leaf, not the master (L5 fix 1)', () => {
  it("keys the rail chat by the leaf once a master's sub-task is drilled open", () => {
    seedDrillableMaster();
    const { getByText, getByTestId } = render(<CockpitShell initialView="operations" />);

    // Select the master, then toggle the rail to the chat surface.
    fireEvent.click(getByText('Ops Master'));
    fireEvent.click(getByTestId('rail-toggle-chat'));

    // Master overview shown (no leaf drilled): the rail is NOT blocked — it offers the
    // create-from-anywhere empty state, and the heading carries no leaf id yet (not the master's).
    expect(getByTestId('document-chat')).not.toBeNull();
    expect(getByTestId('document-chat').getAttribute('data-document')).not.toContain('master-x');

    // Drill into the master's sub-task → the rail keys by THAT leaf id, not the master.
    fireEvent.click(getByTestId('subtask-open-1'));
    const heading = getByTestId('document-chat');
    expect(heading.getAttribute('data-document')).toContain('ops/01_leaf.json');
    expect(heading.getAttribute('data-document')).not.toContain('master-x');
  });
});

describe('Operations drill survives a view switch (DetailPanel mount preservation)', () => {
  it('keeps the drilled sub-task open after switching to another tab and back', () => {
    seedDrillableMaster();
    const { getByText, getByRole, getByTestId, queryByTestId } = render(
      <CockpitShell initialView="operations" />,
    );

    // Select the master, then drill into its sub-task → the leaf reader (a breadcrumb back to the
    // master) replaces the sub-task index.
    fireEvent.click(getByText('Ops Master'));
    fireEvent.click(getByTestId('subtask-open-1'));
    expect(getByTestId('master-parent-link').textContent).toContain('Ops Master');
    expect(queryByTestId('subtask-open-1')).toBeNull(); // we're in the reader, not the index

    // Leave Operations for another tab, then come back: the drill is preserved (the panel was hidden,
    // not unmounted), so it does NOT reset to the master overview.
    fireEvent.click(getByRole('radio', { name: 'File Viewer' }));
    fireEvent.click(getByRole('radio', { name: 'Operations' }));
    expect(getByTestId('master-parent-link').textContent).toContain('Ops Master');
    expect(queryByTestId('subtask-open-1')).toBeNull();
  });
});

describe('canonical Chats route: full-bleed keep-alive cockpit (S5)', () => {
  it('directly shows one persistent Role chats pane without an old-chat selector', () => {
    seed('engine-fleet');
    const { container, getByRole, queryByRole } = render(<CockpitShell initialView="operations" />);

    expect(getByRole('radio', { name: 'Operations' }).getAttribute('aria-checked')).toBe('true');
    expect(queryByRole('radio', { name: 'Sessions' })).toBeNull();

    const chats = container.querySelector('[data-testid="role-chats-pane"]');
    expect(chats).not.toBeNull();
    const chatsLayer = chats?.parentElement as HTMLElement;
    expect(chatsLayer.style.display).toBe('none');
    expect(chatsLayer.getAttribute('aria-hidden')).toBe('true');

    fireEvent.click(getByRole('radio', { name: 'Chats' }));
    expect(container.querySelector('.shell__body')?.getAttribute('data-fullbleed')).toBe('true');
    // The left rail stays mounted, hidden (keep-alive), while Chats goes full-bleed.
    expect((container.querySelector('.rail--left') as HTMLElement).style.display).toBe('none');
    expect(container.querySelector('[data-testid="role-chats-pane"]')).toBe(chats);
    expect(queryByRole('tablist', { name: 'Chats mode' })).toBeNull();
    expect(container.querySelector('[data-testid="sessions-view"]')).toBeNull();
    expect(chatsLayer.style.display).toBe('flex');
    expect(chatsLayer.getAttribute('aria-hidden')).toBe('false');
    fireEvent.click(getByRole('radio', { name: 'Operations' }));
    expect(container.querySelector('[data-testid="role-chats-pane"]')).toBe(chats);
    expect(chatsLayer.style.display).toBe('none');
    expect(chatsLayer.getAttribute('aria-hidden')).toBe('true');
  });
});

describe('visible tab contract (MIK-R79)', () => {
  it('opens Chats, orders four tabs, and mounts hidden pages only through injected initialView', () => {
    seed('engine-fleet');
    const view = render(<CockpitShell />);
    expect(
      [...view.getByRole('radiogroup', { name: 'Views' }).querySelectorAll('[role=radio]')].map(
        (row) => row.textContent,
      ),
    ).toEqual(['Chats', 'Operations', 'Knowledge', 'File Viewer']);
    expect(view.container.querySelector('main')?.dataset.view).toBe('chats');
    for (const id of ['engine-room', 'memory-mirror', 'topology', 'hangar'])
      expect(view.queryByTestId(id)).toBeNull();
    cleanup();
    const hidden = render(<CockpitShell initialView="engine" />);
    expect(hidden.getByTestId('engine-room')).toBeTruthy();
    fireEvent.click(hidden.getByRole('radio', { name: 'Chats' }));
    expect(hidden.queryByTestId('engine-room')).toBeNull();
  });
});

describe('persistent layers are exported memoized (260721 tab-switch CPU)', () => {
  // The memo gate is the fix's contract: a shell re-render (every setView) must not reconcile a
  // layer whose props are unchanged. Guard the export SHAPE here so a future refactor can't
  // silently drop the memo; the render-count behavior lives in Cockpit.memo.test.tsx.
  it('exports every persistent layer as a React.memo component', () => {
    const layers: Array<[string, unknown]> = [
      ['DetailPanel', DetailPanel],
      ['EngineRoom', EngineRoom],
      ['FileViewer', FileViewer],
      ['AttentionQueue', AttentionQueue],
      ['LifecycleList', LifecycleList],
      ['EventRiver', EventRiver],
      ['DocumentChat', DocumentChat],
      ['NotesReaderViewer', NotesReaderViewer],
    ];
    for (const [name, component] of layers) {
      expect((component as { $$typeof: symbol }).$$typeof, name).toBe(Symbol.for('react.memo'));
    }
  });
});

describe('the attention queue behind the bell, on the gallery states the browser tests read', () => {
  // e2e/cockpit.spec.ts opens the bell on `/dev/bench`, which renders this shell from these
  // gallery states, and asserts the same on the same test ids. Those tests run in the Dagger
  // suite alone; these run here.
  function openBell(stateName: string) {
    seed(stateName);
    const view = render(<CockpitShell initialView="operations" />);
    fireEvent.click(view.getByTestId('attention-bell'));
    return view;
  }

  it('ranks alarms first (state=alarm)', () => {
    const view = openBell('alarm');
    expect(view.getByTestId('attention-queue').textContent).toContain('waiting');
    expect(view.getAllByTestId('attn-item')[0].textContent).toContain('down');
  });

  it('renders the empty state without items (state=empty)', () => {
    const view = openBell('empty');
    expect(view.getByTestId('attention-queue').textContent).toContain('nothing waiting');
    expect(view.queryAllByTestId('attn-item')).toHaveLength(0);
    fireEvent.keyDown(view.getByTestId('attention-queue'), { key: 'Escape' });
    expect(view.queryByTestId('attention-queue')).toBeNull();
    expect(view.getByRole('heading', { name: 'Detail' })).toBeTruthy();
  });
});

describe('the left rail shows task status and attention severity together', () => {
  function railProjection(attentionQueue: Analytics['attentionQueue']): WorkspaceProjection {
    return wireProjection({
      tasks: [entry('01', 'Handoff Leaf', '01_handoff.json', {}, { activity: 'paused' })],
      analytics: analytics({ attentionQueue }),
    });
  }

  const WARN_ROW: Analytics['attentionQueue'] = [
    {
      id: 'actionable-drift:repo-a',
      kind: 'actionable-drift',
      severity: 'warn',
      lane: 'repo',
      title: '6 actionable drift',
      waitSeconds: 900,
      itemClass: 'operational',
      dismissible: true,
      repoId: 'repo-a',
    },
  ];

  it('keeps a handoff state and a queue warning apart in the one rail that shows both', () => {
    dashboardStore.getState().applySnapshot(railProjection(WARN_ROW));
    const { getByTestId } = render(<CockpitShell initialView="operations" />);

    // The bell of the rail's head opens the queue's layer over the tree.
    fireEvent.click(getByTestId('attention-bell'));
    const stateDot = getByTestId('task-state').firstElementChild;
    const severityDot = getByTestId('attn-severity').firstElementChild;
    expect(stateDot, 'no task status dot in the rail').toBeTruthy();
    expect(severityDot, 'no attention severity dot in the rail').toBeTruthy();
    // Both are amber — the palette groups rather than identifies — so this is the glyph's job.
    expect(stateDot?.outerHTML).not.toBe(severityDot?.outerHTML);
  });

  it('speaks the severity of an attention row into the accessibility tree', () => {
    // The Dot is `aria-hidden`, so the severity lives entirely on its wrapper — and the wrapper
    // used to be a bare `<span aria-label>`. ARIA prohibits naming a `generic`, so that label was
    // in the DOM and in no accessibility tree: `getAttribute("aria-label")` passed while a screen
    // reader user got nothing. Queried BY ROLE AND NAME here, which is the computed tree, and
    // backed by axe, which fails the prohibited attribute outright.
    dashboardStore.getState().applySnapshot(railProjection(WARN_ROW));
    const { getByRole, getByTestId, baseElement } = render(
      <CockpitShell initialView="operations" />,
    );
    // The state dot's label reaches the tree a different way: React Aria gives the row
    // `role="option"`, whose name comes from its content, so the span's label is absorbed into it.
    // Read before the layer opens: an open layer takes the rest of the page out of the tree.
    expect(getByRole('option', { name: /Task progress: paused/ })).toBeTruthy();
    // The bell speaks the queue, and opens the layer the severity lives in.
    fireEvent.click(getByRole('button', { name: 'Attention: 1 waiting' }));
    expect(getByRole('img', { name: 'Severity: warn' })).toBe(
      baseElement.querySelector('[data-testid="attn-severity"]'),
    );
    expect(getByTestId('attention-queue').getAttribute('role')).toBe('dialog');
  });

  it('passes axe on the bell and on the layer the severity label lives in', async () => {
    // The bell and its layer rather than the whole shell: axe walks every node it is given, and
    // these are the subtrees the labels are on. `aria-prohibited-attr` is `serious` and would
    // fail here.
    dashboardStore.getState().applySnapshot(railProjection(WARN_ROW));
    const { container, getByTestId } = render(<AttentionQueue />);
    // jsdom has no layout engine, so skip the rules that need rendered geometry.
    const rules = { 'color-contrast': { enabled: false }, region: { enabled: false } };
    const bell = await axe.run(container, { rules });
    expect(bell.violations.map((violation) => violation.id)).toEqual([]);

    fireEvent.click(getByTestId('attention-bell'));
    // The whole layer: the popover's outer box, which carries attributes of its own, and the
    // dialog inside it.
    const popover = getByTestId('attention-queue').closest("[data-trigger='DialogTrigger']");
    if (!popover) throw new Error('the layer has no popover around it');
    const layer = await axe.run(popover, { rules });
    expect(layer.violations.map((violation) => violation.id)).toEqual([]);
  });

  it('reveals and focuses each native task selected from the sidebar', async () => {
    const first = entry('01', 'First task', '01_first.json');
    const second = entry('02', 'Second task', '02_second.json');
    dashboardStore.getState().applySnapshot(wireProjection({ tasks: [first, second] }));
    const shown = vi.fn();
    Element.prototype.scrollIntoView = shown;
    try {
      const view = render(<CockpitShell initialView="operations" />);
      for (const task of [first, second, first]) {
        fireEvent.click(view.getByText(task.title));
        await waitFor(() => expect(view.getByTestId('doc-id').textContent).toBe(task.id));
        const row = view.getByRole('option', { name: new RegExp(task.title) });
        expect(document.activeElement).toBe(row);
        expect(row.getAttribute('aria-selected')).toBe('true');
      }
      expect(shown).toHaveBeenCalledTimes(3);
    } finally {
      Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
    }
  });
});
