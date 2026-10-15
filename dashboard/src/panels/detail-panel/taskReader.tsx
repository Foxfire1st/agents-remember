// The DetailPanel task-document reader family: one fixed skeleton per form
// (MIK-R127). A sprint, a master and a task each show a fixed set of blocks in
// a fixed order; a block without content is absent. Nothing is drawn twice,
// and what is long shows its first lines and a link to a view of its own. The
// page leads with the head on every form and the objective first after it
// (MIK-R128 rule 15): it stands below the complete head and series line,
// above Now or Steps.
import { useEffect, useRef, useState, type ReactNode } from 'react';

import { TaskRequirementLinksProvider } from '../../grammar/TaskRequirementLinks';
import { type TaskDocumentBodyState } from '../../data/useTaskDocumentBody';
import { orderedByCreation } from '../../data/taskHierarchy';
import { qualifiedLeafKey, taskDocumentRefForDoc } from '../../data/taskIdentity';
import {
  TaskNotesNotesSection,
  TaskReferencesAndReports,
  useTaskPageNotes,
  useTaskPageNotesLine,
} from '../TaskNotes';
import type {
  DirectGrantNode,
  ProviderNode,
  SeriesNode,
  TaskDocNode,
  TaskDocumentRef,
} from '../../types/projection';
import { postDirectExecutionGrant } from '../../data/directExecutionGrant';
import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../../data/taskArtifacts';
import type { ChangeSetTarget } from '../changeset/ChangeSetViewer';
import {
  CloseoutQueue,
  CodeExample,
  CodeTitlesBlock,
  DecisionsBlock,
  DocHead,
  DocViewShell,
  FurtherNotesBlock,
  FixedMarkdownBlock,
  LongTextBlock,
  ObjectiveLead,
  ReaderHead,
  RegistersBlock,
  RequirementsBlock,
  ReviewBlock,
  StepsBlock,
  TaskNotes,
  REFERENCE_BOUND,
  type HeadCount,
  type DocView,
  useDocView,
} from './skeletons';
import { DocContentView } from './docViews';
import { OverviewTop, TaskBodyNotice } from './readerTop';
import { OrderBlock, OrderGraphViewBody } from './orderGraph';
import {
  GroupViewBody,
  MasterNowBlock,
  MasterTasksBlock,
  SprintMastersBlock,
  SprintNowBlock,
  SprintRowsViewBody,
  fullOrderPositions,
  groupSprintEntries,
  sprintEntries,
} from './task-groups';
import {
  dirName,
  docPathForTaskRef,
  docChain,
  docForm,
  docIdentity,
  furtherSections,
  groupSubTaskRows,
  labelWithId,
  registerTables,
  seriesAsMasterDoc,
  sliceSlug,
  taskStepProgress,
  type MasterDocView,
} from './model';
import { plural } from './pageParts';
import { page } from './pageStyles';
import { cx } from '../../../styled-system/css';
import {
  badge,
  directGiven,
  directKnob,
  directLine,
  directNote,
  directSwitch,
  directSwitchOff,
  directSwitchOn,
  lane,
  laneMeta,
  laneRepo,
  laneTitle,
  series,
  sliceButton,
  sliceMeta,
  slices,
  taskHead,
} from './styles';

function TaskRequirementBoundary({
  doc,
  onOpenNotes,
  children,
}: {
  doc: Pick<TaskDocNode, 'repository' | 'docPath'>;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  children: ReactNode;
}) {
  return (
    <TaskRequirementLinksProvider
      repo={doc.repository}
      master={dirName(doc.docPath)}
      document={taskDocumentRefForDoc(doc)?.path}
      onOpenArtifact={onOpenNotes}
    >
      {children}
    </TaskRequirementLinksProvider>
  );
}

export interface ReaderPool {
  allDocs: TaskDocNode[];
  seriesList: SeriesNode[];
}

// The complete overview head includes its body notice and series/change-set
// line. The objective follows that line with titled-block spacing (MIK-R128
// rule 15), then the master's non-goals (rule 6).
// How many masters a sprint commands: its rows that name a master document.
// Seat rows are rows of the sprint and no masters.
function commandedMasters(doc: TaskDocNode | MasterDocView): HeadCount[] {
  const masters = doc.subTasks.filter((row) => 'masterRef' in row && row.masterRef).length;
  return masters > 0 ? [{ label: 'masters', value: masters }] : [];
}

