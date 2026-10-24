// The explainer column's styles, in one place: the column shell, the header, the placeholder, the
// first request's form, the build's panel and Retire's confirmation all draw from these.
import { css } from '../../styled-system/css';

export const column = css({ display: 'flex', flexDirection: 'column', flex: '1', minHeight: '0', minWidth: '0' });
export const head = css({
  display: 'flex',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: '0.4rem',
  padding: '0.4rem 0.6rem',
  borderBottom: '1px solid var(--grid)',
  background: 'var(--bg-panel)',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.76rem',
});
export const who = css({ display: 'flex', flexDirection: 'column', lineHeight: '1.2', flex: '1', minWidth: '0' });
export const statusWord = css({ color: 'var(--muted)', fontSize: '0.68rem' });
export const quietButton = css({
  border: '1px solid var(--grid)',
  background: 'transparent',
  color: 'inherit',
  borderRadius: '0.3rem',
  padding: '0.15rem 0.4rem',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.72rem',
  cursor: 'pointer',
});
export const body = css({ display: 'flex', flexDirection: 'column', flex: '1', minHeight: '0', overflowY: 'auto', padding: '0.7rem' });
export const card = css({ border: '1px solid var(--grid)', borderRadius: '0.4rem', padding: '0.7rem', background: 'var(--bg-panel)' });
export const label = css({ color: 'var(--muted)', fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.06em' });
export const fixedField = css({ display: 'flex', alignItems: 'center', gap: '0.4rem', fontFamily: 'var(--font-mono)', fontSize: '0.78rem' });
export const line = css({ color: 'var(--muted)', fontSize: '0.76rem', lineHeight: '1.45' });
export const refs = css({ display: 'flex', flexDirection: 'column', gap: '0.3rem', listStyle: 'none', margin: '0.5rem 0', padding: '0' });
export const ref = css({
  display: 'flex',
  alignItems: 'flex-start',
  gap: '0.4rem',
  border: '1px solid var(--grid)',
  borderRadius: '0.3rem',
  padding: '0.35rem 0.45rem',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.74rem',
});
export const refText = css({ flex: '1', minWidth: '0', display: 'flex', flexDirection: 'column', gap: '0.1rem' });
export const removeButton = css({ border: 'none', background: 'transparent', color: 'var(--muted)', cursor: 'pointer', fontSize: '0.9rem', lineHeight: '1' });
export const wish = css({
  width: '100%',
  border: '1px solid var(--grid)',
  borderRadius: '0.3rem',
  background: 'var(--bg)',
  color: 'inherit',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.78rem',
  padding: '0.4rem',
  resize: 'vertical',
});
export const row = css({ display: 'flex', alignItems: 'center', gap: '0.6rem', marginTop: '0.5rem', flexWrap: 'wrap' });
export const spacer = css({ flex: '1' });
export const primaryButton = css({
  border: '1px solid var(--purple, #8b5cf6)',
  background: 'var(--purple, #8b5cf6)',
  color: '#0b0b0f',
  borderRadius: '0.3rem',
  padding: '0.25rem 0.7rem',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.76rem',
  cursor: 'pointer',
  _disabled: { opacity: '0.45', cursor: 'not-allowed' },
});
export const dangerButton = css({
  border: '1px solid #b4533f',
  background: 'transparent',
  color: '#e0846b',
  borderRadius: '0.3rem',
  padding: '0.25rem 0.7rem',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.76rem',
  cursor: 'pointer',
});
export const steps = css({ listStyle: 'none', margin: '0.5rem 0 0', padding: '0', display: 'flex', flexDirection: 'column', gap: '0.28rem' });
export const step = css({ display: 'flex', gap: '0.45rem', fontFamily: 'var(--font-mono)', fontSize: '0.74rem', alignItems: 'baseline' });
export const stepWaiting = css({ color: 'var(--muted)' });
export const bar = css({ height: '0.32rem', background: 'var(--grid)', borderRadius: '0.2rem', overflow: 'hidden', marginTop: '0.4rem' });
export const menu = css({
  position: 'absolute',
  left: '0.4rem',
  top: '2.1rem',
  zIndex: '40',
  width: '19rem',
  border: '1px solid var(--grid)',
  borderRadius: '0.4rem',
  background: 'var(--bg-panel)',
  padding: '0.4rem',
  display: 'flex',
  flexDirection: 'column',
  gap: '0.15rem',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.74rem',
});
export const menuRow = css({ display: 'flex', gap: '0.4rem', alignItems: 'baseline', border: 'none', background: 'transparent', color: 'inherit', textAlign: 'left', cursor: 'pointer', padding: '0.2rem 0.25rem', borderRadius: '0.25rem' });
export const notice = css({ color: 'amber', fontFamily: 'var(--font-mono)', fontSize: '0.72rem', margin: '0.4rem 0' });
export const failureLine = css({ color: '#e0846b', fontFamily: 'var(--font-mono)', fontSize: '0.74rem', margin: '0.4rem 0 0' });
export const layer = css({ position: 'relative' });
