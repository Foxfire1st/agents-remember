// The tray mounted in the shell (MIK-R188 rule 5; rule 7 on the review page). The review surface is
// a stand-in with one object ⊕, so these cases read the shell and the tray alone.
import { act, cleanup, fireEvent, render, waitFor, within, type RenderResult } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import { chatLeaf, chatMaster, chatSprint } from '../test/fixtures/documentChat';
import { projection } from '../test/fixtures/wire';
import { CockpitShell } from '../cockpit/Cockpit';
import type { DocumentAgent } from '../cockpit/document-chat/model';
import { shownStore } from './shownChat';
import { EMPTY_TRAY } from './trayPersistence';
import { seatStore } from './traySeats';
import { trayStore } from './trayStore';

vi.mock('../panels/review/ReviewSurface', async () => {
  const { AddToTray } = await import('./AddToTray');
  const pick = {
    kind: 'reference' as const,
    title: 'INV-1ZKP4R · A silent role-agent turn end',
    reference: { kind: 'invariant' as const, repository: 'repo', id: 'INV-1ZKP4R', memoryTree: 'a'.repeat(40) },
  };
  return {
    ReviewSurface: (props: { onBack: () => void; chat?: { open: boolean; toggle: () => void } }) => (
      <div data-testid="review-surface">
        <button type="button" data-testid="review-chat-toggle" aria-expanded={props.chat?.open} onClick={() => props.chat?.toggle()}>
          Chat
        </button>
        <button type="button" data-testid="review-back" onClick={props.onBack}>
          Back
        </button>
        <AddToTray pick={pick} label="invariant INV-1ZKP4R" testId="review-add" />
      </div>
    ),
  };
});

const ORIGIN = 'https://host.example';
const FRAME = { available: true, frameBaseUrl: ORIGIN, serverId: 's', projectsWorkspaceId: 'wks_projects' };
const REVIEW = '#review?repo=repo&master=master&leaf=LEAF';

const agent = (agentId: string, workspaceId: string, labels: Record<string, string>): DocumentAgent => ({
  agentId,
  workspaceId,
  archivedAt: null,
  createdAt: '2026-01-01',
  labels,
});
const ARCHITECT = [agent('a-arch', 'wks_projects', { 'ar.role': 'architect' })];
const WORKER = [agent('a-wrk', 'wks_leaf', { 'ar.role': 'worker', 'ar.task-ref': 'repo/master/leaf.json' })];

function backend() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string, init?: RequestInit) => {
      const url = new URL(String(address), 'http://dashboard.test');
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      // The launcher's own reads (options, catalog) answer an empty launcher.
      let value: unknown = { roleDefaults: {}, agents: [], executions: [] };
      if (url.pathname === '/api/role-launch/frame') value = FRAME;
      else if (url.pathname === '/api/role-launch/document-chats')
        value = { agents: body?.role === 'worker' ? WORKER : body?.role === 'architect' ? ARCHITECT : [] };
      else if (url.pathname === '/api/role-chats') value = { state: 'seats', seats: [], nextOffset: null, complete: true };
      else if (url.pathname === '/api/task-document')
        value = dashboardStore.getState().tasks[url.searchParams.get('path') ?? '']?.summary ?? {};
      return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
    }),
  );
}

const hydrate = () => dashboardStore.getState().applySnapshot(projection({ tasks: [chatSprint, chatMaster, chatLeaf] }));
const frameOf = (view: RenderResult) => view.container.querySelector<HTMLIFrameElement>("iframe[title='Role chats']");
const generation = (view: RenderResult) =>
  view.container.querySelector('[data-testid="paseo-frame"]')?.getAttribute('data-generation') ?? null;
const chatColumn = (view: RenderResult) => view.container.querySelector<HTMLElement>('.rail--right')!;

beforeEach(() => {
  vi.stubGlobal('CSS', { escape: (value: string) => value });
  trayStore.setState({ ...EMPTY_TRAY, open: false, minimised: false, refusal: null, outcome: null, deferDefault: false, pendingDefault: false });
  seatStore.setState({ seats: [], nextOffset: null, complete: true, status: 'idle', detail: null });
  shownStore.setState({ chat: null, page: null, repository: null });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
});

