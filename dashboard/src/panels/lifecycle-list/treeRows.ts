import type { RowBand } from './rowState';

// The tree of one repository group, flattened to the lines the list shows (MIK-R144 rule 3).
//
// Under a parent stand first the rows that ask for the developer, then those in work, then the
// waiting ones, each part in the order of creation. Rows that are over (completed, abandoned) and
// hold no open work beneath them stand behind one line with their count, which opens in place.
// The tree is always open down to the chosen document: a parent that holds it does not close,
// and a line of completed ones that holds it is open.

export interface TreeRow {
  key: string;
  parentKey?: string;
  createdAt: string;
  fallbackOrder: string;
  depth: number;
}

export type TreeItem<T extends TreeRow> =
  | { kind: 'row'; row: T; hasChildren: boolean; collapsed: boolean; held: boolean }
  | { kind: 'fold'; key: string; depth: number; rows: T[]; open: boolean; held: boolean };

export interface TreeView {
  // Parents the developer closed. A closed parent keeps its own children's state for later.
  collapsedKeys: ReadonlySet<string>;
  // Lines of completed ones the developer opened.
  openFolds: ReadonlySet<string>;
  selectedKey: string | null;
  // Tells the lines of one group from those of another: a root line has no parent to name.
  scope: string;
}

const OVER: RowBand = 3;

export function compareRows(left: TreeRow, right: TreeRow): number {
  const created = left.createdAt.localeCompare(right.createdAt);
  return created || left.fallbackOrder.localeCompare(right.fallbackOrder);
}

export function foldKey(scope: string, parentKey: string): string {
  return `fold:${scope}:${parentKey}`;
}

export function isFoldKey(key: string): boolean {
  return key.startsWith('fold:');
}

function childrenByParent<T extends TreeRow>(rows: T[]): Map<string, T[]> {
  const keys = new Set(rows.map((row) => row.key));
  const byParent = new Map<string, T[]>();
  for (const row of rows) {
    const nested = row.parentKey && row.parentKey !== row.key && keys.has(row.parentKey);
    const parent = nested && row.parentKey ? row.parentKey : '';
    const siblings = byParent.get(parent);
    if (siblings) siblings.push(row);
    else byParent.set(parent, [row]);
  }
  return byParent;
}

// A fact of a row's whole subtree, read once per row. A cycle of parents (two sprint documents
// naming each other) ends at the row that is met twice, which then answers for itself alone.
function subtree<T extends TreeRow, V>(
  byParent: Map<string, T[]>,
  own: (row: T) => V,
  merge: (value: V, child: V) => V,
): (row: T) => V {
  const known = new Map<string, V>();
  const visiting = new Set<string>();
  const read = (row: T): V => {
    if (known.has(row.key)) return known.get(row.key) as V;
    if (visiting.has(row.key)) return own(row);
    visiting.add(row.key);
    const value = (byParent.get(row.key) ?? []).reduce((sum, kid) => merge(sum, read(kid)), own(row));
    visiting.delete(row.key);
    known.set(row.key, value);
    return value;
  };
  return read;
}

export function treeItems<T extends TreeRow>(
  rows: T[],
  bandOf: (row: T) => RowBand,
  view: TreeView,
): TreeItem<T>[] {
  const byParent = childrenByParent(rows);
  // A parent stands where its most pressing row stands: a completed master that still holds a
  // task in work is in work.
  const band = subtree<T, RowBand>(byParent, bandOf, (value, child) => (child < value ? child : value));
  const holdsChosen = subtree<T, boolean>(
    byParent,
    (row) => row.key === view.selectedKey,
    (value, child) => value || child,
  );
  const items: TreeItem<T>[] = [];
  const seen = new Set<string>();

  // The rows under a parent that do not stand in the tree yet. Two parents that name each other
  // would else list one another: a row is listed once, and a parent whose only child stands
  // above it has no caret and no line of completed ones.
  const kidsOf = (parentKey: string): T[] =>
    (byParent.get(parentKey) ?? []).filter((kid) => !seen.has(kid.key));

  const emit = (row: T, depth: number): void => {
    if (seen.has(row.key)) return;
    seen.add(row.key);
    const kids = kidsOf(row.key);
    const held = kids.some(holdsChosen);
    const collapsed = kids.length > 0 && view.collapsedKeys.has(row.key) && !held;
    items.push({ kind: 'row', row: { ...row, depth }, hasChildren: kids.length > 0, collapsed, held });
    if (!collapsed) walk(row.key, depth + 1);
  };

  const walk = (parentKey: string, depth: number): void => {
    const kids = kidsOf(parentKey);
    const open = kids.filter((kid) => band(kid) < OVER);
    const over = kids.filter((kid) => band(kid) === OVER).sort(compareRows);
    for (const row of open.sort((a, b) => band(a) - band(b) || compareRows(a, b))) emit(row, depth);
    if (over.length === 0) return;
    const key = foldKey(view.scope, parentKey);
    const held = over.some(holdsChosen);
    const isOpen = held || view.openFolds.has(key);
    items.push({ kind: 'fold', key, depth, rows: over, open: isOpen, held });
    if (isOpen) for (const row of over) emit(row, depth + 1);
  };

  // What a root leads to, shown or not: a closed parent and a closed line hide rows, they do
  // not cut them off.
  const reached = new Set<string>();
  const reach = (parentKey: string): void => {
    for (const kid of byParent.get(parentKey) ?? []) {
      if (reached.has(kid.key)) continue;
      reached.add(kid.key);
      reach(kid.key);
    }
  };

  walk('', 0);
  reach('');
  // Rows a parent cycle keeps from every root are listed at the top level, never dropped.
  for (const row of [...rows].sort(compareRows)) {
    if (reached.has(row.key)) continue;
    reached.add(row.key);
    reach(row.key);
    emit(row, 0);
  }
  return items;
}
