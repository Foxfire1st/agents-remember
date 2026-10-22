import { describe,expect,it } from 'vitest';

import { taskEntrySelectionKey } from '../data/taskReference';
import { formatLimitTime } from '../panels/detail-panel/pageParts';
import { taskEntry } from '../test/fixtures/wire';
import type { EnclosureNode,ProviderLimit,ProviderNode } from '../types/projection';
import { activeTopologyInputs,buildTopology } from './model';

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

const provider = (overrides: Partial<ProviderNode> = {}): ProviderNode => ({
  id: 'codegraphcontext-code@demo',
  state: 'configured',
  ok: true,
  watcherUp: true,
  indexingState: 'indexed',
  scope: 'worktree',
  role: 'code',
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
    const nodes = buildTopology([{ entry, enclosure: enclosure() }], []);
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
    const nodes = buildTopology([{ entry, enclosure: enclosure() }], []);
    expect(nodes.find((node) => node.kind === 'wt')).toMatchObject({
      sub: `worktree · rate limited, until ${formatLimitTime(LIMITED.until)}`,
      // The tone is paused's: a limit never makes a task look like work.
      status: 'warn',
    });
  });

  it('joins a worktree provider to its enclosure when worktreeGroup formats differ (path vs basename)', () => {
    const owner = enclosure({ worktreeGroup: '/worktrees/agents-remember/demo-ar' });
    // Real served data: the worktree ProviderNode.worktreeGroup is a basename, the enclosure's a full path.
    const nodes = buildTopology([{ enclosure: owner }], [provider({ worktreeGroup: 'demo-ar' })]);

    const worktreeIndex = nodes.findIndex(
      (node) => node.kind === 'wt' && node.label === owner.taskName,
    );
    const providerNode = nodes.find((node) => node.kind === 'prov');

    expect(worktreeIndex).toBeGreaterThan(-1);
    expect(providerNode?.parent).toBe(worktreeIndex);
  });

  it('parents worktree-scoped providers to their owning worktree node', () => {
    const owner = enclosure();
    const nodes = buildTopology(
      [{ enclosure: owner }],
      [provider({ worktreeGroup: owner.worktreeGroup })],
    );

    const worktreeIndex = nodes.findIndex(
      (node) => node.kind === 'wt' && node.label === owner.taskName,
    );
    const providerNode = nodes.find(
      (node) => node.kind === 'prov' && node.label === 'codegraphcontext-code@demo',
    );

    expect(worktreeIndex).toBeGreaterThan(-1);
    expect(providerNode?.parent).toBe(worktreeIndex);
  });

  it('falls back to the workspace core when a worktree provider has no matching group', () => {
    const nodes = buildTopology(
      [{ enclosure: enclosure() }],
      [provider({ worktreeGroup: '/missing/group' })],
    );
    const workspaceIndex = nodes.findIndex((node) => node.kind === 'ws');
    const providerNode = nodes.find((node) => node.kind === 'prov');

    expect(providerNode?.parent).toBe(workspaceIndex);
  });

  it('keeps workspace-scoped providers parented to the workspace core', () => {
    const nodes = buildTopology(
      [{ enclosure: enclosure() }],
      [provider({ id: 'grepai-memory', scope: 'workspace', worktreeGroup: undefined })],
    );
    const workspaceIndex = nodes.findIndex((node) => node.kind === 'ws');
    const providerNode = nodes.find((node) => node.kind === 'prov');

    expect(providerNode?.parent).toBe(workspaceIndex);
  });

  it('parents repo-scoped workspace providers to their covered repo node', () => {
    const nodes = buildTopology(
      [],
      [
        provider({
          id: 'codegraphcontext-code:repo-b',
          scope: 'workspace',
          repoId: 'repo-b',
          worktreeGroup: undefined,
        }),
      ],
    );
    const repoIndex = nodes.findIndex((node) => node.kind === 'repo' && node.label === 'repo-b');
    const providerNode = nodes.find((node) => node.kind === 'prov');

    expect(repoIndex).toBeGreaterThan(-1);
    expect(providerNode?.parent).toBe(repoIndex);
  });

  it('keeps worktreeGroup precedence over repoId for provider parenting', () => {
    const owner = enclosure();
    const nodes = buildTopology(
      [{ enclosure: owner }],
      [
        provider({
          worktreeGroup: owner.worktreeGroup,
          repoId: 'repo-b',
        }),
      ],
    );
    const worktreeIndex = nodes.findIndex(
      (node) => node.kind === 'wt' && node.label === owner.taskName,
    );
    const providerNode = nodes.find((node) => node.kind === 'prov');

    expect(providerNode?.parent).toBe(worktreeIndex);
  });
});
