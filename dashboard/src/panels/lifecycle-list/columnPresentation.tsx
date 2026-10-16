// The column's presentation and controls; stage and activity come from task entries.
import { ToggleButton, ToggleButtonGroup } from 'react-aria-components';
import { css, cva } from '../../../styled-system/css';
export type Pivot = 'repo' | 'phase';
import { columnTabButton } from '../../grammar/viewTabLook';
import { AttentionQueue } from '../AttentionQueue';

export const sizing = css({ flex: '1 1 0', minWidth: '0', overflowX: 'hidden' });
// The head is one line at the column's own width. A rail that was dragged narrower gets the
// switch and the bell on a line of their own under the title, and the bell under the switch at
// the narrowest: nothing of the head leaves the column, and the bell stays in reach.
const headRow = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  justifyContent: 'space-between',
  columnGap: '0.4375rem',
  rowGap: '0.2625rem',
  minWidth: '0',
});
const headTitle = css({ margin: '0', whiteSpace: 'nowrap' });
const headTools = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'stretch',
  columnGap: '0.35rem',
  rowGap: '0.2625rem',
  minWidth: '0',
});
const pivotBar = css({ display: 'flex', gap: '0.2188rem' });
export const listBox = css({
  listStyle: 'none',
  display: 'grid',
  gridTemplateColumns: 'minmax(0, 1fr)',
  gap: '0.4813rem',
  width: '100%',
  minWidth: '0',
  margin: '0',
  padding: '0',
  outline: 'none',
});
export const section = css({
  display: 'grid',
  gridTemplateColumns: 'minmax(0, 1fr)',
  width: '100%',
  minWidth: '0',
});
// A group of the tree, a repository or a phase: a small muted label with its count.
export const groupHeader = css({
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.4375rem',
  minWidth: '0',
  marginBottom: '0.0875rem',
  color: 'muted',
  fontSize: '0.72rem',
  lineHeight: '1.4',
  letterSpacing: '0.09em',
  textTransform: 'uppercase',
  whiteSpace: 'nowrap',
});
export const groupCount = css({ marginLeft: 'auto', letterSpacing: '0.02em' });
// One line, no frame: the row of the reviewer's tree (MIK-R86). It reaches into the panel's
// padding on both sides, so a wash and a focus line have room around the text. The scroll margin
// keeps a row that is brought into view clear of the panel's sticky head.
export const rowItem = css({
  minWidth: '0',
  marginInline: '-0.2625rem',
  fontSize: '0.8rem',
  lineHeight: '1.5',
  cursor: 'pointer',
  outline: 'none',
  scrollMarginTop: '3rem',
  scrollMarginBottom: '0.35rem',
  '&:hover:not([data-selected], [data-chosen])': {
    background: 'color-mix(in oklab, token(colors.amber) 8%, transparent)',
  },
  '&:is([data-selected], [data-chosen])': {
    background: 'color-mix(in oklab, token(colors.amber) 16%, transparent)',
    "& [data-part='title']": { color: 'amber' },
  },
  _focusVisible: { outline: '1px solid token(colors.amber)', outlineOffset: '-1px' },
});
// The line of a row. It fills the row, so its hover text is the row's: a React Aria list item
// passes no `title` on.
export const rowLine = css({
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.3063rem',
  minWidth: '0',
  paddingBlock: '0.1225rem',
  paddingRight: '0.2625rem',
  whiteSpace: 'nowrap',
});
// One cell for both marks. Dot brings a margin for running text; here the row's gap spaces it.
export const markCell = css({
  flex: 'none',
  width: '1ch',
  textAlign: 'center',
  userSelect: 'none',
  '& > span': { marginRight: '0' },
});
// What the document declares: the task page's status dot, in the task page's four colours
// (detail-panel/pageStyles `statusWord`), at the column's size.
export const statusDot = cva({
  base: {
    display: 'inline-block',
    width: '0.4375rem',
    height: '0.4375rem',
    borderRadius: '50%',
    background: 'currentColor',
    verticalAlign: '0.02em',
    color: 'muted',
  },
  variants: {
    tone: {
      work: { color: 'amber' },
      wait: {},
      done: { color: 'mint' },
      gone: { color: 'dormant' },
    },
  },
});
export const rowIdCell = cva({
  base: { flex: 'none', color: 'muted', fontSize: '0.72rem' },
  variants: { long: { true: { fontSize: '0.72rem' } } },
});
export const rowTitle = cva({
  base: { flex: '1 1 0', minWidth: '8ch', overflow: 'hidden', textOverflow: 'ellipsis' },
  variants: {
    weight: { parent: { fontWeight: '600' }, fold: { color: 'muted', fontSize: '0.72rem' } },
  },
});
export const rowRepo = css({
  flex: '0 1 auto',
  minWidth: '0',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  fontSize: '0.72rem',
  color: 'muted',
});
// The word: what asks for the developer is coloured, a state that asks for nothing is muted.
export const rowWordCell = cva({
  base: {
    flex: '0 1 auto',
    minWidth: '0',
    maxWidth: '15ch',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    fontSize: '0.72rem',
  },
  variants: {
    tone: {
      ask: { color: 'amber' },
      fault: { color: 'alarm' },
      run: { color: 'cyan' },
      rest: { color: 'muted' },
    },
  },
});
export const rowFigure = css({
  flex: 'none',
  minWidth: '3ch',
  textAlign: 'right',
  fontSize: '0.72rem',
});
// The indent of the tree: 0.7438rem a level, by padding inside the row.
export function indent(depth: number): { paddingLeft: string } {
  return { paddingLeft: `${(0.2625 + depth * 0.74375).toFixed(4)}rem` };
}

export function TaskPivotBar({
  count,
  pivot,
  onPivot,
}: {
  count: number;
  pivot: Pivot;
  onPivot: (pivot: Pivot) => void;
}) {
  return (
    <div className={headRow}>
      <h2 className={headTitle}>Tasks · {count}</h2>
      <div className={headTools}>
        <ToggleButtonGroup
          className={pivotBar}
          selectionMode="single"
          disallowEmptySelection
          selectedKeys={[pivot]}
          onSelectionChange={(keys) => {
            const next = [...keys][0];
            if (next === 'repo' || next === 'phase') onPivot(next);
          }}
          aria-label="Group tasks by"
        >
          <ToggleButton id="repo" className={columnTabButton}>
            BY REPO
          </ToggleButton>
          <ToggleButton id="phase" className={columnTabButton}>
            BY PHASE
          </ToggleButton>
        </ToggleButtonGroup>
        <AttentionQueue />
      </div>
    </div>
  );
}
