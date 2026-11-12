// "The page never moves under the reader" (MIK-R89 v2 rule 9, amendment 10fb84cb). Once the reader
// has scrolled, reads that land above the reader's line or at it, and the browser's clamp, leave
// that line within 1 px of where it was on screen. The one exception is the single scroll after a
// selection's read (useScrollOnce.ts).
//
// THE READER'S LINE is the first settled block at the top of the column's view, under its sticky
// head, as CSS scroll anchoring picks one: the first block in document order that starts at or
// below the visible top. A block wholly above is passed over, one that straddles the visible top is
// searched inside, and when nothing starts below, the deepest block that holds it is taken. Content
// re-rendered as a whole (an editor, its pane, a card's excerpt) is not searched inside: the next
// settled block below it is the line, and the box itself only when nothing starts below. Once that
// box starts under the head (it shrank, or the head did), it is the first settled block at the top
// and the line, at its top then, so nothing moves (ruling fb5d04f6; final2 probe: an excerpt 1 px
// under the head, the block below it kept, moved 70 px as its marks landed). When the visible top
// falls inside an editor in such a box, the line is that editor's block there, kept by its document
// position, not by its DOM node: the editor's lines at the top hold while the editor is measured,
// draws more of its lines or is built again, and the block below the box is kept only while no
// editor or held drawing of it is left (rule 9, ruling 8450399b (1); WP8 on A4-F3, `center` at the
// tall heights: the block below a pane that had never been in view was kept, and as the pane was
// first measured, 238 → 166.6 px, its lines at the view top moved 65–73 px). Pending content (a
// pending slot, cards or a lane file being read) is never the line's block: it is about to be
// replaced. The pointer plays no part. Growth below the line, from the lazy lane reads in view
// (rule 2), moves only what is below it; a waiting lane file reserves what its read will draw from
// what the lane read already gives and holds its height until its diff has drawn
// (LaneSection.tsx), so no shorter file exists in between. The browser's own scroll anchoring is
// not used: it anchors the first visible node, often the pending block itself, and when that block
// is replaced it keeps nothing (A2-F1 E7: the reader's line moved 20,000 px). The column turns it
// off (ReadingColumn.tsx) while it is the page scroller.
//
// AN EDITOR FIRST IN VIEW. CodeMirror measures an editor only while it is in the window: one drawn
// outside it keeps its first estimates (14 px lines, 7 px characters, so a line of over 30 characters
// is taken to wrap; a removed chunk or a fold as nothing), and its gutters, its gaps and a
// comparison's spacers follow those estimates. It learns that it is in view from an
// IntersectionObserver, which reports after the browser has painted, so its first measurement, and
// every height it corrects, would come one frame after the scroll that brought it in. A scroll of
// the column asks each editor it brings into view and that has not been measured in view for its
// measurement, and runs it at once (EDITORS AT A FIXED POINT).
//
// EDITORS AT A FIXED POINT. CodeMirror runs a measurement it has pending as soon as its layout is
// read. Before the anchor takes or corrects the line, it runs, round after round, every measurement
// pending in the editors in view after a scroll, and in the editors of the line's box after any other
// change, until a round changes nothing in the column. A side-by-side diff's editors align each
// other through the merge view's spacers, and each alignment asks the other editor for one more
// measurement, which CodeMirror would run a frame later (rule 9, ruling 8450399b (1); WP8 on A4-F4,
// 1920 p1: a second measurement before the second paint re-laid side a by +46.2 px and side b by
// −13.3 px, and the anchor followed a paint later). So the first painted frame after a scroll is
// already the one those measurements leave, on both sides, and so is each frame after a re-layout.
// A top that falls where an editor has drawn nothing yet (WP8 on A4-F4, p1 and p3 and two scrollbar
// drags: the top in a `cm-gap`, 13–18 lines drawn, the block below the excerpt kept and corrected
// the wrong way by 40–408 px) is drawn by that measurement before the line is taken; should it still
// fall in a gap, the editor's heights give the position there (`lineBlockAtHeight`), held from then
// on by position like any other.
//
// KEEPING IT. The line's block and its top on screen (the target) are taken at a new place: a
// scroll by the reader's gesture (the gestures that disarm the one landing of a selection,
// useScrollOnce.ts), a product scroll (blockDom.ts `scrollColumnTo`), any other scroll the browser
// did not make by clamping, or a block that is gone. A block drawn again in its place stays the kept
// block, with its target; a block that went (All to Changed, the Worklist, another comparison) takes
// the line with it: the column is corrected by its nearest ancestor's move, the spacer goes, and the
// line is taken where the content then is, never on a blank. After every change to the column's
// content (a React commit, a mounted editor), before the browser paints, the column's own
// `scrollTop` moves so that the block's top is back at its target. Every correction aims at that
// same target, so corrections never add up to a drift, and a correction is no new place. A clamp is
// the browser's scroll when content shrinks below the column's maximum: it lowers the column to its
// new maximum. Before the browser paints, a spacer at the column's end takes the room the shrink
// took and the column goes back, so neither the scroll position nor the line moves; the spacer
// gives the room back as the content grows again, and never so much that the column clamps (dev6 E7
// at 1440). A clamp seen after a change to the content is the browser's even while a gesture's
// scrolls still count as the reader's (dev5 E7 at 1920: the content shrank 414 ms after a wheel,
// and the clamped place, taken as the reader's, moved the line 52 px). Nothing is kept while a
// selection's one landing is still due (`holding`), at the top of the column (nothing is above the
// reader there), or in the stacked narrow layout, where the page scrolls.
import { EditorView } from '@codemirror/view';
import { useEffect } from 'react';

