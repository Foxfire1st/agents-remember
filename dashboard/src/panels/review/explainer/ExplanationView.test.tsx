// The Explanation view, end to end (MIK-R106 v2 rule 6; MIK-R104 v3; MIK-R103 v2).
//
// One case per promise a reader can check:
//   * no explanation yet: the view says so with exactly one button, and pressing it starts nothing
//     here (it opens the chat's first-request form);
//   * during the build: a still placeholder that points at the chat, and no page;
//   * a saved page opens with no model -- one isolated-host channel for one revision, and no
//     generate, no build and no message;
//   * the column is a record: the page, its standfirst and its revision selector, and no staleness;
//   * staleness -- the parts, their reasons and the newer comparison -- is the top line's badge,
//     drawn from the same answer whatever the frame draws, or fails to;
//   * Compare mounts two isolated frames;
//   * the task-total refusal names itself and offers only unpinned revisions to remove;
//
// Assertions are plain DOM assertions: this suite loads no jest-dom matchers.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { resetCardCachesForTests } from '../../../data/reviewTrees';
import { pendingFirstRequest, resetFirstRequest } from '../../../explainer/request';
import { resetTray } from '../../../explainer/testSupport';
import { trayStore } from '../../../tray/trayStore';
import { forwardedBody } from '../canvas/canvasHooks';
import {
  PLACE,
  renderPage,
  servePage,
  stubIntersection,
  waitForOpening,
} from '../reading/reviewPage.test-utils';
import { treeOrderStore } from '../triageOrderPreference';
import { treeViewStore } from '../triageViewPreference';
import { ExplanationSwitch } from './ExplanationSwitch';
import { ExplanationView } from './ExplanationView';
import { RevisionFrame } from './RevisionFrame';

import {
  BASE,
  text,
  json,
  serve,
  channel,
  SAVED_STATE,
  REVISIONS,
  server,
  onExplain,
  props,
  badgeBriefOf,
} from './ExplanationView.test-utils';

afterEach(() => {
  resetTray();
  resetFirstRequest();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('no explanation yet', () => {
  it('says so with exactly one button, and pressing it starts nothing', async () => {
    const calls = server({
      state: {
        ...SAVED_STATE,
        explanation: null,
        seat: null,
        binding: { kind: 'planned', comparison: 0, label: 'planned' },
        stale: { parts: [] },
        claims: [],
        parts: [],
      },
      revisions: { state: 'ok', revisions: [], pinned: null },
    });
    render(<ExplanationView {...props} />);

    const empty = await screen.findByTestId('explanation-empty');
    const buttons = within(empty).getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(text(buttons[0])).toBe('✦ Explain the change set');
    expect(text(empty)).toContain('Nothing runs until you ask.');

    fireEvent.click(buttons[0]);
    expect(onExplain).toHaveBeenCalledTimes(1);

    // No frame, no session, no post of any kind: the press belongs to the chat, not to this view.
    expect(screen.queryAllByTestId('explanation-frame')).toHaveLength(0);
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0);
  });
});

describe('during the build', () => {
  it('shows a still placeholder that points at the chat, and no page', async () => {
    const calls = server({
      state: {
        ...SAVED_STATE,
        explanation: null,
        building: { state: 'building', step: 'Copy the code snapshot' },
        stale: { parts: [] },
      },
      revisions: { state: 'ok', revisions: [], pinned: null },
    });
    render(<ExplanationView {...props} />);

    const building = await screen.findByTestId('explanation-building');
    expect(building.getAttribute('aria-busy')).toBe('true');
    expect(text(screen.getByTestId('explanation-building-line'))).toContain(
      'the progress is in the chat',
    );
    expect(screen.queryByTestId('explain-change-set')).toBeNull();
    expect(screen.queryAllByTestId('explanation-frame')).toHaveLength(0);
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0);
  });
});

