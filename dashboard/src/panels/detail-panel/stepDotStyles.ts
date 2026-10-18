// Pointer-only step details float outside the reader's clipped row cells.
import { css } from '../../../styled-system/css';

export const dotTarget = css({
  display: 'inline-flex',
  padding: '0.15rem 0',
  cursor: 'default',
});
export const stepTipStyles = {
  position: 'fixed',
  zIndex: '1000',
  pointerEvents: 'none',
  width: 'max-content',
  maxWidth: 'min(24rem, calc(100vw - 1rem))',
  padding: '0.65rem 0.8rem',
  border: '1px solid token(colors.grid)',
  borderRadius: '3px',
  background: 'bg',
  color: 'ink',
  boxShadow: '0 4px 18px oklch(0 0 0 / 0.35)',
  fontSize: '0.78rem',
  lineHeight: '1.5',
  // MIK-R175 rule 3: only the title wraps. Breaking words anywhere let a squeezed flex item
  // spell "done" one letter per line, so the box no longer does it and the parts that must stay
  // whole say so themselves.
  overflowWrap: 'normal',
  wordBreak: 'normal',
} as const;
export const stepTip = css(stepTipStyles);
// The head is a flex row of id, title and status word. The id and the status word keep their full
// width (rule 3): a shrink there is what split "done" across lines.
export const tipHeadStyles = {
  display: 'flex',
  gap: '0.6rem',
  alignItems: 'baseline',
  flexWrap: 'wrap',
} as const;
export const tipHead = css(tipHeadStyles);
export const tipPartStyles = { flexShrink: '0', whiteSpace: 'nowrap' } as const;
export const tipPart = css(tipPartStyles);
export const tipTitleStyles = {
  minWidth: '0',
  flex: '1 1 6rem',
  overflowWrap: 'anywhere',
} as const;
export const tipTitle = css(tipTitleStyles);
export const tipChildrenStyles = {
  listStyle: 'none',
  padding: '0',
  margin: '0.4rem 0 0',
  display: 'grid',
  gap: '0.2rem',
  '& li': {
    display: 'grid',
    gridTemplateColumns: 'max-content minmax(0, 1fr) max-content',
    gap: '0.6rem',
  },
} as const;
export const tipChildren = css(tipChildrenStyles);
