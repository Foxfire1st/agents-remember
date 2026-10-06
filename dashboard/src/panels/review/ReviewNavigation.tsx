// Recorded subject navigation. The catalogue supplies identity; the review supplies family content.
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { useReviewCatalogue } from '../../data/useReviewCatalogue';
import type { FamilySelection } from './FamilyTree';
import type { ReviewPageRequest } from './ReviewReadCycle';
import { css } from '../../../styled-system/css';
import type { ReviewEntry, ReviewPayload, ReviewSelectorKind } from '../../data/review';
import type { ReviewCatalogueRead } from '../../data/useReviewCatalogue';

export interface ReviewSubject {
  kind: ReviewSelectorKind;
  id: string;
}
// How a selection treats the family tree the reader has walked (MIK-R39). By default a selection
// starts the tree afresh; `keepTree` marks the selection of a row the tree shows. `page` asks for a
// page of the subject's review (a kept family's roster continuation).
export interface SelectionOptions {
  keepTree?: boolean;
  page?: ReviewPageRequest;
}

export interface ReviewNavigationState {
  catalogue: ReviewCatalogueRead & { refresh: () => void };
  subject?: ReviewSubject;
  onSelect: (
    subject: ReviewSubject | undefined,
    context?: FamilySelection,
    options?: SelectionOptions,
  ) => void;
}

// The navigation the surface drives: the state above plus the two signals the read cycle needs.
export interface ReviewNavigation extends ReviewNavigationState {
  // True while the reviewer was opened with no subject, the catalogue that chooses its first one has
  // not answered yet, and `SUBJECT_HOLD_MS` has not passed. The surface holds its first read that
  // long, so a prompt catalogue costs one subject read instead of a whole-task read and then a
  // subject read -- and a slow or stalled catalogue never withholds the task-context review and its
  // source explorer for longer than the bound.
  settling: boolean;
  // Report the compared snapshot pair the surface is showing. The first pair it reports is the one
  // the entry catalogue describes; a different pair later (a refresh that reached a new candidate
  // generation) re-reads the catalogue for it.
  observeComparison: (snapshots: string | undefined) => void;
  // The reader acted inside the reviewer (pointer, key, wheel or touch). Once the bounded wait has
  // released a read, the subject on screen becomes the reader's own choice, so a catalogue that
  // answers later fills the navigation without moving the reader to its first family. A reader who
  // has not acted yet is still moved there: that is the landing a prompt catalogue gives.
  engage: () => void;
}

const row = css({
  display: 'block',
  width: '100%',
  textAlign: 'left',
  padding: '0.65rem',
  color: 'ink',
  borderBottom: '1px solid var(--grid)',
  background: 'transparent',
  _hover: { background: 'color-mix(in oklab, var(--amber) 8%, transparent)' },
  '&[aria-current=true]': {
    color: 'amber',
    background: 'color-mix(in oklab, var(--amber) 16%, transparent)',
  },
});
const summary = css({ color: 'muted', fontSize: '0.75rem', margin: '0.5rem 0' });

function SubjectButton({
  entry,
  subject,
  onSelect,
}: {
  entry: ReviewEntry;
  subject?: ReviewSubject;
  onSelect: ReviewNavigationState['onSelect'];
}) {
  return (
    <button
      type="button"
      className={row}
      data-testid="review-catalogue-subject"
      data-subject-kind={entry.selector_kind}
      data-subject-id={entry.selector_id}
      aria-current={
        subject?.kind === entry.selector_kind && subject.id === entry.selector_id
          ? 'true'
          : undefined
      }
      onClick={() => onSelect({ kind: entry.selector_kind, id: entry.selector_id })}
    >
      {entry.selector_kind === 'family' ? '▸ ' : ''}
      {entry.label}
      {entry.presence === 'before_only'
        ? ' · before only'
        : entry.presence === 'after_only'
          ? ' · after only'
          : ''}
    </button>
  );
}

