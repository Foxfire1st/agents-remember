// Where the tray docks (MIK-R188 rule 5, ruling Q-D). jsdom has no layout, so the rectangles are
// given; the measured rectangles at 1920 and 1440 come from the sandbox browser runs.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { composerUnavailableText, frameTrayStore, type ComposerEdge } from '../cockpit/frameTrayChannel';
import { DOCK_GAP, TrayBottomRightDock, TrayColumnDock, dockPlacement } from './TrayDock';
import { EMPTY_TRAY } from './trayPersistence';
import { seatStore } from './traySeats';
import { trayStore } from './trayStore';
import type { TrayPickInput } from './types';

const rect = (left: number, top: number, width: number, height: number) => new DOMRect(left, top, width, height);
const FRAME = rect(1400, 100, 500, 800);
const ANCHOR = rect(1400, 900, 500, 0);
const BOX = rect(1400, 600, 500, 240);
const MEASURED: ComposerEdge = { kind: 'measured', top: 680, height: 120, frameHeight: 800 };
// A box narrower than its frame, as the Chats view draws it: Paseo's sidebar and a terminal pane
// stand beside the chat, so the box's left edge is not the frame's.
const MEASURED_EXTENT: ComposerEdge = { kind: 'measured', top: 680, height: 120, frameHeight: 800, left: 120, width: 300 };

function Column({ visible = true }: { visible?: boolean }) {
  return (
    <>
      <section>
        <div>
          <iframe title="Role chats" />
        </div>
        <TrayColumnDock visible={visible} page="document" />
      </section>
      <TrayBottomRightDock />
    </>
  );
}

const reserved = () => frameTrayStore.getState().traySpace.document ?? 0;

const pick = (id: string): TrayPickInput => ({
  kind: 'reference',
  title: `${id} · an invariant`,
  reference: { kind: 'invariant', repository: 'agents-remember', id, memoryTree: 'a'.repeat(40) },
});

beforeEach(() => {
  trayStore.setState({ ...EMPTY_TRAY, open: true, minimised: false, refusal: null, outcome: null });
  seatStore.setState({ seats: [], nextOffset: null, complete: true, status: 'ready', detail: null });
  frameTrayStore.setState({ composer: {}, pickNotice: {}, traySpace: {} });
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    if (this.tagName === 'IFRAME') return FRAME;
    if (this.getAttribute('data-dock')) return ANCHOR;
    if (this.parentElement?.getAttribute('data-dock')) return BOX;
    return rect(0, 0, 0, 0);
  });
});

afterEach(() => vi.restoreAllMocks());

