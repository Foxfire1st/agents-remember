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
      itemClass: 'operational',
      dismissible: true,
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
      itemClass: 'operational',
      dismissible: true,
    };
    expect(
      selectQueue({
        analytics: { attentionQueue: [item] },
        suppressedAttentionIds: { [item.id]: true },
      } as DashboardState),
    ).toEqual([]);
  });

  it('drops the provider kinds the server retired, even when a stale payload carries them', () => {
    // Nothing publishes `provider-down` or `failed-setup` any more, so nothing can dismiss them
    // either. A tab handed an older payload would otherwise raise an alarm with no way to clear it.
    const retired: AttentionItem[] = [
      {
        id: 'provider-down:codegraphcontext-code',
        kind: 'provider-down',
        severity: 'alarm',
        lane: 'repo',
        title: 'Code index is down',
        itemClass: 'operational',
        dismissible: true,
      },
      {
        id: 'failed-setup:legacy-ar',
        kind: 'failed-setup',
        severity: 'alarm',
        lane: 'worktree',
        title: 'Provider setup needs attention',
        itemClass: 'operational',
        dismissible: true,
      },
    ];
    const live: AttentionItem = {
      id: 'blocked-start:legacy-ar',
      kind: 'blocked-start',
      severity: 'warn',
      lane: 'worktree',
      title: 'Worktree start blocked',
      itemClass: 'operational',
      dismissible: true,
    };
    expect(
      selectQueue({
        analytics: { attentionQueue: [...retired, live] },
        suppressedAttentionIds: {},
      } as DashboardState),
    ).toEqual([live]);
    expect(
      selectQueue({
        analytics: { attentionQueue: retired },
        suppressedAttentionIds: {},
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
