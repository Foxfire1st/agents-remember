import { describe, expect, it } from 'vitest';

import { taskEntrySelectionKey } from '../data/taskReference';
import { formatLimitTime } from '../panels/detail-panel/pageParts';
import { taskEntry } from '../test/fixtures/wire';
import type { EnclosureNode, ProviderLimit } from '../types/projection';
import { activeTopologyInputs, buildTopology } from './model';

// MIK-R190 rule 13: the served limit a limited worktree entry carries.
const LIMITED: ProviderLimit = {
  until: '2026-10-21T12:00:00+02:00',
  seats: [
    {
      role: 'worker',
      agentId: '0f0c1d2e-0000-4000-8000-000000000001',
      source: 'Claude',
      account: 'Max 20x',
      window: 'Session',
      resetsAt: '2026-10-21T12:00:00+02:00',
    },
  ],
};

const enclosure = (overrides: Partial<EnclosureNode> = {}): EnclosureNode => ({
  enclosure: '/tasks/demo/enclosures/demo/series-contract.md',
  enclosureId: '/tasks/demo/enclosures/demo/series-contract.md',
  leafId: 'demo',
  taskRoot: '/tasks/demo',
  taskId: 'DEMO',
  taskName: 'demo',
  repoName: 'agents-remember',
  lifecycleId: 'LC1',
  worktreeGroup: '/worktrees/agents-remember/demo-ar',
  humanReviewStatus: 'pending-review',
  closeoutStatus: 'not-started',
  integrationStatus: 'not-started',
  cleanup: 'pending',
  codeWorktreeExists: true,
  memoryWorktreeExists: true,
  actions: [],
  ...overrides,
});

describe('entry worktrees in the topology', () => {
  it('keeps only the server-admitted active groups', () => {
    const owner = { enclosure: enclosure() };
    expect(activeTopologyInputs([owner], ['demo-ar']).worktrees).toEqual([owner]);
    expect(activeTopologyInputs([owner], ['other-ar']).worktrees).toEqual([]);
  });

  it('uses its bound task reference, title and the shared served status without a task ring', () => {
    const entry = taskEntry({ activity: 'paused', worktrees: [] });
    const nodes = buildTopology([{ entry, enclosure: enclosure() }]);
    expect(nodes.find((node) => node.kind === 'wt')).toMatchObject({
      id: taskEntrySelectionKey(entry.key),
      label: entry.title,
      sub: 'worktree · paused',
      status: 'warn',
    });
    expect(nodes.some((node) => (node.kind as string) === 'task')).toBe(false);
  });

  it('says the words and the exact served end on a node that has no clock', () => {
    const entry = taskEntry({ activity: 'paused', worktrees: [], providerLimit: LIMITED });
    const nodes = buildTopology([{ entry, enclosure: enclosure() }]);
    expect(nodes.find((node) => node.kind === 'wt')).toMatchObject({
      sub: `worktree · rate limited, until ${formatLimitTime(LIMITED.until)}`,
      // The tone is paused's: a limit never makes a task look like work.
      status: 'warn',
    });
  });

  it('builds only the workspace, checkout and enclosure rings', () => {
    // The provider satellites — one node per engine, orbiting its scoped parent on a `poff` angle —
    // left with the search providers. The model's vocabulary and every node's own keys say so, so
    // a satellite cannot come back without failing here first.
    const nodes = buildTopology([{ enclosure: enclosure() }]);
    expect(new Set(nodes.map((node) => node.kind))).toEqual(new Set(['ws', 'repo', 'wt']));
    for (const node of nodes) expect(Object.keys(node)).not.toContain('poff');
    expect(nodes.find((node) => node.kind === 'ws')?.sub).toBe('1 checkouts · 1 active worktrees');
  });

  it('keeps every repo with an active enclosure on the checkout ring, and no other', () => {
    const nodes = buildTopology([
      { enclosure: enclosure() },
      { enclosure: enclosure({ enclosure: '/tasks/b', repoName: 'repo-b' }) },
    ]);
    expect(
      nodes
        .filter((node) => node.kind === 'repo')
        .map((node) => node.label),
    ).toEqual(['agents-remember', 'repo-b']);
  });

  it('parents each enclosure to its own checkout', () => {
    const nodes = buildTopology([
      { enclosure: enclosure() },
      { enclosure: enclosure({ enclosure: '/tasks/b', repoName: 'repo-b' }) },
    ]);
    const byLabel = new Map(nodes.map((node, index) => [node.label, index]));
    const repoB = byLabel.get('repo-b');
    const worktrees = nodes.filter((node) => node.kind === 'wt');
    expect(worktrees).toHaveLength(2);
    for (const node of worktrees) {
      expect(nodes[node.parent].kind).toBe('repo');
    }
    expect(worktrees.some((node) => node.parent === repoB)).toBe(true);
  });
});
