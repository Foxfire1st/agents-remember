// Fixed task-page skeletons. Entry identity and membership stay separate from reader content.
import { useMemo } from 'react';
import { useDashboard } from '../../data/store';
import { taskBodyKey } from '../../data/taskDocuments';
import { TaskRequirementLinksProvider } from '../../grammar/TaskRequirementLinks';
import type { TaskDocumentBodyState } from '../../data/useTaskDocumentBody';
import { taskReferenceKey, type DocumentTaskEntry } from '../../data/taskReference';
import type { TaskEntry, TaskSummary } from '../../types/projection';
import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../../data/taskArtifacts';
import type { ChangeSetTarget } from '../changeset/ChangeSetViewer';
import {
  TaskNotesNotesSection,
  TaskReferencesAndReports,
  useTaskPageNotes,
  useTaskPageNotesLine,
} from '../TaskNotes';
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
  type DocView,
  useDocView,
} from './skeletons';
import { DocContentView } from './docViews';
import { TaskBodyNotice } from './readerTop';
import { OrderBlock, OrderGraphViewBody } from './orderGraph';
import { GroupViewBody, SprintRowsViewBody, TaskChildrenBlock, TaskNowBlock } from './task-groups';
import { dirName, docChain, furtherSections, labelWithId, registerTables } from './model';
import { TaskDirectGrant } from './directGrant';
import { page } from './pageStyles';
import { TaskStatusClock, taskStatusKey } from './pageParts';

export interface ReaderProps {
  entry: DocumentTaskEntry;
  doc: TaskSummary;
  bodyState: TaskDocumentBodyState | undefined;
  tasks: Record<string, TaskEntry>;
  onJump: (id: string) => void;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}

export function TaskReader({
  active = true,
  ...props
}: Omit<ReaderProps, 'entry'> & { entry: TaskEntry; active?: boolean }) {
  if (!props.entry.reference) return null;
  const shown = [
    props.entry,
    ...props.entry.children.map((key) => props.tasks[key]),
    ...[...(props.doc.waitsForTasks ?? []), ...(props.doc.neededByTasks ?? [])]
      .filter((dependency) => !dependency.gone && dependency.reference)
      .map((dependency) => props.tasks[taskReferenceKey(dependency.reference!)]),
  ];
  // A served limit counts down on the page as a timed event does, so a limited entry opens the
  // shared clock even when the host timed no event on it (MIK-R190 rule 13).
  const counting = shown.some(
    (entry) => Boolean(entry?.eventAt) || (entry ? taskStatusKey(entry) === 'limited' : false),
  );
  return (
    <TaskStatusClock active={active && counting}>
      <TaskPage
        key={props.entry.key}
        {...props}
        entry={{ ...props.entry, reference: props.entry.reference }}
      />
    </TaskStatusClock>
  );
}

function TaskPage(props: ReaderProps) {
  const { entry, doc, bodyState, tasks, onJump, onOpenChangeSet, onOpenNotes } = props;
  const { view, openView, back } = useDocView();
  const chain = docChain(entry, tasks);
  const children = entry.children.length;
  const counts =
    doc.form === 'task' || children === 0
      ? []
      : [{ label: doc.form === 'sprint' ? 'masters' : 'tasks', value: children }];
  const reference = entry.reference;
  return (
    <TaskRequirementLinksProvider
      repo={reference.repository}
      master={dirName(reference.path)}
      document={bodyState === 'loading' ? undefined : reference.path}
      onOpenArtifact={onOpenNotes}
    >
      {view ? (
        <TaskViewBody {...props} view={view} onBack={back} />
      ) : (
        <div className={page} data-testid={`skeleton-${doc.form}`}>
          <DocHead
            entry={entry}
            doc={doc}
            chain={chain}
            onJump={onJump}
            openView={openView}
            counts={counts}
          />
          {doc.form === 'task' ? <TaskDirectGrant entry={entry} doc={doc} /> : null}
          <TaskBodyNotice state={bodyState} />
          <ReaderHead
            entry={entry}
            bodyState={bodyState}
            kind={doc.form === 'task' ? 'leaf' : 'master'}
            onOpenChangeSet={onOpenChangeSet}
          />
          <ObjectiveLead text={doc.objective} openView={openView} />
          {doc.form !== 'sprint' ? (
            <FixedMarkdownBlock title="Non-goals" block="nonGoals" text={doc.nonGoals} />
          ) : null}
          {doc.form === 'task' ? (
            <LeafBody {...props} openView={openView} />
          ) : (
            <OverviewBody {...props} openView={openView} />
          )}
        </div>
      )}
    </TaskRequirementLinksProvider>
  );
}

