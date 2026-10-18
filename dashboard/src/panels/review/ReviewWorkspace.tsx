// The accepted reading path: family context in the rail, intent and expressions in the center.
// Display state lives above read-cycle unmounts, so paging never resets a reader's choices.
// The workspace stays mounted while another subject is read or could not be read: `reading` swaps
// only the reading area for a statement naming the requested subject (pending, or the owner's failure
// or refusal), so the rail's expansion, scroll, focus and open
// disclosures survive a selection, and the previous subject's reading is never shown under the new
// subject's name.
// The family tree is the tree the reader has walked (MIK-R39, `walkedTree.ts`): selecting a row it shows
// adds the selected subject's families and removes none, so `k` can step back to every change passed;
// any other selection starts it afresh.
// For a tree comparison the rail also offers the unexplained-changes lane's two destinations after the
// families (MIK-R32); choosing one swaps the reading area for the lane, and choosing a family returns.
// Its diffs mark each hunk with the intents whose recorded ranges it meets (MIK-R34); following a
// marker selects its tree position here, and `Back to <file>` returns to the hunk.
import { useContext, useLayoutEffect, useRef } from 'react';
import { css } from '../../../styled-system/css';
import type { ReviewPayload, ReviewSelectorKind } from '../../data/review';
import type { ReviewFamilyContextEntry } from '../../data/reviewFamily';
import { selectedRevision } from './SubjectReview';
import { carriedPage } from '../../data/review';
import type { LaneRead, ReviewUnexplainedLane } from '../../data/reviewLane';
import { prefetchCardEntries, treeComparisonNumber, useReviewTrees } from '../../data/reviewTrees';
import type { ReviewPageRequest } from './ReviewReadCycle';
import { FamilyReviewCenter } from './FamilyReviewCenter';
import { FamilyTree, type FamilySelection, type WalkedTree } from './FamilyTree';
import { catalogueForComparison } from '../../data/useReviewCatalogue';
import { firstOutlineChange } from './changeTriage';
import { ReviewProblemBlock } from './ReviewOutcome';
import { ReviewNavigation, type ReviewNavigationState } from './ReviewNavigation';
import { ReviewScopeHeader } from './ReviewScopeHeader';
import { revealTreeRow } from './treeScroll';
import { codeChangesSummary } from './codeFacts';
import { ReviewDestinations, laneCounts } from './ReviewDestinations';
import { ReviewChangeSetNavigation } from './changeSetNavigation';
import { ReviewMainFacts } from './ReviewRecordPanes';
import { InvariantTargetState, useInvariantTargetState } from './MarkerTargetState';
import { LeafWorklistView } from './LeafKnowledgeChanges';
import { MarkerReturn } from './IntentMarkers';
import { IntentMarkerScope, markerInventory, useIntentMarkerScope } from './intentMarkerScope';

import { workspaceMarkerMoves } from './markerNavigation';
import {
  NO_FAMILY_CONTEXT,
  type TreeIntent,
  type Walk,
  type WalkedSubject,
  keptFamilies,
  subjectTitle,
  useWalkedTree,
} from './walkedTree';

