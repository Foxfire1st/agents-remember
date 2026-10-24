// The intent review page (MIK-R89 v2; MIK-R86 v2): layout B. A root that does not scroll; the top
// line (back, title, status chip, comparison selector, refresh); under it the tree and the reading
// column, both drawn from one change-set model (changeSetOrder.ts), the column the page scroller. The
// page opens with nothing selected and makes exactly the six opening reads (reading/useChangeSet.ts);
// a selection reads its subject only. Every page-level block stays mounted while reads are pending, and
// the selected subject's slot alone changes with its read (rule 9).
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ChangeSetViewer } from '../changeset/ChangeSetViewer';
import { ReviewHeader, shortLeafId } from './ReviewHeader';
import { recordLabelOf } from './ReviewScopeHeader';
import { matchingTreeRead } from './codeFacts';
import { resetCardCachesForTests, useReviewTrees } from '../../data/reviewTrees';
import { useRevalidateIntentEntry } from '../../data/intentEntryRevalidation';
import { useReviewHistory } from './useReviewHistory';
import {
  subscribeReviewExplainerMoved,
  useReviewMemoryWatch,
} from '../../data/reviewMemoryWatch';
import { askFirstRequest } from '../../explainer/request';
import type { ReviewChangeContext } from '../changeset/reviewChangeSet';
import { ReviewChangeSetNavigation, type OpenReviewChanges } from './changeSetNavigation';
import { RIGHT_RAIL_ID } from '../../cockpit/layout';
import type { ReviewChatControl } from '../../cockpit/reviewChat';
import { readReviewHashState } from '../../cockpit/reviewHash';
import { SidebarToggle, useSidebar } from '../../grammar/SidebarToggle';
import {
  useObservedComparison,
  useReviewNavigation,
  type ReviewNavigationState,
  type ReviewSubject,
} from './ReviewNavigation';

import type {
  ReviewCollectionPage,
  ReviewFailure,
  ReviewHistory,
  ReviewPagedCollection,
  ReviewPayload,
  ReviewSelectorKind,
} from '../../data/review';
import {
  REVIEW_WALKABLE_COLLECTIONS,
  carriedPage,
  continuationOf,
  intentOnlyRefusal,
  pageBounds,
} from '../../data/review';
import type { ReviewRefusal } from '../../data/review';
import type { useReviewLane } from '../../data/reviewLane';
import { type ReviewPageRequest, targetKeyOf, useReviewReadCycle } from './ReviewReadCycle';
import {
  comparisonGenerationOf,
  sameGeneration,
  type ComparisonGeneration,
  ReviewReadCache,
} from './ReviewReadCache';
import type { EvidenceDestination } from './canvas/canvasApi';
import { ExplanationSwitch, type ExplanationViewTab } from './explainer/ExplanationSwitch';
import {
  ExplanationView,
  ReadingJudgmentMounts,
  ReviewMovedBanner,
} from './explainer/ExplanationView';
import type { ClaimLink } from './explainer/explainerApi';
import { hasWork, staleParts } from './explainer/explainerModel';
import {
  resolveClaimSelection,
  resolveEvidenceSelection,
  useExplainer,
  usePlannedReview,
  type ClaimResolutionContext,
  type ExplainerRead,
} from './explainer/explainerRead';
import { PlannedReview } from './explainer/planned/PlannedReview';
import type { PlannedPage } from './explainer/planned/plannedPage';
import { ReviewRefresh, generationOf } from './ReviewRefresh';
import {
  type ReviewRead,
  ReviewOutcomeRegion,
  ReviewProblemBlock,
  problemOf,
  shownPayload,
} from './ReviewOutcome';
import { type ReadingStatus, useSelectionFocus, useWorkspaceState } from './ReviewWorkspace';
import { useBlockTraversal } from './changeTraversal';
import { catalogueForComparison } from '../../data/useReviewCatalogue';
import { IntentMarkerScope, markerInventory, useIntentMarkerScope } from './intentMarkerScope';
import { MarkerReturn } from './IntentMarkers';
import { LeafWorklistView } from './LeafKnowledgeChanges';
import { workspaceMarkerMoves } from './markerNavigation';
import type { ReviewSelection } from './reviewSelection';
import { LaneSection } from './reading/LaneSection';
import { crumbOf, OrphanSlot, PageNotes, PageTree, TopLineControls, useReadingValue, useTaskTitle, type PageProps } from './reading/pageParts';
import { ReadingColumn } from './reading/ReadingColumn';
import { ReviewGrid, reviewBody, reviewRoot, useBracketKeys } from './reading/ReviewLayout';
import { SummaryLine } from './reading/SummaryLine';
import { TechnicalDetails } from './reading/TechnicalDetails';
import { bumpLeafMoveGeneration, usePublishLeafJudgments } from './reading/useChangeSet';
import { useReadingLayout } from './reading/useReadingLayout';
import { useReviewPage, type PageReads } from './reading/useReviewPage';

export interface ReviewTarget {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: ReviewHistory;
}

const retryFor = (read: ReviewRead, reread: () => void) =>
  read.phase === 'failed' ? reread : undefined;

