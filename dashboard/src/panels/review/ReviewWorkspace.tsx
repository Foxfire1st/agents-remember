// The accepted reading path: family context in the rail, intent and expressions in the center.
// Display state lives above read-cycle unmounts, so paging never resets a reader's choices.
// The workspace stays mounted while another subject is read or could not be read: `reading` swaps
// only the reading area for a statement naming the requested subject (pending, or the owner's failure
// or refusal), so the rail's expansion, scroll, focus and open
// disclosures survive a selection, and the previous subject's reading is never shown under the new
// subject's name.
import { useEffect, useRef, useState } from 'react';
import { css } from '../../../styled-system/css';
import type { ReviewFamilyContext, ReviewPayload, ReviewSelectorKind } from '../../data/review';
import { selectedRevision } from './SubjectReview';
import { carriedPage } from '../../data/review';
import type { ReviewPageRequest } from './ReviewReadCycle';
import { FamilyReviewCenter } from './FamilyReviewCenter';
import { FamilyTree, type FamilySelection } from './FamilyTree';
import { ReviewNavigation, type ReviewNavigationState } from './ReviewNavigation';
import { ReviewScopeHeader } from './ReviewScopeHeader';
import { SourceExplorer, type DiffLayout } from './SourceExplorer';

const workspace = css({
  display: 'grid',
  gridTemplateColumns: 'minmax(17rem, 24rem) minmax(0, 1fr)',
  gap: '1rem',
  alignItems: 'start',
  '@media (max-width: 60rem)': { gridTemplateColumns: 'minmax(0, 1fr)' },
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
  center: React.RefObject<HTMLDivElement | null>;
  // Set by an explicit selection with the element that had focus then; the answer's focus lands on
  // the selected node only if the reader has not moved focus elsewhere in the meantime.
  focusSelection: React.RefObject<{ from: Element | null } | null>;
}

export function useWorkspaceState(): WorkspaceState {
  const [chosen, setChosen] = useState<FamilySelection | null>(null);
  const [query, setQuery] = useState('');
  const [layout, setLayout] = useState<DiffLayout>('split');
  const [fullFile, setFullFile] = useState(false);
  const [openPath, setOpenPath] = useState<string | null>();
  const opener = useRef<HTMLElement | null>(null);
  const center = useRef<HTMLDivElement>(null);
  const focusSelection = useRef<{ from: Element | null } | null>(null);
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
    if (selection) revealCenter(center);
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
    center,
    focusSelection,
  };
}

function selectedContext(
  payload: ReviewPayload,
  chosen: FamilySelection | null,
  selectorId?: string,
): FamilySelection | null {
  const entries = payload.family_context?.entries ?? [];
  if (chosen && entries.some((entry) => entry.family_id === chosen.familyId)) return chosen;
  return initialContext(payload, entries, selectorId);
}

