// The review page's column is the leaf's explainer (MIK-R106 v2 rule 1): the rail hands the column
// the leaf's address, the pane shows the explainer or its placeholder, and the task's Worker is
// never the default. Every other page keeps the document's own chat, unchanged.
import { act, cleanup, fireEvent, render, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CockpitShell, RightRail } from './Cockpit';
import { dashboardStore } from '../data/store';
import { chatLeaf, chatMaster, chatSprint } from '../test/fixtures/documentChat';
import { projection } from '../test/fixtures/wire';
import { DocumentChat } from './document-chat/DocumentChat';
import { explainerAddress, useReviewChat, type ReviewChatColumn } from './reviewChat';
import type { ChangeSetTarget } from '../panels/changeset/ChangeSetViewer';
import { resetExplainerSeats } from '../explainer/ExplainerTaskDocument';
import { askFirstRequest, resetFirstRequest } from '../explainer/request';
import { ADDRESS, RETIRE, STATE, backend, explainerState, invariantPick, resetTray } from '../explainer/testSupport';
import { trayStore } from '../tray/trayStore';

const EXPLAINER_TARGET = {
  address: ADDRESS,
  task: 'L181',
  preload: true,
  ...ADDRESS,
  planned: false,
  comparisonNumber: null,
  history: 'live' as const,
};

const column = (patch: Partial<ReviewChatColumn> = {}): ReviewChatColumn => ({
  open: true,
  toggle: () => undefined,
  width: 500,
  onResize: () => undefined,
  gutter: 14,
  placement: 'grid',
  documentRef: undefined,
  unbound: null,
  explainer: EXPLAINER_TARGET,
  ...patch,
});

beforeEach(() => {
  vi.stubGlobal('CSS', { escape: (value: string) => value });
  resetTray();
  resetFirstRequest();
  resetExplainerSeats();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
});

describe('the rail hands the column its leaf (rule 1)', () => {
  it('names the review’s repository, master and leaf, and no other page’s chat', () => {
    expect(explainerAddress({ repo: 'repo', master: 'master', leaf: 'L181' })).toEqual(ADDRESS);
    // A review without a leaf has no explainer: the document's own chat stands.
    expect(explainerAddress({ repo: 'repo', master: 'master', leaf: undefined })).toBeNull();
    expect(explainerAddress(null)).toBeNull();
  });

  it('passes column.explainer through RightRail into DocumentChat so cold, open, finalized and planning targets select explainer/placeholder, never Worker', async () => {
    const server = backend({
      [STATE]: [
        { body: explainerState() },
        { body: explainerState({ binding: { comparison_owner: 'planned', comparison_number: 0 } }) },
        { body: explainerState({ seat: 'live', agentId: 'a-exp', workspaceId: 'wks' }) },
      ],
    });
    const view = render(
      <RightRail
        fullBleed={false}
        railEnter={{}}
        railView="chat"
        rightRailWidth={500}
        setRailView={() => undefined}
        viewedTask={{ taskDocumentRef: { repository: 'repo', path: 'master/L181.json' } }}
        tasks={[]}
        onResize={() => undefined}
        column={column()}
      />,
    );
    await waitFor(() => expect(view.getByTestId('explainer-column')).toBeTruthy());
    expect(view.getByTestId('explainer-column').dataset.selection).toBe('placeholder');
    expect(view.getByTestId('explainer-placeholder')).toBeTruthy();
    expect(server.paths()).not.toContain('/api/role-launch/document-chats');

    // Native Explain opens the first-request form in the mounted rail.
    act(() => askFirstRequest(ADDRESS));
    expect(await view.findByTestId('explainer-first-request')).toBeTruthy();

    // Planning, open, and finalized review targets all select explainer/placeholder, never Worker.
    for (const targetPatch of [
      { planned: true, history: 'live' as const },
      { planned: false, history: 'live' as const, comparisonNumber: 8 },
      { planned: false, history: 'recorded' as const, comparisonNumber: 8 },
    ]) {
      view.rerender(
        <RightRail
          fullBleed={false}
          railEnter={{}}
          railView="chat"
          rightRailWidth={500}
          setRailView={() => undefined}
          viewedTask={{ taskDocumentRef: { repository: 'repo', path: 'master/L181.json' } }}
          tasks={[]}
          onResize={() => undefined}
          column={column({ explainer: { ...EXPLAINER_TARGET, ...targetPatch } })}
        />,
      );
      expect(view.getByTestId('explainer-column')).toBeTruthy();
      expect(view.queryByTestId('document-chat')).toBeNull();
    }
    expect(server.paths()).not.toContain('/api/role-launch/document-chats');
  });

  it('mounts ExplainerTaskDocumentControl on Operations leaf task documents and retires cold explainers', async () => {
    dashboardStore.getState().applySnapshot(projection({ tasks: [chatSprint, chatMaster, chatLeaf] }));
    const leafStatePath = '/api/explainer/repo/master/LEAF/state';
    const leafRetirePath = '/api/explainer/repo/master/LEAF/retire';
    const server = backend({
      [leafStatePath]: [
        { body: explainerState({ seat: 'live', agentId: 'a-exp-leaf', workspaceId: 'wks' }) },
        { body: explainerState({ seat: null, retiredAt: '2026-10-19T15:12:00Z' }) },
      ],
      [leafRetirePath]: {
        body: {
          state: 'retired',
          receipt: {
            receiptId: 'r-leaf',
            agentId: 'a-exp-leaf',
            generation: 1,
            state: 'finished',
            step: 'finished',
            endedByMarker: 2,
            endedByTree: 2,
            remaining: [],
            detail: '',
          },
        },
      },
      [RETIRE]: { body: {} },
    });
    const view = render(<CockpitShell initialView="operations" />);
    fireEvent.click(await view.findByText('Leaf'));
    const control = await view.findByTestId('explainer-task-document');
    expect(control.textContent).toContain('live');
    fireEvent.click(view.getByTestId('explainer-task-document-retire'));
    fireEvent.click(await view.findByTestId('explainer-task-document-confirm-send'));
    await waitFor(() => expect(server.count('POST', leafRetirePath)).toBe(1));
    expect((await view.findByTestId('explainer-task-document-receipt')).textContent).toContain('No process remains');
  });

  it('keeps the exact same iframe DOM node and draft across Operations and Review page/phase switches', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string) => {
        const url = new URL(String(address), 'http://dashboard.test');
        if (url.pathname === '/api/role-launch/frame') {
          return new Response(
            JSON.stringify({
              available: true,
              frameBaseUrl: 'https://host.example',
              serverId: 's',
              projectsWorkspaceId: 'wks_projects',
            }),
            { headers: { 'Content-Type': 'application/json' } },
          );
        }
        if (url.pathname === STATE) {
          return new Response(
            JSON.stringify(explainerState({ seat: 'live', agentId: 'a-exp', workspaceId: 'wks' })),
            { headers: { 'Content-Type': 'application/json' } },
          );
        }
        return new Response(JSON.stringify({ agents: [], executions: [], roleDefaults: {} }), {
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );

    const view = render(
      <RightRail
        fullBleed={false}
        railEnter={{}}
        railView="chat"
        rightRailWidth={500}
        setRailView={() => undefined}
        viewedTask={undefined}
        tasks={[]}
        onResize={() => undefined}
        column={null}
      />,
    );
    await waitFor(() => expect(view.container.querySelector("iframe[title='Role chats']")).not.toBeNull());
    const iframeBefore = view.container.querySelector("iframe[title='Role chats']")!;
    act(() => trayStore.getState().setText('keep this draft across page switches'));

    // Switch to Review page (placeholder -> first-request -> live).
    view.rerender(
      <RightRail
        fullBleed={false}
        railEnter={{}}
        railView="chat"
        rightRailWidth={500}
        setRailView={() => undefined}
        viewedTask={undefined}
        tasks={[]}
        onResize={() => undefined}
        column={column()}
      />,
    );
    await waitFor(() => expect(view.getByTestId('explainer-column')).toBeTruthy());
    expect(view.container.querySelector("iframe[title='Role chats']")).toBe(iframeBefore);
    expect(trayStore.getState().text).toBe('keep this draft across page switches');

    // Open first-request form phase via attachment.
    act(() => {
      trayStore.getState().take(invariantPick('INV-1', 'INV-1'));
    });
    expect(view.container.querySelector("iframe[title='Role chats']")).toBe(iframeBefore);

    // Switch back to Operations view: exact same iframe element and draft survive.
    view.rerender(
      <RightRail
        fullBleed={false}
        railEnter={{}}
        railView="chat"
        rightRailWidth={500}
        setRailView={() => undefined}
        viewedTask={undefined}
        tasks={[]}
        onResize={() => undefined}
        column={null}
      />,
    );
    expect(view.getByTestId('document-chat')).toBeTruthy();
    expect(view.container.querySelector("iframe[title='Role chats']")).toBe(iframeBefore);
    expect(trayStore.getState().text).toBe('keep this draft across page switches');
  });
});

