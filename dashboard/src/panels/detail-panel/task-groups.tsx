// MIK-R127 fixed skeletons: the Now/Tasks/Masters blocks (rules 3-4) as tables of
// one-line rows, plus the completed/abandoned group views and the sprint rows view.
// Positions are the row's 1-based index in the document's full index, so a row keeps its
// test id in every group. The shared bounded blocks live in skeletons.tsx; the three
// readers in taskReader.tsx compose everything in the fixed order (rules 3-5).
import type { ReactNode } from 'react';

import { cx } from '../../../styled-system/css';
import { Markdown } from '../../grammar/Markdown';
import { taskDocSelectionKey } from '../../data/taskIdentity';
import type {
  DiscardedSubTaskNode,
  SubTaskRow,
  TaskDocNode,
  TaskDocumentRef,
} from '../../types/projection';
import {
  goneMastersLabel,
  goneTasksLabel,
  groupSubTaskRows,
  hasIndexContent,
  masterPredecessorTitles,
  masterTaskCounts,
  primaryRequirement,
  sectionIntro,
  shortDocId,
  sliceForRef,
  sliceSlug,
  sprintRowStatus,
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
  RowLink,
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
import { DiscardedSubTaskHistory } from './skeletons';

export interface IndexRowHandlers {
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  // What the page adds beside a leaf row's link: the direct-execution grant of
  // MIK-R94, built by the reader, which holds the control.
  leafExtra?: LeafExtra;
}

export type LeafExtra = (
  ref: SubTaskRow,
  match: TaskDocNode | undefined,
  position: number,
) => ReactNode;

export interface RowTarget {
  testid: string;
  run: () => void;
  title?: string;
}

// One clickable index target shared by every row: a commanded master opens
// directly, a linked series opens by id, an authored slice opens by slug.
export function resolveRowTarget(
  ref: SubTaskRow,
  position: number,
  handlers: IndexRowHandlers,
): RowTarget | null {
  const masterRef = 'masterRef' in ref ? ref.masterRef : undefined;
  const masterPath =
    masterRef && handlers.docPathForRef ? handlers.docPathForRef(masterRef) : undefined;
  if (masterRef && masterPath) {
    return {
      testid: `subtask-open-master-${position}`,
      run: () => handlers.onJump(taskDocSelectionKey(masterPath)),
      title: `open the ${masterPath} master document`,
    };
  }
  const linkedLifecycleId = 'linkedLifecycleId' in ref ? ref.linkedLifecycleId : undefined;
  if (linkedLifecycleId) {
    return {
      testid: `subtask-open-link-${position}`,
      run: () => handlers.onJump(linkedLifecycleId),
      title: `open the ${linkedLifecycleId} series`,
    };
  }
  const match = sliceForRef(handlers.sliceDocs, ref);
  if (match) {
    const slug = sliceSlug(match);
    return { testid: `subtask-open-${position}`, run: () => handlers.onOpen(slug) };
  }
  return null;
}

// The id and the name of an index row as one link; a row that leads nowhere
// yet keeps the same two cells and says why.
function IndexLink({
  id,
  name,
  ref,
  position,
  handlers,
}: {
  id: string;
  name: string;
  ref: SubTaskRow;
  position: number;
  handlers: IndexRowHandlers;
}) {
  const target = resolveRowTarget(ref, position, handlers);
  return (
    <RowLink
      id={id}
      name={name}
      onOpen={target?.run}
      testid={target?.testid ?? `subtask-open-${position}`}
      title={target ? (target.title ?? `${id} — ${name}`) : 'not authored as a task document yet'}
    />
  );
}

function dash(): ReactNode {
  return <span className={dim}>—</span>;
}

export function fullOrderPositions(refs: SubTaskRow[]): Map<SubTaskRow, number> {
  return new Map(refs.map((ref, index) => [ref, index + 1] as const));
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

interface TaskRowFacts {
  id: string;
  name: string;
  match: TaskDocNode | undefined;
}

function taskRowFacts(ref: SubTaskRow, sliceDocs: TaskDocNode[], parentId: string): TaskRowFacts {
  const match = sliceForRef(sliceDocs, ref);
  return {
    id: shortDocId(match?.id || ref.number, parentId),
    name: match?.title || ref.name,
    match,
  };
}

function StepsDone({ match }: { match: TaskDocNode | undefined }) {
  if (!match || match.stepsTotal === 0) return dash();
  const progress = taskStepProgress(match);
  return (
    <>
      <StepMarks states={stepMarkStates(match.steps)} />
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
        {handlers.leafExtra?.(ref, match, position)}
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
  leafExtra,
}: {
  doc: Pick<TaskDocNode, 'id' | 'subTasks'>;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  leafExtra?: LeafExtra;
}) {
  const groups = groupSubTaskRows(doc.subTasks);
  // A bare page keeps no empty Now heading: without a row in any group the
  // block is absent.
  if (isEmptyIndex(groups)) return null;
  const positions = fullOrderPositions(doc.subTasks);
  const handlers: IndexRowHandlers = { sliceDocs, onOpen, onJump, docPathForRef, leafExtra };
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
    </Block>
  );
}

// ---------------------------------------------------------------------------
// A sprint's index row: the commanded master's own document where it resolves.
// ---------------------------------------------------------------------------

export function resolveSprintMaster(
  ref: SubTaskRow,
  allDocs: TaskDocNode[],
  docPathForRef: ((ref: TaskDocumentRef) => string | undefined) | undefined,
): TaskDocNode | undefined {
  const masterRef = 'masterRef' in ref ? ref.masterRef : undefined;
  if (!masterRef || !docPathForRef) return undefined;
  const path = docPathForRef(masterRef);
  return path ? allDocs.find((doc) => doc.docPath === path) : undefined;
}

export interface SprintEntry {
  ref: SubTaskRow;
  masterDoc: TaskDocNode | undefined;
}

// Every sprint index row with its resolution (rule 3): rows resolving to a
// commanded master show the master's own status; seat rows and rows whose
// master is not projected keep the row as written and stand with their status
// group — and in the rows view — never hidden.
export function sprintEntries(
  doc: Pick<TaskDocNode, 'subTasks'>,
  allDocs: TaskDocNode[],
  docPathForRef: ((ref: TaskDocumentRef) => string | undefined) | undefined,
): SprintEntry[] {
  return doc.subTasks.map((ref) => ({
    ref,
    masterDoc: resolveSprintMaster(ref, allDocs, docPathForRef),
  }));
}

export function sprintEntryStatus(entry: SprintEntry): string {
  return entry.masterDoc ? sprintRowStatus(entry.ref, entry.masterDoc) : entry.ref.status;
}

export interface SprintGroups {
  work: SprintEntry[];
  waiting: SprintEntry[];
  done: SprintEntry[];
  gone: SprintEntry[];
}

export function groupSprintEntries(entries: SprintEntry[]): SprintGroups {
  return {
    work: entries.filter((entry) => sprintEntryStatus(entry) === 'inProgress'),
    waiting: entries.filter((entry) => {
      const status = sprintEntryStatus(entry);
      return status !== 'inProgress' && status !== 'Completed' && status !== 'abandoned';
    }),
    done: entries.filter((entry) => sprintEntryStatus(entry) === 'Completed'),
    gone: entries.filter((entry) => sprintEntryStatus(entry) === 'abandoned'),
  };
}

function masterRowFacts(entry: SprintEntry): { id: string; name: string } {
  return {
    id: entry.masterDoc?.id ?? entry.ref.number,
    name: entry.masterDoc?.title ?? entry.ref.name,
  };
}

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
      <span className={dim}>
        {waitingFact(ref, match)}
        {handlers.leafExtra?.(ref, match, position)}
      </span>
    </Row>
  );
}

export function MasterTasksBlock({
  doc,
  sliceDocs,
  onOpen,
  onJump,
  docPathForRef,
  leafExtra,
  openView,
}: {
  doc: Pick<TaskDocNode, 'id' | 'subTasks' | 'sections' | 'discardedSubTasks'>;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  leafExtra?: LeafExtra;
  openView: (view: DocView) => void;
}) {
  const groups = groupSubTaskRows(doc.subTasks);
  const positions = fullOrderPositions(doc.subTasks);
  const handlers: IndexRowHandlers = { sliceDocs, onOpen, onJump, docPathForRef, leafExtra };
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
      <DoneGroup count={groups.done.length} noun="task" openView={openView} />
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
  return (
    <div>
      {refs.length > 0 ? (
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
        <span className={dim}>
          {(match ? primaryRequirement(match)?.label : undefined) ?? ''}
          {handlers.leafExtra?.(ref, match, position)}
        </span>
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