describe('a saved page', () => {
  it('puts its frame container in fullscreen and follows entry and exit', async () => {
    server();
    render(<ExplanationView {...props} />);
    await screen.findByTestId('explanation-frame');
    const shell = screen.getByTestId('explanation-frame-slot');
    const button = within(shell).getByTestId('explanation-frame-fullscreen');
    let fullscreenElement: Element | null = null;
    const originalElement = Object.getOwnPropertyDescriptor(document, 'fullscreenElement');
    const originalExit = Object.getOwnPropertyDescriptor(document, 'exitFullscreen');
    const change = (element: Element | null) => {
      fullscreenElement = element;
      document.dispatchEvent(new Event('fullscreenchange'));
      return Promise.resolve();
    };
    const request = vi.fn(() => change(shell));
    const exit = vi.fn(() => change(null));
    Object.defineProperty(shell, 'requestFullscreen', { value: request });
    Object.defineProperties(document, {
      fullscreenElement: { configurable: true, get: () => fullscreenElement },
      exitFullscreen: { configurable: true, value: exit },
    });
    try {
      expect(button.getAttribute('aria-pressed')).toBe('false');
      fireEvent.click(button);
      expect(request).toHaveBeenCalledOnce();
      expect(button.getAttribute('aria-pressed')).toBe('true');
      expect(text(button)).toBe('esc to leave full screen');

      fireEvent.click(button);
      expect(exit).toHaveBeenCalledOnce();
      expect(button.getAttribute('aria-pressed')).toBe('false');
      expect(text(button)).toBe('full screen');

      fireEvent.click(button);
      act(() => void change(null));
      expect(button.getAttribute('aria-pressed')).toBe('false');
      expect(text(button)).toBe('full screen');
    } finally {
      if (originalElement) Object.defineProperty(document, 'fullscreenElement', originalElement);
      else Reflect.deleteProperty(document, 'fullscreenElement');
      if (originalExit) Object.defineProperty(document, 'exitFullscreen', originalExit);
      else Reflect.deleteProperty(document, 'exitFullscreen');
    }
  });

  it('opens in one isolated frame with no model: a channel for its revision and nothing else', async () => {
    const calls = server();
    render(
      <ExplanationView
        {...props}
        headerDetails={<span data-testid="technical-slot">Technical details</span>}
      />,
    );

    const frame = await screen.findByTestId('explanation-frame');
    // One frame instance of MIK-R104 v3's host, on the saved revision exactly.
    const sessions = calls.filter((call) => call.url === '/api/canvas/sessions');
    expect(sessions).toHaveLength(1);
    expect(sessions[0].body?.revision).toBe(8);
    expect(frame.getAttribute('data-revision')).toBe('8');
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
    expect(frame.getAttribute('src')).toBe('/canvas/page/s1');

    // Nothing that could start an agent or a model was called.
    expect(calls.some((call) => /generate|build|messages|\/say/.test(call.url))).toBe(false);
    expect(calls.some((call) => call.url === `${BASE}/update`)).toBe(false);
    expect(text(screen.getByTestId('explanation-standfirst'))).toContain(
      'revision 8 · bound to #8',
    );
    const header = screen.getByTestId('explanation-frame-header');
    expect(within(header).getByTestId('technical-slot')).toBeTruthy();
    const menu = within(header).getByTestId('revision-controls') as HTMLDetailsElement;
    expect(menu.open).toBe(false);
    expect(within(menu).getByTestId('revision-selector')).toBeTruthy();
  });

  it('draws its record and no stale list whatever the frame draws, and the badge holds the parts', async () => {
    server();
    const brief = await badgeBriefOf();
    const { unmount } = render(
      <>
        <ExplanationSwitch
          view="explanation"
          stale={brief.total}
          brief={brief}
          onSwitch={vi.fn()}
        />
        <ExplanationView {...props} />
      </>,
    );
    await screen.findByTestId('explanation-frame');
    const column = screen.getByTestId('explanation-view');
    // F18: the column is the revision's record -- no "Stale parts" block, no rows, no regenerate.
    expect(within(column).queryByTestId('stale-parts')).toBeNull();
    expect(within(column).queryAllByTestId('stale-part')).toHaveLength(0);
    expect(within(column).queryByTestId('regenerate-stale')).toBeNull();
    expect(within(column).getByTestId('revision-controls')).toBeTruthy();

    // The badge holds the trusted list instead: five parts, three listed, with their reasons.
    fireEvent.click(screen.getByTestId('explanation-stale-mark'));
    expect(screen.getByTestId('explanation-stale-badge').getAttribute('data-count')).toBe('5');
    expect(screen.getAllByTestId('stale-part')).toHaveLength(3);
    expect(text(screen.getByTestId('explanation-stale-details'))).toContain('Risks I see');
    expect(text(screen.getByTestId('explanation-stale-details'))).toContain(
      'the grades of 4 changed blocks',
    );
    expect(text(screen.getByTestId('stale-more'))).toContain('+ 2 more stale parts');
    unmount();

    // The page cannot be shown at all: on the mounted review page, whose top line badge is wired
    // from the page's own read (useHeaderStaleness), the badge still holds the trusted list.
    const page = `/api/explainer/${PLACE.repo}/${PLACE.master}/${PLACE.leaf}`;
    window.localStorage.clear();
    window.history.replaceState(
      null,
      '',
      `/#review?${new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf })}`,
    );
    resetCardCachesForTests();
    treeViewStore.getState().setView('changed');
    treeOrderStore.getState().setOrder('priority');
    stubIntersection();
    vi.unstubAllGlobals();
    servePage();
    const baseFetch = globalThis.fetch;
    const sessions: unknown[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL, init?: RequestInit) => {
        const url = new URL(String(input), 'http://localhost');
        if (url.pathname === `${page}/state`) return json(SAVED_STATE);
        if (url.pathname === `${page}/revisions`) return json(REVISIONS);
        if (url.pathname === '/api/canvas/sessions') {
          const body = JSON.parse(String(init?.body)) as { revision?: number };
          sessions.push(body);
          return json({ state: 'refused', reason: 'no-ready-revision', detail: `r${body.revision}` }, 404);
        }
        return baseFetch(input, init);
      }),
    );
    try {
      renderPage({ chat: { open: false, toggle: vi.fn(), expand: vi.fn() } });
      await waitForOpening();
      fireEvent.click(screen.getByTestId('switch-explanation'));
      expect(text(await screen.findByTestId('explanation-frame-problem'))).toContain(
        'no-ready-revision',
      );
      expect(sessions.length).toBeGreaterThan(0);
      expect(screen.queryByTestId('explanation-frame')).toBeNull();
      expect(within(screen.getByTestId('explanation-view')).queryByTestId('stale-parts')).toBeNull();
      fireEvent.click(await screen.findByTestId('explanation-stale-mark'));
      expect(screen.getByTestId('explanation-stale-badge').getAttribute('data-count')).toBe('5');
      expect(screen.getAllByTestId('stale-part')).toHaveLength(3);
      expect(text(screen.getByTestId('explanation-stale-details'))).toContain('Risks I see');
      expect(text(screen.getByTestId('explanation-stale-details'))).toContain(
        'the grades of 4 changed blocks',
      );
      expect(text(screen.getByTestId('stale-more'))).toContain('+ 2 more stale parts');
    } finally {
      cleanup();
    }
  });
});

