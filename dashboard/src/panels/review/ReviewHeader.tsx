// The review's top line (MIK-R89 v2 rules 1, 6 and 10): back, the title, the status chip and the
// comparison selector, with the change set's ⊕ on the comparison label (W29). It spans the tree and
// the reading column and never scrolls (it sits outside the page scroller); stacked, at 40rem and
// narrower, it wraps and scrolls with the page, every part inside the line (rule 11). Refresh and the
// keys reference close the line; a refresh's notice takes a row of its own under it
// (ReviewRefresh.tsx). Its controls are independent of subject reads: the chip keeps its place
// through every read and only its text changes.
import { css } from '../../../styled-system/css';
import { ReviewKeys } from './ReviewKeys';

const line = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.55rem',
  flex: 'none',
  minWidth: 0,
  paddingBlock: '0.2rem 0.55rem',
  // The controls fit one line above 40rem (the title box gives way); a row wraps only for the
  // refresh's notice there, and for the stacked lines at 40rem and narrower.
  flexWrap: 'wrap',
});

// Stacked, the title box keeps the first line beside back and sends the chip and the comparison to the
// next ones; its own parts wrap inside it, so none runs past the line or under the chip (rule 11).
const titleBox = css({
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.5rem',
  minWidth: 0,
  flex: '1',
  fontSize: '0.85rem',
  '@media (max-width: 40rem)': { flex: '1 1 14rem', flexWrap: 'wrap', rowGap: '0.1rem' },
});

const label = css({ whiteSpace: 'nowrap', '@media (max-width: 40rem)': { whiteSpace: 'normal' } });

const titleText = css({
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  minWidth: 0,
  '@media (max-width: 40rem)': { flexBasis: '100%' },
});

// A record label gives way before the title does (D401: the mockup's title fits the 1440 px line) and
// keeps its first word; its whole text is its tooltip.
const recordText = css({
  color: 'muted',
  fontSize: '0.75rem',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  flexShrink: 100,
  minWidth: '9ch',
  '@media (max-width: 40rem)': { whiteSpace: 'normal' },
});

// The leaf's short id, as the mockup's label names it (app.js `topline`, "Intent review · L181"): the
// last part of a dated leaf id; any other id is named whole.
export function shortLeafId(leaf: string): string {
  return /^\d{6}-[A-Z]+-(L\d+)$/.exec(leaf)?.[1] ?? leaf;
}

export function ReviewHeader({
  leaf,
  title,
  record,
  chip,
  comparison,
  refresh,
  onBack,
}: {
  leaf: string;
  // The task's title from the dashboard's task projection; the leaf id stands in when it has none.
  title?: string;
  // A historical or reconstructed comparison's label (INV-S26EBC8P); none for a live review.
  record?: string | null;
  chip?: React.ReactNode;
  comparison?: React.ReactNode;
  refresh?: React.ReactNode;
  onBack: () => void;
}) {
  const short = shortLeafId(leaf);
  return (
    <div className={line} data-testid="review-top-line">
      <button type="button" onClick={onBack} data-testid="review-back">
        ← back
      </button>
      <div className={titleBox}>
        <b className={label} title={short === leaf ? undefined : leaf}>
          Intent review · {short}
        </b>
        <span className={titleText} data-testid="review-title" title={title ?? leaf}>
          {title ?? leaf}
        </span>
        {record ? (
          <span data-testid="review-history" className={recordText} title={record}>
            {record}
          </span>
        ) : null}
      </div>
      {chip}
      {comparison}
      {refresh}
      <ReviewKeys />
    </div>
  );
}
