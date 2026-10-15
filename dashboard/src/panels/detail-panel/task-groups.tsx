// MIK-R127 fixed skeletons: the Now/Tasks/Masters blocks (rules 3-4) as tables of
// one-line rows, plus the completed/abandoned group views and the sprint rows view.
// Positions are the row's 1-based index in the document's full index, so a row keeps its
// test id in every group. The shared bounded blocks live in skeletons.tsx; the three
// readers in taskReader.tsx compose everything in the fixed order (rules 3-5).
import type { ReactNode } from 'react';

import { cx } from '../../../styled-system/css';
import { Markdown } from '../../grammar/Markdown';
import type { SubTaskRow, TaskDocNode, TaskDocumentRef } from '../../types/projection';
import {
  goneMastersLabel,
  goneTasksLabel,
  groupSubTaskRows,
  hasIndexContent,
  masterPredecessorTitles,
  masterTaskCounts,
  primaryRequirement,
  sectionIntro,
  standingStep,
  stepMarkStates,
  subTaskKey,
  taskStepProgress,
} from './model';
import {
  Block,
  Chip,
  MoreLink,
  ProgressBar,
  Row,
  RowHead,
  Rows,
  StepMarks,
  SubHead,
  plural,
  statusLabel,
  statusTone,
  type DocView,
} from './pageParts';
import {
  apart,
  cell,
  cellTwo,
  chips,
  dim,
  intro as introStyle,
  lineRow,
  link,
  none,
  nowReview,
  nowStep,
  table,
  tableFrame,
  warn,
} from './pageStyles';
import { CompletedTaskList } from './CompletedTaskList';
import { CanStartNext } from './orderGraph';
import { docPathForTaskRef } from './model';
import { completedEntries } from './completedTasksModel';
import {
  IndexLink,
  taskRowFacts,
  masterRowFacts,
  resolveSprintMaster,
  resolveRowTarget,
  fullOrderPositions,
  indexedRows,
  sprintEntries,
  groupSprintEntries,
  type IndexRowHandlers,
  type SprintEntry,
} from './indexRows';
export {
  fullOrderPositions,
  resolveRowTarget,
  resolveSprintMaster,
  sprintEntries,
  groupSprintEntries,
} from './indexRows';

function dash(): ReactNode {
  return <span className={dim}>—</span>;
}

function splitLine(groups: {
  work: unknown[];
  waiting: unknown[];
  done: unknown[];
  gone: unknown[];
}): string {
  const gone = groups.gone.length > 0 ? ` · ${groups.gone.length} abandoned` : '';
  return `${groups.work.length} in work · ${groups.waiting.length} waiting · ${groups.done.length} completed${gone}`;
}

function isEmptyIndex(groups: {
  work: unknown[];
  waiting: unknown[];
  done: unknown[];
  gone: unknown[];
}): boolean {
  return (
    groups.work.length === 0 &&
    groups.waiting.length === 0 &&
    groups.done.length === 0 &&
    groups.gone.length === 0
  );
}

// ---------------------------------------------------------------------------
// A master's index row: the task's own document where the row resolves one.
// ---------------------------------------------------------------------------

function StepsDone({ match }: { match: TaskDocNode | undefined }) {
  if (!match || match.stepsTotal === 0) return dash();
  const progress = taskStepProgress(match);
  return (
    <>
      <StepMarks states={stepMarkStates(match.steps)} steps={match.steps} />
      {progress.done}/{progress.total}
    </>
  );
}

function ReviewWord({ match }: { match: TaskDocNode | undefined }) {
  const review = match?.reviewState;
  if (!review || review.round === 0) return null;
  // The whole round's count: the row holds a summary with the first findings only.
  const open = review.openFindings;
  return (
    <>
      <span>round {review.round}</span>
      {review.pending ? <span className={warn}> · verdict open</span> : null}
      {review.totalFindings > 0 ? (
        <span className={open > 0 ? warn : dim}>
          {' '}
          · {open} of {review.totalFindings} findings open
        </span>
      ) : null}
    </>
  );
}

