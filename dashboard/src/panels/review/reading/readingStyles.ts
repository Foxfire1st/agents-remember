// The reading column's shared look (the approved mockup's `.subject`, `.shead`, `.block`, `.bhead`,
// `.group` and `.summary`, styles.css), and the one DOM mark every `j`/`k` stop carries.
import { css } from '../../../../styled-system/css';

export const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.2rem 0' });

// A section of the reading column. A rule stands between consecutive sections, as the mockup's
// `.subject + .subject` draws it (ruling 9498d6e4): above every subject section but the column's first
// (`data-ruled`, ReadingColumn.tsx), across family groups, and above the lane when the subjects' list
// drew one (`data-subjects-drawn`).
export const subjectBox = css({
  paddingBlock: '1.4rem 0.6rem',
  scrollMarginTop: '3rem',
  '&[data-ruled]': { borderTopWidth: '1px', borderTopStyle: 'solid', borderTopColor: 'grid', marginTop: '1rem' },
  '[data-subjects-drawn] + &': { borderTopWidth: '1px', borderTopStyle: 'solid', borderTopColor: 'grid', marginTop: '1rem' },
});

export const sectionHead = css({
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.6rem',
  flexWrap: 'wrap',
  '& h3': { margin: 0, fontSize: '1rem', fontWeight: 700, lineHeight: '1.4', color: 'ink', maxWidth: '90ch' },
});

export const subjectId = css({
  fontSize: '0.75rem',
  color: 'muted',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  paddingInline: '0.35rem',
});

export const badge = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  color: 'muted',
  borderRadius: '2px',
  paddingInline: '0.4rem',
  fontSize: '0.7rem',
  whiteSpace: 'nowrap',
  '&[data-hot="true"]': { color: 'amber', borderColor: 'amber' },
  // A subject's change badge in the mockup's alarm red (styles.css `.badge.change`; d9a1ec8f).
  '&[data-change="changed"], &[data-change="added"], &[data-change="removed"]': { color: 'alarm', borderColor: 'alarm' },
});

export const sublabel = css({
  marginBlock: '1.3rem 0.5rem',
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.7rem',
  fontSize: '0.68rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'cyan',
  '& span': { color: 'muted', letterSpacing: 0, textTransform: 'none' },
});

export const block = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  marginBlock: '0 0.9rem',
  background: 'bg',
  minWidth: 0,
  scrollMarginTop: '3rem',
  '&[data-selected="true"]': { borderColor: 'amber' },
});

export const blockHead = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.6rem',
  paddingBlock: '0.4rem',
  paddingInline: '0.6rem',
  borderBottomWidth: '1px',
  borderBottomStyle: 'solid',
  borderBottomColor: 'grid',
  background: 'bgPanel',
  fontSize: '0.76rem',
  flexWrap: 'wrap',
});

export const group = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  marginBlock: '0.6rem',
  scrollMarginTop: '3rem',
});

export const groupHead = css({
  display: 'flex',
  width: '100%',
  alignItems: 'baseline',
  gap: '0.6rem',
  textAlign: 'left',
  fontSize: '0.78rem',
  '& [data-why]': { color: 'muted', fontSize: '0.72rem' },
});

export const groupRow = css({
  display: 'grid',
  gridTemplateColumns: 'minmax(0, 1fr) auto auto',
  gap: '0.8rem',
  paddingBlock: '0.2rem',
  paddingInline: '0.7rem',
  fontSize: '0.74rem',
  borderTopWidth: '1px',
  borderTopStyle: 'solid',
  borderTopColor: 'grid',
  '& > span:first-child': { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  '& > span:not(:first-child)': { color: 'muted' },
});

// Every `j`/`k` stop of the reading column (MIK-R89 v2 rule 11, W21): a changed block of a shown
// subject, or a lane hunk, in display order. The value is the block's identity key (blockIdentity.ts).
export const BLOCK_STOP = 'data-review-block';

export const laneRowAttrs = (key: string) => ({ [BLOCK_STOP]: key });

// A held block (D382: a block that has shown content keeps its last known height while it is drawn
// again): the held box, at its held height, shows skeleton lines where what it holds is not drawn,
// and its content, on the page's background, covers them where it is.
export const heldSkeleton = css({
  backgroundImage:
    'repeating-linear-gradient(to bottom, transparent 0 0.45rem, token(colors.grid) 0.45rem 0.95rem, transparent 0.95rem 1.4rem)',
});

export const heldContent = css({ display: 'flow-root', background: 'bg' });