describe('the pane shows the explainer column for a review leaf (rule 1)', () => {
  it('shows the placeholder and reads no document chat, so the Worker is never selected', async () => {
    const server = backend({ [STATE]: { body: explainerState() } });
    const panel = render(
      <DocumentChat active tasks={[]} explainer={{ address: ADDRESS, task: 'L181' }} />,
    );
    await waitFor(() => expect(panel.getByTestId('explainer-column')).toBeTruthy());
    expect(panel.getByTestId('explainer-column').dataset.selection).toBe('placeholder');
    expect(panel.getByTestId('explainer-placeholder')).toBeTruthy();
    expect(panel.queryByTestId('document-chat')).toBeNull();
    expect(server.paths()).not.toContain('/api/role-launch/document-chats');
    expect(server.posts()).toEqual([]);
  });

  it('keeps the document’s own chat for a page without an explainer', async () => {
    const server = backend();
    const panel = render(<DocumentChat active tasks={[]} />);
    expect(panel.getByTestId('document-chat')).toBeTruthy();
    expect(panel.queryByTestId('explainer-column')).toBeNull();
    await act(async () => {});
    expect(server.paths()).not.toContain(STATE);
  });
});

// MIK-R211 rule 4: "pressing either Explain control shows something". The press asks for the first
// request, the ask opens the column, and the press must not toggle it shut again from the state it
// started in — which is what left the column inside a `display: none` rail with a 0×0 box.
describe('a press that asks opens the chat column and never closes it (MIK-R211 rule 4)', () => {
  it('opens on the ask, stays open on the press’s own open, and only the toggle closes it', () => {
    const review: ChangeSetTarget = {
      repo: ADDRESS.repository,
      master: ADDRESS.master,
      leaf: ADDRESS.leaf,
      review: {},
    };
    const view = renderHook(() => useReviewChat(review));

    expect(view.result.current.open).toBe(false);
    // The ask opens the column by itself (the shell's listener).
    act(() => askFirstRequest(ADDRESS));
    expect(view.result.current.open).toBe(true);
    // The press then opens it again, idempotently: an open, never a toggle.
    act(() => view.result.current.expand?.());
    expect(view.result.current.open).toBe(true);
    // The side bar's own control is still the one thing that closes it.
    act(() => view.result.current.toggle());
    expect(view.result.current.open).toBe(false);
  });
});