import { columnScrolls, PRODUCT_SCROLL } from './blockDom';
import { BLOCK_STOP } from './readingStyles';

// Content about to be replaced by a read: the selected subject's pending slot, a subject's cards
// being read, a lane file waiting for its read, the lane's own reading line.
const PENDING = [
  '[aria-busy="true"]',
  '[data-slot-state="pending"]',
  '[data-testid="review-subject-cards-reading"]',
  '[data-testid="review-lane-file-waiting"]',
  '[data-testid="review-lane-section"] > [role="status"]',
].join(', ');

const HEAD = '[data-reading-head]';

// The blank the column holds at its end while a shrink would clamp it (ReadingColumn.tsx renders it
// as the column's last child).
const SPACER = 'data-reading-spacer';

// Content re-rendered as a whole: an editor's own nodes (its lines, its gutters) come and go with each
// rebuild of its pane, and a card's excerpt is such a pane (dev6: the kept line "170" of the
// `recordLabelOf` excerpt was replaced while the cards above it shrank). A block found inside one
// would be gone at its next rebuild, so the search does not enter it.
const WHOLE = [
  '.cm-editor',
  '[data-testid="diff-pane"]',
  '[data-testid="file-pane"]',
  '[data-testid="review-card-excerpt"]',
].join(', ');

// What the search passes over: the sticky head, pending content and the spacer.
const SKIP = `${HEAD}, ${PENDING}, [${SPACER}]`;

const EDITOR = '.cm-editor';
const PANE = '[data-testid="diff-pane"], [data-testid="file-pane"]';
// A destroyed editor's drawing, kept in its pane until the next editor is in it (ExpressionCards.tsx).
const HELD = '[data-held-snapshot]';
// CodeMirror's stand-in for lines it has not drawn.
const GAP = 'cm-gap';

// The attributes that name a block: a replacement with the same tag and the same values is the same
// block drawn again.
const IDENTITY = ['id', 'data-testid', 'data-path', 'data-entry', 'data-invariant', 'data-subject', BLOCK_STOP];

// How long after a gesture a scroll is still the reader's (a wheel's or a key's scroll animates).
const GESTURE_MS = 400;

const SCROLL_KEYS = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' ']);

// A key that scrolls the column, pressed outside a control.
export function isScrollKey(event: KeyboardEvent): boolean {
  const control =
    event.target instanceof HTMLElement &&
    Boolean(event.target.closest('button, input, textarea, select, [contenteditable="true"]'));
  return SCROLL_KEYS.has(event.key) && !control;
}

// The column's visible top: under its sticky head.
function visibleTop(scroller: HTMLElement): number {
  const top = scroller.getBoundingClientRect().top;
  const head = scroller.querySelector(HEAD);
  return head ? Math.max(top, head.getBoundingClientRect().bottom) : top;
}

