// Recorded subject navigation. The catalogue supplies identity; the review supplies family content.
import { useState } from 'react';
import { useDashboard } from '../../data/store';
import { useReviewCatalogue } from '../../data/useReviewCatalogue';
import type { FamilySelection } from './FamilyTree';
import { css } from '../../../styled-system/css';
import type { ReviewEntry, ReviewSelectorKind } from '../../data/review';
import type { ReviewCatalogueRead } from '../../data/useReviewCatalogue';

export interface ReviewSubject {
  kind: ReviewSelectorKind;
  id: string;
}
export interface ReviewNavigationState {
  catalogue: ReviewCatalogueRead & { refresh: () => void };
  subject?: ReviewSubject;
  onSelect: (subject: ReviewSubject | undefined, context?: FamilySelection) => void;
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
}: ReviewNavigationState & { children?: React.ReactNode; loadedFamilyIds?: string[] }) {
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

// An explicit source-only choice remains source-only when the catalogue refreshes.
export function useReviewNavigation(target: {
  repo: string;
  master: string;
  leaf: string;
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
  history?: 'recorded';
}): ReviewNavigationState {
  const analytics = useDashboard((state) => state.analytics);
  const facts = analytics === null ? 'no-projection' : JSON.stringify(analytics);
  const catalogue = useReviewCatalogue(target.repo, target.master, target.leaf, facts);
  const key = JSON.stringify(target);
  const [choice, setChoice] = useState<{ key: string; subject?: ReviewSubject }>();
  const subject = choice?.key === key ? choice.subject : initialSubject(target, catalogue.entries);
  return { catalogue, subject, onSelect: (subject) => setChoice({ key, subject }) };
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
  children,
}: {
  entries: ReviewEntry[];
  subject?: ReviewSubject;
  onSelect: ReviewNavigationState['onSelect'];
  loadedFamilyIds: string[];
  children?: React.ReactNode;
}) {
  const firstLoaded = entries.find((entry) => loadedFamilyIds.includes(entry.selector_id));
  return (
    <>
      {entries.map((entry) =>
        loadedFamilyIds.includes(entry.selector_id) ? (
          entry === firstLoaded ? (
            <div key={entry.selector_id}>{children}</div>
          ) : null
        ) : (
          <SubjectButton
            key={entry.selector_id}
            entry={entry}
            subject={subject}
            onSelect={onSelect}
          />
        ),
      )}
      {!firstLoaded ? children : null}
    </>
  );
}
