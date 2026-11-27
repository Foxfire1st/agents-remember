import { act, cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PaseoChatFrame } from './PaseoChatFrame';
import {
  PASEO_CONTROL_TIMEOUT_MS,
  PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS,
  PaseoFrameControl,
} from './paseoFrameControl';

const FRAME_ORIGIN = 'http://127.0.0.1:6820';
const AVAILABLE = {
  available: true,
  frameBaseUrl: FRAME_ORIGIN,
  serverId: 'srv_test',
  projectsWorkspaceId: 'wks_projects',
  projectsWorkspaceDetail: null,
};
const ARCHITECT = { agentId: 'agent-architect', workspaceId: 'wks_projects' };
const WORKER = { agentId: 'agent-worker', workspaceId: 'wks_leaf' };
const PROJECTS_URL = FRAME_ORIGIN + '/h/srv_test/workspace/wks_projects';

type Answer = Record<string, unknown>;
type Route = Answer | ((request: Answer) => Answer);

function stubBackend(frameAnswers: Answer[], other: Record<string, Route> = {}) {
  const queue = [...frameAnswers];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const route =
      url === '/api/role-launch/frame' ? (queue.length > 1 ? queue.shift() : queue[0]) : other[url];
    if (!route) throw new Error('unexpected request ' + url);
    const body =
      typeof route === 'function' ? route(JSON.parse(String(init?.body ?? '{}')) as Answer) : route;
    const status = typeof body.httpStatus === 'number' ? body.httpStatus : 200;
    return { ok: status < 400, status, json: async () => body } as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function settle(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function frameElement(container: HTMLElement): HTMLIFrameElement {
  const frame = container.querySelector('iframe');
  if (!frame) throw new Error('no frame is mounted');
  return frame;
}

function deliver(
  frame: HTMLIFrameElement,
  data: unknown,
  from: { origin?: string; source?: MessageEventSource | null } = {},
): void {
  act(() => {
    window.dispatchEvent(
      new MessageEvent('message', {
        data,
        origin: from.origin ?? FRAME_ORIGIN,
        source: from.source === undefined ? frame.contentWindow : from.source,
      }),
    );
  });
}

const plugin = (message: Answer) => ({ source: 'ar-plugin', ...message });

function watchPosts(frame: HTMLIFrameElement) {
  const posts = vi.fn();
  if (!frame.contentWindow) throw new Error('the frame has no window');
  frame.contentWindow.postMessage = posts as unknown as Window['postMessage'];
  return posts;
}

beforeEach(() => {
  vi.useFakeTimers();
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('PaseoChatFrame transient open retries', () => {
  it('bounds automatic retries for persistent agent-not-found and leaves the frame where it is', async () => {
    stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId, rerender } = render(
      <PaseoChatFrame active scope="architect" target={ARCHITECT} />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    deliver(frame, plugin({ type: 'ready' }));
    deliver(
      frame,
      plugin({ type: 'error', code: 'agent-not-found', agentId: ARCHITECT.agentId, message: 'x' }),
    );
    expect(getByTestId('paseo-frame-agent-problem').textContent).toContain('gone');

    for (const delay of PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS) {
      await settle(delay);
      deliver(
        frame,
        plugin({ type: 'error', code: 'agent-not-found', agentId: ARCHITECT.agentId, message: 'x' }),
      );
    }

    expect(posts).toHaveBeenCalledTimes(1 + PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS.length);
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);
    expect(posts).toHaveBeenCalledTimes(1 + PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS.length);
    expect(vi.getTimerCount()).toBe(0);
    expect(frameElement(container)).toBe(frame);
    expect(frame.getAttribute('src')).toBe(PROJECTS_URL);
    expect(getByTestId('paseo-frame').getAttribute('data-control')).toBe('ready');

    rerender(<PaseoChatFrame active scope="worker" target={WORKER} />);
    expect(queryByTestId('paseo-frame-agent-problem')).toBeNull();
  });

  it('automatically retries ar.open for an unchanged mounted explainer agent after transient agent-not-found without reloading the iframe', async () => {
    stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId } = render(
      <PaseoChatFrame
        active
        scope="explainer:repo/master/L181"
        target={WORKER}
        page="document"
      />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    const openCalls = () =>
      posts.mock.calls.filter(([message]) => (message as { type?: string }).type === 'ar.open');

    deliver(frame, plugin({ type: 'ready' }));
    expect(openCalls()).toEqual([
      [{ type: 'ar.open', agentId: WORKER.agentId, workspaceId: WORKER.workspaceId }, FRAME_ORIGIN],
    ]);

    // Plugin rejects initial open while the newly started explainer registers. Props and scope do not change.
    deliver(
      frame,
      plugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId }),
    );
    expect(getByTestId('paseo-frame-agent-problem').textContent).toContain('gone');

    await settle(PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS[0] - 1);
    expect(openCalls()).toHaveLength(1);

    await settle(1);
    expect(openCalls()).toHaveLength(2);
    expect(openCalls()[1]).toEqual([
      { type: 'ar.open', agentId: WORKER.agentId, workspaceId: WORKER.workspaceId },
      FRAME_ORIGIN,
    ]);
    expect(frameElement(container)).toBe(frame);
    expect(getByTestId('paseo-frame').getAttribute('data-generation')).toBe('1');

    // Once shown, the problem clears and no further retry or open message is posted.
    deliver(frame, plugin({ type: 'shown', agentId: WORKER.agentId }));
    expect(queryByTestId('paseo-frame-agent-problem')).toBeNull();

    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);
    expect(openCalls()).toHaveLength(2);
    expect(vi.getTimerCount()).toBe(0);
    expect(frameElement(container)).toBe(frame);
    expect(getByTestId('paseo-frame').getAttribute('data-generation')).toBe('1');
  });

  it('re-sends ar.open immediately when a supported same-frame ready arrives before a transient retry fires', async () => {
    stubBackend([AVAILABLE]);
    const supportedCapabilities = [
      'open-agent',
      'ping',
      'page',
      'shown-on-display',
      'navigated',
      'on-chat',
      'load-failed',
      'pick',
    ];
    const { container, getByTestId, queryByTestId } = render(
      <PaseoChatFrame
        active
        scope="explainer:repo/master/L181"
        target={WORKER}
        page="document"
      />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    const openCalls = () =>
      posts.mock.calls.filter(([message]) => (message as { type?: string }).type === 'ar.open');

    deliver(frame, plugin({ type: 'ready', capabilities: supportedCapabilities }));
    expect(openCalls()).toEqual([
      [{ type: 'ar.open', agentId: WORKER.agentId, workspaceId: WORKER.workspaceId }, FRAME_ORIGIN],
    ]);

    deliver(
      frame,
      plugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId }),
    );
    expect(getByTestId('paseo-frame-agent-problem').textContent).toContain('gone');

    // A supported ready on the same iframe before the retry timer expires re-sends ar.open immediately.
    await settle(PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS[0] - 50);
    expect(openCalls()).toHaveLength(1);
    deliver(frame, plugin({ type: 'ready', capabilities: supportedCapabilities }));
    expect(openCalls()).toEqual([
      [{ type: 'ar.open', agentId: WORKER.agentId, workspaceId: WORKER.workspaceId }, FRAME_ORIGIN],
      [{ type: 'ar.open', agentId: WORKER.agentId, workspaceId: WORKER.workspaceId }, FRAME_ORIGIN],
    ]);
    expect(frameElement(container)).toBe(frame);
    expect(getByTestId('paseo-frame').getAttribute('data-generation')).toBe('1');

    // The cancelled retry timer does not post a duplicate open.
    await settle(PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS[0]);
    expect(openCalls()).toHaveLength(2);

    // Once shown, the problem clears, the chat settles, and a later ready sends no third ar.open.
    deliver(frame, plugin({ type: 'shown', agentId: WORKER.agentId }));
    expect(queryByTestId('paseo-frame-agent-problem')).toBeNull();
    deliver(frame, plugin({ type: 'ready', capabilities: supportedCapabilities }));
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);
    expect(openCalls()).toHaveLength(2);
    expect(vi.getTimerCount()).toBe(0);
    expect(frameElement(container)).toBe(frame);
    expect(getByTestId('paseo-frame').getAttribute('data-generation')).toBe('1');
  });

  it('cancels a pending transient retry when scope, target, non-transient error, or unmount intervenes', async () => {
    stubBackend([AVAILABLE]);
    const { container, rerender, unmount } = render(
      <PaseoChatFrame
        active
        scope="explainer:repo/master/L181"
        target={WORKER}
        page="document"
      />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    const openCalls = () =>
      posts.mock.calls.filter(([message]) => (message as { type?: string }).type === 'ar.open');

    deliver(frame, plugin({ type: 'ready' }));
    deliver(frame, plugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId }));

    // Clearing target cancels the pending retry timer immediately.
    rerender(
      <PaseoChatFrame
        active
        scope="explainer:repo/master/L181"
        target={null}
        page="document"
      />,
    );
    await settle(PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS[0] * 4);
    expect(openCalls()).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);

    // Switching scope/target starts fresh; a non-transient error on retry cancels further retries.
    rerender(
      <PaseoChatFrame active scope="architect" target={ARCHITECT} page="document" />,
    );
    deliver(frame, plugin({ type: 'error', code: 'agent-not-found', agentId: ARCHITECT.agentId }));
    await settle(PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS[0]);
    expect(openCalls()).toHaveLength(3);
    deliver(frame, plugin({ type: 'error', code: 'agent-archived', agentId: ARCHITECT.agentId }));
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);
    expect(openCalls()).toHaveLength(3);
    expect(vi.getTimerCount()).toBe(0);

    // Unmount cancels any scheduled retry timer.
    rerender(
      <PaseoChatFrame
        active
        scope="explainer:repo/master/L181"
        target={WORKER}
        page="document"
      />,
    );
    deliver(frame, plugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId }));
    unmount();
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);
    expect(openCalls()).toHaveLength(4);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('retries ar.open for the same scope and agent after a transient open failure', () => {
    const posts = vi.fn();
    const fakeWindow = { postMessage: posts } as unknown as Window;
    const setProblem = vi.fn();
    const control = new PaseoFrameControl({
      frameWindow: () => fakeWindow,
      setControl: vi.fn(),
      setFrame: vi.fn(),
      setAgentProblem: setProblem,
      setShowing: vi.fn(),
      setPluginNotice: vi.fn(),
      page: 'document',
    });
    const available = {
      available: true as const,
      frameBaseUrl: FRAME_ORIGIN,
      frameOrigin: FRAME_ORIGIN,
      serverId: 'srv_test',
      projectsWorkspaceId: 'wks_projects',
      projectsWorkspaceDetail: null,
      provisionCommand: '',
    };
    const sendPlugin = (data: Record<string, unknown>) =>
      control.receive(
        new MessageEvent('message', {
          data: plugin(data),
          origin: FRAME_ORIGIN,
          source: fakeWindow,
        }),
      );

    control.start(available, false);
    control.display('explainer:repo/master/L181', WORKER);
    sendPlugin({ type: 'ready' });
    expect(posts).toHaveBeenCalledWith(
      { type: 'ar.open', agentId: WORKER.agentId, workspaceId: WORKER.workspaceId },
      FRAME_ORIGIN,
    );

    // Transient failure on the newly created agent: seenAgent is cleared so a retry on the same scope works.
    sendPlugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId });
    expect(setProblem).toHaveBeenLastCalledWith(expect.stringContaining('gone'));

    posts.mockClear();
    control.display('explainer:repo/master/L181', WORKER);
    expect(posts).toHaveBeenCalledTimes(1);
    expect(posts).toHaveBeenLastCalledWith(
      { type: 'ar.open', agentId: WORKER.agentId, workspaceId: WORKER.workspaceId },
      FRAME_ORIGIN,
    );
    sendPlugin({ type: 'shown', agentId: WORKER.agentId });
    expect(setProblem).toHaveBeenLastCalledWith(null);

    // Once shown, repeating display for the exact same scope and target does not re-send ar.open.
    posts.mockClear();
    control.display('explainer:repo/master/L181', WORKER);
    expect(posts).not.toHaveBeenCalled();
  });

  it('re-sends ar.open without reload from problem Retry when ready, and reloads when unavailable', async () => {
    const fetchMock = stubBackend([AVAILABLE]);
    const { container, getByTestId, queryByTestId } = render(
      <PaseoChatFrame active scope="explainer:repo/master/L62" target={WORKER} page="document" />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    const openCalls = () =>
      posts.mock.calls.filter(([message]) => (message as { type?: string }).type === 'ar.open');

    deliver(frame, plugin({ type: 'ready' }));
    deliver(frame, plugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId }));
    for (const delay of PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS) {
      await settle(delay);
      deliver(frame, plugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId }));
    }
    const exhaustedCount = 1 + PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS.length;
    expect(openCalls()).toHaveLength(exhaustedCount);

    // Retry on paseo-frame-agent-problem while ready re-sends ar.open without reloading.
    const problem = getByTestId('paseo-frame-agent-problem');
    fireEvent.click(within(problem).getByRole('button', { name: 'Retry' }));
    expect(queryByTestId('paseo-frame-agent-problem')).toBeNull();
    expect(openCalls()).toHaveLength(exhaustedCount + 1);
    expect(frameElement(container)).toBe(frame);
    expect(getByTestId('paseo-frame').getAttribute('data-generation')).toBe('1');
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Exhaust retries again, then verify a late ready also recovers an unshown target.
    deliver(frame, plugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId }));
    for (const delay of PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS) {
      await settle(delay);
      deliver(frame, plugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId }));
    }
    expect(getByTestId('paseo-frame-agent-problem').textContent).toContain('gone');
    deliver(frame, plugin({ type: 'ready' }));
    expect(queryByTestId('paseo-frame-agent-problem')).toBeNull();
    expect(openCalls()).toHaveLength(exhaustedCount * 2 + 1);
    expect(getByTestId('paseo-frame').getAttribute('data-generation')).toBe('1');

    // When control becomes unavailable, Retry on paseo-frame-agent-problem reloads the frame.
    await settle(PASEO_CONTROL_TIMEOUT_MS * 2);
    expect(getByTestId('paseo-frame').getAttribute('data-control')).toBe('unavailable');
    fireEvent.click(
      within(getByTestId('paseo-frame-agent-problem')).getByRole('button', { name: 'Retry' }),
    );
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(frameElement(container)).not.toBe(frame);
    expect(getByTestId('paseo-frame').getAttribute('data-generation')).toBe('2');
  });

  it('re-syncs an unshown target without reloading when active flips false to true, and stays quiet once shown', async () => {
    stubBackend([AVAILABLE]);
    const { container, getByTestId, rerender } = render(
      <PaseoChatFrame
        active={false}
        preload
        scope="explainer:repo/master/L62"
        target={WORKER}
        page="document"
      />,
    );
    await settle();
    const frame = frameElement(container);
    const posts = watchPosts(frame);
    const openCalls = () =>
      posts.mock.calls.filter(([message]) => (message as { type?: string }).type === 'ar.open');

    // Ready arrives while hidden; open fails and exhausts retries before the pane becomes visible.
    deliver(frame, plugin({ type: 'ready' }));
    deliver(frame, plugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId }));
    for (const delay of PASEO_TRANSIENT_OPEN_RETRY_DELAYS_MS) {
      await settle(delay);
      deliver(frame, plugin({ type: 'error', code: 'agent-not-found', agentId: WORKER.agentId }));
    }
    const beforeReveal = openCalls().length;

    // Flipping active false -> true triggers a non-reloading ar.open re-sync.
    rerender(
      <PaseoChatFrame
        active
        preload
        scope="explainer:repo/master/L62"
        target={WORKER}
        page="document"
      />,
    );
    expect(openCalls()).toHaveLength(beforeReveal + 1);
    expect(frameElement(container)).toBe(frame);
    expect(getByTestId('paseo-frame').getAttribute('data-generation')).toBe('1');

    // Once shown, hiding and showing again sends no duplicate ar.open.
    deliver(frame, plugin({ type: 'shown', agentId: WORKER.agentId }));
    rerender(
      <PaseoChatFrame
        active={false}
        preload
        scope="explainer:repo/master/L62"
        target={WORKER}
        page="document"
      />,
    );
    rerender(
      <PaseoChatFrame
        active
        preload
        scope="explainer:repo/master/L62"
        target={WORKER}
        page="document"
      />,
    );
    expect(openCalls()).toHaveLength(beforeReveal + 1);
    expect(getByTestId('paseo-frame').getAttribute('data-generation')).toBe('1');
  });
});
