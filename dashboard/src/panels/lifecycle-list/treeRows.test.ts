import { describe, expect, it } from 'vitest';

import type { RowBand } from './rowState';
import { foldKey, treeItems, type TreeItem, type TreeRow, type TreeView } from './treeRows';

interface Row extends TreeRow {
  band: RowBand;
}

// One row of a made-up tree; its key says its place in the creation order.
function row(key: string, band: RowBand, parentKey?: string): Row {
  return { key, parentKey, band, createdAt: key, fallbackOrder: key, depth: 0 };
}

function view(over: Partial<TreeView> = {}): TreeView {
  return { collapsedKeys: new Set(), openFolds: new Set(), selectedKey: null, scope: 'repo', ...over };
}

// The lines as the column shows them: "key@depth" for a row, "(n over)" for the line of completed ones.
function lines(items: TreeItem<Row>[]): string[] {
  return items.map((item) =>
    item.kind === 'row'
      ? `${item.row.key}@${item.row.depth}`
      : `(${item.rows.length} over${item.open ? ', open' : ''})@${item.depth}`,
  );
}

const show = (rows: Row[], over: Partial<TreeView> = {}) =>
  lines(treeItems(rows, (item) => item.band, view(over)));

describe('the tree of the tasks column', () => {
  const master = row('m', 1);
  const rows = [
    master,
    row('m1-waits', 2, 'm'),
    row('m2-done', 3, 'm'),
    row('m3-works', 1, 'm'),
    row('m4-asks', 0, 'm'),
    row('m5-done', 3, 'm'),
    row('m6-works', 1, 'm'),
  ];

  it('orders the rows under a parent by need, then by creation, with the completed behind one line', () => {
    expect(show(rows)).toEqual([
      'm@0',
      'm4-asks@1',
      'm3-works@1',
      'm6-works@1',
      'm1-waits@1',
      '(2 over)@1',
    ]);
  });

  it('opens the line of completed ones in place', () => {
    expect(show(rows, { openFolds: new Set([foldKey('repo', 'm')]) }).slice(-3)).toEqual([
      '(2 over, open)@1',
      'm2-done@2',
      'm5-done@2',
    ]);
    // The same parent in another group keeps its own line.
    expect(show(rows, { openFolds: new Set([foldKey('other', 'm')]) }).slice(-1)).toEqual(['(2 over)@1']);
  });

  it('keeps a completed parent that still holds open work out of the line', () => {
    const tree = [row('a-done', 3), row('a1-works', 1, 'a-done'), row('b-done', 3), row('b1-done', 3, 'b-done')];
    expect(show(tree)).toEqual(['a-done@0', 'a1-works@1', '(1 over)@0']);
  });

  it('stays open down to the chosen document', () => {
    const closed = { collapsedKeys: new Set(['m']) };
    expect(show(rows, closed)).toEqual(['m@0']);
    // The chosen task is inside the closed master, behind the closed line: both are held open.
    const items = treeItems(rows, (item) => item.band, view({ ...closed, selectedKey: 'm5-done' }));
    expect(lines(items).slice(-3)).toEqual(['(2 over, open)@1', 'm2-done@2', 'm5-done@2']);
    expect(items[0]).toMatchObject({ kind: 'row', collapsed: false, held: true });
    expect(items.find((item) => item.kind === 'fold')).toMatchObject({ held: true });
    // Choosing the master itself holds nothing open.
    expect(show(rows, { ...closed, selectedKey: 'm' })).toEqual(['m@0']);
  });

  it('drops no row when two parents name each other', () => {
    const cycle = [row('x', 1, 'y'), row('y', 1, 'x'), row('z', 1)];
    expect(show(cycle).sort()).toEqual(['x@0', 'y@1', 'z@0']);
  });

  it('lists a row once when two completed parents name each other', () => {
    // Each is the other's completed child. The second one's line would hold the first, which
    // stands above it already: a line that opens to nothing.
    const cycle = [row('x', 3, 'y'), row('y', 3, 'x')];
    expect(show(cycle)).toEqual(['x@0', '(1 over)@1']);
    const opened = { openFolds: new Set([foldKey('repo', 'x'), foldKey('repo', 'y')]) };
    expect(show(cycle, opened)).toEqual(['x@0', '(1 over, open)@1', 'y@2']);
    const last = treeItems(cycle, (item) => item.band, view(opened)).at(-1);
    expect(last).toMatchObject({ kind: 'row', hasChildren: false });
  });
});
