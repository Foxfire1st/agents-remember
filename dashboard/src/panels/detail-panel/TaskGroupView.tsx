// Completed master tasks have time ordering; sprint and aborted rows keep authored order.
import { taskEntrySelectionKey } from '../../data/taskReference';
import type { TaskEntry,TaskSummary } from '../../types/projection';
import { CompletedTaskList } from './CompletedTaskList';
import { completedEntries } from './completedTasksModel';
import { ChildProgress,resolveRowTarget,type TaskIndexProps } from './indexRows';
import { primaryRequirement,shortDocId } from './model';
import { Row,RowLink,Rows,SubHead,TaskStatusWord } from './pageParts';
import { dim } from './pageStyles';
import { DiscardedSubTaskHistory } from './skeletons';

export function GroupViewBody({
  doc,
  stage,
  ...props
}: TaskIndexProps & {
  doc: TaskSummary;
  stage: 'completed' | 'aborted';
}) {
  return (
    <div>
      {stage === 'completed' && props.entry.summary?.form !== 'sprint' ? (
        <CompletedTaskList
          entries={completedEntries(props.entry, props.tasks)}
          targetFor={(item) => resolveRowTarget(item.entry, item.position, props.onJump)}
        />
      ) : (
        <Rows cols="group" testid="group-table">
          {props.entry.children.map((ref, index) => {
            const child = props.tasks[ref];
            return child?.stage === stage ? (
              <TerminalRow
                key={ref}
                entry={child}
                parent={props.entry}
                position={index + 1}
                onJump={props.onJump}
              />
            ) : null;
          })}
        </Rows>
      )}
      {stage === 'aborted' && doc.discardedSubTasks?.length ? (
        <>
          <SubHead title="Discarded before start" count={doc.discardedSubTasks.length} />
          <DiscardedSubTaskHistory items={doc.discardedSubTasks} />
        </>
      ) : null}
    </div>
  );
}

function TerminalRow({
  entry,
  parent,
  position,
  onJump,
}: {
  entry: TaskEntry;
  parent: TaskEntry;
  position: number;
  onJump: TaskIndexProps['onJump'];
}) {
  const sprint = parent.summary?.form === 'sprint';
  return (
    <Row testid={`group-row-${position}`}>
      <RowLink
        id={sprint ? entry.id : shortDocId(entry.id, parent.id)}
        name={entry.title}
        testid={`subtask-open-${sprint ? 'master-' : ''}${position}`}
        title={entry.title}
        onOpen={entry.reference ? () => onJump(taskEntrySelectionKey(entry.key)) : undefined}
      />
      <span className={dim}>
        {sprint ? (
          <>
            <ChildProgress entry={entry} /> tasks ·{' '}
          </>
        ) : entry.summary ? (
          primaryRequirement(entry.summary)?.label
        ) : (
          ''
        )}{' '}
        <TaskStatusWord task={entry} />
      </span>
    </Row>
  );
}
