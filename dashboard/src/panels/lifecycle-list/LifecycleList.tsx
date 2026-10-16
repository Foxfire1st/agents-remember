import {
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type RefObject,
} from 'react';

import { Header, ListBox, ListBoxItem, ListBoxSection } from 'react-aria-components';

import { cx } from '../../../styled-system/css';
import { dashboardStore } from '../../data/store';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { Panel } from '../../grammar/Panel';
import type { TaskEntry } from '../../types/projection';
import {
  statusLabel,
  statusTone,
  taskStatusKey,
  TaskStatusWord,
  TaskStatusClock,
} from '../detail-panel/pageParts';
import { dim } from '../detail-panel/pageStyles';
import { TaskGroupDisclosure } from '../TaskGroupDisclosure';
import { useCollapsedTaskGroups } from '../useCollapsedTaskGroups';
import { caretCell } from './columnLook';
import { progressFigure, rowHover, shortTaskId } from './rowState';
import { isFoldKey, treeItems, type TreeItem, type TreeView } from './treeRows';

import { stepMarkStates } from '../detail-panel/model';
import { StepMarks } from '../detail-panel/pageParts';
import {
  groupCount,
  groupHeader,
  indent,
  listBox,
  markCell,
  rowFigure,
  rowIdCell,
  rowItem,
  rowLine,
  rowRepo,
  rowTitle,
  rowWordCell,
  section,
  sizing,
  statusDot,
  TaskPivotBar,
  type Pivot,
} from './columnPresentation';
import { docProgress } from './docProgress';

