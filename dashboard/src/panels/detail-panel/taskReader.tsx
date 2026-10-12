// The DetailPanel task-document reader family: master overview, sub-task index, slice list, spine
// lanes, full reader, and the small section primitives (Section/Bullets/StepList/CodeExample/
// DecisionList). All presentation logic lives here; doc selection stays in model.ts.
import { useEffect, useRef, useState, type ReactNode } from "react";

import { cx } from "../../../styled-system/css";
import { Markdown } from "../../grammar/Markdown";
import { TaskRequirementLinksProvider } from "../../grammar/TaskRequirementLinks";
import { ProgressFill } from "../../grammar/ProgressFill";
import {
  type TaskDocumentBodyState,
} from "../../data/useTaskDocumentBody";
import { orderedByCreation } from "../../data/taskHierarchy";
import type { DirectGrantNode } from "../../types/projection";
import { postDirectExecutionGrant } from "../../data/directExecutionGrant";
import {
  qualifiedLeafKey,
  taskDocSelectionKey,
  taskDocumentRefForDoc,
} from "../../data/taskIdentity";
import type {
  DiscardedSubTaskNode,
  ProviderNode,
  SubTaskRow,
  TaskCodeExampleNode,
  TaskDecisionNode,
  TaskDocNode,
  TaskDocumentRef,
  TaskSectionNode,
  TaskStepNode,
} from "../../types/projection";
import type { TaskArtifactReaderTarget as NotesReaderTarget } from "../../data/taskArtifacts";
import { CloseoutQueue } from "../CloseoutQueue";
import { SprintGraphView } from "../sprint-graph/SprintGraphView";
import { DocChangeSetBar } from "./changeSetBar";
import {
  dirName,
  labelWithId,
  orderDecisionsNewestFirst,
  sliceForRef,
  sliceSlug,
  subTaskKey,
  taskStepProgress,
  type MasterDocView,
} from "./model";
import {
  SUBSTEP,
  badge,
  crossButton,
  grantButton,
  lane,
  laneMeta,
  laneRepo,
  laneTitle,
  series,
  skippedDisposition,
  skippedWord,
  slice,
  sliceButton,
  sliceMeta,
  slices,
  STEP_MARK,
  STEP_TITLE,
  stepMarkBase,
  stepRow,
  stepsList,
  substeps,
  taskHead,
  taskdoc,
  taskdocBullets,
  taskdocCode,
  taskdocCodeHead,
  taskdocCodeMeta,
  taskdocDecision,
  taskdocDecisionMeta,
  taskdocDecisions,
  taskdocH,
  taskdocHead,
  taskdocSection,
  taskdocSnippet,
  taskdocStatus,
  taskdocTitle,
} from "./styles";
import type { ChangeSetTarget } from "../changeset/ChangeSetViewer";
import { TaskNotes } from "../TaskNotes";