describe('the revision selector in the view', () => {
  it('at the task total, names the refusal and removes only what the developer chose', async () => {
    const calls = server({
      state: {
        ...SAVED_STATE,
        limit: {
          state: 'refused',
          reason: 'frame-store-full',
          detail: 'the task total (512 MiB) is full',
        },
      },
    });
    render(<ExplanationView {...props} />);

    expect(text(await screen.findByTestId('revision-refusal-name'))).toContain(
      'frame-store-full: the task total (512 MiB) is full',
    );
    fireEvent.click(screen.getByTestId('remove-old-revisions'));
    const choices = screen.getAllByTestId('remove-choice');
    expect(choices.map((choice) => choice.getAttribute('data-revision'))).toEqual(['7', '9']);
    expect(choices.some((choice) => choice.querySelector('input#remove-8') !== null)).toBe(false);

    fireEvent.click(within(choices[1]).getByRole('checkbox'));
    fireEvent.click(screen.getByTestId('remove-selected'));

    await waitFor(() =>
      expect(calls.filter((call) => call.url === `${BASE}/remove-revisions`)).toHaveLength(1),
    );
    const removal = calls.find((call) => call.url === `${BASE}/remove-revisions`);
    expect(removal?.method).toBe('POST');
    expect(removal?.body).toEqual({ revisions: [9] });
    // The view re-reads after the removal, so it never shows a revision the host no longer holds.
    await waitFor(() =>
      expect(calls.filter((call) => call.url.startsWith(`${BASE}/state`)).length).toBeGreaterThan(
        1,
      ),
    );
  });

  it('Compare mounts two isolated frames, one per revision', async () => {
    const calls = server();
    render(<ExplanationView {...props} />);
    await screen.findByTestId('explanation-frame');
    fireEvent.click(screen.getByTestId('compare'));

    const frames = await screen.findAllByTestId('explanation-frame');
    expect(frames).toHaveLength(2);
    const revisions = frames.map((frame) => frame.getAttribute('data-revision')).sort();
    expect(revisions).toEqual(['8', '9']);
    // The single frame is replaced by the pair: its channel is revoked, and the two frames open
    // their own channels, one channel per revision exactly, each bound to its compare slot.
    const opened = calls
      .filter((call) => call.url === '/api/canvas/sessions')
      .map((call) => call.body);
    expect(
      opened
        .slice(-2)
        .map((body) => body?.revision)
        .sort(),
    ).toEqual([8, 9]);
    expect(
      opened
        .slice(-2)
        .map((body) => body?.compareSlot)
        .sort(),
    ).toEqual(['left', 'right']);
    // Neither compare channel is substituted by the latest revision: each asks for exactly its
    // own retained revision and its own binding.
    const leftCall = opened.slice(-2).find((body) => body?.compareSlot === 'left');
    const rightCall = opened.slice(-2).find((body) => body?.compareSlot === 'right');
    expect(leftCall?.revision).toBe(9);
    expect(leftCall?.comparisonNumber).toBe(9);
    expect(rightCall?.revision).toBe(8);
    expect(rightCall?.comparisonNumber).toBe(8);
    expect(calls.filter((call) => call.url.endsWith('/revoke')).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId('explanation-compare')).not.toBeNull();
    expect(text(screen.getByTestId('compare-line'))).toContain('its own isolated frame');
    // Two frames of the same isolated host: one sandbox policy, two different documents.
    expect(new Set(frames.map((frame) => frame.getAttribute('src'))).size).toBe(2);
  });
});

