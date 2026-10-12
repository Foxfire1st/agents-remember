// DetailPanel style tokens (Panda recipes): the phase stepper, the lifecycle body, spine
// lanes, the change-set bar and the code example of a task document. One recipe per semantic
// axis. The task page's own look lives in pageStyles.ts.
import { css, cva } from '../../../styled-system/css';

export const sizing = css({ flex: '1' });
export const where = css({ fontSize: '0.76rem', color: 'muted', marginBottom: '0.4rem' });

export const stepper = css({
  listStyle: 'none',
  margin: '0.4rem 0',
  padding: '0',
  display: 'flex',
  flexWrap: 'wrap',
  gap: '0.3rem',
});
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
export const taskHead = css({ fontSize: '0.82rem' });
export const slices = css({
  listStyle: 'none',
  margin: '0.3rem 0 0',
  padding: '0',
  display: 'grid',
  gap: '0.15rem',
});
export const sliceMeta = css({ color: 'muted', fontSize: '0.72rem' });
// The direct-execution grant control on a leaf row (MIK-R94 rule 2): a small inline
// action beside the drill-in button, never nested inside it.
export const grantButton = css({
  fontSize: "0.72rem",
  color: "muted",
  textDecoration: "underline",
  textUnderlineOffset: "2px",
  cursor: "pointer",
  background: "transparent",
  border: "0",
  padding: "0",
  marginLeft: "0.5rem",
  _hover: { color: "ink" },
  _disabled: { opacity: 0.5, cursor: "default" },
});
// A clickable sub-task row (drill-in) and the breadcrumb back from a slice to the series.
export const sliceButton = css({
  display: 'flex',
  justifyContent: 'space-between',
  gap: '0.5rem',
  width: '100%',
  textAlign: 'left',
  font: 'inherit',
  fontSize: '0.78rem',
  paddingInline: '0.4rem',
  paddingBlock: '0.2rem',
  background: 'bg',
  border: '0',
  borderLeftWidth: '2px',
  borderLeftStyle: 'solid',
  borderLeftColor: 'cyan',
  color: 'ink',
  cursor: 'pointer',
  _hover: { background: 'oklch(0.7 0.1 200 / 0.12)', borderLeftColor: 'amber' },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '1px' },
});
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
