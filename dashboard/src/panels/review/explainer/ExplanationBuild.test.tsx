import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { resetFirstRequest } from '../../../explainer/request';
import { resetTray } from '../../../explainer/testSupport';
import { ExplanationSwitch } from './ExplanationSwitch';
import { ExplanationView } from './ExplanationView';
import {
  BASE,
  badgeBriefOf,
  type Call,
  text,
  json,
  serve,
  channel,
  SAVED_STATE,
  server,
  props,
} from './ExplanationView.test-utils';

afterEach(() => {
  resetTray();
  resetFirstRequest();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

// MIK-R211's three complaints, as one suite: a failed build names the server's own words, the
// Explanation view draws the request where ✦ Explain was pressed, and "Stale parts" waits for a
// revision to exist.
describe('the request where ✦ Explain was pressed, and staleness that waits for a revision (MIK-R211)', () => {
  // What `/state` answers before anything exists: a seat with no explanation, and staleness that
  // cannot be computed because there is no retained revision to compute it against.
  const PENDING = {
    state: 'ok',
    seat: { state: 'missing' },
    retired: false,
    retiredAt: null,
    binding: { kind: 'recorded', comparison: 9, label: '#9' },
    explanation: null,
    building: null,
    stale: {
      parts: [],
      total: 0,
      unavailable: 'the leaf has no retained revision',
    },
    parts: [],
    claims: [],
    moved: null,
    limit: null,
  };

  const RUNNING_BUILD = {
    state: 'running',
    requestId: 'req-1',
    step: 'checkout-code',
    steps: [
      { name: 'workspace', state: 'done' },
      {
        name: 'checkout-code',
        state: 'running',
        counts: { kind: 'bytes', done: 50, total: 100 },
      },
    ],
    failedStep: null,
  };

  const NO_REVISIONS = { state: 'ok', pinned: null, revisions: [] };

  it('draws no stale-parts block before a revision exists, even when the host cannot read staleness', async () => {
    const calls = server({ state: PENDING, revisions: NO_REVISIONS });
    render(<ExplanationView {...props} />);

    await screen.findByTestId('explanation-empty');
    expect(screen.queryByTestId('stale-parts')).toBeNull();
    expect(screen.queryByText(/Staleness cannot be read/)).toBeNull();
    // Nothing was posted: the empty state reads and starts nothing.
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0);
  });

  it('draws no stale list once a revision exists, and the top badge states its staleness in the host’s own words', async () => {
    server({
      state: {
        ...SAVED_STATE,
        stale: {
          parts: [],
          total: 0,
          unavailable: 'the comparison has no tree',
        },
      },
    });
    // F18: staleness is the top line's badge, built from the same answer the view reads.
    const brief = await badgeBriefOf();
    render(
      <>
        <ExplanationSwitch view="explanation" stale={brief.total} brief={brief} onSwitch={vi.fn()} />
        <ExplanationView {...props} />
      </>,
    );

    await screen.findByTestId('explanation-frame');
    expect(within(screen.getByTestId('explanation-view')).queryByTestId('stale-parts')).toBeNull();
    expect(text(screen.getByTestId('explanation-stale-mark'))).toBe('stale?');
    fireEvent.click(screen.getByTestId('explanation-stale-mark'));
    expect(text(screen.getByTestId('stale-summary-line'))).toContain(
      'Staleness cannot be read: the comparison has no tree',
    );
  });

  it('shows the request where Explain was pressed, then the build’s progress, then the page', async () => {
    let phase: 'empty' | 'running' | 'done' = 'empty';
    const doneState = {
      ...PENDING,
      explanation: {
        revision: 1,
        at: '2026-10-25T10:00:00Z',
        entrypoint: 'index.html',
      },
      build: { ...RUNNING_BUILD, state: 'done' },
    };
    const calls = serve((call) => {
      if (call.url.startsWith(`${BASE}/state`)) {
        // Nothing exists, then the build runs, then the view's own read finds it finished.
        if (phase === 'empty') return json(PENDING);
        phase = 'done';
        return json(doneState);
      }
      if (call.url === `${BASE}/revisions`) {
        return json(
          phase === 'done'
            ? {
                ...NO_REVISIONS,
                revisions: [
                  {
                    revision: 1,
                    binding: { kind: 'recorded', comparison: 9, label: '#9' },
                    at: '2026-10-25T10:00:00Z',
                    kind: 'global',
                    agreed: false,
                    pinned: false,
                  },
                ],
              }
            : NO_REVISIONS,
        );
      }
      if (call.url === `${BASE}/build`) {
        phase = 'running';
        return json(
          {
            attempt: 'a1',
            state: {
              ...PENDING,
              seat: { state: 'closed' },
              build: RUNNING_BUILD,
            },
          },
          202,
        );
      }
      if (call.url === '/api/canvas/sessions')
        return json(channel('s1', Number(call.body?.revision ?? 1)));
      if (call.url.endsWith('/revoke')) return json({ state: 'ok', revoked: true });
      return json({ state: 'refused', reason: 'unmocked', detail: call.url }, 404);
    });

    render(<ExplanationView {...props} />);
    const empty = await screen.findByTestId('explanation-empty');

    // ✦ Explain, pressed here, draws the request here: not only in the chat column's rail.
    fireEvent.click(within(empty).getByTestId('explain-change-set'));
    const form = await screen.findByTestId('explanation-request-form');
    expect(screen.getByTestId('explanation-view').contains(form)).toBe(true);
    expect(screen.queryByTestId('explanation-empty')).toBeNull();
    expect(text(form)).toContain('a new explainer for');

    fireEvent.change(within(form).getByLabelText('What do you want explained?'), {
      target: { value: 'Explain the whole change set.' },
    });
    fireEvent.click(within(form).getByTestId('explanation-request-send'));

    // His Send: one build, his words, and the server's own steps drawn where he pressed.
    await waitFor(() => expect(calls.some((call) => call.url === `${BASE}/build`)).toBe(true));
    const posted = calls.find((call) => call.method === 'POST' && call.url === `${BASE}/build`);
    expect(posted?.body).toMatchObject({
      target: 'new-explainer',
      message: 'Explain the whole change set.',
    });
    const box = await screen.findByTestId('explainer-build');
    expect(text(screen.getByTestId('explainer-build-percent'))).toBe('75%');
    expect(box.getAttribute('data-testid')).toBe('explainer-build');

    // The read that follows draws the page, and the form gives its place up.
    await waitFor(() => expect(screen.queryByTestId('explanation-request-form')).toBeNull());
    expect(await screen.findByTestId('explanation-frame')).toBeTruthy();
    expect(box.isConnected).toBe(false);
  });

  it('keeps a failed build’s reason in the request and the box, and keeps his request for a new Send', async () => {
    const reason = 'the host could not create the workspace: permission denied';
    serve((call) => {
      if (call.url.startsWith(`${BASE}/state`)) return json(PENDING);
      if (call.url === `${BASE}/revisions`) return json(NO_REVISIONS);
      if (call.url === `${BASE}/build`) {
        return json(
          {
            attempt: 'a1',
            state: {
              ...PENDING,
              build: {
                state: 'failed',
                requestId: 'req-1',
                step: 'workspace',
                steps: [{ name: 'workspace', percent: 0 }],
                failedStep: 'workspace',
                error: reason,
              },
            },
          },
          202,
        );
      }
      return json({ state: 'refused', reason: 'unmocked', detail: call.url }, 404);
    });

    render(<ExplanationView {...props} />);
    fireEvent.click(
      within(await screen.findByTestId('explanation-empty')).getByTestId('explain-change-set'),
    );
    const form = await screen.findByTestId('explanation-request-form');
    fireEvent.change(within(form).getByLabelText('What do you want explained?'), {
      target: { value: 'Explain it.' },
    });
    fireEvent.click(within(form).getByTestId('explanation-request-send'));

    const failure = await screen.findByTestId('explanation-request-failure');
    expect(failure.textContent).toContain(reason);
    expect(failure.textContent).not.toContain('no reason was given');
    // The build box says the same sentence, in the server's own words.
    expect(text(screen.getByTestId('explainer-build-failure'))).toContain(reason);
    // His request is still here, ready for another Send.
    expect(screen.getByTestId('explanation-request-form')).toBeTruthy();
    expect(
      (screen.getByLabelText('What do you want explained?') as HTMLTextAreaElement).value,
    ).toBe('Explain it.');
  });

  it('draws a build the server is already running with its own steps, and no stale list', async () => {
    server({
      state: {
        ...PENDING,
        building: { state: 'building', step: 'checkout-code' },
        build: RUNNING_BUILD,
      },
      revisions: NO_REVISIONS,
    });
    render(<ExplanationView {...props} />);

    const box = await screen.findByTestId('explainer-build');
    expect(text(box)).toContain('75%');
    expect(
      screen
        .getByTestId('explainer-build-steps')
        .querySelectorAll('[data-testid="explainer-build-step"]'),
    ).toHaveLength(2);
    expect(screen.queryByTestId('explanation-building')).toBeNull();
    expect(screen.queryByTestId('stale-parts')).toBeNull();
    // Nothing was posted: a read starts no build.
    expect(screen.queryByTestId('explanation-request-send')).toBeNull();
  });

  it('names a build that stopped in `/state` before any press', async () => {
    const reason = 'no Paseo host answered';
    server({
      state: {
        ...PENDING,
        build: {
          state: 'failed',
          step: 'seat',
          steps: [{ name: 'seat' }],
          failedStep: 'seat',
          error: reason,
        },
      },
      revisions: NO_REVISIONS,
    });
    render(<ExplanationView {...props} />);

    const failure = await screen.findByTestId('explanation-request-failure');
    expect(failure.textContent).toContain(reason);
    expect(failure.getAttribute('data-step')).toBe('seat');
    expect(screen.queryByTestId('explanation-empty')).toBeNull();
    expect(screen.queryByTestId('stale-parts')).toBeNull();
  });
});

// The developer's F3: a build that hangs had no way to end it. A running build draws a Cancel in
// its box and in the form that waits; either press names the build's own request id, and what the
// box then shows is the server's ended build — its `failedStep` and its own `error` — with Send
// free for a new attempt (MIK-R211 v2 rules 4 and 5).
describe('a stuck build can be ended, and its reason kept (MIK-R211 v2 rules 4 and 5)', () => {
  const PENDING = {
    state: 'ok',
    seat: { state: 'missing' },
    retired: false,
    retiredAt: null,
    binding: { kind: 'recorded', comparison: 9, label: '#9' },
    explanation: null,
    building: null,
    stale: {
      parts: [],
      total: 0,
      unavailable: 'the leaf has no retained revision',
    },
    parts: [],
    claims: [],
    moved: null,
    limit: null,
  };

  const REQUEST_ID = 'req-cancelled-1';
  const CANCELLED_REASON = 'build-cancelled: the developer ended the attempt';

  const RUNNING = {
    state: 'running',
    requestId: REQUEST_ID,
    startedAt: '2026-10-25T10:00:00Z',
    steps: [
      { name: 'workspace', state: 'done' },
      {
        name: 'checkout-code',
        state: 'running',
        counts: { kind: 'bytes', done: 50, total: 100 },
      },
    ],
    failedStep: null,
  };

  // What `/state` answers once the cancel reached the host: the same attempt, stopped, in the
  // server's own words (`explainer_service.py` writes `failedStep` and `error`).
  const ENDED = {
    state: 'failed',
    requestId: REQUEST_ID,
    startedAt: '2026-10-25T10:00:00Z',
    step: 'checkout-code',
    steps: [{ name: 'workspace' }, { name: 'checkout-code' }],
    failedStep: 'checkout-code',
    error: CANCELLED_REASON,
  };

  const NO_REVISIONS = { state: 'ok', pinned: null, revisions: [] };

  /** A page that hangs: `/state` answers PENDING, then the ended build once a cancel arrived. */
  function hanging(over: { onCancel?: (call: Call) => Response } = {}) {
    let ended = false;
    let running: unknown = null;
    const calls = serve((call) => {
      if (call.url.startsWith(`${BASE}/state`)) {
        return json(ended ? { ...PENDING, build: ENDED } : { ...PENDING, build: running });
      }
      if (call.url === `${BASE}/revisions`) return json(NO_REVISIONS);
      if (call.url === `${BASE}/build`) {
        running = RUNNING;
        return json({ attempt: 'a1', state: { ...PENDING, build: RUNNING } }, 202);
      }
      if (call.url === `${BASE}/cancel`) {
        if (over.onCancel) return over.onCancel(call);
        ended = true;
        return json({
          state: 'ok',
          cancel: {
            requestId: call.body?.requestId,
            seat: 'live',
            turnStopped: true,
            requestUncertain: false,
            publicationPrevented: true,
          },
        });
      }
      return json({ state: 'refused', reason: 'unmocked', detail: call.url }, 404);
    });
    return calls;
  }

  /** ✦ Explain, his words, Send: the build runs and its box is on screen. */
  async function sendRequest(): Promise<void> {
    render(<ExplanationView {...props} />);
    fireEvent.click(
      within(await screen.findByTestId('explanation-empty')).getByTestId('explain-change-set'),
    );
    const form = await screen.findByTestId('explanation-request-form');
    fireEvent.change(within(form).getByLabelText('What do you want explained?'), {
      target: { value: 'Explain the whole change set.' },
    });
    fireEvent.click(within(form).getByTestId('explanation-request-send'));
    await screen.findByTestId('explainer-build');
  }

  it('posts the build’s own request id once, then shows the reason and frees Send', async () => {
    const calls = hanging();
    await sendRequest();

    const cancel = await screen.findByTestId('explainer-build-cancel');
    expect(text(screen.getByTestId('explainer-build-percent'))).toBe('75%');
    // A second press of the same control posts nothing: the first cancel names the one attempt.
    fireEvent.click(cancel);
    fireEvent.click(cancel);

    await waitFor(() =>
      expect(calls.filter((call) => call.url === `${BASE}/cancel`)).toHaveLength(1),
    );
    const posted = calls.find((call) => call.url === `${BASE}/cancel`);
    expect(posted?.method).toBe('POST');
    expect(posted?.body).toEqual({ requestId: REQUEST_ID });

    // The ended build takes the box's place, in the server's own words, and its Cancel is gone.
    const failure = await screen.findByTestId('explainer-build-failure');
    expect(failure.textContent).toContain(CANCELLED_REASON);
    expect(failure.textContent).not.toContain('no reason was given');
    expect(failure.getAttribute('data-step')).toBe('checkout-code');
    expect(screen.queryByTestId('explainer-build-cancel')).toBeNull();

    // The form is not left sending or holding: Send is here again, and it makes a new attempt.
    const send = await screen.findByTestId('explanation-request-send');
    expect((send as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(send);
    await waitFor(() =>
      expect(
        calls.filter((call) => call.method === 'POST' && call.url === `${BASE}/build`),
      ).toHaveLength(2),
    );
    const second = calls.filter(
      (call) => call.method === 'POST' && call.url === `${BASE}/build`,
    )[1];
    expect(typeof second.body?.requestId).toBe('string');
    expect(second.body?.requestId).not.toBe(REQUEST_ID);
  });

  it('draws the same cancel on the form that waits, and names a refusal instead of claiming a stop', async () => {
    const calls = hanging({
      onCancel: () =>
        json({
          state: 'refused',
          reason: 'request-uncertain',
          detail: 'no turn of this request is live',
        }),
    });
    await sendRequest();

    // The form's own control waits beside the box: the press is the same one.
    const waitCancel = await screen.findByTestId('explanation-request-cancel');
    fireEvent.click(waitCancel);

    const refusal = await screen.findByTestId('explanation-request-cancel-failure');
    expect(refusal.textContent).toContain('request-uncertain');
    expect(refusal.textContent).toContain('no turn of this request is live');
    expect(calls.filter((call) => call.url === `${BASE}/cancel`)).toHaveLength(1);
    // A refusal is not a stop: the build's box still runs, and its Cancel is still there.
    expect(screen.getByTestId('explainer-build-cancel')).toBeTruthy();
    expect(screen.queryByTestId('explainer-build-failure')).toBeNull();
  });

  it('offers no Cancel for a build that already stopped', async () => {
    server({
      state: { ...PENDING, build: ENDED },
      revisions: NO_REVISIONS,
    });
    render(<ExplanationView {...props} />);

    await screen.findByTestId('explainer-build-failure');
    expect(screen.queryByTestId('explainer-build-cancel')).toBeNull();
  });
});