// A master Now row (rule 4): the task in work with its steps done, the step it
// stands at, and its review round with the open findings.
function MasterNowRow({
  ref,
  position,
  parentId,
  handlers,
}: {
  ref: SubTaskRow;
  position: number;
  parentId: string;
  handlers: IndexRowHandlers;
}) {
  const { id, name, match } = taskRowFacts(ref, handlers.sliceDocs, parentId);
  const step = match ? standingStep(match.steps) : undefined;
  const hasReview = !!match?.reviewState && match.reviewState.round > 0;
  return (
    <Row testid={`now-row-${position}`}>
      <IndexLink id={id} name={name} ref={ref} position={position} handlers={handlers} />
      <span>
        <span data-testid={`now-steps-${position}`}>
          <StepsDone match={match} />
        </span>
      </span>
      {step ? (
        <span className={nowStep} data-testid={`now-step-${position}`}>
          <span className={dim}>{step.how} </span>
          {step.id} <Markdown inline>{step.title}</Markdown>
        </span>
      ) : (
        <span className={cell({ at: 'wide' })}>{dash()}</span>
      )}
      {hasReview ? (
        <span className={nowReview} data-testid={`now-review-${position}`}>
          <ReviewWord match={match} />
        </span>
      ) : (
        <span className={cell({ at: 'mid' })}>{dash()}</span>
      )}
    </Row>
  );
}

export function MasterNowBlock({
  doc,
  sliceDocs,
  onOpen,
  onJump,
  docPathForRef,
}: {
  doc: Pick<TaskDocNode, 'id' | 'subTasks' | 'executionGraphView'>;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
}) {
  const groups = groupSubTaskRows(doc.subTasks);
  // A bare page keeps no empty Now heading: without a row in any group the
  // block is absent.
  if (isEmptyIndex(groups)) return null;
  const positions = fullOrderPositions(doc.subTasks);
  const handlers: IndexRowHandlers = { sliceDocs, onOpen, onJump, docPathForRef };
  return (
    <Block
      title="Now"
      block="now"
      id="now"
      count={<span data-testid="now-split">{splitLine(groups)}</span>}
    >
      {groups.work.length > 0 ? (
        <>
          <SubHead title="In work" count={groups.work.length} />
          <Rows cols="masterNow" testid="now-table">
            <RowHead>
              <span className={cellTwo}>task</span>
              <span>steps done</span>
              <span className={cell({ at: 'wide' })}>step</span>
              <span className={cell({ at: 'mid' })}>review</span>
            </RowHead>
            {groups.work.map((ref) => (
              <MasterNowRow
                key={subTaskKey(ref, positions.get(ref) ?? 0)}
                ref={ref}
                position={positions.get(ref) ?? 0}
                parentId={doc.id}
                handlers={handlers}
              />
            ))}
          </Rows>
        </>
      ) : (
        <p className={none}>No tasks in work.</p>
      )}
      <CanStartNext
        graphView={doc.executionGraphView}
        handlers={{
          allDocs: sliceDocs,
          onJump,
          docPathForRef: docPathForRef ?? ((ref) => docPathForTaskRef(sliceDocs, ref)),
        }}
      />
    </Block>
  );
}

// ---------------------------------------------------------------------------
// A sprint's index row: the commanded master's own document where it resolves.
// ---------------------------------------------------------------------------

function MasterProgress({ masterDoc }: { masterDoc: TaskDocNode }) {
  const counts = masterTaskCounts(masterDoc);
  const work = masterDoc.subTasks.filter((row) => row.status === 'inProgress').length;
  return (
    <>
      <ProgressBar done={counts.done} work={work} total={counts.total} />
      {counts.done}/{counts.total}
    </>
  );
}

