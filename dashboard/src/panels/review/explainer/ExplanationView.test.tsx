// The Explanation view, end to end (MIK-R106 v2 rule 6; MIK-R104 v3; MIK-R103 v2).
//
// One case per promise a reader can check:
//   * no explanation yet: the view says so with exactly one button, and pressing it starts nothing
//     here (it opens the chat's first-request form);
//   * during the build: a still placeholder that points at the chat, and no page;
//   * a saved page opens with no model -- one isolated-host channel for one revision, and no
//     generate, no build and no message;
//   * the trusted stale list is drawn from the host's answer whatever the frame draws, or fails to;
//   * Compare mounts two isolated frames;
//   * the task-total refusal names itself and offers only unpinned revisions to remove;
//   * every claim carries its basis, the legend names all four, and a claim's link opens its block.
//
// Assertions are plain DOM assertions: this suite loads no jest-dom matchers.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { pendingFirstRequest, resetFirstRequest } from '../../../explainer/request';
import { resetTray } from '../../../explainer/testSupport';
import { trayStore } from '../../../tray/trayStore';
import { forwardedBody } from '../canvas/canvasHooks';
import { ExplanationView } from './ExplanationView';
import { RevisionFrame } from './RevisionFrame';
import type { ClaimLink } from './explainerApi';

const REPO = 'agents-remember';
const MASTER = '260928_maintained-invariant-knowledge';
const LEAF = '260928-MIK-L183';
const BASE = `/api/explainer/${REPO}/${MASTER}/${LEAF}`;

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | undefined;
}

const text = (element: Element | null | undefined): string => element?.textContent ?? '';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function serve(
  reply: (call: Call, calls: Call[], counter: { sessions: number }) => Response,
): Call[] {
  const calls: Call[] = [];
  const counter = { sessions: 0 };
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const call: Call = {
        url: String(input),
        method: init?.method ?? 'GET',
        body:
          typeof init?.body === 'string'
            ? (JSON.parse(init.body) as Record<string, unknown>)
            : undefined,
      };
      calls.push(call);
      return Promise.resolve(reply(call, calls, counter));
    }),
  );
  return calls;
}

const channel = (name: string, revision: number) => ({
  state: 'ok',
  session: name,
  revision,
  comparison: 'review-comparison:task/leaf/9',
  entrypoint: 'index.html',
  scripts: false,
  sandbox: 'allow-scripts',
  pageUrl: `/canvas/page/${name}`,
});

const BINDING9 = { kind: 'recorded', comparison: 9, label: '#9' };

const SAVED_STATE = {
  state: 'ok',
  seat: 'live',
  retiredAt: null,
  binding: BINDING9,
  explanation: {
    revision: 8,
    at: '2026-10-19T12:36:00Z',
    entrypoint: 'index.html',
    comparison: 'review-comparison:task/leaf/8',
  },
  building: null,
  stale: {
    total: 5,
    parts: [
      { id: 'look', title: 'Where to look first', reason: 'noteTurnEnded and the tracker changed in #9' },
      { id: 'risks', title: 'Risks I see', reason: 'the grades of 4 changed blocks' },
      { id: 'flow', title: 'Before and after', reason: 'the wait and the count changed in #9' },
    ],
  },
  parts: [
    { id: 'answer', title: 'In one page' },
    { id: 'look', title: 'Where to look first' },
    { id: 'risks', title: 'Risks I see' },
  ],
  claims: [
    {
      id: 'c1',
      part: 'answer',
      basis: 'recorded-fact',
      text: 'the curator recorded this as a weaken',
      link: { block: 'b20', label: 'turn_ended.ts L1697–1817' },
    },
    { id: 'c2', part: 'look', basis: 'mechanical-signal', text: '13 of 71 locations changed' },
    { id: 'c3', part: 'risks', basis: 'presenter-inference', text: 'the send to the seat may throw' },
    { id: 'c4', part: 'risks', basis: 'intended', text: 'a seat is meant to return to its work' },
  ],
  moved: null,
  limit: null,
};

