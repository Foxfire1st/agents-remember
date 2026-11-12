// The ⊕ on file lines, in the File Viewer's line gutter (MIK-R188 rule 1).
//
// - A quiet ⊕ shows beside the line number of the line under the pointer.
// - One click takes that one line as a file-line reference.
// - Shift-click extends the range from the last clicked line: the pick of the last click is replaced
//   by the range from that line to this one.
// - Lines inside a file-line pick of this file show "✓"; a click on one adds nothing.
// - By keyboard (MIK-R89 v2 rule 11) the gutter is one tab stop: a named ⊕ on one line, quiet until it
//   has focus. Enter or Space takes its line, Shift+Enter extends the range as shift-click does, and
//   the arrows, Home and End move it to another line.
//
// The reference names the repository, the File Viewer's scope, the path and the line range; the
// server resolves the checkout and its tree when it renders the written form.
import { type Extension, StateEffect, StateField } from '@codemirror/state';
import { EditorView, GutterMarker, ViewPlugin, gutter } from '@codemirror/view';
import type { StoreApi } from 'zustand/vanilla';

import { pickKey } from './pickKey';
import { fileLinePick } from './pickBuilders';
import { trayStore, type TrayState } from './trayStore';

export interface FileLineTarget {
  repository: string;
  scope: string;
  path: string;
}

// ---- the click rule ----

export interface LineAnchor {
  line: number;
  pickId: string | null;
}

// The pick the anchor's click took, while the tray still holds it.
const anchorPick = (picks: TrayState['picks'], anchor: LineAnchor): string | null =>
  anchor.pickId && picks.some((pick) => pick.id === anchor.pickId) ? anchor.pickId : null;

// Takes `line` (a file line number) into the tray, or extends the last click's range to it. Answers
// the anchor the next shift-click extends from. A line already in the tray, alone or inside a range,
// adds nothing and leaves the anchor as it was: its "✓" is not a second ⊕ (rule 1).
export function takeFileLine(
  target: FileLineTarget,
  line: number,
  shift: boolean,
  anchor: LineAnchor | null,
  store: StoreApi<TrayState> = trayStore,
): LineAnchor | null {
  const extend = shift && anchor !== null;
  if (!extend && lineInTray(store.getState().picks, target, line)) return anchor;
  const from = extend ? anchor.line : line;
  const input = fileLinePick({ ...target, start: from, end: line });
  const tray = store.getState();
  if (extend) tray.replace(anchorPick(tray.picks, anchor), input);
  else tray.add(input);
  tray.reveal();
  const key = pickKey(input);
  const taken = store.getState().picks.find((pick) => pick.key === key);
  return { line: from, pickId: taken?.id ?? null };
}

// Whether a file line lies inside a file-line pick of this file.
export function lineInTray(picks: TrayState['picks'], target: FileLineTarget, line: number): boolean {
  return picks.some((pick) => {
    if (pick.input.kind !== 'reference') return false;
    const reference = pick.input.reference;
    return (
      reference.kind === 'file-line' &&
      reference.repository === target.repository &&
      reference.scope === target.scope &&
      reference.path === target.path &&
      reference.start <= line &&
      line <= reference.end
    );
  });
}

// ---- the gutter ----

const setHovered = StateEffect.define<number | null>();
const setKeyLine = StateEffect.define<number>();
const trayChanged = StateEffect.define<null>();

const hoveredLine = StateField.define<number | null>({
  create: () => null,
  update(value, transaction) {
    for (const effect of transaction.effects) if (effect.is(setHovered)) return effect.value;
    return value;
  },
});

// The document line of the keyboard ⊕ once it had focus or moved.
const keyLine = StateField.define<number | null>({
  create: () => null,
  update(value, transaction) {
    for (const effect of transaction.effects) if (effect.is(setKeyLine)) return effect.value;
    return value;
  },
});

// Where the keyboard ⊕ is drawn: on its line, or on the drawn viewport's first line until it had
// focus and while its line lies outside the viewport, so the gutter always keeps its one tab stop.
function keyboardLine(view: EditorView): number {
  const { doc } = view.state;
  const first = doc.lineAt(view.viewport.from).number;
  const pinned = view.state.field(keyLine);
  return pinned !== null && pinned >= first && pinned <= doc.lineAt(view.viewport.to).number ? pinned : first;
}

