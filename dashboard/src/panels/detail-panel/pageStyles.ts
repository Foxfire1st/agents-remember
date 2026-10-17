// MIK-R127 rule 10: the look of the task page — layout, density and bounds of the three
// proposal pages — as Panda recipes. The page sizes itself by its own panel and not by the
// window: the detail panel is one column of the cockpit and its rails can be dragged, so the
// three widths below are container widths. Narrow is the default, `36rem` adds the columns a
// middle panel holds, `56rem` is the proposal's full row.
import { css, cva } from '../../../styled-system/css';

// The ground of code on the page: the proposal's value, a shade lighter than the page.
// The dashboard has no token for it, as it has none for dim and line.
const CODE_GROUND = 'oklch(0.24 0.02 250)';

// The page root: one query container for every block, row and view below it. The page
// stands on the proposal's ground (D223), a field of its own inside the panel that holds
// it; the panel keeps its ground.
export const page = css({
  containerType: 'inline-size',
  minWidth: '0',
  padding: '0.8rem 0.9rem 1.2rem',
  background: 'bg',
  borderRadius: '2px',
  fontSize: '0.82rem',
  lineHeight: '1.6',
  color: 'ink',
  overflowWrap: 'break-word',
  // Code inside the page takes the proposal's ground: on the page's ground the ground the
  // shared Markdown gives it could not be seen. Other views keep the shared ground.
  '& code, & pre': { background: `${CODE_GROUND}!` },
  '& pre code': { background: 'transparent!' },
  // Links inside prose follow the page: cyan, underlined only when pointed at.
  '& a, & [data-testid="requirement-link"]': { textDecoration: 'none!' },
  '& a:hover, & [data-testid="requirement-link"]:hover': { textDecoration: 'underline!' },
});

// A link drawn as a link: cyan text, no chrome, underlined when pointed at.
export const link = css({
  font: 'inherit',
  color: 'cyan',
  background: 'transparent',
  border: '0',
  padding: '0',
  textAlign: 'left',
  textDecoration: 'none',
  cursor: 'pointer',
  _hover: { textDecoration: 'underline' },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '2px' },
  _disabled: { color: 'oklch(0.6 0.02 250)', cursor: 'default', textDecoration: 'none' },
});
export const linkPlain = css({ color: 'ink', _hover: { color: 'cyan' } });
export const arrow = css({ color: 'oklch(0.6 0.02 250)' });
export const dim = css({ color: 'oklch(0.6 0.02 250)' });
export const muted = css({ color: 'muted' });
export const warn = css({ color: 'amber' });

// ---------------------------------------------------------------------------
// The head (rule 2): form, id and status on one line, title, labelled facts
// and note lines. The objective follows the complete head and series line.
// ---------------------------------------------------------------------------

export const headTop = css({
  display: 'flex',
  alignItems: 'baseline',
  flexWrap: 'wrap',
  gap: '0.3rem 0.75rem',
});
export const formBadge = cva({
  base: {
    fontSize: '0.66rem',
    letterSpacing: '0.16em',
    textTransform: 'uppercase',
    padding: '0.05rem 0.5rem',
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'currentColor',
    borderRadius: '2px',
  },
  variants: {
    form: {
      sprint: { color: 'gold' },
      master: { color: 'purple' },
      task: { color: 'cyan' },
    },
  },
});
export const docId = css({ color: 'muted' });
export const statusWord = cva({
  base: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '0.4rem',
    fontSize: '0.78rem',
    color: 'muted',
    _before: {
      content: '""',
      width: '0.5rem',
      height: '0.5rem',
      borderRadius: '50%',
      background: 'currentColor',
    },
  },
  variants: {
    mark: {
      false: { _before: { display: 'none' } },
    },
    tone: {
      work: { color: 'amber' },
      wait: {},
      done: { color: 'mint' },
      gone: { color: 'dormant' },
    },
  },
});
export const docTitle = css({
  margin: '0.45rem 0 0.7rem',
  fontSize: '1.2rem',
  lineHeight: '1.35',
  fontWeight: '600',
  letterSpacing: '0',
  textTransform: 'none',
  color: 'ink',
  maxWidth: '88ch',
});

