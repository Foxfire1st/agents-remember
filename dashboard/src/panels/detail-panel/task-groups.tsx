// Task rows use the server's ordered membership, status and counts.
import { cx } from '../../../styled-system/css';
import { selectHostActivityUnknown } from '../../data/selectors';
import { useDashboard } from '../../data/store';
import { taskEntrySelectionKey,taskReferenceKey } from '../../data/taskReference';
import { Markdown } from '../../grammar/Markdown';
import type { TaskEntry,TaskSummary } from '../../types/projection';
import { CompletedTaskList } from './CompletedTaskList';
import { ListDisagreements } from './listDisagreements';
import { completedEntries } from './completedTasksModel';
import { ChildProgress,resolveRowTarget,type TaskIndexProps } from './indexRows';
import {
primaryRequirement,
sectionIntro,
shortDocId,
standingStep,
stepMarkStates,
} from './model';
import { CanStartNext } from './orderGraph';
import {
Block,
Chip,
MoreLink,
Row,
RowHead,
RowLink,
Rows,
StepMarks,
SubHead,
TaskStatusWord,
type DocView,
} from './pageParts';
import {
cell,
cellTwo,
chips,
dim,
intro as introStyle,
lineRow,
none,
nowReview,
nowStep,
table,
tableFrame,
warn,
} from './pageStyles';

export type { TaskIndexProps } from './indexRows';
/** Native entries keep the existing Now and waiting table columns. */
export function TaskIndexRow({
  entry,
  parent,
  position,
  onJump,
  now = false,
}: {
  entry: TaskEntry;
  parent: TaskEntry;
  position: number;
  onJump: TaskIndexProps['onJump'];
  now?: boolean;
}) {
  const sprint = parent.summary?.form === 'sprint';
  return (
    <Row testid={`${now ? 'now' : 'waiting'}-row-${position}`}>
      <RowLink
        id={sprint ? entry.id : shortDocId(entry.id, parent.id)}
        name={entry.title}
        testid={`subtask-open-${sprint ? 'master-' : ''}${position}`}
        title={entry.summary ? entry.title : 'not authored as a task document yet'}
        onOpen={entry.reference ? () => onJump(taskEntrySelectionKey(entry.key)) : undefined}
      />
      {now ? (
        sprint ? (
          <MasterNowCells entry={entry} position={position} />
        ) : (
          <TaskNowCells entry={entry} position={position} />
        )
      ) : (
        <WaitingCells entry={entry} parent={parent} sprint={sprint} position={position} />
      )}
    </Row>
  );
}

function TaskNowCells({ entry, position }: { entry: TaskEntry; position: number }) {
  const step = entry.summary ? standingStep(entry.summary.steps) : undefined;
  return (
    <>
      <span>
        <span data-testid={`now-steps-${position}`}>
          {entry.stepsTotal > 0 ? (
            <>
              <StepMarks
                states={stepMarkStates(entry.summary?.steps ?? [])}
                steps={entry.summary?.steps}
              />
              {entry.stepsDone}/{entry.stepsTotal}
            </>
          ) : (
            <span className={dim}>—</span>
          )}
        </span>
      </span>
      <span className={nowStep} data-testid={`now-step-${position}`}>
        {step ? (
          <>
            <span className={dim}>{step.how} </span>
            {step.id} <Markdown inline>{step.title}</Markdown>
          </>
        ) : (
          <span className={dim}>—</span>
        )}
      </span>
      <span className={nowReview}>
        <span data-testid={`now-review-${position}`}>
          <ReviewWord review={entry.summary?.reviewState} />
        </span>{' '}
        <TaskStatusWord task={entry} testid={`task-row-status-${position}`} />
      </span>
    </>
  );
}

function ReviewWord({ review }: { review: TaskSummary['reviewState'] }) {
  if (!review || review.round === 0) return <span className={dim}>—</span>;
  return (
    <>
      <span>round {review.round}</span>
      {review.pending ? <span className={warn}> · verdict open</span> : null}
      {review.totalFindings > 0 ? (
        <span className={review.openFindings > 0 ? warn : dim}>
          {' '}
          · {review.openFindings} of {review.totalFindings} findings open
        </span>
      ) : null}
    </>
  );
}

function MasterNowCells({ entry, position }: { entry: TaskEntry; position: number }) {
  const hostUnknown = useDashboard(selectHostActivityUnknown);
  return (
    <>
      <span className={cell({ at: 'belowUntilMid' })} data-testid={`now-progress-${position}`}>
        <ChildProgress entry={entry} />
      </span>
      <span className={cell({ at: 'mid' })}>
        {hostUnknown
          ? ''
          : `${entry.counts.works} works · ${entry.counts.paused} paused `}
        <TaskStatusWord task={entry} testid={`task-row-status-${position}`} />
      </span>
    </>
  );
}