// A sprint Now row (rule 3): the master in work with how many of its tasks are
// completed and how many are in work. A row without a projected master keeps
// what the row says.
function SprintNowRow({
  entry,
  position,
  handlers,
}: {
  entry: SprintEntry;
  position: number;
  handlers: IndexRowHandlers;
}) {
  const { id, name } = masterRowFacts(entry);
  const { masterDoc, ref } = entry;
  const inWork = masterDoc
    ? masterDoc.subTasks.filter((row) => row.status === 'inProgress').length
    : 0;
  return (
    <Row testid={`now-row-${position}`}>
      <IndexLink id={id} name={name} ref={ref} position={position} handlers={handlers} />
      <span className={cell({ at: 'belowUntilMid' })} data-testid={`now-progress-${position}`}>
        {masterDoc ? (
          <MasterProgress masterDoc={masterDoc} />
        ) : (
          <span className={dim}>{statusLabel(ref.status)}</span>
        )}
      </span>
      <span className={cell({ at: 'mid' })}>{inWork > 0 ? inWork : dash()}</span>
    </Row>
  );
}

export function SprintNowBlock({
  doc,
  allDocs,
  onJump,
  onOpen,
  sliceDocs,
  docPathForRef,
}: {
  doc: Pick<TaskDocNode, 'subTasks'>;
  allDocs: TaskDocNode[];
  onJump: (id: string) => void;
  onOpen: (slug: string) => void;
  sliceDocs: TaskDocNode[];
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
}) {
  // The masters in work (rule 3): resolving rows show the master's own
  // progress; a row without a projected master keeps its row rendering.
  const groups = groupSprintEntries(sprintEntries(doc, allDocs, docPathForRef));
  // A bare page keeps no empty Now heading: without a row in any group the
  // block is absent.
  if (isEmptyIndex(groups)) return null;
  const positions = fullOrderPositions(doc.subTasks);
  const handlers: IndexRowHandlers = { sliceDocs, onOpen, onJump, docPathForRef };
  return (
    <Block
      title="Now"
      block="now"
      id="now"
      count={<span data-testid="now-split">{splitLine(groups)}</span>}
    >
      {groups.work.length > 0 ? (
        <Rows cols="sprintNow" testid="now-table">
          <RowHead>
            <span className={cellTwo}>master</span>
            <span className={cell({ at: 'mid' })}>tasks completed</span>
            <span className={cell({ at: 'mid' })}>in work</span>
          </RowHead>
          {groups.work.map((entry) => (
            <SprintNowRow
              key={subTaskKey(entry.ref, positions.get(entry.ref) ?? 0)}
              entry={entry}
              position={positions.get(entry.ref) ?? 0}
              handlers={handlers}
            />
          ))}
        </Rows>
      ) : (
        <p className={none}>No masters in work.</p>
      )}
    </Block>
  );
}

// ---------------------------------------------------------------------------
// Tasks/Masters blocks (rules 3-4): grouped by status; what is in work stands
// under Now only, the completed ones are a count with a link.
// ---------------------------------------------------------------------------

function InWorkGroup({ count, noun }: { count: number; noun: 'tasks' | 'masters' }) {
  const goNow = (): void => {
    document.getElementById('now')?.scrollIntoView({ block: 'start' });
  };
  return (
    <>
      <SubHead title="In work" count={count} />
      {count > 0 ? (
        <div className={lineRow}>
          <button type="button" className={link} onClick={goNow} data-testid="tasks-now-link">
            listed under Now ↑
          </button>
        </div>
      ) : (
        <p className={none}>No {noun} in work.</p>
      )}
    </>
  );
}

function DoneGroup({
  count,
  noun,
  openView,
}: {
  count: number;
  noun: 'task' | 'master';
  openView: (view: DocView) => void;
}) {
  return (
    <>
      <SubHead title="Completed" count={count} />
      {count > 0 ? (
        <div className={lineRow}>
          <MoreLink
            onClick={() => openView({ kind: 'group', status: 'done' })}
            testid="group-done-link"
          >
            {plural(count, `completed ${noun}`)}
          </MoreLink>
        </div>
      ) : (
        <p className={none}>No completed {noun}s.</p>
      )}
    </>
  );
}