const REVISIONS = {
  state: 'ok',
  pinned: 8,
  total: { used: 41 * 1024 * 1024, limit: 512 * 1024 * 1024 },
  revisions: [
    {
      revision: 7,
      binding: { kind: 'recorded', comparison: 7, label: '#7' },
      at: '2026-10-19T11:20:00Z',
      kind: 'global',
      agreed: false,
      pinned: false,
    },
    {
      revision: 8,
      binding: { kind: 'recorded', comparison: 8, label: '#8' },
      at: '2026-10-19T12:36:00Z',
      kind: 'global',
      agreed: true,
      pinned: true,
    },
    {
      revision: 9,
      binding: { kind: 'recorded', comparison: 9, label: '#9' },
      at: '2026-10-19T13:10:00Z',
      kind: 'focused',
      focus: 'Risks I see',
      agreed: false,
      pinned: false,
    },
  ],
};

function server(
  over: { state?: unknown; revisions?: unknown; sessions?: (call: Call) => Response } = {},
) {
  return serve((call, _calls, counter) => {
    if (call.url.startsWith(`${BASE}/state`)) return json(over.state ?? SAVED_STATE);
    if (call.url === `${BASE}/revisions`) return json(over.revisions ?? REVISIONS);
    if (call.url === '/api/canvas/sessions') {
      counter.sessions += 1;
      if (over.sessions) return over.sessions(call);
      return json(channel(`s${counter.sessions}`, Number(call.body?.revision ?? 1)));
    }
    if (call.url.endsWith('/revoke')) return json({ state: 'ok', revoked: true });
    if (call.url === `${BASE}/update`) return json({ attempt: 'a1', state: {} });
    if (call.url === `${BASE}/remove-revisions`)
      return json({ state: 'ok', removed: call.body?.revisions ?? [] });
    return json({ state: 'refused', reason: 'unmocked', detail: call.url }, 404);
  });
}

const onExplain = vi.fn();
const onOpenBlock = vi.fn();

const props = {
  target: { repository: REPO, master: MASTER, leaf: LEAF },
  comparison: { reference: 'review-comparison:task/leaf/9', number: 9 },
  onExplain,
  onOpenBlock,
};

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
  it('opens in one isolated frame with no model: a channel for its revision and nothing else', async () => {
    const calls = server();
    render(<ExplanationView {...props} />);

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
    expect(text(screen.getByTestId('explanation-standfirst'))).toContain('revision 8 · bound to #8');
  });

  it('shows the trusted stale list whatever the frame draws, and whatever it fails to draw', async () => {
    server();
    const { unmount } = render(<ExplanationView {...props} />);
    await screen.findByTestId('explanation-frame');
    expect(screen.getAllByTestId('stale-part')).toHaveLength(3);
    expect(screen.getByTestId('stale-parts').getAttribute('data-count')).toBe('5');
    expect(text(screen.getByTestId('stale-more'))).toContain('+ 2 more stale parts');
    expect(text(screen.getByTestId('stale-parts'))).toContain('Risks I see');
    expect(text(screen.getByTestId('stale-parts'))).toContain('the grades of 4 changed blocks');
    unmount();

    // The page cannot be shown at all: the list is unchanged, because it never came from the page.
    vi.unstubAllGlobals();
    server({
      sessions: (call) =>
        json(
          { state: 'refused', reason: 'no-ready-revision', detail: `r${call.body?.revision}` },
          404,
        ),
    });
    render(<ExplanationView {...props} />);
    expect(text(await screen.findByTestId('explanation-frame-problem'))).toContain(
      'no-ready-revision',
    );
    expect(screen.getAllByTestId('stale-part')).toHaveLength(3);
    expect(screen.getByTestId('stale-parts').getAttribute('data-count')).toBe('5');
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
      expect(calls.filter((call) => call.url.startsWith(`${BASE}/state`)).length).toBeGreaterThan(1),
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
    expect(opened.slice(-2).map((body) => body?.revision).sort()).toEqual([8, 9]);
    expect(opened.slice(-2).map((body) => body?.compareSlot).sort()).toEqual(['left', 'right']);
    // Neither compare channel is substituted by the latest revision: each asks for exactly its
    // own retained revision and its own binding.
    const leftCall = opened.slice(-2).find((body) => body?.compareSlot === 'left');
    const rightCall = opened.slice(-2).find((body) => body?.compareSlot === 'right');
    expect(leftCall?.revision).toBe(9);
    expect(leftCall?.comparisonNumber).toBe(9);
    expect(rightCall?.revision).toBe(8);
    expect(rightCall?.comparisonNumber).toBe(8);
    expect(
      calls.filter((call) => call.url.endsWith('/revoke')).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId('explanation-compare')).not.toBeNull();
    expect(text(screen.getByTestId('compare-line'))).toContain('its own isolated frame');
    // Two frames of the same isolated host: one sandbox policy, two different documents.
    expect(new Set(frames.map((frame) => frame.getAttribute('src'))).size).toBe(2);
  });
});

