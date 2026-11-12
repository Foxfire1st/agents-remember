// The records that this master's sub-task list and the records' own master field place apart.
// The server reports each one once, on this master's entry; the page shows them as served.
import { taskEntrySelectionKey, taskReferenceKey } from '../../data/taskReference';
import type { TaskEntry, TaskListDisagreement } from '../../types/projection';
import { Row, RowLink, Rows, SubHead } from './pageParts';
import { dim } from './pageStyles';

type Tasks = Record<string, TaskEntry>;

const titleOf = (tasks: Tasks, key: string | undefined, fallback: string): string =>
  (key ? tasks[key]?.title : undefined) ?? key ?? fallback;

// What the page says of each kind; the served fields carry everything it names.
const WHAT: Record<
  TaskListDisagreement['kind'],
  (item: TaskListDisagreement, tasks: Tasks) => string
> = {
  'names-another-master': (item, tasks) => {
    const named = item.recordMaster ? taskReferenceKey(item.recordMaster) : undefined;
    return `listed as ${item.row ?? 'a row'}; names ${titleOf(tasks, named, 'another master')}`;
  },
  'listed-twice': (item, tasks) =>
    `listed as ${item.row ?? 'a row'}; stands under ${titleOf(tasks, item.parent ?? undefined, 'another master')}`,
  'not-listed': (item) =>
    item.recordMaster ? 'names this master; not in this list' : 'names no master; in no list',
  'unresolvable-master': (item) =>
    `${item.row ? `listed as ${item.row}` : 'in no list'}; names master “${item.masterLiteral ?? ''}”, which is no task`,
};

// One row per served dispute: the server reports each once, so every field together names it.
const disputeKey = (item: TaskListDisagreement): string =>
  [
    item.kind,
    taskReferenceKey(item.record),
    item.row ?? '',
    item.recordMaster ? taskReferenceKey(item.recordMaster) : '',
    item.masterLiteral ?? '',
    item.parent ?? '',
  ].join('\u0000');

export function ListDisagreements({
  entry,
  tasks,
  onJump,
}: {
  entry: TaskEntry;
  tasks: Tasks;
  onJump: (key: string) => void;
}) {
  const items = entry.disagreements ?? [];
  if (items.length === 0) return null;
  return (
    <>
      <SubHead title="Records and list disagree" count={items.length} />
      <Rows cols="discarded" testid="list-disagreements">
        {items.map((item, index) => {
          const key = taskReferenceKey(item.record);
          const record = tasks[key];
          const what = WHAT[item.kind](item, tasks);
          return (
            <Row key={disputeKey(item)} testid={`list-disagreement-${index + 1}`}>
              <RowLink
                id={record?.id ?? ''}
                name={record?.title ?? item.record.path}
                onOpen={record ? () => onJump(taskEntrySelectionKey(record.key)) : undefined}
              />
              <span className={dim} title={what}>
                {what}
              </span>
            </Row>
          );
        })}
      </Rows>
    </>
  );
}