export function ReviewWorkspace({
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
}: {
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
}) {
  useSelectionFocus(payload, state);
  // While unanswered, only the reader's explicit choice is marked: deriving one from the previous
  // payload would mark that subject's family as the requested subject's context.
  const chosen = reading ? state.chosen : selectedContext(payload, state.chosen, selectorId);
  const choose = (selection: FamilySelection) =>
    chooseSubject(payload, selection, state, navigation);
  const rosterNext = (_family: string, _side: string, continuation: string) =>
    onPageSelect({ of: 'family_members', continuation });
  return (
    <div
      className={workspace}
      data-testid="review-workspace"
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
      <WorkspaceRail
        payload={payload}
        repo={repo}
        master={master}
        leaf={leaf}
        state={state}
        navigation={navigation}
        chosen={chosen}
        rosterNext={rosterNext}
        onSelect={choose}
      />
      <WorkspaceCenter
        payload={payload}
        reading={reading}
        state={state}
        subject={navigation?.subject}
        chosen={chosen}
        onOpenMember={(familyId, memberRevisionId) => choose({ familyId, memberRevisionId })}
        rosterNext={rosterNext}
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
}: {
  payload: ReviewPayload;
  reading: ReadingStatus | null;
  state: WorkspaceState;
  subject?: ReviewNavigationState['subject'];
  chosen: FamilySelection | null;
  onOpenMember: (familyId: string, memberRevisionId: string) => void;
  rosterNext: (family: string, side: string, continuation: string) => void;
}) {
  return (
    <div
      ref={state.center}
      tabIndex={-1}
      data-testid="review-center-column"
      className={css({ minWidth: 0 })}
    >
      {reading ? (
        <ReadingStatusCenter reading={reading} />
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
          />
          <RosterPageNote payload={payload} />
        </>
      )}
    </div>
  );
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
  rosterNext,
  onSelect,
}: {
  payload: ReviewPayload;
  repo: string;
  master: string;
  leaf: string;
  state: WorkspaceState;
  navigation?: ReviewNavigationState;
  onSelect: (selection: FamilySelection) => void;
  chosen: FamilySelection | null;
  rosterNext: (family: string, side: string, continuation: string) => void;
}) {
  const context = payload.family_context;
  return (
    <aside className={rail}>
      <div className={shell}>
        <h2 className={title}>Families & invariants</h2>
        {navigation ? (
          <ReviewNavigation
            {...navigation}
            loadedFamilyIds={context?.entries.map((entry) => entry.family_id)}
          >
            <FamilyRailContext
              payload={payload}
              state={state}
              chosen={chosen}
              onSelect={onSelect}
              rosterNext={rosterNext}
              selectable={Boolean(navigation.catalogue.entries?.length)}
            />
          </ReviewNavigation>
        ) : (
          <FamilyRailContext
            payload={payload}
            state={state}
            chosen={chosen}
            onSelect={onSelect}
            rosterNext={rosterNext}
            selectable={false}
          />
        )}
      </div>
      <SourceExplorer
        inventory={payload.source.inventory}
        attribution={sourceAttribution(payload)}
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

function FamilyRailContext({
  payload,
  state,
  chosen,
  rosterNext,
  selectable,
  onSelect,
}: {
  payload: ReviewPayload;
  state: WorkspaceState;
  chosen: FamilySelection | null;
  rosterNext: (family: string, side: string, continuation: string) => void;
  selectable: boolean;
  onSelect: (selection: FamilySelection) => void;
}) {
  const context = payload.family_context;
  return context && (context.state === 'recorded' || context.state === 'partial') ? (
    <FamilyTree
      context={context}
      selection={chosen}
      onSelect={onSelect}
      onRosterNext={rosterNext}
      query={state.query}
      onQuery={state.setQuery}
      embedded
    />
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

function useSelectionFocus(payload: ReviewPayload, state: WorkspaceState): void {
  const { center, focusSelection } = state;
  useEffect(() => {
    const request = focusSelection.current;
    if (!request) return;
    focusSelection.current = null;
    // A reader who moved focus while the subject was pending keeps it: only focus still on the
    // selecting control, or lost with an unmounted node (`body`), is moved to the selection.
    const active = document.activeElement;
    if (active !== null && active !== document.body && active !== request.from) return;
    const workspace = center.current?.closest('[data-testid="review-workspace"]');
    const selected = workspace?.querySelector<HTMLElement>('[data-tree-node][aria-current="true"]');
    (selected ?? center.current)?.focus();
  }, [payload, center, focusSelection]);
}

// The rail owns its scroll; selecting there restores the common reading position without stealing focus.
function revealCenter(center: React.RefObject<HTMLDivElement | null>): void {
  const surface = center.current?.closest<HTMLElement>('[data-testid="review-surface"]');
  if (surface) surface.scrollTop = 0;
}

function sourceAttribution(payload: ReviewPayload): Record<string, string> {
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

function chooseSubject(
  payload: ReviewPayload,
  selection: FamilySelection,
  state: WorkspaceState,
  navigation?: ReviewNavigationState,
): void {
  const family = payload.family_context?.entries.find(
    (entry) => entry.family_id === selection.familyId,
  );
  if (!family) {
    state.setChosen(selection);
    return;
  }
  const member = [...family.before.members, ...family.after.members].find(
    (row) => row.invariant_revision_id === selection.memberRevisionId,
  );
  if (selection.memberRevisionId === undefined) {
    navigation?.onSelect({ kind: 'family', id: selection.familyId }, selection);
  } else if (member?.invariant_id) {
    navigation?.onSelect({ kind: 'invariant', id: member.invariant_id }, selection);
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
