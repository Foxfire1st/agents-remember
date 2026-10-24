/**
 * The top line's Review | Explanation switch (MIK-R106 v2 rule 6; mockup `explanation`,
 * `explanation-empty`, `stale`, `regenerating`).
 *
 * It is the one control that shows the explainer's end-to-end page in the reading column, and it is
 * the top line's: it stays outside the page it switches to, exactly as the tree and the chat do. Its
 * label carries the two facts the top line owns -- that a build is running, and how many parts are
 * stale -- and switching itself starts nothing (rule 10).
 *
 * Pure props: the counts come from the page's own read (`useExplainer`), so the switch and the view
 * never read the same answer twice.
 */

import { css } from '../../../../styled-system/css';

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
  padding: '0.2rem 0.55rem',
  background: 'transparent',
  color: 'muted',
  border: '0',
  cursor: 'pointer',
  '&[aria-selected="true"]': { background: 'bgPanel', color: 'ink' },
});

const badge = css({
  marginLeft: '0.35rem',
  fontFamily: 'mono',
  fontSize: '0.68rem',
  color: 'amber',
});

export type ExplanationViewTab = 'review' | 'explanation';

export interface ExplanationSwitchProps {
  view: ExplanationViewTab;
  /** The number of stale parts the trusted list holds, when it is known. */
  stale?: number;
  /** Whether the environment is being built: the label says so and names the chat. */
  building?: boolean;
  /** The explainer was retired: the page stays readable, and the label says so. */
  retired?: boolean;
  onSwitch: (view: ExplanationViewTab) => void;
}

/** What the label adds beyond the word "Explanation": a build, the stale count, or a retire. */
export function explanationMarks({
  building = false,
  stale = 0,
  retired = false,
}: {
  building?: boolean;
  stale?: number;
  retired?: boolean;
}): string[] {
  const marks: string[] = [];
  if (building) marks.push('◌');
  // A build outranks the stale count of the page it will replace.
  else if (stale > 0) marks.push(`${stale} stale`);
  if (retired) marks.push('retired');
  return marks;
}

export function ExplanationSwitch({ view, stale = 0, building = false, retired = false, onSwitch }: ExplanationSwitchProps) {
  const marks = explanationMarks({ building, stale, retired });
  return (
    <div className={seg} role="tablist" aria-label="Review or Explanation" data-testid="review-explanation-switch">
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
      <button
        type="button"
        role="tab"
        className={tab}
        data-testid="switch-explanation"
        aria-selected={view === 'explanation'}
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
    </div>
  );
}
