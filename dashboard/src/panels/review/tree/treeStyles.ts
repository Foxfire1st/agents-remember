// The review tree's look (mockup styles.css:140-165): one sticky column with a head, one control
// line and one scroller, every row one line. Heights come from CSS only (MIK-R89 v2 rule 9).
import { css } from '../../../../styled-system/css';

export const aside = css({
  containerType: 'inline-size',
  containerName: 'tree',
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
  minWidth: 0,
  height: '100%',
  background: 'bgPanel',
  border: '1px solid token(colors.grid)',
  borderRadius: '3px',
});
// The heading line: "Change set", the counts and the one "All changes" link (MIK-R89 v2 rule 7). In
// the 1440 tree (320 px or less) its padding, gaps and the heading's letter spacing tighten, so the
// counts stay whole beside the link (measured at 292 px: 3 px to spare).
export const head = css({
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.5rem',
  minWidth: 0,
  padding: '0.55rem 0.55rem 0.3rem 0.8rem',
  whiteSpace: 'nowrap',
  '@container tree (max-width: 320px)': { gap: '0.3rem', paddingLeft: '0.5rem', paddingRight: '0.4rem' },
});
export const heading = css({
  margin: 0,
  fontSize: '0.72rem',
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
  color: 'cyan',
  fontWeight: 700,
  '@container tree (max-width: 320px)': { letterSpacing: '0.1em' },
});
export const headCounts = css({
  flex: 1,
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  color: 'muted',
  fontSize: '0.7rem',
});
// The control line (rule 3): the filter, Changed | All and Sort on one line inside the tree at
// 340 px (1920) and 292 px (1440; rule 11). It gives way before anything leaves it, so a focus or an
// opened menu never scrolls the tree sideways: the filter takes what is left and keeps a readable
// 5rem, as the mockup's filter is read at both widths (D401); the view switch keeps its size; Sort
// shortens its key with an ellipsis and keeps its ▾. In the 1440 tree (320 px or less) the padding and
// gaps tighten and Sort drops its drawn word "Sort:" (`sortWord`; its name keeps it), which leaves the
// filter about 85 px there.
export const tools = css({
  display: 'flex',
  flexWrap: 'nowrap',
  alignItems: 'center',
  gap: '0.4rem',
  minWidth: 0,
  padding: '0 0.8rem 0.55rem',
  borderBottom: '1px solid token(colors.grid)',
  '@container tree (max-width: 320px)': { gap: '0.3rem', paddingInline: '0.5rem' },
});
// No clear icon: the mockup's filter draws none, and Escape clears it (ReviewTree.tsx).
export const input = css({
  flex: '1 1 0',
  minWidth: '5rem',
  background: 'bg',
  color: 'ink',
  border: '1px solid token(colors.grid)',
  font: 'inherit',
  fontSize: '0.75rem',
  padding: '0.25rem 0.35rem',
  '@container tree (max-width: 320px)': { paddingInline: '0.3rem' },
  '&::-webkit-search-cancel-button': { appearance: 'none', display: 'none' },
});
export const segment = css({ display: 'flex', flexShrink: 0 });
// Sort's parts ("Sort:", its key, ▾) are flex items, which drop the spaces between them: the gap of
// half a character stands in for them.
export const control = css({
  display: 'inline-flex',
  alignItems: 'baseline',
  gap: '0.5ch',
  minWidth: 0,
  overflow: 'hidden',
  background: 'transparent',
  color: 'muted',
  border: '1px solid token(colors.grid)',
  font: 'inherit',
  fontSize: '0.72rem',
  padding: '0.25rem 0.4rem',
  whiteSpace: 'nowrap',
  cursor: 'pointer',
  '@container tree (max-width: 320px)': { paddingInline: '0.3rem' },
  _pressed: { color: 'amber', borderColor: 'amber' },
  '&[aria-pressed=true]': { color: 'amber', borderColor: 'amber' },
  _disabled: { cursor: 'default', opacity: 0.6 },
  _focusVisible: { outline: '1px solid var(--amber)' },
});
export const sortKey = css({
  color: 'amber',
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
});
export const sortWord = css({ '@container tree (max-width: 320px)': { display: 'none' } });
// Spoken, not drawn: a polite line with nothing to show, and a count the line already shows.
export const unseen = css({ srOnly: true });
export const status = css({ color: 'muted', fontSize: '0.72rem', margin: '0.3rem 0.8rem 0' });
// The tree's only inner scroller (rule 1): contained, so the wheel never scrolls the page from here.
// It is full height beside the column; only the stacked layout (40rem and narrower, the tree above the
// column) caps it. Its rows are one column, so the pinned block can take the space left above it.
export const scroller = css({
  display: 'flex',
  flexDirection: 'column',
  flex: 1,
  minHeight: 0,
  overflowY: 'auto',
  overflowX: 'hidden',
  overscrollBehavior: 'contain',
  padding: '0.35rem 0 0',
  '@media (max-width: 40rem)': { maxHeight: '45dvh' },
});
export const list = css({ listStyle: 'none', margin: 0, padding: 0, minWidth: 0 });
// The tree's rows end with the mockup's 0.6rem before the pinned block (styles.css:144).
export const branches = css({ paddingBottom: '0.6rem' });
// One line per row (D48; INV-PKFP2R): a caret, the ellipsized name and one reason.
export const row = css({
  display: 'grid',
  gridTemplateColumns: '0.9rem auto minmax(0, 1fr) auto',
  gap: '0.35rem',
  alignItems: 'baseline',
  width: '100%',
  minWidth: 0,
  padding: '0.16rem 0.7rem 0.16rem 0.55rem',
  background: 'transparent',
  border: 'none',
  borderLeft: '2px solid transparent',
  color: 'ink',
  cursor: 'pointer',
  font: 'inherit',
  fontSize: '0.76rem',
  lineHeight: '1.5',
  textAlign: 'left',
  whiteSpace: 'nowrap',
  _hover: { background: 'color-mix(in oklab, var(--amber) 8%, transparent)' },
  _focusVisible: { outline: '1px solid var(--amber)' },
  '&[aria-current=true]': {
    background: 'color-mix(in oklab, var(--amber) 16%, transparent)',
    borderLeftColor: 'amber',
    color: 'amber',
  },
});
export const child = css({ paddingLeft: '1.5rem', fontSize: '0.74rem' });
export const grandchild = css({ paddingLeft: '2.3rem', fontSize: '0.74rem' });
export const caret = css({ color: 'muted', fontSize: '0.7rem' });
export const name = css({ overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 });
export const id = css({ color: 'muted', marginRight: '0.45rem' });
export const dim = css({
  color: 'muted',
  marginLeft: '0.4rem',
  '@container tree (max-width: 320px)': { display: 'none' },
});
export const reason = css({ color: 'muted', fontSize: '0.7rem', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' });
// A row's line counts (MIK-R89 v2 rule 1 as amended by 719ade09; mockup styles.css:95-96): "+a" in
// the theme's mint, "−r" in its alarm red. The signs stay in the text.
export const ins = css({ color: 'mint' });
export const del = css({ color: 'alarm' });
export const hot = css({ color: 'amber' });
export const effect = css({ color: 'cyan' });
export const proof = css({ color: 'mint', marginRight: '0.35rem', fontSize: '0.7rem' });
export const label = css({ color: 'muted', fontSize: '0.72rem', margin: '0.35rem 0.8rem 0.1rem' });
export const groupRow = css({ color: 'muted' });
export const empty = css({ color: 'muted', fontSize: '0.72rem', padding: '0.6rem 0.8rem', margin: 0 });
// Worklist and "Code without invariant" are pinned at the bottom of the tree (MIK-R86 v2 rule 4,
// MIK-R89 v2 rule 7; mockup styles.css:145): the block takes the space left above it, so under a short
// tree it sits at the panel's bottom, and over a long one it sticks to the scroller's bottom. The
// lane's file rows follow it inside the same scroller (W14).
export const pinned = css({
  position: 'sticky',
  bottom: 0,
  zIndex: 1,
  background: 'bgPanel',
  borderTop: '1px solid token(colors.grid)',
  padding: '0.3rem 0',
  marginTop: 'auto',
});
export const laneHead = css({ display: 'flex', alignItems: 'baseline', minWidth: 0 });
export const link = css({
  flexShrink: 0,
  background: 'transparent',
  border: 'none',
  color: 'cyan',
  cursor: 'pointer',
  font: 'inherit',
  fontSize: '0.7rem',
  padding: '0 0.5rem 0 0',
  whiteSpace: 'nowrap',
  _hover: { textDecoration: 'underline' },
  _focusVisible: { outline: '1px solid var(--amber)' },
});
// "All changes" in the heading line: `link` without its end padding, as the line's own padding ends
// it.
export const headLink = css({
  flexShrink: 0,
  background: 'transparent',
  border: 'none',
  color: 'cyan',
  cursor: 'pointer',
  font: 'inherit',
  fontSize: '0.7rem',
  padding: 0,
  whiteSpace: 'nowrap',
  _hover: { textDecoration: 'underline' },
  _focusVisible: { outline: '1px solid var(--amber)' },
});
// The Sort popup (MIK-R89 v2 rule 1 as amended by d9a1ec8f item 4; mockup styles.css:108-117,
// app.js:242): 350 px wide, the mockup's rows, a rule between the groups and one explanatory section.
export const popover = css({
  zIndex: 110,
  width: '350px',
  maxWidth: 'calc(100vw - 1.5rem)',
  background: 'bgPanel',
  color: 'ink',
  border: '1px solid token(colors.grid)',
  borderRadius: '3px',
  boxShadow: '0 6px 20px rgba(0, 0, 0, 0.5)',
  fontSize: '0.75rem',
  lineHeight: '1.5',
  padding: '0.35rem 0',
});
export const menuGroup = css({
  color: 'muted',
  fontSize: '0.64rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  padding: '0.45rem 0.8rem 0.2rem',
});
// A mark, the name, and its short reason at the row's end.
export const menuItem = css({
  display: 'grid',
  gridTemplateColumns: '1rem minmax(0, 1fr) auto',
  gap: '0.4rem',
  alignItems: 'baseline',
  padding: '0.3rem 0.8rem',
  fontSize: '0.75rem',
  lineHeight: '1.5',
  cursor: 'pointer',
  outline: 'none',
  '&[data-selected]': { color: 'amber' },
  '&:is([data-focused], [data-hovered])': {
    background: 'color-mix(in oklab, var(--amber) 8%, transparent)',
    color: 'amber',
  },
  '&[data-disabled]': { cursor: 'default', color: 'muted' },
});
export const menuWhy = css({ color: 'muted', fontSize: '0.68rem' });
export const menuRule = css({ border: 0, borderTop: '1px solid token(colors.grid)', margin: '0.3rem 0' });
export const menuHint = css({
  color: 'muted',
  fontSize: '0.68rem',
  lineHeight: '1.45',
  padding: '0.3rem 0.8rem 0.45rem',
  margin: 0,
});
