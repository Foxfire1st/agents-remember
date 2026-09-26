// The accepted reading path: family context in the rail, intent and expressions in the center.
// Display state lives above read-cycle unmounts, so paging never resets a reader's choices.
import { useEffect, useRef, useState } from 'react';
import { css } from '../../../styled-system/css';
import type { ReviewFamilyContext, ReviewPayload, ReviewSelectorKind } from '../../data/review';
import { selectedRevision } from './SubjectReview';
import { carriedPage } from '../../data/review';
import type { ReviewPageRequest } from './ReviewReadCycle';
import { FamilyReviewCenter } from './FamilyReviewCenter';
import { FamilyTree, type FamilySelection } from './FamilyTree';
import { ReviewNavigation, type ReviewNavigationState } from './ReviewNavigation';
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

function ScopeHeader({
  payload,
  repo,
  master,
  leaf,
  history,
}: {
  payload: ReviewPayload;
  repo: string;
  master: string;
  leaf: string;
  history?: 'recorded';
}) {
  const inventory = payload.source.inventory;
  const comparison = payload.comparison;
  const recordLabel = recordLabelOf(payload, history);
  return (
    <header
      className={css({
        gridColumn: '1 / -1',
        borderBottom: '1px solid var(--grid)',
        paddingBottom: '0.75rem',
        minWidth: 0,
      })}
      data-testid="review-scope-header"
    >
      <p className={muted} data-testid="review-scope-record">
        {recordLabel}·{' '}
        {inventory.state === 'unavailable'
          ? 'Source inventory unavailable'
          : `${inventory.listed_total} changed files${inventory.partial ? ' · partial inventory' : ''}`}{' '}
        · Read-only
      </p>
      {payload.staleness.state !== 'current' ? (
        <p className={muted} data-testid="review-currentness-status">
          {payload.staleness.state === 'stale'
            ? 'Comparison has changed · refresh before relying on this view.'
            : 'Currentness not measured · inspect the comparison details.'}
        </p>
      ) : null}
      <details>
        <summary>Comparison details</summary>
        <p className={muted}>
          {repo} · {master} · <span data-testid="review-scope-task">{leaf}</span>
        </p>
        <p className={muted} data-testid="review-scope-comparison">
          {comparison
            ? `Comparison ${comparison.reference} · policy ${comparison.policy_version}`
            : 'No knowledge comparison was made for this read.'}
        </p>
        <p className={muted}>
          Before {inventory.before_code_tree_id ?? 'not recorded'} → after{' '}
          {inventory.after_code_tree_id ?? 'not recorded'}
        </p>
        <p className={muted} data-testid="review-scope-families">
          {payload.family_context
            ? `${payload.family_context.families_returned} of ${payload.family_context.families_total} family contexts read · ${payload.family_context.state}`
            : 'Family context was not supplied.'}
        </p>
      </details>
    </header>
  );
}

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
  focusSelection: React.RefObject<boolean>;
}

export function useWorkspaceState(): WorkspaceState {
  const [chosen, setChosen] = useState<FamilySelection | null>(null);
  const [query, setQuery] = useState('');
  const [layout, setLayout] = useState<DiffLayout>('split');
  const [fullFile, setFullFile] = useState(false);
  const [openPath, setOpenPath] = useState<string | null>();
  const opener = useRef<HTMLElement | null>(null);
  const center = useRef<HTMLDivElement>(null);
  const focusSelection = useRef(false);
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
  const chosen = selectedContext(payload, state.chosen, selectorId);
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
      <ScopeHeader payload={payload} repo={repo} master={master} leaf={leaf} history={history} />
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
      <div
        ref={state.center}
        tabIndex={-1}
        data-testid="review-center-column"
        className={css({ minWidth: 0 })}
      >
        <FamilyReviewCenter
          payload={payload}
          subject={navigation?.subject}
          selection={chosen}
          layout={state.layout}
          onLayout={state.setLayout}
          fullFile={state.fullFile}
          onFullFile={state.setFullFile}
          openPath={state.openPath}
          onOpenPath={state.closePath}
          onOpenFromCenter={state.openFromCenter}
          onOpenMember={(familyId, memberRevisionId) => choose({ familyId, memberRevisionId })}
          onRosterNext={rosterNext}
        />
        <RosterPageNote payload={payload} />
      </div>
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

function recordLabelOf(payload: ReviewPayload, history?: 'recorded'): string {
  if (payload.limitations.includes('history:reconstructed-recorded-endpoints'))
    return 'Reconstructed from recorded endpoints';
  return history === 'recorded' ? 'Recorded task comparison' : 'Live task comparison';
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
    if (!focusSelection.current) return;
    const workspace = center.current?.closest('[data-testid="review-workspace"]');
    const selected = workspace?.querySelector<HTMLElement>('[data-tree-node][aria-current="true"]');
    (selected ?? center.current)?.focus();
    focusSelection.current = false;
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
