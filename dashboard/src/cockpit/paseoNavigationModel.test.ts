import { describe, expect, it } from 'vitest';
import { taskDoc } from '../test/fixtures/wire';
import type { SeriesNode, TaskDocNode } from '../types/projection';
import { groupPaseoChats } from './paseoNavigationModel';
import type { PaseoHierarchyAgent, PaseoHierarchySnapshot } from './paseoFrameModel';

const doc = (folder: string, id: string, kind = 'master', title = 'Same title') =>
  taskDoc({
    repository: 'repo',
    docPath: `/coord/tasks/repo/${folder}/task.json`,
    id,
    kind,
    title,
    orchestrates: [],
  });
const s1 = { ...doc('s1', 'S1'), orchestrates: ['m1', 'm2'] };
const s2 = { ...doc('s2', 'S2'), orchestrates: ['m3'] };
const masters = [doc('m1', 'M1'), doc('m2', 'M2'), doc('m3', 'M3')];
const leaves = masters.map((master, index) => ({
  ...doc(`m${index + 1}`, `L${index + 1}`, 'subTask'),
  docPath: master.docPath.replace('task.json', '01_same.json'),
}));
const docs = [s1, s2, ...masters, ...leaves];
const series: SeriesNode[] = masters.map((master) => ({
  createdAt: '2026-10-03',
  decisions: [],
  discardedCount: 0,
  discardedSubTasks: [],
  docPath: master.docPath,
  doneCount: 0,
  objective: '',
  repository: 'repo',
  sections: [],
  seriesId: master.id,
  seriesTokenTotal: 0,
  status: 'planning',
  subTasks: [
    { file: '01_same.json', name: 'Same title', number: 'L', scope: '', status: 'planning' },
  ],
  title: master.title,
  totalCount: 1,
}));
const agent = (agentId: string, labels: Record<string, string> = {}): PaseoHierarchyAgent => ({
  agentId,
  name: agentId,
  provider: 'pi',
  status: 'idle',
  pendingPermissionCount: 0,
  requiresAttention: false,
  attentionReason: null,
  providerUnavailable: false,
  workspaceId: 'legacy-native-workspace',
  parentAgentId: 'architect',
  archivedAt: null,
  labels,
});
const snapshot = (agents: PaseoHierarchyAgent[]): PaseoHierarchySnapshot => ({
  agents,
  projects: [],
  workspaces: [],
});
const binding = (s: number, m: number) => ({
  'ar.role': 'worker',
  'ar.sprint-ref': `repo/s${s}/task.json`,
  'ar.master-ref': `repo/m${m}/task.json`,
  'ar.task-ref': `repo/m${m}/01_same.json`,
});

describe('canonical AR chat groups', () => {
  it('keeps Projects first and separates duplicate titles by canonical sprint/master/task despite legacy memberships', () => {
    const groups = groupPaseoChats(
      snapshot([
        agent('third', binding(2, 3)),
        agent('second', binding(1, 2)),
        agent('architect'),
        agent('first', binding(1, 1)),
      ]),
      docs,
      series,
    );
    expect(groups.map((g) => g.key)).toEqual([
      'projects',
      'repo/s2/task.json',
      'repo/s1/task.json',
    ]);
    expect(groups[0].chats.map((a) => a.agentId)).toEqual(['architect']);
    expect(groups[1].children[0].documentId).toBe('M3');
    expect(groups[2].children.map((g) => g.key)).toEqual([
      'repo/m2/task.json',
      'repo/m1/task.json',
    ]);
    expect(groups[2].children.map((g) => g.children[0].chats[0].agentId)).toEqual([
      'second',
      'first',
    ]);
    expect(groups[2].children[0].children[0].key).toBe('repo/m2/01_same.json');
  });

  it('does not invent scope for colon aliases, missing documents or conflicting canonical links', () => {
    const agents = [
      agent('colon', { ...binding(1, 1), 'ar.sprint-ref': 'repo:s1/task.json' }),
      agent('missing', { ...binding(1, 1), 'ar.task-ref': 'repo/m1/absent.json' }),
      agent('conflict', binding(2, 1)),
      agent('no-ref', { 'ar.role': 'worker' }),
    ];
    const groups = groupPaseoChats(snapshot(agents), docs, series);
    expect(groups.map((g) => g.key)).toEqual(['projects', 'unresolved']);
    expect(groups[1].chats.map((a) => a.agentId)).toEqual(agents.map((a) => a.agentId));
  });

  it('places sprint and master controllers at their exact scope, skips archived chats and updates canonical titles', () => {
    const source = snapshot([
      agent('orchestrator', { 'ar.sprint-ref': 'repo/s1/task.json' }),
      agent('manager', {
        'ar.sprint-ref': 'repo/s1/task.json',
        'ar.master-ref': 'repo/m1/task.json',
      }),
      { ...agent('old', binding(1, 1)), archivedAt: '2026-10-03' },
    ]);
    const changed: TaskDocNode[] = docs.map((d) =>
      d.id === 'M1' ? { ...d, title: 'Current master title' } : d,
    );
    const groups = groupPaseoChats(source, changed, series);
    expect(groups[1].chats[0].agentId).toBe('orchestrator');
    expect(groups[1].children[0].title).toBe('Current master title');
    expect(groups[1].children[0].chats[0].agentId).toBe('manager');
    expect(groups[1].children[0].children).toEqual([]);
  });
});
