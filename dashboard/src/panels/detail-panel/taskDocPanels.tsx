import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../../data/taskArtifacts';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { Panel } from '../../grammar/Panel';
import { EmptyStateBackdrop } from '../EmptyStateBackdrop';
import type { ChangeSetTarget } from '../changeset/ChangeSetViewer';
import { TaskStatusWord } from './pageParts';
import type { useDetailPanelState } from './state';
import { crumb,sizing,where } from './styles';
import { TaskReader } from './taskReader';
import { WorktreeSpine } from './worktreeSpine';

type DetailState = ReturnType<typeof useDetailPanelState>;
export function TaskDocumentPanel({
  state,
  onOpenChangeSet,
  onOpenNotes,
  active = true,
}: {
  state: DetailState;
  active?: boolean;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  const { entry, document, parent, jump } = state;
  if (!entry) return null;
  const head = parent ? (
    <button
      type="button"
      className={crumb}
      onClick={() => jump(taskEntrySelectionKey(parent.key))}
      data-testid="master-parent-link"
    >
      ↑ {parent.title}
    </button>
  ) : undefined;
  return (
    <Panel testid="detail-panel" head={head ?? false} className={sizing}>
      <div className={where}>task document{entry.reference ? ` · ${entry.reference.repository}` : ''}</div>
      {document && entry.reference ? (
        <TaskReader
          active={active}
          entry={entry}
          doc={document}
          bodyState={state.taskDocumentBodyState}
          tasks={state.tasks}
          onJump={jump}
          onOpenChangeSet={onOpenChangeSet}
          onOpenNotes={onOpenNotes}
        />
      ) : (
        <div data-testid="task-not-authored">
          <h2>
            {entry.id} · {entry.title}
          </h2>
          <TaskStatusWord task={entry} testid="doc-status" active={active} />
          <p className="muted">This task document is not authored yet.</p>
        </div>
      )}
      {entry.worktrees.map((enclosure) => (
        <WorktreeSpine
          key={enclosure.enclosure}
          state={state}
          enclosure={enclosure}
          groupName={enclosure.worktreeGroup}
        />
      ))}
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
