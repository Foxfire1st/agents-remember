import { parentTaskLinkForDoc } from '../../data/taskHierarchy';
import { Panel } from '../../grammar/Panel';
import type { TaskDocNode } from '../../types/projection';
import { EmptyStateBackdrop } from '../EmptyStateBackdrop';
import type { ChangeSetTarget } from '../changeset/ChangeSetViewer';
import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../../data/taskArtifacts';
import { seriesAsMasterDoc, seriesSliceDocs, sliceForSlug } from './model';
import { docForm } from './model';
import type { useDetailPanelState } from './state';
import { crumb, sizing, where } from './styles';
import { MasterOverview, SprintOverview, TaskReader } from './taskReader';

type DetailState = ReturnType<typeof useDetailPanelState>;

interface PanelCallbacks {
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}

function TaskDocBody({
  state,
  selectedTaskDoc,
  openDoc,
  sliceDocs,
  onOpenChangeSet,
  onOpenNotes,
}: {
  state: DetailState;
  selectedTaskDoc: TaskDocNode;
  openDoc: TaskDocNode | undefined;
  sliceDocs: TaskDocNode[];
} & PanelCallbacks) {
  const { fullTaskDoc, taskDocumentBodyState, jump, setOpenSlug, docPathForRef } = state;
  const pool = { allDocs: state.allDocs, seriesList: state.analytics?.series ?? [] };
  if (selectedTaskDoc.kind === 'master' && openDoc) {
    return (
      <TaskReader
        doc={fullTaskDoc(openDoc)}
        bodyState={taskDocumentBodyState}
        pool={pool}
        onJump={jump}
        onOpenChangeSet={onOpenChangeSet}
        onOpenNotes={onOpenNotes}
      />
    );
  }
  if (selectedTaskDoc.kind === 'master') {
    if (docForm(selectedTaskDoc) === 'sprint') {
      return (
        <SprintOverview
          doc={fullTaskDoc(selectedTaskDoc)}
          bodyState={taskDocumentBodyState}
          pool={pool}
          onJump={jump}
          onOpen={setOpenSlug}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
          docPathForRef={docPathForRef}
        />
      );
    }
    return (
      <MasterOverview
        doc={fullTaskDoc(selectedTaskDoc)}
        bodyState={taskDocumentBodyState}
        sliceDocs={sliceDocs}
        pool={pool}
        onOpen={setOpenSlug}
        onJump={jump}
        onOpenChangeSet={onOpenChangeSet}
        onOpenNotes={onOpenNotes}
        docPathForRef={docPathForRef}
      />
    );
  }
  return (
    <TaskReader
      doc={fullTaskDoc(selectedTaskDoc)}
      bodyState={taskDocumentBodyState}
      pool={pool}
      onJump={jump}
      onOpenChangeSet={onOpenChangeSet}
      onOpenNotes={onOpenNotes}
    />
  );
}

export function TaskDocumentPanel({
  state,
  onOpenChangeSet,
  onOpenNotes,
}: {
  state: DetailState;
} & PanelCallbacks) {
  const { selectedTaskDoc, allDocs, analytics, openSlug, setOpenSlug, jump } = state;
  if (!selectedTaskDoc) return null;
  const seriesList = analytics?.series ?? [];
  const sliceDocs = seriesSliceDocs(allDocs, selectedTaskDoc.docPath);
  const openDoc = openSlug ? sliceForSlug(sliceDocs, openSlug) : undefined;
  const parentLink = parentTaskLinkForDoc(selectedTaskDoc, allDocs, seriesList);
  const head = (
    <>
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
      <TaskDocBody
        state={state}
        selectedTaskDoc={selectedTaskDoc}
        openDoc={openDoc}
        sliceDocs={sliceDocs}
        onOpenChangeSet={onOpenChangeSet}
        onOpenNotes={onOpenNotes}
      />
    </Panel>
  );
}

export function EmptyDetailPanel() {
  return (
    <Panel testid="detail-panel" title="Detail" className={sizing} fill>
      <EmptyStateBackdrop src="/assets/sc2-battlecruiser-boomerang.mp4" opacity={0.18}>
        Select a task to inspect its steps, review, requirements and reports.
      </EmptyStateBackdrop>
    </Panel>
  );
}

export function SeriesDetailPanel({
  state,
  onOpenChangeSet,
  onOpenNotes,
}: {
  state: DetailState;
} & PanelCallbacks) {
  const { selectedSeries, allDocs, openSlug, setOpenSlug, fullTaskDoc, taskDocumentBodyState } =
    state;
  if (!selectedSeries) return null;
  const pool = { allDocs, seriesList: state.analytics?.series ?? [] };
  const selectedSeriesTaskDoc = allDocs.find((doc) => doc.docPath === selectedSeries.docPath);
  const seriesDoc = selectedSeriesTaskDoc
    ? fullTaskDoc(selectedSeriesTaskDoc)
    : seriesAsMasterDoc(selectedSeries);
  const seriesSlices = seriesSliceDocs(allDocs, selectedSeries.docPath);
  const openDoc = openSlug ? sliceForSlug(seriesSlices, openSlug) : undefined;
  const head = (
    <>
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
          pool={pool}
          onJump={state.jump}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
        />
      ) : selectedSeriesTaskDoc && docForm(selectedSeriesTaskDoc) === 'sprint' ? (
        <SprintOverview
          doc={fullTaskDoc(selectedSeriesTaskDoc)}
          bodyState={taskDocumentBodyState}
          pool={pool}
          onOpen={setOpenSlug}
          onJump={state.jump}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
          docPathForRef={state.docPathForRef}
        />
      ) : (
        <MasterOverview
          doc={seriesDoc}
          bodyState={taskDocumentBodyState}
          sliceDocs={seriesSlices}
          pool={pool}
          onOpen={setOpenSlug}
          onJump={state.jump}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
          docPathForRef={state.docPathForRef}
        />
      )}
    </Panel>
  );
}
