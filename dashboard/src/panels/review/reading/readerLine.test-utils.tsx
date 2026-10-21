// The reading column laid out for the reader's line tests (MIK-R89 v2 rule 9, amendment 10fb84cb).
// jsdom has no layout: the column's blocks get the heights below (an inline height overrides a
// block's), laid out in document order under the column's `scrollTop`, which the browser rounds to
// whole pixels and clamps to the content, with a scroll event for each change; a head given a height
// stays at the column's top. Shared by ReviewPage.readerLine.test.tsx (the page) and
// readerAnchor.editors.test.tsx (editors in the column).
import { StateEffect, StateField } from '@codemirror/state';
import { Decoration, EditorView, WidgetType } from '@codemirror/view';
import { act, fireEvent, render } from '@testing-library/react';
import { useRef } from 'react';
import { vi } from 'vitest';

import { useReaderAnchor } from './readerAnchor';

export const COLUMN_TOP = 100;
export const VIEWPORT = 600;
// A block's height: a number, or one read off the element (a pane drawn or not yet); `null` makes the
// selector's elements no blocks, so that they span the blocks inside them.
export type Height = number | null | ((element: Element) => number);
// The heights of the blocks that take room; every other element spans the blocks inside it.
const HEIGHTS: [string, Height][] = [
  ['[data-testid="review-subject-select"]', 30],
  ['[data-slot-state="pending"]', 48],
  ['[data-slot-state="answered"]', 8000],
  ['[data-slot-state="unavailable"]', 400],
  ['[data-testid="review-subject-cards-reading"]', 20],
  ['[data-testid="review-expression-card"]', 400],
  ['[data-testid="review-lane-section"] h3', 30],
  // A lane file: its header row, then its body (waiting, 60 in all; read, 900 in all).
  ['[data-testid="review-lane-file"] > div:first-child', 39],
  ['[data-testid="review-lane-file-waiting"]', 21],
  ['[data-testid="review-lane-diff"]', 861],
  ['[data-testid="review-details"]', 1000],
  // The blank the reader's line holds at the column's end (its inline height, set by the anchor).
  ['[data-reading-spacer]', 0],
  // CodeMirror's stand-in for lines it has not drawn (its inline height, from the editor's heights).
  ['.cm-gap', 0],
];

// Elements whose children stand side by side, each from its top (a side-by-side diff's two editors):
// as tall as their tallest child.
const SIDES = '[data-t-sides]';