interface Found {
  below: HTMLElement | null;
  holder: HTMLElement | null;
  // The box re-rendered as a whole that holds `line`, not entered.
  whole: HTMLElement | null;
}

interface Line {
  element: HTMLElement | null;
  // The box re-rendered as a whole that holds `line` above the line's block.
  whole: HTMLElement | null;
  // The box re-rendered as a whole that holds `line`, whether or not a block starts below it.
  box: HTMLElement | null;
}

// The first element in document order that starts at or below `line`, and the deepest one holding it.
// A box re-rendered as a whole that holds `line` is not entered: the first block below it, in its
// nearest ancestor that is not, is the line's block, and the box itself only when nothing starts below.
function search(parent: Element, line: number, found: Found): void {
  for (const child of parent.children) {
    if (found.below) return;
    if (!(child instanceof HTMLElement) || child.matches(SKIP)) continue;
    const box = child.getBoundingClientRect();
    if (box.height <= 0 || box.bottom <= line) continue;
    if (box.top >= line - 0.5) found.below = child;
    else {
      found.holder = child;
      if (!child.matches(WHOLE)) search(child, line, found);
      else found.whole = child;
    }
  }
}

// The line's block and, when it is a block below a box re-rendered as a whole, that box.
function lineElement(scroller: HTMLElement, line: number): Line {
  const found: Found = { below: null, holder: null, whole: null };
  search(scroller, line, found);
  return { element: found.below ?? found.holder, whole: found.below ? found.whole : null, box: found.whole };
}

// THE LINE IN AN EDITOR. Its editor's place in the box (its pane among the box's panes, -1 for an
// editor that is the box; its side of a comparison), the document position and the kind of the
// editor's block there ('' for a position taken from the editor's heights, any block there), that
// block's target top, and the block last drawn for it. An editor built again in its place is the
// same editor: its block at the same position is the line. Side b is held first: it is the after side
// and a unified diff's one editor, so a change of view keeps the side; the merge view aligns side a
// to it at every chunk, and at a fixed point nothing re-lays either side after the line is taken.
interface EditorLine {
  box: HTMLElement;
  pane: number;
  side: string;
  pos: number;
  kind: string;
  target: number;
  drawn: HTMLElement | null;
}

function within(root: HTMLElement, selector: string): HTMLElement[] {
  return root.matches(selector) ? [root] : [...root.querySelectorAll<HTMLElement>(selector)];
}

// The editors in `root` with their views, not a held drawing of one.
function liveEditors(root: HTMLElement): { dom: HTMLElement; view: EditorView }[] {
  const live: { dom: HTMLElement; view: EditorView }[] = [];
  for (const dom of within(root, EDITOR)) {
    const view = dom.closest(HELD) ? null : EditorView.findFromDOM(dom);
    if (view) live.push({ dom, view });
  }
  return live;
}

function sideOf(editor: HTMLElement): string {
  if (editor.classList.contains('cm-merge-a')) return 'a';
  return editor.classList.contains('cm-merge-b') ? 'b' : '';
}

// A line of text, or a block widget by its first class (a removed chunk, a fold, a spacer).
function kindOf(block: Element): string {
  return block.classList.contains('cm-line') ? 'cm-line' : (block.classList[0] ?? block.tagName);
}

// The editor's first drawn block whose bottom is below `line`: the one that holds it, else the first
// below it (a gap is no drawn block).
function blockAt(view: EditorView, line: number): HTMLElement | null {
  for (const child of view.contentDOM.children) {
    if (!(child instanceof HTMLElement) || child.classList.contains(GAP)) continue;
    const box = child.getBoundingClientRect();
    if (box.height > 0 && box.bottom > line) return child;
  }
  return null;
}

// The line in one editor that holds the column's top `line`: its drawn block there, else the
// position its heights give there.
function lineIn(box: HTMLElement, dom: HTMLElement, view: EditorView, line: number): EditorLine | null {
  const rect = dom.getBoundingClientRect();
  if (rect.top >= line || rect.bottom <= line) return null;
  const pane = dom.closest(PANE);
  const place = { box, pane: pane instanceof HTMLElement ? within(box, PANE).indexOf(pane) : -1, side: sideOf(dom) };
  const block = blockAt(view, line);
  if (block)
    return { ...place, pos: view.posAtDOM(block, 0), kind: kindOf(block), target: block.getBoundingClientRect().top, drawn: block };
  const mapped = view.lineBlockAtHeight(line - view.documentTop);
  return { ...place, pos: mapped.from, kind: '', target: view.documentTop + mapped.top, drawn: null };
}

