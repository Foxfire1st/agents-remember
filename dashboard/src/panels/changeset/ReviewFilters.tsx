// Filters use only the classification already held by the reviewer.
import type { TaskChangeset, MasterChangeset } from '../../data/changeset';
import { FILTER_NOTES, laneFiles, type ReviewChangeContext, type ReviewChangeFilter } from './reviewChangeSet';

function FilterButton({
  name,
  context,
  data,
  filter,
  onFilter,
}: {
  name: ReviewChangeFilter;
  context: ReviewChangeContext;
  data: TaskChangeset | MasterChangeset | null;
  filter: ReviewChangeFilter;
  onFilter: (next: ReviewChangeFilter) => void;
}) {
  const files = laneFiles(context, name);
  const waiting = context.lane?.phase === 'loading' ? 'reading…' : 'the change set could not be measured';
  const count = name === 'all' ? (data?.code.length ?? '…') : (files?.length ?? waiting);
  const labels = { all: 'All', unexplained: 'Changes without an invariant', unknown: 'Unknown attribution' };
  return (
    <button
      type="button"
      aria-pressed={filter === name}
      disabled={name !== 'all' && !files}
      onClick={() => onFilter(name)}
    >
      {labels[name]} ({count})
    </button>
  );
}
export function ReviewFilters({
  context,
  data,
  filter,
  onFilter,
}: {
  context?: ReviewChangeContext;
  data: TaskChangeset | MasterChangeset | null;
  filter: ReviewChangeFilter;
  onFilter: (next: ReviewChangeFilter) => void;
}) {
  if (!context) return null;
  return (
    <div style={{ padding: '0.4rem', flexShrink: 0 }}>
      {(['all', 'unexplained', 'unknown'] as const).map((name) => (
        <FilterButton
          key={name}
          name={name}
          context={context}
          data={data}
          filter={filter}
          onFilter={onFilter}
        />
      ))}
      {filter !== 'all' ? <p>{FILTER_NOTES[filter]}</p> : null}
    </div>
  );
}
