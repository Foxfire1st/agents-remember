// The page's panel: saved-first, explicit generation, and a frame that carries its policy.
//
// One case per user-observable promise: opening shows what is saved and starts nothing; a saved page
// is embedded with the host's sandbox; the control is the only thing that starts authoring; a page
// whose comparison moved is marked stale and regenerated from its retained revision; a refused launch
// names its reason; and an evidence destination is opened only when it is the exact one this
// comparison shows.
//
// THE SCOPE PROBES (L104-B10) run the real hooks and replay the four counterexamples an independent
// review found: a replaced review target, a replaced ready revision, a channel the server allocated
// after the panel was disposed, and an answer for a previous scope that arrives last. Each asserts the
// mounted identity, not a mock: the channel is revoked before its successor exists, a late allocation
// is handed back, and a late read never overwrites the current page. The positive control (a ready
// page opens one channel and unmount revokes it) proves the hooks and effects run at all.
//
// THE SAVED ARTIFACT is the host's own answer (frameState.*.captured.json, captured by
// scratch/worker/6d1f2b84-.../b10b11b12/capture_frame_state.py against the real frame store), not a
// hand-supplied stale body: comparison 7 current, comparison 8 stale, and the retained revision's
// folder gone. That is what makes the lineage claim testable (L104-B12).

import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CanvasPanel } from './CanvasPanel';
import type { CanvasComparison, CanvasFrameState, EvidenceDestination } from './canvasApi';
import { useCanvasFrame, useCanvasSession, useRetainedComparison } from './canvasHooks';
import { destinationLine, frameForComparison, panelPhase } from './canvasModel';

const captured = <T,>(name: string): T =>
  JSON.parse(readFileSync(new URL(name, import.meta.url), 'utf8')) as T;

const FRAME7 = captured<CanvasFrameState>('frameState.comparison7.captured.json');
const FRAME8 = captured<CanvasFrameState>('frameState.afterComparison8.captured.json');
const NO_ASSETS = captured<CanvasFrameState>('frameState.missingAssets.captured.json');

const TARGET = {
  repository: 'agents-remember',
  master: '260928_maintained-invariant-knowledge',
  leaf: '260928-MIK-L104',
};
const COMPARISON7: CanvasComparison = { reference: '1'.repeat(64), number: 7, history: 'live' };
const COMPARISON8: CanvasComparison = { ...COMPARISON7, reference: '2'.repeat(64), number: 8 };
const BOUND7 = 'review-comparison:260928_maintained-invariant-knowledge/260928-MIK-L104/7';
const BOUND8 = 'review-comparison:260928_maintained-invariant-knowledge/260928-MIK-L104/8';
const FRAME_URL = `/api/canvas/frames/${TARGET.repository}/${TARGET.master}/${TARGET.leaf}/review-page`;

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | undefined;
  search: URLSearchParams;
}

const answer = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function serve(reply: (call: Call, calls: Call[]) => Response | Promise<Response>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const call: Call = {
        url,
        method: init?.method ?? 'GET',
        body:
          typeof init?.body === 'string'
            ? (JSON.parse(init.body) as Record<string, unknown>)
            : undefined,
        search: new URL(url, 'http://localhost').searchParams,
      };
      calls.push(call);
      return Promise.resolve(reply(call, calls));
    }),
  );
  return calls;
}

const sessionPosts = (calls: Call[]) => calls.filter((call) => call.url === '/api/canvas/sessions');

const channel = (name: string, revision = 1) => ({
  state: 'ok',
  session: name,
  revision,
  comparison: BOUND7,
  entrypoint: 'index.html',
  scripts: false,
  sandbox: '',
  pageUrl: `/canvas/page/${name}`,
});

/** The host's answers for one mounted page: reads, sessions, starts, revokes. */
function servePage(state: CanvasFrameState, session: { scripts?: boolean; sandbox?: string } = {}) {
  return serve((call, calls) => {
    if (call.url.endsWith('/revoke')) return answer({ state: 'ok', revoked: true });
    if (call.url === '/api/canvas/sessions') {
      const opened = sessionPosts(calls).length;
      const revision = Number(call.body?.revision ?? 1);
      return answer({ ...channel(`s${opened}`, revision), ...session });
    }
    if (call.url.endsWith('/generate')) return answer({ state: 'ok' });
    if (call.url.startsWith('/api/canvas/frames/')) return answer(state);
    return answer({ detail: 'unmocked', reason: 'x' }, 404);
  });
}

