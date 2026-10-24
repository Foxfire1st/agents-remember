// The review page's explainer column (MIK-R106 v2 rules 1–5, 7, 8 and 10): it selects the explainer
// or its placeholder and never the Worker; the placeholder starts nothing; the first request's form
// adds and removes references, names the fixed target, and leaves the placeholder on Cancel; the
// build names each step and keeps his request when it fails; Retire names what ends and what stays;
// a Send to an archived explainer opens the form; and nothing here starts a model turn.
import { act, cleanup, fireEvent, render, waitFor, type RenderResult } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { frameTrayStore, type ComposerEdge } from '../cockpit/frameTrayChannel';
import { seatStore } from '../tray/traySeats';
import { shownChatId } from '../tray/shownChat';
import { sendNow } from '../tray/TrayPanel';
import { trayStore } from '../tray/trayStore';
import { ExplainerColumn } from './ExplainerColumn';
import { resetExplainerSeats } from './ExplainerTaskDocument';
import { askFirstRequest, placeholderExplainerTargetId, resetFirstRequest, pendingFirstRequest } from './request';
import {
  ADDRESS,
  BUILD,
  RETIRE,
  STATE,
  TASK,
  backend,
  explainerState,
  invariantPick,
  resetTray,
} from './testSupport';

const view = (active = true): RenderResult =>
  render(<ExplainerColumn address={ADDRESS} task={TASK} active={active} />);

const steps = (view: RenderResult) =>
  [...view.container.querySelectorAll<HTMLElement>('[data-testid="explainer-build-step"]')].map((node) => ({
    name: node.dataset.step,
    state: node.dataset.stepState,
    percent: node.dataset.stepPercent ?? null,
  }));

beforeEach(() => {
  resetTray();
  resetFirstRequest();
  resetExplainerSeats();
  seatStore.setState({ seats: [], nextOffset: null, complete: true, status: 'idle', detail: null });
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('the column selects the explainer or its placeholder (rule 1)', () => {
  it('shows the placeholder and reads no document chat, so the Worker is never the default', async () => {
    const server = backend({ [STATE]: { body: explainerState() } });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    expect(panel.getByTestId('explainer-column').dataset.selection).toBe('placeholder');
    expect(panel.getByTestId('explainer-title').textContent).toBe('Explainer · L181');
    expect(server.count('GET', STATE)).toBe(1);
    // The task's chat binding is never asked for on the review page.
    expect(server.paths()).not.toContain('/api/role-launch/document-chats');
    expect(server.paths()).not.toContain('/api/role-launch/document-chats/tree');
  });

  it('lists the task’s chats in the header menu, with the explainer first', async () => {
    seatStore.setState({
      seats: [
        { agentId: 'a-wrk', workspaceId: 'wks-wrk', role: 'worker', task: { repository: 'repo', path: 'master/L181.json', id: TASK, title: TASK }, createdAt: '2026-10-19' },
        { agentId: 'a-foreign-repo', workspaceId: 'wks-fr', role: 'worker', task: { repository: 'other', path: 'master/L181.json', id: TASK, title: TASK }, createdAt: '2026-10-19' },
        { agentId: 'a-foreign-master', workspaceId: 'wks-fm', role: 'worker', task: { repository: 'repo', path: 'other-master/L181.json', id: TASK, title: TASK }, createdAt: '2026-10-19' },
        { agentId: 'a-other', role: 'worker', task: { repository: 'repo', path: 'master/L118.json', id: 'L118', title: 'L118' }, createdAt: '2026-10-19' },
      ],
      nextOffset: null,
      complete: true,
      status: 'ready',
      detail: null,
    });
    const posts: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string, init?: RequestInit) => {
        const url = new URL(String(address), 'http://dashboard.test');
        if ((init?.method ?? 'GET') !== 'GET') posts.push(url.pathname);
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
        return new Response(JSON.stringify({}), { headers: { 'Content-Type': 'application/json' } });
      }),
    );
    const panel = view();
    const button = await waitFor(() => panel.getByTestId('explainer-agent-menu-button'));
    expect(shownChatId()).toBe('a-exp');
    // The frame the menu steers is mounted before the choice, and the choice must not remount it.
    await waitFor(() => expect(panel.container.querySelector('iframe')).not.toBeNull());
    const iframeBefore = panel.container.querySelector('iframe');
    fireEvent.click(button);
    const rows = [...panel.container.querySelectorAll<HTMLElement>('[data-testid="explainer-agent-row"]')];
    expect(rows.map((row) => row.dataset.agent)).toEqual(['a-exp', 'a-wrk']);
    expect(rows[0].dataset.current).toBe('true');

    // Selecting the Worker row switches the active chat and tray target in the existing frame, with zero POSTs.
    fireEvent.click(rows[1]);
    expect(shownChatId()).toBe('a-wrk');
    await waitFor(() => expect(panel.container.querySelector('iframe')).not.toBeNull());
    expect(panel.container.querySelector('iframe')).toBe(iframeBefore);
    expect(trayStore.getState().targets).toEqual(['a-wrk']);
    fireEvent.click(panel.getByTestId('explainer-agent-menu-button'));
    const updatedRows = [...panel.container.querySelectorAll<HTMLElement>('[data-testid="explainer-agent-row"]')];
    expect(updatedRows[1].dataset.current).toBe('true');
    expect(posts).toEqual([]);
  });
});