// A line of text before a block widget, side b (or a single editor) before side a.
function rank(at: EditorLine): number {
  return (at.kind === 'cm-line' ? 0 : 2) + (at.side === 'a' ? 1 : 0);
}

// The line when the column's top `line` falls inside an editor in `box`.
function editorLine(box: HTMLElement, line: number): EditorLine | null {
  let best: EditorLine | null = null;
  for (const { dom, view } of liveEditors(box)) {
    const at = lineIn(box, dom, view, line);
    if (at && (!best || rank(at) < rank(best))) best = at;
  }
  return best;
}

// The line's editor now: the live editor at its pane and side.
function editorAt(at: EditorLine): EditorView | null {
  const root = at.pane < 0 ? at.box : within(at.box, PANE)[at.pane];
  return root ? (liveEditors(root).find(({ dom }) => sideOf(dom) === at.side)?.view ?? null) : null;
}

// The editor's block at a position's DOM place.
function contentChildAt(view: EditorView, pos: number): Element | null {
  const content = view.contentDOM;
  const { node, offset } = view.domAtPos(pos);
  let child: Node | null = node === content ? (content.childNodes[offset] ?? content.lastChild) : node;
  while (child && child.parentNode !== content) child = child.parentNode;
  return child instanceof Element ? child : null;
}

function isLineBlock(view: EditorView, block: Element | null, at: EditorLine): block is HTMLElement {
  return (
    block instanceof HTMLElement &&
    !block.classList.contains(GAP) &&
    (at.kind === '' || kindOf(block) === at.kind) &&
    view.posAtDOM(block, 0) === at.pos
  );
}

// The block the editor draws for the line's position now, found from the position's DOM place: it or
// a block next to it (a block widget at a line's start shares the line's position).
function drawnFor(view: EditorView, at: EditorLine): HTMLElement | null {
  const child = contentChildAt(view, at.pos);
  if (!child) return null;
  return [child, child.previousElementSibling, child.nextElementSibling].find((block) => isLineBlock(view, block, at)) ?? null;
}

// The line's top now: its editor's drawing of it, the held drawing of a destroyed editor until the
// next one is in its pane, else where its editor's heights put its position before the editor
// draws it; null when neither its editor nor a drawing of it is left.
function editorTop(at: EditorLine): number | null {
  if (!at.box.isConnected) return null;
  if (at.drawn?.isConnected && at.drawn.closest(HELD)) return at.drawn.getBoundingClientRect().top;
  const view = editorAt(at);
  if (!view) return null;
  const block = drawnFor(view, at);
  if (!block) return view.documentTop + view.lineBlockAt(Math.min(at.pos, view.state.doc.length)).top;
  at.drawn = block;
  at.kind = kindOf(block);
  return block.getBoundingClientRect().top;
}

// The line's top, or null on any of CodeMirror's errors (an editor in the middle of its update).
function editorTopSafely(at: EditorLine): number | null {
  try {
    return editorTop(at);
  } catch {
    return null;
  }
}

function editorLineSafely(box: HTMLElement | null, line: number): EditorLine | null {
  if (!box) return null;
  try {
    return editorLine(box, line);
  } catch {
    return null;
  }
}

// How many rounds of pending measurements EDITORS AT A FIXED POINT runs at most: an editor's own,
// the other side's alignment, and its answer.
const ROUNDS = 4;

// Runs the editor's pending measurement, if it has one: CodeMirror runs it before a read of its
// layout.
function flush(view: EditorView): void {
  try {
    view.lineBlockAtHeight(0);
  } catch {
    // An editor in the middle of its own update measures at its end.
  }
}

// EDITORS AT A FIXED POINT: those `ask` names are asked for a measurement, then every pending one
// runs, round after round, until a round changes nothing in the column (`drained` takes the changes
// the column's observer has not delivered yet). Whether any changed it.
function settle(views: readonly EditorView[], ask: (view: EditorView) => boolean, drained: () => boolean): boolean {
  for (const view of views) if (ask(view)) view.requestMeasure();
  let changed = false;
  for (let round = 0; round < ROUNDS && views.length > 0; round += 1) {
    views.forEach(flush);
    if (!drained()) break;
    changed = true;
  }
  return changed;
}