function insteadFor(
  read: ReviewRead,
  problem: ReviewFailure | null,
  instead: ReviewFailure | null,
  offer: (problem: ReviewFailure) => void,
): (() => void) | undefined {
  if (read.phase !== 'refused' || problem === null || instead !== null) return undefined;
  if (!intentOnlyRefusal(problem.code)) return undefined;
  return () => offer(problem);
}

function PageRefusalBlock({
  refusal,
  collection,
  onSelect,
}: {
  refusal: ReviewRefusal;
  collection: ReviewPagedCollection;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  return (
    <section data-testid="review-page-refusal" data-page-refusal-code={refusal.code}>
      <p style={{ margin: '0.3rem 0' }}>
        {collection} could not be served as a page — {refusal.code}: {refusal.detail}
      </p>
      {refusal.expected !== undefined ? (
        <p
          style={{ margin: '0.2rem 0', color: 'var(--muted)' }}
          data-testid="review-page-refusal-expected"
        >
          expected: {refusal.expected}
        </p>
      ) : null}
      {refusal.observed !== undefined ? (
        <p
          style={{ margin: '0.2rem 0', color: 'var(--muted)' }}
          data-testid="review-page-refusal-observed"
        >
          observed: {refusal.observed}
        </p>
      ) : null}
      <p style={{ margin: '0.2rem 0' }} data-testid="review-page-refusal-action">
        {refusal.next_action}
      </p>
      <button
        type="button"
        data-testid="review-page-refusal-first-page"
        onClick={() => onSelect({ of: collection })}
      >
        first page of {collection}
      </button>
    </section>
  );
}

function PagePicker({
  selection,
  onSelect,
}: {
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  return (
    <span style={{ display: 'inline-flex', gap: '0.4rem', alignItems: 'center' }}>
      <label style={{ color: 'var(--muted)' }} htmlFor="review-page-collection">
        page over
      </label>
      <select
        id="review-page-collection"
        data-testid="review-page-collection"
        value={selection?.of ?? ''}
        onChange={(event) =>
          onSelect(
            event.target.value === ''
              ? undefined
              : { of: event.target.value as ReviewPagedCollection },
          )
        }
      >
        <option value="">whole review</option>
        {REVIEW_WALKABLE_COLLECTIONS.map((collection) => (
          <option key={collection} value={collection} data-testid="review-page-option">
            {collection}
          </option>
        ))}
      </select>
    </span>
  );
}

function PageActions({
  next,
  page,
  selection,
  onSelect,
}: {
  next: { of: ReviewPagedCollection; continuation: string } | null;
  page: ReviewCollectionPage | null | undefined;
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  const paging = page !== null && page !== undefined && selection !== undefined;
  return (
    <>
      {next ? (
        <button
          type="button"
          data-testid="review-next-page"
          data-continuation={next.continuation}
          onClick={() => onSelect({ of: next.of, continuation: next.continuation })}
        >
          next page →
        </button>
      ) : null}
      {paging ? (
        <button
          type="button"
          data-testid="review-first-page"
          onClick={() => onSelect({ of: page.collection })}
        >
          first page
        </button>
      ) : null}
    </>
  );
}

function refusedPageOf(
  payload: ReviewPayload,
  selection: ReviewPageRequest | undefined,
): { refusal: ReviewRefusal; collection: ReviewPagedCollection } | null {
  if (selection === undefined || carriedPage(payload) !== null) return null;
  const refusal = payload.page_refusal;
  return refusal === undefined || refusal === null ? null : { refusal, collection: selection.of };
}

function PageBoundsLine({
  payload,
  page,
  refused,
}: {
  payload: ReviewPayload;
  page: ReviewCollectionPage | null | undefined;
  refused: boolean;
}) {
  if (refused) return null;
  if (page) {
    return (
      <p style={{ margin: '0.3rem 0' }} data-testid="review-page-bounds">
        {pageBounds(payload)}
      </p>
    );
  }
  return (
    <p style={{ margin: '0.3rem 0' }} data-testid="review-page-bounds">
      whole review — no bounded collection was paged, so there is no remainder to reach
    </p>
  );
}

function PageControls({
  payload,
  selection,
  onSelect,
}: {
  payload: ReviewPayload;
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  const page = carriedPage(payload);
  const next = continuationOf(payload);
  const reset = page?.reset ?? null;
  const refused = refusedPageOf(payload, selection);
  return (
    <section
      data-testid="review-page-controls"
      data-page-collection={page?.collection ?? ''}
      data-page-requested={selection?.of ?? ''}
      data-page-refused={refused === null ? 'false' : 'true'}
    >
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <PagePicker selection={selection} onSelect={onSelect} />
        <PageActions next={next} page={page} selection={selection} onSelect={onSelect} />
      </div>
      {refused === null ? null : (
        <PageRefusalBlock
          refusal={refused.refusal}
          collection={refused.collection}
          onSelect={onSelect}
        />
      )}
      <PageBoundsLine payload={payload} page={page} refused={refused !== null} />
      {reset ? (
        <p style={{ margin: '0.3rem 0' }} data-testid="review-page-reset">
          {reset.code}: {reset.detail} — {reset.next_action}
        </p>
      ) : null}
    </section>
  );
}

type OpenChangePage =
  | {
      kind: 'comparison';
      repo: string;
      master: string;
      leaf: string;
      generation: ComparisonGeneration;
      context: ReviewChangeContext;
    }
  | { kind: 'unavailable'; problem: ReviewFailure };

function sameLane(
  left: ReturnType<typeof useReviewLane>,
  right: ReturnType<typeof useReviewLane>,
): boolean {
  if (!left || !right) return left === right;
  if (left.phase !== right.phase) return false;
  if (left.phase === 'ready' && right.phase === 'ready') return left.value === right.value;
  if (left.phase === 'unavailable' && right.phase === 'unavailable')
    return JSON.stringify(left.problem) === JSON.stringify(right.problem);
  return true;
}
type CurrentChangeContext = {
  repo: string;
  master: string;
  leaf: string;
  comparison: number | undefined;
  payload: ReviewPayload | null;
  laneRead: ReturnType<typeof useReviewLane>;
  leafTrees: ReturnType<typeof useReviewTrees>;
};
function pageMatchesCurrent(
  state: Extract<OpenChangePage, { kind: 'comparison' }>,
  current: CurrentChangeContext,
): boolean {
  if (!current.payload) return false;
  return (
    state.repo === current.repo &&
    state.master === current.master &&
    state.leaf === current.leaf &&
    state.context.page.comparison === current.comparison &&
    sameGeneration(state.generation, comparisonGenerationOf(current.payload))
  );
}
function sameChangeRead(
  context: ReviewChangeContext,
  current: CurrentChangeContext,
  worklist: ReviewChangeContext['worklist'],
): boolean {
  return sameLane(context.lane, current.laneRead) && context.worklist === worklist;
}
function useHeldPageLane(
  state: OpenChangePage | null,
  setState: React.Dispatch<React.SetStateAction<OpenChangePage | null>>,
  current: CurrentChangeContext,
) {
  useEffect(() => {
    if (state?.kind !== 'comparison' || !current.payload) return;
    const matching = pageMatchesCurrent(state, current);
    const worklist = matchingTreeRead(current.leafTrees, current.comparison)?.worklist;
    if (matching && !sameChangeRead(state.context, current, worklist)) {
      setState({ ...state, context: { ...state.context, lane: current.laneRead, worklist } });
    } else if (!matching && state.context.lane?.phase === 'loading') {
      setState({
        ...state,
        context: {
          ...state.context,
          lane: {
            phase: 'unavailable',
            problem: {
              token: 'unavailable-history',
              code: 'comparison_context_changed',
              detail: `The reviewer moved before comparison ${state.context.page.comparison}'s lane answered. This page retains its recorded comparison, so its lane filters are unavailable.`,
              nextAction: 'Return to the intent reviewer to inspect its current comparison.',
            },
          },
        },
      });
    }
  }, [state, setState, current]);
}
export function useReviewChangePage(current: CurrentChangeContext) {
  const [changePage, setChangePage] = useState<OpenChangePage | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  useHeldPageLane(changePage, setChangePage, current);
  const openChanges: OpenReviewChanges = (filter, path, kind) => {
    opener.current = document.activeElement as HTMLElement | null;
    if (current.comparison === undefined || current.payload === null) {
      setChangePage({
        kind: 'unavailable',
        problem: {
          token: 'unavailable-history',
          code: 'comparison_not_recorded',
          detail:
            'This review has no recorded tree comparison for its source inventory. The change-set viewer cannot select that inventory without a recorded range.',
          offendingInput: `${current.repo}/${current.master}/${current.leaf}`,
          nextAction:
            'Open a recorded tree comparison, or ask the task owner to resolve this review’s range authority.',
        },
      });
      return;
    }
    setChangePage({
      kind: 'comparison',
      repo: current.repo,
      master: current.master,
      leaf: current.leaf,
      generation: comparisonGenerationOf(current.payload),
      context: {
        page: { comparison: current.comparison, filter, path, kind },
        lane: current.laneRead,
        inventory: current.payload.source.inventory,
        worklist: matchingTreeRead(current.leafTrees, current.comparison)?.worklist,
      },
    });
  };
  const closeChanges = () => {
    setChangePage(null);
    requestAnimationFrame(() => opener.current?.focus({ preventScroll: true }));
  };
  return { changePage, openChanges, closeChanges };
}
function UnavailableChangePage({
  problem,
  onBack,
}: {
  problem: ReviewFailure;
  onBack: () => void;
}) {
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        onBack();
      }
    };
    document.addEventListener('keydown', escape, true);
    return () => document.removeEventListener('keydown', escape, true);
  }, [onBack]);
  return (
    <section
      data-testid="review-changes-unavailable"
      data-kbzone="changeset"
      style={{ height: '100%', background: 'var(--bg)', padding: '1rem' }}
    >
      <button type="button" onClick={onBack}>
        ← Back to intent review
      </button>
      <h2>All changes unavailable</h2>
      <ReviewProblemBlock
        origin="refusal"
        subject="the complete changed-file inventory"
        problem={problem}
      />
    </section>
  );
}
function ReviewChangeOverlay({
  state,
  onBack,
}: {
  state: OpenChangePage | null;
  onBack: () => void;
}) {
  if (!state) return null;
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 100, minWidth: 0 }}>
      {state.kind === 'comparison' ? (
        <ChangeSetViewer
          repo={state.repo}
          master={state.master}
          leaf={state.leaf}
          comparison={state.context.page.comparison}
          reviewContext={state.context}
          onBack={onBack}
        />
      ) : (
        <UnavailableChangePage problem={state.problem} onBack={onBack} />
      )}
    </div>
  );
}