describe('the moved leaf', () => {
  it('names the newer comparison in the badge, and the column neither notices nor rebinds by itself', async () => {
    const calls = server({
      state: { ...SAVED_STATE, moved: { from: 8, to: 9, blocks: 4 } },
    });
    const brief = await badgeBriefOf();
    render(
      <>
        <ExplanationSwitch
          view="explanation"
          stale={brief.total}
          brief={brief}
          onSwitch={vi.fn()}
        />
        <ExplanationView {...props} />
      </>,
    );

    await screen.findByTestId('explanation-frame');
    // The notice is gone from the column (F18): its reason is the badge's, in the page's own words.
    expect(screen.queryByTestId('moved-notice')).toBeNull();
    expect(screen.queryByTestId('moved-line')).toBeNull();
    expect(screen.queryByTestId('update')).toBeNull();
    fireEvent.click(screen.getByTestId('explanation-stale-mark'));
    expect(text(screen.getByTestId('newer-comparison'))).toBe(
      'A newer comparison exists: Task moved: #8 → #9 · 4 blocks changed',
    );

    // Reading the page and opening the badge press nothing: the rebind route is never asked, and no
    // model, build or message is started.
    expect(calls.filter((call) => call.url === `${BASE}/update`)).toHaveLength(0);
    expect(calls.filter((call) => /generate|build|messages/.test(call.url))).toHaveLength(0);
  });

  it('draws no notice and no badge when no explainer has worked: there is nothing to say', async () => {
    const calls = server({
      state: {
        ...SAVED_STATE,
        explanation: null,
        seat: null,
        moved: { from: 8, to: 9, blocks: 4 },
        stale: { parts: [] },
        claims: [],
      },
      revisions: { state: 'ok', revisions: [], pinned: null },
    });
    const brief = await badgeBriefOf();
    render(
      <>
        <ExplanationSwitch
          view="explanation"
          stale={brief.total}
          brief={brief}
          onSwitch={vi.fn()}
        />
        <ExplanationView {...props} />
      </>,
    );

    // No page, no grade, nothing stale: no part is claimed, so the top line marks nothing.
    expect(brief.total).toBe(0);
    expect(screen.queryByTestId('explanation-stale-badge')).toBeNull();
    expect(screen.queryByTestId('explanation-stale-mark')).toBeNull();
    expect(screen.queryByTestId('moved-notice')).toBeNull();
    expect(screen.queryByTestId('update')).toBeNull();
    expect(await screen.findByTestId('explanation-empty')).toBeTruthy();
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0);
  });
});

describe('a read that fails', () => {
  it('names the problem and offers Retry, never an empty explanation', async () => {
    serve((call) =>
      call.url.startsWith(`${BASE}/state`)
        ? json(
            {
              state: 'refused',
              reason: 'no-environment',
              detail: 'the leaf has no explainer workspace',
            },
            503,
          )
        : json({ state: 'ok', revisions: [], pinned: null }),
    );
    render(<ExplanationView {...props} />);

    const failed = await screen.findByTestId('explanation-unavailable');
    expect(text(failed)).toContain('no-environment');
    expect(text(failed)).toContain('the leaf has no explainer workspace');
    expect(screen.queryByTestId('explanation-empty')).toBeNull();
    expect(screen.queryByTestId('explanation-retry')).not.toBeNull();
  });
});