const viewsOf = (root: HTMLElement) => liveEditors(root).map(({ view }) => view);

// The live editors in the column's view.
function shownEditors(scroller: HTMLElement): EditorView[] {
  const { top, bottom } = scroller.getBoundingClientRect();
  const views: EditorView[] = [];
  for (const dom of scroller.querySelectorAll<HTMLElement>(EDITOR)) {
    const box = dom.getBoundingClientRect();
    const view = box.bottom <= top || box.top >= bottom || dom.closest(HELD) ? null : EditorView.findFromDOM(dom);
    if (view) views.push(view);
  }
  return views;
}

// One step from the kept block up to the column: an ancestor, the index of the path's child among its
// children, and its top less the kept block's top when the line was last seen.
interface Step {
  element: Element;
  index: number;
  offset: number;
}

interface Kept {
  element: HTMLElement;
  target: number;
  chain: Step[];
  // The box re-rendered as a whole that held the visible top above the kept block when it was taken.
  whole: HTMLElement | null;
  // The line inside an editor in that box. While it is drawn the column is corrected by it, and the
  // kept block's target is where that block is with the line at its target, so that the block takes
  // over where the editor's line would be once no editor or drawing of it is left.
  editor: EditorLine | null;
}

function chainOf(scroller: HTMLElement, element: HTMLElement): Step[] {
  const top = element.getBoundingClientRect().top;
  const chain: Step[] = [];
  for (let child: Element = element; child !== scroller && child.parentElement; child = child.parentElement) {
    const parent = child.parentElement;
    chain.push({ element: parent, index: [...parent.children].indexOf(child), offset: parent.getBoundingClientRect().top - top });
  }
  return chain;
}

// The element now at the kept block's place: down from the chain's ancestor `at` by the indices.
function atPath(chain: Step[], at: number): Element | undefined {
  let element: Element | undefined = chain[at].element;
  for (let step = at; step >= 0 && element; step -= 1) element = element.children[chain[step].index];
  return element;
}

function sameBlock(candidate: Element | undefined, kept: HTMLElement): candidate is HTMLElement {
  return (
    candidate instanceof HTMLElement &&
    candidate.tagName === kept.tagName &&
    IDENTITY.every((name) => candidate.getAttribute(name) === kept.getAttribute(name))
  );
}

// How far the kept line is off its target: the line inside an editor while it is drawn (the kept
// block's target follows it), else the kept block.
function offTarget(kept: Kept): number {
  const block = kept.element.getBoundingClientRect().top;
  const line = kept.editor ? editorTopSafely(kept.editor) : null;
  if (!kept.editor || line === null) return block - kept.target;
  const off = line - kept.editor.target;
  kept.target = block - off;
  return off;
}

class ReaderLine {
  private kept: Kept | null = null;
  // The column's scroll position as this line knows it: taken, corrected or clamped.
  private known = 0;
  // The tallest content since the line was taken: a clamp follows a shrink.
  private tallest = 0;
  private gestureUntil = Number.NEGATIVE_INFINITY;
  private dragging = false;
  // The spacer's height: the room a shrink at the column's end took.
  private held = 0;
  // Takes the changes the column's observer has not delivered yet: whether there were any.
  private drained: () => boolean = () => false;

  constructor(
    private readonly node: HTMLElement,
    private readonly holding: () => boolean,
  ) {}

  drainWith(drained: () => boolean): void {
    this.drained = drained;
  }

  // The line where the column is now; after a scroll (`measure`), the editors of the box at the top
  // measured first, so that one drawn before the scroll draws the lines it now shows there.
  take(measure = false): void {
    this.kept = null;
    if (columnScrolls() && this.node.scrollTop > 0) this.settleTop(measure);
    this.known = this.node.scrollTop;
    this.tallest = this.node.scrollHeight;
    if (!columnScrolls() || this.node.scrollTop <= 0) return;
    const line = visibleTop(this.node);
    const { element, whole, box } = lineElement(this.node, line);
    if (!element) return;
    const editor = editorLineSafely(box, line);
    this.kept = { element, target: element.getBoundingClientRect().top, chain: chainOf(this.node, element), whole, editor };
  }