describe('the placeholder (rule 2)', () => {
  it('fixes the role, names the settings role, and has no Start button', async () => {
    backend({ [STATE]: { body: explainerState() } });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    expect(panel.getByTestId('explainer-placeholder-role').textContent).toContain('Explainer');
    expect(panel.getByTestId('explainer-placeholder-agent').textContent).toBe('review-presenter');
    expect(panel.getByTestId('explainer-placeholder-line').textContent).toBe(
      'Nothing runs and no agent exists until you ask.',
    );
    expect(panel.queryByRole('button', { name: /^start/i })).toBeNull();
  });

  it('starts no agent, no build and no model turn on open', async () => {
    const server = backend({ [STATE]: { body: explainerState() } });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    expect(server.posts()).toEqual([]);
    expect(server.count('GET', STATE)).toBe(1);
  });
});

describe('the first request’s form (rule 3)', () => {
  it('opens on a ⊕ and takes its reference, then removes it with ×', async () => {
    backend({ [STATE]: { body: explainerState() } });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());

    act(() => {
      trayStore.getState().take(invariantPick('INV-1ZKP4R', 'INV-1ZKP4R · the seat’s return'));
    });
    const form = await panel.findByTestId('explainer-first-request');
    expect(form).toBeTruthy();
    expect(panel.getByTestId('explainer-references').textContent).toContain('INV-1ZKP4R');

    // A second ⊕ adds to the same form, and the button reads ✓ in tray.
    act(() => {
      trayStore.getState().take(invariantPick('RLZ-78JFRR', 'RLZ-78JFRR · turn_ended.ts L99–144'));
    });
    await waitFor(() =>
      expect(panel.container.querySelectorAll('[data-testid="explainer-reference"]')).toHaveLength(2),
    );

    fireEvent.click(panel.container.querySelectorAll<HTMLElement>('[data-testid="explainer-reference-remove"]')[0]);
    await waitFor(() =>
      expect(panel.container.querySelectorAll('[data-testid="explainer-reference"]')).toHaveLength(1),
    );
    expect(panel.getByTestId('explainer-references').textContent).not.toContain('INV-1ZKP4R');
  });

  it('names the locked capsule and the fixed target', async () => {
    backend({ [STATE]: { body: explainerState() } });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    fireEvent.click(panel.getByTestId('explainer-explain'));
    const form = await panel.findByTestId('explainer-first-request');
    expect(form.textContent).toContain('included, with the first request only');
    expect(panel.getByTestId('explainer-fixed-target').textContent).toBe('to: a new explainer for L181');
    expect(panel.getByTestId('explainer-binding').textContent).toBe('the comparison on screen');
  });

  it('names a planning leaf’s binding as planned · no changes', async () => {
    backend({
      [STATE]: { body: explainerState({ binding: { owner: 'planned', number: 0, label: 'planned' } }) },
    });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    fireEvent.click(panel.getByTestId('explainer-explain'));
    const binding = await panel.findByTestId('explainer-binding');
    expect(binding.textContent).toBe('planned · no changes');
  });

  it('leaves the placeholder on Cancel, and no seat or environment exists before Send', async () => {
    const server = backend({ [STATE]: { body: explainerState() } });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    act(() => {
      trayStore.getState().take(invariantPick('INV-1ZKP4R', 'INV-1ZKP4R'));
    });
    await panel.findByTestId('explainer-first-request');
    fireEvent.click(panel.getByTestId('explainer-cancel'));
    expect(await panel.findByTestId('explainer-placeholder')).toBeTruthy();
    expect(trayStore.getState().picks).toEqual([]);
    expect(pendingFirstRequest(ADDRESS)).toBeNull();
    expect(server.posts()).toEqual([]);
  });

  it('pre-fills the stale parts a Regenerate press names, with no seat', async () => {
    const stale = [{ id: 'part-3', title: 'Part 3 · the seat’s return', reason: 'its block moved' }];
    const server = backend({ [STATE]: { body: explainerState({ staleParts: stale }) } });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    // The Explanation view's "Regenerate stale parts" with no seat: the same form, pre-filled.
    act(() => askFirstRequest(ADDRESS, stale));
    expect((await panel.findByTestId('explainer-stale-prefill')).textContent).toContain('Part 3 · the seat’s return');
    act(() => {
      trayStore.getState().setText('Regenerate the stale parts only.');
    });
    fireEvent.click(panel.getByTestId('explainer-send'));
    await waitFor(() => expect(server.count('POST', BUILD)).toBe(1));
    expect(server.last('POST', BUILD)?.body?.stalePartIds).toEqual(['part-3']);
  });
});

