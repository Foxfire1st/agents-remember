// The DetailPanel: the selected lifecycle's detail surface — phase stepper, the canonical Gate
// Respond surface (durable gates only), the task-document reader family, the lifecycle → worktree →
// provider spine, and tokens. The reader family lives in taskReader.tsx, doc selection in
// model.ts, selection state in state.ts, and the render families in taskDocPanels.tsx and
// lifecycleBody.tsx; this file owns the container and its dispatch.
import { memo } from "react";

import type { ChangeSetTarget } from "../changeset/ChangeSetViewer";
import type { NotesReaderTarget } from "../notes-reader/NotesReaderViewer";
import {
  EmptyDetailPanel,
  SeriesDetailPanel,
  TaskDocumentPanel,
} from "./taskDocPanels";
import { LifecycleDetailBody } from "./lifecycleBody";
import { useDetailPanelState } from "./state";

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
  const state = useDetailPanelState({
    selectedId,
    onOpenLifecycle,
    onViewLeaf,
  });

  if (state.selectedTaskDoc && !state.lifecycle) {
    return (
      <TaskDocumentPanel
        state={state}
        onOpenChangeSet={onOpenChangeSet}
        onOpenNotes={onOpenNotes}
      />
    );
  }
  if (!state.lifecycle && !state.selectedSeries) {
    return <EmptyDetailPanel />;
  }
  if (!state.lifecycle && state.selectedSeries) {
    return (
      <SeriesDetailPanel
        state={state}
        onOpenChangeSet={onOpenChangeSet}
        onOpenNotes={onOpenNotes}
      />
    );
  }
  if (!state.lifecycle) return null;
  return (
    <LifecycleDetailBody
      state={state}
      lifecycle={state.lifecycle}
      onOpenChangeSet={onOpenChangeSet}
      onOpenNotes={onOpenNotes}
    />
  );
}

// Memoized (tab-switch CPU): a keep-alive cockpit layer — the shell re-renders on every
// view switch with unchanged props, and the memo gate skips this whole subtree then; the panel's
// own store subscriptions still drive its updates.
export const DetailPanel = memo(DetailPanelImpl);
