// The tray as the developer uses it (MIK-R188 rules 3 and 5–10, evidence 1): the "Tray (n)" button,
// a pasted screenshot, notes and quick intents, remove, a chat passage with its provenance, the
// default target and Send's answers.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { frameTrayStore, receiveFrameTray } from '../cockpit/frameTrayChannel';
import { takeChatPick } from './chatPicks';
import { setShownChat, setShownPage } from './shownChat';
import { TrayBottomRightDock } from './TrayDock';
import { TrayButton } from './TrayButton';
import { quickPlacement } from './TrayChip';
import { TrayLayer } from './TrayLayer';
import { EMPTY_TRAY } from './trayPersistence';
import { BOUNDS_KEY, DEFAULT_BOUNDS, trayPreferences } from './trayPreferences';
import { seatLabel, seatStore } from './traySeats';
import { trayStore } from './trayStore';
import type { TrayPickInput, TraySeat } from './types';

const manager: TraySeat = {
  agentId: 'b1d2c3e4-0000-4000-8000-000000000001',
  role: 'manager',
  task: { repository: 'agents-remember', path: 'm/master.json', id: '260928-MIK', title: 'Maintained invariant knowledge' },
  createdAt: '2026-10-19T08:00:00Z',
};
const worker: TraySeat = {
  agentId: 'b1d2c3e4-0000-4000-8000-000000000002',
  role: 'worker',
  task: { repository: 'agents-remember', path: 'm/118.json', id: 'L118', title: 'A leaf' },
  createdAt: '2026-10-19T09:00:00Z',
};
const architect: TraySeat = {
  agentId: 'b1d2c3e4-0000-4000-8000-000000000003',
  role: 'architect',
  task: null,
  createdAt: '2026-10-19T10:00:00Z',
};
let liveSeats: TraySeat[] = [manager, worker];

const invariant: TrayPickInput = {
  kind: 'reference',
  title: 'INV-1ZKP4R · A silent role-agent turn end',
  reference: { kind: 'invariant', repository: 'agents-remember', id: 'INV-1ZKP4R', memoryTree: 'a'.repeat(40) },
};

// A row of the target list by its whole identity, the role and task with the agent tag beside them.
const identity = (text: string) => (_: string, element: Element | null) =>
  element?.hasAttribute('data-target-identity') === true && element.textContent === text;

