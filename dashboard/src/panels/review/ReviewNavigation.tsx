// Recorded subject navigation. The catalogue supplies identity; the review supplies family content.
import { useCallback, useEffect, useRef, useState } from 'react';
import { useReviewCatalogue } from '../../data/useReviewCatalogue';
import type { FamilySelection } from './FamilyTree';
import type { ReviewPageRequest } from './ReviewReadCycle';
import { outlineCounts, firstOutlineChange } from './changeTriage';
import { useTreeOrder } from './triageOrderPreference';
import {
  comparisonGenerationOf,
  sameGeneration,
  type ComparisonGeneration,
} from './ReviewReadCache';
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
  // Report the comparison generation the surface is showing (code trees and known snapshots).
  // The first generation it reports is the one
  // the entry catalogue describes; a different pair later (a refresh that reached a new candidate
  // generation) re-reads the catalogue for it.
  observeComparison: (snapshots: ComparisonGeneration | undefined) => void;
  // The reader acted inside the reviewer (pointer, key, wheel or touch). Once the bounded wait has
  // released a read, the subject on screen becomes the reader's own choice, so a catalogue that
  // answers later fills the navigation without moving the reader to its first family. A reader who
  // has not acted yet is still moved there: that is the landing a prompt catalogue gives.
  engage: () => void;
}

const summary = css({ color: 'muted', fontSize: '0.75rem', margin: '0.5rem 0' });
export interface NavigationCounts {
  unexplained: number | '…' | 'unavailable';
  unknown: number | '…' | 'unavailable';
}
export function ReviewNavigation({
  catalogue,
  children,
  summaryCounts,
  codeSummary,
  destinations,
  walkedEntries = [],
}: ReviewNavigationState & {
  children?: React.ReactNode;
  summaryCounts?: NavigationCounts;
  codeSummary?: string;
  destinations?: React.ReactNode;
  walkedEntries?: import('../../data/review').ReviewFamilyContextEntry[];
}) {
  const { text, reason } = navigationSummary(catalogue, walkedEntries, summaryCounts);
  return (
    <section data-testid="review-catalogue-navigation" aria-label="Families and invariants">
      <h2 className={css({ fontSize: '0.85rem', margin: 0 })}>Families &amp; invariants</h2>
      <p className={summary} data-testid="review-navigation-summary">
        {text}{codeSummary ? ` · ${codeSummary}` : ''}
      </p>
      {reason ? (
        <p className={summary} data-testid="review-outline-unavailable">
          {reason}
        </p>
      ) : null}
      {children}
      {destinations}
    </section>
  );
}

function navigationSummary(
  catalogue: ReviewNavigationState['catalogue'],
  walkedEntries: import('../../data/review').ReviewFamilyContextEntry[],
  lanes?: NavigationCounts,
) {
  const reading = catalogue.loading || (!catalogue.entries && !catalogue.problem);
  const counts = reading ? undefined : outlineCounts(catalogue.outline, walkedEntries);
  const placeholder = reading ? '…' : 'unavailable';
  const text = summaryText(counts, lanes, placeholder);
  return { text, reason: reading || counts ? undefined : unavailableReason(catalogue) };
}
function summaryText(
  counts: ReturnType<typeof outlineCounts>,
  lanes: NavigationCounts | undefined,
  placeholder: string,
): string {
  const families = counts?.families ?? placeholder;
  const invariants = counts?.invariants ?? placeholder;
  return `${families} families changed · ${invariants} invariants changed · ${attributionSummary(lanes)}`;
}

function attributionSummary(lanes?: NavigationCounts): string {
  if (lanes?.unexplained === 'unavailable' || lanes?.unknown === 'unavailable')
    return 'the change set could not be measured';
  return `${lanes?.unexplained ?? '…'} files without an invariant · ${lanes?.unknown ?? '…'} files of unknown attribution`;
}

function unavailableReason(catalogue: ReviewNavigationState['catalogue']): string {
  return (
    catalogue.outline?.detail ??
    catalogue.problem?.detail ??
    'The memory is not converted; change facts are unavailable.'
  );
}