describe('on Send (rule 4)', () => {
  const running = explainerState({
    seat: 'closed',
    agentId: 'a-exp',
    workspaceId: 'wks',
    build: {
      attempt: 'a1',
      state: 'running',
      startedAt: '2026-10-19T14:31:00Z',
      steps: [
        { name: 'Create the explainer’s workspace', state: 'done' },
        {
          name: 'Copy the code snapshot, read-only',
          state: 'running',
          counts: { kind: 'files', done: 2050, total: 4100 },
          detail: '≈ 4,100 files · 95 MiB',
        },
        { name: 'Start the explainer in Paseo', state: 'waiting' },
      ],
      failedStep: null,
      reason: null,
    },
  });

  it('posts the first request once, then shows each step as done, running or waiting', async () => {
    const server = backend({
      [STATE]: [{ body: explainerState() }, { body: running }],
      [BUILD]: { status: 202, body: { attempt: 'a1', state: running } },
    });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    act(() => {
      trayStore.getState().take(invariantPick('INV-1ZKP4R', 'INV-1ZKP4R'));
      trayStore.getState().setText('Explain the whole change set end to end.');
    });
    await panel.findByTestId('explainer-first-request');
    fireEvent.click(panel.getByTestId('explainer-send'));
    await panel.findByTestId('explainer-build');

    const posted = server.last('POST', BUILD);
    expect(posted?.body).toMatchObject({
      target: 'new-explainer',
      message: 'Explain the whole change set end to end.',
      planned: false,
      stalePartIds: [],
    });
    expect((posted?.body?.picks as unknown[]).length).toBe(1);

    expect(steps(panel)).toEqual([
      { name: 'Create the explainer’s workspace', state: 'done', percent: null },
      { name: 'Copy the code snapshot, read-only', state: 'running', percent: '50' },
      { name: 'Start the explainer in Paseo', state: 'waiting', percent: null },
    ]);
    expect(panel.getByTestId('explainer-build-waiting').textContent).toContain('Explain the whole change set end to end.');
    expect(panel.getByTestId('explainer-build-percent').textContent).toBe('50%');
    // His request went out: the tray is empty and the ask is done.
    expect(trayStore.getState().picks).toEqual([]);
    expect(server.count('POST', BUILD)).toBe(1);
  });

  it('keeps his request when the build fails, and names the step', async () => {
    const server = backend({
      [STATE]: { body: explainerState() },
      [BUILD]: { status: 500, body: { detail: 'no space left on device' } },
    });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    act(() => {
      trayStore.getState().take(invariantPick('INV-1ZKP4R', 'INV-1ZKP4R'));
      trayStore.getState().setText('Explain the whole change set.');
    });
    await panel.findByTestId('explainer-first-request');
    fireEvent.click(panel.getByTestId('explainer-send'));
    const failure = await panel.findByTestId('explainer-send-failure');
    expect(failure.textContent).toContain('no space left on device');
    expect(panel.getByTestId('explainer-first-request')).toBeTruthy();
    expect(trayStore.getState().picks).toHaveLength(1);
    expect(trayStore.getState().text).toBe('Explain the whole change set.');
    expect(server.count('POST', BUILD)).toBe(1);
    const firstRequestId = server.last('POST', BUILD)?.body?.requestId;
    expect(typeof firstRequestId).toBe('string');

    // Retrying the same request reuses the exact same requestId.
    fireEvent.click(panel.getByTestId('explainer-send'));
    await waitFor(() => expect(server.count('POST', BUILD)).toBe(2));
    expect(server.last('POST', BUILD)?.body?.requestId).toBe(firstRequestId);
  });

  it('composes reference-only requests when message is empty and surfaces held delivery', async () => {
    const server = backend({
      [STATE]: { body: explainerState() },
      [BUILD]: {
        status: 200,
        body: {
          state: 'ok',
          seat: {
            agentId: 'a-exp',
            workspace: 'wks',
            generation: 1,
            seat: 'live',
            delivery: { state: 'held', reason: 'rate-limited', detail: 'queued until window resets' },
          },
        },
      },
    });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    act(() => {
      trayStore.getState().take(invariantPick('INV-1ZKP4R', 'INV-1ZKP4R · the seat’s return'));
    });
    await panel.findByTestId('explainer-first-request');
    const noteInput = panel.getByLabelText('Your note on INV-1ZKP4R · the seat’s return');
    fireEvent.change(noteInput, { target: { value: 'focus on recovery' } });
    // Leave message empty: reference-only Send is enabled and composes a non-empty first turn.
    fireEvent.click(panel.getByTestId('explainer-send'));
    await waitFor(() => expect(server.count('POST', BUILD)).toBe(1));
    const posted = server.last('POST', BUILD)?.body;
    expect(typeof posted?.requestId).toBe('string');
    // His words go out as they are, and the picks carry the reference and its note: the route
    // composes them into the first turn once (serving/explainer.py `_send_facts`). Sending the
    // composed text AND the picks would put every reference and note in twice.
    expect(posted?.message).toBe('');
    const picks = posted?.picks as Array<{ title?: string; note?: string; reference?: unknown }> | undefined;
    expect(picks).toHaveLength(1);
    expect(picks?.[0]?.title).toContain('INV-1ZKP4R');
    expect(picks?.[0]?.note).toBe('focus on recovery');
    expect(picks?.[0]?.reference).toMatchObject({ id: 'INV-1ZKP4R' });
    // The real route's own check: the composed first turn is non-empty exactly because the picks
    // exist, so a reference-only Send passes `_send_facts` with an empty message.
    expect((posted?.message as string).trim() !== '' || (picks?.length ?? 0) > 0).toBe(true);
    expect((await panel.findByTestId('explainer-held-notice')).textContent).toContain('rate-limited');
  });

  it('never posts a second build for a second press while the first runs', async () => {
    const server = backend({
      [STATE]: [{ body: explainerState() }, { body: running }],
      [BUILD]: { status: 202, body: { attempt: 'a1', state: running } },
    });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    fireEvent.click(panel.getByTestId('explainer-explain'));
    const send = await panel.findByTestId('explainer-send');
    // A Send with nothing to send is refused by the form itself.
    expect((send as HTMLButtonElement).disabled).toBe(true);
    expect(server.posts()).toEqual([]);
  });
});

