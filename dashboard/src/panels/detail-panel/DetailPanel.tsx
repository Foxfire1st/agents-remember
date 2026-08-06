// The DetailPanel: the selected lifecycle's detail surface — phase stepper, the canonical Gate
// Respond surface (durable gates only), the task-document reader family, the lifecycle → worktree →
// provider spine, and tokens. The reader family lives in taskReader.tsx, doc selection in
// model.ts, and the change-set bar in changeSetBar.tsx; this file owns the
// container and its selection state.
import { memo, useEffect, useState } from "react";

import { useDashboard } from "../../data/store";
import {
  findLifecycleEnclosure,
  groupEnclosuresByLifecycle,
  parseTaskSelection,
  qualifiedLeafKey,
  taskDocsForLifecycle,
  taskLabel,
} from "../../data/taskIdentity";
import { parentTaskLinkForDoc } from "../../data/taskHierarchy";
import { useTaskDocumentBody } from "../../data/useTaskDocumentBody";
import { Panel } from "../../grammar/Panel";
import { TokenGauge } from "../../grammar/TokenGauge";
import type { Phase } from "../../types/projection";
import { EmptyStateBackdrop } from "../EmptyStateBackdrop";
import { GateResponder } from "../GateResponder";
import { ChangeSetButton } from "./changeSetBar";
import {
  displayedLeafDoc,
  displayedReaderDoc,
  masterDocWithSeriesTokens,
  seriesAsMasterDoc,
  seriesSliceDocs,
  sliceForSlug,
} from "./model";
import {
  changeSetBar,
  crumb,
  label,
  lanes,
  sizing,
  spine,
  spineHead,
  step,
  stepper,
  tokensRow,
  where,
} from "./styles";
import {
  MasterOverview,
  SpineLane,
  TaskContent,
  TaskReader,
} from "./taskReader";
import type { ChangeSetTarget } from "../changeset/ChangeSetViewer";
import type { NotesReaderTarget } from "../notes-reader/NotesReaderViewer";
// the current as done — mc2's Request→Close mini-map.
const PHASES: Phase[] = [
  "request",
  "trust-checkpoint",
  "reframe-research",
  "decide",
  "build",
  "close",
];


