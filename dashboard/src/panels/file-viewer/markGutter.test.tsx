// MIK-R34 (the rulings round, Q4): in "changed regions" mode a diff collapses runs of lines its own
// chunks leave unchanged, and the owner's hunks are not those chunks -- so a marked line can fall
// inside such a run. The run holding a mark is expanded (in both editors side by side); a run holding
// none stays collapsed as before. The real DiffPane and CodeMirror render in jsdom.
import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DiffPane } from '../changeset/DiffPane';
import {
  HostMarker,
  type PaneMark,
  REVEAL_HOLD_FRAMES,
  type Revealed,
  holdRevealed,
} from './markGutter';

afterEach(cleanup);

// Sixty lines, line 2 edited: the diff's own chunk is line 2; lines 6-60 collapse (margin 3).
const BEFORE = Array.from({ length: 60 }, (_, index) => `line ${index + 1}`).join('\n') + '\n';
const AFTER = BEFORE.replace('line 2\n', 'line two\n');

const mark = (side: 'before' | 'after', line: number): PaneMark => ({
  id: `${side}-${line}`,
  side,
  line,
  label: 'mark',
  node: (
    <button type="button" data-testid="probe-mark">
      mark
    </button>
  ),
});

function pane(mode: 'split' | 'inline', marks: PaneMark[]) {
  return render(
    <DiffPane
      before={BEFORE}
      after={AFTER}
      language="text"
      mode={mode}
      collapse
      marks={{ marks }}
    />,
  );
}

async function drawn(view: ReturnType<typeof render>) {
  await waitFor(() => expect(view.container.querySelector('.cm-editor')).toBeTruthy());
  await waitFor(() => expect(view.container.querySelector('.cm-line')).toBeTruthy());
}

const collapsedRuns = (view: ReturnType<typeof render>) =>
  view.container.querySelectorAll('.cm-collapsedLines').length;

describe('a marked line inside a collapsed run of unchanged lines', () => {
  it('expands the run in both editors of a side-by-side diff', async () => {
    const view = pane('split', [mark('after', 20)]);
    await drawn(view);
    await waitFor(() => expect(view.queryByTestId('probe-mark')).toBeTruthy());
    expect(collapsedRuns(view)).toBe(0);
  });

  it('expands the run for a mark in the before editor too', async () => {
    const view = pane('split', [mark('before', 30)]);
    await drawn(view);
    await waitFor(() => expect(view.queryByTestId('probe-mark')).toBeTruthy());
    expect(collapsedRuns(view)).toBe(0);
  });

  it('expands the run inline', async () => {
    const view = pane('inline', [mark('after', 20)]);
    await drawn(view);
    await waitFor(() => expect(view.queryByTestId('probe-mark')).toBeTruthy());
    expect(collapsedRuns(view)).toBe(0);
  });

  it('leaves a run that holds no mark collapsed, as before', async () => {
    // The mark sits on the diff's own changed line: nothing is hidden, nothing is expanded.
    const split = pane('split', [mark('after', 2)]);
    await drawn(split);
    await waitFor(() => expect(split.queryByTestId('probe-mark')).toBeTruthy());
    expect(collapsedRuns(split)).toBe(2);
    cleanup();
    const inline = pane('inline', []);
    await drawn(inline);
    expect(collapsedRuns(inline)).toBe(1);
  });
});

// Review R2-F1: when CodeMirror redraws a gutter -- lines are measured, the view scrolls, another mark
// enters or leaves the drawn range above -- a mark moves to another gutter element, and moving or
// detaching a focused element blurs it. CodeMirror does one of two things, depending on which element
// it updates first: it draws the mark in its new element (moving the host) before dropping the old one,
// or drops the old one (detaching the host) before drawing the new. Both are done here exactly as its
// gutter does them: `toDOM` for the new element, `destroy` plus removal for the old.
describe('a focused mark redrawn in another gutter element', () => {
  function drawnMark() {
    const host = document.createElement('span');
    const button = document.createElement('button');
    host.appendChild(button);
    const marker = new HostMarker(host);
    const gutter = document.createElement('div');
    document.body.appendChild(gutter);
    const first = marker.toDOM() as HTMLElement;
    gutter.appendChild(first);
    button.focus();
    expect(document.activeElement).toBe(button);
    return { marker, gutter, first, button };
  }
  const settled = () => new Promise((resolve) => queueMicrotask(() => resolve(undefined)));

  it('keeps its focus when the new element is drawn first', async () => {
    const { marker, gutter, first, button } = drawnMark();
    gutter.appendChild(marker.toDOM());
    marker.destroy(first);
    first.remove();
    await settled();
    expect(document.activeElement).toBe(button);
    gutter.remove();
  });

  it('keeps its focus when the old element is dropped first', async () => {
    const { marker, gutter, first, button } = drawnMark();
    marker.destroy(first);
    first.remove();
    expect(document.activeElement).not.toBe(button);
    gutter.appendChild(marker.toDOM());
    await settled();
    expect(document.activeElement).toBe(button);
    gutter.remove();
  });

  it('never takes focus back from where the reader moved it, nor redraws later', async () => {
    const { marker, gutter, first, button } = drawnMark();
    const elsewhere = document.createElement('button');
    document.body.appendChild(elsewhere);
    marker.destroy(first);
    first.remove();
    elsewhere.focus();
    gutter.appendChild(marker.toDOM());
    await settled();
    expect(document.activeElement).toBe(elsewhere);
    // A mark dropped in one update and drawn again in a later one (scrolled out, then back) does not
    // take focus: its line left the view with it.
    const again = drawnMark();
    again.marker.destroy(again.first);
    again.first.remove();
    await settled();
    again.gutter.appendChild(again.marker.toDOM());
    await settled();
    expect(document.activeElement).not.toBe(again.button);
    expect(document.activeElement).not.toBe(button);
    for (const node of [gutter, elsewhere, again.gutter]) node.remove();
  });
});

