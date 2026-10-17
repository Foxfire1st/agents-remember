import { expect, it } from 'vitest';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { hierarchy, task, taskMap } from './test-utils';
import { foldKey, treeItems, type TreeView } from './treeRows';

const view = (over: Partial<TreeView> = {}): TreeView => ({
  collapsedKeys: new Set(),
  openFolds: new Set(),
  selectedKey: null,
  released: new Set(),
  scope: 'repo',
  ...over,
});
it('keeps a completed master folded despite an active child, then opens down to a selected child', () => {
  const { sprint, master, leaf, entries } = hierarchy();
  master.stage = 'completed';
  master.activity = undefined;
  const tasks = taskMap(entries);
  const closed = treeItems(tasks, [sprint.key], view());
  expect(closed.map((item) => item.kind)).toEqual(['row', 'fold']);
  expect(closed[1]).toMatchObject({ entries: [master], open: false });
  const open = treeItems(
    tasks,
    [sprint.key],
    view({
      selectedKey: taskEntrySelectionKey(leaf.key),
      collapsedKeys: new Set([taskEntrySelectionKey(sprint.key)]),
    }),
  );
  expect(open.filter((item) => item.kind === 'row').map((item) => item.row.entry.id)).toEqual([
    'S',
    'M',
    'M-L1',
  ]);
  // D309: once a click has ended the holds, the closed parent and the fold close over the chosen
  // child as well.
  const released = treeItems(
    tasks,
    [sprint.key],
    view({
      selectedKey: taskEntrySelectionKey(leaf.key),
      collapsedKeys: new Set(),
      released: new Set([foldKey('repo', taskEntrySelectionKey(sprint.key))]),
    }),
  );
  expect(released.map((item) => item.kind)).toEqual(['row', 'fold']);
  expect(released[1]).toMatchObject({ open: false, held: false });
  const closedParent = treeItems(
    tasks,
    [sprint.key],
    view({
      selectedKey: taskEntrySelectionKey(leaf.key),
      collapsedKeys: new Set([taskEntrySelectionKey(sprint.key)]),
      released: new Set([taskEntrySelectionKey(sprint.key)]),
    }),
  );
  expect(closedParent).toMatchObject([{ kind: 'row', collapsed: true, held: false }]);
});
it('constructs no terminal child rows at two long-history sizes and preserves authored order', () => {
  for (const size of [109, 300]) {
    const { master } = hierarchy();
    const children = Array.from({ length: size }, (_, i) =>
      task(`M-L${i}`, { stage: 'completed', activity: undefined, parent: master.key }),
    );
    master.children = children.map((entry) => entry.key);
    master.counts = {
      planned: 0,
      started: 0,
      completed: size,
      aborted: 0,
      works: 0,
      paused: 0,
      unknown: 0,
    };
    const tasks = taskMap([master, ...children]);
    const closed = treeItems(tasks, [master.key], view());
    expect(closed.filter((item) => item.kind === 'row')).toHaveLength(1);
    expect(closed[1]).toMatchObject({ kind: 'fold', entries: children });
    const opened = treeItems(
      tasks,
      [master.key],
      view({ openFolds: new Set([foldKey('repo', taskEntrySelectionKey(master.key))]) }),
    );
    expect(
      opened
        .filter((item) => item.kind === 'row')
        .slice(1)
        .map((item) => item.row.entry),
    ).toEqual(children);
  }
});

it('reaches unlisted and missing-parent roots and each declared child once in authored order', () => {
  // Native amendment 4: these documents name a missing or unreciprocated parent,
  // so the server emits them as roots. The browser follows that one relation.
  const unlisted = task('unlisted', { parent: undefined });
  const missingParent = task('missing-parent', { parent: undefined });
  const second = task('second', { parent: unlisted.key });
  const first = task('first', { parent: unlisted.key });
  unlisted.children = [second.key, first.key];
  const entries = [unlisted, missingParent, first, second];
  const rows = treeItems(taskMap(entries), [unlisted.key, missingParent.key], view());
  expect(rows.filter((item) => item.kind === 'row').map((item) => item.row.entry.id)).toEqual([
    'unlisted',
    'second',
    'first',
    'missing-parent',
  ]);
  expect(new Set(rows.filter((item) => item.kind === 'row').map((item) => item.row.key)).size).toBe(
    entries.length,
  );
});