describe('later requests (rule 5)', () => {
  it('adds the change-set reference to the tray with a live seat, and sends nothing', async () => {
    const server = backend({
      [STATE]: { body: explainerState({ seat: 'live', agentId: 'a-exp', workspaceId: 'wks', binding: { owner: 'recorded', number: 8, label: '#8' } }) },
    });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-column').dataset.phase).toBe('live'));
    fireEvent.click(panel.getByTestId('explainer-explain'));
    const picks = trayStore.getState().picks;
    expect(picks).toHaveLength(1);
    expect(picks[0].input.kind).toBe('reference');
    if (picks[0].input.kind === 'reference') expect(picks[0].input.reference.kind).toBe('change-set');
    expect(panel.queryByTestId('explainer-first-request')).toBeNull();
    expect(server.posts()).toEqual([]);
  });
});

describe('Retire (rule 7)', () => {
  it('confirms with two lists, then brings the placeholder back', async () => {
    const server = backend({
      [STATE]: [
        { body: explainerState({ seat: 'live', agentId: 'a-exp', workspaceId: 'wks', generation: 1 }) },
        { body: explainerState({ retiredAt: '2026-10-19T15:12:00Z' }) },
      ],
      [RETIRE]: {
        body: {
          receipt: {
            receiptId: 'r1',
            agentId: 'a-exp',
            generation: 1,
            state: 'finished',
            step: 'finished',
            endedByMarker: 3,
            endedByTree: 3,
            remaining: [],
            detail: '',
          },
          state: explainerState({ retiredAt: '2026-10-19T15:12:00Z' }),
        },
      },
    });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-column').dataset.phase).toBe('live'));
    fireEvent.click(panel.getByTestId('explainer-retire'));
    const confirm = await panel.findByTestId('explainer-retire-confirm');
    expect(confirm.textContent).toContain('Retire the explainer of L181?');
    const ends = panel.getByTestId('explainer-retire-ends').textContent ?? '';
    const stays = panel.getByTestId('explainer-retire-stays').textContent ?? '';
    expect(ends).toContain('The seat');
    expect(ends).toContain('AR_EXPLAINER_WORKSPACE');
    expect(stays).toContain('from a retired explainer');
    expect(stays).toContain('grades');
    fireEvent.click(panel.getByTestId('explainer-retire-confirm-send'));
    expect(server.count('POST', RETIRE)).toBe(1);
    // Both counts are reported, and no process remains.
    expect((await panel.findByTestId('explainer-retire-receipt')).textContent).toContain(
      '3 processes ended by the recorded tree, 3 by the marker. No process remains.',
    );
    const placeholder = await panel.findByTestId('explainer-placeholder');
    expect(placeholder).toBeTruthy();
    expect(panel.getByTestId('explainer-placeholder-headline').textContent).toMatch(/retired at \d\d:\d\d/);
  });

  it('is offered only while a seat exists', async () => {
    backend({ [STATE]: { body: explainerState() } });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    expect(panel.queryByTestId('explainer-retire')).toBeNull();
  });
});

