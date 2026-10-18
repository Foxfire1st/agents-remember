import { expect, it, vi } from 'vitest';
import { revealTreeRow } from './treeScroll';

it('fits an oversized panel to its review viewport so even its last row can be revealed', () => {
  const surface = document.createElement('section');
  surface.dataset.kbzone = 'review';
  surface.scrollTop = 77;
  const panel = document.createElement('aside');
  panel.dataset.reviewTreePanel = '';
  const row = document.createElement('button');
  surface.append(panel);
  panel.append(row);
  const contentHeight = 1000;
  const height = () => Math.min(400, Number.parseFloat(panel.style.maxHeight) || 400);
  let position = 0;
  Object.defineProperty(panel, 'scrollTop', {
    get: () => position,
    set: (value: number) => { position = Math.max(0, Math.min(value, contentHeight - height())); },
  });
  vi.spyOn(surface, 'getBoundingClientRect').mockReturnValue({ top: 0, bottom: 300 } as DOMRect);
  vi.spyOn(panel, 'getBoundingClientRect').mockImplementation(() => ({ top: 100, bottom: 100 + height() } as DOMRect));
  vi.spyOn(row, 'getBoundingClientRect').mockImplementation(() => ({ top: 100 + contentHeight - 20 - position, bottom: 100 + contentHeight - position } as DOMRect));
  revealTreeRow(row);
  expect(row.getBoundingClientRect().bottom).toBeLessThanOrEqual(surface.getBoundingClientRect().bottom);
  expect(row.getBoundingClientRect().top).toBeGreaterThanOrEqual(panel.getBoundingClientRect().top);
  expect(surface.scrollTop).toBe(77);
});

it('scrolls only the tree panel enough to reveal its selected row', () => {
  const panel = document.createElement('aside');
  panel.dataset.reviewTreePanel = '';
  const row = document.createElement('button');
  panel.append(row);
  panel.scrollTop = 100;
  vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue({ top: 20, bottom: 220 } as DOMRect);
  vi.spyOn(row, 'getBoundingClientRect').mockReturnValue({ top: 240, bottom: 260 } as DOMRect);
  const scroll = vi.fn();
  row.scrollIntoView = scroll;
  revealTreeRow(row);
  expect(panel.scrollTop).toBe(140);
  expect(scroll).not.toHaveBeenCalled();
});

it('reveals an upper row below the sticky traversal controls rather than behind them', () => {
  const surface = document.createElement('section');
  surface.dataset.kbzone = 'review';
  surface.scrollTop = 280;
  const panel = document.createElement('aside');
  panel.dataset.reviewTreePanel = '';
  const bar = document.createElement('div');
  bar.dataset.testid = 'review-triage-bar';
  const row = document.createElement('button');
  surface.append(panel);
  panel.append(bar, row);
  panel.scrollTop = 100;
  vi.spyOn(surface, 'getBoundingClientRect').mockReturnValue({ top: 0, bottom: 600 } as DOMRect);
  vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue({ top: 100, bottom: 400 } as DOMRect);
  vi.spyOn(bar, 'getBoundingClientRect').mockReturnValue({ top: 100, bottom: 170 } as DOMRect);
  vi.spyOn(row, 'getBoundingClientRect').mockImplementation(() => ({
    top: 220 - panel.scrollTop, bottom: 240 - panel.scrollTop,
  } as DOMRect));
  revealTreeRow(row);
  expect(row.getBoundingClientRect().top).toBeGreaterThanOrEqual(bar.getBoundingClientRect().bottom);
  expect(surface.scrollTop).toBe(280);
});
