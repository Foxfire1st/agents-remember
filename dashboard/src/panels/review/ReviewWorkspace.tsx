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
import { useContext, useEffect, useRef, useState } from 'react';
import { css } from '../../../styled-system/css';
import type { ReviewFamilyContext, ReviewPayload, ReviewSelectorKind } from '../../data/review';
import type { ReviewFamilyContextEntry } from '../../data/reviewFamily';
import { selectedRevision } from './SubjectReview';
import { carriedPage } from '../../data/review';
import type { LaneRead, ReviewUnexplainedLane } from '../../data/reviewLane';
import { treeComparisonNumber, useReviewTrees } from '../../data/reviewTrees';
import type { ReviewPageRequest } from './ReviewReadCycle';
import { FamilyReviewCenter } from './FamilyReviewCenter';
import { FamilyTree, type FamilySelection, type WalkedTree } from './FamilyTree';
import { ReviewNavigation, type ReviewNavigationState } from './ReviewNavigation';
import { ReviewScopeHeader } from './ReviewScopeHeader';
import { SourceExplorer, type DiffLayout } from './SourceExplorer';
import { LaneDestinations, type LaneSelection, UnexplainedLaneCenter } from './UnexplainedLane';
import type { GateRead } from './LaneFileFocus';
import { explorerAttribution } from './laneFocus';
import { MarkerReturn } from './IntentMarkers';
import { IntentMarkerScope, markerInventory, useIntentMarkerScope } from './intentMarkerScope';
import { InvariantTargetState, useInvariantTargetState } from './MarkerTargetState';
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

function FamilyNotComposed({
  context,
  selectable,
}: {
  context?: ReviewFamilyContext;
  selectable: boolean;
}) {
  const state = context?.state ?? 'absent';
  const knownEmpty = state === 'no_family_recorded';
  const canChoose = state === 'no_subject_selected' && selectable;
  const label = knownEmpty
    ? 'No recorded family'
    : canChoose
      ? 'Choose a recorded subject'
      : 'Attribution unknown';
  const description = emptyFamilyDescription(state, canChoose);
  return (
    <section className={shell} data-testid="review-family-tree" data-family-state={state}>
      <h2 className={title}>{label}</h2>
      <p className={muted} data-testid="review-family-context" data-context-state={state}>
        {description}
      </p>
      {context ? (
        <details>
          <summary>Context details</summary>
          <p>{context.detail}</p>
          {context.limitations.map((item) => (
            <p key={item} data-testid="review-family-limitation">
              {item}
            </p>
          ))}
        </details>
      ) : null}
    </section>
  );
}

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

// A selection's request to land focus on the selected node once its answer is shown. `scrollOnly`
// (a refresh) only brings the node into view; `answered` is set when an answer has arrived after the
// request was made.
interface FocusRequest {
  from: Element | null;
  // The tree intent that was current when the request was made. A render from before the selection
  // (its passive effect can run late, after the reader's next click) must not take the request.
  after: number;
  scrollOnly?: boolean;
  answered?: boolean;
}

export interface WorkspaceState {
  chosen: FamilySelection | null;
  setChosen: (selection: FamilySelection | null) => void;
  query: string;
  setQuery: (next: string) => void;
  layout: DiffLayout;
  setLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  setFullFile: (next: boolean) => void;
  openPath: string | null | undefined;
  openFromCenter: (path: string) => void;
  closePath: (path: string | null) => void;
  // Sets the opened path without moving focus (an intent marker's follow and return).
  setOpenPath: (path: string | null | undefined) => void;
  center: React.RefObject<HTMLDivElement | null>;
  // The unexplained-changes lane destination on screen (and the file it opened), or none.
  lane: LaneSelection | null;
  setLane: (next: LaneSelection | null) => void;
  // Set by an explicit selection with the element that had focus then; the answer's focus lands on
  // the selected node only if the reader has not moved focus elsewhere in the meantime.
  focusSelection: React.RefObject<FocusRequest | null>;
  // The latest selection as far as the family tree is concerned (MIK-R39): an in-tree selection
  // keeps the tree the reader has walked, anything else starts it afresh. See `walkedTree.ts`.
  treeIntent: TreeIntent;
  beginSelection: (keepTree: boolean) => void;
}

