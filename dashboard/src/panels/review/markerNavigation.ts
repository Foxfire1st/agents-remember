// How the review workspace moves for a per-hunk intent marker (MIK-R34): following one selects its
// target in the tree, and the return puts the reading position back as it was.
//
// The target is a tree position of the mounted workspace, selected the way the rail selects it: the
// invariant's own review, at its member row of the named family (shared, before-only or removed
// memberships alike), or with no family at all (`No recorded family`, or the state the review gives
// an invariant whose membership is unknown). The file the marker sat in is closed while the target is
// read; the return reopens it.
import type { FamilySelection } from './FamilyTree';
import type { MarkTarget } from './hunkMarkers';
import type { MarkerMoves } from './intentMarkerScope';
import type { ReviewNavigationState } from './ReviewNavigation';
import type { WorkspaceState } from './ReviewWorkspace';

interface Position {
  subject: ReviewNavigationState['subject'];
  chosen: FamilySelection | null;
  lane: WorkspaceState['lane'];
  openPath: WorkspaceState['openPath'];
  layout: WorkspaceState['layout'];
  fullFile: boolean;
}

export function workspaceMarkerMoves(
  state: WorkspaceState,
  navigation?: ReviewNavigationState,
): MarkerMoves {
  return {
    capture: () => {
      const position: Position = {
        subject: navigation?.subject,
        chosen: state.chosen,
        lane: state.lane,
        openPath: state.openPath,
        layout: state.layout,
        fullFile: state.fullFile,
      };
      return () => restore(position, state, navigation);
    },
    open: (target) => openTarget(target, state, navigation),
  };
}

function openTarget(
  target: MarkTarget,
  state: WorkspaceState,
  navigation?: ReviewNavigationState,
): void {
  const context: FamilySelection | undefined = target.familyKey
    ? { familyId: target.familyKey, memberRevisionId: target.memberRevisionKey }
    : undefined;
  state.setOpenPath(null);
  if (navigation) navigation.onSelect({ kind: 'invariant', id: target.invariantKey }, context);
  else state.setChosen(context ?? null);
}

function restore(
  position: Position,
  state: WorkspaceState,
  navigation?: ReviewNavigationState,
): void {
  if (navigation) {
    navigation.onSelect(position.subject, position.chosen ?? undefined);
    // The return focuses the originating marker, not the tree's selection.
    state.focusSelection.current = null;
  } else {
    state.setChosen(position.chosen);
  }
  // After the subject: choosing a subject leaves the lane, and the return may be into the lane.
  state.setLane(position.lane);
  state.setOpenPath(position.openPath);
  state.setLayout(position.layout);
  state.setFullFile(position.fullFile);
}
