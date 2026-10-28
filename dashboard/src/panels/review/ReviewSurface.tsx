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
import { followMovedNotice, forgetCardAnswers, useReviewTrees } from '../../data/reviewTrees';
import { useRevalidateIntentEntry } from '../../data/intentEntryRevalidation';
import { useReviewHistory } from './useReviewHistory';
import {
  subscribeReviewExplainerMoved,
  useReviewMemoryWatch,
  type ReviewExplainerMovedEvent,
} from '../../data/reviewMemoryWatch';
import { askFirstRequest } from '../../explainer/request';
import type { ReviewChangeContext } from '../changeset/reviewChangeSet';
import { ReviewChangeSetNavigation, type OpenReviewChanges } from './changeSetNavigation';
import { RIGHT_RAIL_ID } from '../../cockpit/layout';
import { useLeafPlanned, type ReviewChatControl } from '../../cockpit/reviewChat';
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
import { ExplanationView, ReadingJudgmentMounts } from './explainer/ExplanationView';
import { coldRegenerateItems, hasWork, staleBriefOf, staleGradesOf, type StaleFacts } from './explainer/explainerModel';
import { readExplainerState, type ExplainerTarget } from './explainer/explainerApi';
import { trayStore } from '../../tray/trayStore';
import {
  resolveEvidenceSelection,
  useExplainer,
  useExplainerBadge,
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
import { readSubjectOf } from './reading/useReviewSelection';
import { catalogueForComparison } from '../../data/useReviewCatalogue';
import { IntentMarkerScope, markerInventory, useIntentMarkerScope } from './intentMarkerScope';
import { MarkerReturn } from './IntentMarkers';
import { WorklistView } from './reading/SelectedWorklist';
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
import { useReviewShown } from './reviewShown';

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
  // Only a paged collection has bounds to state. The whole-review case said "no bounded collection
  // was paged", which is the absence of the control it sat under and took a line of the page's own
  // height to say (F5 c).
  if (refused || !page) return null;
  return (
    <p style={{ margin: '0.15rem 0' }} data-testid="review-page-bounds">
      {pageBounds(payload)}
    </p>
  );
}

function SelectedPagePicker({ page, selection, onSelect }: {
  page: ReviewCollectionPage | null | undefined;
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  return page || selection ? <PagePicker selection={selection} onSelect={onSelect} /> : null;
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
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'nowrap' }}>
        {/* The picker is the way back from a page and the way to choose another collection; with
            nothing paged it had one option and named it (F5 c). */}
        <SelectedPagePicker page={page} selection={selection} onSelect={onSelect} />
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
  return sameLane(context.lane, current.laneRead) && context.worklist === worklist
    && context.worklistSummary === matchingTreeRead(current.leafTrees, current.comparison)?.worklist_summary;
}
function worklistSummaryOf(current: CurrentChangeContext) { return matchingTreeRead(current.leafTrees, current.comparison)?.worklist_summary; }
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
      setState({ ...state, context: { ...state.context, lane: current.laneRead, worklist, worklistSummary: worklistSummaryOf(current) } });
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
        worklistSummary: worklistSummaryOf(current),
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
  const shown = useReviewShown();
  useEffect(() => {
    if (!shown) return undefined;
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        onBack();
      }
    };
    document.addEventListener('keydown', escape, true);
    return () => document.removeEventListener('keydown', escape, true);
  }, [onBack, shown]);
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

