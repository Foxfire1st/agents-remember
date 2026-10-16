import { cleanup, fireEvent, render } from '@testing-library/react';
import { type ComponentProps } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import { GALLERY } from '../dev/fixtures';
import { projection, taskEntry, taskSummary } from '../test/fixtures/wire';
import { taskTree } from '../panels/detail-panel/test-utils';
import { CockpitShell } from './Cockpit';

// Render-count probes (tab-switch CPU, the memoization contract in Cockpit.tsx): every
// persistent layer is replaced by a memoized probe that counts a render and then renders the REAL
// panel. The probe's memo gate has the same default shallow compare as the production export, so
// the count is exactly the layer's PARENT-driven render count — what a setView used to churn.
// Store-driven updates inside a panel never touch the probe, matching the fix's scope.
const counts = vi.hoisted(() => ({
  engineRoom: 0,
  detailPanel: 0,
  fileViewer: 0,
  attentionQueue: 0,
  lifecycleList: 0,
  eventRiver: 0,
}));

vi.mock('../panels/EngineRoom', async (importOriginal) => {
  const { memo } = await import('react');
  const mod = await importOriginal<typeof import('../panels/EngineRoom')>();
  const Real = mod.EngineRoom;
  const Counted = memo(function CountedEngineRoom() {
    counts.engineRoom += 1;
    return <Real />;
  });
  return { ...mod, EngineRoom: Counted };
});

vi.mock('../panels/detail-panel/DetailPanel', async (importOriginal) => {
  const { memo } = await import('react');
  const mod = await importOriginal<typeof import('../panels/detail-panel/DetailPanel')>();
  const Real = mod.DetailPanel;
  const Counted = memo(function CountedDetailPanel(props: ComponentProps<typeof Real>) {
    counts.detailPanel += 1;
    return <Real {...props} />;
  });
  return { ...mod, DetailPanel: Counted };
});

vi.mock('../panels/file-viewer/FileViewer', async (importOriginal) => {
  const { memo } = await import('react');
  const mod = await importOriginal<typeof import('../panels/file-viewer/FileViewer')>();
  const Real = mod.FileViewer;
  const Counted = memo(function CountedFileViewer(props: ComponentProps<typeof Real>) {
    counts.fileViewer += 1;
    return <Real {...props} />;
  });
  return { ...mod, FileViewer: Counted };
});

vi.mock('../panels/AttentionQueue', async (importOriginal) => {
  const { memo } = await import('react');
  const mod = await importOriginal<typeof import('../panels/AttentionQueue')>();
  const Real = mod.AttentionQueue;
  const Counted = memo(function CountedAttentionQueue() {
    counts.attentionQueue += 1;
    return <Real />;
  });
  return { ...mod, AttentionQueue: Counted };
});

vi.mock('../panels/lifecycle-list/LifecycleList', async (importOriginal) => {
  const { memo } = await import('react');
  const mod = await importOriginal<typeof import('../panels/lifecycle-list/LifecycleList')>();
  const Real = mod.LifecycleList;
  const Counted = memo(function CountedLifecycleList(props: ComponentProps<typeof Real>) {
    counts.lifecycleList += 1;
    return <Real {...props} />;
  });
  return { ...mod, LifecycleList: Counted };
});

vi.mock('../panels/EventRiver', async (importOriginal) => {
  const { memo } = await import('react');
  const mod = await importOriginal<typeof import('../panels/EventRiver')>();
  const Real = mod.EventRiver;
  const Counted = memo(function CountedEventRiver() {
    counts.eventRiver += 1;
    return <Real />;
  });
  return { ...mod, EventRiver: Counted };
});

function seed(stateName: string) {
  const fixture = GALLERY.find((entry) => entry.name === stateName);
  if (!fixture) throw new Error(`fixture not found: ${stateName}`);
  dashboardStore.getState().applySnapshot(fixture.projection);
}

// Native parent membership is published once, independently of worktree existence.
function seedDrillableMaster() {
  const master = taskEntry({
    id: 'master-x',
    title: 'Ops Master',
    reference: { repository: 'repo-a', path: 'ops/task.json' },
    stage: 'started',
    summary: taskSummary({ kind: 'master', form: 'master', objective: 'Master objective.' }),
  });
  const leaf = taskEntry({
    id: 'leaf-one',
    title: 'Leaf One',
    reference: { repository: 'repo-a', path: 'ops/01_leaf.json' },
    stage: 'started',
    summary: taskSummary({ objective: 'Leaf objective.' }),
  });
  dashboardStore.getState().applySnapshot(projection({ tasks: taskTree(master, [leaf]) }));
}

beforeEach(() => {
  vi.stubGlobal('CSS', { escape: (value: string) => value });
  for (const key of Object.keys(counts) as (keyof typeof counts)[]) counts[key] = 0;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
  window.localStorage.clear(); // the rail toggle now persists its choice; isolate it between tests
});

