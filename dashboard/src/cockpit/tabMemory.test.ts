import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import {
  enclosure,
  seedTaskEntries,
  taskEntry,
  taskReference,
  taskSummary,
} from '../panels/detail-panel/test-utils';
import type { ChangeSetTarget } from '../panels/changeset/ChangeSetViewer';
import type { EnclosureNode, TaskEntry } from '../types/projection';
import { changeSetTaskGone, useTabChange } from './tabMemory';

const REPO = 'repo';
const MASTER = 'ops';
const MASTER_ID = 'ops-master';
const LEAF = 'leaf-one';

function worktree(owner: 'leaf' | 'master' = 'leaf'): EnclosureNode {
  return enclosure({
    enclosure: 'contract-a',
    enclosureId: 'scope-a',
    worktreeGroup: '/worktrees/a',
    repoName: REPO,
    taskName: MASTER,
    taskId: MASTER_ID,
    leafId: owner === 'leaf' ? LEAF : '',
  });
}

interface RetainedView {
  name: string;
  target: ChangeSetTarget;
  owner: 'leaf' | 'master';
  node?: EnclosureNode;
}

const leafTarget = { repo: REPO, master: MASTER, leaf: LEAF };
const scopeAliases = ['enclosure', 'enclosureId', 'worktreeGroup'] as const;
const scopeViews: RetainedView[] = (['leaf', 'master'] as const).flatMap((owner) => {
  const node = worktree(owner);
  return scopeAliases.map((alias) => ({
    name: `${owner}-owned enclosure through ${alias}`,
    target: { repo: REPO, scope: node[alias] },
    owner,
    node,
  }));
});
const retainedViews: RetainedView[] = [
  { name: 'committed leaf', target: { ...leafTarget, mode: 'committed' }, owner: 'leaf' },
  { name: 'working leaf', target: { ...leafTarget, mode: 'working' }, owner: 'leaf' },
  { name: 'live review', target: { ...leafTarget, review: {} }, owner: 'leaf' },
  { name: 'recorded review', target: { ...leafTarget, review: { historical: true } }, owner: 'leaf' },
  { name: 'numbered comparison', target: { ...leafTarget, comparison: 1 }, owner: 'leaf' },
  { name: 'master net', target: { repo: REPO, master: MASTER }, owner: 'master' },
  {
    name: 'pinned master net',
    target: { repo: REPO, master: MASTER, generation: { codeBase: 'base', codeTip: 'tip' } },
    owner: 'master',
  },
  ...scopeViews,
];

function owningTasks(node = worktree()) {
  return {
    leaf: taskEntry({
      id: LEAF,
      reference: taskReference(`${REPO}/${MASTER}/01_leaf.json`),
      worktrees: node.leafId ? [node] : [],
    }),
    master: taskEntry({
      id: MASTER_ID,
      reference: taskReference(`${REPO}/${MASTER}/task.json`),
      summary: taskSummary({ kind: 'master', form: 'master' }),
      worktrees: node.leafId ? [] : [node],
    }),
  };
}

function seed(tasks: TaskEntry[], enclosures: EnclosureNode[] = []) {
  seedTaskEntries(tasks, { enclosures });
  expect(dashboardStore.getState().generatedAt).not.toBeNull();
}

function returnToOperations(changeSet: ChangeSetTarget) {
  const setChangeSet = vi.fn();
  const setNotesOpen = vi.fn();
  const setView = vi.fn();
  const address = { park: vi.fn(), unpark: vi.fn() };
  const { result } = renderHook(() =>
    useTabChange<'chats' | 'operations'>(
      { view: 'chats', changeSet, notes: null, notesOpen: false, address },
      setChangeSet,
      setNotesOpen,
      setView,
    ),
  );
  act(() => result.current('operations'));
  expect(setView).toHaveBeenCalledWith('operations');
  expect(setNotesOpen).not.toHaveBeenCalled();
  return { setChangeSet, address };
}

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
});