// Review R3-F2: the hold after a return (`holdRevealed`), frame by frame. `requestAnimationFrame` is a
// queue the test runs by hand, so every end condition is asserted as "no further frame asked for, no
// further focus or scroll made, and every listener it added removed".
describe('the hold after a return', () => {
  // Spelled out, not read from the module: each release is pinned on its own.
  const RELEASES = ['pointerdown', 'keydown', 'wheel'];
  let frames: FrameRequestCallback[] = [];
  const added: string[] = [];
  const removed: string[] = [];

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    document.body.replaceChildren();
    frames = [];
    added.length = 0;
    removed.length = 0;
  });

  function held() {
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    const addListener = document.addEventListener.bind(document);
    const removeListener = document.removeEventListener.bind(document);
    vi.spyOn(document, 'addEventListener').mockImplementation((type, listener, options) => {
      added.push(type);
      addListener(type, listener, options);
    });
    vi.spyOn(document, 'removeEventListener').mockImplementation((type, listener, options) => {
      removed.push(type);
      removeListener(type, listener, options);
    });
    const dom = document.createElement('div');
    const host = document.createElement('span');
    const target = document.createElement('button');
    host.appendChild(target);
    dom.appendChild(host);
    document.body.appendChild(dom);
    host.scrollIntoView = vi.fn();
    const scrollToLine = vi.fn();
    const revealed: Revealed = { view: { dom }, host, target, scrollToLine };
    target.focus();
    holdRevealed(revealed);
    return { dom, host, target, scrollToLine, focus: vi.spyOn(target, 'focus') };
  }

  // One frame: run what the hold asked for, and report whether it asked for another.
  function frame(): boolean {
    const next = frames.shift();
    next?.(performance.now());
    return frames.length > 0;
  }

  // The mark drifted: a redraw left focus on the body, and the realigned editors moved it out of view.
  function drift(host: HTMLElement, target: HTMLElement) {
    target.blur();
    vi.spyOn(host, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, -200, 40, 20));
  }

  const ended = () =>
    expect([frames.length, [...removed].sort()]).toEqual([0, [...RELEASES].sort()]);

  it('pulls a drifted mark back into view and focus while it holds', () => {
    const { host, target, focus } = held();
    drift(host, target);
    expect(frame()).toBe(true);
    expect(focus).toHaveBeenCalledTimes(1);
    expect(host.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(added.sort()).toEqual([...RELEASES].sort());
  });

  it("scrolls to an undrawn mark's line again while it holds", () => {
    const { host, scrollToLine } = held();
    host.remove();
    expect(frame()).toBe(true);
    expect(scrollToLine).toHaveBeenCalledTimes(1);
  });

  it.each(RELEASES)("ends at the reader's %s, before any further pull-back", (release) => {
    const { host, target, focus } = held();
    document.body.dispatchEvent(new Event(release, { bubbles: true }));
    drift(host, target);
    expect(frame()).toBe(false);
    expect(focus).not.toHaveBeenCalled();
    expect(host.scrollIntoView).not.toHaveBeenCalled();
    ended();
  });

  it('ends when the reader moved focus elsewhere', () => {
    const { host, target, focus } = held();
    const elsewhere = document.createElement('button');
    document.body.appendChild(elsewhere);
    elsewhere.focus();
    vi.spyOn(host, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, -200, 40, 20));
    expect(frame()).toBe(false);
    expect(document.activeElement).toBe(elsewhere);
    expect([focus.mock.calls.length, vi.mocked(host.scrollIntoView).mock.calls.length]).toEqual([
      0, 0,
    ]);
    expect(target.isConnected).toBe(true);
    ended();
  });

  it('ends when its pane is torn down', () => {
    const { dom, host, target, scrollToLine, focus } = held();
    dom.remove();
    drift(host, target);
    expect(frame()).toBe(false);
    expect([scrollToLine.mock.calls.length, focus.mock.calls.length]).toEqual([0, 0]);
    ended();
  });

  it('ends at its frame cap on a return nobody touches', () => {
    held();
    let run = 0;
    while (frames.length && run < 10 * REVEAL_HOLD_FRAMES) {
      frame();
      run += 1;
    }
    // Frames 0 .. cap-1 hold; the frame at the cap ends it: no frame loop is left running.
    expect(run).toBe(REVEAL_HOLD_FRAMES + 1);
    ended();
  });
});