class LineMark extends GutterMarker {
  constructor(readonly taken: boolean) {
    super();
  }
  eq(other: LineMark) {
    return other.taken === this.taken;
  }
  toDOM() {
    const mark = document.createElement('span');
    mark.className = this.taken ? 'cm-trayTaken' : 'cm-trayAdd';
    mark.textContent = this.taken ? '✓' : '⊕';
    mark.title = this.taken ? 'This line is in the AR tray' : 'Add this line to the AR tray (shift-click: the range from the last line)';
    // The keyboard ⊕ carries the name; the pointer's marks are seen only.
    mark.setAttribute('aria-hidden', 'true');
    return mark;
  }
}

const ADD = new LineMark(false);
const TAKEN = new LineMark(true);

// The keyboard ⊕ on its line (`line` in file numbers): a named button, quiet until it has focus or
// its line is under the pointer, and "✓" on a line in the tray.
class KeyMark extends GutterMarker {
  constructor(
    readonly line: number,
    readonly taken: boolean,
    readonly hovered: boolean,
  ) {
    super();
  }
  eq(other: KeyMark) {
    return other.line === this.line && other.taken === this.taken && other.hovered === this.hovered;
  }
  toDOM() {
    const mark = document.createElement('span');
    mark.className = `${this.taken ? 'cm-trayTaken' : 'cm-trayAdd'} cm-trayKey${this.taken || this.hovered ? '' : ' cm-trayQuiet'}`;
    mark.textContent = this.taken ? '✓' : '⊕';
    mark.tabIndex = 0;
    mark.setAttribute('role', 'button');
    mark.setAttribute('aria-label', this.taken ? `Line ${this.line} is in the AR tray` : `Add line ${this.line} to the AR tray`);
    if (this.taken) mark.setAttribute('aria-disabled', 'true');
    const keys = 'shift-click or Shift+Enter: the range from the last line; ↑ ↓ Home End: another line';
    mark.title = this.taken ? `This line is in the AR tray (${keys})` : `Add this line to the AR tray (Enter; ${keys})`;
    return mark;
  }
}

const isKeyMark = (target: EventTarget | null): boolean =>
  target instanceof HTMLElement && target.classList.contains('cm-trayKey');

// A change of the ⊕'s line, of the tray or of the pointer draws the keyboard ⊕ anew; the focus it had
// moves to the one drawn after `run`.
function keepFocus(view: EditorView, run: () => void): void {
  const had = isKeyMark(view.root.activeElement) && view.dom.contains(view.root.activeElement);
  run();
  if (!had) return;
  const mark = view.dom.querySelector<HTMLElement>('.cm-trayGutter .cm-trayKey');
  if (mark && mark !== view.root.activeElement) mark.focus({ preventScroll: true });
}

function moveKey(view: EditorView, to: number): void {
  const line = Math.max(1, Math.min(view.state.doc.lines, to));
  keepFocus(view, () =>
    view.dispatch({ effects: [setKeyLine.of(line), EditorView.scrollIntoView(view.state.doc.line(line).from)] }),
  );
}

// CodeMirror hides its gutters from assistive technology. The tray gutter holds a control, so their
// container is shown and each other gutter (the line numbers, the marks) stays hidden.
function exposeTrayGutter(view: EditorView): boolean {
  const gutters = view.dom.querySelector('.cm-gutters');
  if (!gutters) return false;
  gutters.removeAttribute('aria-hidden');
  for (const other of gutters.querySelectorAll(':scope > .cm-gutter:not(.cm-trayGutter)')) other.setAttribute('aria-hidden', 'true');
  return true;
}