// The status of a subject whose review has not answered, bound to the question it asked: its key is
// the read cycle's target key, and its label names the requested subject (the catalogue's label when
// it has one). A failure or refusal carries the owner's block, labelled with that subject.
function readingStatusOf(
  read: ReviewRead,
  targetKey: string,
  subject: ReviewSubject | undefined,
  catalogue: ReviewNavigationState['catalogue'],
  problemBlock: (label: string) => React.ReactNode,
): ReadingStatus | null {
  if (read.phase === 'reviewed') return null;
  const entry = subject
    ? catalogue.entries?.find(
        (row) => row.selector_kind === subject.kind && row.selector_id === subject.id,
      )
    : undefined;
  const label = subject ? `${subject.kind} ${entry?.label ?? subject.id}` : 'all source changes';
  return {
    key: targetKey,
    subject: subject ? `${subject.kind}:${subject.id}` : 'task-context',
    label,
    problem: read.phase === 'loading' ? null : problemBlock(label),
  };
}

// A refresh shows the comparison as it reads now, not the tree walked before it (MIK-R39), and asks
// for the selected row to be brought into view once its answer is shown. A refresh whose read fails
// or is refused has no such answer: its request is dropped, so that a later answer for the subject
// on screen (a roster continuation, a page) does not move the rail. The reader's retry of that
// failed read is the refresh again and asks what it asked. The retry of any other failed read (a
// selection's, a page's) reads again and asks nothing more, as before.
function useRefreshReview(
  workspace: ReturnType<typeof useWorkspaceState>,
  read: ReviewRead,
  refresh: () => void,
): { refreshReview: () => void; retryReview: () => void } {
  const { focusSelection } = workspace;
  // The read on screen when the refresh was asked: a failure shown then is not the refresh's own.
  const asked = useRef<ReviewRead | null>(null);
  // The failed or refused read of a refresh, once its request is dropped.
  const failed = useRef<ReviewRead | null>(null);
  const settle = useCallback(
    (shown: ReviewRead) => {
      const unanswered = shown.phase === 'failed' || shown.phase === 'refused';
      if (unanswered && shown !== asked.current && focusSelection.current?.scrollOnly) {
        focusSelection.current = null;
        failed.current = shown;
      }
    },
    [focusSelection],
  );
  useEffect(() => settle(read), [read, settle]);
  const refreshReview = () => {
    workspace.beginSelection(false);
    focusSelection.current = {
      from: document.activeElement,
      after: workspace.treeIntent.id,
      scrollOnly: true,
    };
    asked.current = read;
    refresh();
  };
  const retryReview = () => {
    // A retry made in the turn that shows the failure comes before that turn's effects.
    settle(read);
    if (failed.current === read) refreshReview();
    else refresh();
  };
  return { refreshReview, retryReview };
}