describe('the tray dock', () => {
  it('places the tray over the frame, its bottom edge just above the message box', () => {
    expect(dockPlacement(ANCHOR, FRAME, MEASURED)).toEqual({ left: 0, width: 500, bottom: 900 - 780 + DOCK_GAP, maxHeight: 780 - 100 - 2 * DOCK_GAP });
    // A frame that grew shorter before the plug-in reported again keeps the box at its bottom.
    expect(dockPlacement(ANCHOR, rect(1400, 200, 500, 700), MEASURED)).toEqual({
      left: 0,
      width: 500,
      bottom: 900 - 780 + DOCK_GAP,
      maxHeight: 780 - 200 - 2 * DOCK_GAP,
    });
    expect(dockPlacement(ANCHOR, FRAME, undefined)).toBeNull();
    expect(dockPlacement(ANCHOR, rect(0, 0, 0, 0), MEASURED)).toBeNull();
  });

  it('takes the message box’s own left and width, with the frame only as their origin', () => {
    // The anchor does not share the frame's left edge, so a missing subtraction shows.
    const anchor = rect(1380, 900, 540, 0);
    expect(dockPlacement(anchor, FRAME, MEASURED_EXTENT)).toEqual({
      left: 1400 + 120 - 1380,
      width: 300,
      bottom: 900 - 780 + DOCK_GAP,
      maxHeight: 780 - 100 - 2 * DOCK_GAP,
    });
    // A box wider than nothing is taken as measured, with no minimum and no clamp: at the frame's
    // own edge the formula gives the frame's left and the box's width.
    expect(dockPlacement(ANCHOR, FRAME, { kind: 'measured', top: 680, height: 120, frameHeight: 800, left: 0, width: 500 })).toEqual({
      left: 0,
      width: 500,
      bottom: 900 - 780 + DOCK_GAP,
      maxHeight: 780 - 100 - 2 * DOCK_GAP,
    });
    // No clamp either: what the plug-in measured is what the dock takes, however wide (the box lies
    // inside the frame, so nothing needs bounding).
    expect(dockPlacement(ANCHOR, FRAME, { kind: 'measured', top: 680, height: 120, frameHeight: 800, left: 0, width: 640 })).toMatchObject({
      left: 0,
      width: 640,
    });
  });

  it('docks over the box’s own column in a layout whose frame is wider than its chat', () => {
    frameTrayStore.setState({ composer: { document: MEASURED_EXTENT } });
    render(<Column />);
    const dock = screen.getByTestId('tray-column-dock');
    expect(dock.getAttribute('data-dock')).toBe('above-composer');
    const box = dock.firstElementChild as HTMLElement;
    // The frame starts at 1400 and the anchor at 1400 too: the box's own left edge (120) shows.
    expect([box.style.left, box.style.width]).toEqual(['120px', '300px']);
  });

  it('keeps today’s frame-wide placement when the plug-in reports no extent', () => {
    frameTrayStore.setState({ composer: { document: MEASURED } });
    render(<Column />);
    const box = screen.getByTestId('tray-column-dock').firstElementChild as HTMLElement;
    expect([box.style.left, box.style.width]).toEqual(['0px', '500px']);
  });

  it('docks in the chat column above the composer and reserves the tray’s height there', () => {
    frameTrayStore.setState({ composer: { document: MEASURED } });
    render(<Column />);
    const dock = screen.getByTestId('tray-column-dock');
    expect(dock.getAttribute('data-dock')).toBe('above-composer');
    const box = dock.firstElementChild as HTMLElement;
    expect([box.style.left, box.style.width, box.style.bottom]).toEqual(['0px', '500px', `${900 - 780 + DOCK_GAP}px`]);
    expect(reserved()).toBe(240 + DOCK_GAP);
    expect(screen.queryByTestId('tray-corner-dock')).toBeNull();
    // Closed with nothing in it, the dock is gone and the space is given back.
    act(() => trayStore.getState().setOpen(false));
    expect(screen.queryByTestId('tray-column-dock')).toBeNull();
    expect(reserved()).toBe(0);
  });

  it('sits below the frame with one named notice when the message box cannot be read', () => {
    frameTrayStore.setState({ composer: { document: { kind: 'unavailable', reason: 'message-box-not-found' } } });
    render(<Column />);
    expect(screen.getByTestId('tray-column-dock').getAttribute('data-dock')).toBe('below-frame');
    expect(screen.getAllByTestId('tray-dock-notice').map((notice) => notice.textContent)).toEqual([
      composerUnavailableText('message-box-not-found'),
    ]);
    // Below the frame, the frame itself gives up the height: nothing is reserved inside it.
    expect(reserved()).toBe(0);
  });

  it('shows a new pick as one line "⊕ Attached · n", with no list, target, notes, message, Send, preferences or log', () => {
    act(() => trayStore.setState({ ...EMPTY_TRAY, open: false }));
    frameTrayStore.setState({ composer: { document: MEASURED } });
    render(<Column />);
    act(() => void trayStore.getState().take(pick('INV-1')));
    const line = screen.getByTestId('tray-line');
    expect(screen.getByTestId('tray-column-dock').contains(line)).toBe(true);
    expect(screen.getByRole('button', { name: 'Expand the AR tray, 1 pick' }).textContent).toBe('⊕ Attached · 1');
    expect(screen.queryByTestId('tray-panel')).toBeNull();
    expect(line.querySelectorAll('ul, textarea, input, [data-testid="tray-target-menu"]')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /^Send|Bounds|Sent log|^Send to:/ })).toBeNull();
    act(() => void trayStore.getState().take(pick('INV-2')));
    expect(screen.getByTestId('tray-line').textContent).toContain('⊕ Attached · 2');
  });

  it('expands on a click on its line and minimises again, and a new pick keeps either state', () => {
    act(() => trayStore.setState({ ...EMPTY_TRAY, open: false }));
    render(<Column visible={false} />);
    act(() => void trayStore.getState().take(pick('INV-1')));
    fireEvent.click(screen.getByRole('button', { name: 'Expand the AR tray, 1 pick' }));
    expect(screen.getByTestId('tray-panel')).toBeTruthy();
    act(() => void trayStore.getState().take(pick('INV-2')));
    expect(screen.getByTestId('tray-panel')).toBeTruthy();
    expect(screen.getAllByTestId('tray-pick')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Minimise the AR tray' }));
    expect(screen.queryByTestId('tray-panel')).toBeNull();
    act(() => void trayStore.getState().take(pick('INV-3')));
    expect(screen.queryByTestId('tray-panel')).toBeNull();
    expect(screen.getByTestId('tray-line').textContent).toContain('⊕ Attached · 3');
    // Closing stays as it was: the pill at the edge.
    fireEvent.click(screen.getByRole('button', { name: 'Close the AR tray; its picks stay' }));
    expect(screen.getByTestId('tray-pill').textContent).toBe('⊕ 3 in the AR tray · open');
  });

  // MIK-R197: clicking the minimised line itself, above the chat box, expands the tray; the line's
  // own controls keep their actions.
  it('expands when the docked line itself is clicked, away from its button', () => {
    act(() => trayStore.setState({ ...EMPTY_TRAY, open: false }));
    frameTrayStore.setState({ composer: { document: MEASURED } });
    render(<Column />);
    act(() => void trayStore.getState().take(pick('INV-1')));
    const dock = screen.getByTestId('tray-column-dock');
    expect(dock.getAttribute('data-dock')).toBe('above-composer');
    expect(screen.queryByTestId('tray-panel')).toBeNull();
    // A real click on the bar lands on the line's head or on its empty spacer, never on the section
    // itself; jsdom has no hit testing, so the test dispatches where the pointer lands.
    const head = screen.getByTestId('tray-line-head');
    expect(head.contains(screen.getByRole('button', { name: 'Expand the AR tray, 1 pick' }))).toBe(true);
    fireEvent.click(head);
    expect(screen.getByTestId('tray-panel')).toBeTruthy();
  });

  it('keeps the close control on the docked line closing the tray, and does not expand on it', () => {
    act(() => trayStore.setState({ ...EMPTY_TRAY, open: false }));
    frameTrayStore.setState({ composer: { document: MEASURED } });
    render(<Column />);
    act(() => void trayStore.getState().take(pick('INV-1')));
    fireEvent.click(screen.getByRole('button', { name: 'Close the AR tray; its picks stay' }));
    expect(screen.queryByTestId('tray-panel')).toBeNull();
    expect(screen.getByTestId('tray-pill').textContent).toBe('⊕ 1 in the AR tray · open');
  });

  it('keeps a refusal by name on the line, and the answer of a Send it returns to', () => {
    act(() => trayStore.setState({ ...EMPTY_TRAY, open: false }));
    render(<Column visible={false} />);
    act(() => {
      for (let n = 1; n <= 21; n += 1) trayStore.getState().take(pick(`INV-${n}`));
    });
    expect(screen.getByTestId('tray-line').textContent).toContain('⊕ Attached · 20');
    expect(screen.getByRole('alert').textContent).toContain('“INV-21 · an invariant” is not added: the tray holds at most 20 picks per message');
    act(() => trayStore.getState().setOpen(true));
    const delivered = { state: 'delivered' as const, messageId: 'm', messageFile: '/x.md', messageFileSha256: 'c'.repeat(64) };
    act(() => {
      trayStore.getState().setSending(true);
      trayStore.getState().settle({ at: 'now', results: [{ agentId: 'a-1', delivery: delivered }], pickIds: trayStore.getState().picks.map((p) => p.id), text: '' });
    });
    expect(screen.queryByTestId('tray-panel')).toBeNull();
    expect(screen.getByTestId('tray-line').textContent).toContain('⊕ Attached · 0');
    expect(screen.getByRole('status').textContent).toBe('Sent: delivered to agent a-1.');
    // After any Send the tray returns to its line (15f92a8f); a refused target's picks stay, and the
    // line says it refused.
    act(() => {
      trayStore.getState().add(pick('INV-1'));
      trayStore.getState().setOpen(true);
      trayStore.getState().settle({ at: 'later', results: [{ agentId: 'a-1', delivery: { ...delivered, state: 'refused', reason: 'x' } }], pickIds: [], text: '' });
    });
    expect(screen.queryByTestId('tray-panel')).toBeNull();
    expect(screen.getByTestId('tray-line').textContent).toContain('⊕ Attached · 1');
    expect(screen.getByRole('status').textContent).toBe('Sent: refused by agent a-1.');
    expect(trayStore.getState().targets).toEqual(['a-1']);
  });

  it('reserves the height of the tray as it is drawn, the line or the full view', () => {
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      if (this.tagName === 'IFRAME') return FRAME;
      if (this.getAttribute('data-dock')) return ANCHOR;
      if (this.parentElement?.getAttribute('data-dock')) return this.querySelector('[data-testid="tray-line"]') ? rect(1400, 740, 500, 30) : BOX;
      return rect(0, 0, 0, 0);
    });
    act(() => trayStore.setState({ ...EMPTY_TRAY, open: false }));
    frameTrayStore.setState({ composer: { document: MEASURED } });
    render(<Column />);
    act(() => void trayStore.getState().take(pick('INV-1')));
    expect(reserved()).toBe(30 + DOCK_GAP);
    fireEvent.click(screen.getByRole('button', { name: 'Expand the AR tray, 1 pick' }));
    expect(reserved()).toBe(240 + DOCK_GAP);
    fireEvent.click(screen.getByRole('button', { name: 'Minimise the AR tray' }));
    expect(reserved()).toBe(30 + DOCK_GAP);
  });

  it('bounds the full view to the frame above the message box: its list scrolls, the message field and Send stay', () => {
    frameTrayStore.setState({ composer: { document: MEASURED } });
    act(() => {
      for (let n = 1; n <= 12; n += 1) trayStore.getState().add(pick(`INV-${n}`));
    });
    render(<Column />);
    const box = screen.getByTestId('tray-column-dock').firstElementChild as HTMLElement;
    // The frame's top (100) to the message box's top edge (780), less a gap at each end.
    expect(box.style.maxHeight).toBe(`${780 - 100 - 2 * DOCK_GAP}px`);
    const panel = screen.getByTestId('tray-panel');
    const list = screen.getByRole('list', { name: 'Picks in the tray' });
    expect(list.querySelectorAll(':scope > li')).toHaveLength(12);
    // jsdom draws no layout: the classes carry the rules (the list shrinks and scrolls, contained;
    // the panel may shrink in its box), and the browser leg measures the rectangles.
    expect(list.className.split(' ')).toEqual(expect.arrayContaining(['max-h_38vh', 'min-h_0', 'ov-y_auto', 'ovs-b_contain']));
    expect(panel.className.split(' ')).toEqual(expect.arrayContaining(['min-h_0']));
    const field = screen.getByRole('textbox', { name: 'Your message with these picks' });
    const send = screen.getByRole('button', { name: 'Send' });
    expect([list.compareDocumentPosition(field), list.compareDocumentPosition(send)]).toEqual([
      Node.DOCUMENT_POSITION_FOLLOWING,
      Node.DOCUMENT_POSITION_FOLLOWING,
    ]);
    expect(panel.contains(list) && !list.contains(field)).toBe(true);
  });

  it('docks at the bottom right where no chat column is shown', () => {
    frameTrayStore.setState({ composer: { document: MEASURED } });
    render(<Column visible={false} />);
    expect(screen.queryByTestId('tray-column-dock')).toBeNull();
    expect(screen.getByTestId('tray-corner-dock')).toBeTruthy();
    expect(screen.getByTestId('tray-panel')).toBeTruthy();
  });
});