function DetailPanelImpl({
  selectedId,
  onOpenLifecycle,
  onOpenChangeSet,
  onOpenNotes,
  onViewLeaf,
}: {
  selectedId: string | null;
  onOpenLifecycle?: (id: string) => void;
  // Open the Change-Set Viewer takeover: an enclosure scope, a series master, or a leaf view.
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  // Report the QUALIFIED LEAF ID of the leaf the panel is actually SHOWING — a drilled sub-task or a
  // directly-opened leaf doc — so the rail chat + "attach to leaf" key by that leaf, not the master.
  // `undefined` while only a master/series overview (or the empty state) is shown.
  onViewLeaf?: (leafKey: string | undefined) => void;
}) {
  const jump = onOpenLifecycle ?? (() => {});
  const lifecycles = useDashboard((s) => s.lifecycles);
  const analytics = useDashboard((s) => s.analytics);
  const enclosures = useDashboard((s) => s.enclosures);
  const activeWorktreeGroups = useDashboard((s) => s.activeWorktreeGroups);
  const providers = useDashboard((s) => s.providers);
  // Drill state lives here (not in TaskContent) so the back control can sit in the sticky panel head.
  const [openSlug, setOpenSlug] = useState<string | null>(null);
  useEffect(() => setOpenSlug(null), [selectedId]); // switching lifecycles closes any open sub-task
  const allDocs = analytics?.taskDocuments ?? [];
  const selection = parseTaskSelection(selectedId, lifecycles, analytics);
  const selectedTaskDoc =
    selection?.kind === "taskdoc"
      ? allDocs.find((doc) => doc.docPath === selection.docPath)
      : undefined;
  const lifecycleId =
    selection?.kind === "lifecycle" ? selection.lifecycleId : selectedTaskDoc?.lifecycleId;
  const lifecycle = lifecycleId ? lifecycles[lifecycleId] : undefined;
  const enclosuresByLifecycle = groupEnclosuresByLifecycle(Object.values(enclosures));
  const selectedEnclosure = lifecycle
    ? findLifecycleEnclosure(lifecycle, enclosures, enclosuresByLifecycle)
    : undefined;
  const directDocs = lifecycle
    ? selectedTaskDoc?.lifecycleId === lifecycle.id
      ? [selectedTaskDoc]
      : allDocs.filter((doc) => doc.lifecycleId === lifecycle.id)
    : [];
  const selectedIsRootTask =
    selection?.kind === "lifecycle" &&
    Boolean(lifecycle && selectedEnclosure) &&
    (lifecycle?.id === selectedEnclosure?.taskId || lifecycle?.id === selectedEnclosure?.taskName);
  const selectedSeries = selection
    ? analytics?.series.find(
        (item) =>
          (selection.kind === "series" && item.seriesId === selection.seriesId) ||
          (selectedIsRootTask && item.seriesId === selectedEnclosure?.taskName),
      )
    : undefined;
  const bodyTargetDoc = displayedReaderDoc({
    allDocs,
    selectedTaskDoc,
    lifecycle,
    selectedSeries,
    openSlug,
  });
  const { documentFor: fullTaskDoc, state: taskDocumentBodyState } =
    useTaskDocumentBody(bodyTargetDoc);

  // The leaf the panel is actually SHOWING (a drilled sub-task or a directly-opened leaf doc), mirroring
  // the render branches below — a master/series overview shows no single leaf. Reported up so the rail
  // chat + "attach to leaf" key by this leaf, never the master.
  const viewedLeafDoc = displayedLeafDoc({
    selection,
    allDocs,
    selectedTaskDoc,
    lifecycle,
    selectedSeries,
    openSlug,
  });
  const viewedLeafKey =
    viewedLeafDoc && viewedLeafDoc.kind !== "master" ? qualifiedLeafKey(viewedLeafDoc) : undefined;
  // `onViewLeaf` is a stable setter from CockpitShell; re-report only when the resolved leaf changes.
  useEffect(() => {
    onViewLeaf?.(viewedLeafKey);
  }, [viewedLeafKey, onViewLeaf]);

  if (selectedTaskDoc && !lifecycle) {
    const sliceDocs = seriesSliceDocs(allDocs, selectedTaskDoc.docPath);
    const openDoc = openSlug ? sliceForSlug(sliceDocs, openSlug) : undefined;
    const parentLink = parentTaskLinkForDoc(selectedTaskDoc, allDocs, analytics?.series ?? []);
    const head = (
      <>
        <h2>{selectedTaskDoc.title}</h2>
        {openDoc ? (
          <button
            type="button"
            className={crumb}
            onClick={() => setOpenSlug(null)}
            data-testid="series-breadcrumb"
          >
            ← {selectedTaskDoc.title}
          </button>
        ) : parentLink ? (
          <button
            type="button"
            className={crumb}
            onClick={() => jump(parentLink.targetKey)}
            data-testid="master-parent-link"
          >
            ↑ {parentLink.title}
          </button>
        ) : null}
      </>
    );

    return (
      <Panel testid="detail-panel" head={head} className={sizing}>
        <div className={where}>task document · {selectedTaskDoc.repository}</div>
        {selectedTaskDoc.kind === "master" ? (
          openDoc ? (
            <TaskReader
              doc={fullTaskDoc(openDoc)}
              bodyState={taskDocumentBodyState}
              onOpenChangeSet={onOpenChangeSet}
              onOpenNotes={onOpenNotes}
            />
          ) : (
            <MasterOverview
              doc={masterDocWithSeriesTokens(fullTaskDoc(selectedTaskDoc), analytics?.series ?? [])}
              bodyState={taskDocumentBodyState}
              sliceDocs={sliceDocs}
              onOpen={setOpenSlug}
              onJump={jump}
              onOpenChangeSet={onOpenChangeSet}
              onOpenNotes={onOpenNotes}
            />
          )
        ) : (
          <TaskReader
            doc={fullTaskDoc(selectedTaskDoc)}
            bodyState={taskDocumentBodyState}
            onOpenChangeSet={onOpenChangeSet}
            onOpenNotes={onOpenNotes}
          />
        )}
      </Panel>
    );
  }

  if (!lifecycle && !selectedSeries) {
    return (
      <Panel testid="detail-panel" title="Detail" className={sizing} fill>
        {/* Bumped to 0.18 (from the shared 0.14 default) for a touch more presence, matching the
            File/Diff viewer's siege-tank backdrop. */}
        <EmptyStateBackdrop src="/assets/sc2-battlecruiser-boomerang.mp4" opacity={0.18}>
          Select a task to inspect its phase, gate, and tokens.
        </EmptyStateBackdrop>
      </Panel>
    );
  }

  if (!lifecycle && selectedSeries) {
    const selectedSeriesTaskDoc = allDocs.find((doc) => doc.docPath === selectedSeries.docPath);
    const seriesDoc = selectedSeriesTaskDoc
      ? masterDocWithSeriesTokens(fullTaskDoc(selectedSeriesTaskDoc), analytics?.series ?? [])
      : seriesAsMasterDoc(selectedSeries);
    const seriesSlices = seriesSliceDocs(allDocs, selectedSeries.docPath);
    const openDoc = openSlug ? sliceForSlug(seriesSlices, openSlug) : undefined;
    const head = (
      <>
        <h2>{selectedSeries.title}</h2>
        {openDoc ? (
          <button
            type="button"
            className={crumb}
            onClick={() => setOpenSlug(null)}
            data-testid="series-breadcrumb"
          >
            ← {selectedSeries.title}
          </button>
        ) : null}
      </>
    );

    return (
      <Panel testid="detail-panel" head={head} className={sizing}>
        <div className={where}>series master · {selectedSeries.repository}</div>
        {openDoc ? (
          <TaskReader
            doc={fullTaskDoc(openDoc)}
            bodyState={taskDocumentBodyState}
            onOpenChangeSet={onOpenChangeSet}
            onOpenNotes={onOpenNotes}
          />
        ) : (
          <MasterOverview
            doc={seriesDoc}
            bodyState={taskDocumentBodyState}
            sliceDocs={seriesSlices}
            onOpen={setOpenSlug}
            onJump={jump}
            onOpenChangeSet={onOpenChangeSet}
            onOpenNotes={onOpenNotes}
          />
        )}
      </Panel>
    );
  }

  const activeLifecycle = lifecycle;
  if (!activeLifecycle) return null;

  const currentIdx = PHASES.indexOf(activeLifecycle.phase);
  const enclosure = selectedEnclosure;
  const docs =
    selectedTaskDoc?.lifecycleId === activeLifecycle.id
      ? [selectedTaskDoc]
      : taskDocsForLifecycle(activeLifecycle, allDocs);
  const title = taskLabel(activeLifecycle, directDocs, enclosure);
  const groupName = enclosure ? (enclosure.worktreeGroup.split("/").filter(Boolean).pop() ?? "") : "";
  const engines = groupName
    ? Object.values(providers).filter((p) => p.scope === "worktree" && p.worktreeGroup === groupName)
    : [];

  // Master / slices split + the drilled-into doc. The sticky head carries the title plus the back
  // ("← series") or parent ("↑ parent series") up-link, so navigation stays put while the body scrolls.
  const master = docs.find((doc) => doc.kind === "master");
  const slices = master ? seriesSliceDocs(allDocs, master.docPath) : docs.filter((doc) => doc.kind !== "master");
  const selectedSeriesTaskDoc = selectedSeries
    ? allDocs.find((doc) => doc.docPath === selectedSeries.docPath)
    : undefined;
  const seriesDoc = selectedSeries
    ? selectedSeriesTaskDoc
      ? masterDocWithSeriesTokens(fullTaskDoc(selectedSeriesTaskDoc), analytics?.series ?? [])
      : seriesAsMasterDoc(selectedSeries)
    : undefined;
  const seriesSlices = selectedSeries ? seriesSliceDocs(allDocs, selectedSeries.docPath) : [];
  const contentSlices = seriesDoc ? seriesSlices : slices;
  const openDoc = openSlug ? sliceForSlug(contentSlices, openSlug) : undefined;
  const heading = selectedSeries?.title ?? title;
  const parentLink =
    !selectedSeries && !master && docs.length === 1
      ? parentTaskLinkForDoc(docs[0], allDocs, analytics?.series ?? [])
      : undefined;
  const head = (
    <>
      <h2>{heading}</h2>
      {openDoc ? (
        <button
          type="button"
          className={crumb}
          onClick={() => setOpenSlug(null)}
          data-testid="series-breadcrumb"
        >
          ← {selectedSeries?.title ?? (master ? master.title : "series")}
        </button>
      ) : !selectedSeries && master?.masterLifecycleId ? (
        <button
          type="button"
          className={crumb}
          onClick={() => jump(master.masterLifecycleId as string)}
          data-testid="master-parent-link"
        >
          ↑ {master.masterLifecycleId}
        </button>
      ) : parentLink ? (
        <button
          type="button"
          className={crumb}
          onClick={() => jump(parentLink.targetKey)}
          data-testid="master-parent-link"
        >
          ↑ {parentLink.title}
        </button>
      ) : null}
    </>
  );

  return (
    <Panel testid="detail-panel" head={head} className={sizing}>
      <div className={where}>
        {activeLifecycle.fleeting
          ? "fleeting · no worktree"
          : `persistent worktree · ${activeLifecycle.repoId ?? "—"}`}
        {activeLifecycle.inferred ? " · inferred" : ""}
      </div>

      <ol className={stepper} aria-label="phase">
        {PHASES.map((phase, i) => (
          <li
            key={phase}
            className={step({ state: i < currentIdx ? "done" : i === currentIdx ? "current" : "todo" })}
          >
            {phase}
          </li>
        ))}
      </ol>

      {activeLifecycle.gate ? (
        <GateResponder
          lifecycleId={activeLifecycle.id}
          gateNode={activeLifecycle.gate}
          ask={activeLifecycle.ask}
          testId="gate-review"
        />
      ) : null}

      {openDoc ? (
        <TaskReader
          doc={fullTaskDoc(openDoc)}
          bodyState={taskDocumentBodyState}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
        />
      ) : seriesDoc ? (
        <MasterOverview
          doc={seriesDoc}
          bodyState={taskDocumentBodyState}
          sliceDocs={seriesSlices}
          onOpen={setOpenSlug}
          onJump={jump}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
        />
      ) : master ? (
        <MasterOverview
          doc={masterDocWithSeriesTokens(fullTaskDoc(master), analytics?.series ?? [])}
          bodyState={taskDocumentBodyState}
          sliceDocs={slices}
          onOpen={setOpenSlug}
          onJump={jump}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
        />
      ) : (
        <TaskContent
          docs={docs.map(fullTaskDoc)}
          bodyState={taskDocumentBodyState}
          onOpen={setOpenSlug}
          onJump={jump}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
        />
      )}

      {enclosure ? (
        <div className={spine}>
          <div className={spineHead}>worktree · {groupName || enclosure.repoName}</div>
          {onOpenChangeSet && taskDocumentBodyState !== "loading" ? (
            <div className={changeSetBar}>
              {activeWorktreeGroups.includes(groupName) ? (
                <ChangeSetButton
                  target={{ repo: enclosure.repoName, scope: groupName }}
                  label="change-set"
                  onOpen={onOpenChangeSet}
                />
              ) : null}
              {enclosure.taskName ? (
                <ChangeSetButton
                  target={{ repo: enclosure.repoName, master: enclosure.taskName }}
                  label="series"
                  onOpen={onOpenChangeSet}
                />
              ) : null}
            </div>
          ) : null}
          <div className={lanes}>
            <SpineLane
              kind="code"
              title="code → CGC"
              repo={enclosure.repoName}
              engines={engines.filter((engine) => engine.role === "code")}
            />
            <SpineLane
              kind="memory"
              title="memory → GrepAI"
              repo={`ar-${enclosure.repoName}`}
              engines={engines.filter((engine) => engine.role === "memory")}
            />
          </div>
        </div>
      ) : null}

      <div className={tokensRow}>
        <span className={label}>tokens</span>
        <TokenGauge series={activeLifecycle.tokenSeries} />
      </div>
    </Panel>
  );
}

// Memoized (tab-switch CPU): a keep-alive cockpit layer — the shell re-renders on every
// view switch with unchanged props, and the memo gate skips this whole subtree then; the panel's
// own store subscriptions still drive its updates.
export const DetailPanel = memo(DetailPanelImpl);