function masterTasks(doc: MasterDocView): HeadCount[] {
  return doc.subTasks.length > 0 ? [{ label: 'tasks', value: doc.subTasks.length }] : [];
}

export function TaskContent({
  docs,
  bodyState,
  onOpen,
  onJump,
  onOpenChangeSet,
  onOpenNotes,
  docPathForRef,
  allDocs,
  seriesList,
}: {
  docs: TaskDocNode[];
  bodyState: TaskDocumentBodyState | undefined;
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  allDocs?: TaskDocNode[];
  seriesList?: SeriesNode[];
}) {
  if (docs.length === 0) {
    return <p className="muted">No task document bound to this task.</p>;
  }
  const pool: ReaderPool = { allDocs: allDocs ?? docs, seriesList: seriesList ?? [] };
  const master = docs.find((doc) => doc.kind === 'master');
  const sliceDocs = docs.filter((doc) => doc.kind !== 'master');
  if (master) {
    if (docForm(master) === 'sprint') {
      return (
        <SprintOverview
          doc={master}
          bodyState={bodyState}
          pool={pool}
          onJump={onJump}
          onOpen={onOpen}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
          docPathForRef={docPathForRef}
        />
      );
    }
    return (
      <MasterOverview
        doc={master}
        bodyState={bodyState}
        sliceDocs={sliceDocs}
        pool={pool}
        onOpen={onOpen}
        onJump={onJump}
        onOpenChangeSet={onOpenChangeSet}
        onOpenNotes={onOpenNotes}
        docPathForRef={docPathForRef}
      />
    );
  }
  if (sliceDocs.length === 1) {
    return (
      <TaskReader
        doc={sliceDocs[0]}
        bodyState={bodyState}
        pool={pool}
        onJump={onJump}
        onOpenChangeSet={onOpenChangeSet}
        onOpenNotes={onOpenNotes}
      />
    );
  }
  return <SliceList sliceDocs={sliceDocs} onOpen={onOpen} />;
}

// ---------------------------------------------------------------------------
// The sprint skeleton, in this order (rule 3, with MIK-R150 rule 14): head,
// objective, Now, Order, Masters, Requirements, Registers, Decisions, Further
// notes, Notes. Requirements stand where the proposal's sprint page has them
// (rule 10); without the block a sprint's requirement lines could not be read
// on its page. The closeout queue of the sprint stands after Now: what is
// landing belongs beside what is in work, and the head keeps the first screen.
// ---------------------------------------------------------------------------

function SprintOverviewPage({
  doc,
  bodyState,
  pool,
  onJump,
  onOpen,
  onOpenChangeSet,
  onOpenNotes,
  docPathForRef,
}: {
  // A series-derived sprint overview carries the master view shape; a
  // projected sprint document carries everything (rule 1 dispatch).
  doc: TaskDocNode | MasterDocView;
  bodyState: TaskDocumentBodyState | undefined;
  pool: ReaderPool;
  onJump: (id: string) => void;
  onOpen: (slug: string) => void;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
}) {
  const { view, openView, back } = useDocView();
  const chain = docChain(doc, pool.allDocs, pool.seriesList);
  if (view) {
    return (
      <TaskRequirementBoundary doc={doc} onOpenNotes={onOpenNotes}>
        <SprintViewBody
          view={view}
          doc={doc}
          pool={pool}
          onJump={onJump}
          onOpen={onOpen}
          onBack={back}
          docPathForRef={docPathForRef}
          onOpenNotes={onOpenNotes}
        />
      </TaskRequirementBoundary>
    );
  }
  return (
    <TaskRequirementBoundary doc={doc} onOpenNotes={onOpenNotes}>
      <div className={page} data-testid="skeleton-sprint">
        <OverviewTop
          doc={doc}
          chain={chain}
          counts={commandedMasters(doc)}
          bodyState={bodyState}
          onJump={onJump}
          openView={openView}
          onOpenChangeSet={onOpenChangeSet}
        />
        <SprintPageBody
          doc={doc}
          bodyState={bodyState}
          pool={pool}
          onJump={onJump}
          onOpen={onOpen}
          onOpenNotes={onOpenNotes}
          docPathForRef={docPathForRef}
          openView={openView}
        />
      </div>
    </TaskRequirementBoundary>
  );
}