export function ReviewNavigation({
  catalogue,
  subject,
  onSelect,
  children,
  loadedFamilyIds = [],
  listLoaded = false,
}: ReviewNavigationState & {
  children?: React.ReactNode;
  loadedFamilyIds?: string[];
  // The tree holds a kept family: the catalogue then lists every family the tree shows (MIK-R39).
  listLoaded?: boolean;
}) {
  const families = (catalogue.entries ?? []).filter((entry) => entry.selector_kind === 'family');
  const invariants = (catalogue.entries ?? []).filter(
    (entry) => entry.selector_kind === 'invariant',
  );
  return (
    <section data-testid="review-catalogue-navigation" aria-label="Recorded review subjects">
      <p className={summary}>{catalogueLabel(catalogue)}</p>
      <CatalogueFamilies
        entries={families}
        subject={subject}
        onSelect={onSelect}
        loadedFamilyIds={loadedFamilyIds}
        listLoaded={listLoaded}
      >
        {children}
      </CatalogueFamilies>
      {invariants.length ? (
        <details data-testid="review-all-invariants">
          <summary>All {invariants.length} recorded invariants</summary>
          {invariants.map((entry) => (
            <SubjectButton
              key={entry.selector_id}
              entry={entry}
              subject={subject}
              onSelect={onSelect}
            />
          ))}
        </details>
      ) : null}
      {catalogue.empty ? <p className={summary}>No subjects recorded in this comparison.</p> : null}
      {catalogue.problem ? (
        <details data-testid="review-catalogue-problem">
          <summary>Why attribution is unavailable</summary>
          <p>
            {catalogue.problem.code}: {catalogue.problem.detail}
          </p>
          <p>{catalogue.problem.nextAction}</p>
        </details>
      ) : null}
      <div
        className={css({ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.6rem' })}
      >
        <button type="button" data-testid="review-task-source" onClick={() => onSelect(undefined)}>
          All source changes
        </button>
        <button type="button" onClick={catalogue.refresh}>
          Refresh subjects
        </button>
      </div>
    </section>
  );
}

// The longest the reviewer's first read waits for the catalogue to choose a subject. The catalogue
// read is one indexed listing (tens of milliseconds on a real task), so the bound only matters when it
// is slow or stalled; then the task-context review is read at the bound, exactly as it is when the
// catalogue refuses. When the catalogue does answer, its first subject is selected only if the reader
// has not started working in the task-context view (`engage`); otherwise it only fills the navigation.
export const SUBJECT_HOLD_MS = 750;

// An explicit source-only choice remains source-only when the catalogue refreshes.
export function useReviewNavigation(target: {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: 'recorded';
}): ReviewNavigation {
  const [generation, setGeneration] = useState(0);
  // The snapshot pair first reported for this task context and record; a pair reported under
  // another context is that context's first, not a new generation of this one.
  const comparison = `${target.repo}/${target.master}/${target.leaf}/${target.history ?? 'live'}`;
  const seen = useRef<{ comparison: string; snapshots: string } | undefined>(undefined);
  const catalogue = useReviewCatalogue({
    repo: target.repo,
    master: target.master,
    leaf: target.leaf,
    history: target.history,
    generation,
  });
  const key = JSON.stringify(target);
  const [choice, setChoice] = useState<{ key: string; subject?: ReviewSubject }>();
  const chosen = choice?.key === key;
  const subject = chosen ? choice.subject : initialSubject(target, catalogue.entries);
  const answered = catalogue.entries !== undefined || catalogue.problem !== undefined;
  const waiting = !chosen && !(target.selectorKind && target.selectorId) && !answered;
  const [expired, setExpired] = useState<string>();
  useEffect(() => {
    if (!waiting) return undefined;
    const timer = setTimeout(() => setExpired(key), SUBJECT_HOLD_MS);
    return () => clearTimeout(timer);
  }, [waiting, key]);
  const settling = waiting && expired !== key;
  const observeComparison = useCallback(
    (snapshots: string | undefined) => {
      const previous = seen.current;
      if (snapshots === undefined) return;
      if (previous?.comparison === comparison && previous.snapshots === snapshots) return;
      seen.current = { comparison, snapshots };
      if (previous?.comparison === comparison) setGeneration((value) => value + 1);
    },
    [comparison],
  );
  const engage = useCallback(() => {
    if (settling) return;
    // Functional so an explicit selection queued by the same gesture is never overwritten.
    setChoice((previous) => (previous?.key === key ? previous : { key, subject }));
  }, [settling, key, subject]);
  return {
    catalogue,
    subject,
    onSelect: (subject) => setChoice({ key, subject }),
    settling,
    observeComparison,
    engage,
  };
}

// The gestures that count as the reader working in the reviewer, observed (never handled) on the
// surface's root: they only tell the navigation the subject on screen is now the reader's own.
const ENGAGING_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const;

export function useReaderEngagement(
  root: React.RefObject<HTMLElement | null>,
  engage: ReviewNavigation['engage'],
): void {
  useEffect(() => {
    const node = root.current;
    if (!node) return undefined;
    for (const name of ENGAGING_EVENTS)
      node.addEventListener(name, engage, { capture: true, passive: true });
    return () => {
      for (const name of ENGAGING_EVENTS) node.removeEventListener(name, engage, { capture: true });
    };
  }, [root, engage]);
}

// Report the snapshot pair the surface shows to the navigation that keys its catalogue on it.
export function useObservedComparison(
  observeComparison: ReviewNavigation['observeComparison'],
  shown: ReviewPayload | null,
): void {
  const identity = shown?.comparison;
  const snapshots = identity?.knowledge_compared
    ? `${identity.before_snapshot_digest ?? ''}:${identity.after_snapshot_digest ?? ''}`
    : undefined;
  useEffect(() => observeComparison(snapshots), [observeComparison, snapshots]);
}

function catalogueLabel(catalogue: ReviewNavigationState['catalogue']): string {
  if (catalogue.loading || (!catalogue.entries && !catalogue.problem))
    return 'Reading recorded subjects…';
  if (catalogue.problem) return 'Attribution unavailable';
  return `${catalogue.familyTotal ?? 0} families · ${catalogue.invariantTotal ?? 0} invariants`;
}

function initialSubject(
  target: { selectorKind?: ReviewSelectorKind; selectorId?: string },
  entries?: ReviewEntry[],
): ReviewSubject | undefined {
  if (target.selectorKind && target.selectorId)
    return { kind: target.selectorKind, id: target.selectorId };
  const first = entries?.find((entry) => entry.selector_kind === 'family') ?? entries?.[0];
  return first ? { kind: first.selector_kind, id: first.selector_id } : undefined;
}

function CatalogueFamilies({
  entries,
  subject,
  onSelect,
  loadedFamilyIds,
  listLoaded,
  children,
}: {
  entries: ReviewEntry[];
  subject?: ReviewSubject;
  onSelect: ReviewNavigationState['onSelect'];
  loadedFamilyIds: string[];
  listLoaded: boolean;
  children?: React.ReactNode;
}) {
  const button = (entry: ReviewEntry) => (
    <SubjectButton key={entry.selector_id} entry={entry} subject={subject} onSelect={onSelect} />
  );
  // The tree is one keyed child among the rows, so it keeps its identity (its open disclosures and
  // scroll) however the walk moves the place it stands in. It stands in the place of the first
  // family it shows. While it shows a kept family, every family it shows also keeps its catalogue row
  // (without badges): choosing it starts the tree afresh with that family alone (MIK-R39).
  const nodes: React.ReactNode[] = [];
  let placed = false;
  for (const entry of entries) {
    const loaded = loadedFamilyIds.includes(entry.selector_id);
    if (loaded && !placed) {
      nodes.push(<Fragment key="tree">{children}</Fragment>);
      placed = true;
    }
    if (!loaded || listLoaded) nodes.push(button(entry));
  }
  if (!placed) nodes.push(<Fragment key="tree">{children}</Fragment>);
  return <>{nodes}</>;
}