// The objective leads the page. Closed it shows its first lines; "more" opens it in place.
export const lead = css({ display: 'grid', gap: '0.12rem', marginBottom: '0.8rem' });
export const leadText = cva({
  base: { minWidth: '0', maxWidth: '100ch' },
  variants: {
    // Closed and too tall: the first lines, the last of them fading out.
    clamped: {
      true: {
        maxHeight: '5.4rem',
        overflow: 'hidden',
        maskImage: 'linear-gradient(to bottom, black 72%, transparent 100%)',
      },
    },
  },
});
export const leadTools = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'baseline',
  gap: '0.2rem 1.4rem',
  fontSize: '0.74rem',
});

export const facts = css({
  display: 'flex',
  flexWrap: 'wrap',
  gap: '0.7rem 2.3rem',
  paddingBlock: '0.7rem',
  borderBlock: '1px solid token(colors.grid)',
});
export const fact = css({ display: 'grid', gap: '0.12rem', minWidth: '0', alignContent: 'start' });
// One label style for facts, note lines, row heads and the objective: small capitals, dimmed.
export const factLabel = css({
  fontSize: '0.64rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'oklch(0.6 0.02 250)',
});
export const factValue = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  gap: '0.25rem 0.4rem',
  fontSize: '0.8rem',
  minWidth: '0',
});
export const headNotes = css({ display: 'grid', gap: '0.1rem', paddingTop: '0.55rem' });
export const headTools = css({ marginTop: '0.7rem' });

// One line of text that opens in place: label, the clipped text, and "more" where it is cut.
export const clip = css({ display: 'flex', alignItems: 'baseline', gap: '0.75rem', minWidth: '0' });
export const clipLabel = css({
  flex: 'none',
  width: '6.5rem',
  fontSize: '0.64rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'oklch(0.6 0.02 250)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  '@container (min-width: 36rem)': { width: '9.5rem' },
});
export const clipText = cva({
  base: { flex: '1', minWidth: '0', fontSize: '0.8rem' },
  variants: {
    open: {
      false: { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
      true: { whiteSpace: 'normal', maxWidth: '110ch' },
    },
    tone: { muted: { color: 'muted' }, ink: { color: 'ink' } },
  },
  defaultVariants: { open: false, tone: 'muted' },
});
export const clipMore = css({ flex: 'none', fontSize: '0.72rem' });

// ---------------------------------------------------------------------------
// Blocks: a heading in amber capitals with its count, then the content.
// ---------------------------------------------------------------------------

export const block = css({ marginTop: '2rem', minWidth: '0' });
export const blockHead = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'baseline',
  gap: '0.1rem 0.7rem',
  marginBottom: '0.5rem',
});
export const blockLabel = css({
  margin: '0',
  fontSize: '0.72rem',
  fontWeight: '600',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'amber',
});
export const count = css({ fontSize: '0.72rem', color: 'oklch(0.6 0.02 250)' });
export const sub = css({
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.6rem',
  margin: '1rem 0 0.25rem',
  fontSize: '0.68rem',
  fontWeight: '400',
  letterSpacing: '0.09em',
  textTransform: 'uppercase',
  color: 'muted',
  '&:first-child': { marginTop: '0' },
  '& > span': { textTransform: 'none', letterSpacing: '0.02em' },
});
export const none = css({ margin: '0', color: 'oklch(0.6 0.02 250)', fontSize: '0.78rem' });
export const intro = css({ marginBottom: '0.6rem', maxWidth: '100ch' });
export const lineRow = css({
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.2rem 0.75rem',
  flexWrap: 'wrap',
  fontSize: '0.8rem',
});
export const more = css({ display: 'inline-block', marginTop: '0.5rem', fontSize: '0.78rem' });
export const prose = css({ maxWidth: '100ch' });