function SprintPageBody({
  doc,
  bodyState,
  pool,
  onJump,
  onOpen,
  onOpenNotes,
  docPathForRef,
  openView,
}: {
  doc: TaskDocNode | MasterDocView;
  bodyState: TaskDocumentBodyState | undefined;
  pool: ReaderPool;
  onJump: (id: string) => void;
  onOpen: (slug: string) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  openView: (view: DocView) => void;
}) {
  const registers = registerTables(doc.sections);
  const further = furtherSections(doc, registers);
  return (
    <>
      <SprintNowBlock
        doc={doc}
        allDocs={pool.allDocs}
        onJump={onJump}
        onOpen={onOpen}
        sliceDocs={[]}
        docPathForRef={docPathForRef}
      />
      <CloseoutQueue sprintRef={taskDocumentRefForDoc(doc)} />
      <OrderBlock
        graphView={doc.executionGraphView ?? undefined}
        handlers={{ allDocs: pool.allDocs, docPathForRef, onJump }}
        openView={openView}
      />
      <SprintMastersBlock
        doc={doc}
        allDocs={pool.allDocs}
        onJump={onJump}
        onOpen={onOpen}
        sliceDocs={[]}
        docPathForRef={docPathForRef}
        openView={openView}
      />
      <RequirementsBlock requirements={doc.requirements} openView={openView} />
      <RegistersBlock sections={doc.sections} openView={openView} />
      <DecisionsBlock doc={doc} onOpenNotes={onOpenNotes} />
      <FurtherNotesBlock further={further} references={doc.references} openView={openView} />
      {bodyState !== 'loading' ? (
        <TaskNotes
          repo={doc.repository}
          master={dirName(doc.docPath)}
          references={[]}
          onOpenNotes={onOpenNotes}
        />
      ) : null}
    </>
  );
}

