import { css } from '../../../styled-system/css';

// The native table keeps the reader's row spacing, with a readable time on narrow panels.
export const completedTable = css({
  width: '100%',
  borderCollapse: 'collapse',
  tableLayout: 'fixed',
  fontSize: '0.8rem',
  '& th': {
    padding: '0.1rem 0.5rem 0.2rem',
    textAlign: 'left',
    fontWeight: '400',
    fontSize: '0.64rem',
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    color: 'muted',
  },
  '& td': {
    padding: '0.27rem 0.5rem',
    borderTop: '1px solid token(colors.grid)',
    verticalAlign: 'baseline',
    overflowWrap: 'anywhere',
  },
  '& td:last-child': { color: 'muted', fontSize: '0.74rem' },
  '& tr:hover td': { background: 'oklch(0.7 0.1 200 / 0.07)' },
});
export const completedName = css({
  font: 'inherit',
  textAlign: 'left',
  color: 'ink',
  background: 'transparent',
  border: '0',
  padding: '0',
  cursor: 'pointer',
  _hover: { textDecoration: 'underline', textDecorationColor: 'cyan' },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '2px' },
});
export const sortHead = css({
  font: 'inherit',
  letterSpacing: 'inherit',
  textTransform: 'inherit',
  color: 'inherit',
  border: '0',
  padding: '0',
  background: 'transparent',
  cursor: 'pointer',
  _hover: { color: 'cyan' },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '2px' },
});
