import { describe, expect, it } from 'vitest';

import type { EngineProcessNode } from '../../types/projection';
import { buildEngineRoomModel } from './buildEngineRoomModel';

function node(over: Partial<EngineProcessNode> = {}): EngineProcessNode {
  return {
    id: '/c.md',
    enclosure: '/c.md',
    worktreeGroup: '/w/r/grp',
    taskId: 'T',
    leafId: 'demo-leaf',
    taskName: 'demo',
    repoName: 'r',
    phase: 'worktree-started',
    health: 'nominal',
    codeSource: { factState: 'derived' },
    codeWorktree: { factState: 'observed', exists: true },
    memoryMode: 'external',
    humanReviewStatus: 'pending-review',
    closeoutStatus: 'not-started',
    integrationStatus: 'not-started',
    cleanup: 'pending',
    completedPhases: [],
    failedPhases: [],
    seedFallback: false,
    providers: [],
    edges: [],
    landing: [],
    ledgerRows: [],
    ledgerRowCount: 0,
    actions: [],
    summary: '',
    missingFacts: [],
    sourceFiles: [],
    ...over,
  };
}

describe('buildEngineRoomModel', () => {
  it('uses the published process without a second session join', () => {
    const process = node();
    const model = buildEngineRoomModel([process]);
    expect(model.processes).toHaveLength(1);
    expect(model.processes[0]?.node).toBe(process);
    expect(Object.keys(model.processes[0] ?? {}).sort()).toEqual(['enclosureKey', 'node']);
  });

  it('builds no provider fallback from an empty process surface (MIK-R112)', () => {
    const model = buildEngineRoomModel([]);
    expect(model.processes).toHaveLength(0);
  });

  it('exposes worktreeGroup as the enclosure key, stable across a setup-to-contract id change', () => {
    const starting = buildEngineRoomModel([node({ id: 'start:demo' })]);
    const real = buildEngineRoomModel([node({ id: '/series-contract.md' })]);
    expect(starting.processes[0]?.enclosureKey).toBe('/w/r/grp');
    expect(real.processes[0]?.enclosureKey).toBe('/w/r/grp');
    expect(starting.processes[0]?.enclosureKey).toBe(real.processes[0]?.enclosureKey);
  });

  it('orders active leaf enclosures before cleanup-pending siblings for the same parent task', () => {
    const model = buildEngineRoomModel([
      node({
        worktreeGroup: '/w/r/old',
        leafId: '15_parallel-leaf-enclosure-workflow',
        phase: 'cleanup-pending',
        closeoutStatus: 'completed',
        integrationStatus: 'completed',
      }),
      node({
        worktreeGroup: '/w/r/current',
        leafId: '16_engine-room-stack-entry-height',
        phase: 'worktree-started',
      }),
    ]);

    expect(model.processes.map((view) => view.node.leafId)).toEqual([
      '16_engine-room-stack-entry-height',
      '15_parallel-leaf-enclosure-workflow',
    ]);
  });
});
