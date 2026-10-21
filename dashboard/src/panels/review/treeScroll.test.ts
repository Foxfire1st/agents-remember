// Revealing a tree row changes only the tree scroller's scrollTop (MIK-R89 v2 rule 9; UX F1): no
// style is written, the page does not move, and a row above the pinned bottom rows lands above them.
import { expect, it, vi } from 'vitest';
import { revealTreeRow } from './treeScroll';

function scroller(top: number, bottom: number): HTMLElement {
  const panel = document.createElement('div');
  panel.dataset.reviewTreePanel = '';
  vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue({ top, bottom } as DOMRect);
  return panel;
}

it('scrolls only the tree scroller enough to reveal its selected row, and writes no height', () => {
  const surface = document.createElement('section');
  surface.dataset.kbzone = 'review';
  surface.scrollTop = 77;
  const panel = scroller(20, 220);
  const row = document.createElement('button');
  surface.append(panel);
  panel.append(row);
  panel.scrollTop = 100;
  vi.spyOn(row, 'getBoundingClientRect').mockReturnValue({ top: 240, bottom: 260 } as DOMRect);
  const scroll = vi.fn();
  row.scrollIntoView = scroll;
  revealTreeRow(row);
  expect(panel.scrollTop).toBe(140);
  expect(panel.getAttribute('style')).toBeNull();
  expect(surface.scrollTop).toBe(77);
  expect(scroll).not.toHaveBeenCalled();
});

it('reveals an upper row at the scroller top and leaves a visible row where it is', () => {
  const panel = scroller(100, 400);
  const row = document.createElement('button');
  panel.append(row);
  panel.scrollTop = 100;
  vi.spyOn(row, 'getBoundingClientRect').mockImplementation(
    () => ({ top: 80 + (100 - panel.scrollTop), bottom: 100 + (100 - panel.scrollTop) }) as DOMRect,
  );
  revealTreeRow(row);
  expect(panel.scrollTop).toBe(80);
  revealTreeRow(row);
  expect(panel.scrollTop).toBe(80);
});

it('reveals a row above the pinned Worklist and lane rows rather than behind them', () => {
  const panel = scroller(0, 500);
  const row = document.createElement('button');
  const pinned = document.createElement('div');
  pinned.dataset.reviewTreePinned = '';
  panel.append(row, pinned);
  panel.scrollTop = 0;
  vi.spyOn(pinned, 'getBoundingClientRect').mockReturnValue({ top: 440, bottom: 500 } as DOMRect);
  vi.spyOn(row, 'getBoundingClientRect').mockImplementation(
    () => ({ top: 450 - panel.scrollTop, bottom: 470 - panel.scrollTop }) as DOMRect,
  );
  revealTreeRow(row);
  expect(row.getBoundingClientRect().bottom).toBeLessThanOrEqual(440);
  // A lane file row after the pinned block is revealed against the scroller's own bottom.
  const lane = document.createElement('button');
  panel.append(lane);
  panel.scrollTop = 0;
  vi.spyOn(lane, 'getBoundingClientRect').mockReturnValue({ top: 480, bottom: 498 } as DOMRect);
  revealTreeRow(lane);
  expect(panel.scrollTop).toBe(0);
});
