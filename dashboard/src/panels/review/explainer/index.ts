/**
 * The Explanation view's public surface (MIK-R106 v2 rule 6).
 *
 * The review page mounts these; it owns the top line, the tree and the chat, and this module owns
 * the explainer's page and the things that must stay outside it. Everything here is exportable so
 * `ReviewSurface.tsx` can mount it without this module reaching into that file:
 *
 *   * `ExplanationSwitch`           the top line's Review | Explanation control, with the build and
 *                                   stale marks;
 *   * `ExplanationView`             the page in the reading column: the moved notice, the trusted
 *                                   stale list, the revision selector, the isolated frame(s), the
 *                                   basis legend and the claims;
 *   * `StalePartsList`             the trusted list alone, for a caller that draws it elsewhere;
 *   * `RevisionSelector`           the retained revisions, Compare and the removal control;
 *   * `BasisLegend`, `ClaimChip`, `ClaimList`  the MIK-R102 bases;
 *   * `MovedNotice`                the moved notice and the Update offer;
 *   * `PlannedReview`              the planned-mode page a `planning` leaf shows, with no change set
 *                                   and no action that starts anything (MIK-R189 rule 5);
 *   * `useExplainer`               one read of the state and the revisions, shared by the switch and
 *                                   the view so the page reads once.
 */

export { ExplanationSwitch, type ExplanationSwitchProps, type ExplanationViewTab } from './ExplanationSwitch';
export { ExplanationView, type ExplanationViewProps } from './ExplanationView';
export { StalePartsList, type StalePartsListProps } from './StalePartsList';
export { RevisionSelector, type RevisionSelectorProps, type ComparePair } from './RevisionSelector';
export { BasisLegend, ClaimChip, ClaimList, type ClaimListProps } from './BasisLegend';
export { MovedNotice, type MovedNoticeProps, type UpdateRun } from './MovedNotice';
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
  basisLabel,
  bindingLabel,
  clockOf,
  agreedRevision,
  isPinned,
  kindLabel,
  movedLine,
  phaseOf,
  removalOffered,
  removableRevisions,
  staleParts,
  updateOffered,
  TASK_TOTAL_REFUSAL,
  type ExplanationPhase,
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
  MovedNoticeData,
  RefusalInfo,
  SavedExplanation,
  SeatState,
  StalePart,
} from './explainerApi';
