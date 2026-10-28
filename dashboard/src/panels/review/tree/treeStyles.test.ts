// MIK-R89 v2 rule 1: the tree is full height beside the reading column; only the stacked layout
// (40rem and narrower, where the tree sits above the column) caps its scroller.
import { expect, it } from 'vitest';

import {
  control,
  del,
  head,
  heading,
  headLink,
  input,
  ins,
  menuGroup,
  menuHint,
  menuItem,
  menuRule,
  menuWhy,
  pinned,
  popover,
  reasonCell,
  row,
  scroller,
  segment,
  sortKey,
  sortWord,
  tools,
} from './treeStyles';

const names = (className: string) => className.split(' ');

// MIK-R211 v1 rule 5: the sidebar renders as before L183. A row is three cells — the caret, the name
// that takes what is left, and the trailing reason — so the reason (or "changed" chip) is whole at the
// right. A location row's grade chip joins that trailing cell instead of taking a column of its own,
// which would give every row's name its max-content width and collapse the reason to nothing.
it('keeps a row three cells wide, its name the one that gives way', () => {
  expect(names(row).filter((name) => name.startsWith('grid-tc_'))).toEqual([
    'grid-tc_0.9rem_minmax(0,_1fr)_auto',
  ]);
  expect(names(row)).toEqual(expect.arrayContaining(['d_grid', 'white-space_nowrap']));
  // The trailing cell holds the chip and the reason on the row's one line, the chip keeping its size.
  expect(names(reasonCell)).toEqual(
    expect.arrayContaining(['d_flex', 'ai_baseline', 'gap_0.35rem', 'min-w_0']),
  );
});

it("caps the tree's scroller at 45dvh only on the stacked layout", () => {
  const capped = scroller.split(' ').filter((name) => name.includes('45dvh'));
  expect(capped).toHaveLength(1);
  expect(capped[0]).toContain('max-width:_40rem');
});

// MIK-R89 v2 rules 3 and 11: the filter, Changed | All and Sort stay on one line inside the tree at
// 340 px (1920) and 292 px (1440). jsdom has no layout, so this pins how the line gives way: the
// filter takes what is left and keeps a readable 5rem (the mockup's filter is read at both widths;
// D401), the view switch keeps its size, and Sort shortens its key with an ellipsis, keeping its ▾.
// In the 1440 tree (320 px or less) the padding and gaps tighten and Sort drops its word "Sort:" (its
// name keeps it). The filter draws no clear icon (the mockup's has none; Escape clears it). Nothing on
// the line is wider than the line, so focus never scrolls the tree sideways.
it('lets the control line give way inside its own line before anything overflows it', () => {
  const tight = (name: string) => `[@container_tree_(max-width:_320px)]:${name}`;
  expect(names(tools)).toEqual(
    expect.arrayContaining([
      'd_flex',
      'flex-wrap_nowrap',
      'min-w_0',
      tight('gap_0.3rem'),
      tight('px_0.5rem'),
    ]),
  );
  expect(names(input)).toEqual(
    expect.arrayContaining([
      'flex_1_1_0',
      'min-w_5rem',
      tight('px_0.3rem'),
      '[&::-webkit-search-cancel-button]:ap_none',
      '[&::-webkit-search-cancel-button]:d_none',
    ]),
  );
  expect(names(sortWord)).toContain(tight('d_none'));
  expect(names(segment)).toContain('flex-sh_0');
  expect(names(control)).toEqual(
    expect.arrayContaining([
      'd_inline-flex',
      'gap_0.5ch',
      'min-w_0',
      'ov_hidden',
      'white-space_nowrap',
      tight('px_0.3rem'),
    ]),
  );
  expect(names(sortKey)).toEqual(expect.arrayContaining(['min-w_0', 'ov_hidden', 'tov_ellipsis']));
});

// The heading line at 292 px (1440) holds "Change set", the counts and the one "All changes" link
// (MIK-R89 v2 rule 7) without cutting the counts: in the 1440 tree the line's padding, its gaps and
// the heading's letter spacing tighten, and the link adds no padding of its own (its count, the same
// total the counts show, is spoken, not drawn: ReviewTree.tsx).
it('keeps the heading line whole in the 1440 tree', () => {
  const tight = (name: string) => `[@container_tree_(max-width:_320px)]:${name}`;
  expect(names(head)).toEqual(expect.arrayContaining([tight('pl_0.5rem'), tight('pr_0.4rem'), tight('gap_0.3rem')]));
  expect(names(heading)).toContain(tight('ls_0.1em'));
  expect(names(headLink)).toContain('p_0');
});

// MIK-R89 v2 rule 1 as amended by 719ade09 (mockup styles.css:95-96): a row's line counts draw "+a"
// in the theme's mint and "−r" in its alarm red.
it("draws the line counts in the theme's mint and alarm red", () => {
  expect(names(ins)).toContain('c_mint');
  expect(names(del)).toContain('c_alarm');
});

// MIK-R86 v2 rule 4 and MIK-R89 v2 rule 7: the Worklist row and "Code without invariant" are pinned
// at the bottom of the tree (mockup styles.css:144-145). The one scroller lays its rows out as a
// column, and the pinned block takes the space left above it, so a short tree draws it at the panel's
// bottom; a long one keeps it stuck to the scroller's bottom.
it("pins the Worklist and lane block to the tree's bottom", () => {
  expect(names(scroller)).toEqual(expect.arrayContaining(['d_flex', 'flex-d_column']));
  expect(names(pinned)).toEqual(expect.arrayContaining(['mt_auto', 'pos_sticky', 'bottom_0']));
});

// MIK-R89 v2 rule 1 as amended by d9a1ec8f item 4 (mockup styles.css:108-117, app.js:242): the Sort
// popup is the mockup's 350 px wide, with its row height and look, its rule between the groups and its
// explanatory section.
it("draws the Sort popup at the mockup's width, rows and explanatory section", () => {
  expect(names(popover)).toEqual(
    expect.arrayContaining(['w_350px', 'p_0.35rem_0', 'bx-sh_0_6px_20px_rgba(0,_0,_0,_0.5)']),
  );
  expect(names(menuGroup)).toEqual(expect.arrayContaining(['p_0.45rem_0.8rem_0.2rem', 'fs_0.64rem']));
  expect(names(menuItem)).toEqual(
    expect.arrayContaining(['grid-tc_1rem_minmax(0,_1fr)_auto', 'gap_0.4rem', 'p_0.3rem_0.8rem', 'fs_0.75rem', 'lh_1.5']),
  );
  expect(names(menuWhy)).toEqual(expect.arrayContaining(['fs_0.68rem']));
  expect(names(menuRule)).toEqual(expect.arrayContaining(['m_0.3rem_0']));
  expect(names(menuHint)).toEqual(expect.arrayContaining(['p_0.3rem_0.8rem_0.45rem', 'fs_0.68rem', 'lh_1.45']));
});
