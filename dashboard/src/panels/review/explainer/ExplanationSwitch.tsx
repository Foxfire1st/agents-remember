/**
 * The top line's Review | Explanation switch (MIK-R106 v2 rule 6; mockup `explanation`,
 * `explanation-empty`, `stale`, `regenerating`).
 *
 * It is the one control that shows the explainer's end-to-end page in the reading column, and it is
 * the top line's: it stays outside the page it switches to, exactly as the tree and the chat do. Its
 * label carries the two facts the top line owns -- that a build is running, and how many parts are
 * stale -- and switching itself starts nothing (rule 10).
 *
 * Staleness is reported here and nowhere else (F18). The `N stale` mark is the badge's own summary:
 * opening it says, in plain words, which parts are stale and why, that a newer comparison exists,
 * and offers the one press that regenerates them. The page below draws its record and nothing else.
 *
 * Pure props: the counts and the badge's details come from the page's own read (`useExplainer`), so
 * the switch and the view never read the same answer twice.
 */

import { css } from '../../../../styled-system/css';
import type { StaleBrief } from './explainerModel';

const wrap = css({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.25rem',
  minWidth: 0,
  position: 'relative',
});

const seg = css({
  display: 'inline-flex',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  overflow: 'hidden',
});

const tab = css({
  fontFamily: 'mono',
  fontSize: '0.72rem',
  lineHeight: '1.4',
  padding: '0.2rem 0.55rem',
  background: 'transparent',
  color: 'muted',
  border: '0',
  cursor: 'pointer',
  '&[aria-selected="true"]': {
    background: 'color-mix(in oklab, token(colors.amber) 16%, transparent)',
    color: 'amber',
  },
});

const badge = css({
  marginLeft: '0.35rem',
  fontFamily: 'mono',
  fontSize: '0.68rem',
  color: 'amber',
});

const badgeMenu = css({ position: 'relative', display: 'inline-flex', minWidth: 0 });
const badgeSummary = css({
  fontFamily: 'mono',
  fontSize: '0.68rem',
  color: 'amber',
  cursor: 'pointer',
  padding: '0.1rem 0.3rem',
  listStyle: 'none',
  whiteSpace: 'nowrap',
  '&::-webkit-details-marker': { display: 'none' },
  '&::after': { content: '" ▾"', fontSize: '0.6rem' },
  '&[data-open="true"]::after': { content: '" ▴"' },
});
const badgePanel = css({
  position: 'absolute',
  top: '100%',
  insetInlineStart: 0,
  zIndex: 30,
  display: 'grid',
  gap: '0.3rem',
  width: '26rem',
  maxWidth: '80vw',
  padding: '0.5rem 0.6rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'bgPanel',
  fontFamily: 'mono',
  fontSize: '0.72rem',
  whiteSpace: 'normal',
  cursor: 'auto',
});
const staleLine = css({ margin: 0, color: 'amber' });
const staleRows = css({ margin: 0, paddingLeft: '1.1rem', display: 'grid', gap: '0.15rem' });
const staleRow = css({ fontSize: '0.76rem', overflowWrap: 'anywhere' });
const stalePartTitle = css({ fontWeight: 600 });
const staleReason = css({ color: 'muted', fontFamily: 'mono', fontSize: '0.7rem' });
const note = css({ margin: 0, color: 'muted', fontSize: '0.7rem' });
const alert = css({ margin: 0, color: 'dormant', fontSize: '0.72rem' });
const refresh = css({
  justifySelf: 'start',
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
  _disabled: { opacity: 0.45, cursor: 'default' },
});

export type ExplanationViewTab = 'review' | 'explanation';

export interface ExplanationSwitchProps {
  view: ExplanationViewTab;
  /** The number of stale parts the trusted list holds, when it is known. */
  stale?: number;
  /** What the badge says when it is opened: the parts, their reasons, and the newer comparison. */
  brief?: StaleBrief | null;
  /** A regeneration is in flight: the badge's refresh control says so and holds still. */
  regenerating?: boolean;
  /** The last refresh's refusal, in the host's own words. */
  regenerationProblem?: string | null;
  /** The badge's refresh control: regenerates exactly the stale parts and grades. */
  onRegenerate?: () => void;
  /** Whether the environment is being built: the label says so and names the chat. */
  building?: boolean;
  /** The explainer was retired: the page stays readable, and the label says so. */
  retired?: boolean;
  onSwitch: (view: ExplanationViewTab) => void;
}

/** What the label adds beyond the word "Explanation": a build, or a retire. */
export function explanationMarks({
  building = false,
  retired = false,
}: {
  building?: boolean;
  retired?: boolean;
}): string[] {
  const marks: string[] = [];
  if (building) marks.push('◌');
  if (retired) marks.push('retired');
  return marks;
}

/**
 * The badge's summary, in the top line's own shorthand: the stale parts' count, else the stale
 * grades', else an unresolved mark when the host could not compute staleness at all.
 */
export function staleMarkOf(brief: StaleBrief | null, building = false): string | null {
  // A build outranks the stale count of the page it will replace.
  if (building || !brief?.present) return null;
  if (brief.total > 0) return `${brief.total} stale`;
  if (brief.gradeCount > 0) return `${brief.gradeCount} stale`;
  return brief.unavailable ? 'stale?' : null;
}

/** The badge's first line: how many parts are stale, in plain words. */
function staleCountLine(brief: StaleBrief): string {
  if (brief.unavailable) {
    return `Staleness cannot be read: ${brief.unavailable}. No part is claimed current.`;
  }
  if (brief.total > 0) {
    const verb = brief.total === 1 ? 'is' : 'are';
    return `${brief.total} ${brief.total === 1 ? 'part' : 'parts'} of this explanation ${verb} stale against the comparison on screen.`;
  }
  return 'No part is stale: every claim’s basis still resolves at the comparison on screen.';
}