describe('the claims and their bases', () => {
  it('marks every claim and names all four bases in the legend', async () => {
    server();
    render(<ExplanationView {...props} />);
    await screen.findByTestId('explanation-frame');

    const legend = screen.getByTestId('basis-legend');
    const entries = within(legend).getAllByTestId('legend-entry');
    expect(entries.map((entry) => entry.getAttribute('data-basis'))).toEqual([
      'recorded-fact',
      'mechanical-signal',
      'presenter-inference',
      'intended',
    ]);
    expect(text(legend)).toContain('a fact in the memory');
    expect(text(legend)).toContain('measured by the product');
    expect(text(legend)).toContain("the explainer's reading");
    expect(text(legend)).toContain('intended, not a fact about the code');

    const claims = screen.getAllByTestId('claim');
    expect(claims.map((claim) => claim.getAttribute('data-basis'))).toEqual([
      'recorded-fact',
      'mechanical-signal',
      'presenter-inference',
      'intended',
    ]);
    expect(text(within(claims[2]).getByTestId('claim-chip'))).toContain('✦ inference');
  });

  it('opens a claim’s block in the Review view and selects it', async () => {
    server();
    const selected: ClaimLink[] = [];
    function Harness() {
      const [block, setBlock] = useState<string | null>(null);
      return (
        <>
          <ExplanationView
            {...props}
            onOpenBlock={(link) => {
              selected.push(link);
              setBlock(link.block);
            }}
          />
          <p data-testid="review-selection">{block ?? 'nothing selected'}</p>
        </>
      );
    }
    render(<Harness />);
    await screen.findByTestId('explanation-frame');
    expect(text(screen.getByTestId('review-selection'))).toBe('nothing selected');

    fireEvent.click(screen.getByTestId('claim-link'));
    expect(selected[0]).toEqual({ block: 'b20', label: 'turn_ended.ts L1697–1817' });
    expect(text(screen.getByTestId('review-selection'))).toBe('b20');
  });
});