export function useWorkspaceState(): WorkspaceState {
  const [chosen, setChosen] = useState<FamilySelection | null>(null);
  const [query, setQuery] = useState('');
  const [layout, setLayout] = useState<DiffLayout>('split');
  const [fullFile, setFullFile] = useState(false);
  const [openPath, setOpenPath] = useState<string | null>();
  const [lane, setLaneState] = useState<LaneSelection | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const center = useRef<HTMLDivElement>(null);
  const focusSelection = useRef<FocusRequest | null>(null);
  const [treeIntent, setTreeIntent] = useState<TreeIntent>({ id: 0, keep: false });
  const beginSelection = (keepTree: boolean) =>
    setTreeIntent((previous) => ({ id: previous.id + 1, keep: keepTree }));
  const openFromCenter = (path: string) => {
    opener.current = document.activeElement as HTMLElement | null;
    setOpenPath(path);
  };
  const closePath = (path: string | null) => {
    if (path !== null) opener.current = document.activeElement as HTMLElement | null;
    setOpenPath(path);
    if (path === null) opener.current?.focus();
  };
  const choose = (selection: FamilySelection | null) => {
    setChosen(selection);
    setLaneState(null);
    if (selection) revealCenter(center);
  };
  const setLane = (next: LaneSelection | null) => {
    setLaneState(next);
    if (next) revealCenter(center);
  };
  return {
    chosen,
    setChosen: choose,
    query,
    setQuery,
    layout,
    setLayout,
    fullFile,
    setFullFile,
    openPath,
    openFromCenter,
    closePath,
    setOpenPath,
    center,
    lane,
    setLane,
    focusSelection,
    treeIntent,
    beginSelection,
  };
}

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
  // also gives it to the technical details); `null` for a dataset review.
  laneRead?: LaneRead<ReviewUnexplainedLane> | null;
}

// The workspace, inside the scope of its diffs' intent markers: a tree comparison's changed files are
// classified for them (once per surface), and a followed marker can be returned to. A dataset review
// names no tree comparison and has no scope.
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
    choose: (selection: FamilySelection) => chooseSubject(walked, selection, state, navigation),
    rosterNext,
  };
}

// `open` while a followed marker's way back is shown: the workspace then holds the sticky offset.
function useMarkerReturnState(): 'open' | undefined {
  return useContext(IntentMarkerScope)?.origin ? 'open' : undefined;
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
}: ReviewWorkspaceProps) {
  useSelectionFocus(payload, state, reading);
  const returning = useMarkerReturnState();
  const { walk, chosen, treeChosen, choose, rosterNext } = useWalkedSelection(
    { payload, reading, selectorId, scope: `${repo}/${master}/${leaf}/${history ?? 'live'}` },
    { state, navigation, onPageSelect },
  );
  return (
    <div
      className={workspace}
      data-testid="review-workspace"
      data-marker-return={returning}
      data-diff-layout={state.layout}
      data-full-file={String(state.fullFile)}
    >
      <ReviewScopeHeader
        payload={payload}
        repo={repo}
        master={master}
        leaf={leaf}
        history={history}
        status={reading === null ? null : reading.problem === null ? 'pending' : 'unavailable'}
      />
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
        navigation={navigation}
        chosen={treeChosen}
        walk={walk}
        rosterNext={rosterNext}
        onSelect={choose}
        laneRead={laneRead}
        subject={reading ? undefined : navigation?.subject}
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
        laneRead={laneRead}
      />
    </div>
  );
}

// The reading area. Its column is one DOM node for the life of the workspace; only its content is
// swapped, between the unanswered subject's status and the answered subject's reading.
function WorkspaceCenter({
  payload,
  task,
  reading,
  state,
  subject,
  chosen,
  onOpenMember,
  rosterNext,
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
  laneRead: LaneRead<ReviewUnexplainedLane> | null;
}) {
  // The leaf's tree view (MIK-R25), read once per comparison and only for a tree comparison: a
  // dataset review's payload declares no `review:trees:<n>`, so it makes no tree read.
  // The read is pinned to the comparison the payload names (review F11), so the knowledge panel and
  // the cards' planning marks describe the same four trees as the review on screen.
  const comparison = treeComparisonNumber(payload.limitations);
  const leafTrees = useReviewTrees(
    task.repo,
    task.master,
    task.leaf,
    { comparison },
    comparison !== undefined,
  );
  const lane = state.lane;
  return (
    <div
      ref={state.center}
      tabIndex={-1}
      data-testid="review-center-column"
      className={css({ minWidth: 0 })}
    >
      {reading ? (
        <ReadingStatusCenter reading={reading} />
      ) : lane && laneRead ? (
        <UnexplainedLaneCenter
          read={laneRead}
          selection={lane}
          onOpenFile={(path) => state.setLane({ ...lane, path })}
          task={{ ...task, comparison }}
          inventory={payload.source.inventory}
          layout={state.layout}
          gate={gateRead(leafTrees, comparison)}
        />
      ) : (
        <>
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
            onOpenFromCenter={state.openFromCenter}
            onOpenMember={onOpenMember}
            onRosterNext={rosterNext}
            leafTrees={leafTrees}
          />
          <RosterPageNote payload={payload} />
        </>
      )}
    </div>
  );
}