// ---------------------------------------------------------------------------
// One-line rows. A list is one grid and every row a subgrid of it, so a column
// is as wide as its widest cell in the whole list and the cells line up.
// ---------------------------------------------------------------------------

export const rows = cva({
  base: {
    display: 'grid',
    columnGap: '0.6rem',
    minWidth: '0',
    borderBottom: '1px solid oklch(0.3 0.02 250 / 0.55)',
    '@container (min-width: 36rem)': { columnGap: '0.9rem' },
  },
  variants: {
    cols: {
      // one cell: requirement lines, open questions
      one: { gridTemplateColumns: 'minmax(0, 1fr)' },
      // text and a short fact at the right: further notes, reports, proposed code
      pair: { gridTemplateColumns: 'minmax(0, 1fr) auto' },
      // a name and how much stands behind it: the size below the name while narrow
      sized: {
        gridTemplateColumns: 'minmax(0, 1fr)',
        '@container (min-width: 36rem)': { gridTemplateColumns: 'minmax(0, 1fr) auto' },
      },
      // a link and the place it leads to: references
      link: {
        gridTemplateColumns: 'minmax(0, 1fr)',
        '@container (min-width: 36rem)': { gridTemplateColumns: 'fit-content(60%) minmax(0, 1fr)' },
      },
      // id and text: open findings
      finding: {
        gridTemplateColumns: 'minmax(0, 1fr)',
        '@container (min-width: 36rem)': { gridTemplateColumns: 'max-content minmax(0, 1fr)' },
      },
      // id, state and text: all findings
      findingAll: {
        gridTemplateColumns: 'minmax(0, 1fr) auto',
        '@container (min-width: 36rem)': {
          gridTemplateColumns: 'max-content 4rem minmax(0, 1fr)',
        },
      },
      // mark, number, title, state: the steps of a task
      steps: { gridTemplateColumns: '0.7rem max-content minmax(0, 1fr) auto' },
      // number, time, text: decisions
      decisions: {
        gridTemplateColumns: 'max-content minmax(0, 1fr)',
        '@container (min-width: 36rem)': {
          gridTemplateColumns: 'max-content max-content minmax(0, 1fr)',
        },
      },
      // a master's tasks in work: task, steps done, step, review
      masterNow: {
        gridTemplateColumns: 'max-content minmax(0, 1fr) max-content',
        '@container (min-width: 36rem)': {
          gridTemplateColumns: 'max-content minmax(0, 1fr) max-content max-content',
        },
        '@container (min-width: 56rem)': {
          gridTemplateColumns:
            'max-content minmax(0, 1.1fr) max-content minmax(0, 0.9fr) max-content',
        },
      },
      // a sprint's masters in work: master, tasks completed, in work
      sprintNow: {
        gridTemplateColumns: 'max-content minmax(0, 1fr)',
        '@container (min-width: 36rem)': {
          gridTemplateColumns: 'max-content minmax(0, 1fr) max-content max-content',
        },
        '@container (min-width: 56rem)': {
          gridTemplateColumns: 'max-content minmax(0, 1fr) 13rem 6rem',
        },
      },
      // a master's waiting tasks: task, requirement, steps
      taskWait: {
        gridTemplateColumns: 'max-content minmax(0, 1fr) max-content',
        '@container (min-width: 36rem)': {
          gridTemplateColumns: 'max-content minmax(0, 1fr) max-content max-content',
        },
      },
      // a sprint's waiting masters: master, execution, tasks, waits for
      masterWait: {
        gridTemplateColumns: 'max-content minmax(0, 1fr)',
        '@container (min-width: 36rem)': {
          gridTemplateColumns: 'max-content minmax(0, 1fr) max-content max-content',
        },
        '@container (min-width: 56rem)': {
          gridTemplateColumns: 'max-content minmax(0, 1fr) 7.5rem 4rem minmax(0, 17rem)',
        },
      },
      // a completed or abandoned group: id, name, one fact
      group: { gridTemplateColumns: 'max-content minmax(0, 1fr) max-content' },
      // the discarded-before-start audit: number, name, reason
      discarded: {
        gridTemplateColumns: 'max-content minmax(0, 1fr)',
        '@container (min-width: 36rem)': {
          gridTemplateColumns: 'max-content minmax(0, 18rem) minmax(0, 1fr)',
        },
      },
    },
  },
});