function PageDetails({ props, placement }: { props: PageProps & { reads: Surface }; placement?: 'footer' | 'frame' }) {
  const { reads, page } = props;
  const { shown, navigation } = reads;
  const cards = page.changeSet.cards;
  return (
    <TechnicalDetails
      placement={placement}
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
        onEvidence={onEvidence}
        headerDetails={<PageDetails props={props} placement="frame" />}
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
  plannedPage,
}: {
  props: PageProps & { reads: Surface };
  viewTab: ExplanationViewTab;
  plannedPage: PlannedPage | null;
}) {
  const { reads, page } = props;
  const read = reads.read;
  return (
    <>
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

function PageTreeToggle({ tree }: { tree: ReturnType<typeof useSidebar> }) {
  return <SidebarToggle side="left" open={tree.open} onToggle={tree.toggle} name="Tree sidebar"
    controls="review-tree" keyHint="[" testId="review-tree-toggle" />;
}

function PageChatToggle({ chat }: { chat?: ReviewChatControl }) {
  return chat ? <SidebarToggle side="right" open={chat.open} onToggle={chat.toggle} name="Chat sidebar"
    controls={RIGHT_RAIL_ID} keyHint="]" testId="review-chat-toggle" /> : null;
}

function PageReading(readingProps: PageReadingProps) {
  const { props, viewTab, plannedPage } = readingProps;
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
        focusedSubject={readSubjectOf(page.selection, changeSet.cards, false)}
        view={viewTab}
        orphan={<OrphanSlot props={props} layout={layout} />}
        notes={
          <ReadingNotes props={props} viewTab={viewTab} plannedPage={plannedPage} />
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
        details={viewTab === 'explanation' ? null : <PageDetails props={props} />}
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

// Whether a moved notice renews the deterministic reads: always when it offers no update, else only
// when no explainer work exists -- which needs the explainer's facts. `undefined`: not decidable yet.
function movedRenews(event: ReviewExplainerMovedEvent, facts: StaleFacts | null, known: boolean) {
  if (event.updateOffered === false) return true;
  if (event.kind === 'master-moved') return false;
  return known ? !hasWork(facts) : undefined;
}

/**
 * A moved notice renews the page's deterministic reads when no explainer work exists. The stream
 * replays the leaf's latest notice as soon as it opens, before the explainer's facts are read; that
 * notice waits for them instead of being judged "no work" for want of an answer -- which renewed
 * the opening's intent and tree reads once per open (D569).
 */
// `on`: the comparison on screen; while it is the latest, a move moves from it (D576).
function useExplainerMovedRefresh(surface: Surface, facts: StaleFacts | null, known: boolean, on: { older: boolean; number?: number }) {
  const { repo, master, leaf, history } = surface;
  const shown = on.older ? undefined : on.number;
  const revalidate = useRevalidateIntentEntry();
  const latest = useRef({ facts, known, memoryChanged: surface.memoryChanged, revalidate, shown });
  latest.current = { facts, known, memoryChanged: surface.memoryChanged, revalidate, shown };
  const pending = useRef<ReviewExplainerMovedEvent | null>(null);
  const renew = useCallback((event: ReviewExplainerMovedEvent) => {
    followMovedNotice(repo, master, leaf, latest.current.shown, event, {
      renew: () => {
        forgetCardAnswers(repo, master, leaf);
        bumpLeafMoveGeneration(repo, master, leaf);
        latest.current.memoryChanged();
        latest.current.revalidate();
      },
      reread: () => latest.current.memoryChanged(),
    });
  }, [repo, master, leaf]);

  useEffect(() => {
    pending.current = null;
    if (history === 'recorded') return undefined;
    return subscribeReviewExplainerMoved({ repo, master, leaf }, (event) => {
      if (event.cleared || event.kind === 'cleared') return;
      const renews = movedRenews(event, latest.current.facts, latest.current.known);
      pending.current = renews === undefined ? event : null;
      if (renews) renew(event);
    });
  }, [repo, master, leaf, history, renew]);

  useEffect(() => {
    const event = pending.current;
    if (!event || !known) return;
    pending.current = null;
    if (movedRenews(event, facts, known)) renew(event);
  }, [facts, known, renew]);
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

function isPlannedReadEnabled(
  surface: Surface,
  plannedLeaf: boolean | null,
  hasInitialSubject: boolean,
): boolean {
  // Only a leaf the task list says is planning has a planned view to read (MIK-R211 v2 rule 7). The
  // route refuses any other leaf, so asking anyway put a 4xx in the log for every review a developer
  // opened; an unknown leaf asks nothing.
  return (
    plannedLeaf === true &&
    surface.history !== 'recorded' &&
    !hasInitialSubject &&
    surface.read.phase === 'reviewed'
  );
}

type ComparisonInfo = { reference: string; number?: number; owner?: 'planned' | 'recorded' };

// Only what is on screen is read. The Review tab shows the top bar's badge and the cards' judgment
// chips, which `/stale` answers; the full seat state and the revision list are the Explanation tab's,
// read once that tab is first opened for this leaf -- and then kept current, not re-read per switch.
// Either read waits for the comparison on screen, so it is asked once and not again for its number.
function isBadgeReadEnabled(
  comparisonInfo: ComparisonInfo | undefined,
  surface: Surface,
  hasInitialSubject: boolean,
): boolean {
  if (comparisonInfo !== undefined) return true;
  return !hasInitialSubject && surface.read.phase === 'reviewed';
}

function isExplanationReadEnabled(
  opened: boolean,
  comparisonInfo: ComparisonInfo | undefined,
  surface: Surface,
): boolean {
  return opened && (comparisonInfo !== undefined || surface.read.phase !== 'loading');
}

// Whether the Explanation tab has been opened for this leaf: once opened, its reads stay enabled.
function useExplanationOpened(viewTab: ExplanationViewTab, leafKey: string): boolean {
  const [opened, setOpened] = useState<string | null>(null);
  if (viewTab === 'explanation' && opened !== leafKey) setOpened(leafKey);
  return opened === leafKey || viewTab === 'explanation';
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
  const address = useMemo(() => ({ repository: repo, master, leaf }), [repo, master, leaf]);
  const plannedLeaf = useLeafPlanned(useMemo(() => ({ repo, master, leaf }), [repo, master, leaf]));
  const plannedPage = usePlannedReview(
    address,
    isPlannedReadEnabled(surface, plannedLeaf, hasInitialSubject),
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
  const opened = useExplanationOpened(viewTab, `${repo}/${master}/${leaf}`);
  const explaining = isExplanationReadEnabled(opened, comparisonInfo, surface);
  // The surface's full read follows publications only once it is enabled; until then the badge's
  // own read is the one a publication renews (F17).
  const explainer = useExplainer(address, comparisonInfo, explaining, undefined, {
    watchWhileDisabled: false,
  });
  const badge = useExplainerBadge(
    address,
    comparisonInfo,
    !explaining && isBadgeReadEnabled(comparisonInfo, surface, hasInitialSubject),
  );
  // The full state is a superset of the badge's answer: once read it is the one the top bar shows.
  const facts: StaleFacts | null = explainer.state ?? badge.badge;
  usePublishLeafJudgments(repo, master, leaf, facts?.categories, facts?.grades);
  // The facts are known once either read answered or failed (a failed read decides as "no work"); a
  // notice applied to no answer yet is not one.
  useExplainerMovedRefresh(
    surface,
    facts,
    badge.settled || (explaining && explainer.phase === 'idle'),
    page.changeSet,
  );
  const resolutionContext = useResolutionContext(surface, page, payload, comparisonInfo);

  const select = page.select;
  // A press of ✦ Explain asks for the first-request form and opens the column. It must not
  // *toggle* it: the ask's own listener (`useFirstRequestOpens` in the shell) has opened it in the
  // same batch, and a toggle would close it again from the state the press started in — the press
  // then showed nothing at all. `expand` only ever opens.
  const onExplain = useCallback(() => {
    askFirstRequest({ repository: repo, master, leaf });
    if (chat?.expand) chat.expand();
    else if (chat && !chat.open) chat.toggle();
  }, [repo, master, leaf, chat]);

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

  return { plannedPage, comparisonInfo, explainer, facts, onExplain, onEvidence, address };
}

// The seat a press acts on: the full state when the Explanation tab has read it, else one `/state`
// read at the press itself -- the Review tab never reads the seat just to draw the badge.
async function seatAtPress(
  explainer: ExplainerRead,
  target: ExplainerTarget,
  comparison: ComparisonInfo | undefined,
): Promise<string> {
  if (explainer.state) return explainer.state.seat.state;
  // The same question the Explanation tab's read asks (a numbered comparison by its number), so a
  // press while that read is in flight joins it.
  const state = await readExplainerState(target, {
    comparison: comparison?.number === undefined ? comparison?.reference : undefined,
    comparisonNumber: comparison?.number,
    comparisonOwner: comparison?.owner,
  });
  return state.seat.state;
}

/**
 * The top line's staleness (F18): the count and the badge's own details -- which parts are stale,
 * why, and whether a newer comparison exists -- all from one answer: the full state once the
 * Explanation tab read it, else the Review tab's cheap `/stale` read, so the badge and the view can
 * never disagree. Its refresh control is the one press that regenerates: a live seat gets the
 * selective message, and a seat with no workspace gets the first-request form, prefilled with
 * exactly the stale parts and grades (MIK-R187 rule 8).
 */
function useHeaderStaleness(
  facts: StaleFacts | null,
  explainer: ExplainerRead,
  press: { target: ExplainerTarget; comparison: ComparisonInfo | undefined; onExplain: () => void },
) {
  const { target, comparison, onExplain } = press;
  const revision = facts?.explanation?.revision ?? null;
  const revisionExists = revision !== null || (explainer.revisions?.revisions.length ?? 0) > 0;
  const brief = useMemo(
    () => staleBriefOf(facts, revision, revisionExists),
    [facts, revision, revisionExists],
  );
  const [problem, setProblem] = useState<string | null>(null);
  const [pressing, setPressing] = useState(false);
  const pressRegenerate = useCallback(() => {
    const regeneration = brief.regeneration;
    if (!facts || !regeneration) return;
    setProblem(null);
    setPressing(true);
    void (async () => {
      try {
        const seat = await seatAtPress(explainer, target, comparison);
        if (seat === 'live') {
          const outcome = await explainer.regenerate({
            message: regeneration.message,
            stalePartIds: brief.parts.map((part) => part.id),
            staleGrades: staleGradesOf(facts),
          });
          if (outcome.kind === 'refused') setProblem(`${outcome.reason}: ${outcome.detail}`);
          return;
        }
        // Open the column first: its own ask names no parts and would replace the prefilled one.
        onExplain();
        askFirstRequest(target, coldRegenerateItems(facts));
        trayStore.getState().setText(regeneration.message);
      } catch (error) {
        setProblem(error instanceof Error ? error.message : String(error));
      } finally {
        setPressing(false);
      }
    })();
  }, [brief, facts, explainer, target, comparison, onExplain]);
  return { brief, pressRegenerate, problem, pressing };
}

function HeaderExplanationSwitch({
  viewTab,
  setViewTab,
  explainer,
  facts,
  target,
  comparison,
  onExplain,
}: {
  viewTab: ExplanationViewTab;
  setViewTab: (tab: ExplanationViewTab) => void;
  explainer: ExplainerRead;
  facts: StaleFacts | null;
  target: ExplainerTarget;
  comparison: ComparisonInfo | undefined;
  onExplain: () => void;
}) {
  const staleness = useHeaderStaleness(facts, explainer, { target, comparison, onExplain });
  // The label's marks come from whichever answer is read: the full state, or the badge's /stale.
  const building = explainer.state?.building ?? facts?.building;
  const isBuilding = Boolean(building && building.state !== 'idle');
  return (
    <ExplanationSwitch
      view={viewTab}
      stale={staleness.brief.total}
      brief={staleness.brief}
      regenerating={explainer.busy === 'regenerating' || staleness.pressing}
      regenerationProblem={staleness.problem}
      onRegenerate={staleness.pressRegenerate}
      building={isBuilding}
      retired={explainer.state?.retired ?? facts?.retired ?? false}
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
              treeToggle={<PageTreeToggle tree={tree} />}
              chatToggle={<PageChatToggle chat={props.chat} />}
              crumb={crumbOf(page, reads.navigation.catalogue.entries)}
              contextDisabled={viewTab === 'explanation'}
              markerReturn={<MarkerReturn />}
              chip={<TopLineControls props={parts} />}
              comparison={<HeaderExplanationSwitch viewTab={viewTab} setViewTab={setViewTab} explainer={explainerCtrl.explainer} facts={explainerCtrl.facts} target={explainerCtrl.address} comparison={explainerCtrl.comparisonInfo} onExplain={explainerCtrl.onExplain} />}
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