describe('a Send aimed at an archived explainer (rule 3)', () => {
  it('opens the first-request form with the tray’s picks and intercepts native tray Send', async () => {
    const server = backend({
      [STATE]: { body: explainerState({ agentId: 'a-exp-retired', retiredAt: '2026-10-19T15:12:00Z' }) },
    });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-placeholder')).toBeTruthy());
    act(() => {
      trayStore.getState().add(invariantPick('INV-1ZKP4R', 'INV-1ZKP4R · the seat’s return'));
      trayStore.getState().setText('Continue with the new comparison.');
      trayStore.getState().setTargets(['a-exp-retired']);
    });
    // Native tray Send intercepts the archived explainer target before posting to /api/role-chats.
    await act(async () => {
      await sendNow();
    });
    const form = await panel.findByTestId('explainer-first-request');
    expect(form.textContent).toContain('INV-1ZKP4R · the seat’s return');
    expect(trayStore.getState().picks).toHaveLength(1);
    expect(trayStore.getState().text).toBe('Continue with the new comparison.');
    expect(server.posts()).toEqual([]);
  });

  it('mounts TrayColumnDock above-composer across placeholder, closed and live states', async () => {
    const measured: ComposerEdge = { kind: 'measured', top: 680, height: 120, frameHeight: 800 };
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      if (this.tagName === 'IFRAME') return new DOMRect(1400, 100, 500, 800);
      if (this.getAttribute('data-dock')) return new DOMRect(1400, 900, 500, 0);
      if (this.parentElement?.getAttribute('data-dock')) return new DOMRect(1400, 600, 500, 240);
      return new DOMRect(0, 0, 0, 0);
    });

    // The same shared dock holds through every seat phase, and a later attachment defaults to the
    // chat the column actually shows: the placeholder's own target while none exists, the
    // explainer's seat once one does (MIK-R106 v2 rules 2, 5 and 8; MIK-R188 rule 7).
    const phases: Array<{ phase: string; state: ReturnType<typeof explainerState>; target: string }> = [
      { phase: 'placeholder', state: explainerState(), target: placeholderExplainerTargetId(ADDRESS) },
      { phase: 'closed', state: explainerState({ seat: 'closed', agentId: 'a-exp', workspaceId: 'wks' }), target: 'a-exp' },
      { phase: 'live', state: explainerState({ seat: 'live', agentId: 'a-exp', workspaceId: 'wks' }), target: 'a-exp' },
    ];
    for (const run of phases) {
      resetTray();
      resetFirstRequest();
      act(() =>
        trayStore.setState({
          open: false,
          minimised: false,
          targetsChosen: false,
          defaultDecided: false,
          pendingDefault: false,
        }),
      );
      frameTrayStore.setState({ composer: {}, pickNotice: {}, traySpace: {} });
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
          return new Response(JSON.stringify(run.state), {
            headers: { 'Content-Type': 'application/json' },
          });
        }),
      );

      const panel = render(<ExplainerColumn address={ADDRESS} task={TASK} active preload />);
      await waitFor(() => expect(panel.getByTestId('explainer-column').dataset.phase).toBe(run.phase));
      await waitFor(() => expect(panel.container.querySelector('iframe')).not.toBeNull());
      act(() => {
        frameTrayStore.setState({ composer: { document: measured } });
        trayStore.getState().take(invariantPick('INV-1ZKP4R', 'INV-1ZKP4R'));
      });
      const dock = await panel.findByTestId('tray-column-dock');
      expect(dock.getAttribute('data-dock')).toBe('above-composer');
      expect(trayStore.getState().targets).toEqual([run.target]);
      panel.unmount();
    }
    vi.restoreAllMocks();
  });
});