describe('the Operations tab memory validates every retained change-set variant', () => {
  it.each(retainedViews)('keeps $name while its owning task exists', ({ target, node }) => {
    const tasks = owningTasks(node);
    seed(Object.values(tasks), node ? [node] : []);
    const { setChangeSet, address } = returnToOperations(target);
    expect(setChangeSet).not.toHaveBeenCalled();
    expect(address.unpark).toHaveBeenCalledTimes(target.review ? 1 : 0);
  });

  it.each(retainedViews)('dismisses $name after its owning task is removed', ({ target, owner, node }) => {
    const tasks = owningTasks(node);
    seed([tasks[owner === 'leaf' ? 'master' : 'leaf']]);
    expect(returnToOperations(target).setChangeSet).toHaveBeenCalledWith(null);
  });

  it.each(retainedViews)(
    'dismisses $name when its task is gone and its enclosure is still projected',
    ({ target, owner, node = worktree() }) => {
      const tasks = owningTasks(node);
      seed(Object.values(tasks), [node]);
      expect(returnToOperations(target).setChangeSet).not.toHaveBeenCalled();

      seed([tasks[owner === 'leaf' ? 'master' : 'leaf']], [node]);
      expect(Object.values(dashboardStore.getState().tasks).some((task) => task.id === tasks[owner].id)).toBe(false);
      expect(dashboardStore.getState().enclosures[node.enclosure]).toMatchObject(node);
      const { setChangeSet, address } = returnToOperations(target);
      expect(setChangeSet).toHaveBeenCalledWith(null);
      expect(address.unpark).not.toHaveBeenCalled();
    },
  );

  it.each(retainedViews)('keeps $name until the task population is hydrated', ({ target }) => {
    dashboardStore.setState({ tasks: {}, enclosures: {}, generatedAt: null });
    expect(returnToOperations(target).setChangeSet).not.toHaveBeenCalled();
  });

  it.each(retainedViews)('dismisses $name when only another repository holds its task id', ({ target, owner, node }) => {
    const task = owningTasks(node)[owner];
    seed([{ ...task, reference: { ...task.reference!, repository: 'other-repo' } }], node ? [node] : []);
    expect(returnToOperations(target).setChangeSet).toHaveBeenCalledWith(null);
  });

  it.each(retainedViews)('dismisses $name when only another master holds its task id', ({ target, owner, node }) => {
    const task = owningTasks(node)[owner];
    seed([{ ...task, reference: { repository: REPO, path: `other/${owner === 'leaf' ? '01_leaf.json' : 'task.json'}` } }], node ? [node] : []);
    expect(returnToOperations(target).setChangeSet).toHaveBeenCalledWith(null);
  });

  it.each(scopeViews)('keeps $name from its task-bound worktree without a separate enclosure', ({ target, owner, node }) => {
    seed([owningTasks(node)[owner]]);
    expect(returnToOperations(target).setChangeSet).not.toHaveBeenCalled();
  });

  it.each(scopeViews)('keeps $name when its enclosure is projected apart from its task', ({ target, owner, node }) => {
    seed([{ ...owningTasks(node)[owner], worktrees: [] }], [node!]);
    expect(returnToOperations(target).setChangeSet).not.toHaveBeenCalled();
  });

  it.each(scopeViews)('dismisses $name when another task retains its orphaned worktree', ({ target, owner, node }) => {
    const tasks = owningTasks(node);
    const survivor = tasks[owner === 'leaf' ? 'master' : 'leaf'];
    seed([{ ...survivor, worktrees: [node!] }], [node!]);
    expect(returnToOperations(target).setChangeSet).toHaveBeenCalledWith(null);
  });

  it('dismisses an enclosure scope that no current task or enclosure names', () => {
    seed([]);
    expect(changeSetTaskGone({ repo: REPO, scope: 'gone' })).toBe(true);
  });
});