function GoneGroup({
  gone,
  discarded,
  goneLabel,
  openView,
}: {
  gone: number;
  discarded: number;
  goneLabel: string;
  openView: (view: DocView) => void;
}) {
  if (gone === 0 && discarded === 0) return null;
  return (
    <>
      <SubHead
        title={discarded > 0 ? 'Abandoned or discarded' : 'Abandoned'}
        count={gone + discarded}
      />
      <div className={lineRow}>
        <MoreLink
          onClick={() => openView({ kind: 'group', status: 'gone' })}
          testid="group-gone-link"
        >
          {goneLabel}
        </MoreLink>
      </div>
    </>
  );
}

// What a waiting task shows at the right: its steps where its document has
// any, and its status where that is not plain waiting.
function waitingFact(ref: SubTaskRow, match: TaskDocNode | undefined): string {
  const progress = match && match.stepsTotal > 0 ? `${match.stepsDone}/${match.stepsTotal}` : '';
  const status = ref.status === 'planning' ? '' : statusLabel(ref.status);
  return [progress, status].filter(Boolean).join(' · ');
}

function TaskWaitingRow({
  ref,
  position,
  parentId,
  handlers,
}: {
  ref: SubTaskRow;
  position: number;
  parentId: string;
  handlers: IndexRowHandlers;
}) {
  const { id, name, match } = taskRowFacts(ref, handlers.sliceDocs, parentId);
  const requirement = match ? primaryRequirement(match)?.label : undefined;
  return (
    <Row testid={`waiting-row-${position}`}>
      <IndexLink id={id} name={name} ref={ref} position={position} handlers={handlers} />
      <span className={cx(dim, cell({ at: 'mid' }))}>{requirement ?? ''}</span>
      <span className={dim}>{waitingFact(ref, match)}</span>
    </Row>
  );
}

export function MasterTasksBlock({
  doc,
  sliceDocs,
  onOpen,
  onJump,
  docPathForRef,
  openView,
}: {
  doc: Pick<TaskDocNode, 'id' | 'subTasks' | 'sections' | 'discardedSubTasks'>;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  openView: (view: DocView) => void;
}) {
  const groups = groupSubTaskRows(doc.subTasks);
  const positions = fullOrderPositions(doc.subTasks);
  const handlers = indexedRows({ sliceDocs, onOpen, onJump, docPathForRef });
  const intro = sectionIntro(doc.sections, 'subTasks');
  // The discarded-before-start history stands beside the abandoned rows: the
  // link shows whenever either exists, so no audit is left without a way back.
  const discarded = doc.discardedSubTasks ?? [];
  // A bare page keeps no empty Tasks heading — unless it carries introducing
  // prose or discarded history, which stay with their way back.
  if (!hasIndexContent(groups, discarded.length, intro)) return null;
  return (
    <Block title="Tasks" block="tasks" count={doc.subTasks.length}>
      {intro ? (
        <div className={introStyle} data-testid="tasks-intro">
          <Markdown>{intro}</Markdown>
        </div>
      ) : null}
      <InWorkGroup count={groups.work.length} noun="tasks" />
      <SubHead title="Waiting" count={groups.waiting.length} />
      {groups.waiting.length > 0 ? (
        <Rows cols="taskWait" testid="waiting-table">
          <RowHead>
            <span className={cellTwo}>task</span>
            <span className={cell({ at: 'mid' })}>requirement</span>
            <span>steps</span>
          </RowHead>
          {groups.waiting.map((ref) => (
            <TaskWaitingRow
              key={subTaskKey(ref, positions.get(ref) ?? 0)}
              ref={ref}
              position={positions.get(ref) ?? 0}
              parentId={doc.id}
              handlers={handlers}
            />
          ))}
        </Rows>
      ) : (
        <p className={none}>No waiting tasks.</p>
      )}
      <SubHead title="Completed" count={groups.done.length} />
      <CompletedTaskList
        preview
        entries={completedEntries(groups.done, sliceDocs, doc.id, positions, handlers.sliceForRef)}
        targetFor={(entry) => resolveRowTarget(entry.ref, entry.position, handlers)}
      />
      {groups.done.length > 0 ? (
        <div className={lineRow}>
          <MoreLink
            onClick={() => openView({ kind: 'group', status: 'done' })}
            testid="group-done-link"
          >
            {plural(groups.done.length, 'completed task')}
          </MoreLink>
        </div>
      ) : (
        <p className={none}>No completed tasks.</p>
      )}
      <GoneGroup
        gone={groups.gone.length}
        discarded={discarded.length}
        goneLabel={goneTasksLabel(groups.gone.length, discarded.length)}
        openView={openView}
      />
    </Block>
  );
}