describe('persistent layers skip the setView reconcile (260721 tab-switch CPU)', () => {
  it('keeps unchanged layers still and sends rail visibility only across bleed boundaries', () => {
    seed('engine-fleet');
    const view = render(<CockpitShell initialView="operations" />);

    // Accessible persistent layers mount once; Engine Room is outside the bar and unmounted.
    expect(counts.engineRoom).toBe(0);
    expect(counts.detailPanel).toBe(1);
    expect(counts.fileViewer).toBe(1);
    expect(counts.attentionQueue).toBe(1);
    expect(counts.lifecycleList).toBe(1);
    expect(counts.eventRiver).toBe(1);

    // A full mode-bar sweep — railed↔full-bleed grid flips in both directions. Before the fix
    // EVERY layer reconciled on each of these clicks; now the memo gate holds wherever the
    // layer's props are unchanged, so a switch costs only the shell's style/layout flip.
    for (const [name, detailRenders] of [['Chats', 2], ['File Viewer', 2], ['Operations', 3]] as const) {
      fireEvent.click(view.getByRole('radio', { name }));
      // Only hide/show reaches the persistent page, so its shared clock can stop.
      expect(counts.detailPanel).toBe(detailRenders);
    }

    // No props at all / unchanged props: never re-rendered by the sweep.
    expect(counts.engineRoom).toBe(0);
    expect(counts.detailPanel).toBe(3); // the two visibility edges; stable selection/callbacks
    expect(counts.eventRiver).toBe(1);
    expect(counts.fileViewer).toBe(3); // File Viewer is visited once
    // The task list receives only the visibility edges: Chats hides the left rail, then
    // Operations shows it again. LifecycleList's rendered React Aria collection has its own memo
    // gate, so these controller renders do not rebuild it while hidden.
    expect(counts.lifecycleList).toBe(3);
    // The attention queue is the bell in the task list's head (MIK-R144 rule 3a), so its parent
    // is the list's rendered collection, not the rail, and the visibility edges never reach it.
    // It renders once, when it mounts. Hiding the rail renders the list's controller alone: the
    // collection's gate holds everything under it still. Showing the rail renders the collection
    // again, and the bell's own memo gate stops that render: it has no selection callback.
    expect(counts.attentionQueue).toBe(1);
  });

  it('keeps the visibility/aria contract and DOM identity across switches (keep-alive intact)', () => {
    seed('engine-fleet');
    const { container, getByRole } = render(<CockpitShell initialView="operations" />);
    const railLeft = container.querySelector('.rail--left') as HTMLElement;
    const railRight = container.querySelector('.rail--right') as HTMLElement;
    const room = container.querySelector('[data-testid="file-viewer"]') as HTMLElement;
    const roomLayer = room.parentElement as HTMLElement;
    const chats = container.querySelector('[data-testid="role-chats-pane"]') as HTMLElement;
    const chatsLayerEl = chats.parentElement as HTMLElement;

    // Railed Operations: rails shown, keep-alive layers hidden via display + aria-hidden.
    expect(railLeft.style.display).toBe('flex');
    expect(roomLayer.style.display).toBe('none');
    expect(roomLayer.getAttribute('aria-hidden')).toBe('true');
    expect(chatsLayerEl.style.display).toBe('none');

    fireEvent.click(getByRole('radio', { name: 'File Viewer' }));
    expect(railLeft.style.display).toBe('none');
    expect(railRight.style.display).toBe('none');
    expect(railLeft.getAttribute('aria-hidden')).toBe('true');
    expect(roomLayer.style.display).toBe('flex');
    expect(roomLayer.getAttribute('aria-hidden')).toBe('false');

    fireEvent.click(getByRole('radio', { name: 'Chats' }));
    expect(chatsLayerEl.style.display).toBe('flex');
    expect(chatsLayerEl.getAttribute('aria-hidden')).toBe('false');
    expect(roomLayer.style.display).toBe('none');

    expect(container.querySelector('[data-testid="role-chats-pane"]')).toBe(chats);

    fireEvent.click(getByRole('radio', { name: 'Operations' }));
    // Same DOM nodes throughout — hidden-not-unmounted survived the memo change.
    expect(container.querySelector('.rail--left')).toBe(railLeft);
    expect(container.querySelector('.rail--right')).toBe(railRight);
    expect(container.querySelector('[data-testid="file-viewer"]')).toBe(room);
    expect(container.querySelector('[data-testid="role-chats-pane"]')).toBe(chats);
    expect(railLeft.style.display).toBe('flex');
    expect(roomLayer.style.display).toBe('none');
    expect(chatsLayerEl.style.display).toBe('none');
  });

  it('still re-renders a layer when its real props change (the memo gate passes selection)', () => {
    seedDrillableMaster();
    const view = render(<CockpitShell initialView="operations" />);
    expect(counts.detailPanel).toBe(1);
    expect(counts.lifecycleList).toBe(1);

    // Selecting the master changes selectedId — a REAL prop change the memo must pass through.
    fireEvent.click(view.getByText('Ops Master'));
    expect(counts.detailPanel).toBe(2);
    expect(counts.lifecycleList).toBe(2);
    // …while layers whose props genuinely didn't change still skip.
    expect(counts.attentionQueue).toBe(1);
    expect(counts.engineRoom).toBe(0);
    expect(counts.eventRiver).toBe(1);
  });

  it('hides the right-rail chat behind the River (memoized RailToggle still updates)', () => {
    seed('engine-fleet');
    const view = render(<CockpitShell initialView="operations" />);
    const railRight = view.container.querySelector('.rail--right');
    expect(railRight?.querySelector('[data-testid="event-river"]')).not.toBeNull();

    fireEvent.click(view.getByTestId('rail-toggle-chat'));
    const chat = railRight?.querySelector('[data-testid="document-chat"]');
    expect(chat).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="event-river"]')).toBeNull();

    fireEvent.click(view.getByTestId('rail-toggle-river'));
    expect(railRight?.querySelector('[data-testid="event-river"]')).not.toBeNull();
    expect(railRight?.querySelector('[data-testid="document-chat"]')).toBe(chat);
  });
});
