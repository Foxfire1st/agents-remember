import { describe, expect, it } from 'vitest';

import type { DashboardState } from './store';
import type { AttentionItem, DriftSnapshotNode } from '../types/projection';
import { driftSegments, fmtWait, hasLiveWorktree, selectQueue } from './selectors';

describe('hasLiveWorktree', () => {
  it("is the stat'ed existence truth (L11): either physical worktree admits, neither hides", () => {
    // Never a cleanup-state proxy — the flags alone decide tasks-surface visibility.
    expect(hasLiveWorktree({ codeWorktreeExists: true, memoryWorktreeExists: true })).toBe(true);
    expect(hasLiveWorktree({ codeWorktreeExists: true, memoryWorktreeExists: false })).toBe(true);
    expect(hasLiveWorktree({ codeWorktreeExists: false, memoryWorktreeExists: true })).toBe(true);
    expect(hasLiveWorktree({ codeWorktreeExists: false, memoryWorktreeExists: false })).toBe(false);
  });
});

describe('fmtWait', () => {
  it('scales seconds → s/m/h/d and renders unknown as a dash', () => {
    expect(fmtWait(45)).toBe('45s');
    expect(fmtWait(120)).toBe('2m');
    expect(fmtWait(7200)).toBe('2h');
    expect(fmtWait(172800)).toBe('2d');
    expect(fmtWait(undefined)).toBe('—');
  });
});

describe('selectQueue', () => {
  it('reads the server-computed queue, empty when analytics is absent', () => {
    const item: AttentionItem = {
      id: 'actionable-drift:LC1',
      kind: 'actionable-drift',
      severity: 'info',
      lane: 'repo',
      title: 'Drift requires repair',
    };
    expect(
      selectQueue({
        analytics: { attentionQueue: [item] },
        suppressedAttentionIds: {},
      } as DashboardState),
    ).toEqual([item]);
    expect(selectQueue({ analytics: null, suppressedAttentionIds: {} } as DashboardState)).toEqual(
      [],
    );
  });

  it('hides optimistically suppressed attention ids', () => {
    const item: AttentionItem = {
      id: 'actionable-drift:repo:main',
      kind: 'actionable-drift',
      severity: 'warn',
      lane: 'repo',
      title: '1 actionable drift in repo',
    };
    expect(
      selectQueue({
        analytics: { attentionQueue: [item] },
        suppressedAttentionIds: { [item.id]: true },
      } as DashboardState),
    ).toEqual([]);
  });
});

function driftSnapshot(counts: Record<string, number>): DriftSnapshotNode {
  return { actionableCount: 0, branch: 'main', counts, repository: 'repo' };
}

// A converted repository reports `current`, `stale` and `violation` (MIK-R44):
// the mirror orders and shows those three names instead of legacy classifications.
describe('driftSegments on converted snapshots', () => {
  it('orders current, stale and violation with exact shares', () => {
    const segments = driftSegments(driftSnapshot({ current: 10, stale: 3, violation: 1 }));
    expect(segments.map((segment) => segment.cls)).toEqual(['current', 'stale', 'violation']);
    expect(segments.map((segment) => segment.count)).toEqual([10, 3, 1]);
    expect(segments.reduce((sum, segment) => sum + segment.pct, 0)).toBeLessThanOrEqual(100);
  });

  it('keeps legacy classifications ahead of converted-only names', () => {
    const segments = driftSegments(driftSnapshot({ current: 4, drifted: 2, stale: 2 }));
    expect(segments.map((segment) => segment.cls)).toEqual(['current', 'drifted', 'stale']);
  });
});
