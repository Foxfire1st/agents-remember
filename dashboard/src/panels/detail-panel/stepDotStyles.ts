// Pointer-only step details float outside the reader's clipped row cells.
import { css } from '../../../styled-system/css';

export const dotTarget = css({
  display: 'inline-flex',
  padding: '0.15rem 0',
  cursor: 'default',
});
export const stepTip = css({
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
  overflowWrap: 'anywhere',
});
export const tipHead = css({ display: 'flex', gap: '0.6rem', alignItems: 'baseline' });
export const tipChildren = css({
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
});
