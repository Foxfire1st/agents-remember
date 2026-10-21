// The review page's layout (MIK-R89 v2 rules 1, 8, 11; W22, W26; mockup `open`, `tree-hidden`):
// a root that does not scroll; the top line, spanning the tree and the reading column; under it the
// tree column (its width per width class, resizable, hidden and shown by the shared sidebar toggle)
// and the reading column, which is the page scroller. The review grid's gutter is 14 px above
// 1600 px and 12 px at or below. A hidden tree keeps a 0 px track and stays mounted, so its scroll
// survives. At 40rem and narrower the tree stacks above the reading column and the page around them
// scrolls; nothing scrolls sideways.
import { useEffect } from 'react';

import { css } from '../../../../styled-system/css';
import { RailResizeHandle, useMediaQuery } from '../../../cockpit/layout';
import { usePersistedNumber } from '../../file-viewer/usePersistedFlag';

const REVIEW_TREE_WIDTH_KEYS = {
  wide: 'cockpit.review.tree-w.wide',
  narrow: 'cockpit.review.tree-w.narrow',
} as const;
const REVIEW_TREE_WIDTH_DEFAULTS = { wide: 340, narrow: 292 } as const;
const NARROW_CLASS = '(max-width: 1600px)';

export const reviewRoot = css({
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  minHeight: 0,
  minWidth: 0,
  overflow: 'hidden',
  background: 'bg',
  color: 'ink',
  fontFamily: 'mono',
  fontSize: '0.83rem',
  lineHeight: '1.6',
  overflowWrap: 'anywhere',
  // Above 40rem the cockpit's shell gives the page its inset, as the mockup's `.shell` does (D401);
  // stacked, the page takes over the screen (layout.tsx `takeoverBody`) and keeps an inset of its own.
  '@media (max-width: 40rem)': { height: 'auto', overflow: 'visible', paddingBlock: '0.8rem 0', paddingInline: '0.6rem' },
  '& summary': { cursor: 'pointer', color: 'muted', fontSize: '0.75rem', paddingBlock: '0.3rem' },
  '& details[open] > summary': { color: 'cyan' },
  // The review's shared control look. The tree draws its own controls (treeStyles.ts, fitted to its
  // 292 and 340 px widths; rules 3 and 11), so its column is left out, as its rows are. A segmented
  // switch draws its own buttons and their selected state (ReadingColumn.tsx `segment`, ruling d9a1ec8f
  // item 2): this rule outranks a component's `button[data-selected]`, so a `data-segment` is left out.
  // A subject's title is a heading, not a control (`data-subject-title`; d9a1ec8f, the subject heading).
  // A control drawn as the mockup's quiet text or bordered link marks itself `data-own-look` (D401:
  // the lane's links and hunk rows, a group's heading and its "… N more").
  '& button:not([data-tree-node]):not([data-path]):not([data-add-to-tray]):not([data-sidebar-side]):not([data-subject-title]):not([data-own-look]):not(#review-tree *):not([data-segment] *), & select:not(#review-tree *), & input:not(#review-tree *)': {
    background: 'bgPanel',
    color: 'ink',
    border: '1px solid var(--grid)',
    borderRadius: '2px',
    font: 'inherit',
    fontSize: '0.75rem',
    padding: '0.3rem 0.55rem',
    cursor: 'pointer',
    maxWidth: '100%',
  },
  '& button:focus-visible, & select:focus-visible, & input:focus-visible, & summary:focus-visible': {
    outline: '2px solid var(--amber)',
    outlineOffset: '2px',
  },
  '& button:hover': { color: 'amber' },
});

export const reviewBody = css({
  display: 'flex',
  flexDirection: 'column',
  flex: '1',
  minHeight: 0,
  minWidth: 0,
  '@media (max-width: 40rem)': { flex: 'none' },
});

const grid = css({
  flex: '1',
  minHeight: 0,
  minWidth: 0,
  display: 'grid',
  gridTemplateColumns: 'var(--review-tree-w) minmax(0, 1fr)',
  columnGap: '14px',
  '@media (max-width: 1600px)': { columnGap: '12px' },
  '@media (max-width: 40rem)': {
    gridTemplateColumns: 'minmax(0, 1fr)',
    rowGap: '0.8rem',
    flex: 'none',
  },
});

const treeColumn = css({
  position: 'relative',
  minHeight: 0,
  minWidth: 0,
  display: 'flex',
  flexDirection: 'column',
  // Clipped, but no scroller (`hidden` would make one): a focus or an opened menu in the tree never
  // scrolls it sideways, so its rows keep their place (rules 3 and 9).
  overflow: 'clip',
  '&[data-open="false"]': { visibility: 'hidden', borderWidth: 0 },
  '@media (max-width: 40rem)': {
    maxHeight: '45dvh',
    '&[data-open="false"]': { display: 'none' },
  },
});

function useTreeWidth(): [number, (next: number) => void] {
  const narrow = useMediaQuery(NARROW_CLASS);
  const wide = usePersistedNumber(REVIEW_TREE_WIDTH_KEYS.wide, REVIEW_TREE_WIDTH_DEFAULTS.wide);
  const small = usePersistedNumber(REVIEW_TREE_WIDTH_KEYS.narrow, REVIEW_TREE_WIDTH_DEFAULTS.narrow);
  return narrow ? small : wide;
}

export function ReviewGrid({
  treeOpen,
  tree,
  reading,
}: {
  treeOpen: boolean;
  tree: React.ReactNode;
  reading: React.ReactNode;
}) {
  const [width, setWidth] = useTreeWidth();
  return (
    <div
      className={grid}
      data-testid="review-workspace"
      style={{ '--review-tree-w': treeOpen ? `${width}px` : '0px' } as React.CSSProperties}
    >
      <div className={treeColumn} id="review-tree" data-testid="review-tree-column" data-open={String(treeOpen)} aria-hidden={!treeOpen}>
        {tree}
        {treeOpen ? (
          <RailResizeHandle side="left" width={width} onResize={setWidth} label="Resize the tree" testId="review-tree-resize" />
        ) : null}
      </div>
      {reading}
    </div>
  );
}

function editable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])'));
}

// `[` toggles the tree and `]` the chat (rule 8), at document level while the review is shown (not
// while the change-set page lies over it), never in a text field and never with a modifier.
function bracketKey(event: KeyboardEvent): '[' | ']' | null {
  if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.defaultPrevented) return null;
  if (event.key !== '[' && event.key !== ']') return null;
  return editable(event.target) ? null : event.key;
}

export function useBracketKeys(toggleTree: () => void, toggleChat: (() => void) | undefined, enabled = true): void {
  useEffect(() => {
    if (!enabled) return undefined;
    const onKey = (event: KeyboardEvent) => {
      const key = bracketKey(event);
      if (key === null) return;
      const toggle = key === '[' ? toggleTree : toggleChat;
      if (!toggle) return;
      event.preventDefault();
      toggle();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [toggleTree, toggleChat, enabled]);
}