// The comparison chosen in the selector, null for the latest (MIK-R89 v2 rule 10), and the
// navigation of that comparison: an older number's outline is read with `comparison=<n>`.
function useComparisonNavigation(target: ReviewTarget) {
  const [chosen, setChosen] = useState<number | null>(() => readReviewHashState().comparison);
  const navigation = useReviewNavigation({ ...target, comparison: chosen ?? undefined });
  return { navigation, chosen, setChosen };
}

// The surface's reads: the navigation (the catalogue and the subject the page reads), the read cycle
// of that subject, and the reading status the selected subject's slot shows. The page's selection is
// `useReviewPage`'s; a selection names the subject this reads.
function useSurface({
  repo,
  master,
  leaf,
  selectorKind: initialKind,
  selectorId: initialId,
  history: initialHistory,
}: ReviewTarget) {
  // Which record the surface reads (ICR-R12), as state the page can leave: the delivered link asks
  // for the page's own default again, which only clears the recorded frame (MIK-R195 F06).
  const { history, leaveHistory } = useReviewHistory(initialHistory);
  const { navigation, chosen, setChosen } = useComparisonNavigation({
    ...{ repo, master, leaf, selectorKind: initialKind, selectorId: initialId, history },
  });
  const selectorKind = navigation.subject?.kind;
  const selectorId = navigation.subject?.id;
  const [instead, setInstead] = useState<ReviewFailure | null>(null);
  const [page, setPage] = useState<ReviewPageRequest | undefined>(undefined);
  const workspace = useWorkspaceState();
  // One cache per mounted surface: reclaimed with it (see `ReviewReadCache`).
  const [cache] = useState(() => new ReviewReadCache());
  const cycle = useReviewReadCycle({ repo, master, leaf, selectorKind, selectorId, history, instead, selection: page, cache });
  const { read, retained, frame } = cycle;
  useReviewMemoryWatch({ repo, master, leaf, history }, cycle);
  const key = targetKeyOf(repo, master, leaf, instead, selectorKind, selectorId, page, history);
  const coherent = retained !== null && retained.key === key ? retained.payload : null;
  const shown = shownPayload(read, coherent);
  useObservedComparison(navigation.observeComparison, shown);
  const asked = instead === null ? navigation.subject : undefined;
  const problem = problemOf(read);
  const { refreshReview, retryReview } = useRefreshReview(workspace, read, cycle.refresh);
  // The offer to open the task context after a refusal: set by the page, which owns the selection.
  const openTaskContext = useRef<(failure: ReviewFailure) => void>(() => undefined);
  const problemBlock = (label: string) =>
    problem === null ? null : (
      <ReviewProblemBlock
        origin={read.phase === 'refused' ? 'refusal' : 'failure'}
        problem={problem}
        subject={label}
        onRetry={retryFor(read, retryReview)}
        onOpenTaskContext={insteadFor(read, problem, instead, (failure) => openTaskContext.current(failure))}
      />
    );
  const reading =
    frame === null || shown !== null ? null : readingStatusOf(read, key, asked, navigation.catalogue, problemBlock);
  // The catalogue of another comparison generation is not admitted beside the answer on screen
  // (INV-CZVMX3): its fields read as loading until the catalogue is read for this one.
  const answer = shown ?? frame;
  const bound = answer ? { ...navigation, catalogue: catalogueForComparison(navigation.catalogue, answer) } : navigation;
  return {
    ...{ repo, master, leaf, history, leaveHistory, navigation: bound, instead, setInstead, page, setPage, workspace },
    ...{ chosen, setChosen },
    ...{
      frame,
      coherent,
      shown,
      read,
      carried: cycle.carried,
      refreshReview,
      retryReview,
      memoryChanged: cycle.memoryChanged,
      problem,
      reading,
    },
    openTaskContext,
  };
}