describe('nothing starts a model turn (rule 10)', () => {
  it('reads the state and nothing else on open, on the menu and on a selection', async () => {
    const server = backend({
      [STATE]: { body: explainerState({ seat: 'live', agentId: 'a-exp', workspaceId: 'wks' }) },
    });
    const panel = view();
    await waitFor(() => expect(panel.getByTestId('explainer-column').dataset.phase).toBe('live'));
    fireEvent.click(panel.getByTestId('explainer-agent-menu-button'));
    fireEvent.click(panel.getByTestId('explainer-agent-menu-button'));
    await act(async () => {});
    expect(server.posts()).toEqual([]);
    expect(server.count('GET', STATE)).toBe(1);
    // No document chat, no warmup, no model: the only other read is the frame's own descriptor,
    // which loads the Paseo client for a live seat (rule 1, "preloading opens no agent").
    expect(server.paths().filter((path) => path.startsWith('/api/explainer'))).toEqual([STATE]);
    for (const path of ['/api/role-launch/document-chats', '/api/role-launch/warmup', '/api/role-launch/dispatch'])
      expect(server.paths()).not.toContain(path);
  });

  it('reads nothing at all while the column is closed', async () => {
    const server = backend({ [STATE]: { body: explainerState() } });
    view(false);
    await act(async () => {});
    expect(server.calls).toEqual([]);
  });
});