const box = (top: number, height: number) =>
  ({ top, bottom: top + height, height, left: 0, right: 800, width: 800, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;

const scroller = () => document.querySelector<HTMLElement>('[data-review-scroller]');

// Whether `later` comes after `earlier` in the document, outside it.
const follows = (earlier: Node, later: Node) => Boolean(earlier.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING);

interface Laid {
  blocks: Element[];
  rooms: [Element, number][];
}

// What the held boxes before `element` add above it, and those it holds add to it.
function roomsAround(element: Element, rooms: [Element, number][]): { start: number; height: number } {
  let start = 0;
  let height = 0;
  for (const [held, room] of rooms) {
    if (held === element || element.contains(held)) height += room;
    else if (!held.contains(element) && follows(held, element)) start += room;
  }
  return { start, height };
}

// The column's layout: each block's top is the sum of the blocks before it, less `scrollTop`. A box
// with an inline min-height (LaneSection.tsx's held file body, ExpressionCards.tsx's held pane and
// excerpt) is at least that tall: the room beyond its blocks and the held boxes inside it is at its
// end. A box of sides is one block outside, its children laid out side by side inside.
class ColumnLayout {
  private readonly heights: [string, Height][];
  private stored = 0;
  private clampCount = 0;
  private laid: Laid | null = null;
  // A test's hook on every layout read (a box, the scroll position, the content's height), until it
  // answers false. Inside a React commit the cache's MutationObserver has not run yet, so the hook
  // asks for the layout afresh (`fresh`) where it must see the commit's DOM.
  private watching: (() => boolean) | null = null;
  // Created before the page, so it runs before the page's own observers: their reads see this layout.
  // A read also drops it when the document changed since it was laid out, within one task too (an
  // editor's measurement draws and reads in one), as the browser lays out again.
  private readonly changes = new MutationObserver(() => {
    this.laid = null;
  });

  constructor(extra: [string, Height][]) {
    this.heights = [...extra, ...HEIGHTS];
    this.changes.observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });
    const rectOf = (element: Element) => {
      this.read();
      return this.rectOf(element);
    };
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      return rectOf(this);
    });
  }

  // How often the browser clamped the column's scroll position to a shrunk content's end.
  clamps(): number {
    return this.clampCount;
  }

  // Calls `hook` at every layout read until it answers false; `rectOf` is a box for it to read.
  watch(hook: () => boolean): void {
    this.watching = hook;
  }

  fresh(): void {
    this.laid = null;
  }

  rectOf(element: Element): DOMRect {
    const column = scroller();
    if (!column || element === column) return box(COLUMN_TOP, VIEWPORT);
    if (!column.contains(element)) return box(0, 0);
    // The sticky head, when it has a height: always at the column's top (it is the column's first block).
    const head = element.matches('[data-reading-head]') ? this.heightOf(element) : undefined;
    if (head !== undefined) return box(COLUMN_TOP, head);
    const top = COLUMN_TOP - this.scrollTop(column);
    const { blocks, rooms } = this.layoutOf(column);
    return this.amongBlocks(element, blocks, top, roomsAround(element, rooms));
  }

  attach(column: HTMLElement): void {
    Object.defineProperty(column, 'scrollTop', {
      configurable: true,
      get: () => {
        this.read();
        return this.scrollTop(column);
      },
      set: (value: number) => {
        const next = Math.min(Math.max(0, Math.round(value)), Math.max(0, this.total(column) - VIEWPORT));
        if (next !== this.stored) this.scrolled(column);
        this.stored = next;
      },
    });
    Object.defineProperty(column, 'scrollHeight', {
      configurable: true,
      get: () => {
        this.read();
        return Math.max(this.total(column), VIEWPORT);
      },
    });
    Object.defineProperty(column, 'clientHeight', { configurable: true, get: () => VIEWPORT });
  }

  private heightOf(element: Element): number | undefined {
    if (element.matches(SIDES)) return Math.max(0, ...[...element.children].map((child) => this.inner(child)));
    const rule = this.heights.find(([selector]) => element.matches(selector))?.[1];
    if (rule === undefined || rule === null) return undefined;
    const inline = element instanceof HTMLElement ? parseFloat(element.style.height) : NaN;
    if (!Number.isNaN(inline)) return inline;
    return typeof rule === 'function' ? rule(element) : rule;
  }

  // The height of what is inside a box of sides: a block's own, else its children's in a column.
  private inner(element: Element): number {
    return this.heightOf(element) ?? [...element.children].reduce((total, child) => total + this.inner(child), 0);
  }

  // Where `target` starts below the top of the box of sides that holds it.
  private offsetInside(sides: Element, target: Element): number {
    let offset = 0;
    for (let node = target; node !== sides && node.parentElement; node = node.parentElement) {
      if (node.parentElement.matches(SIDES)) continue;
      for (const sibling of node.parentElement.children) {
        if (sibling === node) break;
        offset += this.inner(sibling);
      }
    }
    return offset;
  }

  private amongBlocks(element: Element, blocks: Element[], top: number, around: { start: number; height: number }): DOMRect {
    let { start, height } = around;
    for (const block of blocks) {
      if (block !== element && block.contains(element))
        return block.matches(SIDES) ? box(top + start + this.offsetInside(block, element), this.inner(element)) : box(top + start, 0);
      if (block === element || element.contains(block)) height += this.heightOf(block)!;
      else if (follows(block, element)) start += this.heightOf(block)!;
    }
    return box(top + start, height);
  }

  private read(): void {
    if (!this.watching) return;
    const hook = this.watching;
    this.watching = null;
    if (hook()) this.watching = hook;
  }

  private sum(blocks: Element[]): number {
    return blocks.reduce((total, block) => total + this.heightOf(block)!, 0);
  }

  // The blocks in document order, none inside another (a block's inside is not walked), and the held
  // boxes outside any block.
  private layoutOf(column: HTMLElement): Laid {
    if (this.changes.takeRecords().length > 0) this.laid = null;
    if (this.laid) return this.laid;
    const blocks: Element[] = [];
    const held: HTMLElement[] = [];
    this.walk(column, blocks, held);
    this.laid = { blocks, rooms: this.roomsOf(blocks, held) };
    return this.laid;
  }

  private walk(parent: Element, blocks: Element[], held: HTMLElement[]): void {
    for (const child of parent.children) {
      if (this.heightOf(child) !== undefined) blocks.push(child);
      else {
        if (child instanceof HTMLElement && child.style.minHeight !== '') held.push(child);
        this.walk(child, blocks, held);
      }
    }
  }

  // Inner held boxes first: a held box inside another is part of what fills the outer one.
  private roomsOf(blocks: Element[], held: HTMLElement[]): [Element, number][] {
    const rooms: [Element, number][] = [];
    for (const element of [...held].reverse()) {
      const inner = rooms.filter(([room]) => element.contains(room)).reduce((total, [, more]) => total + more, 0);
      const filled = this.sum(blocks.filter((block) => element.contains(block))) + inner;
      rooms.unshift([element, Math.max(0, parseFloat(element.style.minHeight) - filled)]);
    }
    return rooms;
  }

  private total(column: HTMLElement): number {
    const { blocks, rooms } = this.layoutOf(column);
    return this.sum(blocks) + rooms.reduce((total, [, more]) => total + more, 0);
  }

  private scrolled(column: HTMLElement): void {
    setTimeout(() => column.dispatchEvent(new Event('scroll')), 0);
  }

  // The browser's scroll position: whole pixels, never past the content's end (a shrink clamps it).
  private scrollTop(column: HTMLElement): number {
    const max = Math.max(0, this.total(column) - VIEWPORT);
    if (this.stored > max) {
      this.stored = max;
      this.clampCount += 1;
      this.scrolled(column);
    }
    return this.stored;
  }
}