type Surface = ReturnType<typeof useSurface>;

function initialSelection(target: ReviewTarget): ReviewSelection | null {
  return target.selectorKind && target.selectorId
    ? { kind: 'subject', subject: { kind: target.selectorKind, id: target.selectorId } }
    : null;
}

// The changed files of the comparison on screen: the task context's inventory for the latest one, and
// an older comparison #k's own lane read, which lists every path #k measured (rule 10). Until #k's
// list is read, nothing is classified at #k.
function comparisonFiles(frame: Surface['frame'], changeSet: ReturnType<typeof useReviewPage>['changeSet']): { changed: string[]; partial: boolean } {
  if (!changeSet.older) return frame ? markerInventory(frame.source.inventory) : { changed: [], partial: true };
  const lane = changeSet.lane;
  if (lane?.phase !== 'ready') return { changed: [], partial: true };
  return { changed: lane.value.paths.map((row) => row.path), partial: lane.value.state !== 'measured' || lane.value.unmeasured.length > 0 };
}

// The marker scope of the page's diffs (MIK-R34): the comparison on screen, its changed files, and how
// a followed marker selects its target and returns.
function usePageMarkers(surface: Surface, page: ReturnType<typeof useReviewPage>) {
  const { repo, master, leaf, workspace, navigation, frame } = surface;
  return useIntentMarkerScope({
    repo,
    master,
    leaf,
    comparison: page.changeSet.number,
    ...comparisonFiles(frame, page.changeSet),
    moves: workspaceMarkerMoves(workspace, {
      current: () => ({ subject: navigation.subject, chosen: workspace.chosen }),
      select: (subject, context) =>
        page.select(subject ? { kind: 'subject', subject } : null, { keepTree: false, context }),
    }),
  });
}

function readingState(reading: ReadingStatus | null): 'pending' | 'unavailable' | null {
  if (!reading) return null;
  return reading.problem === null ? 'pending' : 'unavailable';
}

function PageDetails({ props }: { props: PageProps & { reads: Surface } }) {
  const { reads, page } = props;
  const { shown, navigation } = reads;
  const cards = page.changeSet.cards;
  return (
    <TechnicalDetails
      repo={reads.repo}
      master={reads.master}
      leaf={reads.leaf}
      comparison={page.changeSet.comparison}
      latest={page.changeSet.resolved}
      delivered={page.changeSet.delivered}
      frame={reads.frame}
      shown={shown}
      status={readingState(reads.reading)}
      subject={navigation.subject}
      instead={reads.instead}
      entries={cards.state === 'ready' ? cards.entries : []}
      paging={shown ? <PageControls payload={shown} selection={reads.page} onSelect={reads.setPage} /> : null}
    />
  );
}

