import { taskEntrySelectionKey } from '../../data/taskReference';
import type { TaskEntry } from '../../types/projection';
import { rowBand } from './rowState';

export interface TreeRow {
  key: string;
  entry: TaskEntry;
  depth: number;
  parentKey?: string;
}

export type TreeItem =
  | { kind: 'row'; row: TreeRow; hasChildren: boolean; collapsed: boolean; held: boolean }
  | {
      kind: 'fold';
      key: string;
      depth: number;
      entries: TaskEntry[];
      counts: Pick<TaskEntry['counts'], 'completed' | 'aborted'>;
      open: boolean;
      held: boolean;
    };

export interface TreeView {
  collapsedKeys: ReadonlySet<string>;
  openFolds: ReadonlySet<string>;
  selectedKey: string | null;
  // The parents and folds whose hold a click has ended while this document stays chosen (D309):
  // a held parent stands open over the chosen document only until its own toggle is used.
  released: ReadonlySet<string>;
  scope: string;
}

export const foldKey = (scope: string, parentKey: string): string => `fold:${scope}:${parentKey}`;
export const isFoldKey = (key: string): boolean => key.startsWith('fold:');

function terminalFold(
  entries: TaskEntry[],
  depth: number,
  counts: Pick<TaskEntry['counts'], 'completed' | 'aborted'>,
  parent: TaskEntry | undefined,
  chosenAncestors: ReadonlySet<string>,
  view: TreeView,
): Extract<TreeItem, { kind: 'fold' }> {
  const key = foldKey(view.scope, parent ? taskEntrySelectionKey(parent.key) : '');
  const held = !view.released.has(key) && entries.some((entry) => chosenAncestors.has(entry.key));
  return { kind: 'fold', key, depth, entries, counts, open: held || view.openFolds.has(key), held };
}

/** Placement only: follow the server's ordered children and each task's own stage.
 * Closed terminal folds hold entries, never constructed child rows or descendant priorities. */
export function treeItems(
  tasks: Record<string, TaskEntry>,
  roots: string[],
  view: TreeView,
): TreeItem[] {
  const chosenAncestors = new Set<string>();
  let chosen = Object.values(tasks).find(
    (task) => taskEntrySelectionKey(task.key) === view.selectedKey,
  );
  while (chosen && !chosenAncestors.has(chosen.key)) {
    chosenAncestors.add(chosen.key);
    chosen = chosen.parent ? tasks[chosen.parent] : undefined;
  }
  const seen = new Set<string>();
  const items: TreeItem[] = [];
  const emit = (entry: TaskEntry, depth: number): void => {
    const key = taskEntrySelectionKey(entry.key);
    if (seen.has(key)) return;
    seen.add(key);
    const held = !view.released.has(key) && entry.children.some((ref) => chosenAncestors.has(ref));
    const hasChildren = entry.children.length > 0;
    const collapsed = hasChildren && view.collapsedKeys.has(key) && !held;
    items.push({
      kind: 'row',
      row: {
        key,
        entry,
        depth,
        parentKey: entry.parent ? taskEntrySelectionKey(entry.parent) : undefined,
      },
      hasChildren,
      collapsed,
      held,
    });
    if (!collapsed) walk(entry.children, depth + 1, entry);
  };
  const walk = (references: string[], depth: number, parent?: TaskEntry): void => {
    const terminal: TaskEntry[] = [];
    // A child fold has the parent's published counts. Repository-root folds
    // have no parent entry; count those roots during this one placement pass.
    const rootCounts = { completed: 0, aborted: 0 };
    const ordered = references
      .map((key) => tasks[key])
      .filter(Boolean)
      .sort((left, right) => rowBand(left) - rowBand(right));
    for (const entry of ordered) {
      const reference = entry.key;
      if (seen.has(taskEntrySelectionKey(reference))) continue;
      if (entry.stage === 'completed' || entry.stage === 'aborted') {
        terminal.push(entry);
        if (!parent) rootCounts[entry.stage]++;
      } else emit(entry, depth);
    }
    if (terminal.length === 0) return;
    const fold = terminalFold(
      terminal,
      depth,
      parent ? parent.counts : rootCounts,
      parent,
      chosenAncestors,
      view,
    );
    items.push(fold);
    if (fold.open) for (const entry of terminal) emit(entry, depth + 1);
  };
  walk(roots, 0);
  return items;
}