// The gate's items for the lane: the leaf-wide read's worklist, only when that read answered for the
// comparison the lane shows (a worklist of other trees would describe other hunks).
function gateRead(
  leafTrees: ReturnType<typeof useReviewTrees>,
  comparison: number | undefined,
): GateRead {
  if (leafTrees === null || leafTrees.phase === 'loading') return { state: 'reading' };
  if (leafTrees.phase !== 'trees')
    return { state: 'none', detail: 'the leaf-wide read gave no answer' };
  const { trees } = leafTrees;
  if (trees.comparison?.number !== comparison)
    return {
      state: 'none',
      detail: `the leaf-wide read answered comparison ${trees.comparison?.number}`,
    };
  if (!trees.worklist || trees.worklist.source === 'absent')
    return { state: 'none', detail: 'no worklist applies to this comparison' };
  return { state: 'read', worklist: trees.worklist };
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
});

function WorkspaceRail({
  payload,
  repo,
  master,
  leaf,
  state,
  navigation,
  chosen,
  walk,
  rosterNext,
  onSelect,
  laneRead,
  subject,
}: {
  payload: ReviewPayload;
  repo: string;
  master: string;
  leaf: string;
  state: WorkspaceState;
  navigation?: ReviewNavigationState;
  onSelect: (selection: FamilySelection) => void;
  chosen: FamilySelection | null;
  walk: Walk;
  rosterNext: (family: string, side: string, continuation: string) => void;
  laneRead: LaneRead<ReviewUnexplainedLane> | null;
  // The answered subject (none while another is read): an intent marker's unknown-membership target.
  subject?: ReviewNavigationState['subject'];
}) {
  const tree = walkedTreeOf(walk, payload, navigation?.catalogue);
  const families = (
    <FamilyRailContext
      payload={payload}
      state={state}
      walked={tree}
      // While a lane destination is on screen, no family node is the current selection.
      chosen={state.lane ? null : chosen}
      onSelect={onSelect}
      rosterNext={rosterNext}
      selectable={Boolean(navigation?.catalogue.entries?.length)}
      subject={subject}
    />
  );
  return (
    <aside className={rail}>
      <div className={shell}>
        <h2 className={title}>Families & invariants</h2>
        {navigation ? (
          <ReviewNavigation
            {...navigation}
            loadedFamilyIds={tree.entries.map((entry) => entry.family_id)}
            listLoaded={tree.kept.size > 0}
          >
            {families}
          </ReviewNavigation>
        ) : (
          families
        )}
        <RailLaneDestinations laneRead={laneRead} state={state} />
      </div>
      <SourceExplorer
        inventory={payload.source.inventory}
        attribution={sourceAttribution(payload, laneRead)}
        repo={repo}
        master={master}
        leaf={leaf}
        layout={state.layout}
        onLayout={state.setLayout}
        fullFile={state.fullFile}
        onFullFile={state.setFullFile}
        open={state.openPath ?? null}
        onOpen={(path) => {
          state.closePath(path);
          if (path !== null) revealCenter(state.center);
        }}
        showContent={false}
      />
    </aside>
  );
}