// The native task tree, grouped by repository or served status. Selecting a row
// opens that entry; keyboard navigation and the approved column layout stay.
//
// The look is the one the developer approved (MIK-R144 rule 3b): a row is one line without a
// frame (caret, mark, id, title, word, figure), the tree is indented by padding, and nothing
// leaves the column. The head of the column carries the bell of the attention queue (rule 3a),
// so the tree has the column's whole height. The measures are the approved pages' measures; see
// columnLook.ts for how they are written.
// The lines of completed ones the developer opened. Not remembered: the column opens with all of
// them closed, and a line that holds the chosen document is open by itself.
function useOpenFolds(): { openFolds: ReadonlySet<string>; toggleFold: (key: string) => void } {
  const [openFolds, setOpenFolds] = useState<ReadonlySet<string>>(() => new Set());
  const toggleFold = useCallback((key: string) => {
    setOpenFolds((current) => {
      const next = new Set(current);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }, []);
  return { openFolds, toggleFold };
}

// Reveal each selected task once, including a selection made by the page's drill-down.
// A task without a visible row has nothing to reveal.
function useRevealChosen(
  listRef: RefObject<HTMLElement | null>,
  chosenKey: string | null,
  present: boolean,
): void {
  const shown = useRef<string | null>(null);
  useEffect(() => {
    const list = listRef.current;
    if (!chosenKey || shown.current === chosenKey) return;
    if (!list || !present) {
      shown.current = chosenKey;
      return;
    }
    const show = (): boolean => {
      const options = list.querySelectorAll<HTMLElement>("[role='option']");
      const chosen = [...options].find((option) => option.dataset.key === chosenKey);
      if (!chosen) return false;
      shown.current = chosenKey;
      // jsdom has no scrollIntoView.
      chosen.scrollIntoView?.({ block: 'nearest' });
      // Arrow keys continue from the newly revealed selection.
      chosen.focus({ preventScroll: true });
      return true;
    };
    if (show()) return;
    // The row of a parent that was closed is not in the page yet: React Aria builds its list in
    // a pass of its own, after this one.
    const observer = new MutationObserver(() => {
      if (show()) observer.disconnect();
    });
    observer.observe(list, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [listRef, chosenKey, present]);
}

// → opens and ← closes the parent row that has the keyboard's focus: the one addition to what
// the column does (MIK-R144 rule 5). A vertical list gives these two keys no other meaning. A
// parent that the chosen document holds open does not close.
function treeKey(event: KeyboardEvent, toggleCollapsed: (key: string) => void): void {
  const arrow = plainArrow(event);
  if (!arrow) return;
  const row =
    event.target instanceof Element ? event.target.closest<HTMLElement>('[data-expanded]') : null;
  const key = row?.dataset.key;
  if (!row || !key) return;
  event.preventDefault();
  event.stopPropagation();
  const open = row.dataset.expanded === 'true';
  if (row.dataset.held === undefined && open !== (arrow === 'open')) toggleCollapsed(key);
}

// What an arrow key asks of a parent row. With a modifier the two keys are the browser's (back,
// forward) and the system's, and the row leaves them alone.
function plainArrow(event: KeyboardEvent): 'open' | 'close' | null {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return null;
  if (event.key === 'ArrowRight') return 'open';
  return event.key === 'ArrowLeft' ? 'close' : null;
}

// Memoized (tab-switch CPU): a persistent rail panel — the shell re-renders on every view
// switch with unchanged props, and the memo gate skips this subtree then; the list's own store
// subscriptions still drive its updates.
export const LifecycleList = memo(
  LifecycleListImpl,
  (previous, next) =>
    (previous.active === false && next.active === false) ||
    (previous.active === next.active &&
      previous.selectedId === next.selectedId &&
      previous.onSelect === next.onSelect),
);

const taskSnapshot = () => dashboardStore.getState().tasks;

interface OperationGroup {
  key: string;
  label: string;
  count: number;
  items: TreeItem[];
}

function LifecycleListImpl({
  selectedId,
  onSelect,
  active = true,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
  active?: boolean;
}) {
  const subscribe = useCallback(
    (changed: () => void) => (active ? dashboardStore.subscribe(changed) : () => {}),
    [active],
  );
  const tasks = useSyncExternalStore(subscribe, taskSnapshot);
  const [pivot, setPivot] = useState<Pivot>('repo');
  const { collapsedKeys, toggleCollapsed } = useCollapsedTaskGroups();
  const { openFolds, toggleFold } = useOpenFolds();
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const list = listRef.current;
    if (!list || !active) return;
    const handle = (event: KeyboardEvent): void => treeKey(event, toggleCollapsed);
    list.addEventListener('keydown', handle);
    return () => list.removeEventListener('keydown', handle);
  }, [active, toggleCollapsed]);
  const view = { collapsedKeys, openFolds, selectedKey: selectedId };
  const groups = groupRows(tasks, pivot, view);
  const present = groups.some((group) =>
    group.items.some((item) => item.kind === 'row' && item.row.key === selectedId),
  );
  useRevealChosen(listRef, selectedId, present);
  const select = (key: string): void => {
    if (isFoldKey(key)) toggleFold(key);
    else onSelect(key);
  };
  return (
    <TaskStatusClock
      active={active && Object.values(tasks).some((entry) => Boolean(entry.eventAt))}
    >
      <Panel
        head={<TaskPivotBar count={Object.keys(tasks).length} pivot={pivot} onPivot={setPivot} />}
        className={sizing}
      >
        <div ref={listRef} data-active={active}>
          <ListBox
            aria-label="Tasks"
            selectionMode="single"
            selectedKeys={selectedId ? [selectedId] : []}
            onSelectionChange={(keys) => {
              const key = [...keys][0];
              if (typeof key === 'string') select(key);
            }}
            className={listBox}
          >
            {groups.map((group) => (
              <TaskGroupSection
                key={group.key}
                group={group}
                flat={pivot === 'phase'}
                showRepo={pivot === 'phase'}
                tasks={tasks}
                onToggleCollapsed={toggleCollapsed}
              />
            ))}
          </ListBox>
        </div>
      </Panel>
    </TaskStatusClock>
  );
}

function TaskGroupSection({
  group,
  flat,
  showRepo,
  tasks,
  onToggleCollapsed,
}: {
  group: OperationGroup;
  flat: boolean;
  showRepo: boolean;
  tasks: Record<string, TaskEntry>;
  onToggleCollapsed: (key: string) => void;
}) {
  return (
    <ListBoxSection className={section}>
      <Header className={groupHeader}>
        {group.label}
        <span className={cx(groupCount, dim)}>{group.count}</span>
      </Header>
      {group.items.map((item) =>
        item.kind === 'fold' ? (
          <FoldRow key={item.key} item={item} />
        ) : (
          <TaskRow
            key={item.row.key}
            item={item}
            flat={flat}
            showRepo={showRepo}
            tasks={tasks}
            onToggleCollapsed={onToggleCollapsed}
          />
        ),
      )}
    </ListBoxSection>
  );
}

type RowItem = Extract<TreeItem, { kind: 'row' }>;
type FoldItem = Extract<TreeItem, { kind: 'fold' }>;

// The caret of a parent row. BY PHASE has no tree and no caret; in the tree a row without
// children keeps the caret's room.
function RowCaret({
  item,
  flat,
  onToggleCollapsed,
}: {
  item: RowItem;
  flat: boolean;
  onToggleCollapsed: (key: string) => void;
}) {
  if (flat) return null;
  if (!item.hasChildren) return <span className={caretCell} aria-hidden="true" />;
  return (
    <TaskGroupDisclosure
      label={item.row.entry.title}
      collapsed={item.collapsed}
      held={item.held}
      onToggle={() => onToggleCollapsed(item.row.key)}
    />
  );
}

function RowMark({ entry }: { entry: TaskEntry }) {
  const status = taskStatusKey(entry);
  return (
    <span
      className={markCell}
      aria-label={`Task progress: ${statusLabel(status)}`}
      data-testid="task-state"
    >
      <span className={statusDot({ tone: statusTone(status) })} aria-hidden="true" />
    </span>
  );
}

function TaskRow({
  item,
  flat,
  showRepo,
  tasks,
  onToggleCollapsed,
}: {
  item: RowItem;
  flat: boolean;
  showRepo: boolean;
  tasks: Record<string, TaskEntry>;
  onToggleCollapsed: (key: string) => void;
}) {
  const row = item.row;
  const entry = row.entry;
  const { id, form, tier } = rowPresentation(entry, tasks, flat);
  return (
    <ListBoxItem
      id={row.key}
      isDisabled={!entry.reference}
      textValue={id && entry.parent ? `${id} ${entry.title}` : entry.title}
      className={rowItem}
      data-depth={row.depth}
      data-parent-key={row.parentKey}
      data-tier={tier}
      data-expanded={expandedValue(item, flat)}
      data-held={item.held ? '' : undefined}
      // The mark is for the eye; the reveal gives the row the keyboard's focus. (A React Aria
      // list item passes no `aria-current` on.)
    >
      <TaskRowLine
        item={item}
        id={id}
        form={form}
        flat={flat}
        showRepo={showRepo}
        onToggleCollapsed={onToggleCollapsed}
      />
    </ListBoxItem>
  );
}

function expandedValue(item: RowItem, flat: boolean): string | undefined {
  return item.hasChildren && !flat ? String(!item.collapsed) : undefined;
}

function rowPresentation(entry: TaskEntry, tasks: Record<string, TaskEntry>, flat: boolean) {
  const form = entry.summary?.form ?? 'task';
  return {
    id: flat ? (form === 'task' ? entry.id : '') : shortTaskId(entry, tasks),
    form,
    tier: rowTier(entry, tasks),
  };
}

function rowTier(entry: TaskEntry, tasks: Record<string, TaskEntry>) {
  if (entry.summary?.form === 'sprint') return 'orchestration';
  if (
    entry.summary?.form === 'master' &&
    entry.parent &&
    tasks[entry.parent]?.summary?.form === 'sprint'
  )
    return 'management';
  return undefined;
}

function TaskRowLine({
  item,
  id,
  form,
  flat,
  showRepo,
  onToggleCollapsed,
}: {
  item: RowItem;
  id: string;
  form: string;
  flat: boolean;
  showRepo: boolean;
  onToggleCollapsed: (key: string) => void;
}) {
  const row = item.row;
  const entry = row.entry;
  return (
    <div className={rowLine} style={indent(row.depth)} title={rowHover(entry)}>
      <RowCaret item={item} flat={flat} onToggleCollapsed={onToggleCollapsed} />
      <RowMark entry={entry} />
      {id ? <span className={rowIdCell({ long: flat })}>{id}</span> : null}
      <span
        className={rowTitle({ weight: form === 'task' ? undefined : 'parent' })}
        data-part="title"
      >
        {entry.title}
      </span>
      {showRepo ? <span className={rowRepo}>{entry.reference?.repository}</span> : null}
      <span className={rowWordCell({ tone: taskStatusKey(entry) === 'works' ? 'run' : 'rest' })}>
        <TaskStatusWord task={entry} testid="task-word" mark={false} />
      </span>
      <RowFigure entry={entry} form={form} />
    </div>
  );
}

function RowFigure({ entry, form }: { entry: TaskEntry; form: string }) {
  return (
    <span className={cx(rowFigure, dim)} title={docProgress(entry).progressNoun}>
      {form === 'task' ? (
        <StepMarks
          states={stepMarkStates(entry.summary?.steps ?? [])}
          steps={entry.summary?.steps}
        />
      ) : null}
      {form === 'task' ? null : (
        <span data-testid="child-activity">
          {entry.counts.works} works · {entry.counts.paused} paused
          {entry.counts.unknown > 0 ? ` · ${entry.counts.unknown} unknown` : ''} ·{' '}
        </span>
      )}
      {progressFigure(entry)}
    </span>
  );
}

function foldText(item: FoldItem): string {
  const { aborted } = item.counts;
  const completed = `${item.counts.completed} completed`;
  return aborted > 0 ? `${completed}, ${aborted} aborted` : completed;
}

function foldHover(item: FoldItem): string {
  return item.held
    ? 'Stays open: the chosen document is inside'
    : `${item.open ? 'Hide' : 'Show'} ${foldText(item)}`;
}

function FoldRow({ item }: { item: FoldItem }) {
  const text = foldText(item);
  return (
    <ListBoxItem
      id={item.key}
      textValue={text}
      // The caret is a sign for the eye; the name says the same in words.
      aria-label={`${text}, ${item.open ? 'shown' : 'hidden'}`}
      className={rowItem}
      data-depth={item.depth}
      data-fold={item.open ? 'open' : 'closed'}
    >
      <div className={rowLine} style={indent(item.depth)} title={foldHover(item)}>
        <span className={caretCell} aria-hidden="true">
          {item.open ? '▾' : '▸'}
        </span>
        <span className={markCell}>
          <span className={statusDot({ tone: 'done' })} aria-hidden="true" />
        </span>
        <span className={rowTitle({ weight: 'fold' })}>{text}</span>
      </div>
    </ListBoxItem>
  );
}

/** Group the served entries, without rebuilding their parent relation or status. */
function appendMembers(
  root: TaskEntry,
  tasks: Record<string, TaskEntry>,
  pivot: Pivot,
  seen: Set<string>,
  members: TaskEntry[],
) {
  const pending = [root];
  while (pending.length) {
    const entry = pending.pop()!;
    if (seen.has(entry.key)) continue;
    seen.add(entry.key);
    members.push(entry);
    if (pivot === 'repo') {
      for (const child of [...entry.children].reverse()) {
        if (tasks[child]) pending.push(tasks[child]);
      }
    }
  }
}

function groupedEntries(tasks: Record<string, TaskEntry>, pivot: Pivot): Map<string, TaskEntry[]> {
  const byKey = new Map<string, TaskEntry[]>();
  const seen = new Set<string>();
  for (const root of Object.values(tasks)) {
    if (pivot === 'repo' && root.parent && tasks[root.parent]) continue;
    const key = pivot === 'repo' ? (root.reference?.repository ?? '') : taskStatusKey(root);
    const members = byKey.get(key) ?? [];
    appendMembers(root, tasks, pivot, seen, members);
    byKey.set(key, members);
  }
  return byKey;
}

const PHASE_ORDER = ['works', 'paused', 'unknown', 'started', 'planned', 'completed', 'aborted'];

function groupRows(
  tasks: Record<string, TaskEntry>,
  pivot: Pivot,
  view: Omit<TreeView, 'scope'>,
): OperationGroup[] {
  return [...groupedEntries(tasks, pivot)]
    .map(([key, members]) => ({
      key,
      label: pivot === 'repo' ? key : statusLabel(taskStatusKey(members[0])),
      count: members.length,
      items:
        pivot === 'repo'
          ? treeItems(
              tasks,
              members
                .filter((entry) => !entry.parent || !tasks[entry.parent])
                .map((entry) => entry.key),
              { ...view, scope: key },
            )
          : members.map((entry) => ({
              kind: 'row' as const,
              row: { entry, key: taskEntrySelectionKey(entry.key), depth: 0 },
              hasChildren: false,
              collapsed: false,
              held: false,
            })),
    }))
    .sort((left, right) =>
      pivot === 'repo'
        ? left.key.localeCompare(right.key)
        : PHASE_ORDER.indexOf(left.key) - PHASE_ORDER.indexOf(right.key) ||
          left.key.localeCompare(right.key),
    );
}
