/**
 * The trusted list of stale parts (MIK-R104 v3 rule 3; MIK-R106 v2 rule 6; MIK-R103 v2 item 5).
 *
 * It is the record of staleness, not a reading of the page: it is drawn from the host's own answer,
 * outside the frame, and it is drawn whatever the frame shows, hides or imitates. The marks a page
 * draws for itself are advisory and change nothing here.
 *
 * Each row carries the part's title and its reason -- the changed basis in MIK-R187 rule 8's words.
 * Reasons never travel into the frame.
 */

import { css } from '../../../../styled-system/css';
import type { StalePart, StaleRegeneration } from './explainerApi';

const box = css({
  display: 'grid',
  gap: '0.25rem',
  padding: '0.4rem 0.55rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'bgPanel',
  minWidth: 0,
});

const head = css({
  display: 'flex',
  gap: '0.5rem',
  alignItems: 'baseline',
  fontFamily: 'mono',
  fontSize: '0.72rem',
  color: 'muted',
});

const count = css({ color: 'amber', fontFamily: 'mono', fontSize: '0.72rem' });
const rows = css({ margin: 0, paddingLeft: '1.1rem', display: 'grid', gap: '0.15rem', minWidth: 0 });
const row = css({ fontSize: '0.76rem', overflowWrap: 'anywhere' });
const partTitle = css({ fontWeight: 600 });
const reason = css({ color: 'muted', fontFamily: 'mono', fontSize: '0.7rem' });
const empty = css({ fontSize: '0.74rem', color: 'muted', margin: 0 });
const alert = css({ fontSize: '0.74rem', color: 'dormant', margin: 0 });
const regenerateButton = css({
  fontFamily: 'mono',
  fontSize: '0.72rem',
  padding: '0.2rem 0.5rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'amber',
  borderRadius: '3px',
  background: 'transparent',
  color: 'amber',
  cursor: 'pointer',
  justifySelf: 'start',
  _disabled: { opacity: 0.45, cursor: 'default' },
});

export interface StalePartsListProps {
  parts: readonly StalePart[];
  /** The true number of stale parts, when more are stale than the host lists. */
  total?: number;
  /** Why staleness could not be computed; the list then claims nothing. */
  unavailable?: string | null;
  /** The references of blocks whose grades are stale against the target comparison. */
  staleGrades?: readonly string[];
  /** Selective regeneration label and message for stale parts and grades (MIK-R187 rule 8). */
  regeneration?: StaleRegeneration | null;
  regenerating?: boolean;
  regenerationProblem?: string | null;
  /** Selects a part in the page, when the view can. */
  onOpenPart?: (part: StalePart) => void;
  /** Presses selective regeneration of stale parts and grades (MIK-R187 rule 8). */
  onRegenerate?: () => void;
}

function fallbackRegenerateLabel(partCount: number, gradeCount: number): string {
  const partWord = `${partCount} ${partCount === 1 ? 'part' : 'parts'}`;
  const gradeWord = `${gradeCount} ${gradeCount === 1 ? 'grade' : 'grades'}`;
  return `Regenerate stale (${partWord} · ${gradeWord})`;
}

function StalePartsBody({
  parts,
  unavailable,
  gradeCount,
  onOpenPart,
}: {
  parts: readonly StalePart[];
  unavailable?: string | null;
  gradeCount: number;
  onOpenPart?: (part: StalePart) => void;
}) {
  if (parts.length > 0) {
    return (
      <ol className={rows}>
        {parts.map((part) => (
          <li className={row} key={part.id} data-testid="stale-part" data-part={part.id}>
            {onOpenPart ? (
              <button
                type="button"
                className={partTitle}
                data-testid="stale-part-title"
                onClick={() => onOpenPart(part)}
              >
                {part.title}
              </button>
            ) : (
              <span className={partTitle}>{part.title}</span>
            )}{' '}
            <span className={reason}>— {part.reason}</span>
          </li>
        ))}
      </ol>
    );
  }
  if (unavailable) return null;
  if (gradeCount > 0) {
    return (
      <p className={empty} data-testid="stale-grades-only">
        {`No part is stale; ${gradeCount} ${gradeCount === 1 ? 'grade is' : 'grades are'} stale.`}
      </p>
    );
  }
  return (
    <p className={empty} data-testid="stale-empty">
      Every claim&rsquo;s basis still resolves at the bound comparison.
    </p>
  );
}