function SprintViewBody({
  view,
  doc,
  pool,
  onJump,
  onOpen,
  onBack,
  docPathForRef,
  onOpenNotes,
}: {
  view: DocView;
  doc: TaskDocNode | MasterDocView;
  pool: ReaderPool;
  onJump: (id: string) => void;
  onOpen: (slug: string) => void;
  onBack: () => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  if (view.kind === 'group') {
    const groups = groupSprintEntries(sprintEntries(doc, pool.allDocs, docPathForRef));
    const refs = (view.status === 'done' ? groups.done : groups.gone).map((entry) => entry.ref);
    return (
      <DocViewShell
        title={view.status === 'done' ? 'Completed masters' : 'Abandoned masters'}
        count={`${refs.length}`}
        onBack={onBack}
      >
        <GroupViewBody
          refs={refs}
          discarded={view.status === 'gone' ? (doc.discardedSubTasks ?? undefined) : undefined}
          positions={fullOrderPositions(doc.subTasks)}
          handlers={{ sliceDocs: [], onOpen, onJump, docPathForRef }}
          allDocs={pool.allDocs}
        />
      </DocViewShell>
    );
  }
  if (view.kind === 'orderGraph' && doc.executionGraphView) {
    return (
      <DocViewShell
        title="Order graph"
        count={plural(doc.executionGraphView.nodes.length, 'unit')}
        onBack={onBack}
      >
        <OrderGraphViewBody graphView={doc.executionGraphView} />
      </DocViewShell>
    );
  }
  if (view.kind === 'rows') {
    return (
      <DocViewShell
        title="Manager and seat rows of the sprint document"
        count={`${doc.subTasks.length}`}
        onBack={onBack}
      >
        <SprintRowsViewBody
          refs={doc.subTasks}
          allDocs={pool.allDocs}
          docPathForRef={docPathForRef}
        />
      </DocViewShell>
    );
  }
  return <DocContentView view={view} doc={doc} handlers={{ onBack, onOpenNotes }} scope="sprint" />;
}

// ---------------------------------------------------------------------------
// The master skeleton, in this order (rule 4, with MIK-R150 rule 14): head,
// objective, Now, Tasks, Requirements, Decisions, Further notes, Notes. The
// order of a master's tasks is drawn by MIK-R128; this leaf keeps the received
// order.
// ---------------------------------------------------------------------------

function MasterOverviewPage({
  doc,
  bodyState,
  sliceDocs,
  pool,
  onOpen,
  onJump,
  onOpenChangeSet,
  onOpenNotes,
  docPathForRef,
}: {
  doc: MasterDocView;
  bodyState: TaskDocumentBodyState | undefined;
  sliceDocs: TaskDocNode[];
  pool?: ReaderPool;
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
}) {
  const { view, openView, back } = useDocView();
  const resolvedPool: ReaderPool = pool ?? { allDocs: [], seriesList: [] };
  const chain = docChain(doc, resolvedPool.allDocs, resolvedPool.seriesList);
  if (view) {
    return (
      <TaskRequirementBoundary doc={doc} onOpenNotes={onOpenNotes}>
        <MasterViewBody
          view={view}
          doc={doc}
          sliceDocs={sliceDocs}
          onOpen={onOpen}
          onJump={onJump}
          onBack={back}
          docPathForRef={docPathForRef}
          onOpenNotes={onOpenNotes}
        />
      </TaskRequirementBoundary>
    );
  }
  return (
    <TaskRequirementBoundary doc={doc} onOpenNotes={onOpenNotes}>
      <div className={page} data-testid="skeleton-master">
        <OverviewTop
          doc={doc}
          chain={chain}
          counts={masterTasks(doc)}
          bodyState={bodyState}
          onJump={onJump}
          openView={openView}
          onOpenChangeSet={onOpenChangeSet}
        />
        <CloseoutQueue sprintRef={taskDocumentRefForDoc(doc)} />
        <MasterPageBody
          doc={doc}
          bodyState={bodyState}
          sliceDocs={sliceDocs}
          onOpen={onOpen}
          onJump={onJump}
          onOpenNotes={onOpenNotes}
          docPathForRef={docPathForRef}
          openView={openView}
        />
      </div>
    </TaskRequirementBoundary>
  );
}

function MasterPageBody({
  doc,
  bodyState,
  sliceDocs,
  onOpen,
  onJump,
  onOpenNotes,
  docPathForRef,
  openView,
}: {
  doc: MasterDocView;
  bodyState: TaskDocumentBodyState | undefined;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  openView: (view: DocView) => void;
}) {
  // A master renders no register tables: table-bearing sections stay whole in
  // Further notes (rule 7), never consumed into a block the form does not draw.
  const further = furtherSections(doc, []);
  return (
    <>
      <MasterNowBlock
        doc={doc}
        sliceDocs={sliceDocs}
        onOpen={onOpen}
        onJump={onJump}
        docPathForRef={docPathForRef}
      />
      <OrderBlock
        graphView={doc.executionGraphView}
        openView={openView}
        form="master"
        handlers={{
          allDocs: sliceDocs,
          onJump,
          docPathForRef: docPathForRef ?? ((ref) => docPathForTaskRef(sliceDocs, ref)),
        }}
      />
      <MasterTasksBlock
        doc={doc}
        sliceDocs={sliceDocs}
        onOpen={onOpen}
        onJump={onJump}
        docPathForRef={docPathForRef}
        openView={openView}
      />
      <RequirementsBlock requirements={doc.requirements} openView={openView} />
      <LongTextBlock
        title="Design"
        block="design"
        text={doc.design ?? ''}
        viewKind="design"
        openView={openView}
      />
      <FixedMarkdownBlock title="Acceptance" block="acceptance" text={doc.acceptance} />
      <DecisionsBlock doc={doc} onOpenNotes={onOpenNotes} />
      <FurtherNotesBlock further={further} references={doc.references} openView={openView} />
      {bodyState !== 'loading' ? (
        <TaskNotes
          repo={doc.repository}
          master={dirName(doc.docPath)}
          references={[]}
          onOpenNotes={onOpenNotes}
        />
      ) : null}
    </>
  );
}

function MasterViewBody({
  view,
  doc,
  sliceDocs,
  onOpen,
  onJump,
  onBack,
  docPathForRef,
  onOpenNotes,
}: {
  view: DocView;
  doc: MasterDocView;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  onBack: () => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  if (view.kind === 'orderGraph' && doc.executionGraphView) {
    return (
      <DocViewShell title="Order" count="all tasks" onBack={onBack}>
        <OrderGraphViewBody graphView={doc.executionGraphView} />
      </DocViewShell>
    );
  }
  if (view.kind === 'group') {
    const groups = groupSubTaskRows(doc.subTasks);
    const refs = view.status === 'done' ? groups.done : groups.gone;
    return (
      <DocViewShell
        title={view.status === 'done' ? 'Completed tasks' : 'Abandoned tasks'}
        count={`${refs.length}`}
        onBack={onBack}
      >
        <GroupViewBody
          refs={refs}
          discarded={view.status === 'gone' ? (doc.discardedSubTasks ?? undefined) : undefined}
          positions={fullOrderPositions(doc.subTasks)}
          handlers={{ sliceDocs, onOpen, onJump, docPathForRef }}
          parentId={doc.id}
        />
      </DocViewShell>
    );
  }
  return <DocContentView view={view} doc={doc} handlers={{ onBack, onOpenNotes }} scope="master" />;
}

// One switch on the task's own page for a planning leaf (MIK-R150 rules 10
// and 11): the label, a track with a knob, and the served words beside it. The
// switch is disabled while its request runs and keeps its position; a refusal
// or an unreachable server keeps the real state and shows the server's words on
// the same line. The page has no second control for the grant.
function DirectExecutionSwitch({ doc }: { doc: TaskDocNode }) {
  const docRef = taskDocumentRefForDoc(doc);
  const docKey = docRef ? `${docRef.repository}:${docRef.path}` : doc.id;
  const { grant, pending, notice, run } = useDirectGrant(docKey, docRef, doc.directGrant ?? null);
  if (!docRef) return null;
  const on = grant !== null;
  return (
    <div className={directLine} data-testid="taskdoc-direct-line">
      <span>Direct execution</span>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label="Direct execution"
        className={cx(directSwitch, on ? directSwitchOn : directSwitchOff)}
        data-testid="taskdoc-direct-switch"
        disabled={pending}
        onClick={() => void run(on ? 'revoke' : 'give')}
      >
        <span className={directKnob} />
      </button>
      {on && grant ? <span className={directGiven}>given by {grant.grantedBy}</span> : null}
      {notice ? (
        <span className={directNote} data-testid="taskdoc-direct-note">
          {' '}
          · {notice}
        </span>
      ) : null}
    </div>
  );
}

// The grant's words where it cannot be changed (MIK-R150 rule 12): a task that
// is not planning shows `Direct · given by <who>` when it has a grant and
// nothing when it has none.
function DirectExecutionWords({ doc }: { doc: TaskDocNode }) {
  const served = doc.directGrant ?? null;
  if (!served) return null;
  return (
    <div className={directLine} data-testid="taskdoc-direct-line">
      <span className={directGiven}>Direct · given by {served.grantedBy}</span>
    </div>
  );
}

// Under the head of the task's own page (MIK-R150 rules 9, 10 and 12): one
// switch for a planning leaf, bare words for any other granted state, and
// nothing otherwise. A master page never renders this; rows carry no control.
function TaskDirectGrant({ doc }: { doc: TaskDocNode }) {
  if (doc.kind !== 'subTask') return null;
  if (doc.status === 'planning') return <DirectExecutionSwitch doc={doc} />;
  return <DirectExecutionWords doc={doc} />;
}

// Two served grants match when the server names the same giver at the same time;
// the row's other fields never reopen the control's state.
function sameServedGrant(next: DirectGrantNode | null, prev: DirectGrantNode | null): boolean {
  return (
    (next?.grantedBy ?? null) === (prev?.grantedBy ?? null) &&
    (next?.grantedAt ?? null) === (prev?.grantedAt ?? null)
  );
}

// The control's live grant: the served add/remove/issuer change replaces it, a
// refused or unreachable request keeps the actual grant, a selection change resets
// it, and a late reply is ignored. The sync keys on the document key and the
// served giver/instant content — never on grant object identity.
function useDirectGrant(
  docKey: string,
  docRef: TaskDocumentRef | undefined,
  initialGrant: DirectGrantNode | null | undefined,
): {
  grant: DirectGrantNode | null;
  pending: boolean;
  notice: string | null;
  run: (action: "give" | "revoke") => Promise<void>;
} {
  const fallback = initialGrant ?? null;
  const [grant, setGrant] = useState<DirectGrantNode | null>(fallback);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const served = useRef<{ key: string; grant: DirectGrantNode | null }>({ key: docKey, grant: fallback });
  const callId = useRef(0);
  // The current render's grant for the sync below. A ref mirror (never a
  // dependency) so the effect lists the content mark it truly depends on.
  const grantRef = useRef<DirectGrantNode | null>(fallback);
  grantRef.current = fallback;
  const servedMark = `${fallback?.grantedBy ?? ""}\0${fallback?.grantedAt ?? ""}`;
  useEffect(() => {
    const next = grantRef.current;
    if (served.current.key !== docKey) {
      served.current = { key: docKey, grant: next };
      callId.current += 1;
      setGrant(next);
      setNotice(null);
      setPending(false);
      return;
    }
    if (!sameServedGrant(next, served.current.grant)) {
      served.current = { key: docKey, grant: next };
      setGrant(next);
    }
  }, [docKey, servedMark]);
  const run = async (action: "give" | "revoke") => {
    if (!docRef) return;
    const mine = ++callId.current;
    setPending(true);
    setNotice(null);
    const outcome = await postDirectExecutionGrant(docRef.repository, docRef.path, action);
    if (mine !== callId.current) return;
    setPending(false);
    if (outcome.ok) {
      served.current = { key: docKey, grant: outcome.grant };
      setGrant(outcome.grant);
    }
    setNotice(outcome.text);
  };
  return { grant, pending, notice, run };
}

// ---------------------------------------------------------------------------
// The task skeleton, in this order (rule 5, with MIK-R150 rule 14): head,
// objective, Steps, Review, Requirements, Design, Proposed code, Decisions,
// References and reports (MIK-R126 rule 5) as one block, Further notes, Notes.
// ---------------------------------------------------------------------------

function TaskReaderPage({
  doc,
  bodyState,
  pool,
  onJump,
  onOpenChangeSet,
  onOpenNotes,
}: {
  doc: TaskDocNode;
  bodyState: TaskDocumentBodyState | undefined;
  pool?: ReaderPool;
  onJump?: (id: string) => void;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  const { view, openView, back } = useDocView();
  const resolvedPool: ReaderPool = pool ?? { allDocs: [], seriesList: [] };
  const chain = docChain(doc, resolvedPool.allDocs, resolvedPool.seriesList);
  // A task renders no register tables: table-bearing sections stay whole in
  // Further notes (rule 7), never consumed into a block the form does not draw.
  const further = furtherSections(doc, []);
  const progress = taskStepProgress(doc);
  const leafKey = qualifiedLeafKey(doc);
  const jump = onJump ?? ((): void => {});
  if (view) {
    return (
      <TaskRequirementBoundary doc={doc} onOpenNotes={onOpenNotes}>
        <div data-task-leaf-key={leafKey}>
          <TaskViewBody view={view} doc={doc} onBack={back} onOpenNotes={onOpenNotes} />
        </div>
      </TaskRequirementBoundary>
    );
  }
  return (
    <TaskRequirementBoundary doc={doc} onOpenNotes={onOpenNotes}>
      <div className={page} data-task-leaf-key={leafKey} data-testid="skeleton-task">
        <DocHead doc={doc} chain={chain} onJump={jump} openView={openView} />
        <TaskDirectGrant doc={doc} />
        <TaskBodyNotice state={bodyState} />
        <ReaderHead
          doc={doc}
          bodyState={bodyState}
          kind="leaf"
          leaf={doc.id}
          onOpenChangeSet={onOpenChangeSet}
        />
        <ObjectiveLead text={doc.objective} openView={openView} />
        <FixedMarkdownBlock title="Non-goals" block="nonGoals" text={doc.nonGoals} />
        <StepsBlock steps={doc.steps} done={progress.done} total={progress.total} />
        <ReviewBlock review={doc.reviewState} openView={openView} />
        <RequirementsBlock requirements={doc.requirements} openView={openView} />
        <LongTextBlock
          title="Design"
          block="design"
          text={doc.design ?? ''}
          viewKind="design"
          openView={openView}
        />
        <FixedMarkdownBlock title="Acceptance" block="acceptance" text={doc.acceptance} />
        <CodeTitlesBlock examples={doc.codeExamples} openView={openView} />
        <DecisionsBlock doc={doc} onOpenNotes={onOpenNotes} />
        {bodyState !== 'loading' ? (
          <TaskPageRefsReports doc={doc} openView={openView} onOpenNotes={onOpenNotes} />
        ) : null}
        <FurtherNotesBlock further={further} references={[]} openView={openView} />
        {bodyState !== 'loading' ? <TaskPageNotesLine doc={doc} onOpenNotes={onOpenNotes} /> : null}
      </div>
    </TaskRequirementBoundary>
  );
}

// References and reports before Further notes (rule 5): one block under one
// heading, mounted only once the body arrives so no ancillary request precedes it.
function TaskPageRefsReports({
  doc,
  openView,
  onOpenNotes,
}: {
  doc: TaskDocNode;
  openView: (view: DocView) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  // The reports of a task are asked for by the document's path inside the task
  // root: the server refuses the file path a projected document carries.
  const { refs, reports } = useTaskPageNotes(
    doc.repository,
    dirName(doc.docPath),
    taskDocumentRefForDoc(doc)?.path ?? doc.docPath,
    doc.references,
    onOpenNotes,
  );
  return (
    <TaskReferencesAndReports
      refs={refs}
      reports={reports}
      references={doc.references}
      limit={REFERENCE_BOUND}
      onOpenAll={() => openView({ kind: 'references' })}
    />
  );
}

// The Notes line last (rule 5): its own island over the folder count alone.
function TaskPageNotesLine({
  doc,
  onOpenNotes,
}: {
  doc: TaskDocNode;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  const { topCount, openRoot } = useTaskPageNotesLine(
    doc.repository,
    dirName(doc.docPath),
    onOpenNotes,
  );
  if (topCount === null) return null;
  return <TaskNotesNotesSection data={{ topCount, openRoot }} />;
}

function TaskViewBody({
  view,
  doc,
  onBack,
  onOpenNotes,
}: {
  view: DocView;
  doc: TaskDocNode;
  onBack: () => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  if (view.kind === 'codeExample') {
    const example = doc.codeExamples.find((entry) => entry.id === view.id);
    if (!example) {
      return (
        <DocViewShell title="Proposed code" onBack={onBack}>
          <p className="muted">This example is no longer in the document.</p>
        </DocViewShell>
      );
    }
    return (
      <DocViewShell title={labelWithId(example.id, example.title)} onBack={onBack}>
        <CodeExample example={example} />
      </DocViewShell>
    );
  }
  return <DocContentView view={view} doc={doc} handlers={{ onBack, onOpenNotes }} scope="task" />;
}

// Fallback for a series with no master yet: the slice list, now clickable into each reader.
export function SliceList({
  sliceDocs,
  onOpen,
}: {
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
}) {
  return (
    <div className={series}>
      <div className={taskHead}>
        <span className={badge}>series</span> {sliceDocs.length} task slices
      </div>
      <ul className={slices}>
        {orderedByCreation(sliceDocs).map((doc) => {
          const progress = taskStepProgress(doc);
          return (
            <li key={doc.docPath}>
              <button
                type="button"
                className={sliceButton}
                onClick={() => onOpen(sliceSlug(doc))}
                data-testid={`slice-open-${sliceSlug(doc)}`}
              >
                <span>{doc.title}</span>
                <span className={sliceMeta}>
                  {progress.total > 0 ? `${progress.done}/${progress.total} · ` : ''}
                  {doc.status}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function SpineLane({
  kind,
  title,
  repo,
  engines,
}: {
  kind: 'code' | 'memory';
  title: string;
  repo: string;
  engines: ProviderNode[];
}) {
  return (
    <div className={lane({ kind })}>
      <div className={laneTitle}>{title}</div>
      <div className={laneRepo}>{repo}</div>
      {engines.length > 0 ? (
        <div className={laneMeta}>
          {engines.map((engine) => (
            <span key={engine.id}>{engine.state}</span>
          ))}
        </div>
      ) : (
        <span className="muted">no isolated engine recorded</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// One reader per selected document. What the reader opened on one document
// (a view of its own, the objective, a note line, a row) belongs to that
// document: the next one selected starts closed. The key is the document's
// identity, so the body that arrives later for the same document changes nothing.
// ---------------------------------------------------------------------------

export function SprintOverview(props: Parameters<typeof SprintOverviewPage>[0]) {
  return <SprintOverviewPage key={docIdentity(props.doc)} {...props} />;
}

export function MasterOverview(props: Parameters<typeof MasterOverviewPage>[0]) {
  return <MasterOverviewPage key={docIdentity(props.doc)} {...props} />;
}

export function TaskReader(props: Parameters<typeof TaskReaderPage>[0]) {
  return <TaskReaderPage key={docIdentity(props.doc)} {...props} />;
}

export { TaskBodyNotice } from './readerTop';

// A row whose ref points at another master is a parallel/external series:
// resolveRowTarget in task-groups.tsx opens it, so the readers share the one
// implementation.

// The decision log front page stays importable from the reader module for its
// focused tests.
export { DecisionPreview } from './skeletons';
export { seriesAsMasterDoc };
export type { MasterDocView };