const dispatchFrameMessage = (frame: HTMLElement, data: unknown) =>
  window.dispatchEvent(
    new MessageEvent('message', { source: (frame as HTMLIFrameElement).contentWindow, data }),
  );

/**
 * Send one message from the frame until the panel has acted on it.
 *
 * The message listener is attached by the effect that follows the commit in which the channel is
 * drawn, so a single dispatch can land before the listener exists. Retrying inside `waitFor` is the
 * same race the browser cannot lose (a frame's message always arrives after its channel is live) and
 * it keeps the probe about the panel's answer, not about effect timing.
 */
async function messageFromFrame(
  frame: HTMLElement,
  data: unknown,
  landed: () => boolean,
): Promise<void> {
  await waitFor(() => {
    if (!landed()) dispatchFrameMessage(frame, data);
    expect(landed()).toBe(true);
  });
}

const evidence = (overrides: Partial<EvidenceDestination> = {}): EvidenceDestination => ({
  state: 'opened',
  operation: 'open-evidence',
  reference: 'INV-1',
  kind: 'knowledge-record',
  comparison: BOUND7,
  target: { ...TARGET, comparison: BOUND7 },
  location: { record_id: 'INV-1', record_revision: 3, tree: 'b'.repeat(40) },
  ...overrides,
});

describe('canvasModel', () => {
  it('keeps one page lineage when the comparison number moves', () => {
    // The frame id takes no comparison: the only lineage is the leaf's one saved page. The B12 case
    // below proves the panel's reads all land on this one frame as the comparison moves.
    expect(frameForComparison()).toBe('review-page');
  });

  it('judges the saved page against the comparison on screen, including a historical read', () => {
    expect(panelPhase(FRAME7, null, COMPARISON7)).toBe('ready');
    expect(panelPhase(FRAME7, null, COMPARISON8)).toBe('stale');
    // The host judges the retained revision stale against comparison 8; a reviewer reading the
    // recorded comparison 7 is shown the page that exactly explains the comparison on screen.
    expect(panelPhase(FRAME8, null, COMPARISON7)).toBe('ready');
  });

  it('names the full identity of an exact destination and refuses a foreign one', () => {
    const exact = destinationLine(evidence(), { ...TARGET, comparison: COMPARISON7 });
    expect(exact.selector).toEqual({ kind: 'invariant', id: 'INV-1' });
    expect(exact.line).toContain(`tree ${'b'.repeat(40)}`);
    expect(exact.line).toContain('revision 3');
    expect(exact.line).toContain(`comparison ${BOUND7}`);

    const foreign = destinationLine(
      evidence({ comparison: BOUND8, target: { ...TARGET, comparison: BOUND8 } }),
      { ...TARGET, comparison: COMPARISON7 },
    );
    expect(foreign.selector).toBeUndefined();
    expect(foreign.line).toContain('belongs to comparison 8');

    const unrepresentable = destinationLine(
      evidence({ location: { tree: 'b'.repeat(40), path: 'src/app.py' } }),
      { ...TARGET, comparison: COMPARISON7 },
    );
    expect(unrepresentable.selector).toBeUndefined();
    expect(unrepresentable.line).toContain('does not represent that exact destination');
  });
});