function staleHeaderCountText(unavailable: string | null | undefined, counted: number): string {
  if (unavailable) return 'unreadable';
  return counted === 0 ? 'none' : `${counted}`;
}

function gradeCountOf(props: StalePartsListProps): number {
  if (props.regeneration) return props.regeneration.gradeCount;
  return props.staleGrades ? props.staleGrades.length : 0;
}

function resolveStaleSummary(props: StalePartsListProps) {
  const counted = props.total ?? props.parts.length;
  const gradeCount = gradeCountOf(props);
  const partCount = props.regeneration ? props.regeneration.partCount : counted;
  const more = Math.max(0, counted - props.parts.length);
  const hasAnyStale = counted + props.parts.length + gradeCount > 0;
  const hasStale = !props.unavailable && hasAnyStale;
  const buttonLabel = props.regeneration?.label ?? fallbackRegenerateLabel(partCount, gradeCount);
  return { counted, gradeCount, more, hasStale, buttonLabel };
}

function StalePartsFooter({
  more,
  hasStale,
  regenerating,
  buttonLabel,
  regenerationProblem,
  onRegenerate,
}: {
  more: number;
  hasStale: boolean;
  regenerating: boolean;
  buttonLabel: string;
  regenerationProblem?: string | null;
  onRegenerate?: () => void;
}) {
  return (
    <>
      {more > 0 ? (
        <p className={empty} data-testid="stale-more">
          {`+ ${more} more stale ${more === 1 ? 'part' : 'parts'} not listed here`}
        </p>
      ) : null}
      {hasStale && onRegenerate ? (
        <button
          type="button"
          className={regenerateButton}
          data-testid="regenerate-stale"
          disabled={regenerating}
          onClick={onRegenerate}
        >
          {regenerating ? 'Regenerating…' : buttonLabel}
        </button>
      ) : null}
      {regenerationProblem ? (
        <p className={alert} role="alert" data-testid="regenerate-stale-problem">
          {regenerationProblem}
        </p>
      ) : null}
    </>
  );
}

export function StalePartsList(props: StalePartsListProps) {
  const { parts, unavailable, regenerating = false, regenerationProblem, onOpenPart, onRegenerate } =
    props;
  const { counted, gradeCount, more, hasStale, buttonLabel } = resolveStaleSummary(props);
  return (
    <section
      className={box}
      data-testid="stale-parts"
      data-count={counted}
      data-listed={parts.length}
      data-stale-grades={gradeCount}
      aria-label="Stale parts of the explanation"
      title="The trusted record of staleness. The marks a page draws are advisory."
    >
      <div className={head}>
        <span>Stale parts</span>
        <span className={count} data-testid="stale-count">
          {staleHeaderCountText(unavailable, counted)}
        </span>
        {gradeCount > 0 ? (
          <span className={count} data-testid="stale-grades-count">
            {`· ${gradeCount} stale ${gradeCount === 1 ? 'grade' : 'grades'}`}
          </span>
        ) : null}
      </div>
      {unavailable ? (
        <p className={alert} role="alert" data-testid="stale-unavailable">
          {`Staleness cannot be read: ${unavailable}. No part is claimed current.`}
        </p>
      ) : null}
      <StalePartsBody
        parts={parts}
        unavailable={unavailable}
        gradeCount={gradeCount}
        onOpenPart={onOpenPart}
      />
      <StalePartsFooter
        more={more}
        hasStale={hasStale}
        regenerating={regenerating}
        buttonLabel={buttonLabel}
        regenerationProblem={regenerationProblem}
        onRegenerate={onRegenerate}
      />
    </section>
  );
}
