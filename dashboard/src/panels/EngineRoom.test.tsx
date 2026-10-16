import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import { GALLERY } from '../dev/fixtures';
import { enclosure, taskEntry } from '../test/fixtures/wire';
import { EngineRoom } from './EngineRoom';
import { buildEngineRoomModel } from './engine-room/buildEngineRoomModel';

// Delegate-to-real spy: proves WHEN the model is rebuilt (memo) without changing behavior.
vi.mock('./engine-room/buildEngineRoomModel', async (importOriginal) => {
  const mod = await importOriginal<typeof import('./engine-room/buildEngineRoomModel')>();
  return { ...mod, buildEngineRoomModel: vi.fn(mod.buildEngineRoomModel) };
});
const buildSpy = vi.mocked(buildEngineRoomModel);

function seed(name: string) {
  const projection = GALLERY.find((entry) => entry.name === name)?.projection;
  if (!projection) throw new Error(`fixture not found: ${name}`);
  dashboardStore.getState().applySnapshot(projection);
}

function seedParallelLeafRoom() {
  const fixture = GALLERY.find((entry) => entry.name === 'engine-bootstrap');
  if (!fixture) throw new Error('fixture not found: engine-bootstrap');
  const base = fixture.projection.analytics.engineProcesses[0];
  if (!base) throw new Error('engine-bootstrap fixture missing process');
  dashboardStore.getState().applySnapshot({
    ...fixture.projection,
    analytics: {
      ...fixture.projection.analytics,
      engineProcesses: [
        {
          ...base,
          id: '/tasks/260610_browser-dashboard/enclosures/15_parallel-leaf-enclosure-workflow/series-contract.md',
          enclosure:
            '/tasks/260610_browser-dashboard/enclosures/15_parallel-leaf-enclosure-workflow/series-contract.md',
          worktreeGroup: '/worktrees/260610-browser-dashboard-s15-ar',
          taskName: '260610_browser-dashboard',
          leafId: '15_parallel-leaf-enclosure-workflow',
          phase: 'cleanup-pending',
          closeoutStatus: 'completed',
          integrationStatus: 'completed',
        },
        {
          ...base,
          id: '/tasks/260610_browser-dashboard/enclosures/16_engine-room-stack-entry-height/series-contract.md',
          enclosure:
            '/tasks/260610_browser-dashboard/enclosures/16_engine-room-stack-entry-height/series-contract.md',
          worktreeGroup: '/worktrees/260610-browser-dashboard-s16-ar',
          taskName: '260610_browser-dashboard',
          leafId: '16_engine-room-stack-entry-height',
          phase: 'worktree-started',
        },
      ],
    },
  });
}

function seedSourceLineageRoom() {
  const fixture = GALLERY.find((entry) => entry.name === 'engine-bootstrap');
  if (!fixture) throw new Error('fixture not found: engine-bootstrap');
  const process = fixture.projection.analytics.engineProcesses[0];
  if (!process) throw new Error('engine-bootstrap fixture missing process');
  dashboardStore.getState().applySnapshot({
    ...fixture.projection,
    analytics: {
      ...fixture.projection.analytics,
      engineProcesses: [
        {
          ...process,
          sourceLineage: {
            state: 'blocked',
            summary: 'master code is behind the super-integration branch',
            edges: [],
            recoveries: [],
          },
        },
      ],
    },
  });
}

// Freeze motion so the phase-activity flag (not the pulse) is what's asserted.
beforeEach(() => {
  document.documentElement.dataset.effects = 'off';
});
afterEach(() => {
  document.documentElement.removeAttribute('data-effects');
  cleanup();
});