describe('CanvasPanel', () => {
  beforeEach(() => {
    let next = 0;
    vi.stubGlobal('crypto', {
      ...globalThis.crypto,
      randomUUID: () => `request-${(next += 1)}`,
    });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('shows an empty page and starts nothing on open', async () => {
    const calls = servePage({
      ...FRAME7,
      opening: { ...FRAME7.opening, state: 'unavailable', revision: null, comparison: null },
      staleness: { state: 'unavailable', detail: 'no comparison is selected' },
      presenter: { ...FRAME7.presenter, state: 'unavailable', revision: null },
    });
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    await waitFor(() => expect(screen.getByTestId('canvas-phase').textContent).toBe('empty'));
    expect(screen.getByTestId('canvas-generate').hasAttribute('disabled')).toBe(false);
    expect(sessionPosts(calls)).toHaveLength(0);
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0);
  });

  it('shows the saved page in the host sandbox and reports the scripts value it names', async () => {
    servePage(
      {
        ...FRAME7,
        policy: { ...FRAME7.policy, scripts: true },
      },
      { scripts: true, sandbox: 'allow-scripts' },
    );
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    const frame = await screen.findByTestId('canvas-frame');
    // MIK-R104 v2: the page runs its own scripts, in the sandbox the host names -- never
    // `allow-same-origin`, and never a permission the panel derived for itself.
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
    expect(frame.getAttribute('src')).toBe('/canvas/page/s1');
    expect(screen.getByTestId('canvas-phase').textContent).toBe('ready');
    expect(screen.getByTestId('canvas-authorship').textContent).toContain('scripts enabled');
    expect(screen.getByTestId('canvas-authorship').textContent).toContain('sandboxed frame');
  });

  it('reports scripts disabled when that is the value the host names', async () => {
    servePage(FRAME7);
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    const frame = await screen.findByTestId('canvas-frame');
    expect(frame.getAttribute('sandbox')).toBe('');
    expect(screen.getByTestId('canvas-authorship').textContent).toContain('scripts disabled');
    expect(screen.getByTestId('canvas-authorship').textContent).not.toContain('scripts enabled');
  });

  it('starts one explicit generation for the comparison on screen', async () => {
    const calls = servePage({
      ...FRAME7,
      opening: { ...FRAME7.opening, state: 'unavailable', revision: null, comparison: null },
      staleness: { state: 'unavailable', detail: 'no comparison is selected' },
      presenter: { ...FRAME7.presenter, state: 'unavailable', revision: null },
    });
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    await waitFor(() => expect(screen.getByTestId('canvas-phase').textContent).toBe('empty'));
    fireEvent.click(screen.getByTestId('canvas-generate'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/generate'))).toBe(true));
    const start = calls.find((call) => call.url.endsWith('/generate'));
    expect(start?.body).toMatchObject({
      activation: 'explicit',
      requestId: 'request-1',
      comparison: COMPARISON7.reference,
      comparisonNumber: 7,
      history: 'live',
      regenerate: false,
    });
    expect(start?.body).not.toHaveProperty('baseRevision');
  });

  it('disables the control and names why when the launch is refused', async () => {
    servePage({
      ...FRAME7,
      launch: {
        state: 'refused',
        detail: 'orchestration.roles.review-presenter is absent from the settings',
      },
    });
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    await waitFor(() =>
      expect(screen.getByTestId('canvas-launch').textContent).toContain(
        'is absent from the settings',
      ),
    );
    expect(screen.getByTestId('canvas-generate').hasAttribute('disabled')).toBe(true);
  });

  it('revokes the channel and shows the saved page when the frame navigates', async () => {
    const calls = servePage(FRAME7);
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    const frame = await screen.findByTestId('canvas-frame');
    const binding = vi.spyOn((frame as HTMLIFrameElement).contentWindow!, 'postMessage');
    fireEvent.load(frame);
    fireEvent.load(frame);
    expect(binding).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(calls.some((call) => call.url === '/api/canvas/sessions/s1/revoke')).toBe(true),
    );
    await waitFor(() => expect(sessionPosts(calls)).toHaveLength(2));
    // The navigation itself is an accepted channel under MIK-R104 v2; what the panel states is that
    // the old channel was revoked and the saved page is shown again.
    expect(screen.getByTestId('canvas-navigated').textContent).toContain(
      'previous channel was revoked',
    );
    expect(screen.getByTestId('canvas-navigated').textContent).toContain('saved page is');
  });

  it('revokes a document that departs before the iframe first loads', async () => {
    const calls = servePage(FRAME7);
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    const frame = await screen.findByTestId('canvas-frame');
    fireEvent(
      window,
      new MessageEvent('message', {
        source: (frame as HTMLIFrameElement).contentWindow,
        data: { schema: 'ar-canvas-document/v1', session: 's1', phase: 'departed' },
      }),
    );
    await waitFor(() =>
      expect(calls.some((call) => call.url === '/api/canvas/sessions/s1/revoke')).toBe(true),
    );
    expect(screen.getByTestId('canvas-navigated')).toBeTruthy();
  });

  it('disposes the channel when the panel is hidden because no comparison is shown', async () => {
    const calls = servePage(FRAME7);
    const view = render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    await waitFor(() =>
      expect(screen.getByTestId('canvas-frame').getAttribute('src')).toBe('/canvas/page/s1'),
    );
    view.rerender(<CanvasPanel {...TARGET} />);
    await waitFor(() =>
      expect(calls.some((call) => call.url === '/api/canvas/sessions/s1/revoke')).toBe(true),
    );
    expect(screen.queryByTestId('canvas-panel')).toBeNull();
  });

  it('drops the old page and channel when the review target is replaced', async () => {
    const calls = servePage(FRAME7);
    const view = render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    await waitFor(() =>
      expect(screen.getByTestId('canvas-frame').getAttribute('src')).toBe('/canvas/page/s1'),
    );
    view.rerender(<CanvasPanel {...TARGET} leaf="260928-MIK-L105" comparison={COMPARISON7} />);
    await waitFor(() =>
      expect(calls.some((call) => call.url === '/api/canvas/sessions/s1/revoke')).toBe(true),
    );
    await waitFor(() =>
      expect(screen.getByTestId('canvas-frame').getAttribute('src')).toBe('/canvas/page/s2'),
    );
    expect(
      calls
        .filter((call) => call.url.startsWith('/api/canvas/frames/') && call.method === 'GET')
        .at(-1)?.url,
    ).toContain('/260928-MIK-L105/review-page');
  });
});

describe('the saved artifact keeps its lineage when the comparison moves (L104-B12)', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('marks the retained revision stale and regenerates from it for the new comparison', async () => {
    let shown: CanvasComparison = COMPARISON7;
    const calls = serve((call, calls) => {
      if (call.url.endsWith('/revoke')) return answer({ state: 'ok', revoked: true });
      if (call.url === '/api/canvas/sessions') {
        return answer(channel(`s${sessionPosts(calls).length}`));
      }
      if (call.url.endsWith('/generate')) return answer({ state: 'ok' });
      if (call.url.startsWith(FRAME_URL)) return answer(shown.number === 7 ? FRAME7 : FRAME8);
      return answer({ detail: 'unmocked', reason: 'x' }, 404);
    });
    const view = render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    await waitFor(() => expect(screen.getByTestId('canvas-phase').textContent).toBe('ready'));
    expect((await screen.findByTestId('canvas-frame')).getAttribute('src')).toBe('/canvas/page/s1');

    shown = COMPARISON8;
    view.rerender(<CanvasPanel {...TARGET} comparison={COMPARISON8} />);
    await waitFor(() => expect(screen.getByTestId('canvas-phase').textContent).toBe('stale'));

    // One lineage: every read named the one frame, and the last one named comparison 8.
    const reads = calls.filter((call) => call.method === 'GET' && call.url.startsWith(FRAME_URL));
    expect(new Set(reads.map((call) => call.url.split('?')[0]))).toEqual(new Set([FRAME_URL]));
    expect(reads.at(-1)?.search.get('comparisonNumber')).toBe('8');
    // The retained revision is still the page: a channel serves that revision again and the page is
    // marked stale, not replaced by an empty sibling or by a different comparison's record.
    const lastSession = sessionPosts(calls).at(-1);
    expect(lastSession?.body).toMatchObject({ revision: 1 });
    expect((await screen.findByTestId('canvas-frame')).getAttribute('src')).toBe('/canvas/page/s2');
    expect(screen.getByTestId('canvas-line').textContent).toContain('explains comparison 7');
    expect(screen.getByTestId('canvas-line').textContent).toContain('comparison 8 is on screen');
    expect(screen.getByTestId('canvas-generate').textContent).toBe(
      'Regenerate for this comparison',
    );

    fireEvent.click(screen.getByTestId('canvas-generate'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/generate'))).toBe(true));
    const start = calls.find((call) => call.url.endsWith('/generate'));
    expect(start?.body).toMatchObject({
      regenerate: true,
      baseRevision: 1,
      comparison: COMPARISON8.reference,
      comparisonNumber: 8,
      history: 'live',
    });
  });

  it('names a recovery state instead of an empty page when retained assets are gone', async () => {
    const calls = servePage(NO_ASSETS);
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
    await waitFor(() => expect(screen.getByTestId('canvas-phase').textContent).toBe('recovery'));
    expect(screen.getByTestId('canvas-line').textContent).toContain('cannot be read');
    expect(screen.getByTestId('canvas-line').textContent).toContain('manifest of revision 1');
    expect(screen.getByTestId('canvas-generate').textContent).toBe('Generate a new page');
    expect(screen.queryByTestId('canvas-frame')).toBeNull();

    fireEvent.click(screen.getByTestId('canvas-generate'));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/generate'))).toBe(true));
    // A recovery choice is a new page, never a regeneration that pretends assets exist.
    expect(calls.find((call) => call.url.endsWith('/generate'))?.body).toMatchObject({
      regenerate: false,
    });
  });
});

describe("the frame's own message burst is bounded before it becomes work (L104-B08)", () => {
  function serveBounded() {
    return serve((call) => {
      if (call.url.endsWith('/revoke')) return answer({ state: 'ok', revoked: true });
      if (call.url === '/api/canvas/sessions') return answer(channel('s1'));
      if (call.url.endsWith('/messages')) return answer(evidence());
      if (call.url.startsWith(FRAME_URL)) return answer(FRAME7);
      return answer({ detail: 'unmocked', reason: 'x' }, 404);
    });
  }

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const bursts = [64, 128];

  for (const burst of bursts) {
    it(`drops ${burst} oversized messages without one request`, async () => {
      const calls = serveBounded();
      render(<CanvasPanel {...TARGET} comparison={COMPARISON7} onOpenEvidence={vi.fn()} />);
      const frame = await screen.findByTestId('canvas-frame');
      // The chart's own probe: every message is past the server's 4096-byte body ceiling.
      const oversized = { type: 'open-evidence', reference: 'INV-1', padding: 'x'.repeat(5000) };
      await act(async () => {
        for (let index = 0; index < burst; index += 1) dispatchFrameMessage(frame, oversized);
        await new Promise((resolve) => setTimeout(resolve, 60));
      });
      expect(calls.filter((call) => call.url.endsWith('/messages'))).toHaveLength(0);
      // The page's detail is not frozen by the refusal: the panel still shows it.
      expect(screen.getByTestId('canvas-frame')).toBeTruthy();
    });
  }

  it('still forwards one message inside the ceiling', async () => {
    const calls = serveBounded();
    const onOpenEvidence = vi.fn();
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} onOpenEvidence={onOpenEvidence} />);
    const frame = await screen.findByTestId('canvas-frame');
    await messageFromFrame(frame, { type: 'open-evidence', reference: 'INV-1' }, () =>
      calls.some((call) => call.url.endsWith('/messages')),
    );
    expect(calls.filter((call) => call.url.endsWith('/messages'))).toHaveLength(1);
  });
});

describe('evidence the page asks to open (L104-B11)', () => {
  function serveMessage(destination: EvidenceDestination) {
    return serve((call) => {
      if (call.url.endsWith('/revoke')) return answer({ state: 'ok', revoked: true });
      if (call.url === '/api/canvas/sessions') return answer(channel('s1'));
      if (call.url.endsWith('/messages')) return answer(destination);
      if (call.url.startsWith(FRAME_URL)) return answer(FRAME7);
      return answer({ detail: 'unmocked', reason: 'x' }, 404);
    });
  }

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('opens the exact selector of the comparison on screen', async () => {
    serveMessage(evidence());
    const onOpenEvidence = vi.fn();
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} onOpenEvidence={onOpenEvidence} />);
    const frame = await screen.findByTestId('canvas-frame');
    await messageFromFrame(
      frame,
      { type: 'open-evidence', reference: 'INV-1' },
      () => onOpenEvidence.mock.calls.length > 0,
    );
    expect(onOpenEvidence.mock.calls[0][0]).toMatchObject({
      selector: { kind: 'invariant', id: 'INV-1' },
      target: TARGET,
      comparison: { reference: COMPARISON7.reference, number: 7 },
      location: { record_revision: 3 },
    });
    expect(screen.getByTestId('canvas-destination').textContent).toContain('revision 3');
  });

  it('ignores an evidence answer that lands after the channel was replaced', async () => {
    let release: (value: Response) => void = () => undefined;
    const calls = serve((call, calls) => {
      if (call.url.endsWith('/revoke')) return answer({ state: 'ok', revoked: true });
      if (call.url === '/api/canvas/sessions') {
        return answer(channel(`s${sessionPosts(calls).length}`));
      }
      if (call.url.endsWith('/messages')) {
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      return answer(FRAME7);
    });
    const onOpenEvidence = vi.fn();
    const view = render(
      <CanvasPanel {...TARGET} comparison={COMPARISON7} onOpenEvidence={onOpenEvidence} />,
    );
    const frame = await screen.findByTestId('canvas-frame');
    await messageFromFrame(frame, { type: 'open-evidence', reference: 'INV-1' }, () =>
      calls.some((call) => call.url.endsWith('/messages')),
    );
    view.rerender(
      <CanvasPanel
        {...TARGET}
        leaf="260928-MIK-L105"
        comparison={COMPARISON7}
        onOpenEvidence={onOpenEvidence}
      />,
    );
    await act(async () => release(answer(evidence())));
    expect(onOpenEvidence).not.toHaveBeenCalled();
  });

  it('refuses a destination of another comparison and opens nothing', async () => {
    serveMessage(evidence({ comparison: BOUND8, target: { ...TARGET, comparison: BOUND8 } }));
    const onOpenEvidence = vi.fn();
    render(<CanvasPanel {...TARGET} comparison={COMPARISON7} onOpenEvidence={onOpenEvidence} />);
    const frame = await screen.findByTestId('canvas-frame');
    await messageFromFrame(
      frame,
      { type: 'open-evidence', reference: 'INV-1' },
      () => screen.queryByTestId('canvas-destination') !== null,
    );
    expect(screen.getByTestId('canvas-destination').textContent).toContain(
      'belongs to comparison 8',
    );
    expect(onOpenEvidence).not.toHaveBeenCalled();
  });
});