export function mockLayout(extra: [string, Height][] = []): ColumnLayout {
  return new ColumnLayout(extra);
}

export const column = () => document.querySelector<HTMLElement>('[data-review-scroller]')!;

// The reader scrolls the column to `top`, as a wheel does.
export function scrollTo(top: number): void {
  fireEvent.wheel(column(), { deltaY: 120 });
  column().scrollTop = top;
  fireEvent.scroll(column());
}

export const nextFrame = () => act(async () => new Promise((resolve) => requestAnimationFrame(() => resolve(undefined))));

export const settle = () => act(async () => new Promise((resolve) => setTimeout(resolve, 0)));

// The anchor's clock (a gesture's scrolls are the reader's for a while after it): the real clock plus
// what the case has moved it on, so no case waits on the wall clock.
let clockAhead = 0;

export function installAnchorClock(): void {
  clockAhead = 0;
  const now = performance.now.bind(performance);
  vi.spyOn(performance, 'now').mockImplementation(() => now() + clockAhead);
}

// The gesture's scrolls are over: a later scroll is no longer the reader's (the anchor's clock moves
// past the gesture's 400 ms).
export const gestureOver = () => {
  clockAhead += 450;
  return settle();
};

// The microtasks a frame's callbacks queued (the column's observer and what its corrections set off)
// have run: a fixed number of turns, so the order never depends on the machine's load.
async function microtasks(): Promise<void> {
  for (let turn = 0; turn < 20; turn += 1) await Promise.resolve();
}