// A sprint Waiting row: the master with its execution nature, its tasks done,
// and what it waits for from the execution graph view. A row without a
// projected master keeps the row as written.
function SprintWaitingRow({
  entry,
  position,
  waitsFor,
  handlers,
}: {
  entry: SprintEntry;
  position: number;
  waitsFor: string[];
  handlers: IndexRowHandlers;
}) {
  const { id, name } = masterRowFacts(entry);
  const { masterDoc, ref } = entry;
  const counts = masterDoc ? masterTaskCounts(masterDoc) : null;
  return (
    <Row testid={`waiting-row-${position}`}>
      <IndexLink id={id} name={name} ref={ref} position={position} handlers={handlers} />
      <span className={cx(dim, cell({ at: 'mid' }))}>
        {masterDoc?.executionNature ?? (masterDoc ? '' : statusLabel(ref.status))}
      </span>
      <span className={cx(dim, cell({ at: 'mid' }))}>
        {counts ? `${counts.done}/${counts.total}` : ''}
      </span>
      {waitsFor.length > 0 ? (
        <span
          className={cx(chips, cell({ at: 'belowUntilWide' }))}
          data-testid={`waiting-for-${position}`}
        >
          {waitsFor.map((title) => (
            <Chip key={title} title={`waits for ${title}`}>
              {title}
            </Chip>
          ))}
        </span>
      ) : (
        <span className={cx(dim, cell({ at: 'wide' }))}>{masterDoc ? 'no predecessor' : ''}</span>
      )}
    </Row>
  );
}

function SprintWaitingGroup({
  waiting,
  positions,
  allDocs,
  handlers,
  executionGraphView,
}: {
  waiting: SprintEntry[];
  positions: Map<SubTaskRow, number>;
  allDocs: TaskDocNode[];
  handlers: IndexRowHandlers;
  executionGraphView: TaskDocNode['executionGraphView'];
}) {
  return (
    <>
      <SubHead title="Waiting" count={waiting.length} />
      {waiting.length > 0 ? (
        <Rows cols="masterWait" testid="waiting-table">
          <RowHead>
            <span className={cellTwo}>master</span>
            <span className={cell({ at: 'mid' })}>execution</span>
            <span className={cell({ at: 'mid' })}>tasks</span>
            <span className={cell({ at: 'wide' })}>waits for</span>
          </RowHead>
          {waiting.map((entry) => {
            const masterRef = 'masterRef' in entry.ref ? entry.ref.masterRef : undefined;
            const waitsFor =
              entry.masterDoc && masterRef
                ? masterPredecessorTitles(masterRef, executionGraphView, allDocs)
                : [];
            return (
              <SprintWaitingRow
                key={subTaskKey(entry.ref, positions.get(entry.ref) ?? 0)}
                entry={entry}
                position={positions.get(entry.ref) ?? 0}
                waitsFor={waitsFor}
                handlers={handlers}
              />
            );
          })}
        </Rows>
      ) : (
        <p className={none}>No waiting masters.</p>
      )}
    </>
  );
}