describe('canvas hooks — scope changes (L104-B10)', () => {
  const target = (frame = 'comparison-7') => ({
    repository: 'repo',
    master: 'task',
    leaf: 'leaf',
    frame,
  });
  const ready = (revision = 1) =>
    ({ state: 'ok', opening: { state: 'ready', revision } }) as CanvasFrameState;

  function mockSessions() {
    return serve((call, calls) => {
      if (call.url.endsWith('/revoke')) return answer({ state: 'ok', revoked: true });
      const revision = Number(call.body?.revision ?? 1);
      return answer(channel(`s${sessionPosts(calls).length}`, revision));
    });
  }

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('positive control: a ready page opens one channel and unmount revokes it', async () => {
    const calls = mockSessions();
    const hook = renderHook(() => useCanvasSession(target(), ready()));
    await waitFor(() => expect(hook.result.current?.session).toBe('s1'));
    hook.unmount();
    await act(async () => undefined);
    expect(sessionPosts(calls)).toHaveLength(1);
    expect(calls.some((call) => call.url === '/api/canvas/sessions/s1/revoke')).toBe(true);
  });

  it('replacing the review target revokes the old channel and opens a new one', async () => {
    const calls = mockSessions();
    const hook = renderHook(({ frame }) => useCanvasSession(target(frame), ready()), {
      initialProps: { frame: 'comparison-7' },
    });
    await waitFor(() => expect(hook.result.current?.session).toBe('s1'));
    await act(async () => hook.rerender({ frame: 'comparison-8' }));
    expect(calls.some((call) => call.url === '/api/canvas/sessions/s1/revoke')).toBe(true);
    expect(sessionPosts(calls)).toHaveLength(2);
  });

  it('replacing the ready revision rotates the channel while the page stays ready', async () => {
    const calls = mockSessions();
    const hook = renderHook(({ revision }) => useCanvasSession(target(), ready(revision)), {
      initialProps: { revision: 1 },
    });
    await waitFor(() => expect(hook.result.current?.session).toBe('s1'));
    await act(async () => hook.rerender({ revision: 2 }));
    expect(calls.some((call) => call.url === '/api/canvas/sessions/s1/revoke')).toBe(true);
    expect(sessionPosts(calls)).toHaveLength(2);
  });

  it('revokes a channel the host allocated after the panel was disposed', async () => {
    let release: (value: Response) => void = () => undefined;
    const calls = serve((call) =>
      call.url.endsWith('/revoke')
        ? answer({ state: 'ok', revoked: true })
        : new Promise<Response>((resolve) => {
            release = resolve;
          }),
    );
    const hook = renderHook(() => useCanvasSession(target(), ready()));
    await waitFor(() =>
      expect(calls.some((call) => call.url === '/api/canvas/sessions')).toBe(true),
    );
    hook.unmount();
    await act(async () => release(answer(channel('late'))));
    expect(calls.some((call) => call.url === '/api/canvas/sessions/late/revoke')).toBe(true);
  });

  it('ignores a late answer for a previous scope', async () => {
    let releaseOld: (value: Response) => void = () => undefined;
    serve((call) =>
      call.url.includes('/comparison-7')
        ? new Promise<Response>((resolve) => {
            releaseOld = resolve;
          })
        : answer({ state: 'ok', opening: {}, presenter: {}, marker: 'new-scope' }),
    );
    const hook = renderHook(({ frame }) => useCanvasFrame(target(frame)), {
      initialProps: { frame: 'comparison-7' },
    });
    await act(async () => hook.rerender({ frame: 'comparison-8' }));
    await waitFor(() =>
      expect((hook.result.current.state as unknown as { marker?: string })?.marker).toBe(
        'new-scope',
      ),
    );
    await act(async () =>
      releaseOld(answer({ state: 'ok', opening: {}, presenter: {}, marker: 'old-scope' })),
    );
    expect((hook.result.current.state as unknown as { marker?: string })?.marker).toBe('new-scope');
  });
});

