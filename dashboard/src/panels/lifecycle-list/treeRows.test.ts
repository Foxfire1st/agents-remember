import { expect, it } from 'vitest';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { hierarchy, task, taskMap, taskSummary } from './test-utils';
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

it('orders the started band by host time, newest first, and leaves untimed rows after them', () => {
  // MIK-R174: MIK stands first while it works; once it pauses it used to fall back to its
  // document key's place among masters whose time is unknown. A time it has now orders it.
  const master = (id: string, over: Parameters<typeof task>[1] = {}) =>
    task(id, { summary: taskSummary({ kind: 'master', form: 'master' }), ...over });
  const sprint = master('S');
  const works = master('M-work', { activity: 'works', eventAt: '2026-10-17T19:00:00Z' });
  const untimedFirst = master('M-a');
  const newer = master('M-new', { eventAt: '2026-10-17T20:00:00Z' });
  const older = master('M-old', { eventAt: '2026-10-17T18:00:00Z' });
  const untimedSecond = master('M-b');
  const children = [works, untimedFirst, newer, older, untimedSecond];
  sprint.children = children.map((entry) => entry.key);
  for (const entry of children) entry.parent = sprint.key;
  const rows = treeItems(taskMap([sprint, ...children]), [sprint.key], view())
    .filter((item) => item.kind === 'row')
    .map((item) => item.row.entry.id);
  expect(rows).toEqual(['S', 'M-work', 'M-new', 'M-old', 'M-a', 'M-b']);
  // A missing time is never read as an epoch or as now: the two untimed rows keep the server's
  // order instead of being reordered against each other.
  expect(rows.slice(-2)).toEqual(['M-a', 'M-b']);
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
