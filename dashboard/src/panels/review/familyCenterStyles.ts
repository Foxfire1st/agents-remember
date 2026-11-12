import { css } from '../../../styled-system/css';

export const shell = css({
  background: 'bgPanel',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  padding: '1.1rem',
  minWidth: '0',
  display: 'grid',
  gap: '1.2rem',
  alignContent: 'start',
});

export const sectionLabel = css({
  color: 'cyan',
  fontSize: '0.72rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  margin: '0 0 0.25rem',
});

export const card = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  padding: '0.5rem 0.6rem',
  background: 'bg',
  minWidth: '0',
});

export const muted = css({ color: 'muted', fontSize: '0.8rem', margin: '0.2rem 0' });

export const prose = css({
  whiteSpace: 'pre-wrap',
  margin: '0.2rem 0',
  fontSize: '0.9rem',
  lineHeight: '1.8',
});

export const rows = css({ margin: '0.2rem 0', paddingLeft: '1.1rem' });

export const linkButton = css({
  background: 'transparent',
  border: 'none',
  color: 'amber',
  cursor: 'pointer',
  font: 'inherit',
  padding: '0',
  textDecoration: 'underline',
  _focusVisible: { outline: '1px solid var(--amber)', outlineOffset: '2px' },
});