function WaitingCells({
  entry,
  parent,
  sprint,
  position,
}: {
  entry: TaskEntry;
  sprint: boolean;
  parent: TaskEntry;
  position: number;
}) {
  return sprint ? (
    <>
      <span className={cx(dim, cell({ at: 'mid' }))}>
        {entry.summary?.executionNature ?? ''}{' '}
        <TaskStatusWord task={entry} testid={`task-row-status-${position}`} />
      </span>
      <span className={dim}>
        <ChildProgress entry={entry} />
      </span>
      <PredecessorCells entry={entry} parent={parent} position={position} />
    </>
  ) : (
    <>
      <span className={dim}>{entry.summary ? primaryRequirement(entry.summary)?.label : ''}</span>
      <span className={dim}>
        {entry.stepsTotal > 0 ? `${entry.stepsDone}/${entry.stepsTotal} · ` : ''}
        <TaskStatusWord task={entry} testid={`task-row-status-${position}`} />
      </span>
    </>
  );
}

function PredecessorCells({
  entry,
  parent,
  position,
}: {
  entry: TaskEntry;
  parent: TaskEntry;
  position: number;
}) {
  const node = parent.summary?.executionGraphView?.nodes.find(
    (node) => taskReferenceKey(node.masterRef) === entry.key,
  );
  return node?.predecessors.length ? (
    <span
      className={cx(chips, cell({ at: 'belowUntilWide' }))}
      data-testid={`waiting-for-${position}`}
    >
      {node.predecessors.map((edge) => (
        <Chip
          key={taskReferenceKey(edge.predecessorRef)}
          title={`waits for ${edge.predecessorTitle}`}
        >
          {edge.predecessorTitle}
        </Chip>
      ))}
    </span>
  ) : (
    <span className={cx(dim, cell({ at: 'wide' }))}>{entry.summary ? 'no predecessor' : ''}</span>
  );
}

function IndexHead({ sprint, now }: { sprint: boolean; now: boolean }) {
  if (now)
    return sprint ? (
      <RowHead>
        <span className={cellTwo}>master</span>
        <span className={cell({ at: 'mid' })}>tasks completed</span>
        <span className={cell({ at: 'mid' })}>activity</span>
      </RowHead>
    ) : (
      <RowHead>
        <span className={cellTwo}>task</span>
        <span>steps done</span>
        <span className={cell({ at: 'wide' })}>step</span>
        <span className={cell({ at: 'mid' })}>review</span>
      </RowHead>
    );
  return (
    <RowHead>
      <span className={cellTwo}>{sprint ? 'master' : 'task'}</span>
      <span>{sprint ? 'execution' : 'requirement'}</span>
      <span>{sprint ? 'tasks' : 'steps'}</span>
      {sprint ? <span>waits for</span> : null}
    </RowHead>
  );
}

function IndexRows({
  entry,
  tasks,
  onJump,
  stage,
  now = false,
}: TaskIndexProps & { stage: TaskEntry['stage']; now?: boolean }) {
  const sprint = entry.summary?.form === 'sprint';
  return (
    <Rows
      cols={now ? (sprint ? 'sprintNow' : 'masterNow') : sprint ? 'masterWait' : 'taskWait'}
      testid={now ? 'now-table' : 'waiting-table'}
    >
      <IndexHead sprint={sprint} now={now} />
      {entry.children.map((reference, index) => {
        const child = tasks[reference];
        return child?.stage === stage ? (
          <TaskIndexRow
            key={reference}
            entry={child}
            parent={entry}
            position={index + 1}
            onJump={onJump}
            now={now}
          />
        ) : null;
      })}
    </Rows>
  );
}

export function TaskNowBlock(props: TaskIndexProps) {
  const { entry } = props;
  const counts = entry.counts;
  if (!entry.children.length) return null;
  return (
    <Block
      title="Now"
      block="now"
      id="now"
      count={
        <span data-testid="now-split">
          {counts.started} started · {counts.planned} planned · {counts.completed} completed
          {counts.aborted > 0 ? ` · ${counts.aborted} aborted` : ''}
        </span>
      }
    >
      {counts.started > 0 ? (
        <IndexRows {...props} stage="started" now />
      ) : (
        <p className={none}>No tasks started.</p>
      )}
      <CanStartNext
        graphView={entry.summary?.executionGraphView ?? undefined}
        handlers={{ tasks: props.tasks, onJump: props.onJump }}
      />
    </Block>
  );
}

// The Tasks block shows when the master lists, introduces, discarded or disagrees with anything.
function hasTaskContent(entry: TaskEntry, intro: string | undefined, discarded: number): boolean {
  return (
    entry.children.length > 0 ||
    Boolean(intro) ||
    discarded > 0 ||
    (entry.disagreements?.length ?? 0) > 0
  );
}

