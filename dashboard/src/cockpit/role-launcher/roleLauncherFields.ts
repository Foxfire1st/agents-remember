import { css } from '../../../styled-system/css';

// A selector's wrapper: no visible title (each selector keeps its accessible name and
// carries a hover text that says what it selects). It sizes to its value up to its cap and
// yields below it, so the full row still lands on one line.
export const roleLauncherField = css({
  display: 'grid',
  flexGrow: '0',
  flexBasis: 'auto',
  minWidth: '6rem',
});

// The agent, model, effort and mode selectors share one width family; the mode's own label
// carries its source, so it gets the room to name both.
export const roleLauncherOverrideField = css({
  maxWidth: '10rem',
  flexShrink: 1.25,
  '@media (min-width: 90rem)': { minWidth: '4.5rem' },
});

export const roleLauncherModeField = css({
  maxWidth: '17rem',
  flexShrink: 1.25,
  '@media (min-width: 90rem)': { minWidth: '6rem' },
});