describe('revision & comparison re-reads, compare dual metadata, and selective regeneration (MIK-R103 v2; MIK-R104 v3; MIK-R187)', () => {
  it('requests frame ID "explainer" with compareSlot and full binding, and re-reads state when revision or comparison changes', async () => {
    const calls = serve((call, _calls, counter) => {
      if (call.url.startsWith(`${BASE}/state`)) {
        const search = new URL(call.url, 'http://localhost').searchParams;
        const rev = Number(search.get('revision') ?? 8);
        return json({
          ...SAVED_STATE,
          explanation: { ...SAVED_STATE.explanation, revision: rev },
          claims: [
            {
              id: `claim-r${rev}`,
              part: 'answer',
              basis: 'recorded-fact',
              text: `claim text for revision ${rev}`,
            },
          ],
          // Each revision's own stale part, so the frame's list can name the revision it belongs to.
          stale: {
            total: 1,
            parts: [
              {
                id: rev === 7 ? 'risks' : 'look',
                title: `Stale in r${rev}`,
                reason: `reason r${rev}`,
              },
            ],
          },
        });
      }
      if (call.url === `${BASE}/revisions`) return json(REVISIONS);
      if (call.url === '/api/canvas/sessions') {
        counter.sessions += 1;
        return json(channel(`s${counter.sessions}`, Number(call.body?.revision ?? 1)));
      }
      if (call.url.endsWith('/revoke')) return json({ state: 'ok', revoked: true });
      return json({ state: 'ok' });
    });

    const view = render(<ExplanationView {...props} />);
    await screen.findByTestId('explanation-frame');
    // The staleness the frame is told on its ready: the shown revision's own stale parts.
    const staleSentOnReady = () => {
      const frame = screen.getByTestId('explanation-frame') as HTMLIFrameElement;
      const post = vi.spyOn(frame.contentWindow!, 'postMessage');
      act(() => {
        window.dispatchEvent(
          new MessageEvent('message', {
            source: frame.contentWindow,
            data: { type: 'ready', requestId: `req-ready-${frame.getAttribute('data-revision')}` },
          }),
        );
      });
      const sent = post.mock.calls
        .map(([envelope]) => envelope as { type: string; revision: number; payload: { parts: string[] } })
        .filter((envelope) => envelope.type === 'stale-parts');
      post.mockRestore();
      // The ready and the parts' own effect may both send it; each envelope says the same.
      return [...new Set(sent.map((envelope) => JSON.stringify([envelope.revision, envelope.payload.parts])))]
        .map((one) => JSON.parse(one) as [number, string[]]);
    };
    expect(staleSentOnReady()).toEqual([[8, ['look']]]);

    const firstSession = calls.find((call) => call.url === '/api/canvas/sessions')!;
    expect(firstSession.body?.frame).toBe('explainer');
    expect(firstSession.body?.compareSlot).toBe('primary');
    expect(firstSession.body?.revision).toBe(8);
    expect(firstSession.body?.comparisonNumber).toBe(8);
    expect(text(screen.getByTestId('explanation-standfirst'))).toContain(
      'revision 8 · bound to #8',
    );

    // Switch to historical revision 7: re-reads state for revision 7 and updates the standfirst. The
    // stale list is the top line's badge (F18), so the column draws none for either revision.
    const revButtons = screen.getAllByTestId('revision-show');
    fireEvent.click(revButtons[0]);

    await waitFor(() =>
      expect(text(screen.getByTestId('explanation-standfirst'))).toContain(
        'revision 7 · bound to #7',
      ),
    );
    expect(
      calls.some(
        (call) =>
          call.url.startsWith(`${BASE}/state`) &&
          new URL(call.url, 'http://localhost').searchParams.get('revision') === '7',
      ),
    ).toBe(true);
    expect(screen.queryByTestId('stale-parts')).toBeNull();
    // The staleness shown with revision 7 is revision 7's: its frame is told r7's stale part.
    await waitFor(() =>
      expect(screen.getByTestId('explanation-frame').getAttribute('data-revision')).toBe('7'),
    );
    await waitFor(() => expect(staleSentOnReady()).toEqual([[7, ['risks']]]));

    // Change comparison prop: re-reads state with the new comparisonNumber.
    view.rerender(
      <ExplanationView
        {...props}
        comparison={{
          reference: 'review-comparison:task/leaf/10',
          number: 10,
          owner: 'recorded',
        }}
      />,
    );
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url.startsWith(`${BASE}/state`) && call.url.includes('comparisonNumber=10'),
        ),
      ).toBe(true),
    );

    // Compare mode renders a standfirst for each compared revision, and no stale list in either.
    fireEvent.click(screen.getByTestId('compare'));
    const compareCols = await screen.findAllByTestId('explanation-compare-column');
    expect(compareCols).toHaveLength(2);
    for (const column of compareCols) {
      expect(within(column).getByTestId('explanation-standfirst')).toBeTruthy();
      expect(within(column).queryByTestId('stale-parts')).toBeNull();
    }
  });

  it('sends selective regeneration once when live, and opens prefilled parts+grades form when no seat or archived, preserving state on failure', async () => {
    // F18: the one refresh control is the top line badge's, wired by the mounted review page, so
    // this case runs through ReviewSurface rather than the column alone.
    const address = { repository: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf };
    const page = `/api/explainer/${PLACE.repo}/${PLACE.master}/${PLACE.leaf}`;
    window.localStorage.clear();
    window.history.replaceState(
      null,
      '',
      `/#review?${new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf })}`,
    );
    resetCardCachesForTests();
    treeViewStore.getState().setView('changed');
    treeOrderStore.getState().setOrder('priority');
    stubIntersection();
    let seat: 'live' | 'archived' = 'live';
    let failBuild = false;
    const builds: Array<Record<string, unknown>> = [];
    servePage();
    const baseFetch = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL, init?: RequestInit) => {
        const url = new URL(String(input), 'http://localhost');
        const saved = {
          ...SAVED_STATE,
          seat: { state: seat },
          retired: seat === 'archived',
          stale: {
            ...SAVED_STATE.stale,
            staleGrades: ['RLZ-10', 'RLZ-11'],
            regeneration: {
              label: 'Regenerate stale (3 parts · 2 grades)',
              message: 'Regenerate revision 8: 3 parts and 2 grades are stale.',
              partCount: 3,
              gradeCount: 2,
            },
          },
        };
        // The Review tab's badge reads `/stale` alone: the same staleness, no seat (D569).
        if (url.pathname === `${page}/stale`) {
          return json({ state: 'ok', stale: saved.stale, ...saved.stale, explanation: saved.explanation, movedNotice: null });
        }
        if (url.pathname === `${page}/state`) return json(saved);
        if (url.pathname === `${page}/revisions`) return json(REVISIONS);
        if (url.pathname === `${page}/build`) {
          builds.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
          if (failBuild) {
            return json({ state: 'refused', reason: 'host-busy', detail: 'explainer is busy' }, 409);
          }
          return json({ state: 'ok', attempt: 'regen-1' });
        }
        if (url.pathname === '/api/canvas/sessions') return json(channel('s1', 8));
        if (url.pathname.endsWith('/revoke')) return json({ state: 'ok', revoked: true });
        return baseFetch(input, init);
      }),
    );
    const chat = { open: false, toggle: vi.fn(), expand: vi.fn() };

    try {
      const live = renderPage({ chat });
      await waitForOpening();
      fireEvent.click(screen.getByTestId('switch-explanation'));
      await screen.findByTestId('explanation-frame');
      fireEvent.click(await screen.findByTestId('explanation-stale-mark'));
      const regenBtn = screen.getByTestId('regenerate-stale');
      expect(text(regenBtn)).toBe('Regenerate stale (3 parts · 2 grades)');

      // Failure preserves the saved revision and stale marks.
      failBuild = true;
      fireEvent.click(regenBtn);
      expect(text(await screen.findByTestId('regenerate-stale-problem'))).toContain(
        'host-busy: explainer is busy',
      );
      expect(screen.getByTestId('explanation-frame').getAttribute('data-revision')).toBe('8');
      expect(screen.getAllByTestId('stale-part')).toHaveLength(3);

      // Live success sends selective message once.
      failBuild = false;
      fireEvent.click(screen.getByTestId('regenerate-stale'));
      await waitFor(() => expect(builds).toHaveLength(2));
      expect(builds[1]).toMatchObject({
        message: 'Regenerate revision 8: 3 parts and 2 grades are stale.',
        stalePartIds: ['look', 'risks', 'flow'],
        staleGrades: ['RLZ-10', 'RLZ-11'],
        regeneration: true,
      });
      live.unmount();

      // Archived / no-seat case: opens first-request form prefilled with both stale parts and stale grades, no POST.
      seat = 'archived';
      renderPage({ chat });
      await waitForOpening();
      fireEvent.click(await screen.findByTestId('explanation-stale-mark'));
      fireEvent.click(screen.getByTestId('regenerate-stale'));

      // From the Review tab the seat is read at the press, so the form opens once that read answers.
      await waitFor(() => expect(chat.expand).toHaveBeenCalledTimes(1));
      const pending = pendingFirstRequest(address);
      expect(pending?.staleParts.map((p) => p.id)).toEqual([
        'look',
        'risks',
        'flow',
        'RLZ-10',
        'RLZ-11',
      ]);
      expect(trayStore.getState().text).toBe(
        'Regenerate revision 8: 3 parts and 2 grades are stale.',
      );
      expect(builds).toHaveLength(2);
    } finally {
      cleanup();
    }
  });
});