// The one sticky offset of the stacked layout (MIK-L33 x MIK-L34): while a followed marker's way back
// is open it holds the top (`markerReturn`: 0.5rem down, 2rem tall), and every other sticky control
// of the workspace -- the family tree's triage bar -- sticks below it at `--review-sticky-top`, so
// neither covers the other. Side by side the rail scrolls on its own and the way back is not sticky.
const workspace = css({
  display: 'grid',
  gridTemplateColumns: 'minmax(17rem, 24rem) minmax(0, 1fr)',
  gap: '1rem',
  alignItems: 'start',
  '@media (max-width: 60rem)': {
    gridTemplateColumns: 'minmax(0, 1fr)',
    '&[data-marker-return="open"]': { '--review-sticky-top': '2.75rem' },
  },
});
const shell = css({
  background: 'bgPanel',
  border: '1px solid var(--grid)',
  borderRadius: '3px',
  padding: '1rem',
  minWidth: 0,
});
const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.35rem 0' });
const title = css({
  color: 'cyan',
  fontSize: '0.75rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  margin: '0 0 0.6rem',
});
const jump = css({
  gridColumn: '1 / -1',
  justifySelf: 'start',
  '@media (min-width: 60.01rem)': { display: 'none' },
});
// While the rail and the reading area stack, the way back stays in view at the target.
const markerReturn = css({
  gridColumn: '1 / -1',
  justifySelf: 'start',
  '@media (max-width: 60rem)': {
    position: 'sticky',
    top: '0.5rem',
    zIndex: 2,
    // A fixed box, so the offset below it (`--review-sticky-top`) is exact: one line, however long
    // the file's name.
    boxSizing: 'border-box',
    height: '2rem',
    maxWidth: '100%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
});

// The subject on screen whose review has not answered: still being read (`problem` null), or its
// read failed or was refused (`problem` is the owner's own block, labelled with this subject). It is
// bound to the read cycle's key for that question.
export interface ReadingStatus {
  key: string;
  subject: string;
  label: string;
  problem: React.ReactNode | null;
}

function ReadingStatusCenter({ reading }: { reading: ReadingStatus }) {
  if (reading.problem !== null)
    return (
      <section
        className={shell}
        data-testid="review-reading-problem"
        data-problem-key={reading.key}
        data-problem-subject={reading.subject}
      >
        <h2 className={title}>{reading.label} could not be read</h2>
        {reading.problem}
        <p className={muted}>
          The families, invariants and source explorer remain available; choose another subject or
          ask again.
        </p>
      </section>
    );
  return (
    <section
      className={shell}
      data-testid="review-reading-pending"
      data-pending-key={reading.key}
      data-pending-subject={reading.subject}
      aria-busy="true"
      role="status"
    >
      <h2 className={title}>Reading {reading.label}…</h2>
      <p className={muted}>
        The families, invariants and source explorer stay available while this review is read.
      </p>
    </section>
  );
}

import type { FocusRequest, WorkspaceState } from './workspaceState';
export { useWorkspaceState, type WorkspaceState } from './workspaceState';

// The selection to mark. `shown` are the families a mark may sit on: the answer's own for the reading
// area, the walked tree's for the rail (a kept family's row is a row of the tree, not of the answer).
function selectedContext(
  payload: ReviewPayload,
  chosen: FamilySelection | null,
  selectorId?: string,
  shown: ReviewFamilyContextEntry[] = payload.family_context?.entries ?? [],
): FamilySelection | null {
  if (chosen && shown.some((entry) => entry.family_id === chosen.familyId)) return chosen;
  return initialContext(payload, payload.family_context?.entries ?? [], selectorId);
}

interface ReviewWorkspaceProps {
  payload: ReviewPayload;
  // Set while the subject on screen has no answer (pending, failed or refused): `payload` is then
  // the last admitted answer of this task context, used for the shell only.
  reading?: ReadingStatus | null;
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: 'recorded';
  onPageSelect: (page: ReviewPageRequest | undefined) => void;
  state: WorkspaceState;
  navigation?: ReviewNavigationState;
  // The unexplained-changes lane of this payload's tree comparison (read once by the surface, which
  // also gives it to the technical details); null until a read starts.
  laneRead?: LaneRead<ReviewUnexplainedLane> | null;
  changesOpen?: boolean;
  leafTrees: ReturnType<typeof useReviewTrees>;
}

// The workspace, inside the scope of its diffs' intent markers: a tree comparison's changed files are
// classified for them (once per surface), and a followed marker can be returned to.
export function ReviewWorkspace(props: ReviewWorkspaceProps) {
  const { payload, repo, master, leaf, state, navigation } = props;
  const markers = useIntentMarkerScope({
    repo,
    master,
    leaf,
    comparison: treeComparisonNumber(payload.limitations),
    ...markerInventory(payload.source.inventory),
    moves: workspaceMarkerMoves(state, navigation),
  });
  return (
    <IntentMarkerScope.Provider value={markers}>
      <WorkspaceBody {...props} />
    </IntentMarkerScope.Provider>
  );
}

// The tree the reader has walked (MIK-R39), the selection marks on the reading area and on the tree,
// and the two selections that start in the tree: a row, and a family's roster continuation.
function useWalkedSelection(
  shown: {
    payload: ReviewPayload;
    reading: ReadingStatus | null;
    selectorId?: string;
    scope: string;
  },
  actions: {
    state: WorkspaceState;
    navigation?: ReviewNavigationState;
    onPageSelect: (page: ReviewPageRequest | undefined) => void;
    prefetchCards: (invariants: string[]) => void;
  },
) {
  const { payload, reading, selectorId, scope } = shown;
  const { state, navigation, onPageSelect } = actions;
  const walk = useWalkedTree(payload, scope, state.treeIntent);
  const walked = walk.families.map((family) => family.entry);
  // While unanswered, only the reader's explicit choice is marked: deriving one from the previous
  // payload would mark that subject's family as the requested subject's context.
  const marked = (entries?: ReviewFamilyContextEntry[]) =>
    reading ? state.chosen : selectedContext(payload, state.chosen, selectorId, entries);
  const rosterNext = (family: string, _side: string, continuation: string) => {
    const page: ReviewPageRequest = { of: 'family_members', continuation };
    // A kept family's cursor was published for an earlier subject and the server continues it only
    // for a subject whose context holds the family: selecting the family continues it.
    const inContext = (payload.family_context?.entries ?? []).some(
      (entry) => entry.family_id === family,
    );
    if (inContext || !navigation) onPageSelect(page);
    else
      navigation.onSelect(
        { kind: 'family', id: family },
        { familyId: family },
        { keepTree: true, page },
      );
  };
  return {
    walk,
    chosen: marked(),
    treeChosen: marked(walked),
    choose: (selection: FamilySelection) =>
      chooseSubject(walked, selection, state, navigation, actions.prefetchCards),
    rosterNext,
  };
}

// `open` while a followed marker's way back is shown: the workspace then holds the sticky offset.
function useMarkerReturnState(): 'open' | undefined {
  return useContext(IntentMarkerScope)?.origin ? 'open' : undefined;
}

function bindWorkspaceNavigation(
  navigation: ReviewNavigationState | undefined,
  payload: ReviewPayload,
): ReviewNavigationState | undefined {
  if (!navigation) return undefined;
  return { ...navigation, catalogue: catalogueForComparison(navigation.catalogue, payload) };
}
function readingState(reading: ReadingStatus | null): 'pending' | 'unavailable' | null {
  if (!reading) return null;
  return reading.problem === null ? 'pending' : 'unavailable';
}

function WorkspaceBody({
  payload,
  reading = null,
  repo,
  master,
  leaf,
  selectorId,
  history,
  onPageSelect,
  state,
  navigation,
  laneRead = null,
  changesOpen = false,
  leafTrees,
}: ReviewWorkspaceProps) {
  const boundNavigation = bindWorkspaceNavigation(navigation, payload);
  const safeCatalogue = boundNavigation?.catalogue;
  useSelectionFocus(payload, state, reading, safeCatalogue?.loading === true);
  const returning = useMarkerReturnState();
  const comparison = treeComparisonNumber(payload.limitations);
  // The rail tree's selections start their cards beside the subject request too (L81-R1-F18):
  // the same bound prefetch the center roster's `choose` uses, shared with the rail below.
  const prefetchFamilyCards = (invariants: string[]) =>
    prefetchCardEntries(repo, master, leaf, comparison, invariants);
  const { walk, chosen, treeChosen, choose, rosterNext } = useWalkedSelection(
    { payload, reading, selectorId, scope: `${repo}/${master}/${leaf}/${history ?? 'live'}` },
    {
      state,
      navigation,
      onPageSelect,
      prefetchCards: prefetchFamilyCards,
    },
  );
  return (
    <div
      className={workspace}
      data-testid="review-workspace"
      data-marker-return={returning}
      data-diff-layout={state.layout}
      data-full-file={String(state.fullFile)}
    >
      <ReviewScopeHeader payload={payload} repo={repo} master={master} leaf={leaf} history={history} status={readingState(reading)} />
      <button
        type="button"
        className={jump}
        data-testid="review-jump-to-selection"
        onClick={() => jumpToReview(state.center)}
      >
        ↓ Jump to selected review
      </button>
      <MarkerReturn className={markerReturn} />
      <WorkspaceRail
        payload={payload}
        repo={repo}
        master={master}
        leaf={leaf}
        state={state}
        navigation={boundNavigation}
        chosen={treeChosen}
        walk={walk}
        laneRead={laneRead}
        subject={navigation?.subject}
        changesOpen={changesOpen}
        leafTrees={leafTrees}
        prefetchCards={prefetchFamilyCards}
      />
      <WorkspaceCenter
        payload={payload}
        task={{ repo, master, leaf, history }}
        reading={reading}
        state={state}
        subject={navigation?.subject}
        chosen={chosen}
        onOpenMember={(familyId, memberRevisionId) => choose({ familyId, memberRevisionId })}
        rosterNext={rosterNext}
        leafTrees={leafTrees}
        catalogue={safeCatalogue}
        laneRead={laneRead}
      />
    </div>
  );
}

// The reading area. Its column is one DOM node for the life of the workspace; only its content is
// swapped, between the unanswered subject's status and the answered subject's reading.
function WorkspaceCenter({
  payload,
  reading,
  state,
  subject,
  chosen,
  onOpenMember,
  rosterNext,
  leafTrees,
  catalogue,
  laneRead,
}: {
  payload: ReviewPayload;
  task: { repo: string; master: string; leaf: string; history?: 'recorded' };
  reading: ReadingStatus | null;
  state: WorkspaceState;
  subject?: ReviewNavigationState['subject'];
  chosen: FamilySelection | null;
  onOpenMember: (familyId: string, memberRevisionId: string) => void;
  rosterNext: (family: string, side: string, continuation: string) => void;
  leafTrees: ReturnType<typeof useReviewTrees>;
  catalogue?: ReviewNavigationState['catalogue'];
  laneRead: LaneRead<ReviewUnexplainedLane> | null;
}) {
  // The leaf's tree view (MIK-R25), shared from the surface's single comparison-bound read.
  // The read is pinned to the comparison the payload names (review F11), so the knowledge panel and
  // the cards' planning marks describe the same four trees as the review on screen.
  const comparison = treeComparisonNumber(payload.limitations);
  const openChanges = useContext(ReviewChangeSetNavigation);
  return (
    <div
      ref={state.center}
      tabIndex={-1}
      data-testid="review-center-column"
      className={css({ minWidth: 0 })}
    >
      {reading ? (
        <ReadingStatusCenter reading={reading} />
      ) : state.worklist ? (
        <LeafWorklistView
          read={comparison === undefined ? { phase: 'not-converted' } : leafTrees}
          reviewComparison={comparison}
          onOpenChanges={() => openChanges?.('all', undefined, 'memory')}
          onOpenPath={(path) => openChanges?.('all', path)}
        />
      ) : (
        <>
          <ReviewContextNotes
            payload={payload}
            subject={subject}
            onWorklist={() => state.setWorklist(true)}
            catalogue={catalogue}
          />
          <FamilyReviewCenter
            payload={payload}
            subject={subject}
            selection={chosen}
            layout={state.layout}
            onLayout={state.setLayout}
            fullFile={state.fullFile}
            onFullFile={state.setFullFile}
            openPath={state.openPath}
            onOpenPath={state.closePath}
            onOpenFromCenter={(path) => openChanges?.('all', path)}
            onOpenMember={onOpenMember}
            onRosterNext={rosterNext}
            leafTrees={leafTrees}
            outline={catalogue?.outline}
          />
          <ReviewMainFacts
            payload={payload}
            laneRead={laneRead}
            onAllChanges={() => openChanges?.('all')}
          />
          <RosterPageNote payload={payload} />
        </>
      )}
    </div>
  );
}

function ReviewContextNotes({
  payload,
  subject,
  onWorklist,
  catalogue,
}: {
  payload: ReviewPayload;
  subject?: ReviewNavigationState['subject'];
  onWorklist: () => void;
  catalogue?: ReviewNavigationState['catalogue'];
}) {
  const markerTarget = useInvariantTargetState(subject, payload.family_context);
  const openChanges = useContext(ReviewChangeSetNavigation);
  return (
    <>
      {' '}
      {markerTarget ? (
        <InvariantTargetState target={markerTarget} context={payload.family_context} />
      ) : null}
      {payload.family_context &&
      !['recorded', 'no_subject_selected'].includes(payload.family_context.state) ? (
        <div data-review-content>
          <p>
            {payload.family_context.state}: {payload.family_context.detail}
          </p>
          {payload.family_context.limitations.map((text) => (
            <p key={text}>{text}</p>
          ))}
        </div>
      ) : null}
      {!subject ? (
        <section>
          <UnselectedMeaning catalogue={catalogue} />
          <button type="button" onClick={() => openChanges?.('all')}>
            All changes
          </button>
          <button type="button" onClick={() => onWorklist()}>
            Worklist
          </button>
        </section>
      ) : null}
    </>
  );
}

function UnselectedMeaning({ catalogue }: { catalogue?: ReviewNavigationState['catalogue'] }) {
  if (catalogue?.problem)
    return (
      <ReviewProblemBlock
        problem={catalogue.problem}
        origin="failure"
        subject="the subject catalogue"
      />
    );
  if (catalogue?.outline?.state === 'unavailable')
    return <p data-review-content="reason">{catalogue.outline.detail}</p>;
  if (
    catalogue?.outline?.state === 'outlined' &&
    !firstOutlineChange(catalogue.outline, 'authored')
  )
    return <p>This leaf changed no recorded meaning.</p>;
  return <p>No recorded intent is selected.</p>;
}

const rail = css({
  display: 'grid',
  gap: '0.75rem',
  minWidth: 0,
  alignContent: 'start',
  position: 'sticky',
  top: 0,
  maxHeight: 'calc(100dvh - 12rem)',
  overflowY: 'auto',
  scrollbarGutter: 'stable',
  '@media (max-width: 60rem)': {
    position: 'static',
    maxHeight: 'none',
    overflowY: 'visible',
  },
  '@media (max-width: 40rem)': { maxHeight: '45dvh', overflowY: 'auto' },
});

function WorkspaceRail({
  payload,
  state,
  navigation,
  chosen,
  walk,
  laneRead,
  subject,
  changesOpen,
  leafTrees,
  prefetchCards,
}: {
  payload: ReviewPayload;
  repo: string;
  master: string;
  leaf: string;
  state: WorkspaceState;
  navigation?: ReviewNavigationState;
  chosen: FamilySelection | null;
  walk: Walk;
  laneRead: LaneRead<ReviewUnexplainedLane> | null;
  // The answered subject (none while another is read): an intent marker's unknown-membership target.
  subject?: ReviewNavigationState['subject'];
  changesOpen: boolean;
  leafTrees: ReturnType<typeof useReviewTrees>;
  prefetchCards: (invariants: string[]) => void;
}) {
  const tree = walkedTreeOf(walk, payload, navigation?.catalogue);
  const families = (
    <FamilyRailContext
      payload={payload}
      state={state}
      walked={tree}
      // While a lane destination is on screen, no family node is the current selection.
      chosen={state.worklist ? null : chosen}
      selectable={Boolean(navigation?.catalogue.entries?.length)}
      subject={subject}
      navigation={navigation}
      traversalEnabled={!changesOpen}
      prefetchCards={prefetchCards}
      leafTrees={leafTrees}
    />
  );
  return (
    <aside className={rail} data-review-tree-panel>
      <div className={shell}>
        {navigation ? (
          <ReviewNavigation
            {...navigation}
            walkedEntries={tree.entries}
            summaryCounts={laneCounts(laneRead)}
            codeSummary={codeChangesSummary(navigation.catalogue.outline, leafTrees, treeComparisonNumber(payload.limitations))}
            destinations={
              <ReviewDestinations
                payload={payload}
                laneRead={laneRead}
                state={state}
                leafTrees={leafTrees}
              />
            }
          >
            {families}
          </ReviewNavigation>
        ) : (
          families
        )}
      </div>
    </aside>
  );
}

// What the family tree draws (MIK-R39): the walked families, which of them are kept and for which
// subject they were last read, and what to say of an answer that composed no family context.
function walkedTreeOf(
  walk: Walk,
  payload: ReviewPayload,
  catalogue?: ReviewNavigationState['catalogue'],
): WalkedTree {
  const context = payload.family_context;
  const composed = context?.state === 'recorded' || context?.state === 'partial';
  return {
    entries: walk.families.map((family) => family.entry),
    kept: new Map(
      keptFamilies(walk).map(({ entry, readFor, answer }) => [
        entry.family_id,
        readForLabel(readFor, answer, catalogue),
      ]),
    ),
    notice: walk.notice,
    subjectState: composed
      ? null
      : `For the selected subject: ${emptyFamilyDescription(context?.state ?? 'absent', false)}`,
  };
}

function readForLabel(
  subject: WalkedSubject | undefined,
  answer: ReviewPayload,
  catalogue?: ReviewNavigationState['catalogue'],
): string {
  if (!subject) return 'an earlier subject';
  const entry = catalogue?.entries?.find(
    (row) => row.selector_kind === subject.kind && row.selector_id === subject.id,
  );
  return `${subject.kind} ${entry?.label ?? subjectTitle(answer, subject)}`;
}

function FamilyRailContext({
  payload,
  state,
  walked,
  chosen,
  subject,
  navigation,
  traversalEnabled,
  prefetchCards,
  leafTrees,
}: {
  payload: ReviewPayload;
  state: WorkspaceState;
  walked: WalkedTree;
  chosen: FamilySelection | null;
  selectable: boolean;
  subject?: ReviewNavigationState['subject'];
  navigation?: ReviewNavigationState;
  traversalEnabled: boolean;
  prefetchCards: (invariants: string[]) => void;
  leafTrees: ReturnType<typeof useReviewTrees>;
}) {
  const context = payload.family_context;
  return (
    <FamilyTree
      context={context ?? NO_FAMILY_CONTEXT}
      treeRead={leafTrees}
      reviewComparison={treeComparisonNumber(payload.limitations)}
      walked={walked}
      selection={chosen}
      query={state.query}
      onQuery={state.setQuery}
      outline={navigation?.catalogue.outline}
      catalogueEntries={navigation?.catalogue.entries}
      catalogueLoading={navigation?.catalogue.loading}
      catalogueProblem={navigation?.catalogue.problem}
      subject={subject}
      traversalEnabled={traversalEnabled}
      onSubjectSelect={(next, selection) => {
        // The new set's cards start now, beside the subject request -- not after it
        // resolves (L81-R1-F18). The walked entries name the set, as in `chooseSubject`.
        if (selection) {
          const entry = walked.entries.find(
            (row) => row.family_id === selection.familyId,
          );
          if (entry) prefetchCards(familyInvariantSet(entry));
        }
        navigation?.onSelect(next, selection, { keepTree: true });
        state.setChosen(selection ?? null);
      }}
    />
  );
}

function RosterPageNote({ payload }: { payload: ReviewPayload }) {
  const page = carriedPage(payload);
  if (page?.collection !== 'family_members') return null;
  return (
    <details data-disclosure="provenance">
      <summary>Roster page details</summary>
      <p className={muted} data-testid="review-roster-walk">
        Roster page · {page.scope.join(' · ')}
      </p>
    </details>
  );
}

function emptyFamilyDescription(state: string, canChoose: boolean): string {
  if (state === 'no_family_recorded')
    return 'The selected invariant has no recorded family membership.';
  if (state === 'absent')
    return 'No family context was supplied for this comparison. Attribution is unknown.';
  if (state === 'no_subject_selected')
    return canChoose
      ? 'Open a recorded family or invariant above. All source changes remain available below.'
      : 'No recorded intent is selected. Source review remains available.';
  return 'Family context could not be read for this comparison. Source review remains available.';
}

// The answer of a selection moves focus to the selected node. A selection that needs no new answer
// (the same subject again) moves it when the selection itself is made, and a request left by a
// refresh only brings the selected node into view: the rail keeps its scroll across the new answer.
function useSelectionFocus(
  payload: ReviewPayload,
  state: WorkspaceState,
  reading: ReadingStatus | null,
  catalogueLoading: boolean,
): void {
  const { center, focusSelection, treeIntent } = state;
  const pending = reading !== null;
  const unavailable = reading !== null && reading.problem !== null;
  const seen = useRef(payload);
  useLayoutEffect(() => {
    const request = focusSelection.current;
    const answered = seen.current !== payload;
    seen.current = payload;
    if (!request || treeIntent.id <= request.after) return;
    // Only a render made after the request can answer it: the answer a late effect of an earlier
    // render sees was on screen before the reader asked.
    if (answered) request.answered = true;
    if (catalogueLoading && !pending) return;
    if (pending) {
      revealPendingRead(request, center.current, treeIntent, unavailable);
      return;
    }
    if (request.scrollOnly && !request.answered) return;
    focusSelection.current = null;
    landFocus(request, center.current, treeIntent);
  }, [payload, pending, unavailable, catalogueLoading, treeIntent, center, focusSelection]);
}

function revealPendingRead(
  request: FocusRequest,
  center: HTMLElement | null,
  intent: TreeIntent,
  unavailable: boolean,
): void {
  if (unavailable) revealUnavailableRead(request, center, intent);
  else if (intent.keep && selectionFocusAvailable(request))
    // The pending shell can move/clip the panel after the key handler's first reveal.
    revealTreeRow(selectionNode(center));
}

// OR-R042: a failed/refused selection reveals its own status without landing focus early or
// consuming the request that a successful retry still needs.
function revealUnavailableRead(
  request: FocusRequest,
  center: HTMLElement | null,
  intent: TreeIntent,
): void {
  if (selectionFocusAvailable(request) && stackedTreeRead(intent))
    center?.scrollIntoView({ block: 'start' });
}

function landFocus(request: FocusRequest, center: HTMLElement | null, intent: TreeIntent): void {
  if (request.scrollOnly) {
    selectionNode(center)?.scrollIntoView?.({ block: 'nearest' });
    return;
  }
  // A reader who moved focus while the subject was pending keeps it: only focus still on the
  // selecting control, or lost with an unmounted node (`body`), is moved to the selection.
  if (!selectionFocusAvailable(request)) return;
  focusSelectedRead(center, intent);
}

function selectionFocusAvailable(request: FocusRequest): boolean {
  const active = document.activeElement;
  return active === null || active === document.body || active === request.from;
}

function stackedTreeRead(intent: TreeIntent): boolean {
  return intent.keep && window.matchMedia('(max-width: 60rem)').matches;
}

function focusSelectedRead(center: HTMLElement | null, intent: TreeIntent): void {
  const node = selectionNode(center) ?? center;
  // Kept-tree navigation has the same local scroll ownership at every width. Desktop panels can
  // also extend past the review's clipping edge, so native focus alone cannot reveal their rows.
  if (intent.keep) {
    node?.focus({ preventScroll: true });
    revealTreeRow(node);
    return;
  }
  node?.focus();
}

// What a selection focuses: an intent marker's unknown-membership target as its own state (MIK-R34,
// review F3), else the tree's current node.
function selectionNode(center: HTMLElement | null): HTMLElement | null {
  const workspace = center?.closest('[data-testid="review-workspace"]');
  if (!workspace) return null;
  return (
    workspace.querySelector<HTMLElement>('[data-target-state-focus]') ??
    workspace.querySelector<HTMLElement>('[data-tree-node][aria-current="true"]')
  );
}

// The invariants whose entries make one family's cards: every member's invariant, sorted, so one
// family is one cards read (mirrors cardInvariants in FamilyReviewCenter for the walked entry).
function familyInvariantSet(entry: ReviewFamilyContextEntry): string[] {
  const keys = new Set<string>();
  for (const row of [...entry.before.members, ...entry.after.members])
    if (row.invariant_id) keys.add(row.invariant_id);
  return [...keys].sort();
}

// A selection of a row the tree shows, which may be a kept family's: the walked tree keeps (MIK-R39).
function chooseSubject(
  walked: ReviewFamilyContextEntry[],
  selection: FamilySelection,
  state: WorkspaceState,
  navigation?: ReviewNavigationState,
  prefetchCards?: (invariants: string[]) => void,
): void {
  const family = walked.find((entry) => entry.family_id === selection.familyId);
  if (!family) {
    state.setChosen(selection);
    return;
  }
  // The new set's cards start now, beside the subject request -- not after it resolves (L81-R1-F18).
  prefetchCards?.(familyInvariantSet(family));
  const member = [...family.before.members, ...family.after.members].find(
    (row) => row.invariant_revision_id === selection.memberRevisionId,
  );
  if (selection.memberRevisionId === undefined) {
    navigation?.onSelect({ kind: 'family', id: selection.familyId }, selection, { keepTree: true });
  } else if (member?.invariant_id) {
    navigation?.onSelect({ kind: 'invariant', id: member.invariant_id }, selection, {
      keepTree: true,
    });
  }
  state.setChosen(selection);
}

function jumpToReview(center: React.RefObject<HTMLDivElement | null>): void {
  center.current?.focus({ preventScroll: true });
  center.current?.scrollIntoView({ block: 'start' });
}

function initialContext(
  payload: ReviewPayload,
  entries: NonNullable<ReviewPayload['family_context']>['entries'],
  selectorId?: string,
): FamilySelection | null {
  const family = entries.find((entry) => entry.family_id === selectorId) ?? entries[0];
  if (!family) return null;
  const selected = selectedRevision(payload.knowledge, { kind: 'invariant', id: selectorId ?? '' });
  const revision = selected?.after_revision_id ?? selected?.before_revision_id;
  return { familyId: family.family_id, memberRevisionId: revision };
}