describe('EngineRoom lifecycle phase motion (5f S5, T12–T18)', () => {
  it('reads the bound task entry status while keeping the separate operation phase', () => {
    seed('engine-bootstrap');
    const analytics = dashboardStore.getState().analytics;
    if (!analytics?.engineProcesses[0]) throw new Error('engine fixture has no process');
    const process = analytics.engineProcesses[0];
    const entry = taskEntry({
      stage: 'started',
      activity: 'paused',
      worktrees: [enclosure({ enclosure: process.enclosure })],
    });
    act(() => dashboardStore.getState().applyDelta('task', entry));
    const ui = render(<EngineRoom />);
    expect(ui.getByTestId('engine-room-task-status').textContent).toBe('paused');
    expect(ui.getByTestId('engine-room-card-task-status').textContent).toBe('paused');
    expect(ui.getByTestId('engine-room-header').textContent).toContain(process.phase);
    act(() => dashboardStore.getState().applyDelta('task', { ...entry, activity: 'works' }));
    expect(ui.getByTestId('engine-room-task-status').textContent).toBe('works');
    expect(ui.getByTestId('engine-room-card-task-status').textContent).toBe('works');
    act(() =>
      dashboardStore
        .getState()
        .applyDelta('task', { ...entry, stage: 'completed', activity: undefined }),
    );
    expect(ui.getByTestId('engine-room-task-status').textContent).toBe('completed');
    expect(ui.getByTestId('engine-room-card-task-status').textContent).toBe('completed');
    expect(ui.getByTestId('engine-room-header').textContent).toContain(process.phase);
  });
  it('marks the header phase-active for a human-gated lifecycle phase (cleanup-pending)', () => {
    seed('engine-cleanup-pending');
    const { getByTestId } = render(<EngineRoom />);
    expect(getByTestId('engine-room-header').getAttribute('data-phase-active')).toBe('true');
  });

  it('does not mark phase-active for a freshly started worktree', () => {
    seed('engine-bootstrap');
    const { getByTestId } = render(<EngineRoom />);
    expect(getByTestId('engine-room-header').getAttribute('data-phase-active')).toBe('false');
  });

  it('renders task-resolved source lineage before an agent reads from the enclosure', () => {
    seedSourceLineageRoom();
    const { getByText } = render(<EngineRoom />);

    const label = getByText('Source lineage');
    expect(label.parentElement?.textContent).toContain('blocked');
    expect(label.parentElement?.getAttribute('title')).toBe(
      'master code is behind the super-integration branch',
    );
  });

  it('renders no official provider strip — workspace engines left the surface (MIK-R112)', () => {
    seed('engine-bootstrap');

    const { queryByTestId } = render(<EngineRoom />);
    expect(queryByTestId('official-strip')).toBeNull();
    expect(queryByTestId('engine-room-fallback')).toBeNull();
    expect(queryByTestId('enclosure-canvas')).not.toBeNull();
  });

  it('renders leaf enclosure identity ahead of the parent series task name', () => {
    seedParallelLeafRoom();

    const { getAllByTestId, getByTestId } = render(<EngineRoom />);
    const rows = getAllByTestId('enclosure-stack-item');

    expect(rows[0]?.textContent).toContain('16_engine-room-stack-entry-height');
    expect(rows[0]?.textContent).toContain('260610_browser-dashboard');
    expect(rows[0]?.textContent).toContain('worktree-started');
    expect(rows[1]?.textContent).toContain('15_parallel-leaf-enclosure-workflow');
    expect(rows[1]?.textContent).toContain('cleanup-pending');
    expect(getByTestId('engine-room-header').textContent).toContain(
      '16_engine-room-stack-entry-height',
    );
  });
});

describe('EngineRoom — memoized model + narrowed analytics subscription (260721 C3)', () => {
  beforeEach(() => {
    buildSpy.mockClear();
  });

  it('does not rebuild the model on a re-render with unchanged slices (identical inputs → no rebuild)', () => {
    seed('engine-bootstrap');
    const view = render(<EngineRoom />);
    const calls = buildSpy.mock.calls.length;
    expect(calls).toBeGreaterThan(0);
    view.rerender(<EngineRoom />);
    expect(buildSpy.mock.calls.length).toBe(calls);
  });

  it('ignores an analytics delta that leaves engineProcesses + ledgers untouched', () => {
    seed('engine-bootstrap');
    render(<EngineRoom />);
    const calls = buildSpy.mock.calls.length;
    const analytics = dashboardStore.getState().analytics;
    if (!analytics) throw new Error('analytics missing');
    // A wholesale analytics replacement (the wire protocol) touching only the attention queue: the
    // stableEquals slice selectors keep the previous identities → no re-render, no model rebuild.
    act(() =>
      dashboardStore.getState().applyDelta('analytics', {
        ...analytics,
        attentionQueue: [{ id: 'att-1', severity: 'warn' }],
      }),
    );
    expect(buildSpy.mock.calls.length).toBe(calls);
  });

  it('rebuilds when an engineProcesses field actually changes (the memo invalidates honestly)', () => {
    seed('engine-bootstrap');
    render(<EngineRoom />);
    const calls = buildSpy.mock.calls.length;
    const analytics = dashboardStore.getState().analytics;
    if (!analytics) throw new Error('analytics missing');
    act(() =>
      dashboardStore.getState().applyDelta('analytics', {
        ...analytics,
        engineProcesses: analytics.engineProcesses.map((process, index) =>
          index === 0 ? { ...process, summary: 'changed summary' } : process,
        ),
      }),
    );
    expect(buildSpy.mock.calls.length).toBeGreaterThan(calls);
  });
});
