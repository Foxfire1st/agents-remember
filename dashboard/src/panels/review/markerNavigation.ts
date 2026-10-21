// How the review page moves for a per-hunk intent marker (MIK-R34): following one selects its target
// the way the tree selects it, and the return puts the reading position back as it was.
//
// The target is the invariant's own review, at its member row of the named family (shared,
// before-only or removed memberships alike), or with no family at all (`No recorded family`, or the
// state the review gives an invariant whose membership is unknown). The return puts back the selected
// subject and the worklist view.
import type { FamilySelection } from './FamilyTree';
import type { MarkTarget } from './hunkMarkers';
import type { MarkerMoves } from './intentMarkerScope';
import type { ReviewNavigationState } from './ReviewNavigation';
import type { WorkspaceState } from './workspaceState';

type Subject = ReviewNavigationState['subject'];

// The page's own selection, as the marker moves need it: what is selected, and how to select.
export interface MarkerSelection {
  current: () => { subject: Subject; chosen: FamilySelection | null };
  select: (subject: Subject, context: FamilySelection | null) => void;
}

interface Position {
  subject: Subject;
  chosen: FamilySelection | null;
  worklist: boolean;
}

export function workspaceMarkerMoves(state: WorkspaceState, selection: MarkerSelection): MarkerMoves {
  return {
    capture: () => {
      const { subject, chosen } = selection.current();
      const position: Position = { subject, chosen, worklist: state.worklist };
      return () => restore(position, state, selection);
    },
    open: (target) => openTarget(target, selection),
  };
}

function openTarget(target: MarkTarget, selection: MarkerSelection): void {
  const context: FamilySelection | null = target.familyKey
    ? { familyId: target.familyKey, memberRevisionId: target.memberRevisionKey }
    : null;
  selection.select({ kind: 'invariant', id: target.invariantKey }, context);
}

function restore(position: Position, state: WorkspaceState, selection: MarkerSelection): void {
  selection.select(position.subject, position.chosen);
  // The return focuses the originating marker, not the tree's selection.
  state.focusSelection.current = null;
  // After the subject: choosing a subject leaves the worklist, and the return may be into it.
  state.setWorklist(position.worklist);
}