describe('the tray in the shell', () => {
  it('puts the Tray button outside the view tabs, the tray beside the one frame, and the rest outside the body', async () => {
    window.localStorage.setItem('cockpit.rail-chat', '1');
    hydrate();
    backend();
    const view = render(<CockpitShell initialView="operations" />);
    await waitFor(() => expect(frameOf(view)).not.toBeNull());
    const frame = frameOf(view)!;
    const loaded = generation(view);

    const views = view.getByRole('radiogroup', { name: 'Views' });
    const button = view.getByTestId('tray-button');
    expect(views.contains(button)).toBe(false);
    expect(within(views).getAllByRole('radio')).toHaveLength(4);

    fireEvent.click(button);
    const dock = view.getByTestId('tray-column-dock');
    // A sibling right after the frame's wrapper: the frame is neither inside the dock nor moved.
    expect(dock.previousElementSibling?.contains(frame)).toBe(true);
    expect(dock.contains(frame)).toBe(false);
    expect(view.queryByTestId('tray-corner-dock')).toBeNull();
    const body = view.container.querySelector('.shell__body')!;
    expect([...body.children].map((node) => node.tagName)).toEqual(['ASIDE', 'MAIN', 'ASIDE']);
    await waitFor(() => expect(shownStore.getState().chat?.agentId).toBe('a-arch'));

    // The rail shows the River: the column is hidden, so the tray docks at the bottom right,
    // outside the railed body.
    fireEvent.click(view.getByTestId('rail-toggle-river'));
    const corner = view.getByTestId('tray-corner-dock');
    expect(body.contains(corner)).toBe(false);
    expect(view.container.querySelector('.cockpit--shell')?.contains(corner)).toBe(true);
    expect(shownStore.getState().chat).toBeNull();
    fireEvent.click(view.getByTestId('rail-toggle-chat'));
    expect(frameOf(view)).toBe(frame);
    expect(generation(view)).toBe(loaded);
  });

  it('docks above the Chats page’s own frame, not at the corner over its message box', async () => {
    hydrate();
    backend();
    const view = render(<CockpitShell initialView="chats" />);
    await waitFor(() => expect(frameOf(view)).not.toBeNull());
    fireEvent.click(view.getByTestId('tray-button'));
    const dock = view.getByTestId('tray-column-dock');
    expect(view.getByTestId('role-chats-pane').contains(dock)).toBe(true);
    expect(dock.previousElementSibling?.contains(frameOf(view))).toBe(true);
    expect(view.queryByTestId('tray-corner-dock')).toBeNull();
  });
});

describe('the tray on the review page with its column closed', () => {
  it('shows a pick as the line with the column closed; his open of the tray opens the column, whose chat the pick takes', async () => {
    window.history.replaceState(null, '', '/' + REVIEW);
    hydrate();
    backend();
    const view = render(<CockpitShell />);
    await view.findByTestId('review-surface');
    expect(chatColumn(view).style.display).toBe('none');

    // The silent show after a pick opens no column: the line docks at the bottom right.
    fireEvent.click(view.getByTestId('review-add'));
    expect([trayStore.getState().open, trayStore.getState().minimised]).toEqual([true, true]);
    expect(chatColumn(view).style.display).toBe('none');
    expect(view.getByTestId('tray-corner-dock').contains(view.getByTestId('tray-line'))).toBe(true);
    expect(trayStore.getState().pendingDefault).toBe(true);

    // His click on the line opens the full view and the column; the column's chat is the default.
    fireEvent.click(view.getByRole('button', { name: 'Expand the AR tray, 1 pick' }));
    expect(chatColumn(view).style.display).toBe('flex');
    expect(view.getByTestId('review-chat-toggle').getAttribute('aria-expanded')).toBe('true');
    await waitFor(() => expect(trayStore.getState().targets).toEqual(['a-wrk']));
    expect(trayStore.getState().pendingDefault).toBe(false);
    expect(chatColumn(view).querySelector('[data-testid="tray-column-dock"]')).not.toBeNull();

    // He closes the column: the tray stays open, at the bottom right.
    fireEvent.click(view.getByTestId('review-chat-toggle'));
    expect(chatColumn(view).style.display).toBe('none');
    expect(trayStore.getState().open).toBe(true);
    expect(view.getByTestId('tray-corner-dock')).toBeTruthy();
    // Minimised, the "Tray (n)" button opens it in full again, and with it the column.
    fireEvent.click(view.getByRole('button', { name: 'Minimise the AR tray' }));
    expect(chatColumn(view).style.display).toBe('none');
    fireEvent.click(view.getByTestId('tray-button'));
    expect(chatColumn(view).style.display).toBe('flex');
  });

  it('keeps his own choice of target made before the column shows its chat, and ends the wait on leaving', async () => {
    window.history.replaceState(null, '', '/' + REVIEW);
    hydrate();
    backend();
    const view = render(<CockpitShell />);
    await view.findByTestId('review-surface');
    act(() => {
      fireEvent.click(view.getByTestId('review-add'));
      trayStore.getState().setTargets(['a-arch']);
    });
    fireEvent.click(view.getByTestId('tray-button'));
    await waitFor(() => expect(shownStore.getState().chat?.agentId).toBe('a-wrk'));
    expect(trayStore.getState().targets).toEqual(['a-arch']);

    // A new tray on the review page whose wait is ended by leaving it.
    act(() => {
      trayStore.setState({ ...EMPTY_TRAY, open: false });
      fireEvent.click(view.getByTestId('review-chat-toggle'));
    });
    expect(chatColumn(view).style.display).toBe('none');
    act(() => void trayStore.getState().add({ kind: 'text', title: 'x', text: 'x', provenance: { source: 'panel', panel: 'Review' } }));
    expect(trayStore.getState().pendingDefault).toBe(true);
    fireEvent.click(view.getByTestId('review-back'));
    expect(trayStore.getState().pendingDefault).toBe(false);
    expect(trayStore.getState().deferDefault).toBe(false);
  });
});