export function SprintMastersBlock({
  doc,
  allDocs,
  onJump,
  onOpen,
  sliceDocs,
  docPathForRef,
  openView,
}: {
  doc: Pick<TaskDocNode, 'subTasks' | 'sections' | 'executionGraphView' | 'discardedSubTasks'>;
  allDocs: TaskDocNode[];
  onJump: (id: string) => void;
  onOpen: (slug: string) => void;
  sliceDocs: TaskDocNode[];
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  openView: (view: DocView) => void;
}) {
  const entries = sprintEntries(doc, allDocs, docPathForRef);
  const groups = groupSprintEntries(entries);
  const positions = fullOrderPositions(doc.subTasks);
  const handlers: IndexRowHandlers = { sliceDocs, onOpen, onJump, docPathForRef };
  const unresolvable = entries.filter(({ masterDoc }) => !masterDoc).length;
  const intro = sectionIntro(doc.sections, 'subTasks');
  const discarded = doc.discardedSubTasks ?? [];
  // A bare page keeps no empty Masters heading — unless it carries introducing
  // prose or discarded history, which stay with their way back.
  if (!hasIndexContent(groups, discarded.length, intro)) return null;
  return (
    <Block title="Masters" block="masters" count={doc.subTasks.length}>
      {intro ? (
        <div className={introStyle} data-testid="tasks-intro">
          <Markdown>{intro}</Markdown>
        </div>
      ) : null}
      <InWorkGroup count={groups.work.length} noun="masters" />
      <SprintWaitingGroup
        waiting={groups.waiting}
        positions={positions}
        allDocs={allDocs}
        handlers={handlers}
        executionGraphView={doc.executionGraphView}
      />
      <DoneGroup count={groups.done.length} noun="master" openView={openView} />
      <GoneGroup
        gone={groups.gone.length}
        discarded={discarded.length}
        goneLabel={goneMastersLabel(groups.gone.length, discarded.length)}
        openView={openView}
      />
      <div className={cx(lineRow, apart)}>
        <MoreLink onClick={() => openView({ kind: 'rows' })} testid="rows-all-link">
          {plural(doc.subTasks.length, 'manager and seat row')} of the sprint document
        </MoreLink>
        {unresolvable > 0 ? (
          <span className={dim}>{unresolvable} without a projected master stand here only</span>
        ) : null}
      </div>
    </Block>
  );
}

export { GroupViewBody } from './TaskGroupView';

// The sprint rows view: every manager and seat row with what the row says
// beside what the commanded master's own document says (open truth gap 3).
export function SprintRowsViewBody({
  refs,
  allDocs,
  docPathForRef,
}: {
  refs: SubTaskRow[];
  allDocs: TaskDocNode[];
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
}) {
  return (
    <div className={tableFrame}>
      <table className={table} data-testid="sprint-rows-table">
        <colgroup>
          <col style={{ width: '13%' }} />
          <col style={{ width: '37%' }} />
          <col style={{ width: '11%' }} />
          <col style={{ width: '11%' }} />
          <col style={{ width: '28%' }} />
        </colgroup>
        <thead>
          <tr>
            <th>Row</th>
            <th>Name</th>
            <th>Row says</th>
            <th>Master says</th>
            <th>Scope</th>
          </tr>
        </thead>
        <tbody>
          {refs.map((ref, index) => {
            const masterDoc = resolveSprintMaster(ref, allDocs, docPathForRef);
            const differs = masterDoc !== undefined && masterDoc.status !== ref.status;
            const scope = 'scope' in ref ? ref.scope : '';
            return (
              <tr key={subTaskKey(ref, index)} data-testid={`sprint-row-${ref.number}`}>
                <td title={ref.number}>{ref.number}</td>
                <td title={masterDoc?.title ?? ref.name}>{masterDoc?.title ?? ref.name}</td>
                <td data-tone={statusTone(ref.status)}>{statusLabel(ref.status)}</td>
                <td className={differs ? warn : undefined}>
                  {masterDoc ? statusLabel(masterDoc.status) : '—'}
                </td>
                <td title={scope}>{scope ? <Markdown inline>{scope}</Markdown> : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