describe('the moved notice', () => {
  it('offers Update when the leaf moved and an explainer has worked, and presses only that route', async () => {
    const calls = server({ state: { ...SAVED_STATE, moved: { from: 8, to: 9, blocks: 4 } } });
    render(<ExplanationView {...props} />);

    const notice = await screen.findByTestId('moved-notice');
    expect(notice.getAttribute('data-moved')).toBe('moved');
    expect(text(screen.getByTestId('moved-line'))).toContain('Task moved: #8 → #9 · 4 blocks changed');
    fireEvent.click(screen.getByTestId('update'));

    await waitFor(() =>
      expect(calls.filter((call) => call.url === `${BASE}/update`)).toHaveLength(1),
    );
    expect(calls.find((call) => call.url === `${BASE}/update`)?.body).toEqual({
      comparison: 'review-comparison:task/leaf/9',
    });
    expect(calls.some((call) => /generate|build|messages/.test(call.url))).toBe(false);
  });

  it('says the view renewed itself, and offers no Update, when no explainer has worked', async () => {
    server({
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
    render(<ExplanationView {...props} />);

    const notice = await screen.findByTestId('moved-notice');
    expect(notice.getAttribute('data-moved')).toBe('moved-no-explainer');
    expect(text(screen.getByTestId('moved-no-explainer-note'))).toContain(
      'the view renewed itself',
    );
    expect(screen.queryByTestId('update')).toBeNull();
  });
});

describe('a read that fails', () => {
  it('names the problem and offers Retry, never an empty explanation', async () => {
    serve((call) =>
      call.url.startsWith(`${BASE}/state`)
        ? json(
            { state: 'refused', reason: 'no-environment', detail: 'the leaf has no explainer workspace' },
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
          stale: {
            total: 1,
            parts: [{ id: 'look', title: `Stale in r${rev}`, reason: `reason r${rev}` }],
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

    const firstSession = calls.find((call) => call.url === '/api/canvas/sessions')!;
    expect(firstSession.body?.frame).toBe('explainer');
    expect(firstSession.body?.compareSlot).toBe('primary');
    expect(firstSession.body?.revision).toBe(8);
    expect(firstSession.body?.comparisonNumber).toBe(8);
    expect(text(screen.getByTestId('explanation-standfirst'))).toContain('revision 8 · bound to #8');
    expect(text(screen.getByTestId('explanation-claims'))).toContain('claim text for revision 8');

    // Switch to historical revision 7: re-reads state for revision 7 and updates standfirst, stale list, and claims.
    const revButtons = screen.getAllByTestId('revision-show');
    fireEvent.click(revButtons[0]);

    await waitFor(() =>
      expect(text(screen.getByTestId('explanation-standfirst'))).toContain('revision 7 · bound to #7'),
    );
    await waitFor(() =>
      expect(text(screen.getByTestId('explanation-claims'))).toContain('claim text for revision 7'),
    );
    expect(text(screen.getByTestId('stale-parts'))).toContain('Stale in r7');

    // Change comparison prop: re-reads state with the new comparisonNumber.
    view.rerender(
      <ExplanationView
        {...props}
        comparison={{ reference: 'review-comparison:task/leaf/10', number: 10, owner: 'recorded' }}
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

    // Compare mode renders per-page stale lists, standfirsts, and claims for BOTH compared revisions.
    fireEvent.click(screen.getByTestId('compare'));
    const compareCols = await screen.findAllByTestId('explanation-compare-column');
    expect(compareCols).toHaveLength(2);
    expect(screen.getAllByTestId('stale-parts')).toHaveLength(2);
    expect(screen.getAllByTestId('explanation-claims')).toHaveLength(2);
  });

  it('sends selective regeneration once when live, and opens prefilled parts+grades form when no seat or archived, preserving state on failure', async () => {
    let failBuild = false;
    const calls = serve((call, _calls, counter) => {
      if (call.url.startsWith(`${BASE}/state`)) {
        return json({
          ...SAVED_STATE,
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
        });
      }
      if (call.url === `${BASE}/revisions`) return json(REVISIONS);
      if (call.url === '/api/canvas/sessions') {
        counter.sessions += 1;
        return json(channel(`s${counter.sessions}`, Number(call.body?.revision ?? 1)));
      }
      if (call.url === `${BASE}/build`) {
        if (failBuild) {
          return json({ state: 'refused', reason: 'host-busy', detail: 'explainer is busy' }, 409);
        }
        return json({ state: 'ok', attempt: 'regen-1' });
      }
      if (call.url.endsWith('/revoke')) return json({ state: 'ok', revoked: true });
      return json({ state: 'ok' });
    });

    const { unmount } = render(<ExplanationView {...props} />);
    const regenBtn = await screen.findByTestId('regenerate-stale');
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
    fireEvent.click(regenBtn);
    await waitFor(() =>
      expect(calls.filter((call) => call.url === `${BASE}/build`)).toHaveLength(2),
    );
    const buildCall = calls.filter((call) => call.url === `${BASE}/build`)[1];
    expect(buildCall.body).toMatchObject({
      message: 'Regenerate revision 8: 3 parts and 2 grades are stale.',
      stalePartIds: ['look', 'risks', 'flow'],
      staleGrades: ['RLZ-10', 'RLZ-11'],
      regeneration: true,
    });
    unmount();

    // Archived / no-seat case: opens first-request form prefilled with both stale parts and stale grades, no POST.
    vi.unstubAllGlobals();
    const archivedCalls = serve((call) => {
      if (call.url.startsWith(`${BASE}/state`)) {
        return json({
          ...SAVED_STATE,
          seat: { state: 'archived' },
          retired: true,
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
        });
      }
      if (call.url === `${BASE}/revisions`) return json(REVISIONS);
      if (call.url === '/api/canvas/sessions') return json(channel('s1', 8));
      return json({ state: 'ok' });
    });

    render(<ExplanationView {...props} />);
    const archivedBtn = await screen.findByTestId('regenerate-stale');
    fireEvent.click(archivedBtn);

    expect(onExplain).toHaveBeenCalledTimes(1);
    const pending = pendingFirstRequest(props.target);
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
    expect(archivedCalls.filter((call) => call.url === `${BASE}/build`)).toHaveLength(0);
  });
});

describe('frame protocol: stale-parts, offer-selections, request-explanation, and per-frame navigation revocation (MIK-R104 v3)', () => {
  it('sends stale-parts with dropped/truncated counts, clears when empty, and handles offer-selections (1/s rate-limited, replacing pick) and request-explanation', async () => {
    expect(
      forwardedBody({
        type: 'offer-selections',
        payload: { selections: [{ question: 'Which path?', choice: 'Fast path' }] },
      }),
    ).not.toBeNull();

    serve((call, _calls, counter) => {
      if (call.url.startsWith(`${BASE}/state`)) {
        return json({
          ...SAVED_STATE,
          explainer: { agentId: 'explainer-seat-1', workspace: '/tmp/wks', generation: 1 },
          stale: {
            total: 4,
            parts: [
              { id: 'look', title: 'Where to look first', reason: 'changed' },
              { id: 'unknown-part', title: 'Unknown', reason: 'not in part map' },
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
    expect(screen.getByTestId('explanation-frame-slot').getAttribute('data-stale-dropped')).toBe('1');

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

    await waitFor(() =>
      expect(screen.getByTestId('explanation-frame-navigated')).toBeTruthy(),
    );
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
          data: { schema: 'ar-canvas-document/v1', session: rightSession, phase: 'departed' },
        }),
      );
    });
    await waitFor(() =>
      expect(
        calls.some((call) => call.url === `/api/canvas/sessions/${rightSession}/revoke`),
      ).toBe(true),
    );
  });

  it('revokes and reopens the right channel when its document-owned liveness port is lost', async () => {
    const calls = server();
    render(<ExplanationView {...props} />);
    const frame = await screen.findByTestId('explanation-frame');
    const srcBefore = frame.getAttribute('src');
    const sessionBefore = (srcBefore ?? '').split('/').pop()!;

    // First load hands the document over to the presence probe. The document never answers the
    // challenge with its port (a lost port, as a document that navigated while parsing leaves):
    // the probe's own 250 ms timer revokes exactly this channel and reopens the saved revision.
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
});