export function TaskChildrenBlock({
  doc,
  openView,
  ...props
}: TaskIndexProps & {
  doc: TaskSummary;
  openView: (view: DocView) => void;
}) {
  const { entry } = props;
  const sprint = doc.form === 'sprint';
  const intro = sectionIntro(doc.sections, 'subTasks');
  const discarded = doc.discardedSubTasks ?? [];
  if (!hasTaskContent(entry, intro, discarded.length)) return null;
  return (
    <Block
      title={sprint ? 'Masters' : 'Tasks'}
      block={sprint ? 'masters' : 'tasks'}
      count={entry.children.length}
    >
      {intro ? (
        <div className={introStyle} data-testid="tasks-intro">
          <Markdown>{intro}</Markdown>
        </div>
      ) : null}
      <OpenGroups {...props} />
      <TerminalGroups
        {...props}
        discarded={discarded.length}
        noun={sprint ? 'masters' : 'tasks'}
        openView={openView}
      />
      <ListDisagreements entry={entry} tasks={props.tasks} onJump={props.onJump} />
      <SeatRowsLink doc={doc} hasChildren={entry.children.length > 0} openView={openView} />
    </Block>
  );
}

export { GroupViewBody } from './TaskGroupView';

export function SprintRowsViewBody({ doc, entry, tasks }: TaskIndexProps & { doc: TaskSummary }) {
  return (
    <div className={tableFrame}>
      <table className={table} data-testid="sprint-rows-table">
        <colgroup>
          <col style={{ width: '13%' }} />
          <col style={{ width: '37%' }} />
          <col style={{ width: '22%' }} />
          <col style={{ width: '28%' }} />
        </colgroup>
        <thead>
          <tr>
            <th>Row</th>
            <th>Name</th>
            <th>State</th>
            <th>Scope</th>
          </tr>
        </thead>
        <tbody>
          {entry.children.map((ref, index) => {
            const child = tasks[ref];
            return child ? (
              <tr key={ref} data-testid={`sprint-row-${index + 1}`}>
                <td title={child.id}>{child.id}</td>
                <td title={child.title}>{child.title}</td>
                <td>
                  <TaskStatusWord task={child} />
                </td>
                <td title={child.scope}>
                  {child.scope ? <Markdown inline>{child.scope}</Markdown> : '—'}
                </td>
              </tr>
            ) : null;
          })}
          {doc.seats.map((seat) => (
            <tr key={seat.role}>
              <td>{seat.role}</td>
              <td>{seat.label}</td>
              <td>{seat.state}</td>
              <td>{seat.identity}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TerminalGroups({
  entry,
  tasks,
  onJump,
  discarded,
  noun,
  openView,
}: TaskIndexProps & {
  discarded: number;
  noun: string;
  openView: (view: DocView) => void;
}) {
  return (
    <>
      <SubHead title="Completed" count={entry.counts.completed} />
      {entry.summary?.form !== 'sprint' ? (
        <CompletedTaskList
          preview
          entries={completedEntries(entry, tasks)}
          targetFor={(item) => resolveRowTarget(item.entry, item.position, onJump)}
        />
      ) : null}
      {entry.counts.completed > 0 ? (
        <div className={lineRow}>
          <MoreLink
            onClick={() => openView({ kind: 'group', status: 'done' })}
            testid="group-done-link"
          >
            {entry.counts.completed} completed {noun}
          </MoreLink>
        </div>
      ) : (
        <p className={none}>No completed tasks.</p>
      )}
      {entry.counts.aborted > 0 || discarded > 0 ? (
        <>
          <SubHead title="Aborted or discarded" count={entry.counts.aborted + discarded} />
          <div className={lineRow}>
            <MoreLink
              onClick={() => openView({ kind: 'group', status: 'gone' })}
              testid="group-gone-link"
            >
              {entry.counts.aborted} aborted · {discarded} discarded before start
            </MoreLink>
          </div>
        </>
      ) : null}
    </>
  );
}

function OpenGroups(props: TaskIndexProps) {
  const { entry } = props;
  return (
    <>
      <SubHead title="Started" count={entry.counts.started} />
      {entry.counts.started > 0 ? (
        <div className={lineRow}>
          <a href="#now" data-testid="tasks-now-link">
            listed under Now ↑
          </a>
        </div>
      ) : (
        <p className={none}>No tasks started.</p>
      )}
      <SubHead title="Planned" count={entry.counts.planned} />
      {entry.counts.planned > 0 ? (
        <IndexRows {...props} stage="planned" />
      ) : (
        <p className={none}>No planned tasks.</p>
      )}
    </>
  );
}

function SeatRowsLink({
  doc,
  hasChildren,
  openView,
}: {
  doc: TaskSummary;
  hasChildren: boolean;
  openView: (view: DocView) => void;
}) {
  return (
    <>
      {doc.form === 'sprint' && (hasChildren || doc.seats.length > 0) ? (
        <div className={lineRow}>
          <MoreLink onClick={() => openView({ kind: 'rows' })} testid="rows-all-link">
            Manager and seat rows
          </MoreLink>
        </div>
      ) : null}
    </>
  );
}
