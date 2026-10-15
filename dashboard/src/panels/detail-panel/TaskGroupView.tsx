// Completed master rows have time ordering; sprint and abandoned rows keep their authored order.
import type { DiscardedSubTaskNode, SubTaskRow, TaskDocNode } from '../../types/projection';
import { masterTaskCounts, primaryRequirement, subTaskKey } from './model';
import { Row, Rows, SubHead } from './pageParts';
import { dim } from './pageStyles';
import { DiscardedSubTaskHistory } from './skeletons';
import { CompletedTaskList } from './CompletedTaskList';
import { completedEntries } from './completedTasksModel';
import {
  IndexLink,
  taskRowFacts,
  masterRowFacts,
  resolveSprintMaster,
  resolveRowTarget,
  indexSlices,
  type IndexRowHandlers,
} from './indexRows';

// A completed/abandoned group view: one row per document as in the index, with
// the discarded-before-start history beside the abandoned ones.
export function GroupViewBody({
  refs,
  discarded,
  positions,
  handlers,
  parentId,
  allDocs,
}: {
  refs: SubTaskRow[];
  discarded: DiscardedSubTaskNode[] | undefined;
  positions: Map<SubTaskRow, number>;
  handlers: IndexRowHandlers;
  // The master whose tasks these are; absent on a sprint, whose rows are masters.
  parentId?: string;
  allDocs?: TaskDocNode[];
}) {
  const completed = parentId !== undefined && refs.every((ref) => ref.status === 'Completed');
  const completedHandlers = completed
    ? { ...handlers, sliceForRef: indexSlices(handlers.sliceDocs) }
    : handlers;
  return (
    <div>
      {refs.length > 0 ? (
        completed ? (
          <CompletedTaskList
            entries={completedEntries(
              refs,
              handlers.sliceDocs,
              parentId!,
              positions,
              completedHandlers.sliceForRef,
            )}
            targetFor={(entry) => resolveRowTarget(entry.ref, entry.position, completedHandlers)}
          />
        ) : (
          <Rows cols="group" testid="group-table">
            {refs.map((ref) => (
              <GroupRow
                key={subTaskKey(ref, positions.get(ref) ?? 0)}
                ref={ref}
                position={positions.get(ref) ?? 0}
                handlers={handlers}
                parentId={parentId}
                allDocs={allDocs}
              />
            ))}
          </Rows>
        )
      ) : null}
      {discarded && discarded.length > 0 ? (
        <>
          <SubHead title="Discarded before start" count={discarded.length} />
          <DiscardedSubTaskHistory items={discarded} />
        </>
      ) : null}
    </div>
  );
}

function GroupRow({
  ref,
  position,
  handlers,
  parentId,
  allDocs,
}: {
  ref: SubTaskRow;
  position: number;
  handlers: IndexRowHandlers;
  parentId: string | undefined;
  allDocs: TaskDocNode[] | undefined;
}) {
  if (parentId !== undefined) {
    const { id, name, match } = taskRowFacts(ref, handlers.sliceDocs, parentId);
    return (
      <Row testid={`group-row-${position}`}>
        <IndexLink id={id} name={name} ref={ref} position={position} handlers={handlers} />
        <span className={dim}>{(match ? primaryRequirement(match)?.label : undefined) ?? ''}</span>
      </Row>
    );
  }
  const masterDoc = resolveSprintMaster(ref, allDocs ?? [], handlers.docPathForRef);
  const { id, name } = masterRowFacts({ ref, masterDoc });
  const counts = masterDoc ? masterTaskCounts(masterDoc) : null;
  return (
    <Row testid={`group-row-${position}`}>
      <IndexLink id={id} name={name} ref={ref} position={position} handlers={handlers} />
      <span className={dim}>{counts ? `${counts.done}/${counts.total} tasks` : ''}</span>
    </Row>
  );
}