export const row = cva({
  base: {
    display: 'grid',
    gridTemplateColumns: 'subgrid',
    gridColumn: '1 / -1',
    alignItems: 'baseline',
    minWidth: '0',
    padding: '0.27rem 0.5rem',
    borderTop: '1px solid oklch(0.3 0.02 250 / 0.55)',
    fontSize: '0.8rem',
    '& > *': { minWidth: '0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  },
  variants: {
    kind: {
      body: { _hover: { background: 'oklch(0.7 0.1 200 / 0.07)' } },
      // the line of column names above a list
      head: {
        paddingBlock: '0.1rem 0.2rem',
        borderTop: '0',
        fontSize: '0.64rem',
        letterSpacing: '0.1em',
        textTransform: 'uppercase',
        color: 'oklch(0.6 0.02 250)',
      },
    },
    opens: { true: { cursor: 'pointer' } },
    open: {
      true: {
        paddingBlock: '0.55rem',
        '& > *': { whiteSpace: 'normal', overflow: 'visible' },
      },
    },
  },
  defaultVariants: { kind: 'body' },
});

// The id and the name of a row are one link that covers both columns.
export const rowLink = css({
  display: 'grid',
  gridTemplateColumns: 'subgrid',
  gridColumn: 'span 2',
  font: 'inherit',
  color: 'ink',
  background: 'transparent',
  border: '0',
  padding: '0',
  textAlign: 'left',
  cursor: 'pointer',
  '& > *': { minWidth: '0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  '&:hover > :last-child': {
    textDecoration: 'underline',
    textDecorationColor: 'token(colors.cyan)',
  },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '2px' },
});
export const rowStatic = css({
  display: 'grid',
  gridTemplateColumns: 'subgrid',
  gridColumn: 'span 2',
  '& > *': { minWidth: '0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
});
export const rowId = cva({
  base: {},
  variants: { tone: { link: { color: 'cyan' }, plain: { color: 'muted' } } },
  defaultVariants: { tone: 'link' },
});
export const cellRight = css({ textAlign: 'right' });
// Why a sub-task was discarded: under its name while narrow, in its own column after.
export const discardedWhy = css({
  gridColumn: '2 / -1',
  whiteSpace: 'normal!',
  overflow: 'visible!',
  '@container (min-width: 36rem)': { gridColumn: 'auto' },
});
// The text of a finding: under its id while the list is narrow, in its own column after.
export const findingText = css({
  gridColumn: '1 / -1',
  whiteSpace: 'normal!',
  overflow: 'visible!',
  '@container (min-width: 36rem)': { gridColumn: 'auto' },
});
export const cellTwo = css({ gridColumn: 'span 2' });
export const cellWrap = css({ whiteSpace: 'normal!', overflow: 'visible!' });

// Where a cell stands at each width. `below` puts it on a second line under the name.
export const cell = cva({
  base: {},
  variants: {
    at: {
      // shown from the middle width on
      mid: { display: 'none', '@container (min-width: 36rem)': { display: 'block' } },
      // shown at the full width only
      wide: { display: 'none', '@container (min-width: 56rem)': { display: 'block' } },
      // a second line below the name until the row has a column for it
      belowUntilMid: {
        gridColumn: '2 / -1',
        gridRow: '2',
        fontSize: '0.74rem',
        '@container (min-width: 36rem)': {
          gridColumn: 'auto',
          gridRow: 'auto',
          fontSize: '0.8rem',
        },
      },
      belowUntilWide: {
        gridColumn: '2 / -1',
        gridRow: '2',
        fontSize: '0.74rem',
        '@container (min-width: 56rem)': {
          gridColumn: 'auto',
          gridRow: 'auto',
          fontSize: '0.8rem',
        },
      },
    },
  },
});
// Pinned columns of the in-work table, so the step may stand below without moving the review.
export const nowReview = css({
  gridColumn: '2 / -1',
  gridRow: '2',
  fontSize: '0.74rem',
  '@container (min-width: 36rem)': { gridColumn: '4', gridRow: '1', fontSize: '0.8rem' },
  '@container (min-width: 56rem)': { gridColumn: '5' },
});
export const nowStep = css({
  display: 'none',
  '@container (min-width: 36rem)': {
    display: 'block',
    gridColumn: '2 / -1',
    gridRow: '2',
    fontSize: '0.74rem',
  },
  '@container (min-width: 56rem)': { gridColumn: '4', gridRow: '1', fontSize: '0.8rem' },
});

// ---------------------------------------------------------------------------
// Status colours: chips, step marks, progress bars.
// ---------------------------------------------------------------------------

// A line that stands a little apart from the rows above it.
export const apart = css({ marginTop: '0.7rem' });
export const chips = css({ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' });
export const chip = cva({
  base: {
    display: 'block',
    flex: 'none',
    paddingInline: '0.4rem',
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'grid',
    borderRadius: '2px',
    fontSize: '0.72rem',
    lineHeight: '1.5',
    whiteSpace: 'nowrap',
    color: 'muted',
    maxWidth: '100%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  variants: {
    tone: {
      work: { color: 'amber', borderColor: 'oklch(0.82 0.16 75 / 0.7)' },
      wait: {},
      done: { color: 'mint', borderColor: 'oklch(0.88 0.16 165 / 0.4)' },
      gone: { color: 'dormant' },
    },
  },
  defaultVariants: { tone: 'wait' },
});
export const pips = css({
  display: 'inline-flex',
  gap: '2px',
  verticalAlign: 'middle',
  marginRight: '0.5rem',
});
export const pip = cva({
  base: {
    display: 'inline-block',
    width: '0.56rem',
    height: '0.56rem',
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'grid',
    borderRadius: '2px',
    flex: 'none',
  },
  variants: {
    state: {
      done: { background: 'mint', borderColor: 'mint' },
      current: { background: 'amber', borderColor: 'amber' },
      blocked: { background: 'alarm', borderColor: 'alarm' },
      next: { borderColor: 'amber' },
      pending: {},
    },
  },
  defaultVariants: { state: 'pending' },
});
export const bar = css({
  display: 'inline-flex',
  width: '5.5rem',
  height: '0.42rem',
  marginRight: '0.6rem',
  background: 'grid',
  borderRadius: '1px',
  overflow: 'hidden',
  verticalAlign: 'middle',
  '@container (min-width: 56rem)': { width: '7.5rem' },
});
export const barDone = css({ background: 'mint' });
export const barWork = css({ background: 'amber' });

// ---------------------------------------------------------------------------
// The steps of a task: mark, number, title, and the state at the right.
// ---------------------------------------------------------------------------

export const stepMark = css({ alignSelf: 'center' });
export const stepId = css({ color: 'muted' });
// A step's title is read whole: a long one takes a second line and is never cut.
export const stepTitle = cva({
  base: { whiteSpace: 'normal!', overflow: 'visible!', overflowWrap: 'anywhere' },
  variants: { done: { true: { color: 'muted' } } },
});
export const stepState = cva({
  base: { fontSize: '0.72rem', color: 'oklch(0.6 0.02 250)', textAlign: 'right' },
  variants: { hot: { true: { color: 'amber' } } },
});
// What stands under a step's title: its note, its skip reason, its substeps.
export const stepUnder = css({
  gridColumn: '3 / -1',
  whiteSpace: 'normal!',
  overflow: 'visible!',
  minWidth: '0',
});
export const substepList = css({
  listStyle: 'none',
  margin: '0.1rem 0 0',
  padding: '0',
  display: 'grid',
  gap: '0.05rem',
  fontSize: '0.74rem',
  color: 'muted',
});
export const substep = cva({
  base: { display: 'flex', alignItems: 'baseline', gap: '0.5rem', minWidth: '0' },
  variants: { hot: { true: { color: 'amber' } } },
});

// ---------------------------------------------------------------------------
// Decisions: number, time and one line; the row opens the rationale in place.
// ---------------------------------------------------------------------------

export const decisionReading = css({ maxWidth: '100ch', marginInline: 'auto', minWidth: '0' });
export const productRecords = css({
  color: 'muted',
  marginTop: '1rem',
  fontSize: '0.74rem',
  '& summary': { cursor: 'pointer' },
  '& ul': { paddingLeft: '1.1rem' },
  '& li': { marginBlock: '0.6rem' },
});
export const decisionNumber = css({ color: 'oklch(0.6 0.02 250)' });
export const decisionAt = css({
  color: 'muted',
  fontSize: '0.72rem',
  '@container (min-width: 36rem)': { fontSize: '0.8rem' },
});
export const decisionText = css({
  gridColumn: '2 / -1',
  '@container (min-width: 36rem)': { gridColumn: 'auto' },
});
export const decisionWhy = css({
  gridColumn: '2 / -1',
  marginTop: '0.3rem',
  color: 'muted',
  maxWidth: '110ch',
  '@container (min-width: 36rem)': { gridColumn: '3' },
});

// ---------------------------------------------------------------------------
// Register tables: the first columns show, a row opens the rest.
// ---------------------------------------------------------------------------

export const tableFrame = css({ overflowX: 'auto', maxWidth: '100%' });
export const table = css({
  width: '100%',
  borderCollapse: 'collapse',
  tableLayout: 'fixed',
  fontSize: '0.78rem',
  '& th': {
    padding: '0.25rem 0.5rem',
    textAlign: 'left',
    fontWeight: '400',
    fontSize: '0.64rem',
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    color: 'oklch(0.6 0.02 250)',
    borderBottom: '1px solid token(colors.grid)',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  '& td': {
    padding: '0.27rem 0.5rem',
    verticalAlign: 'top',
    color: 'muted',
    borderBottom: '1px solid oklch(0.3 0.02 250 / 0.55)',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  '& td:first-child': { color: 'ink' },
});
export const tableRow = cva({
  base: { cursor: 'pointer', _hover: { background: 'oklch(0.7 0.1 200 / 0.07)' } },
  variants: {
    open: { true: { '& td': { whiteSpace: 'normal', overflowWrap: 'anywhere' } } },
  },
});
export const tableDetail = css({
  '& td': { whiteSpace: 'normal', overflowWrap: 'anywhere' },
});

// ---------------------------------------------------------------------------
// A view of its own: back, the title and its count, then the whole content.
// ---------------------------------------------------------------------------

export const viewHead = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'baseline',
  gap: '0.2rem 1.1rem',
  marginBottom: '1.1rem',
  paddingBottom: '0.6rem',
  borderBottom: '1px solid token(colors.grid)',
});
export const viewTitle = css({
  margin: '0',
  fontSize: '0.98rem',
  fontWeight: '600',
  letterSpacing: '0',
  textTransform: 'none',
  color: 'ink',
});
export const viewBack = css({ fontSize: '0.8rem', whiteSpace: 'nowrap' });

// ---------------------------------------------------------------------------
// The order of a sprint's masters: one row per wave, one mark per master.
// ---------------------------------------------------------------------------

export const orderRow = cva({
  base: {
    display: 'grid',
    gridTemplateColumns: '4.6rem minmax(0, 1fr)',
    columnGap: '0.5rem',
    paddingBlock: '0.42rem',
    borderBottom: '1px solid oklch(0.3 0.02 250 / 0.55)',
    '@container (min-width: 56rem)': {
      gridTemplateColumns: '5.4rem minmax(0, 1fr)',
      columnGap: '0.8rem',
    },
  },
  variants: { now: { true: {} } },
});
export const orderLabel = cva({
  base: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: '0.4rem',
    padding: '0.22rem 0.5rem 0 0',
    fontSize: '0.64rem',
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    color: 'oklch(0.6 0.02 250)',
  },
  variants: { now: { true: { color: 'amber' } } },
});
export const orderCount = css({ color: 'muted', letterSpacing: '0' });
export const orderNodes = cva({
  base: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(13.5rem, 1fr))',
    gap: '0.3rem 0.4rem',
    minWidth: '0',
    '@container (min-width: 56rem)': {
      gridTemplateColumns: 'repeat(auto-fill, minmax(19rem, 1fr))',
    },
  },
  variants: {
    now: {
      true: {
        '@container (min-width: 56rem)': {
          gridTemplateColumns: 'repeat(auto-fill, minmax(23rem, 1fr))',
        },
      },
    },
  },
});
export const orderNode = cva({
  base: {
    display: 'flex',
    alignItems: 'baseline',
    gap: '0.5rem',
    minWidth: '0',
    padding: '0.1rem 0.45rem',
    font: 'inherit',
    fontSize: '0.74rem',
    textAlign: 'left',
    color: 'muted',
    background: 'transparent',
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'grid',
    borderLeftWidth: '3px',
    borderRadius: '2px',
    outline: '1px solid transparent',
    outlineOffset: '1px',
    cursor: 'pointer',
    _focusVisible: { outlineColor: 'amber' },
  },
  variants: {
    // A started task is in work and wears the golden border; one that waits for nothing
    // unfinished is ready; the rest wait.
    state: {
      work: {
        paddingBlock: '0.3rem',
        fontSize: '0.8rem',
        color: 'ink',
        borderColor: 'oklch(0.82 0.16 75 / 0.75)',
        borderLeftColor: 'amber',
        background: 'oklch(0.82 0.16 75 / 0.09)',
      },
      ready: { color: 'ink', borderLeftColor: 'cyan' },
      waits: {},
    },
    // The mark of rule 11: it fades in and out, unless the reader asks for calm.
    fade: {
      true: {
        transition:
          'opacity 150ms ease, color 150ms ease, border-color 150ms ease, outline-color 150ms ease',
      },
    },
    mark: {
      none: {},
      self: { outlineColor: 'ink' },
      related: { borderColor: 'ink', color: 'ink' },
      other: { opacity: '0.3' },
    },
  },
  defaultVariants: { state: 'waits', mark: 'none' },
});
export const orderNodeId = cva({
  base: { flex: 'none', color: 'oklch(0.6 0.02 250)' },
  variants: { state: { work: { color: 'amber' }, ready: { color: 'cyan' }, waits: {} } },
});
export const orderNodeTitle = css({
  flex: '1',
  minWidth: '0',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
});
export const orderNodeMeta = cva({
  base: { flex: 'none', fontSize: '0.68rem', color: 'oklch(0.6 0.02 250)' },
  variants: { state: { work: { color: 'muted' }, ready: {}, waits: {} } },
});
// The rows of the order stand under one line, as every list of the page does.
export const orderList = css({ borderTop: '1px solid token(colors.grid)' });
// The line under the rows: the key to the marks, or what the marked master waits for.
export const orderRead = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'baseline',
  gap: '0.25rem 1.2rem',
  minHeight: '1.9rem',
  paddingTop: '0.45rem',
  fontSize: '0.74rem',
  color: 'muted',
});
export const orderKey = cva({
  base: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '0.35rem',
    _before: {
      content: '""',
      width: '0.75rem',
      height: '0.6rem',
      borderWidth: '1px',
      borderStyle: 'solid',
      borderColor: 'grid',
      borderLeftWidth: '3px',
      borderRadius: '2px',
    },
  },
  variants: {
    state: {
      work: { _before: { borderColor: 'amber', background: 'oklch(0.82 0.16 75 / 0.14)' } },
      ready: { _before: { borderLeftColor: 'cyan' } },
    },
  },
});
export const orderNote = css({
  fontSize: '0.72rem',
  color: 'oklch(0.6 0.02 250)',
  paddingTop: '0.2rem',
});