  private settleTop(measure: boolean): void {
    const { box } = lineElement(this.node, visibleTop(this.node));
    if (box) settle(viewsOf(box), () => measure, this.drained);
  }

  // After a scroll: the editors in the column's view at their fixed point, those not measured in view
  // asked for a measurement (AN EDITOR FIRST IN VIEW). Whether that changed the column.
  settleShown(): boolean {
    return settle(shownEditors(this.node), (view) => !view.inView, this.drained);
  }

  // After a change to the column's content: the kept block goes back to its target top. A kept block
  // that is gone is settled first, so that a clamp its going caused is not held for a line that went.
  // The editors of the kept line's box are brought to their fixed point first.
  changed(): void {
    this.settleKept();
    if (this.kept && !this.holding() && !this.kept.element.isConnected) this.replaced(this.kept);
    if (Math.abs(this.node.scrollTop - this.known) >= 0.5) this.scrolled(true);
    const kept = this.kept;
    if (this.holding() || !kept) {
      this.take();
      return;
    }
    this.correct(kept);
    this.underHead(kept);
    this.release();
  }

  private settleKept(): void {
    const box = this.kept?.editor?.box;
    if (box?.isConnected) settle(viewsOf(box), () => false, this.drained);
  }

  private correct(kept: Kept): void {
    this.tallest = Math.max(this.tallest, this.node.scrollHeight);
    const moved = offTarget(kept);
    if (Math.abs(moved) >= 0.5) {
      this.node.scrollTop = this.node.scrollTop + moved;
      this.known = this.node.scrollTop;
    }
    kept.chain = chainOf(this.node, kept.element);
  }

  // The box re-rendered as a whole that held the visible top when the line was taken now starts under
  // the head, settled: it is the line from here, at its top now (ruling fb5d04f6). Its rebuilds and its
  // marks' reads then grow or shrink only what is below it. Not while a clamp keeps the kept block off
  // its target.
  private underHead(kept: Kept): void {
    const box = kept.whole;
    if (!box) return;
    if (!box.isConnected || box.closest(PENDING)) {
      kept.whole = null;
      return;
    }
    const top = box.getBoundingClientRect().top;
    if (top < visibleTop(this.node) - 0.5 || Math.abs(kept.element.getBoundingClientRect().top - kept.target) >= 0.5) return;
    this.kept = { element: box, target: top, chain: chainOf(this.node, box), whole: null, editor: null };
  }

  // The kept block is gone. The element now at its path under its nearest connected ancestor, when it
  // is the same block drawn again, is the kept block, with the same target. Otherwise the line went
  // with the block (a filter, the Worklist or another comparison removed it): the spacer held room for
  // that line and goes, the column is corrected by that ancestor's own move as far as the content
  // reaches, and the line is taken again where the column then is, on the surviving content or the
  // content's end.
  private replaced(kept: Kept): void {
    const at = kept.chain.findIndex((step) => step.element.isConnected);
    const same = at >= 0 ? atPath(kept.chain, at) : undefined;
    if (sameBlock(same, kept.element)) {
      kept.element = same;
      return;
    }
    if (this.toBox(kept)) return;
    const spacer = this.spacer();
    if (spacer) this.hold(spacer, 0);
    if (at >= 0) {
      const ancestor = kept.chain[at];
      const moved = ancestor.element.getBoundingClientRect().top - (kept.target + ancestor.offset);
      if (Math.abs(moved) >= 0.5) this.node.scrollTop = this.node.scrollTop + moved;
    }
    this.take();
  }

  // The kept block went while the line inside an editor is still drawn: the box that holds the editor
  // is the block kept below the line from here.
  private toBox(kept: Kept): boolean {
    const at = kept.editor;
    const top = at ? editorTopSafely(at) : null;
    if (!at || top === null) return false;
    kept.element = at.box;
    kept.whole = null;
    kept.target = at.box.getBoundingClientRect().top - (top - at.target);
    kept.chain = chainOf(this.node, at.box);
    return true;
  }

  // A scroll this line did not make: the reader's or another new place, or the browser's clamp. A
  // clamp first seen after a change to the content (`afterChange`) is the browser's, even within a
  // gesture; one seen only by a scroll event within a gesture is the reader's scroll to the end.
  scrolled(afterChange = false): void {
    const at = this.node.scrollTop;
    if (Math.abs(at - this.known) < 0.5) return;
    if (this.clamped(at) && (afterChange || !this.readerScrolling())) this.unclamp(at);
    else this.take(true);
  }