// The lane's two destinations after the families, for a tree comparison only.
function RailLaneDestinations({
  laneRead,
  state,
}: {
  laneRead: LaneRead<ReviewUnexplainedLane> | null;
  state: WorkspaceState;
}) {
  if (!laneRead) return null;
  return (
    <LaneDestinations
      read={laneRead}
      selection={state.lane}
      onSelect={(destination) => state.setLane({ destination, path: null })}
    />
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
  rosterNext,
  selectable,
  onSelect,
  subject,
}: {
  payload: ReviewPayload;
  state: WorkspaceState;
  walked: WalkedTree;
  chosen: FamilySelection | null;
  rosterNext: (family: string, side: string, continuation: string) => void;
  selectable: boolean;
  onSelect: (selection: FamilySelection) => void;
  subject?: ReviewNavigationState['subject'];
}) {
  const context = payload.family_context;
  // An intent marker opened this invariant on an unknown membership the tree has no row for.
  const unknown = useInvariantTargetState(subject, context);
  const composed = context?.state === 'recorded' || context?.state === 'partial';
  if (unknown && !composed) return <InvariantTargetState target={unknown} context={context} />;
  // Above a composed tree too: the state is what a follow focuses (review R1 F3), not the tree's
  // auto-selected row, whose name says nothing of it.
  const unknownState = unknown ? <InvariantTargetState target={unknown} context={context} /> : null;
  return composed || walked.entries.length > 0 ? (
    <>
      {unknownState}
      <FamilyTree
        context={context ?? NO_FAMILY_CONTEXT}
        walked={walked}
        selection={chosen}
        onSelect={onSelect}
        onRosterNext={rosterNext}
        query={state.query}
        onQuery={state.setQuery}
        embedded
        tree={treeComparisonNumber(payload.limitations) !== undefined}
      />
    </>
  ) : (
    <FamilyNotComposed context={context} selectable={selectable} />
  );
}

function RosterPageNote({ payload }: { payload: ReviewPayload }) {
  const page = carriedPage(payload);
  if (page?.collection !== 'family_members') return null;
  return (
    <details>
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
): void {
  const { center, focusSelection, treeIntent } = state;
  const pending = reading !== null;
  const unavailable = reading !== null && reading.problem !== null;
  const seen = useRef(payload);
  useEffect(() => {
    const request = focusSelection.current;
    const answered = seen.current !== payload;
    seen.current = payload;
    if (!request || treeIntent.id <= request.after) return;
    // Only a render made after the request can answer it: the answer a late effect of an earlier
    // render sees was on screen before the reader asked.
    if (answered) request.answered = true;
    if (pending) {
      if (unavailable) revealUnavailableRead(request, center.current, treeIntent);
      return;
    }
    if (request.scrollOnly && !request.answered) return;
    focusSelection.current = null;
    landFocus(request, center.current, treeIntent);
  }, [payload, pending, unavailable, treeIntent, center, focusSelection]);
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
  // OR-R033: a stacked in-tree activation reveals its read while focus stays on its row. Other
  // selections and the shared lane/file reveal path keep their existing scroll behavior.
  if (stackedTreeRead(intent)) {
    node?.focus({ preventScroll: true });
    center?.scrollIntoView({ block: 'start' });
  } else node?.focus();
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

// The rail owns its scroll; selecting there restores the common reading position without stealing focus.
function revealCenter(center: React.RefObject<HTMLDivElement | null>): void {
  const surface = center.current?.closest<HTMLElement>('[data-testid="review-surface"]');
  if (surface) surface.scrollTop = 0;
}

// The explorer's labels: a tree comparison's come from the lane's classification, so the two never
// disagree; a dataset review keeps the landed accounting's lists.
function sourceAttribution(
  payload: ReviewPayload,
  laneRead: LaneRead<ReviewUnexplainedLane> | null,
): Record<string, string> {
  if (laneRead)
    return explorerAttribution(
      laneRead,
      payload.source.inventory.entries.map((entry) => entry.path),
    );
  const mapped = new Set(payload.source.attributed_changed_paths);
  const unmapped = new Set(payload.source.unattributed_changed_paths);
  return Object.fromEntries(
    payload.source.inventory.entries.map((entry) => [
      entry.path,
      mapped.has(entry.path)
        ? 'Mapped'
        : unmapped.has(entry.path)
          ? 'Unmapped'
          : 'Attribution unknown',
    ]),
  );
}

// A selection of a row the tree shows, which may be a kept family's: the walked tree keeps (MIK-R39).
function chooseSubject(
  walked: ReviewFamilyContextEntry[],
  selection: FamilySelection,
  state: WorkspaceState,
  navigation?: ReviewNavigationState,
): void {
  const family = walked.find((entry) => entry.family_id === selection.familyId);
  if (!family) {
    state.setChosen(selection);
    return;
  }
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