describe('the comparison on screen survives a selection still reading (L104-B11)', () => {
  afterEach(cleanup);

  it('holds the last comparison while the surface reads and replaces it when it moves', () => {
    const hook = renderHook(
      ({ shown }: { shown: CanvasComparison | undefined }) => useRetainedComparison(shown),
      {
        initialProps: { shown: COMPARISON7 as CanvasComparison | undefined },
      },
    );
    expect(hook.result.current).toEqual(COMPARISON7);
    hook.rerender({ shown: undefined });
    expect(hook.result.current).toEqual(COMPARISON7);
    hook.rerender({ shown: COMPARISON8 });
    expect(hook.result.current).toEqual(COMPARISON8);
  });
});

describe('trusted-side serialization work is bounded before admission (R4 B08)', () => {
  for (const burst of [64, 128]) {
    it(`does no JSON traversal for ${burst} large, deep or wide bodies`, async () => {
      const calls = servePage(FRAME7);
      render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
      const frame = await screen.findByTestId('canvas-frame');
      await act(async () => {
        await Promise.resolve();
      });
      const bodies = [
        { type: 'status', payload: { detail: 'x'.repeat(1024 * 1024) } },
        { type: 'status', payload: { nested: { another: { level: 'unbounded' } } } },
        {
          type: 'status',
          payload: Object.fromEntries(Array.from({ length: 10000 }, (_, i) => [`field${i}`, i])),
        },
      ];
      const stringify = vi.spyOn(JSON, 'stringify');
      await act(async () => {
        for (let i = 0; i < burst; i++) dispatchFrameMessage(frame, bodies[i % 3]);
      });
      expect(stringify).not.toHaveBeenCalled();
      stringify.mockRestore();
      expect(calls.filter((call) => call.url.endsWith('/messages'))).toHaveLength(0);
      expect(screen.getByTestId('canvas-frame')).toBeTruthy();
    });

    it(`inspects and serializes no body after four requests are in flight (${burst} more)`, async () => {
      let finish!: (value: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        finish = resolve;
      });
      const calls = serve((call) => {
        if (call.url.endsWith('/messages')) return pending;
        if (call.url.endsWith('/revoke')) return answer({ state: 'ok', revoked: true });
        if (call.url === '/api/canvas/sessions') return answer(channel('s1'));
        return answer(FRAME7);
      });
      const mounted = render(<CanvasPanel {...TARGET} comparison={COMPARISON7} />);
      const frame = await screen.findByTestId('canvas-frame');
      await act(async () => {
        await Promise.resolve();
        for (let i = 0; i < 4; i++)
          dispatchFrameMessage(frame, { type: 'ready', requestId: `request-${i}` });
      });
      expect(calls.filter((call) => call.url.endsWith('/messages'))).toHaveLength(4);
      const stringify = vi.spyOn(JSON, 'stringify');
      await act(async () => {
        for (let i = 0; i < burst; i++)
          dispatchFrameMessage(frame, { type: 'status', payload: { detail: 'x'.repeat(5000) } });
      });
      expect(stringify).not.toHaveBeenCalled();
      stringify.mockRestore();
      expect(calls.filter((call) => call.url.endsWith('/messages'))).toHaveLength(4);
      mounted.unmount();
      await act(async () => {
        finish(answer({ state: 'accepted', operation: 'ready' }));
      });
    });
  }
});
