// The master's completed list and latest-five preview share facts, time and row rendering.
import { useState } from 'react';
import { shortWhen } from './model';
import {
  NEWEST_COMPLETED,
  orderCompleted,
  type CompletedEntry,
  type CompletedSort,
} from './completedTasksModel';
import { completedName, completedTable, sortHead } from './completedTasksStyles';
import type { RowTarget } from './indexRows';

interface CompletedListProps {
  entries: CompletedEntry[];
  targetFor: (entry: CompletedEntry) => RowTarget | null;
  preview?: boolean;
}
function SortHead({
  column,
  label,
  sort,
  change,
}: {
  column: CompletedSort['column'];
  label: string;
  sort: CompletedSort;
  change: (column: CompletedSort['column']) => void;
}) {
  const active = sort.column === column;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button type="button" className={sortHead} onClick={() => change(column)}>
        {label}
        {active ? <span aria-hidden="true">{sort.direction === 'asc' ? ' ↑' : ' ↓'}</span> : null}
      </button>
    </th>
  );
}
function CompletedRow({ entry, target }: { entry: CompletedEntry; target: RowTarget | null }) {
  return (
    <tr data-testid={`group-row-${entry.position}`}>
      <td data-completed-number>{entry.id}</td>
      <td>
        {target ? (
          <button
            type="button"
            className={completedName}
            onClick={target.run}
            data-testid={target.testid}
            title={target.title ?? `${entry.id} — ${entry.name}`}
            aria-label={`${entry.id} — ${entry.name}`}
          >
            {entry.name}
          </button>
        ) : (
          <span>{entry.name}</span>
        )}
      </td>
      <td>
        {entry.at ? (
          <time dateTime={entry.at}>{shortWhen(entry.at)}</time>
        ) : (
          <span title="Completion time unknown">—</span>
        )}
      </td>
    </tr>
  );
}
export function CompletedTaskList({ entries, targetFor, preview = false }: CompletedListProps) {
  const [sort, setSort] = useState<CompletedSort>(NEWEST_COMPLETED);
  const change = (column: CompletedSort['column']) =>
    setSort((old) => ({
      column,
      direction:
        old.column === column
          ? old.direction === 'asc'
            ? 'desc'
            : 'asc'
          : column === 'time'
            ? 'desc'
            : 'asc',
    }));
  const ordered = orderCompleted(entries, preview ? NEWEST_COMPLETED : sort);
  const shown = preview ? ordered.slice(0, 5) : ordered;
  if (shown.length === 0) return null;
  return (
    <table className={completedTable} data-testid={preview ? 'completed-preview' : 'group-table'}>
      <colgroup>
        <col style={{ width: '17%' }} />
        <col style={{ width: '53%' }} />
        <col style={{ width: '30%' }} />
      </colgroup>
      {!preview ? (
        <thead>
          <tr>
            <SortHead column="number" label="Number" sort={sort} change={change} />
            <th scope="col">Name</th>
            <SortHead column="time" label="Completed" sort={sort} change={change} />
          </tr>
        </thead>
      ) : null}
      <tbody>
        {shown.map((entry) => (
          <CompletedRow key={entry.position} entry={entry} target={targetFor(entry)} />
        ))}
      </tbody>
    </table>
  );
}
