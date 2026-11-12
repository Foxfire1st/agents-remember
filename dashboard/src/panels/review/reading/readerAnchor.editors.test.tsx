// MIK-R89 v2 rule 9, "The page never moves under the reader" (amendment 10fb84cb), rulings fb5d04f6
// and 8450399b (1), for a reader's line inside a CodeMirror editor: the lines of a pane at the view
// top hold, within 1 px, in every painted frame after a scroll and through the editor's measurements,
// re-layouts and rebuilds, on both sides of a side-by-side diff (readerAnchor.ts, THE LINE IN AN
// EDITOR, EDITORS AT A FIXED POINT). Real editors stand in an anchored column laid out by
// readerLine.test-utils.tsx; the cases add their own blocks around them.
import { EditorView } from '@codemirror/view';
import { act, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  anchoredColumn,
  COLUMN_TOP,
  docOf,
  gestureOver,
  growSpacer,
  installAnchorClock,
  lineAtTop,
  lineTopIn,
  mockLayout,
  nextFrame,
  painted,
  paintedAfter,
  scrollTo,
  settle,
  spacerAtLine3,
  testBlock,
} from './readerLine.test-utils';

beforeEach(() => {
  installAnchorClock();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the reader's line in an editor (rule 9)", () => {
  // Rule 9, rulings 8450399b and fb5d04f6 (WP8 on A4-F3, `center` at the tall heights: a scripted scroll
  // left the view top inside a card's diff pane that had never been in view; on its first measurement the
  // pane went 238 → 166.6 px, and the block kept below the pane moved the pane's lines at the view top by
  // 65–73 px). When the visible top falls inside an editor, the line is the editor's block there, kept by
  // its document position: it holds while the editor's lines change height below it and above it, while
  // the pane is built again with a new editor, and while content above the pane changes. Here an editor of
  // 40 lines of 20 px in a card's excerpt, the view top 5 px into its line 11, a block below, a tail.
  it("keeps an editor's line at the view top while its pane changes height below and above it, and is built again", async () => {
    const heights = new Map<string, number>();
    const layout = mockLayout([
      ['[data-testid="t-above"]', 1000],
      ['[data-testid="t-below"]', 30],
      ['[data-testid="t-tail"]', 2000],
      ['.cm-line', (element) => heights.get(element.textContent ?? '') ?? 20],
    ]);
    const add = anchoredColumn(layout);
    const [above, excerpt, pane, below, tail] = ['t-above', 'review-card-excerpt', 'diff-pane', 't-below', 't-tail'].map(testBlock);
    excerpt.append(pane);
    add(above, excerpt, below, tail);
    const doc = docOf(40);
    let view = new EditorView({ doc, parent: pane });
    await settle();
    const lineTop = () => [...view.contentDOM.children].find((line) => line.textContent === 'line 11')!.getBoundingClientRect().top;
    // A layout change the column sees (the editor's own measuring changes nothing in its DOM here).
    const relayout = async (change: () => void) => {
      await act(async () => {
        change();
        tail.classList.toggle('t-relayout');
      });
      await settle();
    };
    scrollTo(1000 + 10 * 20 + 5);
    expect(lineTop()).toBe(COLUMN_TOP - 5);
    await gestureOver();
    const seen: Record<string, number> = {};

    // Lines below the view top grow, as an editor's lines do on its first measurement.
    await relayout(() => ['line 20', 'line 21', 'line 22'].forEach((text) => heights.set(text, 60)));
    seen.belowGrew = lineTop() - (COLUMN_TOP - 5);
    // Lines above it shrink while one below grows as much: the pane keeps its height.
    await relayout(() => {
      ['line 2', 'line 3', 'line 4'].forEach((text) => heights.set(text, 10));
      heights.set('line 30', 50);
    });
    seen.aboveShrank = lineTop() - (COLUMN_TOP - 5);
    // The pane is built again with a new editor, as when its marks land.
    await relayout(() => {
      view.destroy();
      heights.set('line 5', 44);
      view = new EditorView({ doc, parent: pane });
    });
    seen.rebuilt = lineTop() - (COLUMN_TOP - 5);
    // Content above the pane grows.
    await relayout(() => {
      above.style.height = '1300px';
    });
    seen.aboveGrew = lineTop() - (COLUMN_TOP - 5);
    expect(Math.max(...Object.values(seen).map(Math.abs)), JSON.stringify(seen)).toBeLessThanOrEqual(1);
    view.destroy();
  });

  // Rule 9, ruling 8450399b (1): CodeMirror measures an editor drawn outside the window only once its
  // IntersectionObserver reports, after the browser has painted, so the first measurement of a pane a
  // scroll brought into view came a frame after that scroll (WP8 on A4-F3: the gutter line at the view
  // top moved 73.4 px). The column's scroll asks each editor it brings into view that has not been
  // measured in view for its measurement at once, to run in that frame's animation callbacks, before
  // the paint; an editor measured in view, and one still outside the view, are not asked.
  it('asks an editor a scroll brings into view, never measured in view, for its measurement at that scroll', async () => {
    const layout = mockLayout([
      ['[data-testid="t-above"]', 1000],
      ['[data-testid="t-gap"]', 2000],
      ['[data-testid="t-tail"]', 2000],
      ['.cm-line', 20],
    ]);
    const add = anchoredColumn(layout);
    const panes = [0, 1, 2].map(() => testBlock('diff-pane'));
    const views = panes.map((pane) => new EditorView({ doc: docOf(40), parent: pane }));
    add(testBlock('t-above'), panes[0], panes[1], testBlock('t-gap'), panes[2], testBlock('t-tail'));
    await settle();
    // In view at the scroll: the first two (1000–1800 and 1800–2600 under a view of 1500–2100), the
    // first never measured in view; the third, at 4600, outside the view.
    [false, true, false].forEach((inView, index) => vi.spyOn(views[index], 'inView', 'get').mockReturnValue(inView));
    const asked = views.map((view) => vi.spyOn(view, 'requestMeasure'));
    scrollTo(1500);
    expect(asked.map((spy) => spy.mock.calls.length > 0)).toEqual([true, false, false]);
    views.forEach((view) => view.destroy());
  });

  // Rule 9, ruling 8450399b (1); WP8 on A4-F4 (p1 and p3 at both widths, two scrollbar drags): a
  // scroll left the view top in the `cm-gap` of an editor drawn while outside the window (13–18 lines
  // drawn, never measured in view). The line was not taken in the editor: the block below the excerpt
  // was kept, and as the editor was measured and laid out again its lines at the top moved 40–408 px.
  // Here an editor of 200 lines is built outside the window, so it draws its first lines only, and a
  // scroll puts the view top 1300 px into it; then its lines below the top grow, as an editor's do
  // when measured or given a gutter.
  it("takes the line of an editor whose gap held the view top, and keeps it while its lines below grow", async () => {
    const heights = new Map<string, number>();
    const layout = mockLayout([
      ['[data-testid="t-above"]', 3000],
      ['[data-testid="t-below"]', 30],
      ['[data-testid="t-tail"]', 6000],
      ['.cm-line', (element) => heights.get(element.textContent ?? '') ?? 20],
    ]);
    const add = anchoredColumn(layout);
    const [above, excerpt, pane, below, tail] = ['t-above', 'review-card-excerpt', 'diff-pane', 't-below', 't-tail'].map(testBlock);
    excerpt.append(pane);
    add(above, excerpt, below, tail);
    const view = new EditorView({ doc: docOf(200), parent: pane });
    await nextFrame();
    // Built outside the window: never measured in view, its later lines a gap.
    expect(view.inView).toBe(false);
    expect(pane.querySelector('.cm-gap')).not.toBeNull();
    scrollTo(3000 + 1300);
    await gestureOver();
    await painted(2, () => null);
    const { text, top } = lineAtTop(view);
    expect(text).not.toBe('');
    const index = Number(text.split(' ')[1]);
    // Lines below the top grow by 30 px each.
    await act(async () => {
      for (let line = index + 1; line <= index + 10; line += 1) heights.set(`line ${line}`, 50);
      tail.classList.toggle('t-relayout');
    });
    await settle();
    const shown = await painted(3, () => lineTopIn(view, text) - top);
    expect(Math.max(...shown.map(Math.abs)), JSON.stringify({ text, shown })).toBeLessThanOrEqual(1);
    view.destroy();
  });

  // Rule 9, ruling 8450399b (1); WP8 on A4-F4 (1920 p1, split): a side-by-side diff's editors align
  // each other, so a second measurement before the second paint re-laid side a by +46.2 px and side b
  // by −13.3 px, and the anchor, keeping side a, followed a paint later. Here two editors of 40 lines
  // stand side by side, built outside the window. When side b is first measured in view, its spacer
  // above the top grows 40 px and it asks side a for a measurement that grows a's line 10 by 40 px
  // (the merge view's alignment, which CodeMirror runs a frame later). Every painted frame after the
  // scroll shows both sides' lines at the top where the first one did.
  it('holds both sides of a side-by-side diff at the top in every painted frame after a scroll', async () => {
    const heights = new Map<string, number>();
    const layout = mockLayout([
      ['[data-testid="t-above"]', 3000],
      ['[data-testid="t-below"]', 30],
      ['[data-testid="t-tail"]', 4000],
      ['.t-spacer', 0],
      ['.cm-line', (element) => heights.get(element.textContent ?? '') ?? 20],
    ]);
    const add = anchoredColumn(layout);
    const [above, excerpt, pane, below, tail] = ['t-above', 'review-card-excerpt', 'diff-pane', 't-below', 't-tail'].map(testBlock);
    const sides = document.createElement('div');
    sides.setAttribute('data-t-sides', '');
    const lanes = [document.createElement('div'), document.createElement('div')];
    sides.append(...lanes);
    pane.append(sides);
    excerpt.append(pane);
    add(above, excerpt, below, tail);
    let aligned = false;
    const views: EditorView[] = [];
    const align = EditorView.updateListener.of((update) => {
      if (aligned || !update.view.inView) return;
      aligned = true;
      update.view.dispatch({ effects: growSpacer.of(40) });
      views[0].requestMeasure({
        read: () => null,
        write: () => {
          heights.set('a 10', 60);
          tail.classList.toggle('t-relayout');
        },
      });
    });
    views.push(
      new EditorView({ doc: docOf(40, 'a'), parent: lanes[0], extensions: [EditorView.editorAttributes.of({ class: 'cm-merge-a' })] }),
      new EditorView({
        doc: docOf(40, 'b'),
        parent: lanes[1],
        extensions: [EditorView.editorAttributes.of({ class: 'cm-merge-b' }), spacerAtLine3, align],
      }),
    );
    await nextFrame();
    expect(views.map((view) => view.inView)).toEqual([false, false]);
    scrollTo(3000 + 10 * 20 + 5);
    const shown = await painted(5, () => ({ a: lineTopIn(views[0], 'a 11'), b: lineTopIn(views[1], 'b 11') }));
    expect(aligned).toBe(true);
    const off = shown.map(({ a, b }) => Math.max(Math.abs(a - shown[0].a), Math.abs(b - shown[0].b)));
    expect(Math.max(...off), JSON.stringify(shown)).toBeLessThanOrEqual(1);
    views.forEach((view) => view.destroy());
  });

  // Rule 9, ruling 8450399b (1); WP8 on A4-F4 (1440 p1, unified): after a measurement the pane shrank
  // 36.8 px inside its held box (ExpressionCards.tsx, HeldPane: the box keeps its last drawn height
  // until its ResizeObserver's report is drawn), so the block below did not move; the anchor, keeping
  // that block, corrected only once the box let go, and the line was off for one painted frame, again
  // at 2.0 s and 3.3 s. Here an editor's pane, its view top in the editor's gap at the scroll, stands
  // in a box holding its drawn height. In one frame lines above the top shrink by 36 px; in the next
  // the box lets go: every painted frame shows the line where it was.
  it("keeps an editor's line in every painted frame while its held box hides a re-layout above it", async () => {
    const heights = new Map<string, number>();
    const layout = mockLayout([
      ['[data-testid="t-above"]', 3000],
      ['[data-testid="t-below"]', 30],
      ['[data-testid="t-tail"]', 6000],
      ['.cm-line', (element) => heights.get(element.textContent ?? '') ?? 20],
    ]);
    const add = anchoredColumn(layout);
    const [above, excerpt, held, pane, below, tail] = ['t-above', 'review-card-excerpt', 't-held', 'diff-pane', 't-below', 't-tail'].map(testBlock);
    held.append(pane);
    excerpt.append(held);
    add(above, excerpt, below, tail);
    const view = new EditorView({ doc: docOf(200), parent: pane });
    await nextFrame();
    expect(view.inView).toBe(false);
    scrollTo(3000 + 1300);
    await gestureOver();
    await painted(2, () => null);
    await act(async () => {
      held.style.minHeight = `${pane.getBoundingClientRect().height}px`;
    });
    await settle();
    const { text, top } = lineAtTop(view);
    expect(text).not.toBe('');
    const index = Number(text.split(' ')[1]);
    const off = () => lineTopIn(view, text) - top;
    const seen: Record<string, number> = { held: off() };
    seen.relaidUnderTheHold = await paintedAfter(() => {
      for (let line = index - 6; line < index; line += 1) heights.set(`line ${line}`, 14);
      tail.classList.toggle('t-relayout');
    }, off);
    seen.letGo = await paintedAfter(() => {
      held.style.minHeight = `${pane.getBoundingClientRect().height}px`;
    }, off);
    expect(Math.max(...Object.values(seen).map(Math.abs)), JSON.stringify({ text, seen })).toBeLessThanOrEqual(1);
    view.destroy();
  });
});