function followPointer(store: StoreApi<TrayState>) {
  return ViewPlugin.fromClass(
    class {
      private readonly move: (event: MouseEvent) => void;
      private readonly leave: () => void;
      private readonly unsubscribe: () => void;
      private exposed: boolean;
      constructor(readonly view: EditorView) {
        this.move = (event) => {
          const block = view.lineBlockAtHeight(event.clientY - view.documentTop);
          const line = view.state.doc.lineAt(block.from).number;
          if (view.state.field(hoveredLine) !== line) keepFocus(view, () => view.dispatch({ effects: setHovered.of(line) }));
        };
        this.leave = () => {
          if (view.state.field(hoveredLine) !== null) keepFocus(view, () => view.dispatch({ effects: setHovered.of(null) }));
        };
        view.dom.addEventListener('mousemove', this.move);
        view.dom.addEventListener('mouseleave', this.leave);
        this.unsubscribe = store.subscribe((state, previous) => {
          if (state.picks !== previous.picks) keepFocus(view, () => view.dispatch({ effects: trayChanged.of(null) }));
        });
        this.exposed = exposeTrayGutter(view);
      }
      update() {
        if (!this.exposed) this.exposed = exposeTrayGutter(this.view);
      }
      destroy() {
        this.view.dom.removeEventListener('mousemove', this.move);
        this.view.dom.removeEventListener('mouseleave', this.leave);
        this.unsubscribe();
      }
    },
  );
}

const look = EditorView.theme({
  '.cm-trayGutter .cm-gutterElement': { cursor: 'pointer', padding: '0 3px', color: 'var(--cyan)' },
  '.cm-trayTaken': { color: 'var(--mint)' },
  '.cm-trayKey': { borderRadius: '2px' },
  '.cm-trayQuiet': { opacity: '0' },
  '.cm-trayKey:focus-visible': { opacity: '1', outline: '1px solid var(--amber)', outlineOffset: '1px' },
});

// The gutter for one file of the File Viewer. `firstLine` is the number of the document's first line
// in its file (1 for a whole file).
export function trayLineGutter(target: FileLineTarget, firstLine = 1, store: StoreApi<TrayState> = trayStore): Extension {
  let anchor: LineAnchor | null = null;
  const fileLine = (view: EditorView, from: number) => view.state.doc.lineAt(from).number + firstLine - 1;
  return [
    hoveredLine,
    keyLine,
    followPointer(store),
    look,
    gutter({
      class: 'cm-trayGutter',
      lineMarker(view, block) {
        const docLine = view.state.doc.lineAt(block.from).number;
        const line = docLine + firstLine - 1;
        const taken = lineInTray(store.getState().picks, target, line);
        const hovered = view.state.field(hoveredLine) === docLine;
        if (docLine === keyboardLine(view)) return new KeyMark(line, taken, hovered);
        if (taken) return TAKEN;
        return hovered ? ADD : null;
      },
      lineMarkerChange: (update) =>
        update.transactions.some((transaction) =>
          transaction.effects.some((effect) => effect.is(setHovered) || effect.is(setKeyLine) || effect.is(trayChanged)),
        ),
      initialSpacer: () => ADD,
      domEventHandlers: {
        mousedown(_view, _block, event) {
          event.preventDefault();
          return true;
        },
        click(view, block, event) {
          anchor = takeFileLine(target, fileLine(view, block.from), (event as MouseEvent).shiftKey, anchor, store);
          return true;
        },
        // The ⊕ that took focus stays on its line while the viewport moves.
        focusin(view, _block, event) {
          const line = keyboardLine(view);
          if (isKeyMark(event.target) && view.state.field(keyLine) !== line)
            keepFocus(view, () => view.dispatch({ effects: setKeyLine.of(line) }));
          return false;
        },
        keydown(view, _block, event) {
          if (!isKeyMark(event.target)) return false;
          const { key, shiftKey } = event as KeyboardEvent;
          const line = keyboardLine(view);
          const moves: Partial<Record<string, number>> = { ArrowDown: line + 1, ArrowUp: line - 1, Home: 1, End: view.state.doc.lines };
          const to = moves[key];
          if (to !== undefined) moveKey(view, to);
          else if (key === 'Enter' || key === ' ') anchor = takeFileLine(target, line + firstLine - 1, shiftKey, anchor, store);
          else return false;
          // The page's own keys do not act on a key the ⊕ took.
          event.stopPropagation();
          return true;
        },
      },
    }),
  ];
}