/** The sentence that says a newer comparison exists, when one does. */
function newerComparisonLine(newerComparison: string): string {
  return `A newer comparison exists: ${newerComparison}`;
}

function StaleGradeLine({ gradeCount }: { gradeCount: number }) {
  if (gradeCount <= 0) return null;
  const word = gradeCount === 1 ? 'grade is' : 'grades are';
  return (
    <p className={note} data-testid="stale-grades-only">
      {`${gradeCount} ${word} stale too: those blocks were graded against an earlier comparison.`}
    </p>
  );
}

function StaleRows({ parts }: { parts: StaleBrief['parts'] }) {
  if (parts.length === 0) return null;
  return (
    <ol className={staleRows}>
      {parts.map((part) => (
        <li className={staleRow} key={part.id} data-testid="stale-part" data-part={part.id}>
          <span className={stalePartTitle}>{part.title}</span>{' '}
          <span className={staleReason}>— {part.reason}</span>
        </li>
      ))}
    </ol>
  );
}

function StaleBadgePanel({
  brief,
  regenerating,
  regenerationProblem,
  onRegenerate,
}: {
  brief: StaleBrief;
  regenerating: boolean;
  regenerationProblem?: string | null;
  onRegenerate?: () => void;
}) {
  const more = Math.max(0, brief.total - brief.parts.length);
  const regeneration = brief.regeneration;
  return (
    <div className={badgePanel} data-testid="explanation-stale-details">
      <p className={staleLine} data-testid="stale-summary-line">
        {staleCountLine(brief)}
      </p>
      <StaleRows parts={brief.parts} />
      {more > 0 ? (
        <p className={note} data-testid="stale-more">
          {`+ ${more} more stale ${more === 1 ? 'part' : 'parts'} not listed here`}
        </p>
      ) : null}
      <StaleGradeLine gradeCount={brief.gradeCount} />
      {brief.newerComparison ? (
        <p className={note} data-testid="newer-comparison">
          {newerComparisonLine(brief.newerComparison)}
        </p>
      ) : null}
      {regeneration && onRegenerate ? (
        <button
          type="button"
          className={refresh}
          data-testid="regenerate-stale"
          disabled={regenerating}
          title="Regenerates exactly the stale parts and grades; nothing else is redone."
          onClick={onRegenerate}
        >
          {regenerating ? 'Regenerating…' : regeneration.label}
        </button>
      ) : null}
      {regenerationProblem ? (
        <p className={alert} role="alert" data-testid="regenerate-stale-problem">
          {regenerationProblem}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The stale mark and its details (F18): the mark is the badge's summary, so pressing it opens the
 * record rather than switching views, and the refresh control inside it does the regeneration.
 */
function StaleBadge({
  brief,
  mark,
  regenerating = false,
  regenerationProblem,
  onRegenerate,
}: {
  brief: StaleBrief;
  mark: string;
  regenerating?: boolean;
  regenerationProblem?: string | null;
  onRegenerate?: () => void;
}) {
  return (
    <details
      className={badgeMenu}
      data-testid="explanation-stale-badge"
      data-count={brief.total}
      data-parts={brief.parts.length}
      data-newer={brief.newerComparison ? 'true' : undefined}
    >
      <summary
        className={badgeSummary}
        data-testid="explanation-stale-mark"
        title="What is stale, why, and what a refresh would redo"
      >
        {mark}
      </summary>
      <StaleBadgePanel
        brief={brief}
        regenerating={regenerating}
        regenerationProblem={regenerationProblem}
        onRegenerate={onRegenerate}
      />
    </details>
  );
}

function ExplanationTab({
  selected,
  stale,
  building,
  retired,
  onSwitch,
}: {
  selected: boolean;
  stale: number;
  building: boolean;
  retired: boolean;
  onSwitch: ExplanationSwitchProps['onSwitch'];
}) {
  const marks = explanationMarks({ building, retired });
  return (
    <button
      type="button"
      role="tab"
      className={tab}
      data-testid="switch-explanation"
      aria-selected={selected}
      data-stale={stale}
      data-building={building ? 'true' : undefined}
      data-retired={retired ? 'true' : undefined}
      title={
        building
          ? 'The explainer is being built; the progress is in the chat'
          : 'The explainer’s end-to-end page for this comparison'
      }
      onClick={() => onSwitch('explanation')}
    >
      Explanation
      {marks.length ? <span className={badge}>{` ${marks.join(' ')}`}</span> : null}
    </button>
  );
}

export function ExplanationSwitch({
  view,
  stale = 0,
  brief = null,
  regenerating = false,
  regenerationProblem = null,
  onRegenerate,
  building = false,
  retired = false,
  onSwitch,
}: ExplanationSwitchProps) {
  // The badge is the mark itself: a build outranks it, and nothing is drawn when nothing is stale.
  const staleMark = staleMarkOf(brief, building);
  return (
    <div
      className={wrap}
      data-testid="review-explanation-switch"
      data-segment
      data-stale={stale}
    >
      <div className={seg} role="tablist" aria-label="Review or Explanation">
        <button
          type="button"
          role="tab"
          className={tab}
          data-testid="switch-review"
          aria-selected={view === 'review'}
          onClick={() => onSwitch('review')}
        >
          Review
        </button>
        <ExplanationTab
          selected={view === 'explanation'}
          stale={stale}
          building={building}
          retired={retired}
          onSwitch={onSwitch}
        />
      </div>
      {staleMark && brief ? (
        <StaleBadge
          brief={brief}
          mark={staleMark}
          regenerating={regenerating}
          regenerationProblem={regenerationProblem}
          onRegenerate={onRegenerate}
        />
      ) : null}
    </div>
  );
}
