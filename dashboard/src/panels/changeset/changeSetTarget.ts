// The Change-Set Viewer's target and the props its screen and helpers share. Kept in one
// small module so the screen (and the tab rule that imports it) do not grow a second copy.
import type { LeafMode, MasterNetPins } from '../../data/changeset';
import type { ReviewSelectorKind } from '../../data/review';
import type { ReviewChangeContext } from './reviewChangeSet';

export interface ChangeSetTarget {
  repo: string;
  scope?: string; // one active enclosure (full base->worktree diff)
  master?: string; // a series master (net base->selected result); also QUALIFIES a `leaf`
  leaf?: string; // a single leaf (committed/working), resolved by leaf-id; needs `master` + `mode`
  mode?: LeafMode; // committed = landed delta (base->code_commit), working = uncommitted delta (live)
  // The master net's generation pins: the exact recorded endpoints a listing published. Empty /
  // absent means the declared integrated result. Carried into the list request (a pinned list
  // reopens the recorded net after the branch advances) and -- via the list response -- into
  // each file expansion, so an opened entry stays bound to its generation (R03's idiom).
  generation?: MasterNetPins;
  comparison?: number;
  // The Intent Reviewer's own selector: the reviewed subject's recorded identity, when the server
  // offered one. Its PRESENCE is what marks this target as a review and the cockpit's takeover
  // dispatch is what reads it -- the change-set viewer is never mounted for one, so no change-set
  // request is made from a review. An EMPTY object is the task-context entry: the review is opened
  // from the task alone and lists the complete source inventory, which is what a task with no
  // recorded invariant still has.
  //
  // `historical` says which record that review is read from (ICR-R12): absent is the live candidate,
  // and true is the leaf's own recorded comparison -- the entry a closed leaf offers, where the
  // worktree is gone and the durable generation is the only comparison there is.
  review?: { selectorKind?: ReviewSelectorKind; selectorId?: string; historical?: boolean };
}

export type ViewerProps = ChangeSetTarget & {
  onBack: () => void;
  onOpenLeaf?: (target: ChangeSetTarget) => void;
  reviewContext?: ReviewChangeContext;
};

export type ActiveFile = { kind: 'code' | 'memory'; path: string; hasSidecar?: boolean };