// The leaf worklist view (MIK-R86 rule 21), in the column in place of the change set when chosen.
function WorklistView({ props, open }: { props: PageProps; open: OpenReviewChanges }) {
  const { changeSet } = props.page;
  return (
    <LeafWorklistView
      read={changeSet.number === undefined ? { phase: 'not-converted' } : changeSet.leafTrees}
      reviewComparison={changeSet.number}
      onOpenChanges={() => open('all', undefined, 'memory')}
      onOpenPath={(path) => open('all', path)}
    />
  );
}

interface PageReadingProps {
  props: PageProps & { reads: Surface };
  tree: ReturnType<typeof useSidebar>;
  chat?: ReviewChatControl;
  open: OpenReviewChanges;
  viewTab: ExplanationViewTab;
  explainer: ExplainerRead;
  plannedPage: PlannedPage | null;
  comparisonInfo: { reference: string; number?: number; owner?: 'planned' | 'recorded' } | undefined;
  onExplain: () => void;
  onOpenBlock: (claim: ClaimLink) => void;
  onEvidence: (destination: EvidenceDestination) => void;
}

function readingReplacementOf({
  props,
  open,
  viewTab,
  explainer,
  plannedPage,
  comparisonInfo,
  onExplain,
  onOpenBlock,
  onEvidence,
}: PageReadingProps): React.ReactNode | undefined {
  const { reads } = props;
  if (viewTab === 'explanation') {
    return (
      <ExplanationView
        target={{ repository: reads.repo, master: reads.master, leaf: reads.leaf }}
        comparison={comparisonInfo}
        read={explainer}
        taskLabel={shortLeafId(reads.leaf)}
        onExplain={onExplain}
        onOpenBlock={onOpenBlock}
        onEvidence={onEvidence}
      />
    );
  }
  if (plannedPage) return <PlannedReview payload={plannedPage} />;
  if (reads.workspace.worklist) return <WorklistView props={props} open={open} />;
  return undefined;
}

function ReadingNotes({
  props,
  viewTab,
  explainer,
  plannedPage,
}: {
  props: PageProps & { reads: Surface };
  viewTab: ExplanationViewTab;
  explainer: ExplainerRead;
  plannedPage: PlannedPage | null;
}) {
  const { reads, page } = props;
  const read = reads.read;
  return (
    <>
      {viewTab === 'review' ? <ReviewMovedBanner read={explainer} /> : null}
      {plannedPage && viewTab === 'review' ? null : (
        <PageNotes
          page={page}
          frame={reads.frame}
          outcome={
            <ReviewOutcomeRegion
              read={read}
              instead={reads.instead}
              shown={reads.shown}
              lastCoherent={read.phase === 'failed' ? reads.coherent : null}
              onRetry={retryFor(read, reads.retryReview)}
              onOpenTaskContext={insteadFor(read, reads.problem, reads.instead, (failure) =>
                reads.openTaskContext.current(failure),
              )}
              readingInWorkspace={reads.reading !== null}
            />
          }
        />
      )}
    </>
  );
}

function PageReading(readingProps: PageReadingProps) {
  const { props, tree, chat, viewTab, explainer, plannedPage } = readingProps;
  const { reads, page } = props;
  const { changeSet } = page;
  const layout = useReadingLayout(page.scroller);
  const context = useReadingValue(props, layout);
  const replacement = readingReplacementOf(readingProps);
  const showJudgments = viewTab === 'review' && !plannedPage && !reads.workspace.worklist;

  return (
    <>
      <ReadingColumn
        scroller={page.scroller}
        context={context}
        model={changeSet.model}
        treeToggle={
          <SidebarToggle
            side="left"
            open={tree.open}
            onToggle={tree.toggle}
            name="Tree sidebar"
            controls="review-tree"
            keyHint="["
            testId="review-tree-toggle"
          />
        }
        chatToggle={
          chat ? (
            <SidebarToggle
              side="right"
              open={chat.open}
              onToggle={chat.toggle}
              name="Chat sidebar"
              controls={RIGHT_RAIL_ID}
              keyHint="]"
              testId="review-chat-toggle"
            />
          ) : null
        }
        crumbText={crumbOf(page, reads.navigation.catalogue.entries)}
        orphan={<OrphanSlot props={props} layout={layout} />}
        markerReturn={<MarkerReturn />}
        notes={
          <ReadingNotes
            props={props}
            viewTab={viewTab}
            explainer={explainer}
            plannedPage={plannedPage}
          />
        }
        summary={
          <SummaryLine
            model={changeSet.model}
            catalogue={reads.navigation.catalogue}
            summary={changeSet.summary}
            lane={changeSet.lane}
          />
        }
        replacement={replacement}
        lane={
          <LaneSection
            read={changeSet.lane}
            place={context.place}
            comparison={changeSet.comparison}
            layout={layout}
            collapse={context.collapse}
            select={{ selection: page.selection, onSelect: context.select, onFile: page.lanes.onFile }}
          />
        }
        canvas={null}
        details={<PageDetails props={props} />}
      />
      {showJudgments ? (
        <ReadingJudgmentMounts model={changeSet.model} scroller={page.scroller} />
      ) : null}
    </>
  );
}

