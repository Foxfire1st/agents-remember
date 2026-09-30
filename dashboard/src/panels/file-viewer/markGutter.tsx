// Marks on file lines of a read-only CodeMirror pane: one gutter whose markers are React content,
// placed on the pane's own file line numbers (an excerpt keeps its file's numbering). The pane only
// places what it is given; which line a mark sits on is the caller's decision (the reviewer's per-hunk
// intent markers, MIK-R34, place them on the classification owner's side lines).
//
// Each mark renders into a host element the component owns for the life of its id, through a portal,
// so the mark stays one React tree while CodeMirror draws and drops its gutter elements as lines
// scroll in and out of view. A gutter marker wraps the host in a fresh element every time it is drawn:
// CodeMirror removes the node it drew, never the host a later draw moved elsewhere.
import { type Chunk, getChunks, uncollapseUnchanged } from '@codemirror/merge';
import { RangeSet, type Extension, type Text } from '@codemirror/state';
import { EditorView, GutterMarker, gutter } from '@codemirror/view';
import { useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

export interface PaneMark {
  id: string;
  // The editor of a side-by-side diff the mark sits in; a single-editor pane has only one.
  side: 'before' | 'after';
  // The file line the mark sits on, in the pane's own numbering.
  line: number;
  // The text the mark shows, which sizes the gutter.
  label: string;
  // The shorter text a phone-width gutter shows (sizes that gutter); the label when absent.
  compact?: string;
  node: ReactNode;
}

export interface PaneMarks {
  marks: readonly PaneMark[];
  // A mark to bring into view and focus once the pane is drawn (a return to a followed mark).
  reveal?: string | null;
  onRevealed?: () => void;
}

// The widest mark text, in characters of the gutter's font; a longer label wraps. On a phone-width
// pane the marks show their compact text in a gutter sized to it, so a side-by-side diff keeps room
// for its code (review R1 F2).
const MARK_WIDTH = 12;
const NARROW_MARK_WIDTH = 5;
// The mark's padding and border, and its gutter element's padding, beside its text.
const MARK_CHROME = '22px';
const NARROW_MARK_CHROME = '10px';
const NARROW = '@media (max-width: 40rem)';
// How many animation frames a reveal waits for CodeMirror to draw the mark's line.
const REVEAL_FRAMES = 30;
// How many frames a reveal then keeps the mark focused and in view: CodeMirror measures the wrapped
// lines around it, and a side-by-side view realigns its two editors, after the first scroll -- which
// can move the mark out of view or redraw its gutter element (review R2-F1).
export const REVEAL_HOLD_FRAMES = 45;

// A gutter redraw moves a mark between gutter elements (lines are measured, a run is expanded, the
// view scrolls), and moving or detaching a focused element blurs it. The control that held focus keeps
// it: a redraw that detaches it remembers it for the rest of that update, and the draw that attaches it
// again gives focus back once it is in the document -- unless the reader has moved focus since.
const carried = new WeakMap<HTMLElement, HTMLElement>();

function focusedWithin(host: HTMLElement): HTMLElement | null {
  const active = host.ownerDocument.activeElement;
  return active instanceof HTMLElement && active !== host && host.contains(active) ? active : null;
}

function focusLost(doc: Document): boolean {
  return doc.activeElement === null || doc.activeElement === doc.body;
}

function refocusWhenAttached(focused: HTMLElement): void {
  queueMicrotask(() => {
    if (focused.isConnected && focusLost(focused.ownerDocument))
      focused.focus({ preventScroll: true });
  });
}

// Exported for its redraw test: CodeMirror's own element reassignment cannot be driven in jsdom.
export class HostMarker extends GutterMarker {
  constructor(readonly host: HTMLElement) {
    super();
  }

  eq(other: GutterMarker): boolean {
    return other instanceof HostMarker && other.host === this.host;
  }

  toDOM(): Node {
    const focused = focusedWithin(this.host) ?? carried.get(this.host) ?? null;
    const wrap = document.createElement('div');
    wrap.className = 'cm-paneMark';
    wrap.appendChild(this.host);
    if (focused) refocusWhenAttached(focused);
    return wrap;
  }

  destroy(dom: Node): void {
    if (this.host.parentNode !== dom) return;
    const focused = focusedWithin(this.host);
    dom.removeChild(this.host);
    if (!focused) return;
    carried.set(this.host, focused);
    queueMicrotask(() => carried.delete(this.host));
  }
}

function docLine(doc: Text, line: number, first: number): number | null {
  const at = line - first + 1;
  return at >= 1 && at <= doc.lines ? at : null;
}

function markerSet(
  doc: Text,
  marks: readonly PaneMark[],
  hosts: Map<string, HTMLElement>,
  first: number,
): RangeSet<GutterMarker> {
  const ranges = [];
  for (const mark of marks) {
    const host = hosts.get(mark.id);
    const at = docLine(doc, mark.line, first);
    if (host && at !== null) ranges.push(new HostMarker(host).range(doc.line(at).from));
  }
  return RangeSet.of(ranges, true);
}

// The gutter of one editor, for the marks that sit in it. No marks, no gutter: an unmarked pane is
// exactly the pane it was.
function markGutter(
  marks: readonly PaneMark[],
  hosts: Map<string, HTMLElement>,
  first: number,
): Extension {
  if (!marks.length) return [];
  const width = Math.min(MARK_WIDTH, Math.max(...marks.map((mark) => mark.label.length)));
  const narrow = Math.min(
    NARROW_MARK_WIDTH,
    Math.max(...marks.map((mark) => (mark.compact ?? mark.label).length)),
  );
  let drawn: { doc: Text; set: RangeSet<GutterMarker> } | null = null;
  return [
    gutter({
      class: 'cm-paneMarks',
      markers: (view) => {
        if (drawn?.doc !== view.state.doc)
          drawn = { doc: view.state.doc, set: markerSet(view.state.doc, marks, hosts, first) };
        return drawn.set;
      },
    }),
    EditorView.theme({
      '.cm-paneMarks': { width: `calc(${width}ch + ${MARK_CHROME})`, fontSize: '0.68rem' },
      '.cm-paneMarks .cm-gutterElement': { padding: '0 4px 0 6px', overflow: 'visible' },
      [NARROW]: {
        '.cm-paneMarks': { width: `calc(${narrow}ch + ${NARROW_MARK_CHROME})` },
        '.cm-paneMarks .cm-gutterElement': { padding: '0 2px' },
      },
    }),
  ];
}

// Whether the mark shows inside its diff's own scroll box and the window.
function inView(host: HTMLElement): boolean {
  const box = host.getBoundingClientRect();
  const pane = (
    host.closest('.cm-mergeView') ?? host.closest('.cm-scroller')
  )?.getBoundingClientRect();
  const inPane = !pane || (box.top >= pane.top && box.bottom <= pane.bottom);
  return inPane && box.top >= 0 && box.bottom <= window.innerHeight;
}

// What a hold keeps revealed: the mark's host and its control, and how to scroll to its line again.
// Exported, with `holdRevealed`, for the hold's frame-by-frame tests.
export interface Revealed {
  view: Pick<EditorView, 'dom'>;
  host: HTMLElement;
  target: HTMLElement;
  scrollToLine: () => void;
}

// The reader's own actions that end a hold: a pointer, a key, or a wheel or trackpad scroll -- which
// fires neither of the other two (review R3-F1).
const HOLD_RELEASES = ['pointerdown', 'keydown', 'wheel'] as const;

// One frame of the hold: an undrawn mark's line is scrolled to again; a drawn one is refocused if a
// redraw left focus on the body, and brought back into view if it moved out.
function keepRevealed({ host, target, scrollToLine }: Revealed): void {
  if (!host.isConnected) {
    scrollToLine();
    return;
  }
  if (focusLost(host.ownerDocument)) target.focus({ preventScroll: true });
  if (!inView(host)) host.scrollIntoView?.({ block: 'center', inline: 'nearest' });
}

// After a reveal, for at most `REVEAL_HOLD_FRAMES` frames: focus the mark again if a redraw left it on
// the body, and bring it back into view if the measured lines or the realigned editors moved it out.
// The reader's own pointer, key or wheel ends the hold, and so do focus the reader moved elsewhere and a
// torn-down pane; the hold then stops asking for frames and removes its listeners.
export function holdRevealed(revealed: Revealed): void {
  const { view, host } = revealed;
  const doc = host.ownerDocument;
  let held = true;
  const release = () => {
    held = false;
  };
  for (const name of HOLD_RELEASES)
    doc.addEventListener(name, release, { capture: true, once: true, passive: true });
  const over = (count: number) => {
    const active = doc.activeElement;
    const movedAway = active !== null && active !== doc.body && !host.contains(active);
    return !held || movedAway || !view.dom.isConnected || count >= REVEAL_HOLD_FRAMES;
  };
  const frame = (count: number) => {
    if (over(count)) {
      for (const name of HOLD_RELEASES) doc.removeEventListener(name, release, { capture: true });
      return;
    }
    keepRevealed(revealed);
    requestAnimationFrame(() => frame(count + 1));
  };
  requestAnimationFrame(() => frame(0));
}

// Bring a mark's line into view in its editor, then focus the mark once CodeMirror has drawn it, and
// keep it focused and in view while the editor settles.
function revealMark(editor: DrawnEditor, mark: PaneMark, host: HTMLElement, done: () => void) {
  const { view, first } = editor;
  const scrollToLine = () => {
    const at = docLine(view.state.doc, mark.line, first);
    if (at !== null)
      view.dispatch({
        effects: EditorView.scrollIntoView(view.state.doc.line(at).from, { y: 'center' }),
      });
  };
  scrollToLine();
  const settle = (frame: number) => {
    if (!host.isConnected && frame < REVEAL_FRAMES) {
      requestAnimationFrame(() => settle(frame + 1));
      return;
    }
    const target = host.querySelector<HTMLElement>('button');
    target?.focus({ preventScroll: true });
    host.scrollIntoView?.({ block: 'center', inline: 'nearest' });
    if (target) holdRevealed({ view, host, target, scrollToLine });
    done();
  };
  requestAnimationFrame(() => settle(0));
}

// CodeMirror hides its whole gutter column from assistive technology. The marks are controls the
// reader focuses (and returns to), so their gutter is exposed; line numbers and the change bar stay
// hidden, as before.
function exposeMarks(view: EditorView): void {
  const gutters = view.dom.querySelector('.cm-gutters');
  if (!gutters?.querySelector(':scope > .cm-paneMarks')) return;
  gutters.removeAttribute('aria-hidden');
  for (const other of gutters.querySelectorAll(':scope > .cm-gutter:not(.cm-paneMarks)'))
    other.setAttribute('aria-hidden', 'true');
}

// "Changed regions" collapses runs of lines the diff's own chunks leave unchanged. The owner's hunks
// are not those chunks, so a marked line can fall inside such a run: the run holding a mark is expanded
// (in both editors of a side-by-side diff), and no mark is ever hidden in a collapsed run.
const COLLAPSED_RUN = 'collapsed-unchanged-code';

function collapsedRunAt(editor: DrawnEditor, line: number): number | null {
  const { view, first } = editor;
  const at = docLine(view.state.doc, line, first);
  if (at === null) return null;
  const block = view.lineBlockAt(view.state.doc.line(at).from);
  const widget = block.widget as { type?: string } | null;
  return widget?.type === COLLAPSED_RUN ? block.from : null;
}

// A position of one editor's unchanged run in the other editor (as the merge view's own control maps
// it when a reader expands a run by hand).
function siblingPos(pos: number, chunks: readonly Chunk[], isA: boolean): number {
  let [ours, other] = [0, 0];
  for (const chunk of chunks) {
    if ((isA ? chunk.fromA : chunk.fromB) >= pos) break;
    [ours, other] = isA ? [chunk.toA, chunk.toB] : [chunk.toB, chunk.toA];
  }
  return other + (pos - ours);
}

function expandMarkedRuns(editors: DrawnEditors, marks: readonly PaneMark[]): void {
  for (const mark of marks) {
    const editor = mark.side === 'before' && editors.before ? editors.before : editors.after;
    const from = collapsedRunAt(editor, mark.line);
    if (from === null) continue;
    editor.view.dispatch({ effects: uncollapseUnchanged.of(from) });
    const isA = editor === editors.before;
    const sibling = isA ? editors.after : editors.before;
    const chunks = getChunks(editor.view.state)?.chunks;
    if (sibling && chunks)
      sibling.view.dispatch({ effects: uncollapseUnchanged.of(siblingPos(from, chunks, isA)) });
  }
}

// One drawn editor of a pane, with the file line its document starts at.
export interface DrawnEditor {
  view: EditorView;
  first: number;
}

// The editors a pane drew: `before` only in a side-by-side diff.
export interface DrawnEditors {
  before?: DrawnEditor;
  after: DrawnEditor;
}

export type MarkSide = 'before' | 'after' | 'all';

export interface MarkedPane {
  // Render these beside the pane: the marks' content, portalled into their hosts.
  portals: ReactNode;
  // Changes exactly when a mark moves, so the pane is rebuilt only then.
  placement: string;
  // The gutter extension for one editor of the pane.
  gutterFor: (side: MarkSide, first: number) => Extension;
  // Called by the pane once its editors exist (and with `null` when it tears them down).
  drawn: (editors: DrawnEditors | null) => void;
}

// What a pane needs to draw marks: the hosts and their portals, the gutter of each editor, and the
// reveal of a mark once the editors are drawn.
export function useMarkedPane(marks: PaneMarks | undefined): MarkedPane {
  const list = marks?.marks;
  const ids = (list ?? []).map((mark) => mark.id).join('\n');
  const hosts = useMemo(() => new Map((ids ? ids.split('\n') : []).map(hostEntry)), [ids]);
  const latest = useRef(marks);
  const editors = useRef<DrawnEditors | null>(null);
  useEffect(() => {
    latest.current = marks;
  });
  const reveal = useCallback(() => {
    const current = latest.current;
    const mark = current?.marks.find((one) => one.id === current.reveal);
    const host = mark && hosts.get(mark.id);
    const drawn = editors.current;
    if (!current || !mark || !host || !drawn) return;
    const editor = mark.side === 'before' && drawn.before ? drawn.before : drawn.after;
    revealMark(editor, mark, host, () => current.onRevealed?.());
  }, [hosts]);
  const wanted = marks?.reveal;
  useEffect(() => {
    if (wanted) reveal();
  }, [wanted, reveal]);
  const gutterFor = useCallback(
    (side: MarkSide, first: number) =>
      markGutter(
        (latest.current?.marks ?? []).filter((mark) => side === 'all' || mark.side === side),
        hosts,
        first,
      ),
    [hosts],
  );
  const drawn = useCallback(
    (next: DrawnEditors | null) => {
      editors.current = next;
      if (!next) return;
      for (const editor of [next.before, next.after]) if (editor) exposeMarks(editor.view);
      expandMarkedRuns(next, latest.current?.marks ?? []);
      reveal();
    },
    [reveal],
  );
  const portals = (list ?? []).map((mark) => {
    const host = hosts.get(mark.id);
    return host ? createPortal(mark.node, host, mark.id) : null;
  });
  const placement = (list ?? [])
    .map((mark) => `${mark.id}@${mark.side}:${mark.line}:${mark.label}:${mark.compact ?? ''}`)
    .join('|');
  return { portals, placement, gutterFor, drawn };
}

function hostEntry(id: string): [string, HTMLElement] {
  const host = document.createElement('span');
  host.className = 'cm-paneMarkHost';
  host.dataset.markId = id;
  return [id, host];
}