// What the next frame shows after `work`: `work` runs as the frame's last animation callback (each
// step asks for its frame after every callback the frame before queued), then the microtasks run, as
// the browser paints then.
function afterFrame<T>(work: () => void, read: () => T): Promise<T> {
  return new Promise((resolve) =>
    requestAnimationFrame(async () => {
      work();
      await microtasks();
      resolve(read());
    }),
  );
}

// What each of the next `count` frames shows.
export async function painted<T>(count: number, read: () => T): Promise<T[]> {
  const seen: T[] = [];
  await act(async () => {
    for (let frame = 0; frame < count; frame += 1) seen.push(await afterFrame(() => undefined, read));
  });
  return seen;
}

// What one frame shows after `work` runs in its animation callbacks (as an editor's measurement does).
export async function paintedAfter<T>(work: () => void, read: () => T): Promise<T> {
  let seen: T | undefined;
  await act(async () => {
    seen = await afterFrame(work, read);
  });
  return seen as T;
}

// A column with the anchor and only the blocks a case adds before its spacer.
function AnchoredColumn() {
  const ref = useRef<HTMLDivElement>(null);
  useReaderAnchor(ref, () => false);
  return (
    <div ref={ref} data-review-scroller>
      <div data-reading-spacer />
    </div>
  );
}

// Renders an anchored column laid out by `layout`; the answer adds blocks before its spacer.
export function anchoredColumn(layout: ColumnLayout): (...blocks: HTMLElement[]) => void {
  render(<AnchoredColumn />);
  layout.attach(column());
  return (...blocks) => act(() => column().querySelector('[data-reading-spacer]')!.before(...blocks));
}

export function testBlock(testid: string): HTMLElement {
  const element = document.createElement('div');
  element.dataset.testid = testid;
  return element;
}

export const docOf = (count: number, prefix = 'line') =>
  Array.from({ length: count }, (_, index) => `${prefix} ${index + 1}`).join('\n');

// The top of an editor's drawn line with this text, NaN when it is not drawn.
export function lineTopIn(view: EditorView, text: string): number {
  const line = [...view.contentDOM.children].find((child) => child.classList.contains('cm-line') && child.textContent === text);
  return line ? line.getBoundingClientRect().top : NaN;
}

// The editor's drawn line that holds the column's top.
export function lineAtTop(view: EditorView): { text: string; top: number } {
  const line = [...view.contentDOM.children].find((child) => {
    const rect = child.getBoundingClientRect();
    return child.classList.contains('cm-line') && rect.top <= COLUMN_TOP && rect.bottom > COLUMN_TOP;
  });
  return { text: line?.textContent ?? '', top: line ? line.getBoundingClientRect().top : NaN };
}

// A block of a given height at a line's start: a side-by-side diff's spacer.
class TestSpacer extends WidgetType {
  constructor(readonly height: number) {
    super();
  }
  eq(other: TestSpacer) {
    return other.height === this.height;
  }
  toDOM() {
    const element = document.createElement('div');
    element.className = 't-spacer';
    element.style.height = `${this.height}px`;
    return element;
  }
  get estimatedHeight() {
    return this.height;
  }
}

export const growSpacer = StateEffect.define<number>();

// A spacer before an editor's line 3, grown by `growSpacer`.
export const spacerAtLine3 = StateField.define<number>({
  create: () => 0,
  update: (height, tr) => tr.effects.reduce((total, effect) => (effect.is(growSpacer) ? total + effect.value : total), height),
  provide: (field) =>
    EditorView.decorations.compute([field], (state) =>
      Decoration.set(
        state.field(field) > 0
          ? [Decoration.widget({ widget: new TestSpacer(state.field(field)), block: true, side: -1 }).range(state.doc.line(3).from)]
          : [],
      ),
    ),
});
