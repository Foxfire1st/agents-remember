import { useRef, useState } from 'react';
import { css } from '../../../styled-system/css';
import {
  useObservedComparison,
  useReaderEngagement,
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
import type { FamilySelection } from './FamilyTree';
import { type ReviewPageRequest, targetKeyOf, useReviewReadCycle } from './ReviewReadCycle';
import { ReviewReadCache, ReviewReadCacheContext } from './ReviewReadCache';
import { ReviewTechnicalDetails } from './ReviewRecordPanes';
import { ReviewRefresh, generationOf } from './ReviewRefresh';
import {
  type ReviewRead,
  ReviewOutcomeRegion,
  ReviewProblemBlock,
  problemOf,
  shownPayload,
} from './ReviewOutcome';
import { type ReadingStatus, ReviewWorkspace, useWorkspaceState } from './ReviewWorkspace';

export interface ReviewTarget {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: ReviewHistory;
}

const reviewShell = css({
  background: 'bg',
  color: 'ink',
  fontFamily: 'mono',
  fontSize: '0.83rem',
  lineHeight: '1.65',
  padding: '1.2rem',
  overflowWrap: 'anywhere',
  '@media (max-width: 40rem)': { padding: '0.7rem' },
  '& summary': { cursor: 'pointer', color: 'muted', fontSize: '0.75rem', padding: '0.3rem 0' },
  '& details[open] > summary': { color: 'cyan' },
  '& button:not([data-tree-node]):not([data-path]), & select, & input': {
    background: 'bgPanel',
    color: 'ink',
    border: '1px solid var(--grid)',
    borderRadius: '2px',
    font: 'inherit',
    fontSize: '0.75rem',
    padding: '0.4rem 0.6rem',
    cursor: 'pointer',
    maxWidth: '100%',
  },
  '& button:focus-visible, & select:focus-visible, & input:focus-visible, & summary:focus-visible':
    {
      outline: '2px solid var(--amber)',
      outlineOffset: '2px',
    },
  '& button:hover': { color: 'amber' },
  '& [data-testid=review-center-column]': { outlineOffset: '3px' },
});

function subjectLabel(
  selectorKind: ReviewSelectorKind | undefined,
  selectorId: string | undefined,
  instead: ReviewFailure | null,
): string {
  if (instead !== null) return 'whole task (no subject selected)';
  if (selectorKind && selectorId) return `${selectorKind} ${selectorId}`;
  return 'whole task (no subject selected)';
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

// The workspace and its record panes. `shown` is the answer for the subject on screen; while another
// subject is pending, or its read failed or was refused, `frame` (the last admitted payload of this
// task context) keeps the shell -- scope, navigation, family rail, source explorer and the open
// disclosures -- mounted, and the reading area alone states the requested subject's status. Nothing
// of the frame's subject is rendered as the requested subject's reading.
function ReviewPanes({
  shown,
  frame,
  reading,
  selection,
  onSelect,
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  history,
  workspace,
  navigation,
}: {
  shown: ReviewPayload | null;
  frame: ReviewPayload | null;
  reading: ReadingStatus | null;
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: ReviewHistory;
  workspace: ReturnType<typeof useWorkspaceState>;
  navigation: ReviewNavigationState;
}) {
  const payload = shown ?? (reading ? frame : null);
  if (payload === null) return null;
  return (
    <>
      <ReviewWorkspace
        payload={payload}
        reading={shown === null ? reading : null}
        repo={repo}
        master={master}
        leaf={leaf}
        selectorKind={selectorKind}
        selectorId={selectorId}
        history={history}
        onPageSelect={onSelect}
        state={workspace}
        navigation={navigation}
      />
      <ReviewTechnicalDetails
        payload={shown}
        unanswered={{ label: reading?.label ?? '', unavailable: Boolean(reading?.problem) }}
        paging={
          shown === null ? null : (
            <PageControls payload={shown} selection={selection} onSelect={onSelect} />
          )
        }
      />
    </>
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

function ReviewHeader({
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  instead,
  history,
  refresh,
  onBack,
}: {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  instead: ReviewFailure | null;
  history?: ReviewHistory;
  refresh?: React.ReactNode;
  onBack: () => void;
}) {
  return (
    <>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '0.5rem',
          alignItems: 'center',
          marginBottom: '0.75rem',
        }}
      >
        <button type="button" onClick={onBack} data-testid="review-back">
          ← back
        </button>
        <strong>Intent review · {leaf}</strong>
        <span
          style={{ color: 'var(--muted)', minWidth: 0, overflowWrap: 'anywhere' }}
          data-testid="review-subject"
        >
          <details>
            <summary>Task and subject identifiers</summary>
            {repo} · {master} · {leaf} · {subjectLabel(selectorKind, selectorId, instead)}
          </details>
        </span>
        {refresh}
      </div>
      {history === 'recorded' ? (
        <span data-testid="review-history" className={css({ color: 'muted', fontSize: '0.75rem' })}>
          Historical task comparison
        </span>
      ) : null}
    </>
  );
}

function useSurface({
  repo,
  master,
  leaf,
  selectorKind: initialKind,
  selectorId: initialId,
  history,
  onBack,
}: ReviewTarget & { onBack: () => void }) {
  const navigation = useReviewNavigation({
    repo,
    master,
    leaf,
    selectorKind: initialKind,
    selectorId: initialId,
    history,
  });
  const selectorKind = navigation.subject?.kind;
  const selectorId = navigation.subject?.id;
  const [instead, setInstead] = useState<ReviewFailure | null>(null);
  const [selection, setSelection] = useState<ReviewPageRequest | undefined>(undefined);
  const workspace = useWorkspaceState();
  // One cache per mounted surface: reclaimed with it (see `ReviewReadCache`).
  const [cache] = useState(() => new ReviewReadCache());
  const selectSubject = (subject: ReviewSubject | undefined, context?: FamilySelection) => {
    workspace.focusSelection.current = { from: document.activeElement };
    navigation.onSelect(subject);
    setInstead(null);
    setSelection(undefined);
    workspace.setChosen(context ?? null);
  };
  const cycle = useReviewReadCycle({
    repo,
    master,
    leaf,
    selectorKind,
    selectorId,
    history,
    instead,
    selection,
    hold: navigation.settling,
    cache,
  });
  const { read, retained, frame } = cycle;
  const key = targetKeyOf(
    repo,
    master,
    leaf,
    instead,
    selectorKind,
    selectorId,
    selection,
    history,
  );
  const coherent = retained !== null && retained.key === key ? retained.payload : null;
  const shown = shownPayload(read, coherent);
  useObservedComparison(navigation.observeComparison, shown);
  const asked = instead === null ? navigation.subject : undefined;
  const problem = problemOf(read);
  // The owner's failure or refusal for the requested subject, rendered in the reading area.
  const problemBlock = (label: string) =>
    problem === null ? null : (
      <ReviewProblemBlock
        origin={read.phase === 'refused' ? 'refusal' : 'failure'}
        problem={problem}
        subject={label}
        onRetry={retryFor(read, cycle.refresh)}
        onOpenTaskContext={insteadFor(read, problem, instead, setInstead)}
      />
    );
  return {
    ...{ repo, master, leaf, selectorKind, selectorId, history, onBack, navigation, selectSubject },
    ...{ instead, setInstead, selection, setSelection, workspace, cache, frame, coherent, shown },
    ...{ read, carried: cycle.carried, refresh: cycle.refresh, problem },
    reading:
      frame === null || shown !== null
        ? null
        : readingStatusOf(read, key, asked, navigation.catalogue, problemBlock),
  };
}

export function ReviewSurface(props: ReviewTarget & { onBack: () => void }) {
  const surface = useSurface(props);
  const { repo, master, leaf, history, read, shown, reading, refresh, instead } = surface;
  // Any reading gesture makes the subject on screen the reader's own: a catalogue that answers late
  // then fills the navigation without moving the reader (see `useReviewNavigation`).
  const root = useRef<HTMLDivElement>(null);
  useReaderEngagement(root, surface.navigation.engage);
  return (
    <div
      ref={root}
      className={reviewShell}
      data-testid="review-surface"
      data-comparison={shown?.comparison?.reference}
      data-review-target={`${repo}/${master}/${leaf}`}
      data-review-history={history ?? 'live'}
      data-review-pending={reading && !reading.problem ? reading.subject : undefined}
      data-review-unavailable={reading?.problem ? reading.subject : undefined}
      style={{ height: '100%', minHeight: 0, minWidth: 0, overflowY: 'auto' }}
    >
      <ReviewReadCacheContext.Provider value={surface.cache}>
        <ReviewHeader
          repo={repo}
          master={master}
          leaf={leaf}
          selectorKind={surface.selectorKind}
          selectorId={surface.selectorId}
          instead={instead}
          history={history}
          onBack={surface.onBack}
          refresh={
            <ReviewRefresh
              onRefresh={refresh}
              busy={read.phase === 'loading'}
              generation={generationOf(read, surface.carried, shown)}
            />
          }
        />
        <ReviewOutcomeRegion
          read={read}
          instead={instead}
          shown={shown}
          lastCoherent={read.phase === 'failed' ? surface.coherent : null}
          onRetry={retryFor(read, refresh)}
          onOpenTaskContext={insteadFor(read, surface.problem, instead, surface.setInstead)}
          readingInWorkspace={reading !== null}
        />
        <ReviewPanes
          shown={shown}
          frame={surface.frame}
          reading={reading}
          selection={surface.selection}
          onSelect={surface.setSelection}
          repo={repo}
          master={master}
          leaf={leaf}
          selectorKind={surface.selectorKind}
          selectorId={surface.selectorId}
          history={history}
          workspace={surface.workspace}
          navigation={{ ...surface.navigation, onSelect: surface.selectSubject }}
        />
      </ReviewReadCacheContext.Provider>
    </div>
  );
}