function surfaceAttributes(surface: Surface, layout: { markers: boolean }) {
  const { repo, master, leaf, history, reading, shown } = surface;
  return {
    'data-comparison': shown?.comparison?.reference,
    'data-review-target': `${repo}/${master}/${leaf}`,
    'data-review-history': history ?? 'live',
    'data-review-pending': reading && !reading.problem ? reading.subject : undefined,
    'data-review-unavailable': reading?.problem ? reading.subject : undefined,
    'data-marker-return': layout.markers ? 'open' : undefined,
  };
}

function resolveComparisonInfo(
  plannedPage: PlannedPage | null,
  payloadRef: string | undefined,
  number: number | undefined,
  master: string,
  leaf: string,
): { reference: string; number?: number; owner?: 'planned' | 'recorded' } | undefined {
  if (plannedPage) {
    return {
      reference: plannedPage.binding.reference,
      number: plannedPage.binding.number,
      owner: 'planned',
    };
  }
  const ref =
    payloadRef ?? (number !== undefined ? `review-comparison:${master}/${leaf}/${number}` : undefined);
  if (!ref && number === undefined) return undefined;
  return { reference: ref ?? '', number, owner: 'recorded' };
}

function useExplainerMovedRefresh(surface: Surface, explainer: ExplainerRead) {
  const { repo, master, leaf, history } = surface;
  const revalidate = useRevalidateIntentEntry();
  const explainerStateRef = useRef(explainer.state);
  explainerStateRef.current = explainer.state;
  const surfaceMemoryChangedRef = useRef(surface.memoryChanged);
  surfaceMemoryChangedRef.current = surface.memoryChanged;
  const revalidateRef = useRef(revalidate);
  revalidateRef.current = revalidate;

  useEffect(() => {
    if (history === 'recorded') return undefined;
    return subscribeReviewExplainerMoved({ repo, master, leaf }, (event) => {
      if (event.cleared || event.kind === 'cleared') return;
      const workExists = hasWork(explainerStateRef.current) || event.kind === 'master-moved';
      if (!workExists || event.updateOffered === false) {
        resetCardCachesForTests();
        bumpLeafMoveGeneration(repo, master, leaf);
        surfaceMemoryChangedRef.current();
        revalidateRef.current();
      }
    });
  }, [repo, master, leaf, history]);
}

function useResolutionContext(
  surface: Surface,
  page: ReturnType<typeof useReviewPage>,
  payload: ReviewPayload | null,
  comparisonInfo: { reference: string; number?: number; owner?: 'planned' | 'recorded' } | undefined,
): ClaimResolutionContext {
  return useMemo<ClaimResolutionContext>(() => {
    const laneHunks = [...page.lanes.files.values()].flatMap((f) => f.hunks);
    return {
      model: page.changeSet.model,
      cards: page.changeSet.cards,
      comparison: page.changeSet.comparison,
      comparisonNumber: page.changeSet.number,
      comparisonReference: payload?.comparison?.reference ?? comparisonInfo?.reference ?? null,
      laneHunks,
      catalogue: surface.navigation.catalogue.entries,
    };
  }, [
    page.changeSet.model,
    page.changeSet.cards,
    page.changeSet.comparison,
    page.changeSet.number,
    page.lanes.files,
    payload?.comparison?.reference,
    comparisonInfo?.reference,
    surface.navigation.catalogue.entries,
  ]);
}

function isPlannedReadEnabled(surface: Surface, hasInitialSubject: boolean): boolean {
  return (
    surface.history !== 'recorded' &&
    !hasInitialSubject &&
    surface.read.phase === 'reviewed'
  );
}

function isExplainerReadEnabled(
  viewTab: ExplanationViewTab,
  comparisonInfo: { reference: string; number?: number; owner?: 'planned' | 'recorded' } | undefined,
  surface: Surface,
  hasInitialSubject: boolean,
): boolean {
  if (viewTab === 'explanation' || comparisonInfo !== undefined) return true;
  return !hasInitialSubject && surface.read.phase === 'reviewed';
}