function jsonResponse(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

const server = vi.fn(async (url: string, init?: RequestInit) => {
  if (url.startsWith('/api/role-chats?')) return jsonResponse({ state: 'seats', seats: liveSeats, nextOffset: null, complete: true });
  if (url.endsWith('/sent?limit=20')) return jsonResponse({ entries: [] });
  const body = JSON.parse(String(init?.body)) as { messageId: string };
  const held = url.includes(worker.agentId);
  return jsonResponse({
    state: held ? 'held' : 'delivered',
    messageId: body.messageId,
    messageFile: '/coord/notes/reports/developer-messages/agents-remember/x.md',
    messageFileSha256: 'c'.repeat(64),
    ...(held ? { reason: 'recipient-in-turn' } : {}),
  });
});

function Shell() {
  return (
    <>
      <TrayButton />
      <TrayBottomRightDock />
    </>
  );
}

beforeEach(() => {
  localStorage.clear();
  trayStore.setState({ ...EMPTY_TRAY, open: false, minimised: false, refusal: null, outcome: null, sending: false, persistence: { state: 'kept' } });
  seatStore.setState({ seats: [], nextOffset: null, complete: true, status: 'idle', detail: null });
  trayPreferences.setState({ bounds: DEFAULT_BOUNDS });
  setShownChat(null);
  setShownPage(null, null);
  liveSeats = [manager, worker];
  vi.stubGlobal('fetch', server);
});

afterEach(() => {
  server.mockClear();
  vi.unstubAllGlobals();
});

describe('the tray', () => {
  it('opens empty from "Tray (0)", with Send disabled until there is something and somewhere to send', async () => {
    render(<Shell />);
    expect(screen.queryByTestId('tray-panel')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Open the AR tray, 0 picks' }));
    expect(screen.getByTestId('tray-button').textContent).toBe('Tray (0)');
    expect(screen.getByTestId('tray-empty').textContent).toContain('Paste a screenshot here');
    expect(screen.getByTestId('tray-preview').textContent).toBe('Nothing to send yet: add a pick or write your text.');
    expect(screen.getByRole('button', { name: 'Send' }).hasAttribute('disabled')).toBe(true);
    await waitFor(() => expect(seatStore.getState().status).toBe('ready'));
    // Closing keeps the picks; closed with picks, the pill shows.
    act(() => void trayStore.getState().add(invariant));
    fireEvent.click(screen.getByRole('button', { name: 'Close the AR tray; its picks stay' }));
    expect(screen.getByTestId('tray-pill').textContent).toBe('⊕ 1 in the AR tray · open');
    fireEvent.click(screen.getByTestId('tray-pill'));
    expect(screen.getAllByTestId('tray-pick')).toHaveLength(1);
  });

  it('takes a pasted PNG as an image pick while it is open', async () => {
    render(<Shell />);
    fireEvent.click(screen.getByTestId('tray-button'));
    const png = new File([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])], 'shot.png', { type: 'image/png' });
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(paste, 'clipboardData', { value: { files: [png] } });
    act(() => void document.dispatchEvent(paste));
    expect(paste.defaultPrevented).toBe(true);
    const chip = await screen.findByTestId('tray-pick');
    expect(chip.getAttribute('data-pick-kind')).toBe('image');
    expect(chip.textContent).toContain('Screenshot');
    expect(chip.textContent).toContain('image · 8 bytes');
    // jsdom offers no IndexedDB: the image lives in memory, and the tray says so.
    expect(screen.getByTestId('tray-memory-only').textContent).toContain('will not survive a reload');
  });

  it('refuses a pasted image of a type the server does not take, by name, before it enters', async () => {
    render(<Shell />);
    fireEvent.click(screen.getByTestId('tray-button'));
    const bmp = new File([new Uint8Array([66, 77, 1, 2])], 'shot.bmp', { type: 'image/bmp' });
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(paste, 'clipboardData', { value: { files: [bmp] } });
    act(() => void document.dispatchEvent(paste));
    expect(paste.defaultPrevented).toBe(true);
    const refusal = await screen.findByTestId('tray-refusal');
    expect(refusal.textContent).toMatch(
      /^“Screenshot · pasted [^”]+” is refused: its type image\/bmp is not one the tray sends \(PNG, JPEG, GIF or WebP\)\./,
    );
    expect(trayStore.getState().picks).toEqual([]);
    expect(screen.getByTestId('tray-empty')).toBeTruthy();
  });

  it('adds, edits and removes a note, sets it by a quick intent, and removes the pick', () => {
    act(() => void trayStore.getState().add(invariant));
    act(() => trayStore.getState().setOpen(true));
    render(<Shell />);
    fireEvent.click(screen.getByRole('button', { name: `Add a note to ${invariant.title}` }));
    const field = screen.getByRole('textbox', { name: `Note on ${invariant.title}` });
    fireEvent.change(field, { target: { value: 'Is this cap the same?' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(screen.getByRole('button', { name: `Edit the note on ${invariant.title}` }).textContent).toBe('✎ Is this cap the same?');
    fireEvent.click(screen.getByRole('button', { name: `Edit the note on ${invariant.title}` }));
    const again = screen.getByRole('textbox', { name: `Note on ${invariant.title}` });
    fireEvent.change(again, { target: { value: '' } });
    fireEvent.blur(again);
    expect(screen.getByRole('button', { name: `Add a note to ${invariant.title}` })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: `Set the note on ${invariant.title} to “challenge this”` }));
    expect(trayStore.getState().picks[0].note).toBe('challenge this');
    expect(screen.getByTestId('tray-preview').textContent).toBe("One message: 1 reference the agent opens with AR's tools, 1 note.");
    fireEvent.click(screen.getByRole('button', { name: `Remove ${invariant.title} from the tray` }));
    expect(screen.queryByTestId('tray-pick')).toBeNull();
  });

  it("places a chip's quick intents in a row just outside it: below, or above where the tray has no room below or the chip below is in use", () => {
    const chip = { left: 1431, top: 224, right: 1884, bottom: 266 };
    const bar = { width: 290, height: 19 };
    const tray = { top: 120, bottom: 600, width: 1920 };
    const free = { above: false, below: false };
    // Its top edge on the chip's bottom border: the pointer moves onto it without leaving the chip.
    expect(quickPlacement(chip, bar, tray, free)).toEqual({ left: 1431, top: 265, side: 'below' });
    // No room below in the tray: its bottom edge on the chip's top border.
    expect(quickPlacement(chip, bar, { ...tray, bottom: 280 }, free)).toEqual({ left: 1431, top: 206, side: 'above' });
    // The chip below is hovered or has the keyboard's focus: the row goes above, off its id.
    expect(quickPlacement(chip, bar, tray, { above: false, below: true })).toEqual({ left: 1431, top: 206, side: 'above' });
    // The chip above is in use: the row stays below, even with no room there in the tray.
    expect(quickPlacement(chip, bar, { ...tray, bottom: 280 }, { above: true, below: false })).toEqual({ left: 1431, top: 265, side: 'below' });
    // The rail at its 220 px minimum: the chip is narrower than the row, at the window's right edge.
    // The row stays one line, its right end 8 px inside the window.
    expect(quickPlacement({ left: 1738, top: 224, right: 1912, bottom: 266 }, bar, tray, free)).toEqual({ left: 1622, top: 265, side: 'below' });
    // Either way the row never reaches into the chip past its border: nothing of the chip is covered.
    for (const taken of [free, { above: false, below: true }]) {
      const { top } = quickPlacement(chip, bar, tray, taken);
      expect(top >= chip.bottom - 1 || top + bar.height <= chip.top + 1).toBe(true);
    }
  });

  it("shows a chip's quick intents outside it, off the id, the × and the focused control of each chip in use, and moves no chip", async () => {
    const task = (id: string): TrayPickInput => ({
      kind: 'reference',
      title: `${id} · A leaf`,
      reference: { kind: 'task', reference: { repository: 'agents-remember', path: `m/${id}.json` }, id },
    });
    act(() => {
      for (const id of ['L1', 'L2', 'L3']) void trayStore.getState().add(task(id));
      trayStore.getState().setOpen(true);
    });
    // The geometry jsdom does not lay out: chips 42 px tall, 6 px apart, in a tray inside the window.
    const view = { innerWidth: window.innerWidth, innerHeight: window.innerHeight };
    const box = (left: number, top: number, width: number, height: number) =>
      ({ left, top, right: left + width, bottom: top + height, x: left, y: top, width, height, toJSON: () => ({}) }) as DOMRect;
    const boxes = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      if (this.hasAttribute('data-quick')) return box(0, 0, 290, 19);
      if (this.hasAttribute('data-tray-root')) return box(1420, 150, 470, 550);
      const at = Array.from(this.parentElement?.children ?? []).indexOf(this);
      if (this.getAttribute('data-testid') === 'tray-pick') return box(1431, 200 + at * 48, 453, 42);
      return box(0, 0, 0, 0);
    });
    try {
      Object.assign(window, { innerWidth: 1920, innerHeight: 1080 });
      render(<Shell />);
      await waitFor(() => expect(seatStore.getState().status).toBe('ready'));
      const chips = screen.getAllByTestId('tray-pick');
      const bar = (chip: HTMLElement) => chip.querySelector('[data-quick]') as HTMLElement;
      const placed = (chip: HTMLElement) => [bar(chip).style.left, bar(chip).style.top];
      const looks = () => chips.map((chip) => [chip, ...chip.querySelectorAll('div')].map((part) => part.className));
      const atRest = looks();
      // Placed in the window, out of the chip's flow: the chip is as tall as its own lines.
      expect(bar(chips[0]).className.split(' ')).toEqual(expect.arrayContaining(['pos_fixed']));
      // The second chip hovered: its row lies below it, on its bottom border (290 - 1).
      fireEvent.pointerEnter(chips[1]);
      expect(placed(chips[1])).toEqual(['1431px', '289px']);
      // The keyboard's focus on the third chip's "+ note" while the second is hovered: the second
      // chip's row goes above it, off the third chip's id; the third chip's row lies below it.
      act(() => within(chips[2]).getByRole('button', { name: 'Add a note to L3 · A leaf' }).focus());
      expect(placed(chips[1])).toEqual(['1431px', '230px']);
      expect(placed(chips[2])).toEqual(['1431px', '337px']);
      // The pointer leaves: the focused chip keeps its row below it.
      fireEvent.pointerLeave(chips[1]);
      expect(placed(chips[2])).toEqual(['1431px', '337px']);
      // Showing the rows changed no chip's box, and each row stays in the keyboard's order: the
      // note, the three intents, Remove.
      expect(looks()).toEqual(atRest);
      expect(Array.from(chips[2].querySelectorAll('button'), (button) => [button.getAttribute('aria-label'), button.tabIndex])).toEqual([
        ['Add a note to L3 · A leaf', 0],
        ['Set the note on L3 · A leaf to “explain”', 0],
        ['Set the note on L3 · A leaf to “challenge this”', 0],
        ['Set the note on L3 · A leaf to “is this a bug?”', 0],
        ['Remove L3 · A leaf from the tray', 0],
      ]);
      // At rest a row is not drawn and catches no pointer; hover and the keyboard's focus show it.
      const rules = chips[0].className.split(' ').filter((token) => token.includes('[data-quick]'));
      expect(rules).toEqual(expect.arrayContaining(['[&_[data-quick]]:op_0', '[&_[data-quick]]:pointer-events_none']));
      expect(rules.every((rule) => /\]:(op|pointer-events)_/.test(rule))).toBe(true);
    } finally {
      boxes.mockRestore();
      Object.assign(window, view);
    }
  });

  it('sets the bounds beside copy on highlight: the defaults, a lower bound that refuses by name, and back to the defaults', () => {
    const task = (id: string): TrayPickInput => ({
      kind: 'reference',
      title: `${id} · A leaf`,
      reference: { kind: 'task', reference: { repository: 'agents-remember', path: `m/${id}.json` }, id },
    });
    act(() => trayStore.getState().setOpen(true));
    render(<Shell />);
    fireEvent.click(screen.getByRole('button', { name: 'Bounds ▸' }));
    const field = (name: string) => screen.getByRole('spinbutton', { name }) as HTMLInputElement;
    const names = ['Picks per message', 'MiB per image', 'MiB per message'];
    expect(names.map((name) => [field(name).value, field(name).getAttribute('placeholder')])).toEqual([
      ['20', '20'],
      ['5', '5'],
      ['20', '20'],
    ]);
    expect(screen.queryByRole('button', { name: 'Back to defaults' })).toBeNull();
    fireEvent.change(field('Picks per message'), { target: { value: '2' } });
    fireEvent.change(field('MiB per image'), { target: { value: '0' } });
    expect(JSON.parse(localStorage.getItem(BOUNDS_KEY) ?? 'null')).toEqual({ maxPicks: 2 });
    act(() => {
      trayStore.getState().add(invariant);
      trayStore.getState().add(task('L118'));
      trayStore.getState().take(task('L119'));
    });
    expect(screen.getByTestId('tray-refusal').textContent).toContain(
      "“L119 · A leaf” is not added: the tray holds at most 2 picks per message (set under Bounds in the tray's foot). Remove one first.",
    );
    fireEvent.click(screen.getByRole('button', { name: 'Back to defaults' }));
    expect(field('Picks per message').value).toBe('20');
    expect(localStorage.getItem(BOUNDS_KEY)).toBeNull();
    act(() => void trayStore.getState().take(task('L119')));
    expect(trayStore.getState().picks).toHaveLength(3);
  });

  it('shows in the sent log the exact pick ids of each send, with its time, target and message file digest', async () => {
    const sent = {
      time: new Date(2026, 9, 19, 14, 5).toISOString(),
      targetAgentId: manager.agentId,
      pickIds: ['pick-mgx1-1', 'pick-mgx1-2'],
      messageId: 'm-7',
      messageFile: '/coord/notes/reports/developer-messages/agents-remember/x.md',
      sha256: 'c'.repeat(64),
      state: 'delivered',
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => (url.endsWith('/sent?limit=20') ? jsonResponse({ entries: [sent] }) : server(url, init))),
    );
    act(() => {
      trayStore.getState().setTargets([manager.agentId]);
      trayStore.getState().setOpen(true);
    });
    render(<Shell />);
    await waitFor(() => expect(seatStore.getState().status).toBe('ready'));
    fireEvent.click(screen.getByRole('button', { name: 'Sent log ▸' }));
    const log = await screen.findByRole('list', { name: /^Sent to Manager · 260928-MIK/ });
    const entry = within(log).getAllByRole('listitem')[0];
    expect(entry.textContent).toMatch(/^14:05 · Manager · 260928-MIK · b1d2c3e4 · 2 picks · delivered/);
    fireEvent.click(within(entry).getByText(/2 picks/));
    expect(entry.querySelector('details')?.open).toBe(true);
    const ids = within(entry).getByRole('list', { name: 'Pick ids' });
    expect(within(ids).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['pick-mgx1-1', 'pick-mgx1-2']);
    expect(entry.textContent).toContain(`${sent.messageFile} · SHA-256 ${'c'.repeat(64)}`);
  });

  it('gives the one-message line a line of its own above Send, and names the target by role and task on one line', async () => {
    const reviewer: TraySeat = {
      ...worker,
      agentId: '0f89acd5-0000-4000-8000-000000000005',
      role: 'reviewer',
      task: { repository: 'agents-remember', path: 'm/89.json', id: '260928-MIK-L89', title: 'The review page' },
    };
    liveSeats = [manager, reviewer];
    const object = (id: string): TrayPickInput => ({
      kind: 'reference',
      title: `${id} · an invariant`,
      reference: { kind: 'invariant', repository: 'agents-remember', id, memoryTree: 'a'.repeat(40) },
    });
    act(() => {
      for (let n = 1; n <= 12; n += 1) trayStore.getState().add(object(`INV-${n}`));
      trayStore.getState().setTargets([reviewer.agentId]);
      trayStore.getState().setOpen(true);
    });
    render(<Shell />);
    await waitFor(() => expect(seatStore.getState().status).toBe('ready'));
    const preview = screen.getByTestId('tray-preview');
    // Send says the target's role and task; its name for assistive technology keeps the agent tag.
    const send = screen.getByRole('button', { name: 'Send to Reviewer · 260928-MIK-L89 · 0f89acd5' });
    expect(send.textContent).toBe('Send to Reviewer · 260928-MIK-L89');
    // The sentence stands on its own line above the button, never as a column beside it.
    expect(send.parentElement?.contains(preview)).toBe(false);
    expect(preview.compareDocumentPosition(send)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    // The head names the chosen chat on one line: its role and task, cut short at need, then its tag.
    const to = screen.getByRole('button', { name: 'Send to: Reviewer · 260928-MIK-L89 · 0f89acd5. Choose the chats' });
    expect(to.textContent).toBe('to: Reviewer · 260928-MIK-L89 · 0f89acd5 ▾');
    expect(to.className.split(' ')).toEqual(expect.arrayContaining(['white-space_nowrap', 'ov_hidden']));
  });

  it('lists the targets as the mockup does: a dot on the chosen chat, its identity on one line, the agent tag whole', async () => {
    act(() => {
      trayStore.getState().setTargets([manager.agentId]);
      trayStore.getState().setOpen(true);
    });
    render(<Shell />);
    await waitFor(() => expect(seatStore.getState().status).toBe('ready'));
    fireEvent.click(screen.getByRole('button', { name: 'Send to: Manager · 260928-MIK · b1d2c3e4. Choose the chats' }));
    const menu = await screen.findByTestId('tray-target-menu');
    await within(menu).findByText(identity('Worker · L118 · b1d2c3e4'));
    const rows = within(menu).getAllByTestId('tray-target');
    const marks = () => rows.map((row) => row.querySelector('[data-target-mark]')?.textContent);
    expect(marks()).toEqual(['●', '']);
    // The checkbox stays for the keyboard and assistive technology, and is not drawn.
    const box = within(rows[1]).getByRole('checkbox', { name: /Worker · L118/ });
    expect(box.className.split(' ')).toEqual(expect.arrayContaining(['pos_absolute', 'w_1px', 'h_1px']));
    fireEvent.click(box);
    expect(marks()).toEqual(['●', '●']);
    const named = rows[1].querySelector('[data-target-identity]') as HTMLElement;
    expect(Array.from(named.children, (part) => part.textContent)).toEqual(['Worker · L118', ' · b1d2c3e4']);
    expect(named.className.split(' ')).toEqual(expect.arrayContaining(['white-space_nowrap']));
    expect(menu.className.split(' ')).toEqual(expect.arrayContaining(['w_min(400px,_calc(100vw_-_2rem))']));
  });

  it('draws the target popup whole beside the "to:" button and inside the window, and keeps it there as the page moves', async () => {
    act(() => {
      trayStore.getState().setTargets([manager.agentId]);
      trayStore.getState().setOpen(true);
    });
    // The geometry jsdom does not lay out: the window's size, the button's box, the popup's width
    // and the height of its rows.
    const view = { innerWidth: window.innerWidth, innerHeight: window.innerHeight };
    let button = { left: 1560, top: 120, right: 1880, bottom: 140 };
    const box = (edges: { left: number; top: number; right: number; bottom: number }) =>
      ({ ...edges, x: edges.left, y: edges.top, width: edges.right - edges.left, height: edges.bottom - edges.top, toJSON: () => edges }) as DOMRect;
    const boxes = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      if (this.hasAttribute('aria-expanded') && this.getAttribute('aria-label')?.startsWith('Send to:')) return box(button);
      if (this.getAttribute('data-testid') === 'tray-target-menu') return box({ left: 0, top: 0, right: 400, bottom: 0 });
      return box({ left: 0, top: 0, right: 0, bottom: 0 });
    });
    const heights = vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockImplementation(function (this: Element) {
      return this.getAttribute('data-testid') === 'tray-target-menu' ? 230 : 0;
    });
    const resize = (innerWidth: number, innerHeight: number) => {
      Object.assign(window, { innerWidth, innerHeight });
      fireEvent(window, new Event('resize'));
    };
    try {
      Object.assign(window, { innerWidth: 1920, innerHeight: 1080 });
      render(<Shell />);
      await waitFor(() => expect(seatStore.getState().status).toBe('ready'));
      fireEvent.click(screen.getByRole('button', { name: 'Send to: Manager · 260928-MIK · b1d2c3e4. Choose the chats' }));
      const menu = await screen.findByTestId('tray-target-menu');
      await within(menu).findByText(identity('Worker · L118 · b1d2c3e4'));
      const placed = () => [menu.style.left, menu.style.top, menu.style.bottom, menu.style.maxHeight];
      // Placed in the window, not in the column: no rail, dock or list that clips its content clips it.
      expect(menu.className.split(' ')).toEqual(expect.arrayContaining(['pos_fixed']));
      // The head near the window's top: its rows do not fit above the button, so the popup opens
      // below it, its right edge on the button's, at most half the window high.
      expect(placed()).toEqual(['1480px', '145px', '', '540px']);
      // A narrower window, the button lower: the popup opens above it, wider than the button's
      // column, its left edge still inside the window.
      button = { left: 1180, top: 600, right: 1420, bottom: 620 };
      act(() => resize(1440, 900));
      expect(placed()).toEqual(['1020px', '', '305px', '450px']);
      // Something on the page scrolls and the button moves with it: the popup follows.
      button = { left: 1180, top: 560, right: 1420, bottom: 580 };
      act(() => void fireEvent.scroll(screen.getByTestId('tray-panel')));
      expect(placed()).toEqual(['1020px', '', '345px', '450px']);
      // A button near the window's left edge: the popup starts inside the window.
      button = { left: 40, top: 600, right: 300, bottom: 620 };
      act(() => resize(1440, 900));
      expect(placed()[0]).toBe('8px');
    } finally {
      boxes.mockRestore();
      heights.mockRestore();
      Object.assign(window, view);
    }
  });

  it('tells apart two agents of one role and task on every target row and in the chosen target', async () => {
    const first: TraySeat = { ...architect, agentId: '5e1f0a2b-7c3d-4e5f-8a9b-0c1d2e3f4a5b', role: 'investigator' };
    const second: TraySeat = { ...architect, agentId: '9c3d7e4f-1a2b-4c5d-8e6f-7a8b9c0d1e2f', role: 'investigator' };
    liveSeats = [first, second];
    act(() => trayStore.getState().setOpen(true));
    render(<Shell />);
    fireEvent.click(screen.getByRole('button', { name: 'Send to: choose a chat. Choose the chats' }));
    const menu = await screen.findByTestId('tray-target-menu');
    await within(menu).findByText(identity('Investigator · Projects · 5e1f0a2b'));
    const rows = within(menu).getAllByTestId('tray-target');
    expect(rows.map((row) => [row.textContent, row.getAttribute('title')])).toEqual([
      ['Investigator · Projects · 5e1f0a2blive', `agent ${first.agentId}`],
      ['Investigator · Projects · 9c3d7e4flive', `agent ${second.agentId}`],
    ]);
    fireEvent.click(within(menu).getByRole('checkbox', { name: /9c3d7e4f/ }));
    expect(screen.getByRole('button', { name: 'Send to: Investigator · Projects · 9c3d7e4f. Choose the chats' })).toBeTruthy();
    // Ids that share their first eight characters are told apart by a longer prefix.
    const twin: TraySeat = { ...first, agentId: '5e1f0a2b-7c3d-4e5f-8a9b-ffffffffffff' };
    expect([seatLabel(first, [first, twin]), seatLabel(twin, [first, twin])]).toEqual([
      'Investigator · Projects · 5e1f0a2b-7c3d-4e5f-8a9b-0',
      'Investigator · Projects · 5e1f0a2b-7c3d-4e5f-8a9b-f',
    ]);
  });

  it('takes a chat passage from the frame channel with its provenance and makes that chat the default target', () => {
    seatStore.setState({ seats: [manager, worker], nextOffset: null, complete: true, status: 'ready', detail: null });
    render(
      <>
        <TrayButton />
        <TrayLayer />
      </>,
    );
    // While the tray listens, every frame is told to offer "⊕ add" and to copy as the preference says.
    expect(frameTrayStore.getState().pickListeners).toBe(1);
    expect(frameTrayStore.getState().copyOnHighlight).toBe(true);
    act(() =>
      receiveFrameTray('document', {
        type: 'pick',
        text: 'No, not forever.',
        agentId: manager.agentId,
        messageId: 'm-1',
        messageTime: new Date(2026, 9, 19, 14, 5).toISOString(),
      }),
    );
    // The pick shows the tray minimised (15f92a8f); his click on its line shows the passage.
    expect(screen.getByRole('button', { name: 'Expand the AR tray, 1 pick' }).textContent).toBe('⊕ Attached · 1');
    fireEvent.click(screen.getByRole('button', { name: 'Expand the AR tray, 1 pick' }));
    const chip = screen.getByTestId('tray-pick');
    expect(chip.getAttribute('data-pick-kind')).toBe('text');
    expect(chip.textContent).toContain('from Manager · 14:05');
    expect(screen.getByRole('button', { name: 'Send to Manager · 260928-MIK · b1d2c3e4' })).toBeTruthy();
    act(() => void takeChatPick({ text: 'A later one.', agentId: worker.agentId, messageId: null, messageTime: new Date(2026, 9, 19, 9, 30).toISOString() }));
    expect(screen.getAllByTestId('tray-pick')[1].textContent).toContain('from Worker · 09:30');
  });

  it('defaults an object pick to the shown chat; with no chat shown, Send stays disabled', () => {
    act(() => {
      trayStore.getState().add(invariant);
      trayStore.getState().setOpen(true);
    });
    render(<Shell />);
    expect(screen.getByRole('button', { name: 'Send' }).getAttribute('title')).toBe('Choose a chat to send to');
    expect(screen.getByRole('button', { name: 'Send' }).hasAttribute('disabled')).toBe(true);
    act(() => {
      trayStore.setState({ ...EMPTY_TRAY });
      setShownChat({ agentId: manager.agentId, label: 'Manager · 260928-MIK' });
      trayStore.getState().add(invariant);
    });
    expect(trayStore.getState().targets).toEqual([manager.agentId]);
    expect(screen.getByRole('button', { name: 'Send to Manager · 260928-MIK' }).hasAttribute('disabled')).toBe(false);
  });

  it('reads the live seats again each time the panel or the target menu opens', async () => {
    act(() => trayStore.getState().setOpen(true));
    render(<Shell />);
    const menuButton = () => screen.getByRole('button', { name: 'Send to: choose a chat. Choose the chats' });
    fireEvent.click(menuButton());
    await within(screen.getByTestId('tray-target-menu')).findByText(identity('Worker · L118 · b1d2c3e4'));
    fireEvent.click(menuButton());
    // A seat started and one archived since: the next opening lists the host as it is now.
    liveSeats = [manager, architect];
    fireEvent.click(menuButton());
    const menu = screen.getByTestId('tray-target-menu');
    await within(menu).findByText(identity('Architect · Projects · b1d2c3e4'));
    expect(within(menu).queryByText(identity('Worker · L118 · b1d2c3e4'))).toBeNull();
    fireEvent.click(menuButton());
    await waitFor(() => expect(seatStore.getState().status).toBe('ready'));
    act(() => trayStore.getState().setOpen(false));
    liveSeats = [manager];
    act(() => trayStore.getState().setOpen(true));
    await waitFor(() => expect(seatStore.getState().seats.map((seat) => seat.agentId)).toEqual([manager.agentId]));
  });

  it('names a default target by its seat row once the seat list arrives; an unlisted one says why it shows its id', async () => {
    let openList = () => {};
    const listRead = new Promise<void>((resolve) => {
      openList = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.startsWith('/api/role-chats?')) await listRead;
        return server(url, init);
      }),
    );
    act(() => {
      setShownChat({ agentId: manager.agentId });
      trayStore.getState().add(invariant);
      trayStore.getState().setOpen(true);
    });
    render(<Shell />);
    const send = screen.getByRole('button', { name: 'Send to agent b1d2c3e4' });
    expect(send.getAttribute('title')).toBe('agent b1d2c3e4 is named by its id: the seat list is still being read.');
    await act(async () => openList());
    const named = await screen.findByRole('button', { name: 'Send to Manager · 260928-MIK · b1d2c3e4' });
    expect(named.hasAttribute('title')).toBe(false);
    expect(screen.getByRole('button', { name: 'Send to: Manager · 260928-MIK · b1d2c3e4. Choose the chats' })).toBeTruthy();
    // A chosen chat the host's list does not hold keeps its id, and says why.
    const gone = 'f613c27b-0000-4000-8000-000000000009';
    act(() => trayStore.getState().toggleTarget(gone));
    const why = "agent f613c27b is named by its id: the host's list of live seats does not hold this chat; it may have ended.";
    expect(screen.getByRole('button', { name: 'Send to 2 chats' }).getAttribute('title')).toBe(why);
    fireEvent.click(screen.getByRole('button', { name: 'Send to: 2 chats. Choose the chats' }));
    const menu = screen.getByTestId('tray-target-menu');
    await waitFor(() => expect(seatStore.getState().status).toBe('ready'));
    const row = within(menu).getByText('agent f613c27b').closest('label');
    expect(row?.getAttribute('title')).toBe(why);
  });

  it('sends from a page without a repository and shows every refusal by name', async () => {
    const explainer: TraySeat = { ...worker, agentId: 'b1d2c3e4-0000-4000-8000-000000000004', role: 'explainer' };
    liveSeats = [architect, manager, worker, explainer];
    const refused = (reason: string, detail: string, messageId: string | null = 'm') =>
      jsonResponse({ state: 'refused', messageId, messageFile: null, messageFileSha256: null, reason, detail });
    const answers: Record<string, () => Response> = {
      [architect.agentId]: () =>
        refused('unknown-repository', 'The page names no repository and this server has 2; a target without a task needs one to file under.'),
      [manager.agentId]: () => refused('image-type-unsupported', "Pick 1 is an image of type 'image/bmp'; the tray sends png, jpeg, gif and webp."),
      [worker.agentId]: () => refused('invalid-request', 'picks.0.title: Field required', null),
      [explainer.agentId]: () => refused('message-too-large', 'The message is 21000000 bytes; the bound is 20971520.'),
    };
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (!url.endsWith('/message')) return server(url, init);
        posts.push({ url, body: JSON.parse(String(init?.body)) as Record<string, unknown> });
        return answers[decodeURIComponent(url.split('/')[3])]();
      }),
    );
    act(() => {
      trayStore.getState().add({
        kind: 'text',
        title: 'No, not forever.',
        text: 'No, not forever.',
        provenance: { source: 'chat', agentId: manager.agentId, messageId: 'm-1', messageTime: '2026-10-19T12:05:00Z' },
      });
      trayStore.getState().setTargets(liveSeats.map((seat) => seat.agentId));
      trayStore.getState().setOpen(true);
    });
    render(<Shell />);
    await waitFor(() => expect(seatStore.getState().status).toBe('ready'));
    fireEvent.click(screen.getByRole('button', { name: 'Send to 4 chats' }));
    // After the Send the tray is its line again (15f92a8f), which names every refusal under it.
    const outcome = await screen.findByTestId('tray-line-refusals');
    expect(screen.queryByTestId('tray-panel')).toBeNull();
    // The taskless seat's message names no repository: the server takes its sole one or refuses.
    expect(posts.map((post) => [post.url.split('/')[3], post.body.repository])).toEqual([
      [architect.agentId, undefined],
      [manager.agentId, 'agents-remember'],
      [worker.agentId, 'agents-remember'],
      [explainer.agentId, 'agents-remember'],
    ]);
    const stays = ' The picks and your text stay, with this chat as the target.';
    expect(Array.from(outcome.querySelectorAll('li'), (line) => line.textContent)).toEqual([
      `Refused by Architect · Projects · b1d2c3e4: unknown-repository: The page names no repository and this server has 2; a target without a task needs one to file under.${stays}`,
      `Refused by Manager · 260928-MIK · b1d2c3e4: image-type-unsupported: Pick 1 is an image of type 'image/bmp'; the tray sends png, jpeg, gif and webp.${stays}`,
      `Refused by Worker · L118 · b1d2c3e4: invalid-request: picks.0.title: Field required.${stays}`,
      `Refused by Explainer · L118 · b1d2c3e4: message-too-large: The message is 21000000 bytes; the bound is 20971520.${stays}`,
    ]);
  });

  it('sends to several chosen chats and shows a held target as held', async () => {
    act(() => {
      trayStore.getState().add(invariant);
      trayStore.getState().setOpen(true);
    });
    render(<Shell />);
    fireEvent.click(screen.getByRole('button', { name: 'Send to: choose a chat. Choose the chats' }));
    const menu = await screen.findByTestId('tray-target-menu');
    await within(menu).findByText(identity('Manager · 260928-MIK · b1d2c3e4'));
    fireEvent.click(within(menu).getByRole('checkbox', { name: /Manager · 260928-MIK/ }));
    fireEvent.click(within(menu).getByRole('checkbox', { name: /Worker · L118/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Your message with these picks' }), { target: { value: 'How likely is each?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to 2 chats' }));
    // The sent tray returns to its line, which says what the Send did; the full view lists each answer.
    const summary = await screen.findByTestId('tray-line-outcome');
    expect(summary.textContent).toBe('Sent: delivered to Manager · 260928-MIK · b1d2c3e4, held for Worker · L118 · b1d2c3e4.');
    fireEvent.click(screen.getByRole('button', { name: 'Expand the AR tray, 0 picks' }));
    const outcome = screen.getByTestId('tray-outcome');
    expect(outcome.textContent).toContain('Delivered to Manager · 260928-MIK · b1d2c3e4.');
    expect(outcome.textContent).toContain('Held for Worker · L118 · b1d2c3e4 (recipient-in-turn)');
    expect(trayStore.getState().picks).toEqual([]);
    const posts = server.mock.calls.filter(([url]) => String(url).endsWith('/message'));
    expect(posts.map(([url]) => url)).toEqual([
      `/api/role-chats/${manager.agentId}/message`,
      `/api/role-chats/${worker.agentId}/message`,
    ]);
  });
});