  // The browser's clamp, undone before it is painted: the spacer at the column's end takes the room
  // the shrink took, and the column goes back to where it was, so the line and its target stay.
  // Without a spacer, or while a selection's landing is due, the clamp only keeps the line.
  private unclamp(at: number): void {
    const spacer = this.spacer();
    if (!spacer || this.holding()) {
      this.known = at;
      return;
    }
    this.hold(spacer, this.held + this.known - at);
    this.node.scrollTop = this.known;
    this.known = this.node.scrollTop;
  }

  // The spacer gives back what the content no longer needs: the column's end stays at the scroll
  // position or below it, so the browser never clamps, and the spacer goes once the content has grown
  // back (what it gives back is below the view's bottom).
  release(): void {
    const spacer = this.held > 0 ? this.spacer() : null;
    if (!spacer) return;
    const { scrollTop, scrollHeight, clientHeight } = this.node;
    const needed = Math.max(0, Math.ceil(scrollTop - (scrollHeight - this.held - clientHeight)));
    if (needed < this.held) this.hold(spacer, needed);
  }

  private spacer(): HTMLElement | null {
    const last = this.node.lastElementChild;
    return last instanceof HTMLElement && last.hasAttribute(SPACER) ? last : null;
  }

  private hold(spacer: HTMLElement, height: number): void {
    const next = Math.max(0, Math.ceil(height));
    if (next === this.held) return;
    this.held = next;
    spacer.style.height = next > 0 ? `${next}px` : '';
  }

  // The content is shorter than at its tallest since the line was taken, and the column went up to
  // its new maximum, with the kept block still on the page.
  private clamped(at: number): boolean {
    const { scrollHeight, clientHeight } = this.node;
    return (
      this.kept !== null &&
      this.kept.element.isConnected &&
      at < this.known - 0.5 &&
      scrollHeight < this.tallest - 0.5 &&
      scrollHeight - clientHeight - at <= 1
    );
  }

  private readerScrolling(): boolean {
    return this.dragging || performance.now() <= this.gestureUntil;
  }

  gesture(): void {
    this.gestureUntil = performance.now() + GESTURE_MS;
  }

  drag(on: boolean): void {
    this.dragging = on;
  }
}

function listen(node: HTMLElement, line: ReaderLine): () => void {
  const onKey = (event: KeyboardEvent) => {
    if (isScrollKey(event)) line.gesture();
  };
  // A pointer pressed on the column itself, not on its content, is its scrollbar.
  const onPress = (event: PointerEvent) => {
    if (event.target === node) line.drag(true);
  };
  const onRelease = () => line.drag(false);
  const onGesture = () => line.gesture();
  const onScroll = () => {
    const settled = line.settleShown();
    line.scrolled();
    if (settled) line.changed();
    line.release();
  };
  const onProduct = () => line.take(true);
  const pairs: [EventTarget, string, EventListener][] = [
    [node, 'wheel', onGesture],
    [node, 'keydown', onKey as EventListener],
    [node, 'pointerdown', onPress as EventListener],
    [window, 'pointerup', onRelease],
    [window, 'pointercancel', onRelease],
    [node, 'touchmove', onGesture],
    [node, 'scroll', onScroll],
    [node, PRODUCT_SCROLL, onProduct],
  ];
  for (const [target, type, handler] of pairs) target.addEventListener(type, handler, { passive: true });
  return () => {
    for (const [target, type, handler] of pairs) target.removeEventListener(type, handler);
  };
}

export function useReaderAnchor(scroller: React.RefObject<HTMLElement | null>, holding: () => boolean): void {
  useEffect(() => {
    const node = scroller.current;
    if (!node) return undefined;
    const line = new ReaderLine(node, holding);
    line.take();
    const observer = new MutationObserver(() => line.changed());
    line.drainWith(() => observer.takeRecords().length > 0);
    observer.observe(node, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['style', 'class', 'open', 'hidden'],
    });
    const stop = listen(node, line);
    return () => {
      observer.disconnect();
      stop();
    };
  }, [scroller, holding]);
}