function OverviewBody(props: ReaderProps & { openView: (view: DocView) => void }) {
  const { entry, doc, bodyState, tasks, onJump, onOpenNotes, openView } = props;
  const { registers, further } = usePreparedReaderContent(entry, doc, bodyState);
  const sprint = doc.form === 'sprint';
  return (
    <>
      <TaskNowBlock entry={entry} tasks={tasks} onJump={onJump} />
      <CloseoutQueue sprintRef={entry.reference} />
      <OrderBlock
        graphView={doc.executionGraphView ?? undefined}
        handlers={{ tasks, onJump }}
        openView={openView}
        form={sprint ? 'sprint' : 'master'}
      />
      <TaskChildrenBlock
        entry={entry}
        doc={doc}
        tasks={tasks}
        onJump={onJump}
        openView={openView}
      />
      <RequirementsBlock requirements={doc.requirements} openView={openView} />
      {!sprint ? (
        <>
          <LongTextBlock
            title="Design"
            block="design"
            text={doc.design ?? ''}
            viewKind="design"
            openView={openView}
          />
          <FixedMarkdownBlock title="Acceptance" block="acceptance" text={doc.acceptance} />
        </>
      ) : null}
      {sprint ? <RegistersBlock registers={registers} openView={openView} /> : null}
      <DecisionsBlock entry={entry} doc={doc} onOpenNotes={onOpenNotes} />
      <FurtherNotesBlock further={further} references={doc.references} openView={openView} />
      {bodyState !== 'loading' ? (
        <TaskNotes
          repo={entry.reference.repository}
          master={dirName(entry.reference.path)}
          references={[]}
          onOpenNotes={onOpenNotes}
        />
      ) : null}
    </>
  );
}

function LeafBody(props: ReaderProps & { openView: (view: DocView) => void }) {
  const { entry, doc, bodyState, onOpenNotes, openView } = props;
  const { further } = usePreparedReaderContent(entry, doc, bodyState);
  return (
    <>
      <StepsBlock steps={doc.steps} done={entry.stepsDone} total={entry.stepsTotal} />
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
      <DecisionsBlock entry={entry} doc={doc} onOpenNotes={onOpenNotes} />
      {bodyState !== 'loading' ? <TaskPageRefsReports {...props} /> : null}
      <FurtherNotesBlock further={further} references={[]} openView={openView} />
      {bodyState !== 'loading' ? (
        <TaskPageNotesLine entry={entry} onOpenNotes={onOpenNotes} />
      ) : null}
    </>
  );
}

function TaskPageRefsReports({
  entry,
  doc,
  openView,
  onOpenNotes,
}: ReaderProps & { openView: (view: DocView) => void }) {
  const reference = entry.reference;
  const { refs, reports } = useTaskPageNotes(
    reference.repository,
    dirName(reference.path),
    reference.path,
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

function TaskPageNotesLine({ entry, onOpenNotes }: Pick<ReaderProps, 'entry' | 'onOpenNotes'>) {
  const reference = entry.reference;
  const { topCount, openRoot } = useTaskPageNotesLine(
    reference.repository,
    dirName(reference.path),
    onOpenNotes,
  );
  return topCount === null ? null : <TaskNotesNotesSection data={{ topCount, openRoot }} />;
}

function TaskViewBody({
  view,
  onBack,
  ...props
}: ReaderProps & { view: DocView; onBack: () => void }) {
  const { entry, doc, tasks, onJump, onOpenNotes } = props;
  if (view.kind === 'group') {
    const stage = view.status === 'done' ? 'completed' : 'aborted';
    return (
      <DocViewShell
        title={stage === 'completed' ? 'Completed tasks' : 'Aborted tasks'}
        count={String(entry.counts[stage])}
        onBack={onBack}
      >
        <GroupViewBody entry={entry} doc={doc} tasks={tasks} onJump={onJump} stage={stage} />
      </DocViewShell>
    );
  }
  if (view.kind === 'rows')
    return (
      <DocViewShell title="Manager and seat rows" onBack={onBack}>
        <SprintRowsViewBody entry={entry} doc={doc} tasks={tasks} onJump={onJump} />
      </DocViewShell>
    );
  if (view.kind === 'orderGraph' && doc.executionGraphView)
    return (
      <DocViewShell title="Order graph" onBack={onBack}>
        <OrderGraphViewBody graphView={doc.executionGraphView} />
      </DocViewShell>
    );
  if (view.kind === 'codeExample') {
    const example = doc.codeExamples.find((item) => item.id === view.id);
    return (
      <DocViewShell
        title={example ? labelWithId(example.id, example.title) : 'Proposed code'}
        onBack={onBack}
      >
        {example ? (
          <CodeExample example={example} />
        ) : (
          <p className="muted">This example is no longer in the document.</p>
        )}
      </DocViewShell>
    );
  }
  return (
    <DocContentView
      entry={entry}
      view={view}
      doc={doc}
      handlers={{ onBack, onOpenNotes }}
      scope={doc.form}
    />
  );
}

export { TaskBodyNotice } from './readerTop';
export { DecisionPreview } from './skeletons';

function usePreparedReaderContent(
  entry: DocumentTaskEntry,
  doc: TaskSummary,
  state: TaskDocumentBodyState | undefined,
) {
  const resources = useDashboard((current) => current.taskDocuments);
  return useMemo(() => {
    const make = () => {
      const registers = doc.form === 'sprint' ? registerTables(doc.sections) : [];
      return { registers, further: furtherSections(doc, registers) };
    };
    return state === 'available'
      ? resources.prepare(`reader:${taskBodyKey(entry.reference, doc.bodyRevision)}`, make)
      : make();
  }, [resources, entry.reference, doc, state]);
}
