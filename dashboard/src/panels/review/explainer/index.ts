/**
 * The Explanation view's public surface (MIK-R106 v2 rule 6).
 *
 * The review page mounts these; it owns the top line, the tree and the chat, and this module owns
 * the explainer's page and the things that must stay outside it. Everything here is exportable so
 * `ReviewSurface.tsx` can mount it without this module reaching into that file:
 *
 *   * `ExplanationSwitch`           the top line's Review | Explanation control, with the build mark
 *                                   and the staleness badge -- the one place staleness is reported
 *                                   (F18): the count, the stale parts and their reasons, the newer
 *                                   comparison, and the refresh control that regenerates;
 *   * `ExplanationView`             the page in the reading column: the revision selector and the
 *                                   isolated frame(s), and no staleness;
 *   * `RevisionSelector`           the retained revisions, Compare and the removal control;
 *   * `PlannedReview`              the planned-mode page a `planning` leaf shows, with no change set
 *                                   and no action that starts anything (MIK-R189 rule 5);
 *   * `useExplainer`               one read of the state and the revisions, shared by the switch and
 *                                   the view so the page reads once.
 */

export {
  ExplanationSwitch,
  staleMarkOf,
  type ExplanationSwitchProps,
  type ExplanationViewTab,
} from './ExplanationSwitch';
export { ExplanationView, type ExplanationViewProps } from './ExplanationView';
export { RevisionSelector, type RevisionSelectorProps, type ComparePair } from './RevisionSelector';
export { RevisionFrame, type RevisionFrameProps } from './RevisionFrame';
export { PlannedReview, type PlannedReviewProps } from './planned/PlannedReview';
export {
  PLANNED_PARTS,
  PLANNED_SORTS,
  PLANNED_TOP_LINE,
  readPlannedPage,
  rowDetail,
  scopeCaption,
  sortRows,
  type PlannedContext,
  type PlannedFile,
  type PlannedPacket,
  type PlannedPage,
  type PlannedRow,
  type PlannedScopePath,
  type PlannedSort,
  type PlannedSortId,
} from './planned/plannedPage';
export { useExplainer, limitRefusal, type ExplainerRead, type UpdateOutcome } from './explainerRead';
export {
  bindingLabel,
  clockOf,
  agreedRevision,
  isPinned,
  kindLabel,
  movedLine,
  phaseOf,
  removalOffered,
  removableRevisions,
  staleBriefOf,
  staleParts,
  coldRegenerateItems,
  newerComparisonOf,
  TASK_TOTAL_REFUSAL,
  type ExplanationPhase,
  type StaleBrief,
} from './explainerModel';
export type {
  ClaimBasis,
  ClaimLink,
  ExplainerBinding,
  ExplainerRevision,
  ExplainerRevisions,
  ExplainerState,
  ExplainerTarget,
  ExplanationClaim,
  RefusalInfo,
  SavedExplanation,
  SeatState,
  StalePart,
} from './explainerApi';