function TaskRequirementBoundary({
  doc,
  onOpenNotes,
  children,
}: {
  doc: Pick<TaskDocNode, "repository" | "docPath">;
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

export function TaskContent({
  docs,
  bodyState,
  onOpen,
  onJump,
  onOpenChangeSet,
  onOpenNotes,
  docPathForRef,
}: {
  docs: TaskDocNode[];
  bodyState: TaskDocumentBodyState | undefined;
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
}) {
  if (docs.length === 0) {
    return <p className="muted">No task document bound to this task.</p>;
  }
  const master = docs.find((doc) => doc.kind === "master");
  const sliceDocs = docs.filter((doc) => doc.kind !== "master");
  if (master) {
    return (
      <MasterOverview
        doc={master}
        bodyState={bodyState}
        sliceDocs={sliceDocs}
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
        onOpenChangeSet={onOpenChangeSet}
        onOpenNotes={onOpenNotes}
      />
    );
  }
  return <SliceList sliceDocs={sliceDocs} onOpen={onOpen} />;
}

// The master overview: identity + objective, then its ordered render plan (`sections`). A
// `subTasks` section renders the clickable index in place; `sharedDecisions` renders the decision
// table. If no section drives the index but the master carries one, it is appended.
function MasterOverviewHeader({
  doc,
  bodyState,
  onOpenChangeSet,
}: {
  doc: MasterDocView;
  bodyState: TaskDocumentBodyState | undefined;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
}) {
  return (
    <>
      <div className={taskdocHead}>
        <span className={badge}>{doc.kind}</span>
        <span className={taskdocTitle}>{doc.title}</span>
        <span className={taskdocStatus}>{doc.status}</span>
      </div>
      <TaskBodyNotice state={bodyState} />
      {bodyState !== "loading" ? (
        <DocChangeSetBar
          kind="master"
          repo={doc.repository}
          master={dirName(doc.docPath)}
          onOpen={onOpenChangeSet}
        />
      ) : null}
    </>
  );
}

export function MasterOverview({
  doc,
  bodyState,
  sliceDocs,
  onOpen,
  onJump,
  onOpenChangeSet,
  onOpenNotes,
  docPathForRef,
}: {
  doc: MasterDocView;
  bodyState: TaskDocumentBodyState | undefined;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
}) {
  return (
    <TaskRequirementBoundary doc={doc} onOpenNotes={onOpenNotes}>
    <div className={taskdoc}>
      <MasterOverviewHeader
        doc={doc}
        bodyState={bodyState}
        onOpenChangeSet={onOpenChangeSet}
      />
      {/* Pinned navigation: the sub-task index sits above the description, always reachable. The
          authored `subTasks` section still renders its own copy in place (MasterSection). */}
      {doc.subTasks.length > 0 ? (
        <Section title="Sub-tasks">
          <SubTaskIndex
            refs={doc.subTasks}
            sliceDocs={sliceDocs}
            onOpen={onOpen}
            onJump={onJump}
            docPathForRef={docPathForRef}
          />
        </Section>
      ) : null}
      {doc.discardedSubTasks && doc.discardedSubTasks.length > 0 ? (
        <Section title={`Discarded before start (${doc.discardedCount ?? doc.discardedSubTasks.length})`}>
          <DiscardedSubTaskHistory items={doc.discardedSubTasks} />
        </Section>
      ) : null}
      {/* Graph rendering is optional, but a legal sprint projection stays reachable without it. */}
      <SprintGraphSection doc={doc} />
      <CloseoutQueue sprintRef={taskDocumentRefForDoc(doc)} />
      {doc.objective ? (
        <Section title="Objective">
          <Markdown>{doc.objective}</Markdown>
        </Section>
      ) : null}
      {doc.sections.map((section) => (
        <MasterSection
          key={section.heading}
          section={section}
          doc={doc}
          sliceDocs={sliceDocs}
          onOpen={onOpen}
          onJump={onJump}
          onOpenNotes={onOpenNotes}
          docPathForRef={docPathForRef}
        />
      ))}
      {/* The series' coordination notes (design records, friction ledger, reports/) —
          browsable from the master overview too, not only from a drilled leaf reader. */}
      {bodyState !== "loading" ? (
        <TaskNotes
          repo={doc.repository}
          master={dirName(doc.docPath)}
          references={doc.references}
          onOpenNotes={onOpenNotes}
        />
      ) : null}
    </div>
    </TaskRequirementBoundary>
  );
}

// The wave-grid is optional. CloseoutQueue is mounted by the master surface independently because
// atomic-sequential orchestration sprints are supported without an authored execution graph.
function SprintGraphSection({ doc }: { doc: MasterDocView }) {
  if (!doc.executionGraphView) return null;
  return (
    <Section title="Execution graph">
      <SprintGraphView graphView={doc.executionGraphView} />
    </Section>
  );
}
export function MasterSection({
  section,
  doc,
  sliceDocs,
  onOpen,
  onJump,
  onOpenNotes,
  docPathForRef,
}: {
  section: TaskSectionNode;
  doc: MasterDocView;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
}) {
  return (
    <Section title={section.heading}>
      {section.body ? <Markdown>{section.body}</Markdown> : null}
      {section.kind === "subTasks" ? (
        <SubTaskIndex
          refs={doc.subTasks}
          sliceDocs={sliceDocs}
          onOpen={onOpen}
          onJump={onJump}
          testidPrefix="subtask-mid"
          docPathForRef={docPathForRef}
        />
      ) : null}
      {section.kind === "sharedDecisions" ? (
        <DecisionPreview
          items={doc.decisions}
          repo={doc.repository}
          master={dirName(doc.docPath)}
          title={doc.title}
          onOpenNotes={onOpenNotes}
        />
      ) : null}
    </Section>
  );
}

// A sprint row carrying a typed masterRef (L14-R1): opens the commanded master document directly
// — the sprint → master leg of the drill-down (L14-R2). Only a task-doc master's rows can carry
// one (`SeriesSubTaskNode` has no such field), so like the cross-series link this is structurally
// unreachable for a series rendered via `seriesAsMasterDoc`.
function MasterRefIndexRow({
  ref,
  masterRef,
  masterPath,
  position,
  label,
  meta,
  onJump,
  testidPrefix,
}: {
  ref: SubTaskRow;
  masterRef: TaskDocumentRef;
  masterPath: string;
  position: number;
  label: string;
  meta: ReactNode;
  onJump: (id: string) => void;
  testidPrefix: string;
}) {
  return (
    <li key={subTaskKey(ref, position - 1)}>
      <button
        type="button"
        className={crossButton}
        onClick={() => onJump(taskDocSelectionKey(masterPath))}
        data-testid={`${testidPrefix}-master-${position}`}
        title={`open the ${masterRef.path} master document`}
      >
        <span>⇒ {label}</span>
        {meta}
      </button>
    </li>
  );
}

// The clickable series index: one row per master `SubTaskRef`. A row whose slice has been authored
// as a task document opens its reader; an un-migrated slice shows as a static index row.
function SubTaskIndexRow({
  ref,
  position,
  sliceDocs,
  onOpen,
  onJump,
  testidPrefix,
  docPathForRef,
}: {
  ref: SubTaskRow;
  position: number;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  testidPrefix: string;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
}) {
  const match = sliceForRef(sliceDocs, ref);
  const { displayNumber, displayName } = subTaskDisplay(match, ref);
  const label = `${displayNumber}. ${displayName}`;
  const meta = subTaskMeta(match, ref);
  // masterRef first (L14-R2), then the older behaviors; an unprojected masterRef target falls through.
  const masterRef = "masterRef" in ref ? ref.masterRef : undefined;
  const masterPath = masterRef && docPathForRef ? docPathForRef(masterRef) : undefined;
  if (masterRef && masterPath) {
    return (
      <MasterRefIndexRow
        ref={ref}
        masterRef={masterRef}
        masterPath={masterPath}
        position={position}
        label={label}
        meta={meta}
        onJump={onJump}
        testidPrefix={testidPrefix}
      />
    );
  }
  // A row whose ref points at another master is a parallel/external series → jump lifecycles.
  const linkedLifecycleId = "linkedLifecycleId" in ref ? ref.linkedLifecycleId : undefined;
  if (linkedLifecycleId) {
    return (
      <li key={subTaskKey(ref, position - 1)}>
        <button
          type="button"
          className={crossButton}
          onClick={() => onJump(linkedLifecycleId)}
          data-testid={`${testidPrefix}-link-${position}`}
          title={`open the ${linkedLifecycleId} series`}
        >
          <span>→ {label}</span>
          {meta}
        </button>
      </li>
    );
  }
  return (
    <SubTaskLeafRow
      ref={ref}
      match={match}
      position={position}
      label={label}
      meta={meta}
      onOpen={onOpen}
      testidPrefix={testidPrefix}
    />
  );
}

// The in-series leaf row: the drill-in button plus, for an authored leaf, the
// developer's direct-execution grant control beside it (MIK-R94 rule 2).
function SubTaskLeafRow({
  ref,
  match,
  position,
  label,
  meta,
  onOpen,
  testidPrefix,
}: {
  ref: SubTaskRow;
  match: TaskDocNode | undefined;
  position: number;
  label: string;
  meta: ReactNode;
  onOpen: (slug: string) => void;
  testidPrefix: string;
}) {
  return (
    <li key={subTaskKey(ref, position - 1)}>
      {match ? (
        <button
          type="button"
          className={sliceButton}
          onClick={() => onOpen(sliceSlug(match))}
          data-testid={`${testidPrefix}-${position}`}
        >
          <span>{label}</span>
          {meta}
        </button>
      ) : (
        <div
          className={slice}
          data-testid={`${testidPrefix}-${position}`}
          title="not authored as a task document yet"
        >
          <span>{label}</span>
          {meta}
        </div>
      )}
      {match && match.kind === "subTask" ? (
        <DirectGrantControl
          doc={match}
          initialGrant={rowDirectGrant(ref, match)}
          testidPrefix={testidPrefix}
          position={position}
        />
      ) : null}
    </li>
  );
}

function subTaskDisplay(
  match: TaskDocNode | undefined,
  ref: SubTaskRow,
): { displayNumber: string; displayName: string } {
  return {
    displayNumber: match?.id || ref.number,
    displayName: match?.title || ref.name,
  };
}

function subTaskMeta(match: TaskDocNode | undefined, ref: SubTaskRow): ReactNode {
  const progress = match ? taskStepProgress(match) : undefined;
  const grant = rowDirectGrant(ref, match);
  return (
    <span className={sliceMeta}>
      {progress && progress.total > 0 ? `${progress.done}/${progress.total} · ` : ""}
      {ref.status}
      {grant ? (
        <>
          {" · "}
          <span className={badge}>Direct · {grant.grantedBy}</span>
        </>
      ) : null}
    </span>
  );
}

// A leaf's grant from either row shape: a series row carries it directly, a master
// index row reads it off the matched leaf document (MIK-R94 rule 4).
function rowDirectGrant(
  ref: SubTaskRow,
  match: TaskDocNode | undefined,
): DirectGrantNode | null | undefined {
  if ("directGrant" in ref && ref.directGrant) return ref.directGrant;
  return match?.directGrant;
}

// The developer's direct-execution grant control on an authored leaf row (MIK-R94
// rule 2): give the grant or take it back through the serving route, showing the
// server's words. A sibling of the drill-in button, never nested inside it.
//
// The control follows the live projection: a served add/remove/issuer change
// replaces its grant, while a refused or unreachable request keeps the actual
// grant and only shows the server's words. A selection change resets it, and a
// reply that arrives after one is ignored, so grant state never transfers to
// another leaf (F06).
function DirectGrantControl({
  doc,
  initialGrant,
  testidPrefix,
  position,
}: {
  doc: TaskDocNode;
  initialGrant: DirectGrantNode | null | undefined;
  testidPrefix: string;
  position: number;
}) {
  const docRef = taskDocumentRefForDoc(doc);
  const docKey = docRef ? `${docRef.repository}:${docRef.path}` : doc.id;
  const [grant, setGrant] = useState<DirectGrantNode | null>(initialGrant ?? null);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const served = useRef<{ key: string; grant: DirectGrantNode | null }>({
    key: docKey,
    grant: initialGrant ?? null,
  });
  const callId = useRef(0);
  const servedBy = initialGrant?.grantedBy ?? null;
  const servedAt = initialGrant?.grantedAt ?? null;
  useEffect(() => {
    if (served.current.key !== docKey) {
      served.current = { key: docKey, grant: initialGrant ?? null };
      callId.current += 1;
      setGrant(initialGrant ?? null);
      setNotice(null);
      setPending(false);
      return;
    }
    const next = initialGrant ?? null;
    const prev = served.current.grant;
    if (
      (next?.grantedBy ?? null) !== (prev?.grantedBy ?? null) ||
      (next?.grantedAt ?? null) !== (prev?.grantedAt ?? null)
    ) {
      served.current = { key: docKey, grant: next };
      setGrant(next);
    }
  }, [docKey, servedBy, servedAt]);
  if (!docRef) return null;
  const run = async (action: "give" | "revoke") => {
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
  return (
    <span className={sliceMeta}>
      <button
        type="button"
        className={grantButton}
        disabled={pending}
        onClick={() => void run(grant ? "revoke" : "give")}
        data-testid={`${testidPrefix}-direct-${position}`}
        title={
          grant
            ? `Take back this leaf's direct-execution grant (${grant.grantedBy})`
            : "Give this leaf its direct-execution grant"
        }
      >
        {pending ? "…" : grant ? "Take back" : "Make direct"}
      </button>
      {notice ? (
        <span data-testid={`${testidPrefix}-direct-note-${position}`}> · {notice}</span>
      ) : null}
    </span>
  );
}

export function SubTaskIndex({
  refs,
  sliceDocs,
  onOpen,
  onJump,
  testidPrefix = "subtask-open",
  docPathForRef,
}: {
  refs: SubTaskRow[];
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  testidPrefix?: string;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
}) {
  if (refs.length === 0) {
    return <p className="muted">No sub-tasks indexed.</p>;
  }
  // Rendered in the order received, deliberately. Neither source needs a client-side sort: a
  // master's `TaskSubTaskRefNode` rows carry no `createdAt` at all (so the old
  // `orderedByCreation(refs)` here could never do anything), and a series' `SeriesSubTaskNode`
  // rows arrive already ordered by it from `snapshots.py::_series_subtask_nodes`.
  return (
    <ul className={slices}>
      {refs.map((ref, index) => (
        <SubTaskIndexRow
          key={subTaskKey(ref, index)}
          ref={ref}
          position={index + 1}
          sliceDocs={sliceDocs}
          onOpen={onOpen}
          onJump={onJump}
          testidPrefix={testidPrefix}
          docPathForRef={docPathForRef}
        />
      ))}
    </ul>
  );
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
        {orderedByCreation(sliceDocs)
          .map((doc) => {
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
                    {progress.total > 0 ? `${progress.done}/${progress.total} · ` : ""}
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
  kind: "code" | "memory";
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

function TaskReaderSections({
  doc,
  bodyState,
  onOpenNotes,
}: {
  doc: TaskDocNode;
  bodyState: TaskDocumentBodyState | undefined;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  return (
    <>
      {doc.objective ? (
        <Section title="Objective">
          <Markdown>{doc.objective}</Markdown>
        </Section>
      ) : null}
      {doc.requirements.length > 0 ? (
        <Section title="Requirements">
          <Bullets items={doc.requirements} />
        </Section>
      ) : null}
      {doc.design ? (
        <Section title="Design">
          <Markdown>{doc.design}</Markdown>
        </Section>
      ) : null}
      {doc.steps.length > 0 ? (
        <Section title="Implementation steps">
          <StepList steps={doc.steps} />
        </Section>
      ) : null}
      {doc.codeExamples.length > 0 ? (
        <Section title="Proposed code">
          {doc.codeExamples.map((example) => (
            <CodeExample key={example.id} example={example} />
          ))}
        </Section>
      ) : null}
      {doc.decisions.length > 0 ? (
        <Section title="Decision log">
          <DecisionPreview
            items={doc.decisions}
            repo={doc.repository}
            master={dirName(doc.docPath)}
            title={doc.title}
            onOpenNotes={onOpenNotes}
          />
        </Section>
      ) : null}
      {doc.openQuestions.length > 0 ? (
        <Section title="Open questions">
          <Bullets items={doc.openQuestions} />
        </Section>
      ) : null}
      {doc.sections.map((section) => (
        <Section key={`${section.kind}:${section.heading}`} title={section.heading}>
          {section.body ? <Markdown>{section.body}</Markdown> : null}
        </Section>
      ))}
      {/* References moved into TaskNotes so a reference naming an existing notes/ file
          renders as an openable link into the series-notes view (plain text otherwise). */}
      {bodyState !== "loading" ? (
        <TaskNotes
          repo={doc.repository}
          master={dirName(doc.docPath)}
          docPath={doc.docPath}
          references={doc.references}
          onOpenNotes={onOpenNotes}
        />
      ) : null}
    </>
  );
}

export function TaskReader({
  doc,
  bodyState,
  onOpenChangeSet,
  onOpenNotes,
}: {
  doc: TaskDocNode;
  bodyState: TaskDocumentBodyState | undefined;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  const progress = taskStepProgress(doc);
  const leafKey = qualifiedLeafKey(doc);
  const readerGrant = doc.kind === "subTask" ? (doc.directGrant ?? null) : null;
  return (
    <TaskRequirementBoundary doc={doc} onOpenNotes={onOpenNotes}>
    <div className={taskdoc} data-task-leaf-key={leafKey}>
      <div className={taskdocHead}>
        <span className={badge}>{doc.kind}</span>
        <span className={taskdocTitle}>{doc.title}</span>
        <span className={taskdocStatus}>{doc.status}</span>
        {readerGrant ? (
          <span className={badge}>Direct · {readerGrant.grantedBy}</span>
        ) : null}
        <ProgressFill completed={progress.done} total={progress.total} label="steps done" />
      </div>
      {doc.kind === "subTask" ? (
        <DirectGrantControl
          doc={doc}
          initialGrant={readerGrant}
          testidPrefix="taskdoc-direct"
          position={0}
        />
      ) : null}
      <TaskBodyNotice state={bodyState} />
      {bodyState !== "loading" ? (
        <DocChangeSetBar
          kind="leaf"
          repo={doc.repository}
          master={dirName(doc.docPath)}
          leaf={doc.id}
          onOpen={onOpenChangeSet}
        />
      ) : null}
      <TaskReaderSections doc={doc} bodyState={bodyState} onOpenNotes={onOpenNotes} />
    </div>
    </TaskRequirementBoundary>
  );
}

export function TaskBodyNotice({ state }: { state: TaskDocumentBodyState | undefined }) {
  if (state === "loading") {
    return (
      <p className="muted" role="status">
        Loading complete task document…
      </p>
    );
  }
  if (state === "unavailable") {
    return (
      <p className="muted">Full task document details are unavailable; showing the available summary.</p>
    );
  }
  return null;
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={taskdocSection}>
      <h3 className={taskdocH}>{title}</h3>
      {children}
    </section>
  );
}

export function Bullets({ items }: { items: string[] }) {
  return (
    <ul className={taskdocBullets}>
      {items.map((item) => (
        <li key={item}>
          <Markdown inline>{item}</Markdown>
        </li>
      ))}
    </ul>
  );
}

export function DiscardedSubTaskHistory({ items }: { items: DiscardedSubTaskNode[] }) {
  return (
    <ul className={taskdocBullets}>
      {items.map((item) => (
        <li key={`${item.number}:${item.proof.fingerprint}`}>
          <strong>{item.number}. {item.name}</strong>
          {` — ${item.reason} · ${item.discardedAt} · proof ${item.proof.fingerprint}`}
        </li>
      ))}
    </ul>
  );
}

export function StepList({ steps }: { steps: TaskStepNode[] }) {
  return (
    <ol className={stepsList}>
      {steps.map((s) => (
        <li key={s.id} className={stepRow}>
          <span className={cx(stepMarkBase, STEP_MARK[s.status] ?? "")} aria-hidden="true" />
          <span className={STEP_TITLE[s.status] ?? ""}>
            {labelWithId(s.id, s.title)}
            {s.disposition ? <SkippedDisposition reason={s.disposition.reason} /> : null}
          </span>
          {s.substeps.length > 0 ? (
            <ul className={substeps}>
              {s.substeps.map((sub) => (
                <li key={sub.id} className={SUBSTEP[sub.status] ?? ""}>
                  {labelWithId(sub.id, sub.title)}
                  {sub.disposition ? <SkippedDisposition reason={sub.disposition.reason} /> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

export function SkippedDisposition({ reason }: { reason: string }) {
  return (
    <span className={skippedDisposition}>
      <span className={skippedWord}>SKIPPED</span> — {reason}
    </span>
  );
}

export function CodeExample({ example }: { example: TaskCodeExampleNode }) {
  return (
    <div className={taskdocCode}>
      <div className={taskdocCodeHead}>{labelWithId(example.id, example.title)}</div>
      <div className={taskdocCodeMeta}>covers: {example.distinctChange}</div>
      <div className={taskdocCodeMeta}>why: {example.why}</div>
      {example.snippet ? <pre className={taskdocSnippet}>{example.snippet}</pre> : null}
    </div>
  );
}

export function DecisionList({ items }: { items: TaskDecisionNode[] }) {
  return (
    <ul className={taskdocDecisions}>
      {items.map((item) => (
        <li key={`${item.at}:${item.decision}`}>
          <div className={taskdocDecision}>
            <Markdown inline>{item.decision}</Markdown>
          </div>
          <div className={taskdocDecisionMeta}>
            {item.at} — <Markdown inline>{item.rationale}</Markdown>
          </div>
        </li>
      ))}
    </ul>
  );
}

// The decision log front page (MIK-R126 rule 6 under the lifecycle-records ruling): the
// five newest authored decisions, newest first. Product-written records — marked by the
// server from the writers' own constants — count toward nothing and are not drawn here.
// The link opens the whole log on a page of its own; it appears whenever at least one
// entry is omitted from this page and names both counts where they differ. No link when
// every entry is visible.
export function DecisionPreview({
  items,
  repo,
  master,
  title,
  onOpenNotes,
}: {
  items: TaskDecisionNode[];
  repo: string;
  master: string;
  title: string;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  const authored = items.filter((item) => !item.product_record);
  const newest = orderDecisionsNewestFirst(authored).slice(0, 5);
  const omitted = items.length - newest.length;
  const linkLabel =
    authored.length !== items.length
      ? `all ${items.length} entries, ${authored.length} decision${authored.length === 1 ? "" : "s"}`
      : `all ${items.length} decision${items.length === 1 ? "" : "s"}`;
  return (
    <>
      <DecisionList items={newest} />
      {omitted > 0 ? (
        <button
          type="button"
          className={crossButton}
          onClick={() => onOpenNotes?.({ kind: "decisions", repo, master, title, decisions: items })}
          data-testid="decision-log-all-link"
          title="open the whole decision log"
        >
          <span>→ {linkLabel}</span>
        </button>
      ) : null}
    </>
  );
}
