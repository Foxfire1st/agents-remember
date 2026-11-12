// The selected task entry, its document reader, and its worktree/checkout facts.
// state.ts owns selection; taskDocPanels.tsx composes the page.
import { memo } from 'react';

import type { ChangeSetTarget } from '../changeset/ChangeSetViewer';
import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../../data/taskArtifacts';
import { EmptyDetailPanel, TaskDocumentPanel } from './taskDocPanels';
import { useDetailPanelState, type ViewedTaskContext } from './state';

function DetailPanelImpl({
  selectedId,
  onOpenLifecycle,
  onOpenChangeSet,
  onOpenNotes,
  onViewTask,
  active = true,
}: {
  selectedId: string | null;
  active?: boolean;
  onOpenLifecycle?: (id: string) => void;
  // Open the Change-Set Viewer takeover: an enclosure scope, a series master, or a leaf view.
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  // Report the canonical task document actually shown. The Operations chat projection resolves
  // sprint/master/leaf roles from this document; a leaf key remains supplementary task context.
  onViewTask?: (context: ViewedTaskContext | undefined) => void;
}) {
  const state = useDetailPanelState({
    selectedId,
    onOpenLifecycle,
    onViewTask,
  });

  if (!state.entry) return <EmptyDetailPanel />;
  return (
    <TaskDocumentPanel state={state} active={active} onOpenChangeSet={onOpenChangeSet} onOpenNotes={onOpenNotes} />
  );
}

// Memoized (tab-switch CPU): a keep-alive cockpit layer — the shell re-renders on every
// view switch with unchanged props, and the memo gate skips this whole subtree then; the panel's
// own store subscriptions still drive its updates.
export const DetailPanel = memo(DetailPanelImpl);