// The longest the reviewer's first read waits for the catalogue to choose a subject. The catalogue
// read is one indexed listing (tens of milliseconds on a real task), so the bound only matters when it
// is slow or stalled; then the task-context review is read at the bound, exactly as it is when the
// catalogue refuses. Subject chosen at that point, the answer on screen is the task context, so a
// catalogue that arrives later fills the navigation and does not select a subject on its own: one
// default open is one main read (L177-R1-07). The reader moves on by choosing, as always.
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
  const [identity, setIdentity] = useState<{ comparison: string; value: ComparisonGeneration }>();
  // The generation first reported for this task context and record; a generation reported under
  // another context is that context's first, not a new generation of this one.
  const comparison = `${target.repo}/${target.master}/${target.leaf}/${target.history ?? 'live'}`;
  const seen = useRef<{ comparison: string; snapshots: ComparisonGeneration } | undefined>(
    undefined,
  );
  const catalogue = useReviewCatalogue({
    repo: target.repo,
    master: target.master,
    leaf: target.leaf,
    history: target.history,
    generation,
    comparisonGeneration: identityForTask(identity, comparison),
  });
  const order = useTreeOrder();
  const key = JSON.stringify(target);
  const [choice, setChoice] = useState<{ key: string; subject?: ReviewSubject }>();
  const chosen = choice?.key === key;
  const [expired, setExpired] = useState<string>();
  // Past the bound the task-context read is the open's read: a catalogue that arrives afterwards
  // fills the navigation and selects nothing on its own, so one default open is one main read
  // (L177-R1-07). A choice the reader makes is still theirs and still wins.
  const subject = chosen
    ? choice.subject
    : expired === key
      ? undefined
      : initialSubject(target, catalogue.entries, catalogue.outline, order);
  const answered = catalogue.entries !== undefined || catalogue.problem !== undefined;
  const waiting = !chosen && !(target.selectorKind && target.selectorId) && !answered;
  useEffect(() => {
    if (!waiting) return undefined;
    const timer = setTimeout(() => setExpired(key), SUBJECT_HOLD_MS);
    return () => clearTimeout(timer);
  }, [waiting, key]);
  const settling = waiting && expired !== key;
  const observeComparison = useCallback(
    (snapshots: ComparisonGeneration | undefined) => {
      const previous = seen.current;
      if (snapshots === undefined) return;
      if (previous?.comparison === comparison && sameGeneration(previous.snapshots, snapshots)) {
        if (previous.snapshots.snapshots === undefined && snapshots.snapshots !== undefined) {
          seen.current = { comparison, snapshots };
          setIdentity({ comparison, value: snapshots });
        }
        return;
      }
      seen.current = { comparison, snapshots };
      setIdentity({ comparison, value: snapshots });
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

// Report the complete generation to the navigation; code-only moves also invalidate the catalogue.
export function useObservedComparison(
  observeComparison: ReviewNavigation['observeComparison'],
  shown: ReviewPayload | null,
): void {
  const trees = shown?.source.inventory;
  const identity = shown?.comparison;
  const beforeTree = trees?.before_code_tree_id;
  const afterTree = trees?.after_code_tree_id;
  const beforeSnapshot = identity?.before_snapshot_digest;
  const afterSnapshot = identity?.after_snapshot_digest;
  const compared = identity?.knowledge_compared;
  useEffect(() => {
    if (!shown) return;
    observeComparison(comparisonGenerationOf(shown));
  }, [observeComparison, shown, beforeTree, afterTree, beforeSnapshot, afterSnapshot, compared]);
}

export function initialSubject(
  target: { selectorKind?: ReviewSelectorKind; selectorId?: string },
  entries?: ReviewEntry[],
  outline?: import('../../data/review').ReviewOutline,
  order: import('./triageOrderPreference').TreeOrder = 'triage',
): ReviewSubject | undefined {
  if (target.selectorKind && target.selectorId)
    return { kind: target.selectorKind, id: target.selectorId };
  if (outline?.state === 'outlined') {
    const first = firstOutlineChange(outline, order);
    return first ? { kind: first.kind, id: first.selectorId } : undefined;
  }
  const first = catalogueFirst(entries);
  return first ? { kind: first.selector_kind, id: first.selector_id } : undefined;
}

function catalogueFirst(entries: ReviewEntry[] = []): ReviewEntry | undefined {
  return entries.find((entry) => entry.selector_kind === 'family') ?? entries[0];
}

function identityForTask(
  identity: { comparison: string; value: ComparisonGeneration } | undefined,
  comparison: string,
): ComparisonGeneration | undefined {
  return identity?.comparison === comparison ? identity.value : undefined;
}