function useSurfaceExplainer(
  surface: Surface,
  page: ReturnType<typeof useReviewPage>,
  chat: ReviewChatControl | undefined,
  viewTab: ExplanationViewTab,
  setViewTab: (tab: ExplanationViewTab) => void,
  hasInitialSubject: boolean,
) {
  const { repo, master, leaf } = surface;
  const payload = surface.shown ?? surface.frame;
  const plannedPage = usePlannedReview(
    { repository: repo, master, leaf },
    isPlannedReadEnabled(surface, hasInitialSubject),
  );
  const comparisonInfo = useMemo(
    () =>
      resolveComparisonInfo(
        plannedPage,
        payload?.comparison?.reference,
        page.changeSet.number,
        master,
        leaf,
      ),
    [plannedPage, payload?.comparison?.reference, page.changeSet.number, master, leaf],
  );
  const explainer = useExplainer(
    { repository: repo, master, leaf },
    comparisonInfo,
    isExplainerReadEnabled(viewTab, comparisonInfo, surface, hasInitialSubject),
  );
  usePublishLeafJudgments(repo, master, leaf, explainer.state?.categories, explainer.state?.grades);
  useExplainerMovedRefresh(surface, explainer);
  const resolutionContext = useResolutionContext(surface, page, payload, comparisonInfo);

  const select = page.select;
  const onExplain = useCallback(() => {
    askFirstRequest({ repository: repo, master, leaf });
    if (chat && !chat.open) chat.toggle();
  }, [repo, master, leaf, chat]);

  const onOpenBlock = useCallback(
    (claim: ClaimLink) => {
      const resolved = resolveClaimSelection(claim, resolutionContext);
      if (!resolved) return;
      setViewTab('review');
      select(resolved, { keepTree: true });
    },
    [resolutionContext, select, setViewTab],
  );

  const onEvidence = useCallback(
    (destination: EvidenceDestination) => {
      const resolved = resolveEvidenceSelection(
        destination,
        { repository: repo, master, leaf },
        resolutionContext,
      );
      if (!resolved) return;
      setViewTab('review');
      select(resolved, { keepTree: true });
    },
    [repo, master, leaf, resolutionContext, select, setViewTab],
  );

  return { plannedPage, comparisonInfo, explainer, onExplain, onOpenBlock, onEvidence };
}

function HeaderExplanationSwitch({
  viewTab,
  setViewTab,
  explainer,
}: {
  viewTab: ExplanationViewTab;
  setViewTab: (tab: ExplanationViewTab) => void;
  explainer: ExplainerRead;
}) {
  const staleInfo = staleParts(explainer.state);
  const isBuilding = Boolean(
    explainer.state?.building && explainer.state.building.state !== 'idle',
  );
  return (
    <ExplanationSwitch
      view={viewTab}
      stale={staleInfo.total}
      building={isBuilding}
      retired={explainer.state?.retired ?? false}
      onSwitch={setViewTab}
    />
  );
}

export function ReviewSurface(props: ReviewTarget & { onBack: () => void; chat?: ReviewChatControl }) {
  const surface = useSurface(props);
  const reads: PageReads = { ...surface, initial: initialSelection(props) };
  const page = useReviewPage(reads);
  const root = useRef<HTMLDivElement>(null);
  const { repo, master, leaf, read, workspace } = surface;
  const [viewTab, setViewTab] = useState<ExplanationViewTab>('review');
  const payload = surface.shown ?? surface.frame;
  const explainerCtrl = useSurfaceExplainer(
    surface,
    page,
    props.chat,
    viewTab,
    setViewTab,
    Boolean(props.selectorKind && props.selectorId),
  );
  const change = useReviewChangePage({
    repo,
    master,
    leaf,
    comparison: page.changeSet.number,
    payload,
    laneRead: page.changeSet.lane,
    leafTrees: page.changeSet.leafTrees,
  });
  const select = page.select;
  useLayoutEffect(() => {
    surface.openTaskContext.current = (failure) => {
      select(null, { keepTree: false });
      surface.setInstead(failure);
    };
  });
  const onTraverse = useCallback((next: ReviewSelection) => select(next, { keepTree: true }), [select]);
  const traversal = useBlockTraversal({ zone: root, stops: page.stops, selection: page.selection, onSelect: onTraverse, enabled: change.changePage === null });
  const tree = useSidebar('review', 'left');
  useBracketKeys(tree.toggle, props.chat?.toggle, change.changePage === null);
  const markers = usePageMarkers(surface, page);
  useSelectionFocus(payload, workspace, surface.reading, root);
  const title = useTaskTitle(repo, master, leaf);
  const parts = { reads: { ...surface, initial: reads.initial }, page };
  return (
    <div ref={root} className={reviewRoot} data-testid="review-surface" data-kbzone="review" {...surfaceAttributes(surface, { markers: markers?.origin != null })}>
      <IntentMarkerScope.Provider value={markers}>
        <ReviewChangeSetNavigation.Provider value={change.openChanges}>
          <div className={reviewBody} data-review-body inert={change.changePage !== null} style={{ visibility: change.changePage ? 'hidden' : undefined }}>
            <ReviewHeader
              leaf={leaf}
              title={title}
              record={recordLabelOf(surface.frame, surface.history)}
              onBack={props.onBack}
              chip={<TopLineControls props={parts} />}
              comparison={<HeaderExplanationSwitch viewTab={viewTab} setViewTab={setViewTab} explainer={explainerCtrl.explainer} />}
              refresh={<ReviewRefresh onRefresh={surface.refreshReview} busy={read.phase === 'loading'} generation={generationOf(read, surface.carried, surface.shown)} />}
            />
            <ReviewGrid
              treeOpen={tree.open}
              tree={<PageTree props={parts} traversal={traversal.status} />}
              reading={
                <PageReading
                  props={parts}
                  tree={tree}
                  chat={props.chat}
                  open={change.openChanges}
                  viewTab={viewTab}
                  {...explainerCtrl}
                />
              }
            />
          </div>
          <ReviewChangeOverlay state={change.changePage} onBack={change.closeChanges} />
        </ReviewChangeSetNavigation.Provider>
      </IntentMarkerScope.Provider>
    </div>
  );
}
