// DetailPanel style tokens: step marks, worktree lanes, the change-set bar and task code.
// The task page's own look lives in pageStyles.ts.
import { css, cva } from '../../../styled-system/css';

export const sizing = css({ flex: '1' });
export const where = css({ fontSize: '0.76rem', color: 'muted', marginBottom: '0.4rem' });

export const step = cva({
  base: {
    fontSize: '0.72rem',
    letterSpacing: '0.04em',
    paddingInline: '0.4rem',
    paddingBlock: '0.15rem',
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'grid',
    borderRadius: '2px',
    color: 'oklch(0.6 0.02 250)',
  },
  variants: {
    state: {
      todo: {},
      done: { color: 'cyan', borderColor: 'cyan' },
      current: {
        color: 'amber',
        borderColor: 'amber',
        textShadow: '0 0 calc(5px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.5)',
      },
    },
  },
});

export const badge = css({
  fontSize: '0.68rem',
  paddingInline: '0.35rem',
  paddingBlock: '0.05rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  color: 'muted',
});

export const series = css({ margin: '0.5rem 0' });
export const slices = css({
  listStyle: 'none',
  margin: '0.3rem 0 0',
  padding: '0',
  display: 'grid',
  gap: '0.15rem',
});
// One switch on the task's own page for the direct-execution grant (MIK-R150
// rules 9 to 13): a label, a track with a knob, and the served words. The track
// carries the position through its alignment, off at the left and on at the
// right, in the dashboard's own tokens.
export const directLine = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.5rem',
  fontSize: '0.78rem',
  color: 'ink',
  margin: '0.3rem 0 0',
});
export const directSwitch = css({
  width: '2rem',
  height: '1.1rem',
  borderRadius: '999px',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  background: 'bg',
  display: 'inline-flex',
  alignItems: 'center',
  padding: '0.12rem',
  cursor: 'pointer',
});
export const directSwitchOff = css({ justifyContent: 'flex-start' });
export const directSwitchOn = css({ justifyContent: 'flex-end', background: 'oklch(0.7 0.1 200 / 0.12)', borderColor: 'amber' });
export const directKnob = css({
  width: '0.75rem',
  height: '0.75rem',
  borderRadius: '999px',
  background: 'muted',
});
export const directGiven = css({ color: 'muted', fontSize: '0.74rem' });
export const directNote = css({ color: 'amber', fontSize: '0.74rem' });
// The breadcrumb back from a slice to the series.
export const crumb = css({
  font: 'inherit',
  fontSize: '0.76rem',
  color: 'cyan',
  background: 'transparent',
  border: '0',
  padding: '0',
  marginBottom: '0.5rem',
  cursor: 'pointer',
  _hover: { textDecoration: 'underline' },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '1px' },
});

export const spine = css({ margin: '0.6rem 0' });
export const spineHead = css({
  fontSize: '0.72rem',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  color: 'muted',
  marginBottom: '0.3rem',
});
export const lanes = css({ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' });
export const lane = cva({
  base: {
    display: 'grid',
    gap: '0.25rem',
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'grid',
    borderRadius: '3px',
    padding: '0.4rem 0.5rem',
    borderLeftWidth: '2px',
  },
  variants: { kind: { code: { borderLeftColor: 'amber' }, memory: { borderLeftColor: 'cyan' } } },
});
export const laneTitle = css({ fontSize: '0.76rem', color: 'ink' });
export const laneRepo = css({ fontSize: '0.74rem', color: 'muted' });
export const laneMeta = css({
  display: 'flex',
  flexWrap: 'wrap',
  gap: '0.5rem',
  fontSize: '0.72rem',
  color: 'muted',
});

export const changeSetBar = css({
  display: 'flex',
  flexWrap: 'wrap',
  gap: '0.4rem',
  marginBottom: '0.4rem',
});
export const changeSetBtn = css({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.35rem',
  fontSize: '0.72rem',
  fontFamily: 'mono',
  color: 'cyan',
  background: 'transparent',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  paddingInline: '0.45rem',
  paddingBlock: '0.15rem',
  cursor: 'pointer',
  _hover: { borderColor: 'cyan', color: 'ink' },
});
export const changeSetCounts = css({ color: 'muted' });
// A change-set or Intent review control's state detail: a brief label stays in the control and the
// owner's full explanation is one click away, so an entry never prints a backend paragraph inline.
export const entryStateDetails = css({
  display: 'inline-block',
  fontSize: '0.72rem',
  fontFamily: 'mono',
  color: 'muted',
  maxWidth: '100%',
  '&[open]': { flexBasis: '100%' },
});
export const entryStateSummary = css({
  display: 'inline-block',
  cursor: 'pointer',
  listStyle: 'none',
  paddingInline: '0.3rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  '&::-webkit-details-marker': { display: 'none' },
  _hover: { borderColor: 'cyan', color: 'ink' },
});
export const entryStateBody = css({
  margin: '0.3rem 0 0',
  maxWidth: '72ch',
  lineHeight: '1.4',
  overflowWrap: 'anywhere',
});

export const label = css({
  fontSize: '0.72rem',
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  color: 'muted',
});

export const taskdocCode = css({
  display: 'grid',
  gap: '0.2rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  padding: '0.45rem 0.55rem',
});
export const taskdocCodeHead = css({ color: 'amber', fontSize: '0.82rem' });
export const taskdocCodeMeta = css({ fontSize: '0.78rem', color: 'muted' });
export const taskdocSnippet = css({
  margin: '0.2rem 0 0',
  padding: '0.5rem 0.6rem',
  background: 'bg',
  borderRadius: '2px',
  overflow: 'auto',
  fontSize: '0.78rem',
  lineHeight: '1.45',
});