describe('frame protocol: stale-parts, offer-selections, request-explanation, and per-frame navigation revocation (MIK-R104 v3)', () => {
  it('sends stale-parts with dropped/truncated counts, clears when empty, and handles offer-selections (1/s rate-limited, replacing pick) and request-explanation', async () => {
    expect(
      forwardedBody({
        type: 'offer-selections',
        payload: {
          selections: [{ question: 'Which path?', choice: 'Fast path' }],
        },
      }),
    ).not.toBeNull();

    serve((call, _calls, counter) => {
      if (call.url.startsWith(`${BASE}/state`)) {
        return json({
          ...SAVED_STATE,
          explainer: {
            agentId: 'explainer-seat-1',
            workspace: '/tmp/wks',
            generation: 1,
          },
          stale: {
            total: 4,
            parts: [
              { id: 'look', title: 'Where to look first', reason: 'changed' },
              {
                id: 'unknown-part',
                title: 'Unknown',
                reason: 'not in part map',
              },
            ],
          },
        });
      }
      if (call.url === `${BASE}/revisions`) return json(REVISIONS);
      if (call.url === '/api/canvas/sessions') {
        counter.sessions += 1;
        return json(channel(`s${counter.sessions}`, Number(call.body?.revision ?? 1)));
      }
      if (call.url.endsWith('/messages')) {
        return json({ state: 'accepted', operation: 'offer-selections' });
      }
      if (call.url.endsWith('/revoke')) return json({ state: 'ok', revoked: true });
      return json({ state: 'ok' });
    });

    render(<ExplanationView {...props} />);
    const frame = (await screen.findByTestId('explanation-frame')) as HTMLIFrameElement;
    const postSpy = vi.spyOn(frame.contentWindow!, 'postMessage');

    // Ready triggers stale-parts host message, dropping unknown-part not in part map.
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: frame.contentWindow,
          data: { type: 'ready', requestId: 'req-ready-1' },
        }),
      );
    });
    expect(postSpy).toHaveBeenCalled();
    const sentEnvelope = postSpy.mock.calls.at(-1)?.[0] as {
      type: string;
      payload: { parts: string[]; total: number };
    };
    expect(sentEnvelope.type).toBe('stale-parts');
    expect(sentEnvelope.payload.parts).toEqual(['look']);
    expect(screen.getByTestId('explanation-frame-slot').getAttribute('data-stale-dropped')).toBe(
      '1',
    );

    // First offer-selections places one replacing pick in trayStore defaulted to explainer-seat-1.
    let now = 1000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: frame.contentWindow,
          data: {
            type: 'offer-selections',
            requestId: 'req-offer-1',
            payload: {
              selections: [
                { question: 'Lock order?', choice: 'Single leaf lock' },
                { question: 'Scope?', choice: 'All writers' },
              ],
            },
          },
        }),
      );
    });

    expect(trayStore.getState().picks).toHaveLength(1);
    expect(trayStore.getState().picks[0].input.title).toContain(
      'Selections (revision 8) · Lock order?: Single leaf lock · Scope?: All writers',
    );
    expect(trayStore.getState().targets).toEqual(['explainer-seat-1']);

    // Second offer within 1s is refused as rate-limited and does not replace the pick.
    now = 1500;
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: frame.contentWindow,
          data: {
            type: 'offer-selections',
            requestId: 'req-offer-2',
            payload: {
              selections: [{ question: 'Lock order?', choice: 'Burst choice' }],
            },
          },
        }),
      );
    });
    expect(screen.getByTestId('explanation-frame-slot').getAttribute('data-last-refusal')).toBe(
      'rate-limited',
    );
    expect(trayStore.getState().picks[0].input.title).toContain('Single leaf lock');

    // Offer after > 1s replaces the existing pick for revision 8 (still 1 pick total).
    now = 2500;
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: frame.contentWindow,
          data: {
            type: 'offer-selections',
            requestId: 'req-offer-3',
            payload: {
              selections: [{ question: 'Lock order?', choice: 'Updated choice' }],
            },
          },
        }),
      );
    });
    expect(trayStore.getState().picks).toHaveLength(1);
    expect(trayStore.getState().picks[0].input.title).toContain('Updated choice');

    // request-explanation adds a verbatim question pick to trayStore without starting a model turn.
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: frame.contentWindow,
          data: {
            type: 'request-explanation',
            requestId: 'req-explain-1',
            payload: { question: 'Why was the second lock removed?' },
          },
        }),
      );
    });
    expect(trayStore.getState().picks).toHaveLength(2);
    expect(trayStore.getState().picks[1].input.title).toContain(
      'Explanation request (revision 8) · Why was the second lock removed?',
    );
  });

  it('sends an empty stale-parts list once stale marks were drawn, so the page clears them', async () => {
    serve((call, _calls, counter) => {
      if (call.url === '/api/canvas/sessions') {
        counter.sessions += 1;
        return json(channel(`s${counter.sessions}`, Number(call.body?.revision ?? 8)));
      }
      if (call.url.endsWith('/revoke')) return json({ state: 'ok', revoked: true });
      return json({ state: 'ok' });
    });

    const { rerender } = render(
      <RevisionFrame
        target={props.target}
        revision={8}
        caption="revision 8"
        stalePartIds={['look']}
        knownPartIds={['look', 'risks']}
        staleTotal={1}
      />,
    );
    const frame = (await screen.findByTestId('explanation-frame')) as HTMLIFrameElement;
    const postSpy = vi.spyOn(frame.contentWindow!, 'postMessage');

    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: frame.contentWindow,
          data: { type: 'ready', requestId: 'req-ready-clear' },
        }),
      );
    });
    const firstEnvelope = postSpy.mock.calls.at(-1)?.[0] as {
      type: string;
      payload: { parts: string[]; total: number };
    };
    expect(firstEnvelope.type).toBe('stale-parts');
    expect(firstEnvelope.payload.parts).toEqual(['look']);
    expect(firstEnvelope.payload.total).toBe(1);

    // The staleness answer changes to empty: the same frame gets the empty list, which clears the
    // marks it drew (MIK-R104 v3 rule 1), without reopening its channel.
    rerender(
      <RevisionFrame
        target={props.target}
        revision={8}
        caption="revision 8"
        stalePartIds={[]}
        knownPartIds={['look', 'risks']}
        staleTotal={0}
      />,
    );
    await waitFor(() => expect(postSpy.mock.calls.length).toBeGreaterThanOrEqual(2));
    const clearEnvelope = postSpy.mock.calls.at(-1)?.[0] as {
      type: string;
      payload: { parts: string[]; total: number };
    };
    expect(clearEnvelope.type).toBe('stale-parts');
    expect(clearEnvelope.payload.parts).toEqual([]);
    expect(clearEnvelope.payload.total).toBe(0);
  });

  it('revokes and reopens only the navigated compare frame on departure or second load without disturbing its sibling', async () => {
    const calls = server();
    render(<ExplanationView {...props} />);
    await screen.findByTestId('explanation-frame');
    fireEvent.click(screen.getByTestId('compare'));

    const compareFrames = await screen.findAllByTestId('explanation-frame');
    expect(compareFrames).toHaveLength(2);
    const leftFrame = compareFrames.find((f) => f.getAttribute('data-slot') === 'left')!;
    const rightFrame = compareFrames.find((f) => f.getAttribute('data-slot') === 'right')!;
    const rightSrcBefore = rightFrame.getAttribute('src');

    // Second load on leftFrame revokes only leftFrame's session and reopens it; rightFrame stays untouched.
    fireEvent.load(leftFrame);
    fireEvent.load(leftFrame);

    await waitFor(() => expect(screen.getByTestId('explanation-frame-navigated')).toBeTruthy());
    await waitFor(() => {
      const updatedFrames = screen.getAllByTestId('explanation-frame');
      const currentRight = updatedFrames.find((f) => f.getAttribute('data-slot') === 'right')!;
      expect(currentRight.getAttribute('src')).toBe(rightSrcBefore);
    });

    // Document departure signal on rightFrame revokes rightFrame's session.
    const currentRight = screen
      .getAllByTestId('explanation-frame')
      .find((f) => f.getAttribute('data-slot') === 'right')!;
    const rightSession = currentRight.getAttribute('src')!.split('/').pop()!;
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: (currentRight as HTMLIFrameElement).contentWindow,
          data: {
            schema: 'ar-canvas-document/v1',
            session: rightSession,
            phase: 'departed',
          },
        }),
      );
    });
    await waitFor(() =>
      expect(calls.some((call) => call.url === `/api/canvas/sessions/${rightSession}/revoke`)).toBe(
        true,
      ),
    );
  });

  it('revokes and reopens the right channel when its document never presents a liveness port', async () => {
    const calls = server();
    render(<ExplanationView {...props} />);
    const frame = await screen.findByTestId('explanation-frame');
    const srcBefore = frame.getAttribute('src');
    const sessionBefore = (srcBefore ?? '').split('/').pop()!;

    // DOM attachment precedes passive-effect registration: finish that mount, as the next case does,
    // before the document's first load. Then the load hands the document over to the presence
    // probe, and the document never posts its `entered` port at all (a lost port, as a document that
    // navigated while parsing leaves): the probe's own 250 ms timer revokes exactly this channel and
    // reopens the saved revision.
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.load(frame);

    // No local ceiling: the wait runs on the shared async bound configured in test/setup.ts
    // (20 s), so a loaded machine delays the 250 ms product timer's revoke without failing it.
    await waitFor(() =>
      expect(
        calls.some((call) => call.url === `/api/canvas/sessions/${sessionBefore}/revoke`),
      ).toBe(true),
    );
    expect(screen.getByTestId('explanation-frame-navigated')).toBeTruthy();
    // The replacement document is never rebound: a new channel opens, and the frame shows the
    // saved page of the same revision through it.
    await waitFor(() => {
      const reopened = screen.getByTestId('explanation-frame');
      expect(reopened.getAttribute('src')).not.toBe(srcBefore);
    });
    expect(screen.getByTestId('explanation-frame').getAttribute('data-revision')).toBe('8');
  });

  it('revokes and reopens the right channel when its document-owned liveness port is lost', async () => {
    const calls = server();
    render(<ExplanationView {...props} />);
    const frame = await screen.findByTestId('explanation-frame');
    const srcBefore = frame.getAttribute('src');
    const sessionBefore = (srcBefore ?? '').split('/').pop()!;

    // DOM attachment precedes passive-effect registration. Finish that mount before supplying
    // the document's port, just as the admitted document's bootstrap does before its first load.
    await act(async () => {
      await Promise.resolve();
    });
    const port = {
      onmessage: null as ((event: MessageEvent) => void) | null,
      postMessage: vi.fn(),
      close: vi.fn(),
    };
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: (frame as HTMLIFrameElement).contentWindow,
          data: { schema: 'ar-canvas-document/v1', session: sessionBefore, phase: 'entered' },
          ports: [port as unknown as MessagePort],
        }),
      );
    });
    expect(port.onmessage).toEqual(expect.any(Function));
    // The admitted document's port receives the challenge but never answers it. The probe's own
    // 250 ms timer must revoke exactly this channel and reopen the saved revision.
    fireEvent.load(frame);
    expect(port.postMessage).toHaveBeenCalledOnce();
    expect(port.postMessage).toHaveBeenCalledWith(expect.any(String));

    // No local ceiling: the wait runs on the shared async bound configured in test/setup.ts
    // (20 s), so a loaded machine delays the 250 ms product timer's revoke without failing it.
    await waitFor(() =>
      expect(
        calls.some((call) => call.url === `/api/canvas/sessions/${sessionBefore}/revoke`),
      ).toBe(true),
    );
    expect(port.close).toHaveBeenCalledOnce();
    expect(screen.getByTestId('explanation-frame-navigated')).toBeTruthy();
    // The replacement document is never rebound: a new channel opens, and the frame shows the
    // saved page of the same revision through it.
    await waitFor(() => {
      const reopened = screen.getByTestId('explanation-frame');
      expect(reopened.getAttribute('src')).not.toBe(srcBefore);
    });
    expect(screen.getByTestId('explanation-frame').getAttribute('data-revision')).toBe('8');
  });
});
